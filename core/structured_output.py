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
    thread: str = ""
    goal: str = ""
    obstacle: str = ""
    choice: str = ""
    cost: str = ""
    change: str = ""


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


class StoryState(_FlexibleModel):
    updated_through_chapter: int = 0
    story_phase: dict = Field(default_factory=dict)
    arcs: list[dict] = Field(default_factory=list)
    recent_chapter_changes: list[dict] = Field(default_factory=list)


class ChapterStrategy(_FlexibleModel):
    chapter_role: str
    primary_arc: str = ""
    secondary_arc: str = ""
    primary_change: str
    secondary_change: str = ""
    opening_pressure: str = ""
    central_choice: str = ""
    cost: str = ""
    ending_change: str
    reader_question_before: str = ""
    reader_question_after: str = ""
    avoid_repeating: list[str] = Field(default_factory=list)


class StoryDirection(_FlexibleModel):
    story_state: StoryState
    strategy: ChapterStrategy


class ProgressReview(_FlexibleModel):
    progressed: bool
    summary: str
    repeated_functions: list[dict] = Field(default_factory=list)
    missing_change: str = ""
    strategy_revision: str = ""


class BlueprintFact(_FlexibleModel):
    id: str
    statement: str
    evidence_chapters: list[int] = Field(default_factory=list)
    status: str = "observed"
    locked: bool = False


class BlueprintPlan(_FlexibleModel):
    premise: str = ""
    destination: str = ""
    ending_state: str = ""
    phases: list[dict] = Field(default_factory=list)
    arcs: list[dict] = Field(default_factory=list)
    milestones: list[dict] = Field(default_factory=list)
    chapter_roadmap: list[dict] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_roadmap_reminders(cls, value):
        if not isinstance(value, dict):
            return value
        result = dict(value)
        roadmap = []
        for source in result.get("chapter_roadmap") or []:
            if not isinstance(source, dict):
                roadmap.append(source)
                continue
            row = dict(source)
            reminders = row.get("must_not_repeat")
            if isinstance(reminders, str):
                parts = reminders.replace("\n", "；").replace(";", "；").split("；")
                row["must_not_repeat"] = [part.strip() for part in parts if part.strip()]
            elif reminders is None:
                row["must_not_repeat"] = []
            roadmap.append(row)
        result["chapter_roadmap"] = roadmap
        return result


class StoryBlueprint(_FlexibleModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    facts_through_chapter: int = 0
    facts: list[BlueprintFact] = Field(default_factory=list)
    plan: BlueprintPlan = Field(default_factory=BlueprintPlan)
    proposals: list[dict] = Field(default_factory=list)
    updated_at: str = ""

    @model_validator(mode="after")
    def validate_blueprint_links(self):
        missing_text = [label for label, value in (
            ("核心命题", self.plan.premise),
            ("最终抵达的变化", self.plan.destination),
            ("目标结局状态", self.plan.ending_state),
        ) if not str(value or "").strip()]
        if missing_text:
            raise ValueError("蓝图缺少" + "、".join(missing_text))
        missing_sections = [label for label, rows in (
            ("故事阶段", self.plan.phases),
            ("故事线", self.plan.arcs),
            ("关键转折", self.plan.milestones),
            ("临近章节路线", self.plan.chapter_roadmap),
        ) if not rows]
        if missing_sections:
            raise ValueError("蓝图缺少" + "、".join(missing_sections))
        fact_ids = [fact.id for fact in self.facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("蓝图事实 ID 重复")
        for fact in self.facts:
            if fact.status == "observed" and not fact.evidence_chapters:
                raise ValueError(f"事实 {fact.id} 缺少依据章节")
        collections = {
            "阶段": self.plan.phases,
            "故事线": self.plan.arcs,
            "里程碑": self.plan.milestones,
        }
        known = {}
        for label, rows in collections.items():
            ids = [str(row.get("id") or "").strip() for row in rows]
            if any(not item for item in ids):
                raise ValueError(f"{label}缺少稳定 ID")
            if len(ids) != len(set(ids)):
                raise ValueError(f"{label} ID 重复")
            known[label] = set(ids)
        chapters = [row.get("chapter") for row in self.plan.chapter_roadmap]
        if any(type(number) is not int or number < 1 for number in chapters):
            raise ValueError("章节路线图含有无效章号")
        if len(chapters) != len(set(chapters)):
            raise ValueError("章节路线图章号重复")
        for row in self.plan.milestones:
            if row.get("phase_id") and row.get("phase_id") not in known["阶段"]:
                raise ValueError(f"里程碑 {row.get('id')} 引用了不存在的阶段")
            missing_arcs = [item for item in row.get("arc_ids") or []
                            if item not in known["故事线"]]
            if missing_arcs:
                raise ValueError(f"里程碑 {row.get('id')} 引用了不存在的故事线")
        for row in self.plan.chapter_roadmap:
            if row.get("phase_id") and row.get("phase_id") not in known["阶段"]:
                raise ValueError(f"第 {row.get('chapter')} 章引用了不存在的阶段")
            arc_ids = [row.get("primary_arc"), *(row.get("secondary_arcs") or [])]
            if any(item and item not in known["故事线"] for item in arc_ids):
                raise ValueError(f"第 {row.get('chapter')} 章引用了不存在的故事线")
        return self


def validate_chapter_plan(data: dict) -> dict:
    return ChapterPlan.model_validate(data).model_dump(exclude_none=True)


def validate_plan_review(data: dict) -> dict:
    return PlanReview.model_validate(data).model_dump(exclude_none=True)


def validate_story_direction(data: dict) -> dict:
    return StoryDirection.model_validate(data).model_dump(exclude_none=True)


def validate_progress_review(data: dict) -> dict:
    return ProgressReview.model_validate(data).model_dump(exclude_none=True)


def validate_story_blueprint(data: dict) -> dict:
    return StoryBlueprint.model_validate(data).model_dump(exclude_none=True)
