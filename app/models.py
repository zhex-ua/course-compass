"""Public request types and the compact boundary between catalog, LLM, and solver."""
import re
from typing import Literal
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator


def course_code(value: str) -> str:
    value = re.sub(r"\s+", " ", value.strip().upper())
    match = re.fullmatch(r"([A-Z]{2,10})\s*(\d{4})", value)
    if not match:
        raise ValueError("Use a Cornell course code, e.g. CS 1110")
    return f"{match[1]} {match[2]}"


GRADES = {"A+": 4.3, "A": 4, "A-": 3.7, "B+": 3.3, "B": 3, "B-": 2.7,
          "C+": 2.3, "C": 2, "C-": 1.7, "D+": 1.3, "D": 1, "D-": .7, "F": 0}


class CompletedCourse(BaseModel):
    code: str
    grade: str = "P"
    status: Literal["completed", "in_progress", "unknown"] = "completed"

    _code = field_validator("code")(course_code)

    @field_validator("grade")
    @classmethod
    def normalize_grade(cls, v):
        v = v.strip().upper()
        if v not in {*GRADES, "P", "S", "U", "W", "I", "", "UNKNOWN"}:
            raise ValueError("Unsupported grade; use a letter grade, P/S, or UNKNOWN")
        return v


class BlockedTime(BaseModel):
    day: Literal["M", "T", "W", "R", "F", "S", "U"]
    start: int = Field(ge=0, lt=1440)
    end: int = Field(gt=0, le=1440)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("Blocked time must end after it starts")
        return self


class PlanRequest(BaseModel):
    api_key: SecretStr = Field(default=SecretStr(""), exclude=True)
    model: Literal["gpt-4.1-mini", "gpt-4.1"] = "gpt-4.1-mini"
    goal: str = Field(min_length=10, max_length=3000)
    roster: str = Field(default="SP27", pattern=r"^(SP|FA|SU|WI)\d{2}$")
    career: Literal["UG", "GR"] = "UG"
    campus: Literal["MAIN", "NYCT", "ANY"] = "MAIN"
    completed: list[CompletedCourse] = Field(default_factory=list, max_length=150)
    min_credits: float = Field(default=12, ge=1, le=24, multiple_of=.5)
    max_credits: float = Field(default=16, ge=1, le=24, multiple_of=.5)
    course_count: int = Field(default=4, ge=2, le=6)
    earliest: int = Field(default=540, ge=420, le=1200)
    latest: int = Field(default=1080, ge=480, le=1380)
    blocked: list[BlockedTime] = Field(default_factory=list, max_length=30)
    required: list[str] = Field(default_factory=list, max_length=6)
    confirmed_permissions: list[str] = Field(default_factory=list, max_length=40)
    subject_hints: list[str] = Field(default_factory=list, max_length=8)
    demo: bool = False

    @field_validator("required", "confirmed_permissions")
    @classmethod
    def valid_codes(cls, vs):
        return list(dict.fromkeys(course_code(v) for v in vs))

    @field_validator("subject_hints")
    @classmethod
    def valid_subjects(cls, vs):
        if any(not re.fullmatch(r"[A-Z]{2,10}", v) for v in vs):
            raise ValueError("Subject hints must be uppercase Cornell subject codes")
        return list(dict.fromkeys(vs))

    @model_validator(mode="after")
    def consistent(self):
        if self.max_credits < self.min_credits or self.latest <= self.earliest:
            raise ValueError("Check the credit and time ranges")
        if len(self.required) > self.course_count:
            raise ValueError("Too many required courses for the selected course count")
        return self


class Requirement(BaseModel):
    code: str
    min_grade: str | None
    _code = field_validator("code")(course_code)

    @field_validator("min_grade")
    @classmethod
    def grade(cls, v):
        if v is not None and v not in GRADES:
            raise ValueError("Minimum grade must be a letter grade or null")
        return v


class Assessment(BaseModel):
    code: str
    score: int = Field(ge=0, le=100)
    foundation: int = Field(ge=0, le=100)
    skills: list[str] = Field(max_length=5)
    reason: str
    evidence: str
    # Disjunctive normal form: any alternative; all courses within an alternative.
    prerequisites: list[list[Requirement]]
    corequisites: list[list[Requirement]]
    requires_review: bool
    review_reason: str
    _code = field_validator("code")(course_code)


class AssessmentBatch(BaseModel):
    courses: list[Assessment]


class SearchDecision(BaseModel):
    subjects: list[str] = Field(max_length=8)
    keywords: list[str] = Field(max_length=12)
    reason: str


class TranscriptResult(BaseModel):
    courses: list[CompletedCourse]
    notes: list[str]


class Meeting(BaseModel):
    days: str
    start: int
    end: int
    date_start: str
    date_end: str


class Section(BaseModel):
    number: int
    component: str
    label: str
    status: str
    meetings: list[Meeting]


class Bundle(BaseModel):
    id: str
    credits: float
    sections: list[Section]

    @property
    def meetings(self):
        return [m for section in self.sections for m in section.meetings]


class Course(BaseModel):
    code: str
    canonical: str
    aliases: list[str] = Field(default_factory=list)
    title: str
    description: str
    subject: str
    career: str
    source: str
    prereq_text: str
    coreq_text: str
    restriction_text: str
    forbidden_text: str
    notes: str
    bundles: list[Bundle]
    warnings: list[str] = Field(default_factory=list)
    assessment: Assessment | None = None
    assessment_error: str = ""


def passed(item: CompletedCourse, minimum: str | None = None) -> bool:
    if item.status != "completed":
        return False
    if minimum:
        return item.grade in GRADES and GRADES[item.grade] >= GRADES[minimum]
    return item.grade in {"P", "S"} or GRADES.get(item.grade, 0) > 0


def requirement_met(req: Requirement, transcript: list[CompletedCourse]) -> bool:
    return any(c.code == req.code and passed(c, req.min_grade) for c in transcript)


def prerequisites_met(alternatives, transcript):
    return not alternatives or any(option and all(requirement_met(r, transcript) for r in option)
                                   for option in alternatives)
