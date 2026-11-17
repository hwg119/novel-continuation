"""Evidence-backed identity decisions for names found in relationship records."""
from __future__ import annotations

import json
import re
from pathlib import Path

from core.utils import load_json, save_data_to_json


FILENAME = "identity_resolutions.json"


def identity_path(project_dir: str) -> Path:
    return Path(project_dir) / "wiki" / FILENAME


def load_identity_decisions(project_dir: str) -> list[dict]:
    data = load_json(str(identity_path(project_dir)), [])
    return [row for row in data if isinstance(row, dict) and row.get("left") and row.get("right")] if isinstance(data, list) else []


def _parts(name: str) -> list[str]:
    return [part.strip() for part in name.replace("・", "·").split("·") if part.strip()]


def identity_candidates(rows: list[dict]) -> list[tuple[str, str]]:
    """Find plausible variants without treating a shared given name as proof."""
    names = sorted({str(row.get(key) or "").strip() for row in rows for key in ("subject", "target")
                    if not re.search(r"(?:与|和|、|\band\b)", str(row.get(key) or ""), re.IGNORECASE)} - {""})
    family = {name: set() for name in names}
    for row in rows:
        if not any(word in str(row.get("predicate") or "") for word in ("父", "母", "兄", "弟", "姐", "妹", "儿", "女")):
            continue
        left, right = str(row.get("subject") or "").strip(), str(row.get("target") or "").strip()
        if left in family and right in family:
            family[left].add(right)
            family[right].add(left)
    pairs = []
    for index, left in enumerate(names):
        a = _parts(left)
        if not a:
            continue
        for right in names[index + 1:]:
            b = _parts(right)
            if not b or a[0] != b[0]:
                continue
            if len(a) == 1 or len(b) == 1:
                short, full = (left, right) if len(a) == 1 else (right, left)
                expansions = [name for name in names if len(_parts(name)) > 1 and _parts(name)[0] == short]
                plausible = len(expansions) == 1 and bool(family[short]) and bool(family[full])
            else:
                plausible = ((len(a) != len(b) and a[-1] == b[-1]) or bool(family[left] & family[right]))
            if plausible:
                pairs.append((left, right))
    return pairs


def identity_status(project_dir: str) -> dict:
    from core.story_state import load_curated_relationships, load_relationship_seeds
    rows = [*load_curated_relationships(project_dir), *load_relationship_seeds(project_dir)]
    decisions = load_identity_decisions(project_dir)
    decided = {tuple(sorted((row["left"], row["right"]))) for row in decisions}
    pending = [row for row in decisions if row.get("status") == "tentative"]
    return {"candidate_pairs": len(identity_candidates(rows)),
            "unprocessed": sum(pair not in decided for pair in identity_candidates(rows)),
            "confirmed": sum(row.get("status") == "confirmed" for row in decisions),
            "pending": pending, "distinct": sum(row.get("status") == "distinct" for row in decisions)}


def review_identities(project_dir: str, llm, progress=None) -> dict:
    """Review plausible historical aliases; checkpoint every model decision."""
    from core.auto_wiki import _extract_json, _valid_facts
    from core.story_state import load_curated_relationships, load_relationship_seeds
    rows = [*load_curated_relationships(project_dir), *load_relationship_seeds(project_dir)]
    original = [row for row in _valid_facts(project_dir) if row.get("kind") == "relationship"]
    pairs = identity_candidates(rows)
    decisions = load_identity_decisions(project_dir)
    seen = {tuple(sorted((row["left"], row["right"]))) for row in decisions}
    processed = 0
    failed = False
    for index, (left, right) in enumerate(pairs, 1):
        if (left, right) in seen:
            continue
        evidence = [row for row in [*rows, *original]
                    if left in (row.get("subject"), row.get("target"))
                    or right in (row.get("subject"), row.get("target"))]
        # Include both names, close relatives and citations; avoid arbitrary first-N truncation.
        selected = ([row for row in evidence if row.get("source") == "author_setting"]
                    + [row for row in evidence if left in (row.get("subject"), row.get("target"))][-6:]
                    + [row for row in evidence if right in (row.get("subject"), row.get("target"))][-6:])
        selected = list({(row.get("subject"), row.get("target"), row.get("chapter"), row.get("predicate")): row
                         for row in selected}.values())
        if progress:
            progress(f"正在核对人物身份 {index}/{len(pairs)}：{left} / {right}")
        prompt = ("你是小说人物身份编辑。判断两个姓名是否指同一人物；同名者必须区分。"
                  "只能依据给定原文关系与作者设定，不要借助作品外知识。"
                  "输出 JSON 数组，仅一项，字段 status (confirmed/tentative/distinct), reason, evidence_chapters。"
                  "confirmed 需要明确的共同身份依据；不确定用 tentative。不要编造章节。\n"
                  f"姓名：{json.dumps([left, right], ensure_ascii=False)}\n"
                  f"关系证据：{json.dumps(selected, ensure_ascii=False)[:16000]}")
        try:
            parsed = _extract_json(llm.complete(prompt, disable_thinking=True, num_predict=1200))
        except (ValueError, json.JSONDecodeError):
            failed = True
            break  # Preserve the checkpoint and allow a later retry.
        item = parsed[0] if parsed and isinstance(parsed[0], dict) else {}
        status = item.get("status") if item.get("status") in {"confirmed", "tentative", "distinct"} else "tentative"
        chapters = sorted({int(row.get("chapter") or 0) for row in selected if row.get("chapter")})
        cited = [number for number in item.get("evidence_chapters", []) if isinstance(number, int) and number in chapters]
        if status == "confirmed" and not cited and not any(row.get("source") == "author_setting" for row in selected):
            status = "tentative"
        decisions.append({"left": left, "right": right, "status": status,
                          "reason": str(item.get("reason") or "")[:500],
                          "chapters": cited, "source": "model"})
        save_data_to_json(decisions, str(identity_path(project_dir)))
        processed += 1
    return {**identity_status(project_dir), "processed": processed,
            "status": "partial" if failed else "completed"}


def decide_identity(project_dir: str, left: str, right: str, status: str) -> dict:
    if status not in {"confirmed", "distinct"}:
        raise ValueError("身份决定无效")
    decisions = load_identity_decisions(project_dir)
    pair = tuple(sorted((left, right)))
    for row in decisions:
        if tuple(sorted((row["left"], row["right"]))) == pair:
            if row.get("status") != "tentative":
                raise ValueError("该身份已处理")
            row.update(status=status, source="user_confirmed")
            save_data_to_json(decisions, str(identity_path(project_dir)))
            return row
    raise ValueError("待确认身份不存在")
