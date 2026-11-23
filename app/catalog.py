"""Cornell API, public-data cache, and conservative section normalization."""
import asyncio
import datetime as dt
import itertools
import json
import re
import time
from pathlib import Path

import httpx
from .models import Bundle, Course, Meeting, PlanRequest, Section

DATA = Path(__file__).resolve().parent.parent / "data"
API = "https://classes.cornell.edu/api/2.0/"


class CatalogError(Exception):
    pass


class CornellCatalog:
    """One process-wide instance: at most one API request per second, no user data."""
    def __init__(self, transport=None):
        self.lock = asyncio.Lock()
        self.last_request = 0.0
        self.cache = {}
        self.transport = transport

    async def get(self, path, **params):
        key = (path, tuple(sorted(params.items())))
        async with self.lock:
            cached = self.cache.get(key)
            if cached and time.monotonic() - cached[0] < 300:
                return cached[1]
            await asyncio.sleep(max(0, 1.05 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            headers = {"User-Agent": "CourseCompass/1.0 (educational course planner)"}
            if cached and cached[2]:
                headers["If-Modified-Since"] = cached[2]
            try:
                async with httpx.AsyncClient(timeout=35, transport=self.transport) as client:
                    response = await client.get(API + path, params=params, headers=headers)
                if response.status_code == 304 and cached:
                    payload = cached[1]
                elif response.status_code == 404 and path == "search/classes.json":
                    payload = {"classes": []}
                else:
                    response.raise_for_status()
                    body = response.json()
                    if body.get("status") != "success":
                        raise CatalogError("Cornell did not return a successful response.")
                    payload = body["data"]
                if len(self.cache) > 256:
                    self.cache.pop(next(iter(self.cache)))
                self.cache[key] = (time.monotonic(), payload,
                                   response.headers.get("last-modified") or (cached[2] if cached else None))
                return payload
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                raise CatalogError("Cornell is unavailable or returned invalid data. Please retry.") from exc

    async def rosters(self):
        return (await self.get("config/rosters.json"))["rosters"]

    async def subjects(self, roster):
        return (await self.get("config/subjects.json", roster=roster))["subjects"]

    async def courses(self, roster, subject):
        # q deliberately omitted: it misses matches in descriptions.
        return (await self.get("search/classes.json", roster=roster, subject=subject))["classes"]


def date(value):
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(value or "", fmt).date().isoformat()
        except ValueError:
            pass
    return None


def minutes(value):
    try:
        t = dt.datetime.strptime((value or "").replace(" ", ""), "%I:%M%p")
        return t.hour * 60 + t.minute
    except ValueError:
        return None


def overlap(a: Meeting, b: Meeting):
    lo, hi = max(a.date_start, b.date_start), min(a.date_end, b.date_end)
    days = set(a.days) & set(b.days)
    if not days or lo > hi or a.start >= b.end or b.start >= a.end:
        return False
    start, end = dt.date.fromisoformat(lo), dt.date.fromisoformat(hi)
    # Date ranges can intersect without sharing an actual occurrence of a weekday.
    return any("MTWRFSU"[(start.weekday() + offset) % 7] in days
               for offset in range(min(7, (end - start).days + 1)))


def bundle_conflict(a, b):
    return any(overlap(x, y) for x in a.meetings for y in b.meetings)


def normalize(raw, request: PlanRequest) -> Course:
    code = f"{raw['subject']} {raw['catalogNbr']}"
    warnings, bundles, all_notes = set(), [], set()
    for gi, group in enumerate(raw.get("enrollGroups", [])):
        low, high = group.get("unitsMinimum"), group.get("unitsMaximum")
        if low is None or high is None or low != high or low <= 0 or (low * 2) % 1:
            warnings.add("Variable or unspecified credit value: excluded from this version.")
            continue
        components = group.get("componentsRequired", [])
        if not components:
            warnings.add("Required registration components are unspecified.")
            continue
        by_component = {component: [] for component in components}
        for s in group.get("classSections", []):
            notes = " ".join(n.get("descrlong", "") for n in s.get("notes", []))
            all_notes.add(notes)
            if request.campus != "ANY" and s.get("campus") != request.campus:
                continue
            if s.get("openStatus") != "O":
                continue
            if s.get("addConsent") not in (None, "", "N") and code not in request.confirmed_permissions:
                warnings.add("Some sections require consent; excluded unless permission is confirmed.")
                continue
            # The roster does not expose a general section-link constraint schema.
            if re.search(r"(must|only|require\w*)[^.]{0,100}(enroll|register)[^.]{0,100}(section|lab|dis|lec)", notes, re.I):
                warnings.add("Linked-section restriction needs manual verification; affected section excluded.")
                continue
            meetings, invalid = [], False
            for m in s.get("meetings", []):
                start, end = minutes(m.get("timeStart")), minutes(m.get("timeEnd"))
                ds, de = date(m.get("startDt") or s.get("startDt")), date(m.get("endDt") or s.get("endDt"))
                days = m.get("pattern", "") or ""
                if start is None or end is None or end <= start or not ds or not de or ds > de or not days or any(d not in "MTWRFSU" for d in days):
                    invalid = True
                    break
                meeting = Meeting(days=days, start=start, end=end, date_start=ds, date_end=de)
                if start < request.earliest or end > request.latest or any(
                    b.day in days and start < b.end and b.start < end for b in request.blocked
                ):
                    invalid = True
                    break
                meetings.append(meeting)
            if invalid or not meetings:
                warnings.add("Some sections have unknown times or violate your time window.")
                continue
            if s.get("ssrComponent") in by_component:
                by_component[s["ssrComponent"]].append(Section(
                    number=s["classNbr"], component=s["ssrComponent"], label=s["section"],
                    status=s["openStatus"], meetings=meetings))
        for sections in itertools.product(*(by_component[k] for k in components)):
            if any(overlap(a, b) for i, section in enumerate(sections)
                   for other in sections[i + 1:] for a in section.meetings for b in other.meetings):
                continue
            bundles.append(Bundle(id=f"{gi}:" + "-".join(str(s.number) for s in sections),
                                  credits=low, sections=list(sections)))
            if len(bundles) >= 80:
                warnings.add("Section combinations capped at 80 for this course; search is bounded.")
                break
        if len(bundles) >= 80:
            break
    aliases = set()
    for group in raw.get("enrollGroups", []):
        for alias in group.get("simpleCombinations", []):
            if alias.get("subject") and alias.get("catalogNbr"):
                aliases.add(f"{alias['subject']} {alias['catalogNbr']}")
    return Course(code=code, canonical=str(raw["crseId"]), aliases=sorted(aliases - {code}),
                  title=raw.get("titleLong") or raw.get("titleShort", ""),
                  description=raw.get("description") or "", subject=raw["subject"],
                  career=raw.get("acadCareer", ""),
                  source=f"https://classes.cornell.edu/browse/roster/{request.roster}/class/{raw['subject']}/{raw['catalogNbr']}",
                  prereq_text=raw.get("catalogPrereq") or raw.get("catalogPrereqCoreq") or "",
                  coreq_text=raw.get("catalogCoreq") or "",
                  restriction_text=" ".join(str(raw.get(k) or "") for k in ("catalogEnrollmentPriority", "catalogPermission")).strip(),
                  forbidden_text=raw.get("catalogForbiddenOverlaps") or "", notes="\n".join(sorted(all_notes)),
                  bundles=bundles, warnings=sorted(warnings))


def demo_catalog():
    return json.loads((DATA / "demo-cornell.json").read_text())
