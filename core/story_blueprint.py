"""Long-range story blueprint with evidence and proposal boundaries."""
from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.project_manager import list_chapter_files
from core.utils import load_json, read_file, save_data_to_json


BLUEPRINT_FILENAME = "story_blueprint.json"
BLUEPRINT_CANDIDATE_FILENAME = "story_blueprint_candidate.json"
BLUEPRINT_CANDIDATE_DISMISSED_FILENAME = "story_blueprint_candidate_dismissed.json"


def blueprint_path(project_dir: str) -> Path:
    return Path(project_dir) / BLUEPRINT_FILENAME


def blueprint_candidate_path(project_dir: str) -> Path:
    return Path(project_dir) / BLUEPRINT_CANDIDATE_FILENAME


def dismissed_blueprint_candidate_job(project_dir: str) -> str:
    data = load_json(str(Path(project_dir) / BLUEPRINT_CANDIDATE_DISMISSED_FILENAME), {})
    return str(data.get("job_id") or "") if isinstance(data, dict) else ""


def _merge_partial_roadmap(base_rows: list, incoming_rows: list) -> list:
    """Merge an accidental chapter subset instead of erasing the remaining route.

    Incremental model calls occasionally return only the chapters named by the
    user even though the transport field is called ``replace``.  Treat a strict
    subset of the accepted chapter numbers as a patch.  A complete set (or a
    genuinely different range) remains a normal replacement.
    """
    base = [row for row in base_rows if isinstance(row, dict)]
    incoming = [row for row in incoming_rows if isinstance(row, dict)]
    base_keys = {row.get("chapter") for row in base if type(row.get("chapter")) is int}
    incoming_keys = {row.get("chapter") for row in incoming
                     if type(row.get("chapter")) is int}
    if not base_keys or not incoming_keys or not incoming_keys < base_keys:
        return incoming_rows
    replacements = {row["chapter"]: row for row in incoming
                    if type(row.get("chapter")) is int}
    return [deepcopy(replacements.get(row.get("chapter"), row)) for row in base]


def _apply_roadmap_updates(base_rows: list, updates: list) -> list:
    """Replace matching chapters and retain every chapter not mentioned."""
    replacements = {row.get("chapter"): row for row in updates
                    if isinstance(row, dict) and type(row.get("chapter")) is int}
    result = [deepcopy(replacements.pop(row.get("chapter"), row))
              for row in base_rows if isinstance(row, dict)]
    result.extend(deepcopy(row) for _, row in sorted(replacements.items()))
    return result


def _roadmap_keys(rows: list) -> set[int]:
    return {row.get("chapter") for row in rows
            if isinstance(row, dict) and type(row.get("chapter")) is int}


def _merge_partial_candidate_roadmap(current: dict[str, Any],
                                     candidate: dict[str, Any]) -> dict[str, Any]:
    """Preserve untouched accepted chapters in an incremental candidate."""
    current_rows = (current.get("plan") or {}).get("chapter_roadmap") or []
    candidate_plan = candidate.get("plan") or {}
    incoming_rows = candidate_plan.get("chapter_roadmap") or []
    merged = _merge_partial_roadmap(current_rows, incoming_rows)
    if merged is not incoming_rows:
        phase_ids = {row.get("id") for row in candidate_plan.get("phases") or []
                     if isinstance(row, dict)}
        arc_ids = {row.get("id") for row in candidate_plan.get("arcs") or []
                   if isinstance(row, dict)}
        references_are_valid = all(
            row.get("phase_id") in phase_ids
            and row.get("primary_arc") in arc_ids
            and all(arc_id in arc_ids for arc_id in row.get("secondary_arcs") or [])
            for row in merged if isinstance(row, dict))
        if not references_are_valid:
            return candidate
        candidate = deepcopy(candidate)
        candidate.setdefault("plan", {})["chapter_roadmap"] = merged
    return candidate


def load_blueprint_candidate(project_dir: str) -> dict[str, Any] | None:
    data = load_json(str(blueprint_candidate_path(project_dir)), None)
    if not isinstance(data, dict) or not isinstance(data.get("candidate"), dict):
        return None
    try:
        from core.structured_output import validate_story_blueprint
        candidate = validate_story_blueprint(data["candidate"])
        candidate = _merge_partial_candidate_roadmap(
            load_story_blueprint(project_dir), candidate)
        data["candidate"] = validate_story_blueprint(candidate)
        return data
    except ValueError:
        return None


def save_blueprint_candidate(project_dir: str, candidate: dict[str, Any],
                             job_id: str = "") -> bool:
    """Persist a recoverable draft without changing the accepted blueprint."""
    from core.structured_output import validate_story_blueprint
    candidate = validate_story_blueprint(candidate)
    candidate = _merge_partial_candidate_roadmap(
        load_story_blueprint(project_dir), candidate)
    payload = {
        "candidate": validate_story_blueprint(candidate),
        "job_id": job_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return save_data_to_json(payload, str(blueprint_candidate_path(project_dir)))


def set_blueprint_item_lock(project_dir: str, collection: str, key: str,
                            locked: bool) -> dict[str, Any]:
    """Apply an explicit user lock decision to both accepted and pending plans."""
    allowed = {"facts", "phases", "arcs", "milestones", "chapter_roadmap"}
    if collection not in allowed:
        raise ValueError("未知的蓝图锁定区域")
    formal = load_story_blueprint(project_dir)
    pending = load_blueprint_candidate(project_dir)

    def rows_of(blueprint: dict[str, Any]) -> list:
        if collection == "facts":
            return blueprint.get("facts") or []
        return (blueprint.get("plan") or {}).get(collection) or []

    def update(blueprint: dict[str, Any]) -> bool:
        changed = False
        for row in rows_of(blueprint):
            if not isinstance(row, dict):
                continue
            row_key = str(row.get("chapter") if collection == "chapter_roadmap"
                          else row.get("id") or "")
            if row_key == str(key):
                row["locked"] = bool(locked)
                changed = True
        return changed

    formal_changed = update(formal)
    candidate = deepcopy((pending or {}).get("candidate") or {})
    candidate_changed = bool(candidate) and update(candidate)
    if not formal_changed and not candidate_changed:
        raise ValueError("没有找到要修改的蓝图节点")
    from core.structured_output import validate_story_blueprint
    formal = validate_story_blueprint(formal)
    if candidate_changed:
        candidate = validate_story_blueprint(candidate)
    if formal_changed:
        formal["updated_at"] = datetime.now(timezone.utc).isoformat()
        if not save_data_to_json(formal, str(blueprint_path(project_dir))):
            raise ValueError("正式蓝图锁定状态保存失败")
    if candidate_changed and not save_blueprint_candidate(
            project_dir, candidate, str((pending or {}).get("job_id") or "")):
        raise ValueError("候选蓝图锁定状态保存失败")
    return {"blueprint": formal, "candidate": candidate if candidate_changed else None}


def discard_blueprint_candidate(project_dir: str) -> None:
    pending = load_blueprint_candidate(project_dir)
    job_id = str((pending or {}).get("job_id") or "")
    if job_id:
        save_data_to_json({"job_id": job_id}, str(
            Path(project_dir) / BLUEPRINT_CANDIDATE_DISMISSED_FILENAME))
    blueprint_candidate_path(project_dir).unlink(missing_ok=True)


def empty_blueprint() -> dict[str, Any]:
    return {
        "version": 1,
        "facts_through_chapter": 0,
        "facts": [],
        "plan": {
            "premise": "", "destination": "", "ending_state": "",
            "phases": [], "arcs": [], "milestones": [], "chapter_roadmap": [],
        },
        "proposals": [],
        "updated_at": "",
    }


def load_story_blueprint(project_dir: str) -> dict[str, Any]:
    data = load_json(str(blueprint_path(project_dir)), None)
    if not isinstance(data, dict):
        return empty_blueprint()
    result = empty_blueprint()
    result.update(data)
    result["plan"] = {**empty_blueprint()["plan"],
                      **(data.get("plan") if isinstance(data.get("plan"), dict) else {})}
    result["facts"] = data.get("facts") if isinstance(data.get("facts"), list) else []
    result["proposals"] = data.get("proposals") if isinstance(data.get("proposals"), list) else []
    return result


def invalidate_story_blueprint(project_dir: str, from_chapter: int) -> None:
    """Mark derived facts stale when source prose is added or edited."""
    path = blueprint_path(project_dir)
    if not path.is_file():
        return
    blueprint = load_story_blueprint(project_dir)
    blueprint["facts_through_chapter"] = min(
        int(blueprint.get("facts_through_chapter") or 0), max(0, int(from_chapter) - 1))
    for fact in blueprint.get("facts") or []:
        if isinstance(fact, dict) and any(
                int(chapter or 0) >= from_chapter for chapter in fact.get("evidence_chapters") or []):
            fact["status"] = "needs_review"
    blueprint["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_data_to_json(blueprint, str(path))


def save_story_blueprint(project_dir: str, blueprint: dict[str, Any]) -> bool:
    """Persist a validated candidate without allowing locked facts to disappear."""
    from core.structured_output import validate_story_blueprint
    current = load_story_blueprint(project_dir)
    candidate = validate_story_blueprint(blueprint)
    current_chapters = _roadmap_keys(
        (current.get("plan") or {}).get("chapter_roadmap") or [])
    submitted_chapters = _roadmap_keys(
        (candidate.get("plan") or {}).get("chapter_roadmap") or [])
    candidate = _merge_partial_candidate_roadmap(current, candidate)
    candidate = validate_story_blueprint(candidate)
    if submitted_chapters and submitted_chapters < current_chapters and _roadmap_keys(
            (candidate.get("plan") or {}).get("chapter_roadmap") or []) != current_chapters:
        raise ValueError("候选章节路线不完整且无法安全合并；请重新执行局部调整，正式蓝图未改变")
    locked = {str(row.get("id")): row for row in current.get("facts") or []
              if isinstance(row, dict) and row.get("locked") and row.get("id")}
    incoming = {str(row.get("id")): row for row in candidate.get("facts") or []
                if isinstance(row, dict) and row.get("id")}
    changed = [key for key, value in locked.items() if incoming.get(key) != value]
    current_plan = current.get("plan") or {}
    incoming_plan = candidate.get("plan") or {}
    for collection in ("phases", "arcs", "milestones", "chapter_roadmap"):
        old_locked = {str(row.get("id") or row.get("chapter")): row
                      for row in current_plan.get(collection) or []
                      if isinstance(row, dict) and row.get("locked")}
        new_rows = {str(row.get("id") or row.get("chapter")): row
                    for row in incoming_plan.get(collection) or [] if isinstance(row, dict)}
        changed.extend(f"{collection}:{key}" for key, value in old_locked.items()
                       if new_rows.get(key) != value)
    if changed:
        raise ValueError("锁定的蓝图内容不能被删除或修改：" + "、".join(changed[:8]))
    candidate["version"] = 1
    candidate["updated_at"] = datetime.now(timezone.utc).isoformat()
    saved = save_data_to_json(candidate, str(blueprint_path(project_dir)))
    if saved:
        discard_blueprint_candidate(project_dir)
    return saved


def _summary_rows(project_dir: str, char_budget: int = 32000) -> list[dict[str, Any]]:
    from core.continuation import read_chapter_summary
    rows = []
    for number, _, _ in list_chapter_files(project_dir):
        summary = (read_chapter_summary(project_dir, number) or "").strip()
        if summary:
            rows.append({"chapter": number, "summary": summary[:320]})
    if len(json.dumps(rows, ensure_ascii=False)) <= char_budget:
        return rows
    # Preserve the opening, the recent run, and evenly spaced middle evidence.
    chosen = rows[:10] + rows[-30:]
    middle = rows[10:-30]
    if middle:
        step = max(1, len(middle) // 40)
        chosen += middle[::step][:40]
    return sorted({row["chapter"]: row for row in chosen}.values(), key=lambda row: row["chapter"])


def _chapter_excerpt_rows(project_dir: str) -> list[dict[str, Any]]:
    """Bounded prose evidence for projects whose historical summaries are sparse."""
    files = list_chapter_files(project_dir)
    if not files:
        return []
    selected = files[:3] + files[-12:]
    middle = files[3:-12]
    if middle:
        step = max(1, len(middle) // 5)
        selected += middle[::step][:5]
    rows = []
    for number, _, path in sorted({row[0]: row for row in selected}.values()):
        text = read_file(path).strip()
        if text:
            rows.append({"chapter": number,
                         "excerpt": text[:420] + ("\n…\n" + text[-620:] if len(text) > 1040 else "")})
    return rows


def blueprint_source_context(project_dir: str, settings: dict) -> dict[str, Any]:
    from core.story_state import load_story_state
    numbers = [number for number, _, _ in list_chapter_files(project_dir)]
    pending = load_blueprint_candidate(project_dir)
    current = ((pending or {}).get("candidate") or load_story_blueprint(project_dir))
    current["proposals"] = list(current.get("proposals") or [])[-10:]
    all_facts = list(current.get("facts") or [])
    locked_facts = [row for row in all_facts if isinstance(row, dict) and row.get("locked")]
    recent_facts = [row for row in all_facts if row not in locked_facts][-60:]
    current["facts"] = locked_facts + recent_facts
    summaries = _summary_rows(project_dir)
    excerpts = _chapter_excerpt_rows(project_dir)
    if (current.get("plan") or {}).get("phases"):
        # An adjustment already has a validated factual/structural baseline.
        # Keep only recent evidence instead of resending the whole book sample.
        summaries = summaries[-24:]
        excerpts = excerpts[-4:]
    return {
        "background": settings.get("background", ""),
        "book_rules": settings.get("extra_requirements", ""),
        "latest_saved_chapter": max(numbers, default=0),
        "chapter_summaries": summaries,
        "representative_prose_excerpts": excerpts,
        "current_story_state": load_story_state(project_dir),
        "current_blueprint": current,
    }


def generate_blueprint_candidate(project_dir: str, settings: dict, llm,
                                 requirements: str = "", audit=None, progress=None,
                                 mode: str = "auto") -> dict[str, Any]:
    """Build a new blueprint in bounded stages, or patch an existing one."""
    context = blueprint_source_context(project_dir, settings)
    if mode == "regenerate":
        # A retained candidate is only a preview, not a new source of truth.
        # Rebuilding must start from the confirmed blueprint so model-authored
        # lock flags in an earlier preview cannot constrain the next design.
        confirmed = load_story_blueprint(project_dir)
        if (confirmed.get("plan") or {}).get("phases"):
            context["current_blueprint"] = confirmed
    current = context["current_blueprint"]
    has_plan = bool((current.get("plan") or {}).get("phases"))
    if mode not in {"auto", "create", "adjust", "regenerate"}:
        raise ValueError("未知的蓝图生成模式")
    if mode == "create" and has_plan:
        raise ValueError("当前已有故事蓝图；请选择调整现有蓝图或重新设计一版")
    if mode == "adjust" and not has_plan:
        raise ValueError("当前没有可调整的故事蓝图；请先建立第一版")
    from core.agent_skill import skill_guidance
    guidance = skill_guidance("story-blueprint", audit=audit, stage="story_blueprint")
    if mode == "regenerate":
        # A genuinely alternative design needs the wider book evidence. The old
        # plan is not a baseline, but locked rows remain explicit constraints.
        context["chapter_summaries"] = _summary_rows(project_dir)
        context["representative_prose_excerpts"] = _chapter_excerpt_rows(project_dir)
        return _create_blueprint(context, requirements, guidance, llm, audit, progress,
                                 preserve_locked=True)
    if mode == "adjust" or (mode == "auto" and has_plan):
        return _adjust_blueprint(context, requirements, guidance, llm, audit, progress)
    return _create_blueprint(context, requirements, guidance, llm, audit, progress)


def generate_blueprint_directions(project_dir: str, settings: dict, llm,
                                  requirements: str = "", audit=None,
                                  progress=None) -> list[dict[str, Any]]:
    """Propose mutually exclusive story engines before expanding a blueprint."""
    context = blueprint_source_context(project_dir, settings)
    current = context.get("current_blueprint") or {}
    old_plan = current.get("plan") or {}
    brief = {
        "latest_saved_chapter": context.get("latest_saved_chapter"),
        "confirmed_facts": current.get("facts") or [],
        "recent_chapter_summaries": (context.get("chapter_summaries") or [])[-12:],
        "book_rules": str(context.get("book_rules") or "")[:2400],
        "background": str(context.get("background") or "")[:1800],
    }
    old_function = [{"name": row.get("name"), "purpose": row.get("purpose"),
                     "exit_state": row.get("exit_state")}
                    for row in old_plan.get("phases") or [] if isinstance(row, dict)]
    if progress:
        progress("正在提出三种互斥的长期架构方向")
    result = _model_json(llm, f"""不要直接写完整蓝图。根据高层事实提出恰好 3 种彼此互斥的长期故事架构，供用户选择。
每种 direction 包含 id,title,story_engine,current_case_resolution,dominant_conflict,phase_logic,future_arc,reader_promise,why_different。
三种方案必须使用不同的故事发动机、不同的主要对抗来源和不同的阶段边界，不能只是给同一结构换名字。至少一种方案须在 2—3 章内结束当前档案搜证并转入新的外部事件；至少一种以对手主动行动为发动机；至少一种让家庭、学校或社会支线脱离档案调查独立发展。不得把日期、文书、名单、复核和继续搜证作为未来长期架构。
高层事实：{json.dumps(brief, ensure_ascii=False)}
旧阶段功能（只用于避免同构，不得仿写）：{json.dumps(old_function, ensure_ascii=False)}
用户补充方向：{requirements or '希望获得真正不同的长期走向'}
只输出 JSON：{{"directions":[...]}}""",
                         "架构方向", audit, progress, 4200,
                         required_lists=("directions",),
                         validator=_validate_blueprint_directions)
    return result["directions"]


def _validate_blueprint_directions(value: dict[str, Any]) -> None:
    rows = value.get("directions") or []
    if len(rows) != 3:
        raise ValueError("必须恰好提供三种架构方向")
    required = ("id", "title", "story_engine", "current_case_resolution",
                "dominant_conflict", "phase_logic", "future_arc", "reader_promise")
    for index, row in enumerate(rows, 1):
        missing = [key for key in required if not str((row or {}).get(key) or "").strip()]
        if missing:
            raise ValueError(f"第 {index} 种架构缺少字段：" + "、".join(missing))
    engines = {str(row.get("story_engine") or "").strip() for row in rows}
    conflicts = {str(row.get("dominant_conflict") or "").strip() for row in rows}
    if len(engines) != 3 or len(conflicts) != 3:
        raise ValueError("三种架构的故事发动机和主要冲突必须彼此不同")


def _model_json(llm, prompt: str, stage: str, audit=None, progress=None,
                budget: int = 5000, required_text: tuple[str, ...] = (),
                required_lists: tuple[str, ...] = (),
                require_proposal: bool = False, validator=None) -> dict[str, Any]:
    """Call one bounded stage; retry only that stage on truncation or connection loss."""
    last_error = "模型未返回 JSON 对象"
    for attempt in range(2):
        request = prompt if attempt == 0 else (
            f"{prompt}\n\n上次输出失败：{last_error}。这是当前阶段的精简重试。"
            "不要复述整体任务，不要输出其他阶段字段，不要输出思考过程或解释，只输出当前阶段 JSON。")
        if audit:
            audit.write("blueprint_stage_request", stage=stage, attempt=attempt + 1,
                        prompt=request)
        try:
            raw = llm.complete(request, system=(
                f"你只负责故事蓝图的“{stage}”阶段。整体用户要求只决定内容，不能改变本阶段输出结构。"
                "禁止提前输出其他阶段，禁止输出思考过程，只输出一个完整、精简的 JSON 对象。"),
                               temperature=0.3 if attempt == 0 else 0.1,
                               num_predict=budget,
                               disable_thinking=True)
        except Exception as exc:
            message = str(exc or "")
            transient = any(marker in message.lower() for marker in
                            ("connection error", "timeout", "timed out", "connection reset"))
            if attempt == 0 and transient:
                last_error = "模型连接中断"
                if progress:
                    progress(f"{stage}连接中断，正在重试当前阶段")
                continue
            raise
        if audit:
            audit.write("blueprint_stage_response", stage=stage, attempt=attempt + 1,
                        raw_response=raw)
        try:
            value = _normalize_stage_object(_complete_json_object(raw))
            missing_text = [key for key in required_text
                            if not str(value.get(key) or "").strip()]
            missing_lists = [key for key in required_lists
                             if not isinstance(value.get(key), list) or not value.get(key)]
            if missing_text or missing_lists:
                raise ValueError("缺少字段：" + "、".join(missing_text + missing_lists))
            if require_proposal and not isinstance(value.get("proposal"), dict):
                raise ValueError("缺少 proposal 对象")
            if validator:
                validator(value)
            return value
        except ValueError as exc:
            last_error = str(exc)
    raise ValueError(f"{stage}失败：{last_error}")


def _normalize_stage_object(value: dict[str, Any]) -> dict[str, Any]:
    """Accept a stage object or the same fields wrapped in a full blueprint."""
    result = dict(value)
    known = {"premise", "destination", "ending_state", "phases", "arcs", "milestones",
             "chapter_roadmap", "facts", "proposal", "proposals", "plan_fields", "replace",
             "chapter_updates"}
    # OpenAI-compatible models occasionally add one descriptive root key even
    # when asked for a bare object. Unwrap only an unambiguous single wrapper.
    for _ in range(2):
        if known.intersection(result):
            break
        nested = [item for item in result.values() if isinstance(item, dict)]
        if len(result) == 1 and len(nested) == 1:
            result = dict(nested[0])
        else:
            break
    plan = result.get("plan")
    if isinstance(plan, dict):
        # Some models honor the global blueprint schema even when a stage asks
        # for only part of it. Promote plan fields to the stage root.
        result.update(plan)
    if not isinstance(result.get("proposal"), dict):
        proposals = result.get("proposals")
        if isinstance(proposals, list) and proposals and isinstance(proposals[-1], dict):
            result["proposal"] = proposals[-1]
    return result


def _validate_blueprint_candidate(value: dict[str, Any]) -> dict[str, Any]:
    from core.structured_output import validate_story_blueprint
    try:
        return validate_story_blueprint(value)
    except ValueError as exc:
        message = str(exc)
        match = re.search(r"Value error, (.+?) \[type=", message, flags=re.DOTALL)
        raise ValueError((match.group(1).strip() if match else message.splitlines()[0])
                         or "蓝图结构校验失败") from None


def _reference_map(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Map stable IDs and unique display names to IDs without fuzzy guessing."""
    result: dict[str, str] = {}
    names: dict[str, list[str]] = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        ident = str(row["id"])
        result[ident] = ident
        name = str(row.get("name") or "").strip()
        if name:
            names.setdefault(name, []).append(ident)
    result.update({name: ids[0] for name, ids in names.items() if len(ids) == 1})
    return result


def _validate_structure_stage(value: dict[str, Any], phases: list[dict[str, Any]]) -> None:
    phase_refs = _reference_map(phases)
    arc_refs = _reference_map(value.get("arcs") or [])
    for row in value.get("milestones") or []:
        phase = str(row.get("phase_id") or "")
        if phase in phase_refs:
            row["phase_id"] = phase_refs[phase]
        elif phase:
            raise ValueError(f"里程碑 {row.get('id') or row.get('name')} 引用了不存在的阶段 {phase}")
        normalized = []
        for arc in row.get("arc_ids") or []:
            key = str(arc)
            if key not in arc_refs:
                raise ValueError(f"里程碑 {row.get('id') or row.get('name')} 引用了不存在的故事线 {key}")
            normalized.append(arc_refs[key])
        row["arc_ids"] = normalized


def _validate_roadmap_stage(value: dict[str, Any], phases: list[dict[str, Any]],
                            arcs: list[dict[str, Any]], expected_start: int | None = None,
                            locked_chapters: set[int] | None = None) -> None:
    phase_refs, arc_refs = _reference_map(phases), _reference_map(arcs)
    chapters = set()
    rows = value.get("chapter_roadmap") or []
    if not 6 <= len(rows) <= 10:
        raise ValueError("近期章节路线必须包含 6—10 章")
    for row in value.get("chapter_roadmap") or []:
        chapter = row.get("chapter")
        if type(chapter) is not int or chapter < 1 or chapter in chapters:
            raise ValueError(f"章节路线含无效或重复章号：{chapter}")
        chapters.add(chapter)
        phase = str(row.get("phase_id") or "")
        if phase not in phase_refs:
            raise ValueError(f"第 {chapter} 章引用了不存在的阶段 {phase}")
        row["phase_id"] = phase_refs[phase]
        primary = str(row.get("primary_arc") or "")
        if primary not in arc_refs:
            raise ValueError(f"第 {chapter} 章引用了不存在的故事线 {primary}")
        row["primary_arc"] = arc_refs[primary]
        normalized = []
        for arc in row.get("secondary_arcs") or []:
            key = str(arc)
            if key not in arc_refs:
                raise ValueError(f"第 {chapter} 章引用了不存在的故事线 {key}")
            normalized.append(arc_refs[key])
        row["secondary_arcs"] = normalized
    ordered = sorted(chapters)
    if expected_start is not None and ordered and ordered[0] != expected_start:
        raise ValueError(f"近期章节路线必须从第 {expected_start} 章开始")
    if ordered and ordered != list(range(ordered[0], ordered[0] + len(ordered))):
        raise ValueError("近期章节路线章号必须连续")
    _validate_roadmap_movement(rows, locked_chapters or set())


def _validate_roadmap_movement(rows: list[dict[str, Any]], locked_chapters: set[int]) -> None:
    """Require visible story movement instead of a run of procedural checks."""
    allowed = {"opposition", "consequence", "subplot", "relationship",
               "discovery", "decision", "institutional"}
    modes: list[str] = []
    primary_arcs: set[str] = set()
    for row in rows:
        chapter = row.get("chapter")
        if chapter in locked_chapters:
            continue
        mode = str(row.get("event_mode") or "").strip().lower()
        if mode not in allowed:
            raise ValueError(f"第 {chapter} 章缺少有效 event_mode")
        if not str(row.get("visible_event") or "").strip():
            raise ValueError(f"第 {chapter} 章缺少读者可见的外部事件")
        if not str(row.get("irreversible_change") or "").strip():
            raise ValueError(f"第 {chapter} 章缺少不可逆变化")
        modes.append(mode)
        if row.get("primary_arc"):
            primary_arcs.add(str(row["primary_arc"]))
    missing = {"opposition", "consequence", "subplot"} - set(modes)
    if missing:
        raise ValueError("近期路线缺少必要叙事功能：" + "、".join(sorted(missing)))
    if len(primary_arcs) < 3:
        raise ValueError("近期路线至少需要实质推进三条不同故事线")
    procedural = {"discovery", "institutional"}
    run = 0
    for mode in modes:
        run = run + 1 if mode in procedural else 0
        if run > 2:
            raise ValueError("查证或制度整理类章节不能连续超过两章")


def _validate_regenerate_names(value: dict[str, Any], key: str,
                               previous_names: list[str], allowed_overlap: int = 1) -> None:
    """Reject a supposed redesign that merely reproduces the old section names."""
    old = {str(name).strip() for name in previous_names if str(name).strip()}
    new = {str(row.get("name") or "").strip() for row in value.get(key) or []
           if isinstance(row, dict) and str(row.get("name") or "").strip()}
    overlap = sorted(old & new)
    if len(overlap) > allowed_overlap:
        raise ValueError("重新设计仍复用了过多旧名称：" + "、".join(overlap))


def _create_blueprint(context: dict[str, Any], requirements: str, guidance: str, llm,
                      audit=None, progress=None, preserve_locked: bool = False) -> dict[str, Any]:
    evidence = {key: context[key] for key in (
        "background", "book_rules", "latest_saved_chapter", "chapter_summaries",
        "representative_prose_excerpts", "current_story_state")}
    goal = requirements or "从现有故事自然提出后续方向"
    locked = _locked_blueprint_constraints(context["current_blueprint"]) if preserve_locked else {}
    old_plan = (context.get("current_blueprint") or {}).get("plan") or {}
    skeleton_evidence = evidence
    if preserve_locked:
        # Architecture needs high-level pressures, not another dump of every
        # document/date detail. Granular evidence stays in later roadmap stages.
        skeleton_evidence = {
            "background": str(context.get("background") or "")[:1800],
            "book_rules": str(context.get("book_rules") or "")[:2400],
            "latest_saved_chapter": context.get("latest_saved_chapter"),
            "confirmed_facts": (context.get("current_blueprint") or {}).get("facts") or [],
            "recent_chapter_summaries": (context.get("chapter_summaries") or [])[-12:],
            "current_story_state": context.get("current_story_state") or {},
        }
    avoid_design = {
        "phase_names": [row.get("name") for row in old_plan.get("phases") or [] if row.get("name")],
        "arc_names": [row.get("name") for row in old_plan.get("arcs") or [] if row.get("name")],
        "milestone_names": [row.get("name") for row in old_plan.get("milestones") or [] if row.get("name")],
        "roadmap_roles": [row.get("role") for row in old_plan.get("chapter_roadmap") or [] if row.get("role")],
    } if preserve_locked else {}
    if preserve_locked:
        goal = ("这是重新设计任务：旧蓝图中未锁定的阶段、故事线、里程碑和章节路线"
                "不作为沿用基准。依据正文事实重新判断结构；只有下列锁定约束必须保留。\n" + goal)
    if progress:
        progress("正在生成蓝图总体骨架（1/4）")
    skeleton = _model_json(llm, f"""{guidance}
根据资料和用户目标设计长篇小说的总体骨架。只输出 premise、destination、ending_state、phases。
phases 为 3—4 个全书阶段，每项包含 id,name,purpose,entry_state,exit_state,milestone_ids,status,locked；文字精简，不能把日期或单个线索当阶段。
高层资料：{json.dumps(skeleton_evidence, ensure_ascii=False)}
必须保留的锁定约束：{json.dumps(locked, ensure_ascii=False)}
用户目标：{goal}
上一版设计特征（重新设计时应主动避开，锁定行除外）：{json.dumps(avoid_design, ensure_ascii=False)}
【本轮输出边界】整体目标中即使要求完整蓝图，本轮也只完成总体骨架。根对象只能有 premise、destination、ending_state、phases 四个键；严禁输出 arcs、milestones、chapter_roadmap、facts 或 proposals。锁定约束中的 required_phase_ids 必须继续作为 phases 的 ID 出现，因为锁定里程碑仍引用它们；可以重新设计这些阶段的文字内容。
输出 JSON：{{"premise":"","destination":"","ending_state":"","phases":[...]}}""",
                           "总体骨架", audit, progress, 4200,
                           required_text=("premise", "destination", "ending_state"),
                           required_lists=("phases",),
                           validator=(lambda value: _validate_regenerate_names(
                               value, "phases", avoid_design.get("phase_names") or []))
                           if preserve_locked else None)
    if preserve_locked:
        skeleton["phases"] = _restore_locked_rows(
            (context["current_blueprint"].get("plan") or {}).get("phases") or [],
            skeleton.get("phases") or [])
    if progress:
        progress("正在设计故事线与关键里程碑（2/4）")
    structure = _model_json(llm, f"""根据总体骨架设计 2—4 条故事线和 3—6 个里程碑。
每条 arc 包含 id,name,kind,current_state,target_state,next_pressure,intersections,closure_condition,status,locked；每条 milestone 包含 id,name,purpose,arc_ids,phase_id,target_range,preconditions,payoff,status,locked。引用的 phase_id 必须来自骨架。
骨架：{json.dumps(skeleton, ensure_ascii=False)}
必须保留的锁定约束：{json.dumps(locked, ensure_ascii=False)}
高层资料：{json.dumps(skeleton_evidence, ensure_ascii=False)}
用户目标：{goal}
上一版设计特征（重新设计时不要复用未锁定名称与组织方式）：{json.dumps(avoid_design, ensure_ascii=False)}
【本轮输出边界】只输出 arcs 与 milestones 两个键；严禁重复总体骨架，严禁输出章节路线、facts 或 proposals。锁定约束中的 required_arc_ids 必须继续作为 arcs 的 ID 出现；锁定 milestones 必须逐字段原样返回，不得改 phase_id、arc_ids 或其他字段。
输出 JSON：{{"arcs":[...],"milestones":[...]}}""",
                            "故事线与里程碑", audit, progress, 5200,
                            required_lists=("arcs", "milestones"),
                            validator=lambda value: (
                                _validate_structure_stage(value, skeleton.get("phases") or []),
                                _validate_regenerate_names(
                                    value, "arcs", avoid_design.get("arc_names") or [])
                                if preserve_locked else None))
    if preserve_locked:
        old_plan = context["current_blueprint"].get("plan") or {}
        structure["arcs"] = _restore_locked_rows(old_plan.get("arcs") or [],
                                                   structure.get("arcs") or [])
        structure["milestones"] = _restore_locked_rows(old_plan.get("milestones") or [],
                                                         structure.get("milestones") or [])
    milestone_ids_by_phase: dict[str, list[str]] = {}
    for row in structure.get("milestones") or []:
        if isinstance(row, dict) and row.get("phase_id") and row.get("id"):
            milestone_ids_by_phase.setdefault(str(row["phase_id"]), []).append(str(row["id"]))
    for phase in skeleton.get("phases") or []:
        if isinstance(phase, dict) and phase.get("id"):
            phase["milestone_ids"] = milestone_ids_by_phase.get(str(phase["id"]), [])
    # The roadmap used to share one response with facts.  Reasoning-heavy models
    # could produce a valid 15k+ character object and still be cut off halfway
    # through the facts list.  Keep the creative route and evidence extraction
    # as independently bounded stages instead.
    recent_summaries = (context.get("chapter_summaries") or [])[-8:]
    roadmap_design = {
        "premise": skeleton.get("premise"),
        "destination": skeleton.get("destination"),
        "phases": skeleton.get("phases") or [],
        "arcs": structure.get("arcs") or [],
        "milestones": structure.get("milestones") or [],
    }
    if progress:
        progress("正在规划近期章节路线（3/4）")
    next_chapter = int(context["latest_saved_chapter"]) + 1
    locked_roadmap_chapters = {int(row["chapter"]) for row in locked.get("chapter_roadmap", [])
                               if isinstance(row, dict) and type(row.get("chapter")) is int}
    near_term = _model_json(llm, f"""根据已经确定的总体设计，规划从第 {next_chapter} 章开始的临近 6—10 章，并给出一条候选蓝图说明。
chapter_roadmap 每项包含 chapter,phase_id,role,primary_arc,secondary_arcs,event_mode,visible_event,required_change,irreversible_change,ending_state,must_not_repeat,status,locked。章节必须连续；phase_id 和故事线 ID 必须引用给定设计。proposal 包含 id,status=pending,reason,changes。
event_mode 只能从 opposition（对手主动行动）、consequence（现实损失或后果）、subplot（主线之外的情节实质推进）、relationship、discovery、decision、institutional 中选择。整段路线必须至少各有一章 opposition、consequence、subplot；至少三条故事线轮流成为主线；discovery 与 institutional 合计不得连续超过两章。visible_event 必须是读者能直接看到的场景事件，irreversible_change 必须说明本章结束后什么再也回不到原状。
不要把“继续核验、再查一次、整理清单、建立记录、缩小范围”本身当作章节核心。查证只能服务于冲突、选择、损失或行动；新增故事线必须在近期章节中真正发生事件。
总体设计：{json.dumps(roadmap_design, ensure_ascii=False)}
最近章节摘要：{json.dumps(recent_summaries, ensure_ascii=False)}
当前故事状态：{json.dumps(context.get('current_story_state') or {}, ensure_ascii=False)}
必须保留的锁定章节路线：{json.dumps(locked.get('chapter_roadmap', []), ensure_ascii=False)}
上一版章节职责（重新设计时不要逐章同义改写）：{json.dumps(avoid_design.get('roadmap_roles', []), ensure_ascii=False)}
【本轮输出边界】只输出 chapter_roadmap 和 proposal 两个键；不要复述整体目标，不要输出 facts、总体骨架、arcs 或 milestones。
输出 JSON：{{"chapter_roadmap":[...],"proposal":{{...}}}}""",
                            "近期章节路线", audit, progress, 5200,
                            required_lists=("chapter_roadmap",), require_proposal=True,
                            validator=lambda value: _validate_roadmap_stage(
                                value, skeleton.get("phases") or [], structure.get("arcs") or [],
                                expected_start=next_chapter,
                                locked_chapters=locked_roadmap_chapters))

    current_blueprint = context.get("current_blueprint") or {}
    try:
        facts_are_current = (bool(current_blueprint.get("facts")) and
                             int(current_blueprint.get("facts_through_chapter") or 0)
                             >= int(context["latest_saved_chapter"]))
    except (TypeError, ValueError):
        facts_are_current = False
    if facts_are_current:
        if progress:
            progress("正在复用已确认的正文事实（4/4）")
        near_term["facts"] = deepcopy(current_blueprint.get("facts") or [])
    else:
        if progress:
            progress("正在独立整理正文事实（4/4）")
        fact_evidence = {
            "latest_saved_chapter": context["latest_saved_chapter"],
            "chapter_summaries": context.get("chapter_summaries") or [],
            "current_story_state": context.get("current_story_state") or {},
            "existing_facts": current_blueprint.get("facts") or [],
        }
        fact_stage = _model_json(llm, f"""只从已经发生的正文证据中整理 6—10 条高层事实。
facts 每项包含 id,statement,evidence_chapters,status,locked；未来计划不得写入 facts，证据明确用 confirmed，证据不足用 needs_review。已有事实仍被证据支持时保留原 ID。
证据：{json.dumps(fact_evidence, ensure_ascii=False)}
必须保留的锁定事实：{json.dumps(locked.get('facts', []), ensure_ascii=False)}
【本轮输出边界】只输出 facts 一个键；不要输出章节路线、proposal 或蓝图设计。
输出 JSON：{{"facts":[...]}}""",
                                 "正文事实", audit, progress, 3600,
                                 required_lists=("facts",))
        near_term["facts"] = fact_stage.get("facts") or []
    if preserve_locked:
        near_term["facts"] = _restore_locked_rows(context["current_blueprint"].get("facts") or [],
                                                   near_term.get("facts") or [])
        near_term["chapter_roadmap"] = _restore_locked_rows(
            (context["current_blueprint"].get("plan") or {}).get("chapter_roadmap") or [],
            near_term.get("chapter_roadmap") or [])
    candidate = {
        "version": 1, "facts_through_chapter": context["latest_saved_chapter"],
        "plan": {"premise": skeleton.get("premise"),
                 "destination": skeleton.get("destination"),
                 "ending_state": skeleton.get("ending_state"),
                 "phases": skeleton.get("phases"), "arcs": structure.get("arcs"),
                 "milestones": structure.get("milestones"),
                 "chapter_roadmap": near_term.get("chapter_roadmap")},
        "facts": near_term.get("facts"), "proposals": [near_term.get("proposal")],
    }
    if preserve_locked:
        _normalize_candidate_locks(context["current_blueprint"], candidate)
    candidate = _validate_blueprint_candidate(candidate)
    if preserve_locked:
        _ensure_blueprint_locks(context["current_blueprint"], candidate)
    return candidate


def _locked_blueprint_constraints(blueprint: dict[str, Any]) -> dict[str, Any]:
    plan = blueprint.get("plan") or {}
    result: dict[str, Any] = {"facts": [row for row in blueprint.get("facts") or []
                                              if isinstance(row, dict) and row.get("locked")]}
    for key in ("phases", "arcs", "milestones", "chapter_roadmap"):
        result[key] = [row for row in plan.get(key) or []
                       if isinstance(row, dict) and row.get("locked")]
    locked_milestones = result.get("milestones") or []
    result["required_phase_ids"] = sorted({str(row.get("phase_id")) for row in locked_milestones
                                            if row.get("phase_id")})
    result["required_arc_ids"] = sorted({str(arc_id) for row in locked_milestones
                                          for arc_id in row.get("arc_ids") or [] if arc_id})
    return result


def _restore_locked_rows(old_rows: list, new_rows: list) -> list:
    """Make model output honor exact locked rows while preserving its new order."""
    locked = {str(row.get("id") or row.get("chapter")): row for row in old_rows
              if isinstance(row, dict) and row.get("locked")}
    if not locked:
        return new_rows
    restored, seen = [], set()
    for row in new_rows:
        key = str(row.get("id") or row.get("chapter")) if isinstance(row, dict) else ""
        restored.append(deepcopy(locked.get(key, row)))
        if key in locked:
            seen.add(key)
    restored.extend(deepcopy(row) for key, row in locked.items() if key not in seen)
    return restored


def _ensure_blueprint_locks(current: dict[str, Any], candidate: dict[str, Any]) -> None:
    _ensure_locked_rows(current.get("facts") or [], candidate.get("facts") or [], "facts")
    current_plan, candidate_plan = current.get("plan") or {}, candidate.get("plan") or {}
    for key in ("phases", "arcs", "milestones", "chapter_roadmap"):
        _ensure_locked_rows(current_plan.get(key) or [], candidate_plan.get(key) or [], key)


def _normalize_candidate_locks(current: dict[str, Any], candidate: dict[str, Any]) -> None:
    """Only confirmed locks are authoritative; previews cannot create locks."""
    collections = [(current.get("facts") or [], candidate.get("facts") or [])]
    current_plan, candidate_plan = current.get("plan") or {}, candidate.get("plan") or {}
    collections.extend((current_plan.get(key) or [], candidate_plan.get(key) or [])
                       for key in ("phases", "arcs", "milestones", "chapter_roadmap"))
    for old_rows, new_rows in collections:
        locked_ids = {str(row.get("id") or row.get("chapter")) for row in old_rows
                      if isinstance(row, dict) and row.get("locked")}
        for row in new_rows:
            if isinstance(row, dict):
                row["locked"] = str(row.get("id") or row.get("chapter")) in locked_ids


def _adjust_blueprint(context: dict[str, Any], requirements: str, guidance: str, llm,
                      audit=None, progress=None) -> dict[str, Any]:
    current = context["current_blueprint"]
    evidence = {"latest_saved_chapter": context["latest_saved_chapter"],
                "chapter_summaries": context["chapter_summaries"],
                "current_story_state": context["current_story_state"]}
    if progress:
        progress("正在生成蓝图增量调整")
    fast_closure = "[节奏模式：快速收束]" in (requirements or "")
    fast_instruction = ""
    if fast_closure:
        fast_instruction = """
【快速收束硬约束】replace 必须同时包含完整 phases、milestones、chapter_roadmap，三者必须使用一致的收束时间；不得只改近期章节而保留旧里程碑。locked=true 的行必须逐字段原样保留，其余阶段退出条件、里程碑 target_range 与 payoff 要同步前移。chapter_roadmap 每项除原字段外增加 investigation_state，只能为 collect、commit、confront、resolve、aftermath、next_arc。collect 最多两章；前三章必须进入 confront；第六章前必须出现 resolve，使当前案件形成明确责任结论或完成正面对抗；resolve 后至少安排一章 aftermath 或 next_arc。不得新增日期、文书、名单或旁证来延长搜集阶段。"""
    patch = _model_json(llm, f"""{guidance}
根据用户要求调整已有蓝图。只返回需要改变的部分；未提及的内容由程序原样保留，禁止整份重写。
局部修改章节时必须输出 chapter_updates，只列出发生改变的章节；程序会按 chapter 合并。replace 仅用于确实要完整替换 phases、arcs、milestones 或整段 chapter_roadmap 的情况。plan_fields 仅可含 premise、destination、ending_state；facts 仅在事实需要更新时输出完整替代集合。不得修改或删除 locked=true 的内容。
proposal 必须包含 id,status=pending,reason,changes，并准确概括这次变更。
{fast_instruction}
当前蓝图：{json.dumps(current, ensure_ascii=False)}
近期证据：{json.dumps(evidence, ensure_ascii=False)}
用户要求：{requirements or '依据当前故事状态优化后续规划'}
输出 JSON：{{"plan_fields":{{...可选}},"chapter_updates":[...可选],"replace":{{...可选}},"facts":[...可选],"proposal":{{...}}}}""",
                        "增量调整", audit, progress, 6000, require_proposal=True,
                        validator=(lambda value: _validate_fast_closure_patch(
                            value, current, int(context["latest_saved_chapter"]) + 1))
                        if fast_closure else None)
    if progress:
        progress("正在合并并校验蓝图调整")
    candidate = deepcopy(current)
    changed = False
    for key, value in (patch.get("plan_fields") or {}).items():
        if key in ("premise", "destination", "ending_state"):
            candidate.setdefault("plan", {})[key] = value
            changed = True
    chapter_updates = patch.get("chapter_updates")
    if isinstance(chapter_updates, list) and chapter_updates:
        old_rows = candidate.get("plan", {}).get("chapter_roadmap") or []
        updated_rows = _apply_roadmap_updates(old_rows, chapter_updates)
        _ensure_locked_rows(old_rows, updated_rows, "chapter_roadmap")
        candidate.setdefault("plan", {})["chapter_roadmap"] = updated_rows
        changed = True
    for key, value in (patch.get("replace") or {}).items():
        if key in ("phases", "arcs", "milestones", "chapter_roadmap") and isinstance(value, list):
            _ensure_locked_rows(candidate.get("plan", {}).get(key) or [], value, key)
            if key == "chapter_roadmap" and not fast_closure:
                value = _merge_partial_roadmap(
                    candidate.get("plan", {}).get("chapter_roadmap") or [], value)
            candidate.setdefault("plan", {})[key] = value
            changed = True
    if isinstance(patch.get("facts"), list):
        _ensure_locked_rows(candidate.get("facts") or [], patch["facts"], "facts")
        candidate["facts"] = patch["facts"]
        changed = True
    proposal = patch.get("proposal")
    if not changed:
        raise ValueError("模型没有返回可应用的蓝图变更")
    if not isinstance(proposal, dict):
        raise ValueError("蓝图调整缺少 proposal")
    proposal.setdefault("status", "pending")
    candidate["proposals"] = (list(candidate.get("proposals") or []) + [proposal])[-10:]
    candidate["facts_through_chapter"] = context["latest_saved_chapter"]
    return _validate_blueprint_candidate(candidate)


def _validate_fast_closure_patch(value: dict[str, Any], current: dict[str, Any],
                                 expected_start: int) -> None:
    replacement = value.get("replace") or {}
    roadmap = replacement.get("chapter_roadmap")
    if not isinstance(roadmap, list) or not roadmap:
        raise ValueError("快速收束必须返回完整的近期章节路线")
    phases = replacement.get("phases")
    milestones = replacement.get("milestones")
    if not isinstance(phases, list) or not phases:
        raise ValueError("快速收束必须同步返回完整故事阶段")
    if not isinstance(milestones, list) or not milestones:
        raise ValueError("快速收束必须同步返回完整关键里程碑")
    # Fast-closure output already carries the same semantics in a more specific
    # field.  Some providers omit the three generic movement fields rather than
    # repeat themselves; derive them without inventing any new story content.
    event_modes = {
        "collect": "discovery",
        "commit": "opposition",
        "confront": "opposition",
        "resolve": "consequence",
        "aftermath": "relationship",
        "next_arc": "subplot",
    }
    for row in roadmap:
        if not isinstance(row, dict):
            continue
        state = str(row.get("investigation_state") or "").strip().lower()
        row.setdefault("event_mode", event_modes.get(state, ""))
        row.setdefault("visible_event", row.get("required_change") or "")
        row.setdefault("irreversible_change", row.get("ending_state") or "")
    plan = current.get("plan") or {}
    _ensure_locked_rows(plan.get("phases") or [], phases, "phases")
    _ensure_locked_rows(plan.get("milestones") or [], milestones, "milestones")
    _validate_structure_stage({"arcs": plan.get("arcs") or [],
                               "milestones": milestones}, phases)
    locked_chapters = {row.get("chapter") for row in plan.get("chapter_roadmap") or []
                       if isinstance(row, dict) and row.get("locked")}
    stage = {"chapter_roadmap": roadmap}
    _validate_roadmap_stage(stage, phases, plan.get("arcs") or [],
                            expected_start=expected_start,
                            locked_chapters={chapter for chapter in locked_chapters
                                             if type(chapter) is int})
    states = [str(row.get("investigation_state") or "").strip().lower()
              for row in roadmap if row.get("chapter") not in locked_chapters]
    allowed = {"collect", "commit", "confront", "resolve", "aftermath", "next_arc"}
    if any(state not in allowed for state in states):
        raise ValueError("快速收束路线缺少有效 investigation_state")
    if states.count("collect") > 2:
        raise ValueError("证据搜集章节不能超过两章")
    if "confront" not in states[:3]:
        raise ValueError("快速收束必须在前三章进入正面对抗")
    if "resolve" not in states[:6]:
        raise ValueError("快速收束必须在六章内解决当前案件核心")
    resolve_at = states.index("resolve")
    if not any(state in {"aftermath", "next_arc"} for state in states[resolve_at + 1:]):
        raise ValueError("案件解决后必须进入后果或下一故事线")


def _ensure_locked_rows(old_rows: list, new_rows: list, collection: str) -> None:
    old_locked = {str(row.get("id") or row.get("chapter")): row for row in old_rows
                  if isinstance(row, dict) and row.get("locked")}
    incoming = {str(row.get("id") or row.get("chapter")): row for row in new_rows
                if isinstance(row, dict)}
    changed = [key for key, row in old_locked.items() if incoming.get(key) != row]
    if changed:
        raise ValueError(f"增量调整修改了锁定内容 {collection}：" + "、".join(changed[:8]))


def _complete_json_object(raw: str) -> dict[str, Any]:
    """Decode the root JSON object; never salvage a nested object from truncation."""
    text = re.sub(r"<think(?:ing)?>.*?</think(?:ing)?>", "", raw or "",
                  flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"```(?:json)?", "", text).strip()
    start = text.find("{")
    if start < 0:
        raise ValueError("模型未返回正式 JSON，只返回了思考过程或说明文字")
    try:
        value, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        raise ValueError("模型输出在根 JSON 闭合前被截断") from None
    if not isinstance(value, dict):
        raise ValueError("模型返回的根 JSON 不是对象")
    return value


def blueprint_for_planning(project_dir: str) -> dict[str, Any]:
    """Bounded source for chapter planning; omit proposal history."""
    blueprint = load_story_blueprint(project_dir)
    return {
        "facts_through_chapter": blueprint.get("facts_through_chapter", 0),
        "facts": (blueprint.get("facts") or [])[-40:],
        "plan": blueprint.get("plan") or {},
    }


def blueprint_graph_data(project_dir: str) -> dict[str, Any]:
    """Adapter-neutral graph IR suitable for the local viewer or Archify export."""
    blueprint = load_story_blueprint(project_dir)
    plan = blueprint.get("plan") or {}
    nodes, edges = [], []
    for phase in plan.get("phases") or []:
        nodes.append({"id": phase.get("id"), "kind": "phase", "label": phase.get("name"),
                      "status": phase.get("status"), "detail": phase.get("purpose")})
    for arc in plan.get("arcs") or []:
        nodes.append({"id": arc.get("id"), "kind": "arc", "label": arc.get("name"),
                      "status": arc.get("status"), "detail": arc.get("current_state")})
    for milestone in plan.get("milestones") or []:
        node_id = milestone.get("id")
        nodes.append({"id": node_id, "kind": "milestone", "label": milestone.get("name"),
                      "status": milestone.get("status"), "detail": milestone.get("purpose")})
        if milestone.get("phase_id"):
            edges.append({"source": milestone.get("phase_id"), "target": node_id,
                          "relation": "contains"})
        for arc_id in milestone.get("arc_ids") or []:
            edges.append({"source": arc_id, "target": node_id, "relation": "advances"})
    for chapter in plan.get("chapter_roadmap") or []:
        node_id = f"chapter-{chapter.get('chapter')}"
        nodes.append({"id": node_id, "kind": "chapter", "label": f"第 {chapter.get('chapter')} 章",
                      "status": chapter.get("status"), "detail": chapter.get("role")})
        if chapter.get("phase_id"):
            edges.append({"source": chapter.get("phase_id"), "target": node_id,
                          "relation": "contains"})
        if chapter.get("primary_arc"):
            edges.append({"source": chapter.get("primary_arc"), "target": node_id,
                          "relation": "primary"})
        for arc_id in chapter.get("secondary_arcs") or []:
            edges.append({"source": arc_id, "target": node_id, "relation": "secondary"})
    return {"version": 1, "title": "故事蓝图", "nodes": nodes, "edges": edges}
