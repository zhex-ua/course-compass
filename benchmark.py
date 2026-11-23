"""Measure a deployed instance with a synthetic profile and no OpenAI calls.

Usage: python benchmark.py https://your-service.onrender.com --output benchmark-results.json
The first request is recorded separately. It is a cold-start measurement only
when the operator has independently verified that the instance was asleep.
"""
import argparse
import asyncio
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx


PROFILE = {
    "goal": "I want to become a product or data analyst.", "demo": True, "roster": "SP27",
    "career": "UG", "campus": "MAIN", "min_credits": 12, "max_credits": 16, "course_count": 4,
    "earliest": 540, "latest": 1080,
    "completed": [{"code": code, "grade": "A", "status": "completed"}
                  for code in ["CS 1110", "MATH 1110", "MATH 1710", "ECON 1110"]],
}


async def get_once(client, path):
    started = time.perf_counter()
    response = await client.get(path)
    elapsed = (time.perf_counter() - started) * 1000
    response.raise_for_status()
    return response, {"path": path, "status": response.status_code, "total_ms": round(elapsed, 2),
                      "bytes": len(response.content)}


async def plan_once(client):
    started = time.perf_counter()
    first_event_ms, result = None, None
    async with client.stream("POST", "/api/plan", json=PROFILE) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.strip():
                continue
            event = json.loads(line)
            if event["type"] == "event" and first_event_ms is None:
                first_event_ms = round((time.perf_counter() - started) * 1000, 2)
            if event["type"] == "error":
                raise RuntimeError(event["message"])
            if event["type"] == "result":
                result = event["data"]
    if not result or not result["families"]:
        raise RuntimeError("The deployed demo did not return a plan.")
    assert result["usage"]["calls"] == 0
    return {"total_ms": round((time.perf_counter() - started) * 1000, 2), "first_event_ms": first_event_ms,
            "server_timings_ms": result.get("timings_ms"), "families": len(result["families"]),
            "variants": sum(len(f["variants"]) for f in result["families"]), "solver_status": result["status"]}


async def measure(args):
    report = {"base_url": args.base_url, "measured_at_utc": datetime.now(timezone.utc).isoformat(),
              "profile": "Synthetic data-analytics profile + official Cornell snapshot",
              "mode": "No OpenAI calls; serial requests from the test computer; includes network latency",
              "first_request_context": args.first_request_context}
    async with httpx.AsyncClient(base_url=args.base_url.rstrip("/"), timeout=180, follow_redirects=True) as client:
        response, report["first_health_request"] = await get_once(client, "/api/health")
        assert response.json()["status"] == "ok"
        report["page_samples"] = [(await get_once(client, "/"))[1] for _ in range(args.runs)]
        rosters, report["rosters_first"] = await get_once(client, "/api/rosters")
        report["rosters_first"]["cornell_live"] = rosters.json().get("live")
        _, report["rosters_cached"] = await get_once(client, "/api/rosters")
        report["demo_samples"] = []
        for i in range(args.runs):
            sample = await plan_once(client)
            report["demo_samples"].append(sample)
            print(f"Demo {i+1}/{args.runs}: {sample['total_ms']} ms, server {sample['server_timings_ms']}", flush=True)
    report["summary_ms"] = {}
    for key in ("page_samples", "demo_samples"):
        values = [row["total_ms"] for row in report[key]]
        report["summary_ms"][key] = {"median": round(statistics.median(values), 2), "min": min(values), "max": max(values)}
    server = [s["server_timings_ms"] for s in report["demo_samples"] if s["server_timings_ms"]]
    if server:
        for key in ("pipeline", "optimizer"):
            values = [s[key] for s in server]
            report["summary_ms"]["server_" + key] = {"median": round(statistics.median(values), 2), "min": min(values), "max": max(values)}
    Path(args.output).write_text(json.dumps(report, indent=2))
    print(json.dumps(report["summary_ms"], indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url")
    parser.add_argument("--runs", type=int, choices=range(1, 11), default=5)
    parser.add_argument("--output", default="benchmark-results.json")
    parser.add_argument("--first-request-context", default="Service state not independently verified; do not label this a cold start")
    asyncio.run(measure(parser.parse_args()))
