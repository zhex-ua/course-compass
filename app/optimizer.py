"""CP-SAT scheduling. No language model is allowed to relax these constraints."""
import itertools
import math
import re
from ortools.sat.python import cp_model

from .catalog import bundle_conflict
from .models import Course, PlanRequest, passed, prerequisites_met, requirement_met


def eligible_pool(courses: list[Course], req: PlanRequest, threshold=35):
    eligible, excluded = [], []
    completed = {c.code for c in req.completed if passed(c)}
    # Keep even low-scoring corequisites before applying the relevance cutoff.
    needed = set(req.required)
    for course in courses:
        if course.assessment:
            needed.update(r.code for alternative in course.assessment.corequisites for r in alternative)
    for course in courses:
        a, reason = course.assessment, None
        if course.career != req.career:
            reason = "Outside your selected undergraduate/graduate level."
        elif {course.code, *course.aliases} & completed:
            reason = "Already completed (including a published cross-listing)."
        elif not course.bundles:
            reason = "No open, complete section bundle fits the campus and time constraints."
        elif not a:
            reason = "Not assessed; no eligibility decision has been made."
        elif course.assessment_error:
            reason = "Assessment failed source validation: " + course.assessment_error
        elif a.requires_review and course.code not in req.confirmed_permissions:
            reason = "Manual review required: " + a.review_reason
        elif course.prereq_text and not a.prerequisites:
            reason = "Published prerequisite could not be reduced to verified course requirements."
        elif course.coreq_text and not a.corequisites:
            reason = "Published corequisite could not be reduced to verified course requirements."
        elif not prerequisites_met(a.prerequisites, req.completed):
            reason = "A prerequisite is missing, not passed, or still in progress."
        elif set(re.findall(r"\b[A-Z]{2,10}\s+\d{4}\b", course.forbidden_text)) & completed:
            reason = "Published forbidden-overlap list includes a completed course; review credit eligibility."
        elif a.score < threshold and course.code not in needed:
            reason = f"Career relevance {a.score}/100 is below the current cutoff {threshold}."
        if reason:
            excluded.append({"code": course.code, "title": course.title, "reason": reason, "source": course.source})
        else:
            eligible.append(course)
    # One underlying Cornell course, one decision variable, regardless of listing.
    canonical = {}
    for course in sorted(eligible, key=lambda c: (c.code in req.required, c.assessment.score), reverse=True):
        if course.canonical in canonical:
            canonical[course.canonical].aliases = sorted(set(canonical[course.canonical].aliases) | {course.code})
            excluded.append({"code": course.code, "title": course.title, "reason": "Combined with its cross-listed course.", "source": course.source})
        else:
            canonical[course.canonical] = course
    eligible = list(canonical.values())
    # All courses were scored; bounded optimization pool retains required/corequisite closure.
    retained = sorted(eligible, key=lambda c: (bool({c.code, *c.aliases} & needed), c.assessment.score), reverse=True)[:45]
    for c in eligible:
        if c not in retained:
            excluded.append({"code": c.code, "title": c.title, "reason": "Outside the bounded 45-course optimization pool.", "source": c.source})
    return retained, excluded


class ScheduleModel:
    def __init__(self, courses, req):
        self.courses, self.req = courses, req
        self.options = [(i, b) for i, c in enumerate(courses) for b in c.bundles]
        self.conflicts = [(a, b) for a in range(len(self.options)) for b in range(a + 1, len(self.options))
                          if self.options[a][0] != self.options[b][0]
                          and bundle_conflict(self.options[a][1], self.options[b][1])]
        self.by_code = {code: i for i, c in enumerate(courses) for code in [c.code, *c.aliases]}

    def solve(self, objective="career", floor=0, prior=(), fixed=None, avoid=(), preserve=(), avoid_sets=()):
        m = cp_model.CpModel()
        xs = [m.new_bool_var(f"course_{i}") for i in range(len(self.courses))]
        ys = [m.new_bool_var(f"bundle_{j}") for j in range(len(self.options))]
        for i in range(len(xs)):
            m.add(sum(ys[j] for j, (ci, _) in enumerate(self.options) if ci == i) == xs[i])
        m.add(sum(xs) == self.req.course_count)
        credits = sum(round(b.credits * 2) * ys[j] for j, (_, b) in enumerate(self.options))
        m.add(credits >= round(2 * self.req.min_credits))
        m.add(credits <= round(2 * self.req.max_credits))
        for code in self.req.required:
            if code not in self.by_code:
                return None, "MISSING_REQUIRED_COURSE"
            m.add(xs[self.by_code[code]] == 1)
        for i, c in enumerate(self.courses):
            # Ordinary prerequisites were checked ONLY against prior completed courses.
            alternatives = c.assessment.corequisites
            if alternatives:
                routes = []
                for n, option in enumerate(alternatives):
                    if not option:
                        continue
                    route = m.new_bool_var(f"coreq_{i}_{n}")
                    for requirement in option:
                        if requirement_met(requirement, self.req.completed):
                            continue
                        if requirement.code in self.by_code and not requirement.min_grade:
                            m.add(route <= xs[self.by_code[requirement.code]])
                        else:
                            m.add(route == 0)
                    routes.append(route)
                m.add(sum(routes) >= xs[i])
            for code in re.findall(r"\b[A-Z]{2,10}\s+\d{4}\b", c.forbidden_text):
                other = self.by_code.get(code)
                if other is not None and other != i:
                    m.add(xs[i] + xs[other] <= 1)
        for a, b in self.conflicts:
            m.add(ys[a] + ys[b] <= 1)
        quality = sum(c.assessment.score * xs[i] for i, c in enumerate(self.courses))
        m.add(quality >= floor)
        for selected in prior:
            # Fixed course count: different families must replace two courses (one for n=2).
            m.add(sum(xs[i] for i in selected) <= self.req.course_count - min(2, self.req.course_count - 1))
        if fixed is not None:
            for i, x in enumerate(xs):
                m.add(x == int(i in fixed))
        for i in preserve:
            m.add(xs[i] == 1)
        for selected in avoid_sets:
            m.add(sum(xs[i] for i in selected) <= self.req.course_count - 1)
        for previous_options in avoid:
            m.add(sum(ys[j] for j in previous_options) <= self.req.course_count - 1)
        day_vars = []
        for day in "MTWRFSU":
            used = m.new_bool_var(f"day_{day}")
            affected = [ys[j] for j, (_, b) in enumerate(self.options) if any(day in t.days for t in b.meetings)]
            if affected:
                m.add_max_equality(used, affected)
            else:
                m.add(used == 0)
            day_vars.append(used)
        earliness = sum(sum(max(0, 660 - t.start) * len(t.days) for t in b.meetings) * ys[j]
                        for j, (_, b) in enumerate(self.options))
        if objective == "foundation":
            m.maximize(sum((c.assessment.foundation * 3 + c.assessment.score) * xs[i] for i, c in enumerate(self.courses)))
        elif objective == "breadth":
            covered = []
            for subject in sorted({c.subject for c in self.courses}):
                v = m.new_bool_var("subject_" + subject)
                m.add_max_equality(v, [xs[i] for i, c in enumerate(self.courses) if c.subject == subject])
                covered.append(v)
            m.maximize(sum(covered) * 1000 + quality)
        elif objective == "days":
            m.minimize(sum(day_vars) * 100000 + earliness)
        elif objective == "mornings":
            m.minimize(earliness * 100 + sum(day_vars))
        else:
            m.maximize(quality * 100000 - sum(day_vars) * 1000 - earliness)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 4
        solver.parameters.num_search_workers = 1
        solver.parameters.random_seed = 42
        status = solver.solve(m)
        label = solver.status_name(status)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None, label
        selected = {i for i, x in enumerate(xs) if solver.value(x)}
        chosen = [j for j, y in enumerate(ys) if solver.value(y)]
        result = {"selected": selected, "options": chosen, "quality": int(solver.value(quality)), "status": label,
                  "objective": objective, "objective_value": solver.objective_value,
                  "best_bound": solver.best_objective_bound}
        self.verify(result)
        return result, label

    def verify(self, result):
        """Independent post-solve validation catches model or extraction mistakes."""
        chosen = [self.options[j] for j in result["options"]]
        assert len(chosen) == self.req.course_count
        assert len({i for i, _ in chosen}) == len(chosen)
        credits = sum(b.credits for _, b in chosen)
        assert self.req.min_credits <= credits <= self.req.max_credits
        assert not any(bundle_conflict(a, b) for (_, a), (_, b) in itertools.combinations(chosen, 2))
        codes = {code for i, _ in chosen for code in [self.courses[i].code, *self.courses[i].aliases]}
        assert set(self.req.required) <= codes
        for i, _ in chosen:
            c = self.courses[i]
            assert prerequisites_met(c.assessment.prerequisites, self.req.completed)
            assert not c.assessment.corequisites or any(
                alternative and all(requirement_met(r, self.req.completed) or (r.code in codes and not r.min_grade)
                                    for r in alternative) for alternative in c.assessment.corequisites)

    def serialize(self, result, name):
        items, days, starts = [], set(), []
        for j in result["options"]:
            i, bundle = self.options[j]
            c = self.courses[i]
            items.append({**c.model_dump(exclude={"bundles"}), "bundle": bundle.model_dump()})
            for t in bundle.meetings:
                days.update(t.days)
                starts.append(t.start)
        return {"name": name, "courses": items, "credits": sum(c["bundle"]["credits"] for c in items),
                "quality": result["quality"], "match": round(result["quality"] / len(items)),
                "days": len(days), "earliest": min(starts, default=0), "status": result["status"],
                "objective": result["objective"], "objective_value": result["objective_value"],
                "best_bound": result["best_bound"]}


def optimize(courses, req, threshold=35):
    pool, excluded = eligible_pool(courses, req, threshold)
    info = {"candidate_count": len(courses), "eligible_count": len(pool), "excluded": excluded,
            "families": [], "threshold": threshold, "quality_floor": None}
    if len(pool) < req.course_count:
        return {**info, "status": "INSUFFICIENT_ELIGIBLE_COURSES"}
    model = ScheduleModel(pool, req)
    best, status = model.solve()
    if not best:
        return {**info, "status": status}
    floor = math.ceil(best["quality"] * .85)
    anchors = [best]
    for objective in ["foundation", "breadth"]:
        solution, _ = model.solve(objective, floor=floor, prior=[s["selected"] for s in anchors])
        if solution:
            anchors.append(solution)
    used_variant_sets = [s["selected"] for s in anchors]
    for i, anchor in enumerate(anchors):
        core = set(sorted(anchor["selected"], key=lambda j: (pool[j].code in req.required, pool[j].assessment.score), reverse=True)[:req.course_count-1])
        variants, used = [model.serialize(anchor, "Balanced timetable")], [anchor["options"]]
        for objective, name in [("days", "Fewer campus days"), ("mornings", "Later mornings")]:
            solution, _ = model.solve(objective, fixed=anchor["selected"], avoid=used)
            if solution:
                used.append(solution["options"])
                variants.append(model.serialize(solution, name))
        # Collapse time-identical alternatives (e.g. different teachers at the same time).
        signatures, unique = set(), []
        for variant in variants:
            signature = tuple(sorted((c["code"], tuple(sorted((m["days"], m["start"], m["end"], m["date_start"], m["date_end"])
                                    for s in c["bundle"]["sections"] for m in s["meetings"]))) for c in variant["courses"]))
            if signature not in signatures:
                signatures.add(signature)
                unique.append(variant)
        # A family can also contain one-elective alternatives, anchored to the same core.
        # Never drift through chained similarity, or duplicate another family's course set.
        if len(unique) < 3:
            solution, _ = model.solve("career", floor=floor, preserve=core, avoid_sets=used_variant_sets)
            if solution:
                used_variant_sets.append(solution["selected"])
                unique.append(model.serialize(solution, "One elective swap"))
        names = {"career": "Career focus", "foundation": "Build your foundations", "breadth": "Broaden your perspective"}
        info["families"].append({"id": f"family-{i+1}", "name": names[anchor["objective"]],
                                 "core": [pool[j].code for j in sorted(core)],
                                 "anchor_courses": [pool[j].code for j in sorted(anchor["selected"])], "variants": unique})
    return {**info, "status": status, "quality_floor": floor, "reference_quality": best["quality"],
            "family_distance": min(2, req.course_count - 1),
            "search_note": "Bounded search over retrieved subjects and up to 45 eligible courses. Family quality is at least 85% of the best found schedule; family anchors replace at least two courses (one for a two-course plan). Variants preserve the shared core and change sections or at most one elective. Solver status applies to each stated objective, not to the university-wide catalog."}
