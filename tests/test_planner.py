import asyncio
import json
from copy import deepcopy
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agent import OpenAIBrain, attach_assessments, run_agent
from app.catalog import CornellCatalog, bundle_conflict, demo_catalog, normalize, overlap
from app.main import app
from app.models import Assessment, Bundle, CompletedCourse, Course, Meeting, PlanRequest, Requirement, SearchDecision, Section
from app.optimizer import eligible_pool, optimize


def request(**kwargs):
    return PlanRequest(**{"goal": "I want a career in data science", "course_count": 2,
                          "min_credits": 6, "max_credits": 6, **kwargs})


def meeting(start=600, end=650, days="M", **kwargs):
    return Meeting(start=start, end=end, days=days, date_start=kwargs.get("date_start", "2027-01-25"),
                   date_end=kwargs.get("date_end", "2027-05-11"))


def course(code, start=600, score=90, prerequisites=None, corequisites=None, **kwargs):
    a = Assessment(code=code, score=score, foundation=90, skills=["data"], reason="Useful", evidence="Data",
                   prerequisites=prerequisites or [], corequisites=corequisites or [], requires_review=False, review_reason="")
    bundle = Bundle(id=code, credits=3, sections=[Section(number=int(code.split()[1]), component="LEC", label="001", status="O",
                                                        meetings=[meeting(start=start, end=start+50)])])
    return Course(code=code, canonical=code, subject=code.split()[0], title="Data", description="Data science", career="UG",
                  source="https://classes.cornell.edu/", prereq_text="" if not prerequisites else "Published prerequisite",
                  coreq_text="" if not corequisites else "Published corequisite", restriction_text="", forbidden_text="", notes="",
                  bundles=[bundle], assessment=a, **kwargs)


def requirement(code, grade=None):
    return Requirement(code=code, min_grade=grade)


def test_ordinary_prerequisite_cannot_be_taken_concurrently():
    courses = [course("CS 1110"), course("CS 2110", 700, prerequisites=[[requirement("CS 1110")]])]
    result = optimize(courses, request())
    assert not result["families"]
    assert "prerequisite" in result["excluded"][0]["reason"]


@pytest.mark.parametrize("grade,status,eligible", [("A","completed",True),("C","completed",False),
    ("A","in_progress",False),("F","completed",False),("P","completed",False),("UNKNOWN","completed",False)])
def test_grade_and_completion_requirements(grade, status, eligible):
    target=course("CS 2110", prerequisites=[[requirement("CS 1110", "B-")]])
    pool,_=eligible_pool([target], request(completed=[CompletedCourse(code="CS 1110",grade=grade,status=status)]))
    assert bool(pool) == eligible


def test_or_of_and_prerequisite_logic():
    target=course("CS 3000", prerequisites=[[requirement("CS 1110"),requirement("MATH 1110")],[requirement("CS 2110")]])
    pool,_=eligible_pool([target],request(completed=[{"code":"CS 2110","grade":"B"}]))
    assert pool
    pool,_=eligible_pool([target],request(completed=[{"code":"CS 1110","grade":"A"}]))
    assert not pool


def test_corequisite_is_selected_even_if_its_score_is_low():
    courses=[course("CS 2000",600,corequisites=[[requirement("MATH 2000")]]),course("MATH 2000",700,score=1)]
    result=optimize(courses,request())
    assert len(result["families"])==1
    assert set(result["families"][0]["anchor_courses"])=={"CS 2000","MATH 2000"}


def test_missing_corequisite_is_infeasible():
    courses=[course("CS 2000",600,corequisites=[[requirement("MATH 2000")]]),course("INFO 2000",700)]
    assert not optimize(courses,request())["families"]


def test_time_conflict_and_back_to_back_boundary():
    assert overlap(meeting(),meeting(start=649,end=700))
    assert not overlap(meeting(),meeting(start=650,end=700))
    assert not overlap(meeting(),meeting(days="T"))


def test_disjoint_dates_and_weekday_occurrence():
    assert not overlap(meeting(date_end="2027-03-01"),meeting(date_start="2027-03-02"))
    assert not overlap(meeting(date_start="2027-01-26",date_end="2027-01-27"),meeting())


def test_conflicting_courses_never_return_a_plan():
    assert not optimize([course("CS 2000"),course("INFO 2000")],request())["families"]


def test_credit_range_required_and_completed_are_hard_constraints():
    courses=[course("CS 2000",600),course("INFO 2000",700),course("ECON 2000",800)]
    result=optimize(courses,request(required=["ECON 2000"], completed=[{"code":"CS 2000","grade":"A"}]))
    assert set(result["families"][0]["anchor_courses"])=={"INFO 2000","ECON 2000"}
    assert not optimize(courses,request(min_credits=7,max_credits=8))["families"]


def test_crosslisted_courses_cannot_double_count():
    a,b=course("CS 2000",600),course("INFO 2000",700)
    a.canonical=b.canonical="shared"
    assert not optimize([a,b],request())["families"]


def test_forbidden_overlap_with_transcript_or_same_plan():
    a,b=course("CS 2000",600),course("INFO 2000",700)
    a.forbidden_text="CS 2000, INFO 2000"
    assert not optimize([a,b],request())["families"]
    assert not eligible_pool([a],request(completed=[{"code":"INFO 2000","grade":"A"}]))[0]


def test_missing_rule_parse_is_never_treated_as_no_prerequisite():
    c=course("CS 2000")
    c.prereq_text="Permission and experience required"
    assert not eligible_pool([c],request(confirmed_permissions=["CS 2000"]))[0]


def test_fabricated_evidence_and_course_rules_cannot_be_overridden():
    c=course("CS 2000")
    a=c.assessment.model_copy(deep=True);a.evidence="Invented catalog evidence"
    attach_assessments([c],[a])
    assert not eligible_pool([c],request(confirmed_permissions=["CS 2000"]))[0]
    c=course("CS 2000");a=c.assessment.model_copy(deep=True)
    a.corequisites=[[requirement("MATH 9999")]]
    attach_assessments([c],[a])
    assert c.assessment_error


def test_real_required_components_and_unknown_time_exclusion():
    raw=next(c for c in demo_catalog()["courses"]["CS"] if c["catalogNbr"]=="1110")
    c=normalize(raw,request())
    assert c.bundles
    assert all({s.component for s in b.sections}=={"LEC","DIS"} for b in c.bundles)
    modified=deepcopy(raw)
    for g in modified["enrollGroups"]:
        for section in g["classSections"]:
            if section["ssrComponent"]=="LEC": section["meetings"]=[]
    assert not normalize(modified,request()).bundles


def test_families_replace_courses_variants_keep_same_core():
    courses=[course(f"CS {1000+i}",600+i*60,score=90-i) for i in range(8)]
    # Add an actual alternative time for each course.
    for c in courses:
        alt=c.bundles[0].model_copy(deep=True);alt.id+='-alt';alt.sections[0].meetings[0].days="T";c.bundles.append(alt)
    result=optimize(courses,request(course_count=4,min_credits=12,max_credits=12))
    assert len(result["families"])==3
    for i,a in enumerate(result["families"]):
        for b in result["families"][i+1:]:assert len(set(a["anchor_courses"])-set(b["anchor_courses"]))>=2
        for v in a["variants"]:
            assert set(a["core"]) <= {c["code"] for c in v["courses"]}
            assert len(set(a["anchor_courses"])-{c["code"] for c in v["courses"]})<=1
            assert v["quality"]>=result["quality_floor"]
            assert v["credits"]==12


@pytest.mark.asyncio
async def test_cornell_full_subject_request_and_public_cache():
    seen=[]
    def handler(request):
        seen.append(request)
        return httpx.Response(200,json={"status":"success","data":{"classes":[]}})
    client=CornellCatalog(httpx.MockTransport(handler))
    assert await client.courses("SP27","CS")==[]
    assert await client.courses("SP27","CS")==[]
    assert len(seen)==1
    assert dict(seen[0].url.params)=={"roster":"SP27","subject":"CS"}


@pytest.mark.asyncio
async def test_openai_sdk_structured_wire_contract_and_private_key_exclusion():
    brain=OpenAIBrain("test-only-placeholder", "gpt-4.1-mini")
    seen=[]
    def handler(req):
        body=json.loads(req.content);seen.append(body)
        assert "test-only-placeholder" not in req.content.decode()
        assert body["response_format"]["type"]=="json_schema"
        return httpx.Response(200,json={"id":"test","object":"chat.completion","created":1,"model":"gpt-4.1-mini",
             "choices":[{"index":0,"message":{"role":"assistant","content":json.dumps({"subjects":["CS","INFO"],"keywords":["data"],"reason":"Relevant subjects"})},"finish_reason":"stop"}],
             "usage":{"prompt_tokens":100,"completion_tokens":20,"total_tokens":120}})
    old=brain.client
    from openai import AsyncOpenAI
    brain.client=AsyncOpenAI(api_key="test-only-placeholder",http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await old.close()
    try:
        decision=await brain.select(request(api_key="test-only-placeholder"),[{"value":"CS"},{"value":"INFO"}])
        assert decision.subjects==["CS","INFO"]
        assert brain.usage["calls"]==1
    finally:await brain.close()


@pytest.mark.asyncio
async def test_agent_reflects_and_expands_on_infeasible_search():
    class Brain:
        usage={"calls":0}
        def __init__(self):self.feedback=[]
        async def select(self,req,subjects,previous):
            self.feedback.append(previous)
            return SearchDecision(subjects=["CS"] if previous is None else ["INFO"],keywords=[],reason="Expand")
    class Catalog:
        async def subjects(self,roster):return [{"value":"CS"},{"value":"INFO"}]
        async def courses(self,roster,subject):return []
    brain=Brain();events=[]
    async def emit(*args):events.append(args)
    result=await run_agent(request(),Catalog(),emit,brain)
    assert result["retrieved_subjects"]==["CS","INFO"]
    assert brain.feedback[1]["status"]=="INSUFFICIENT_ELIGIBLE_COURSES"


def test_http_key_validation_does_not_echo_key():
    with TestClient(app) as client:
        response=client.post('/api/plan',json={"goal":"too short","api_key":"test-sensitive-placeholder"})
        assert response.status_code==422
        assert "test-sensitive-placeholder" not in response.text
        assert client.post('/api/plan',json={"goal":"I want a data career"}).status_code==400
        assert client.get('/').status_code==200
        assert client.get('/api/health').json()=={"status":"ok"}


def test_transcript_upload_uses_memory_and_preserves_uncertainty(monkeypatch):
    from app import main
    from app.models import TranscriptResult
    class Brain:
        def __init__(self,key,model):assert key=="test-placeholder"
        async def transcript(self,text):
            assert 'CS 1110' in text
            return TranscriptResult(courses=[CompletedCourse(code="CS 1110",grade="A",status="in_progress")],notes=["Verify completion."])
        async def close(self):pass
    monkeypatch.setattr(main,"OpenAIBrain",Brain)
    with TestClient(app) as client:
        response=client.post('/api/transcript',data={"api_key":"test-placeholder"},files={"file":("sample.txt",b"CS 1110 A current semester; not yet completed.","text/plain")})
        assert response.status_code==200
        assert response.json()["courses"][0]["status"]=="in_progress"
        assert "test-placeholder" not in response.text
        response=client.post('/api/transcript',data={"api_key":"test-placeholder"},files={"file":("bad.pdf",b"not a pdf","application/pdf")})
        assert response.status_code==400


def test_streaming_endpoint_delivers_result_without_credentials():
    with TestClient(app) as client:
        req=request(demo=True,course_count=4,min_credits=12,max_credits=16).model_dump()
        response=client.post('/api/plan',json=req)
        assert response.status_code==200
        messages=[json.loads(line) for line in response.text.splitlines()]
        assert messages[-1]["type"]=="result"
        assert any(m.get("stage")=="optimization" for m in messages)
        assert "api_key" not in response.text


def test_uploads_do_not_roll_to_disk_and_large_bodies_are_rejected(monkeypatch):
    import starlette.formparsers as parser
    original=parser.SpooledTemporaryFile
    files=[]
    def tracked(*args,**kwargs):
        file=original(*args,**kwargs);files.append(file);return file
    monkeypatch.setattr(parser,"SpooledTemporaryFile",tracked)
    with TestClient(app) as client:
        response=client.post('/api/transcript',data={"api_key":"test-placeholder"},
                             files={"file":("bad.pdf",b"x"*(2*1024*1024),"application/pdf")})
        assert response.status_code==400
        assert files and all(not f._rolled for f in files)
        response=client.post('/api/plan',content=b"x"*(7*1024*1024),headers={"content-type":"application/json"})
        assert response.status_code==413


@pytest.mark.asyncio
async def test_real_snapshot_demo_end_to_end():
    async def emit(*args):pass
    req=request(demo=True,course_count=4,min_credits=12,max_credits=16,
                completed=[{"code":c,"grade":"A"} for c in ["CS 1110","MATH 1110","MATH 1710","ECON 1110"]])
    result=await run_agent(req,None,emit)
    assert len(result["families"])>=2
    assert result["usage"]["calls"]==0
    assert result["raw_count"]==308
    for family in result["families"]:
        for variant in family["variants"]:
            assert 12 <= variant["credits"] <= 16
            bundles=[Bundle.model_validate(c["bundle"]) for c in variant["courses"]]
            for i,a in enumerate(bundles):
                for b in bundles[i+1:]:assert not bundle_conflict(a,b)
