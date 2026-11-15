"""Extract a bounded, read-only narrative map from already written chapters."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.continuation import read_chapter_summary
from core.project_manager import chapter_path, list_chapter_files
from core.utils import read_file


ANALYSIS_VERSION = 1
MAX_RANGE = 20


def _analysis_path(project_dir: str, start: int, end: int) -> Path:
    directory = Path(project_dir) / "narrative_analysis"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"chapters_{start}_{end}.json"


def _extract_object(raw: str) -> dict[str, Any]:
    cleaned = re.sub(r"<think(?:ing)?>.*?</think(?:ing)?>", "", raw or "",
                     flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"```(?:json)?", "", cleaned).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型未返回完整的叙事分析 JSON")
    data = json.loads(cleaned[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("叙事分析结果不是对象")
    return data


def _source_rows(project_dir: str, start: int, end: int) -> list[dict[str, Any]]:
    available = {number for number, _, _ in list_chapter_files(project_dir)}
    missing = [number for number in range(start, end + 1) if number not in available]
    if missing:
        shown = "、".join(str(number) for number in missing[:5])
        raise ValueError(f"章节范围不连续，缺少第 {shown} 章")
    rows = []
    for number in range(start, end + 1):
        text = read_file(chapter_path(project_dir, number)).strip()
        title, _, body = text.partition("\n")
        summary = (read_chapter_summary(project_dir, number) or "").strip()
        excerpt = body.strip() or text
        if len(excerpt) > 2400:
            excerpt = excerpt[:1400] + "\n……\n" + excerpt[-900:]
        rows.append({"chapter": number, "title": title.strip(),
                     "summary": summary[:700], "excerpt": excerpt})
    return rows


def _validate(data: dict[str, Any], start: int, end: int) -> dict[str, Any]:
    chapters = data.get("chapters")
    storylines = data.get("storylines")
    characters = data.get("characters")
    relationships = data.get("relationships")
    diagnosis = data.get("diagnosis")
    if not isinstance(chapters, list) or not chapters:
        raise ValueError("分析结果缺少章节节奏")
    if not isinstance(storylines, list) or not storylines:
        raise ValueError("分析结果缺少叙事线")
    if not isinstance(characters, list):
        raise ValueError("分析结果缺少人物变化")
    if not isinstance(relationships, list):
        raise ValueError("分析结果缺少人物关系")
    if not isinstance(diagnosis, dict):
        raise ValueError("分析结果缺少续写诊断")
    allowed = set(range(start, end + 1))
    seen = {row.get("chapter") for row in chapters if isinstance(row, dict)}
    if not seen or not seen.issubset(allowed):
        raise ValueError("章节节奏引用了范围外的章节")
    for row in chapters:
        if not isinstance(row, dict):
            continue
        for key in ("tension", "emotion", "mystery"):
            try:
                row[key] = max(1, min(5, int(row.get(key) or 1)))
            except (TypeError, ValueError):
                row[key] = 1
    data["version"] = ANALYSIS_VERSION
    data["range"] = {"start": start, "end": end}
    data["generated_at"] = datetime.now(timezone.utc).isoformat()
    return data


def load_narrative_analysis(project_dir: str, start: int, end: int) -> dict[str, Any] | None:
    target = _analysis_path(project_dir, start, end)
    if not target.is_file():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) and data.get("version") == ANALYSIS_VERSION else None


def latest_narrative_guidance(project_dir: str, before_chapter: int) -> dict[str, Any]:
    """Return the newest applicable diagnosis as advisory planning context."""
    directory = Path(project_dir) / "narrative_analysis"
    if not directory.is_dir():
        return {}
    candidates = []
    for path in directory.glob("chapters_*_*.json"):
        match = re.fullmatch(r"chapters_(\d+)_(\d+)\.json", path.name)
        if match and int(match.group(2)) < before_chapter:
            candidates.append((int(match.group(2)), path.stat().st_mtime, path))
    if not candidates:
        return {}
    _, _, target = max(candidates)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {"range": data.get("range"), "overview": data.get("overview", ""),
            "theme": data.get("theme", {}), "diagnosis": data.get("diagnosis", {})}


def generate_narrative_analysis(project_dir: str, start: int, end: int, llm,
                                progress=None) -> dict[str, Any]:
    if start < 1 or end < start:
        raise ValueError("章节范围无效")
    if end - start + 1 > MAX_RANGE:
        raise ValueError(f"一次最多分析 {MAX_RANGE} 章，请缩小范围")
    if progress:
        progress(f"正在读取第 {start}—{end} 章正文与摘要")
    rows = _source_rows(project_dir, start, end)
    prompt = f"""分析以下已经写完的小说章节。你的任务不是规划未来，也不是复述每章，而是从正文证据中提取叙事结构，帮助作者判断下一步如何续写。

必须区分：
1. storylines 是实际已经推进的叙事线，不得把同一调查的不同证据拆成多条线。
2. characters 关注人物欲望、选择、认知或底线的变化，不要只列出出场行为。
3. relationships 只记录本范围内确实发生变化或显出张力的关系。
4. theme 说明作品正在辩论的价值问题及正文证据，不要写空泛主题词。
5. diagnosis 必须指出重复、失衡和下一阶段可利用的机会，但不得虚构后续情节。
6. tension、emotion、mystery 使用 1—5 的整数。
7. 只输出完整 JSON，不要 Markdown 或解释。

输出结构：
{{
  "overview":"这一段故事实际发生了什么变化",
  "chapters":[{{"chapter":1,"role":"章节功能","event_mode":"action|discovery|relationship|consequence|decision|setup","summary":"一句话变化","turning_point":"转折或空字符串","tension":1,"emotion":1,"mystery":1}}],
  "storylines":[{{"id":"稳定英文ID","name":"线名","kind":"main|character|relationship|subplot|theme","current_direction":"这条线正在走向哪里","chapters":[{{"chapter":1,"change":"本章让该线发生的变化"}}]}}],
  "characters":[{{"name":"人物","role":"本段作用","want":"表层欲望","inner_conflict":"内在矛盾","change":"范围起点到终点的变化","chapters":[1]}}],
  "relationships":[{{"source":"人物A","target":"人物B","type":"关系性质","change":"关系变化","evidence_chapters":[1]}}],
  "theme":{{"question":"核心价值问题","positions":[{{"holder":"人物或力量","position":"其立场"}}],"evidence":[{{"chapter":1,"signal":"正文如何体现"}}],"current_landing":"目前落点或风险"}},
  "diagnosis":{{"strengths":["..."],"imbalances":["..."],"stalled_patterns":["..."],"continuation_priorities":["..."],"questions_to_preserve":["..."]}}
}}

章节资料：
{json.dumps(rows, ensure_ascii=False)}"""
    if progress:
        progress("正在提取叙事线、人物变化与关系网络")
    raw = llm.complete(prompt, system="你是长篇小说结构编辑，只根据提供的已写正文进行分析。",
                       temperature=0.2, num_predict=7000, disable_thinking=True)
    result = _validate(_extract_object(raw), start, end)
    target = _analysis_path(project_dir, start, end)
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(target)
    if progress:
        progress("叙事分析已生成，可用于判断后续续写重点")
    return result
