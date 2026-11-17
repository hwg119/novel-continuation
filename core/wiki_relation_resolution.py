"""Model-reviewed current relationship labels, kept separate from raw Wiki facts."""
from __future__ import annotations

import json
from pathlib import Path

from core.utils import load_json, save_data_to_json


FILENAME = "relationships_resolved.json"


def resolution_path(project_dir: str) -> Path:
    return Path(project_dir) / "wiki" / FILENAME


def load_resolutions(project_dir: str) -> list[dict]:
    rows = load_json(str(resolution_path(project_dir)), [])
    source = Path(project_dir) / "wiki" / "relationships_curated.json"
    source_mtime = source.stat().st_mtime_ns if source.is_file() else 0
    return [row for row in rows if isinstance(row, dict) and row.get("subject") and row.get("target")
            and row.get("source_mtime_ns") == source_mtime] if isinstance(rows, list) else []


def _pair(subject: str, target: str) -> tuple[str, str]:
    return tuple(sorted((subject, target)))


def review_character_relationships(project_dir: str, subject: str, llm, progress=None,
                                   through_chapter: int | None = None) -> dict:
    """Review one character's relation pairs from the full evidence history."""
    from core.auto_wiki import _extract_json, _valid_facts
    from core.project_manager import chapter_path
    from core.utils import read_file
    from core.story_state import (is_relation_label, load_relationship_candidates,
                                  load_relationship_seeds, relationship_name_aliases)
    from core.wiki_identity import load_identity_decisions

    curated = load_relationship_candidates(project_dir)
    seeds = load_relationship_seeds(project_dir)
    confirmed = [row for row in curated if row.get("confidence") == "confirmed"]
    aliases = relationship_name_aliases([*confirmed, *seeds], load_identity_decisions(project_dir))
    focus = aliases.get(subject, subject)
    if not focus:
        raise ValueError("请先选择人物")
    raw = [row for row in _valid_facts(project_dir, (through_chapter + 1) if through_chapter else None)
           if row.get("kind") == "relationship"]
    grouped: dict[tuple[str, str], list[dict]] = {}
    for row in [*seeds, *curated, *raw]:
        chapter = int(row.get("chapter") or 0)
        if through_chapter and chapter > through_chapter:
            continue
        left = aliases.get(str(row.get("subject") or "").strip(), str(row.get("subject") or "").strip())
        right = aliases.get(str(row.get("target") or "").strip(), str(row.get("target") or "").strip())
        if focus not in (left, right) or not left or not right or left == right:
            continue
        grouped.setdefault(_pair(left, right), []).append({
            "subject": left, "target": right, "relation": str(row.get("predicate") or ""),
            "quote": str(row.get("quote") or ""), "reason": str(row.get("reason") or ""),
            "chapter": chapter, "source": str(row.get("source") or "wiki_original"),
        })
    grouped = {pair: evidence for pair, evidence in grouped.items()
               if any(is_relation_label(row["relation"]) or row["source"] == "author_setting"
                      for row in evidence)
               or len({row["chapter"] for row in evidence if row["chapter"]}) >= 3}
    if not grouped:
        return {"subject": focus, "reviewed": 0, "confirmed": 0, "uncertain": 0, "pairs": 0}
    saved = load_resolutions(project_dir)
    source_file = Path(project_dir) / "wiki" / "relationships_curated.json"
    source_mtime = source_file.stat().st_mtime_ns if source_file.is_file() else 0
    by_pair = {_pair(row["subject"], row["target"]): row for row in saved}
    reviewed = 0
    incomplete = False
    for index, (pair, evidence) in enumerate(sorted(grouped.items()), 1):
        if progress:
            progress(f"正在核对 {focus} 的人物关系 {index}/{len(grouped)}：{pair[0]} / {pair[1]}")
        # Keep author assertions and varied older/recent evidence; no single
        # chapter's incidental address may replace a durable relation.
        author = [row for row in evidence if row["source"] == "author_setting"]
        dated = sorted((row for row in evidence if row["source"] != "author_setting"),
                       key=lambda row: row["chapter"])
        selected = [*author, *dated[:4], *dated[-12:]]
        selected = list({(row["subject"], row["target"], row["relation"], row["chapter"], row["quote"]): row
                         for row in selected}.values())
        kin_terms = ("父", "母", "儿", "女", "兄", "弟", "姐", "妹", "夫妻", "配偶")
        nearby = [row for row in [*seeds, *confirmed]
                  if (aliases.get(str(row.get("subject") or ""), row.get("subject")) in pair or
                      aliases.get(str(row.get("target") or ""), row.get("target")) in pair)
                  and any(term in str(row.get("predicate") or "") for term in kin_terms)]
        nearby = sorted(nearby, key=lambda row: (row.get("source") == "author_setting",
                                                   int(row.get("chapter") or 0)), reverse=True)[:12]
        context_chapters = sorted({int(row.get("chapter") or 0) for row in [*selected, *nearby]
                                   if row.get("chapter")}, reverse=True)[:2]
        chapter_context = []
        for number in context_chapters:
            body = read_file(chapter_path(project_dir, number)).strip()
            if body:
                excerpt = body if len(body) <= 7000 else body[:2500] + "\n……\n" + body[-4500:]
                chapter_context.append({"chapter": number, "text": excerpt})
        prompt = ("你是长篇小说的人物关系编辑。根据已保存的逐章证据，给出这两人的当前稳定关系。"
                  "请区分同名者、亲子与教亲、交往与婚姻、过去与现在；作者设定优先。"
                  "不要把称呼、单次互动或旁人的亲属关系写成这两人的关系。"
                  "关系名称用简短明确称谓，如夫妻、父子、师生、挚友；证据不足或相互矛盾时标 uncertain。"
                  "可以返回 no_relation，表示没有可确认的稳定关系。"
                  "仅输出一个 JSON 数组，只有一项：subject,target,relation,status,reason,evidence_chapters,evidence_quotes,direction_from,direction_to,from_role,to_role；"
                  "只有证据明确支持非对称身份（如父亲到儿子）时填写 direction_from/direction_to 和双方角色，名字必须来自人物对；其他情况留空。"
                  "evidence_quotes 为 [{chapter,quote}]，quote 必须是所给正文或关系证据里的连续原句。"
                  "status 为 confirmed、uncertain 或 no_relation，subject/target 必须与给定名字一致。\n"
                  f"人物对：{json.dumps(pair, ensure_ascii=False)}\n"
                  f"逐章证据：{json.dumps(selected, ensure_ascii=False)[:18000]}\n"
                  f"相关亲属线索（仅作交叉核对）：{json.dumps(nearby, ensure_ascii=False)[:5000]}\n"
                  f"相关章节原文：{json.dumps(chapter_context, ensure_ascii=False)[:14000]}")
        try:
            parsed = _extract_json(llm.complete(prompt, disable_thinking=True, num_predict=1400))
        except (ValueError, json.JSONDecodeError):
            incomplete = True
            break
        item = parsed[0] if parsed and isinstance(parsed[0], dict) else {}
        status = item.get("status") if item.get("status") in {"confirmed", "uncertain", "no_relation"} else "uncertain"
        relation = str(item.get("relation") or "").strip()
        if _pair(str(item.get("subject") or ""), str(item.get("target") or "")) != pair:
            status = "uncertain"
        chapters = {row["chapter"] for row in selected if row["chapter"]}
        requested_citations = item.get("evidence_chapters")
        cited = [n for n in requested_citations if isinstance(n, int) and n in chapters] if isinstance(requested_citations, list) else []
        requested_quotes = item.get("evidence_quotes")
        grounded_quotes = []
        for quote_item in requested_quotes if isinstance(requested_quotes, list) else []:
            if not isinstance(quote_item, dict):
                continue
            number, quote = quote_item.get("chapter"), str(quote_item.get("quote") or "").strip()
            if isinstance(number, int) and number in cited and quote and quote in read_file(chapter_path(project_dir, number)):
                grounded_quotes.append(quote)
        if status == "confirmed" and (not relation or len(relation) > 12 or
                                       not grounded_quotes and not author):
            status = "uncertain"
        direction_from = str(item.get("direction_from") or "").strip()
        direction_to = str(item.get("direction_to") or "").strip()
        from_role = str(item.get("from_role") or "").strip()
        to_role = str(item.get("to_role") or "").strip()
        verified_direction = (status == "confirmed" and direction_from != direction_to and
                              {direction_from, direction_to} == set(pair) and
                              bool(from_role and to_role) and len(from_role) <= 12 and len(to_role) <= 12)
        decision = {"subject": pair[0], "target": pair[1], "predicate": relation,
                    "status": status, "reason": str(item.get("reason") or "")[:600],
                    "evidence_quotes": grounded_quotes[:3] or [row["quote"] for row in author if row["quote"]][:1],
                    "chapters": cited, "through_chapter": through_chapter or max(chapters or {0}),
                    "source": "model_reviewed", "source_mtime_ns": source_mtime,
                    "direction_from": direction_from if verified_direction else "",
                    "direction_to": direction_to if verified_direction else "",
                    "from_role": from_role if verified_direction else "",
                    "to_role": to_role if verified_direction else ""}
        by_pair[pair] = decision
        save_data_to_json(list(by_pair.values()), str(resolution_path(project_dir)))
        reviewed += 1
    return {"subject": focus, "reviewed": reviewed,
            "confirmed": sum(row.get("status") == "confirmed" for pair, row in by_pair.items() if focus in pair),
            "uncertain": sum(row.get("status") == "uncertain" for pair, row in by_pair.items() if focus in pair),
            "pairs": len(grouped), "status": "partial" if incomplete else "completed"}
