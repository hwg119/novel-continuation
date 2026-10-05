"""规划审查的分类证据目录与逐条引文定位。"""
import re


def _text(value):
    if isinstance(value, dict):
        return "\n".join(_text(part) for part in value.values())
    if isinstance(value, list):
        return "\n".join(_text(part) for part in value)
    return str(value or "")


def evidence_sources(context, wiki, plan):
    sources = []
    headings = list(re.finditer(r"【([^】]+)】", context))
    for index, match in enumerate(headings):
        label = match.group(1)
        end = headings[index + 1].start() if index + 1 < len(headings) else len(context)
        kind = ("metadata" if "可用性提示" in label else "plan_reference" if "规划参考" in label
                else "summary" if "摘要" in label else "body")
        sources.append({"id": f"C{index + 1:02}", "kind": kind, "label": label,
                        "text": context[match.end():end].strip()})
    if not headings and context:
        sources.append({"id": "C01", "kind": "context", "label": "上下文", "text": context})
    wiki_entries = list(re.finditer(r"\[(W\d+)\]", wiki))
    for index, match in enumerate(wiki_entries):
        end = wiki_entries[index + 1].start() if index + 1 < len(wiki_entries) else len(wiki)
        sources.append({"id": match.group(1), "kind": "wiki", "label": "Wiki 原文依据",
                        "text": wiki[match.end():end].strip()})
    if not wiki_entries and wiki:
        sources.append({"id": "W00", "kind": "wiki", "label": "Wiki", "text": wiki})
    for index, beat in enumerate(plan.get("beats") or [], 1):
        sources.append({"id": f"P{index:02}", "kind": "plan", "label": f"待审第{index}幕",
                        "text": _text(beat)})
    return sources


def verify_issue(item, sources):
    def normalized(value):
        return re.sub(r"\s+", "", str(value)).replace("“", '"').replace("”", '"')
    by_id = {row["id"]: row for row in sources}
    citations = item.get("citations")
    # 兼容旧输出，但不得把拼接引文跨来源定位。
    if not isinstance(citations, list):
        citations = [{"source_id": "", "quote": item.get(key, "")}
                     for key in ("context_quote", "plan_quote")]
    verified = []
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        quote = str(citation.get("quote") or "").strip()
        source_id = str(citation.get("source_id") or "")
        candidates = [by_id[source_id]] if source_id in by_id else sources if not source_id else []
        found = next((row for row in candidates if quote and normalized(quote) in normalized(row["text"])), None)
        # 来源编号可能写错；只在唯一匹配时恢复，避免把歧义引文错归来源。
        if not found and quote:
            matches = [row for row in sources if normalized(quote) in normalized(row["text"])]
            if len(matches) == 1:
                found = matches[0]
        verified.append({"source_id": found["id"] if found else source_id,
                         "claimed_source_id": source_id,
                         "source_corrected": bool(found and source_id and found["id"] != source_id),
                         "source_kind": found["kind"] if found else None,
                         "quote": quote, "verified": bool(found)})
    item["citations"] = verified
    located = [row for row in verified if row["verified"]]
    has_plan = any(row["source_kind"] == "plan" for row in located)
    has_comparison = (any(row["source_kind"] in {"body", "summary", "wiki", "context"}
                          for row in located)
                      or len({(row["source_id"], normalized(row["quote"])) for row in located
                              if row["source_kind"] == "plan"}) >= 2)
    # 无效的附加引文不应否定已定位的充分证据。低风险文字修补允许单点依据。
    low_risk = str(item.get("severity") or "").lower() == "low"
    item["evidence_verified"] = (has_plan and
                                 (low_risk or (len(located) >= 2 and has_comparison)))
    item.pop("evidence_status", None)
    if item.get("evidence_state") in {"source_conflict", "uncertain"}:
        item["evidence_verified"] = False
        item["evidence_status"] = ("历史依据自身冲突，需统一来源，不自动修补" if
                                   item["evidence_state"] == "source_conflict" else
                                   "查阅后仍有疑问，不自动修补")
        return item
    if not item["evidence_verified"]:
        item["evidence_status"] = "已定位引文不足以支持修补或仅有规划参考；保留风险等级，暂不自动修补"
    return item
