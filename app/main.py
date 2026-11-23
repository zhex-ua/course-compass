"""One FastAPI service for the website and Python solver. No private-data persistence."""
import asyncio
import contextlib
import io
import json
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from openai import APIConnectionError, APIStatusError, AuthenticationError, RateLimitError
from pypdf import PdfReader
from starlette.formparsers import MultiPartParser

from .agent import AgentError, OpenAIBrain, run_agent
from .catalog import CatalogError, CornellCatalog
from .models import PlanRequest

ROOT = Path(__file__).resolve().parent.parent
MAX_BODY = 6 * 1024 * 1024
# UploadFile otherwise rolls files over 1 MB onto disk. The body guard below
# ensures no accepted request reaches this in-memory spool threshold.
MultiPartParser.spool_max_size = MAX_BODY


class MemoryBodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > MAX_BODY:
                response = JSONResponse(status_code=413, content={"detail": "Request exceeds the 6 MB limit."},
                                        headers={"Cache-Control": "no-store"})
                return await response(scope, receive, send)
            chunks.append(chunk)
            if not message.get("more_body"):
                break
        body = b"".join(chunks)
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()
        await self.app(scope, replay, send)


app = FastAPI(title="Course Compass", docs_url="/api/docs", redoc_url=None)
app.add_middleware(MemoryBodyLimit)
catalog = CornellCatalog()
active = asyncio.Semaphore(3)


@app.middleware("http")
async def privacy_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
    return response


# FastAPI's default 422 response includes rejected input values: never echo credentials.
from fastapi.exceptions import RequestValidationError


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return JSONResponse(status_code=422, content={"detail": [
        {"field": ".".join(str(x) for x in e["loc"]), "message": e["msg"]} for e in exc.errors()]})


def public_error(exc):
    if isinstance(exc, AuthenticationError):
        return "OpenAI rejected the API key. Check the key and project permissions."
    if isinstance(exc, RateLimitError):
        return "OpenAI rate or spending limit reached. Check API billing, then retry."
    if isinstance(exc, APIConnectionError):
        return "Could not connect to OpenAI. Please retry."
    if isinstance(exc, APIStatusError):
        return "OpenAI could not complete this request. Check model access or try again."
    if isinstance(exc, (AgentError, CatalogError)):
        return str(exc)
    return "The run could not be completed. Check the inputs and retry. No plan was accepted."


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/rosters")
async def rosters():
    try:
        return {"rosters": await catalog.rosters(), "live": True}
    except CatalogError:
        return {"rosters": [{"slug": "SP27", "descr": "Spring 2027"}], "live": False,
                "note": "Cornell unavailable; SP27 is the bundled demo term."}


@app.post("/api/transcript")
async def transcript(api_key: str = Form(...), model: str = Form("gpt-4.1-mini"), file: UploadFile = File(...)):
    if model not in ("gpt-4.1-mini", "gpt-4.1") or not api_key.strip():
        raise HTTPException(400, "Choose a supported model and enter an OpenAI API key.")
    if Path(file.filename or "").suffix.lower() not in (".pdf", ".txt", ".csv"):
        raise HTTPException(400, "Use a text-based PDF, TXT or CSV file.")
    data = await file.read(5 * 1024 * 1024 + 1)
    await file.close()
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(413, "Use a transcript smaller than 5 MB.")
    try:
        if (file.filename or "").lower().endswith(".pdf"):
            reader = PdfReader(io.BytesIO(data))
            if len(reader.pages) > 15:
                raise HTTPException(400, "Use at most 15 transcript pages.")
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        else:
            text = data.decode("utf-8-sig")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "Could not read this file. Use a text-based PDF, TXT or CSV.") from None
    if len(text.strip()) < 20:
        raise HTTPException(400, "No readable text found. Scanned-image PDFs need OCR first, or enter courses manually.")
    if len(text) > 45000:
        raise HTTPException(400, "Transcript text is too long. Upload only the course and grade pages.")
    brain = OpenAIBrain(api_key.strip(), model)
    try:
        async with active:
            result = await brain.transcript(text)
        return result.model_dump()
    except Exception as exc:
        raise HTTPException(502, public_error(exc)) from None
    finally:
        await brain.close()


@app.post("/api/plan")
async def plan(req: PlanRequest):
    if not req.demo and not req.api_key.get_secret_value().strip():
        raise HTTPException(400, "Enter your OpenAI API key or choose the offline demo.")

    async def stream():
        queue = asyncio.Queue()

        async def emit(stage, message, data):
            await queue.put({"type": "event", "stage": stage, "message": message, "data": data})

        async def run():
            brain = None
            try:
                async with active:
                    if not req.demo:
                        brain = OpenAIBrain(req.api_key.get_secret_value().strip(), req.model)
                    async with asyncio.timeout(600):
                        result = await run_agent(req, catalog, emit, brain)
                    await queue.put({"type": "result", "data": result})
            except asyncio.TimeoutError:
                await queue.put({"type": "error", "message": "This run reached the 10-minute limit. Narrow the subject hints and retry."})
            except Exception as exc:
                await queue.put({"type": "error", "message": public_error(exc)})
            finally:
                if brain:
                    await brain.close()
                await queue.put(None)

        task = asyncio.create_task(run())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), 15)
                except asyncio.TimeoutError:
                    yield json.dumps({"type": "heartbeat"}) + "\n"
                    continue
                if item is None:
                    break
                yield json.dumps(item) + "\n"
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    return StreamingResponse(stream(), media_type="application/x-ndjson", headers={"X-Accel-Buffering": "no"})


@app.get("/")
def home():
    return FileResponse(ROOT / "static" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
