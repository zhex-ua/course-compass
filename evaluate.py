"""Reproducible real-roster evaluation; explicitly no paid-model quality claims."""
import asyncio
import json
import time
from pathlib import Path
from app.agent import run_agent
from app.models import PlanRequest


async def main():
    completed = [{"code": code, "grade": "A"} for code in ["CS 1110", "MATH 1110", "MATH 1710", "ECON 1110"]]
    scenarios = [
        ("baseline", {}, True),
        ("without_programming_prerequisite", {"completed": completed[1:]}, True),
        ("impossible_credit_load", {"min_credits": 24, "max_credits": 24, "course_count": 2}, False),
        ("all_weekdays_blocked", {"blocked": [{"day": d, "start": 0, "end": 1440} for d in "MTWRF"]}, False),
    ]
    results = []
    async def emit(*args): pass
    for name, settings, expected in scenarios:
        req = PlanRequest(**{"goal": "I want to become a product or data analyst.", "demo": True,
                             "completed": completed, **settings})
        started = time.perf_counter()
        output = await run_agent(req, None, emit)
        plans = [v for f in output["families"] for v in f["variants"]]
        results.append({"scenario": name, "expected_feasible": expected, "returned_feasible": bool(plans),
                        "expectation_met": bool(plans) == expected, "status": output["status"],
                        "families": len(output["families"]), "variants": len(plans),
                        "eligible_courses": output["eligible_count"], "seconds": round(time.perf_counter()-started, 3),
                        "credits": [p["credits"] for p in plans],
                        "course_sets": [[c["code"] for c in p["courses"]] for p in plans]})
    payload = {"mode": "Real Cornell SP27 snapshot; deterministic sample scoring; no OpenAI calls",
               "record_count": output["raw_count"], "snapshot_timestamp": output["retrieved_at"], "scenarios": results}
    path = Path(__file__).parent / "evaluation-results.json"
    path.write_text(json.dumps(payload, indent=2))
    print(json.dumps(payload, indent=2))
    if not all(r["expectation_met"] for r in results): raise SystemExit(1)


if __name__ == "__main__": asyncio.run(main())
