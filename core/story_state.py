"""Durable, model-maintained narrative direction for chapter planning.

The file records story movement, not canon facts.  Canon still comes from saved
chapter bodies and validated summaries.  Keeping this separate from project.json
lets old projects adopt the feature without migrating their settings schema.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.project_manager import list_chapter_files
from core.utils import load_json, save_data_to_json


STORY_STATE_FILENAME = "story_state.json"


def story_state_path(project_dir: str) -> Path:
    return Path(project_dir) / STORY_STATE_FILENAME


def empty_story_state() -> dict[str, Any]:
    return {
        "updated_through_chapter": 0,
        "story_phase": {},
        "arcs": [],
        "recent_chapter_changes": [],
    }


def load_story_state(project_dir: str) -> dict[str, Any]:
    data = load_json(str(story_state_path(project_dir)), None)
    if not isinstance(data, dict):
        return empty_story_state()
    result = empty_story_state()
    result.update(data)
    if not isinstance(result.get("story_phase"), dict):
        result["story_phase"] = {}
    if not isinstance(result.get("arcs"), list):
        result["arcs"] = []
    if not isinstance(result.get("recent_chapter_changes"), list):
        result["recent_chapter_changes"] = []
    return result


def save_story_state(project_dir: str, state: dict[str, Any]) -> bool:
    return save_data_to_json(state, str(story_state_path(project_dir)))


def invalidate_story_state(project_dir: str, from_chapter: int) -> None:
    """Mark derived direction stale after an already-read chapter changes."""
    state = load_story_state(project_dir)
    if int(state.get("updated_through_chapter") or 0) < from_chapter:
        return
    state["updated_through_chapter"] = max(0, int(from_chapter) - 1)
    state["recent_chapter_changes"] = [
        row for row in state.get("recent_chapter_changes") or []
        if isinstance(row, dict) and int(row.get("chapter") or 0) < from_chapter
    ]
    save_story_state(project_dir, state)


def latest_saved_chapter(project_dir: str, before_chapter: int | None = None) -> int:
    numbers = [number for number, _, _ in list_chapter_files(project_dir)
               if before_chapter is None or number < before_chapter]
    return max(numbers, default=0)


def recent_summary_context(project_dir: str, before_chapter: int,
                           limit: int = 10) -> list[dict[str, Any]]:
    from core.continuation import read_chapter_summary
    numbers = [number for number, _, _ in list_chapter_files(project_dir)
               if number < before_chapter][-max(1, limit):]
    rows = []
    for number in numbers:
        summary = read_chapter_summary(project_dir, number) or ""
        if summary.strip():
            rows.append({"chapter": number, "summary": summary.strip()})
    return rows


def story_direction_context(project_dir: str, before_chapter: int) -> dict[str, Any]:
    """Return bounded material for the story-director model pass."""
    from core.story_blueprint import blueprint_for_planning
    state = load_story_state(project_dir)
    latest = latest_saved_chapter(project_dir, before_chapter)
    return {
        "stored_state": state,
        "stored_state_is_current": int(state.get("updated_through_chapter") or 0) >= latest,
        "latest_saved_chapter": latest,
        "recent_summaries": recent_summary_context(project_dir, before_chapter),
        "story_blueprint": blueprint_for_planning(project_dir),
    }
