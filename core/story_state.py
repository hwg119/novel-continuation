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
RELATIONSHIP_SEEDS_FILENAME = "relationship_seeds.json"
RELATIONSHIP_CURATED_FILENAME = "relationships_curated.json"


def is_relation_label(value: str) -> bool:
    """A relationship label names a role; a sentence belongs in evidence."""
    label = str(value or "").strip()
    roles = ("父", "母", "儿", "女", "兄", "弟", "姐", "妹", "祖", "孙", "叔", "伯", "姨", "舅",
             "夫妻", "配偶", "恋人", "伴侣", "朋友", "挚友", "同学", "同事", "师", "徒", "盟友", "敌人", "对手", "亲戚")
    return (bool(label) and len(label) <= 8 and any(role in label for role in roles)
            and not any(mark in label for mark in "，。；：,:;!?！？()（）—")
            and not label.startswith(("因", "与", "对", "曾", "听", "称")))


def _is_canonical_relationship(row: dict[str, Any]) -> bool:
    """Keep only durable relationship assertions in the planning layer.

    Raw Wiki extraction remains available in the history graph, but incidental
    address, proximity, observation and one-off actions must not become canon.
    """
    predicate = str(row.get("predicate") or "").strip()
    value = str(row.get("value") or "").strip()
    category = str(row.get("stable_category") or "").strip()
    if str(row.get("source") or "") == "author_setting":
        return True
    if predicate in {"称呼", "在场", "同场", "看见", "遇见", "交谈", "合作", "冲突"}:
        return False
    if category in {"biological_kinship", "identity_origin"}:
        return True
    durable_terms = ("关系", "兄", "弟", "姐", "妹", "父", "母", "夫妻", "配偶", "亲属", "师生", "盟友", "宿敌", "朋友")
    return any(term in predicate or term in value for term in durable_terms) and len(value) >= 2


def story_state_path(project_dir: str) -> Path:
    return Path(project_dir) / STORY_STATE_FILENAME


def relationship_seeds_path(project_dir: str) -> Path:
    return Path(project_dir) / "wiki" / RELATIONSHIP_SEEDS_FILENAME


def load_relationship_seeds(project_dir: str) -> list[dict[str, Any]]:
    data = load_json(str(relationship_seeds_path(project_dir)), [])
    if not isinstance(data, list):
        return []
    rows = []
    for item in data:
        if not isinstance(item, dict):
            continue
        subject = str(item.get("subject") or "").strip()
        target = str(item.get("target") or "").strip()
        predicate = str(item.get("predicate") or "").strip()
        if subject and target and predicate and subject != target:
            rows.append({"subject": subject, "target": target, "predicate": predicate,
                         "value": str(item.get("value") or ""), "chapter": 0,
                         "quote": str(item.get("quote") or "作者设定关系"),
                         "confidence": "author_setting", "source": "author_setting"})
    return rows


def load_curated_relationships(project_dir: str) -> list[dict[str, Any]]:
    data = load_json(str(Path(project_dir) / "wiki" / RELATIONSHIP_CURATED_FILENAME), [])
    return [row for row in data if isinstance(row, dict) and row.get("subject") and row.get("target")
            and row.get("confidence") == "confirmed"
            and is_relation_label(row.get("predicate", ""))]


def load_relationship_candidates(project_dir: str) -> list[dict[str, Any]]:
    data = load_json(str(Path(project_dir) / "wiki" / RELATIONSHIP_CURATED_FILENAME), [])
    return [row for row in data if isinstance(row, dict) and row.get("subject") and row.get("target")]


def confirm_relationship_candidate(project_dir: str, index: int) -> dict[str, Any]:
    path = Path(project_dir) / "wiki" / RELATIONSHIP_CURATED_FILENAME
    rows = load_json(str(path), [])
    if not isinstance(rows, list) or index < 0 or index >= len(rows) or not isinstance(rows[index], dict):
        raise ValueError("关系候选不存在")
    row = rows[index]
    if row.get("confidence") != "tentative" or not is_relation_label(row.get("predicate", "")):
        raise ValueError("该关系不是待确认状态")
    row["confidence"] = "confirmed"
    row["source"] = "user_confirmed"
    save_data_to_json(rows, str(path))
    refresh_relationship_state(project_dir)
    return row


def empty_story_state() -> dict[str, Any]:
    return {
        "updated_through_chapter": 0,
        "story_phase": {},
        "arcs": [],
        "recent_chapter_changes": [],
        "relationship_state": [],
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
    if not isinstance(result.get("relationship_state"), list):
        result["relationship_state"] = []
    return result


def merge_relationship_state(project_dir: str, through_chapter: int | None = None) -> list[dict[str, Any]]:
    """Build a compact current relation snapshot from reviewed chapter facts.

    Chapter Wiki files remain the evidence layer. This derived view keeps the
    latest explicit relation records while retaining their source citations.
    It is intentionally deterministic; no model call is needed for merging.
    """
    from core.auto_wiki import _valid_facts
    curated = load_curated_relationships(project_dir)
    facts = [row for row in curated if through_chapter is None or int(row.get("chapter") or 0) <= through_chapter]
    latest: dict[tuple[str, str], dict[str, Any]] = {
        (row["subject"], row["target"]): row
        for row in load_relationship_seeds(project_dir)
    }
    for fact in facts:
        subject = str(fact.get("subject") or "").strip()
        target = str(fact.get("target") or "").strip()
        predicate = str(fact.get("predicate") or "").strip()
        if not subject or not target or not predicate or subject == target:
            continue
        key = (subject, target)
        if fact.get("status") == "ended":
            latest.pop(key, None)
            continue
        latest[key] = {
            "subject": subject, "target": target, "predicate": predicate,
            "value": str(fact.get("value") or ""),
            "chapter": int(fact.get("chapter") or 0),
            "quote": str(fact.get("quote") or ""),
            "confidence": str(fact.get("confidence") or "explicit"),
            "source": str(fact.get("source") or "model_curated"),
        }
    return sorted(latest.values(), key=lambda row: (row["subject"], row["target"], row["predicate"]))


def relationship_name_aliases(rows: list[dict[str, Any]], decisions: list[dict] | None = None) -> dict[str, str]:
    """Join name variants only when a name and independent family evidence agree.

    A shared given name alone is insufficient: namesakes must stay separate.
    The derived mapping does not alter the model's evidence or author settings.
    """
    names = {str(row.get(key) or "").strip() for row in rows for key in ("subject", "target")}
    names.discard("")
    parent = {name: name for name in names}
    decisions = decisions or []
    distinct = {frozenset((row["left"], row["right"])) for row in decisions
                if row.get("status") == "distinct"}

    def root(name: str) -> str:
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    def join(left: str, right: str) -> None:
        left_group = {name for name in names if root(name) == root(left)}
        right_group = {name for name in names if root(name) == root(right)}
        if any(frozenset((a, b)) in distinct for a in left_group for b in right_group):
            return
        parent[root(right)] = root(left)

    def parts(name: str) -> list[str]:
        return [part.strip() for part in name.replace("・", "·").split("·") if part.strip()]

    family: dict[str, set[str]] = {name: set() for name in names}
    for row in rows:
        label = str(row.get("predicate") or "")
        if not any(word in label for word in ("父", "母", "兄", "弟", "姐", "妹", "儿", "女")):
            continue
        subject, target = str(row.get("subject") or "").strip(), str(row.get("target") or "").strip()
        if subject in family and target in family:
            family[subject].add(target)
            family[target].add(subject)

    ordered = sorted(names)
    for index, left in enumerate(ordered):
        left_parts = parts(left)
        if len(left_parts) < 2:
            continue
        for right in ordered[index + 1:]:
            right_parts = parts(right)
            if len(right_parts) < 2 or left_parts[0] != right_parts[0]:
                continue
            # A shared first and last component supports a shortened/full form.
            same_family_name = (len(left_parts) != len(right_parts)
                                and left_parts[-1] == right_parts[-1])
            shared_relative = bool(family[left] & family[right])
            if same_family_name or shared_relative:
                join(left, right)

    for row in decisions:
        if row.get("status") == "confirmed" and row["left"] in names and row["right"] in names:
            join(row["left"], row["right"])

    # A short name may be the first component of one full name. Require a
    # shared relative after the longer name variants have already been joined.
    for short in ordered:
        if len(parts(short)) != 1:
            continue
        full_names = [name for name in ordered if len(parts(name)) > 1 and parts(name)[0] == short]
        full_groups = {root(name) for name in full_names}
        if len(full_groups) != 1 or not family[short]:
            continue
        full = full_names[0]
        short_relatives = {root(name) for name in family[short]}
        full_relatives = {root(relative) for name in full_names for relative in family[name]}
        if short_relatives & full_relatives:
            join(short, full)

    groups: dict[str, list[str]] = {}
    for name in ordered:
        groups.setdefault(root(name), []).append(name)
    aliases = {}
    for group in groups.values():
        if len(group) < 2:
            continue
        # Prefer a name present in author settings, then the most connected name.
        seed_names = {str(row.get(key) or "") for row in rows if row.get("source") == "author_setting"
                      for key in ("subject", "target")}
        canonical = max(group, key=lambda name: (name in seed_names,
                         sum(name in (row.get("subject"), row.get("target")) for row in rows), len(name)))
        aliases.update({name: canonical for name in group if name != canonical})
    return aliases


def refresh_relationship_state(project_dir: str, through_chapter: int | None = None) -> dict[str, Any]:
    state = load_story_state(project_dir)
    latest = latest_saved_chapter(project_dir, (through_chapter + 1) if through_chapter else None)
    state["relationship_state"] = merge_relationship_state(project_dir, through_chapter)
    state["relationship_state_through_chapter"] = int(through_chapter or latest or 0)
    state["relationship_state_version"] = 6
    curated_path = Path(project_dir) / "wiki" / RELATIONSHIP_CURATED_FILENAME
    state["relationship_curated_mtime_ns"] = curated_path.stat().st_mtime_ns if curated_path.is_file() else 0
    save_story_state(project_dir, state)
    return state


def relationship_context(project_dir: str, before_chapter: int | None = None,
                         names: list[str] | None = None, limit: int = 24) -> list[dict[str, Any]]:
    state = load_story_state(project_dir)
    curated_path = Path(project_dir) / "wiki" / RELATIONSHIP_CURATED_FILENAME
    curated_mtime = curated_path.stat().st_mtime_ns if curated_path.is_file() else 0
    through = (before_chapter - 1) if before_chapter else None
    seed_keys = {(row["subject"], row["target"], row["predicate"])
                 for row in load_relationship_seeds(project_dir)}
    state_keys = {(str(row.get("subject") or ""), str(row.get("target") or ""),
                   str(row.get("predicate") or "")) for row in state.get("relationship_state") or []}
    if (int(state.get("relationship_state_through_chapter") or 0)
            < latest_saved_chapter(project_dir, before_chapter)
            or int(state.get("relationship_curated_mtime_ns") or 0) != curated_mtime
            or state.get("relationship_state_version") != 6
            or not seed_keys.issubset(state_keys)):
        state = refresh_relationship_state(project_dir, through)
    rows = state.get("relationship_state") or []
    wanted = {str(name).strip() for name in (names or []) if str(name).strip()}
    if wanted:
        rows = [row for row in rows if row.get("subject") in wanted or row.get("target") in wanted]
    return rows[:max(1, limit)]


def review_relationship_context(project_dir: str, chapter_number: int,
                                chapter_text: str, limit: int = 32) -> list[dict[str, Any]]:
    """Provide the story editor with relevant, identity-resolved prior relations."""
    from core.auto_wiki import _entity_aliases
    from core.wiki_identity import load_identity_decisions

    rows = relationship_context(project_dir, chapter_number, limit=10000)
    aliases = relationship_name_aliases(rows, load_identity_decisions(project_dir))
    current = []
    for row in rows:
        subject = aliases.get(row["subject"], row["subject"])
        target = aliases.get(row["target"], row["target"])
        if subject == target:
            continue
        current.append({**row, "subject": subject, "target": target})
    current = apply_relation_resolutions(project_dir, current, aliases, chapter_number)
    # Wiki extraction is evidence, not a settled character fact. Keep its
    # provenance explicit so the story editor cannot promote a raw label to a
    # kinship assertion merely because it appears in merged context.
    for row in current:
        row["relation_status"] = ("confirmed" if row.get("source") in {"model_reviewed", "author_setting"}
                                  else "unverified")
        row["role_direction_verified"] = bool(
            row.get("source") == "model_reviewed" and row.get("direction_from") and
            row.get("direction_to") and row.get("from_role") and row.get("to_role"))

    # The chapter body, not alphabetical entity order, chooses the reference set.
    mentioned = {name for row in current for name in (row["subject"], row["target"])
                 if len(name) >= 2 and name in chapter_text}
    for alias, canonical in aliases.items():
        if len(alias) >= 2 and alias in chapter_text:
            mentioned.add(canonical)
    glossary_aliases, _ = _entity_aliases(project_dir)
    for alias, canonical in glossary_aliases.items():
        if len(alias) >= 2 and alias in chapter_text.casefold():
            mentioned.add(aliases.get(canonical, canonical))
    relevant = [row for row in current if row["subject"] in mentioned or row["target"] in mentioned]
    relevant.sort(key=lambda row: (row.get("source") == "author_setting",
                                   int(row.get("chapter") or 0)), reverse=True)
    result = []
    seen = set()
    for row in relevant:
        key = (row["subject"], row["target"], row["predicate"])
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
        if len(result) >= limit:
            break
    return result


def apply_relation_resolutions(project_dir: str, rows: list[dict[str, Any]],
                               aliases: dict[str, str], before_chapter: int | None) -> list[dict[str, Any]]:
    """Overlay reviewed pair states without changing the chapter evidence files."""
    from core.wiki_relation_resolution import load_resolutions

    replacements = {}
    for item in load_resolutions(project_dir):
        if before_chapter and int(item.get("through_chapter") or 0) >= before_chapter:
            continue
        source = aliases.get(item["subject"], item["subject"])
        target = aliases.get(item["target"], item["target"])
        if source == target:
            continue
        replacements[tuple(sorted((source, target)))] = {**item, "subject": source, "target": target}
    kept = []
    for row in rows:
        source = aliases.get(row["subject"], row["subject"])
        target = aliases.get(row["target"], row["target"])
        if tuple(sorted((source, target))) not in replacements:
            kept.append(row)
    for item in replacements.values():
        if item.get("status") != "confirmed":
            continue
        kept.append({"subject": item["subject"], "target": item["target"],
                     "predicate": item["predicate"], "value": item["predicate"],
                     "chapter": max(item.get("chapters") or [0]),
                     "quote": "；".join(item.get("evidence_quotes") or []) or item.get("reason") or "模型复核关系",
                     "confidence": "confirmed", "source": "model_reviewed",
                     "direction_from": item.get("direction_from", ""),
                     "direction_to": item.get("direction_to", ""),
                     "from_role": item.get("from_role", ""), "to_role": item.get("to_role", "")})
    return kept


def merged_relationship_graph(project_dir: str, before_chapter: int | None = None) -> dict[str, Any]:
    """Graph-shaped view of the current merged relation snapshot."""
    rows = relationship_context(project_dir, before_chapter, limit=10000)
    from core.wiki_identity import load_identity_decisions
    from core.wiki_relation_resolution import load_resolutions
    aliases = relationship_name_aliases(rows, load_identity_decisions(project_dir))
    review_pending = [row for row in load_resolutions(project_dir)
                      if row.get("status") == "uncertain" and
                      (before_chapter is None or int(row.get("through_chapter") or 0) < before_chapter)]
    rows = apply_relation_resolutions(project_dir, rows, aliases, before_chapter)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        source = aliases.get(str(row.get("subject") or ""), str(row.get("subject") or ""))
        target = aliases.get(str(row.get("target") or ""), str(row.get("target") or ""))
        if not source or not target or source == target:
            continue
        pair = tuple(sorted((source, target)))
        direction_from = aliases.get(str(row.get("direction_from") or ""), str(row.get("direction_from") or ""))
        direction_to = aliases.get(str(row.get("direction_to") or ""), str(row.get("direction_to") or ""))
        verified_direction = (row.get("source") == "model_reviewed" and
                              {direction_from, direction_to} == set(pair) and
                              direction_from != direction_to and row.get("from_role") and row.get("to_role"))
        grouped.setdefault(pair, []).append({
            "source": source, "target": target,
            "predicate": row.get("predicate", ""), "value": row.get("value", ""),
            "chapter": row.get("chapter", 0), "story_time": "", "quote": row.get("quote", ""),
            "source_type": row.get("source", "chapter_fact"),
            "role_direction_verified": bool(verified_direction),
            "direction_from": direction_from if verified_direction else "",
            "direction_to": direction_to if verified_direction else "",
            "from_role": row.get("from_role", "") if verified_direction else "",
            "to_role": row.get("to_role", "") if verified_direction else "",
        })
    edges, degrees = [], {}
    for index, (pair, records) in enumerate(sorted(grouped.items()), 1):
        source, target = pair
        degrees[source] = degrees.get(source, 0) + 1
        degrees[target] = degrees.get(target, 0) + 1
        labels = []
        for record in records:
            # Prefer a concrete assertion over a broad classifier such as
            # “家庭关系”.
            value = str(record.get("value") or "").strip()
            predicate = str(record.get("predicate") or "").strip()
            label = predicate if is_relation_label(predicate) else value
            if label and label not in labels:
                labels.append(label)
        edges.append({"id": f"r{index}", "source": source, "target": target,
                      "count": len(records), "chapters": sorted({r["chapter"] for r in records}),
                      "predicates": labels,
                      "records": records})
    candidates = [{**row, "index": index} for index, row in enumerate(load_relationship_candidates(project_dir))
                  if row.get("confidence") == "tentative" and is_relation_label(row.get("predicate", ""))
                  and (before_chapter is None or int(row.get("chapter") or 0) < before_chapter)]
    return {"nodes": [{"id": name, "label": name, "degree": degree}
                      for name, degree in sorted(degrees.items(), key=lambda item: (-item[1], item[0]))],
            "edges": edges, "records_count": sum(edge["count"] for edge in edges),
            "candidates": candidates, "candidates_count": len(candidates),
            "review_pending": review_pending,
            "view": "merged", "through_chapter": before_chapter - 1 if before_chapter else None,
            "name_aliases": aliases}


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
        "story_blueprint": blueprint_for_planning(project_dir, before_chapter),
    }
