"""Typed contracts for model-generated planning data.

Pydantic is already a project dependency through FastAPI/LangChain.  Keeping
the contracts here gives structured stages one validation path without tying
creative/free-text calls to a particular provider's JSON-schema support.
"""
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _FlexibleModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class PlanElement(_FlexibleModel):
    name: str = ""
    type: str = ""
    source_or_entry: str = ""
    future_use: str = ""


class PlanBeat(_FlexibleModel):
    num: str | int | None = None
    name: str = ""
    pov: str = ""
    time: str = ""
    location: str = ""
    known_before: list[Any] = Field(default_factory=list)
    new_facts: list[Any] = Field(default_factory=list)
    new_elements: list[PlanElement | dict] = Field(default_factory=list)
    desc: str
    end_state: str = ""


class ChapterPlan(_FlexibleModel):
    title: str
    beats: list[PlanBeat]


class PlanReview(_FlexibleModel):
    # Tool-directed research turns use action/calls instead of issues.
    action: str | None = None
    calls: list[dict] | None = None
    question: str | None = None
    source_ids: list[str] | None = None
    issues: list[dict] | None = None
    requirement_checks: list[dict] = Field(default_factory=list)
    uncertainties: list[Any] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_action_or_result(self):
        if self.action not in {"lookup", "investigate"} and self.issues is None:
            raise ValueError("最终审查结果必须包含 issues 数组")
        return self


def validate_chapter_plan(data: dict) -> dict:
    return ChapterPlan.model_validate(data).model_dump(exclude_none=True)


def validate_plan_review(data: dict) -> dict:
    return PlanReview.model_validate(data).model_dump(exclude_none=True)
