# -*- coding: utf-8 -*-
"""Wiki 高风险事实的局部证据复核，不让复核模型重读整章。"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from core.auto_wiki import (_extract_json, _ground_quote, _is_address_fact,
                            classify_fact)
from core.project_manager import chapter_path
from core.utils import read_file


def _review_path(project_dir: str) -> Path:
    path = Path(project_dir) / "wiki" / "reviews.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _fact_id(fact: dict) -> str:
    source = "\x1f".join(str(fact.get(key) or "") for key in
                         ("chapter", "offset", "subject", "predicate", "value", "quote"))
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:20]


def _risk(fact: dict) -> tuple[int, list[str]]:
    score, reasons = 0, []
    kind = str(fact.get("kind") or "")
    text = f"{fact.get('predicate', '')} {fact.get('value', '')}"
    if kind == "relationship":
        score += 3; reasons.append("人物关系或称呼方向")
    if kind == "stable":
        score += 3; reasons.append("将长期参与续写的稳定设定")
    if any(word in text for word in ("死亡", "死去", "失踪", "结婚", "离开", "失去", "销毁",
                                     "损毁", "继承", "身份", "death", "dead", "destroyed",
                                     "missing", "married", "left")):
        score += 3; reasons.append("重要状态变化")
    quote = str(fact.get("quote") or "")
    if len(quote) > 80:
        score += 1; reasons.append("证据引句较长")
    if str(fact.get("subject") or "").casefold() not in quote.casefold():
        score += 2; reasons.append("主体未直接出现在引句中")
    target = str(fact.get("target") or "")
    if kind == "relationship" and target and target.casefold() not in quote.casefold():
        score += 2; reasons.append("关系对象未直接出现在引句中")
    if any(word in text for word in ("可能", "似乎", "据说", "大概", "perhaps", "seems", "apparently")):
        score += 2; reasons.append("事实含不确定表达")
    return score, reasons


def _load_reviews(project_dir: str) -> dict:
    path = _review_path(project_dir)
    if not path.is_file():
        return {"version": 1, "items": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and isinstance(data.get("items"), dict) else {"version": 1, "items": {}}
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "items": {}}


def _chapter_caches(project_dir: str):
    for path in sorted((Path(project_dir) / "wiki" / "chapters").glob("chapter_*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        yield path, data


def review_status(project_dir: str, limit: int = 30) -> dict:
    reviews = _load_reviews(project_dir).get("items", {})
    counts = {"pending": 0, "kept": 0, "corrected": 0, "rejected": 0, "uncertain": 0}
    pending = []
    recent = []
    for _, cache in _chapter_caches(project_dir):
        for fact in cache.get("facts", []):
            score, reasons = _risk(fact)
            if score < 4:
                continue
            fact_id = _fact_id(fact)
            embedded_status = str(fact.get("review_status") or "")
            review = reviews.get(fact_id)
            if embedded_status in counts:
                counts[embedded_status] += 1
                continue
            if not review:
                counts["pending"] += 1
                pending.append({"id": fact_id, "chapter": fact.get("chapter"), "subject": fact.get("subject"),
                                "predicate": fact.get("predicate"), "value": fact.get("value"),
                                "risk_score": score, "risk_reasons": reasons})
            else:
                status = review.get("status", "uncertain")
                counts[status] = counts.get(status, 0) + 1
                recent.append(review)
    recent.sort(key=lambda item: item.get("reviewed_at", ""), reverse=True)
    return {"counts": counts, "pending": pending[:limit], "recent": recent[:limit],
            "total_high_risk": sum(counts.values())}


def _candidate_batches(project_dir: str, only_pending: bool = True, batch_size: int = 8):
    reviews = _load_reviews(project_dir).get("items", {})
    candidates = []
    for cache_path, cache in _chapter_caches(project_dir):
        number = int(cache.get("chapter") or 0)
        text = read_file(chapter_path(project_dir, number))
        for fact in cache.get("facts", []):
            score, reasons = _risk(fact)
            fact_id = _fact_id(fact)
            if score < 4 or (only_pending and (fact_id in reviews or fact.get("review_status"))):
                continue
            offset = max(0, int(fact.get("offset") or 0))
            quote = str(fact.get("quote") or "")
            start, end = max(0, offset - 350), min(len(text), offset + len(quote) + 350)
            candidates.append({"id": fact_id, "fact": fact, "risk_score": score,
                               "risk_reasons": reasons, "evidence": text[start:end],
                               "cache_path": cache_path})
    for index in range(0, len(candidates), batch_size):
        yield candidates[index:index + batch_size]


def _save_reviews(project_dir: str, data: dict) -> None:
    target = _review_path(project_dir)
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(target)


def review_high_risk_facts(project_dir: str, llm, progress=None, audit=None,
                           only_pending: bool = True) -> dict:
    batches = list(_candidate_batches(project_dir, only_pending=only_pending))
    reviews = _load_reviews(project_dir)
    totals = {"kept": 0, "corrected": 0, "rejected": 0, "uncertain": 0}
    processed = 0
    total_items = sum(len(item) for item in batches)
    failed_batches = []
    for batch_index, batch in enumerate(batches, 1):
        if progress:
            progress(f"正在复核第 {batch_index}/{len(batches)} 组高风险事实",
                     {"done": processed, "total": total_items,
                      "failed_batches": len(failed_batches)})
        payload = [{"id": item["id"], "fact": item["fact"],
                    "risk_reasons": item["risk_reasons"], "evidence": item["evidence"]}
                   for item in batch]
        prompt = """你是小说 Wiki 事实复核员。每项只依据它自己的 evidence 局部原文复核，不使用书外知识。
判断 fact 的主体、关系方向、事实值和 kind 是否得到原文直接支持。
返回 JSON 数组，每项必须包含 id、status、reason、corrected。
status 只能是 kept、corrected、rejected、uncertain：完全正确用 kept；可从局部原文明确修正用 corrected；与原文冲突用 rejected；上下文仍不足用 uncertain。
corrected 在 corrected 时填写完整事实对象（subject、predicate、value、quote、kind、target、story_time、stable_category），其它状态填空对象。
corrected.quote 必须逐字复制 evidence 中连续出现的不超过120字原文，关系事实必须有 target。只输出 JSON。

待复核项目：
""" + json.dumps(payload, ensure_ascii=False)
        response = None
        last_error = None
        expected_ids = [item["id"] for item in batch]
        for attempt in (1, 2):
            request = prompt if attempt == 1 else (
                f"上次输出无效。必须恰好返回 {len(batch)} 项，每个下列 id 只出现一次，"
                "不得重复、不得遗漏、不得解释：\n" + json.dumps(expected_ids, ensure_ascii=False) +
                "\n\n" + prompt)
            if audit:
                audit.write("review_request", batch=batch_index, attempt=attempt,
                            items=len(batch), prompt=request)
            try:
                raw = llm.complete(
                    request, system="只输出一个可解析的 JSON 数组；每个输入 id 恰好返回一次。",
                    temperature=0.1 if attempt == 1 else 0.0,
                    num_predict=3200, disable_thinking=True)
                if audit:
                    audit.write("review_response", batch=batch_index, attempt=attempt,
                                raw_response=raw)
                parsed = _extract_json(raw)
                returned_ids = [str(item.get("id") or "") for item in parsed
                                if isinstance(item, dict)]
                if (len(returned_ids) != len(expected_ids) or
                        len(set(returned_ids)) != len(returned_ids) or
                        set(returned_ids) != set(expected_ids)):
                    raise ValueError(
                        f"复核结果ID不完整：期望{len(expected_ids)}项，实际{len(returned_ids)}项")
                response = parsed
                break
            except Exception as exc:
                last_error = exc
                if audit:
                    audit.write("review_retry" if attempt == 1 else "review_failed",
                                batch=batch_index, attempt=attempt, error=str(exc))
        if response is None:
            failed_batches.append({"batch": batch_index, "items": len(batch),
                                   "ids": expected_ids, "error": str(last_error or "未知错误")})
            if progress:
                progress(f"第 {batch_index} 组返回无效，已保留为待复核并继续",
                         {"done": processed, "total": total_items,
                          "failed_batches": len(failed_batches)})
            continue
        by_id = {str(item.get("id") or ""): item for item in response if isinstance(item, dict)}
        caches = {}
        for candidate in batch:
            fact_id = candidate["id"]
            answer = by_id.get(fact_id, {})
            status = str(answer.get("status") or "uncertain").lower()
            if status not in totals:
                status = "uncertain"
            corrected = answer.get("corrected") if isinstance(answer.get("corrected"), dict) else {}
            reason = str(answer.get("reason") or "模型未给出明确理由")[:500]
            original = candidate["fact"]
            applied = None
            if status == "corrected":
                fields = {key: str(corrected.get(key) or "").strip() for key in
                          ("subject", "predicate", "value", "quote", "kind", "target", "story_time", "stable_category")}
                chapter_text = read_file(chapter_path(project_dir, int(original.get("chapter") or 0)))
                grounded = _ground_quote(chapter_text, fields["quote"])
                if (not all(fields[key] for key in ("subject", "predicate", "value", "quote")) or
                        grounded is None or ((fields["kind"] == "relationship" or _is_address_fact(fields)) and not fields["target"])):
                    status, reason = "uncertain", "模型给出的修正未通过本地原文校验"
                else:
                    fields["quote"], fields["offset"] = grounded
                    fields["kind"] = classify_fact(fields)
                    fields["chapter"] = original.get("chapter")
                    fields["source_hash"] = original.get("source_hash", "")
                    applied = fields
            cache_path = candidate["cache_path"]
            if cache_path not in caches:
                caches[cache_path] = json.loads(cache_path.read_text(encoding="utf-8"))
            facts = caches[cache_path].get("facts", [])
            for index, fact in enumerate(facts):
                if _fact_id(fact) != fact_id:
                    continue
                if status == "corrected" and applied:
                    applied["review_status"] = "corrected"
                    facts[index] = applied
                else:
                    fact["review_status"] = status
                    fact["review_reason"] = reason
                break
            record = {"id": fact_id, "status": status, "chapter": original.get("chapter"),
                      "subject": original.get("subject"), "predicate": original.get("predicate"),
                      "value": original.get("value"), "quote": original.get("quote"),
                      "risk_score": candidate["risk_score"], "risk_reasons": candidate["risk_reasons"],
                      "reason": reason, "corrected": applied or {},
                      "reviewed_at": datetime.now(timezone.utc).isoformat()}
            reviews["items"][fact_id] = record
            totals[status] += 1
            processed += 1
        for cache_path, cache in caches.items():
            temp = cache_path.with_suffix(".tmp")
            temp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(cache_path)
        _save_reviews(project_dir, reviews)
    return {**totals, "processed": processed,
            "failed_batches": failed_batches, "failed_items": sum(item["items"] for item in failed_batches),
            "remaining": review_status(project_dir)["counts"]["pending"]}
