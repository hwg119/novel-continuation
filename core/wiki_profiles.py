# -*- coding: utf-8 -*-
"""从逐章原文事实自动生成有证据编号的人物档案。"""
import hashlib
import json
import re
from pathlib import Path

from core.auto_wiki import _valid_facts, canonical_wiki_subject
from core.project_manager import chapter_path
from core.utils import read_file

PROFILE_VERSION = 1
VERIFICATION_VERSION = 2
SECTIONS = ("身份与称谓", "人物关系", "重要经历", "状态变化", "未解线索")
VERDICTS = {"supported", "unsupported", "uncertain"}


def _evidence(project_dir: str, subject: str, before_chapter: int | None) -> list[dict]:
    facts = [fact for fact in _valid_facts(project_dir, before_chapter)
             if fact.get("subject") == subject or
             (fact.get("kind") == "relationship" and fact.get("target") == subject)]
    chapters = {}
    result = []
    for index, fact in enumerate(facts, 1):
        number = fact["chapter"]
        if number not in chapters:
            chapters[number] = read_file(chapter_path(project_dir, number))
        source = chapters[number]
        quote = str(fact.get("quote") or "")
        offset = int(fact.get("offset") or 0)
        if not quote or source[offset:offset + len(quote)] != quote:
            offset = source.find(quote)
        if offset < 0:
            continue
        result.append({"id": f"F{index}", "chapter": number,
                       "source_hash": fact.get("source_hash"), "kind": fact.get("kind"),
                       "subject": fact.get("subject"), "target": fact.get("target"),
                       "predicate": fact.get("predicate"), "value": fact.get("value"),
                       "quote": quote, "context": source[max(0, offset - 180):offset + len(quote) + 180]})
    return result


def _signature(evidence: list[dict], before_chapter: int | None) -> str:
    payload = [{key: row.get(key) for key in ("chapter", "source_hash", "subject", "target", "predicate", "value", "quote")}
               for row in evidence]
    return hashlib.sha256(json.dumps([PROFILE_VERSION, before_chapter, payload],
                                     ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _path(project_dir: str, subject: str, before_chapter: int | None) -> Path:
    # 文件名不采用用户输入，避免实体名被解释为路径。
    key = hashlib.sha256(f"{subject}\0{before_chapter}".encode("utf-8")).hexdigest()[:24]
    return Path(project_dir) / "wiki" / "profiles" / f"{key}.json"


def read_profile(project_dir: str, subject: str, before_chapter: int | None = None) -> dict | None:
    subject = canonical_wiki_subject(project_dir, subject)
    evidence = _evidence(project_dir, subject, before_chapter)
    if not evidence:
        return None
    path = _path(project_dir, subject, before_chapter)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (data.get("subject") != subject or data.get("before_chapter") != before_chapter or
            data.get("signature") != _signature(evidence, before_chapter)):
        return None
    if 0 < data.get("verification_version", 0) < VERIFICATION_VERSION:
        for claim in data.get("claims", []):
            claim["status"] = "unverified"
            claim["review_note"] = "旧版复核结果需重新复核"
            claim.pop("atomic_checks", None)
        data["verification_version"] = 0
        data.pop("verification_counts", None)
        data["note"] = "档案仍可查看；旧版复核结果不再作为可信结论，请重新复核。"
    return data


def _parse_claims(raw: str, allowed: set[str]) -> list[dict]:
    clean = re.sub(r"<think(?:ing)?>.*?</think(?:ing)?>", "", raw or "",
                   flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r"```(?:json)?", "", clean).strip()
    match = re.search(r"\[.*\]", clean, flags=re.DOTALL)
    if not match:
        raise ValueError("模型未返回人物档案 JSON 数组")
    data = json.loads(match.group(0))
    if not isinstance(data, list):
        raise ValueError("人物档案结果不是数组")
    result = []
    for item in data:
        if not isinstance(item, dict):
            continue
        ids = item.get("fact_ids")
        section = str(item.get("section") or "")
        content = str(item.get("text") or "").strip()
        if (section not in SECTIONS or not content or len(content) > 180 or
                not isinstance(ids, list) or not ids or len(ids) > 4 or
                any(not isinstance(i, str) or i not in allowed for i in ids)):
            continue
        result.append({"section": section, "text": content,
                       "fact_ids": list(dict.fromkeys(ids))})
    return result


def _repair_text_quotes(raw: str) -> str:
    """仅修复模型在 text 字段中常见的未转义引号，不触碰其它字段。"""
    pattern = re.compile(r'("text"\s*:\s*")(.*?)("\s*,\s*"fact_ids"\s*:)', re.DOTALL)
    def fix(match):
        value = re.sub(r'(?<!\\)"', r'\\"', match.group(2))
        return match.group(1) + value + match.group(3)
    return pattern.sub(fix, raw)


def _explicit_chapter_mismatch(claim: dict) -> bool:
    """只拦截明确写作“第 N 章”的错误，不猜测跨章时间段。"""
    chapters = {int(source["chapter"]) for source in claim.get("sources", [])}
    mentioned = {int(number) for number in re.findall(r"第\s*(\d+)\s*章", claim.get("text", ""))}
    return bool(mentioned - chapters)


def _parse_review_array(raw: str) -> list[dict]:
    clean = re.sub(r"<think(?:ing)?>.*?</think(?:ing)?>", "", raw or "",
                   flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r"```(?:json)?", "", clean).strip()
    match = re.search(r"\[.*\]", clean, flags=re.DOTALL)
    if not match:
        raise ValueError("模型未返回复核 JSON 数组")
    items = json.loads(match.group(0))
    if not isinstance(items, list):
        raise ValueError("复核结果不是数组")
    return [item for item in items if isinstance(item, dict)]


def _parse_atoms(raw: str, allowed: set[str]) -> dict[str, dict]:
    result = {}
    for item in _parse_review_array(raw):
        atoms = item.get("atoms")
        if (item.get("id") not in allowed or not isinstance(atoms, list) or
                not atoms or len(atoms) > 8):
            continue
        cleaned = [str(atom).strip() for atom in atoms if isinstance(atom, str) and atom.strip()]
        if len(cleaned) != len(atoms) or any(len(atom) > 160 for atom in cleaned):
            continue
        result[item["id"]] = {"atoms": cleaned, "complete": item.get("complete") is True}
    return result


def _parse_verdicts(raw: str, allowed: set[str]) -> dict[str, dict]:
    result = {}
    for item in _parse_review_array(raw):
        if item.get("id") in allowed and item.get("verdict") in VERDICTS:
            result[item["id"]] = {"verdict": item["verdict"],
                                   "source_id": str(item.get("source_id") or "")}
    return result


def _expanded_context(project_dir: str, evidence: dict, radius: int = 800) -> str:
    source = read_file(chapter_path(project_dir, evidence["chapter"]))
    quote = evidence["quote"]
    offset = int(evidence.get("offset") or 0)
    if source[offset:offset + len(quote)] != quote:
        offset = source.find(quote)
    if offset < 0:
        return ""
    return source[max(0, offset - radius):offset + len(quote) + radius]


def _parse_expanded_verdicts(raw: str, allowed: set[str]) -> dict[str, dict]:
    result = {}
    for item in _parse_review_array(raw):
        if item.get("id") in allowed and item.get("verdict") in VERDICTS:
            result[item["id"]] = {
                "verdict": item["verdict"],
                "source_id": str(item.get("source_id") or ""),
                "support_quote": str(item.get("support_quote") or "").strip(),
            }
    return result


def expand_uncertain_profile(project_dir: str, subject: str, llm,
                             before_chapter: int | None = None, progress=None,
                             audit=None) -> dict:
    """仅对待核实要点扩展原文窗口；没有精确引句不得升级为支持。"""
    subject = canonical_wiki_subject(project_dir, subject)
    profile = read_profile(project_dir, subject, before_chapter)
    if not profile or profile.get("verification_version") != VERIFICATION_VERSION:
        raise ValueError("请先完成人物档案的逐项复核")
    facts = {row["id"]: row for row in _evidence(project_dir, subject, before_chapter)}
    names = {str(fact.get(field) or "").strip() for fact in _valid_facts(project_dir, before_chapter)
             for field in ("subject", "target")}
    names = {name for name in names if name != subject and re.fullmatch(r"[\u4e00-\u9fff]{2,4}", name)}
    pending = []
    for claim_index, claim in enumerate(profile["claims"], 1):
        if claim.get("status") != "uncertain":
            continue
        for atom_index, atom in enumerate(claim.get("atomic_checks") or [], 1):
            if atom.get("status") == "uncertain":
                pending.append((f"E{claim_index}A{atom_index}", claim, atom))
    groups = [pending[i:i + 4] for i in range(0, len(pending), 4)]
    for batch_index, group in enumerate(groups, 1):
        if progress:
            progress(f"人物档案：补证待核实要点 {batch_index}/{len(groups)}")
        questions = []
        source_windows = {}
        for atom_id, claim, atom in group:
            sources = []
            for fact_id in claim.get("fact_ids", []):
                fact = facts.get(fact_id)
                if not fact:
                    continue
                context = _expanded_context(project_dir, fact)
                if not context:
                    continue
                sources.append({"id": fact_id, "chapter": fact["chapter"], "context": context})
                source_windows[(atom_id, fact_id)] = context
            questions.append({"id": atom_id, "atom": atom["text"], "sources": sources})
        prompt = (
            "只依据下列较完整的小说原文窗口，重新核验每个要点。不得使用书外知识。"
            "supported 仅当某一窗口直接证明该要点所有具体人名、关系方向、动作和时间；"
            "代词所指仍不明确、没有直接证据或看似相反时均判 uncertain。"
            "只输出 JSON 数组，每项为 {id,verdict,source_id,support_quote}。"
            "若 supported，source_id 必须来自该要点的 sources，support_quote 必须是该窗口中连续出现的"
            "短引句（不超过 120 字）；其余情况 source_id 和 support_quote 留空。"
            "必须覆盖所有 id。\n"
            f"待核验：{json.dumps(questions, ensure_ascii=False)}")
        if audit:
            audit.write("profile_expand_request", subject=subject, batch=batch_index,
                        batches_total=len(groups), prompt=prompt)
        try:
            raw = llm.complete(prompt, system="只输出合法 JSON 数组。", temperature=0,
                               num_predict=1600, disable_thinking=True)
        except Exception as exc:
            if audit:
                audit.write("profile_expand_failed", subject=subject, batch=batch_index,
                            error_type=type(exc).__name__, error=str(exc))
            raise
        if audit:
            audit.write("profile_expand_response", subject=subject, batch=batch_index,
                        raw_response=raw)
        try:
            verdicts = _parse_expanded_verdicts(raw, {item[0] for item in group})
        except (ValueError, json.JSONDecodeError) as exc:
            verdicts = {}
            if audit:
                audit.write("profile_expand_parse_failed", subject=subject, batch=batch_index,
                            error_type=type(exc).__name__, error=str(exc))
        for atom_id, claim, atom in group:
            answer = verdicts.get(atom_id, {})
            verdict = answer.get("verdict", "uncertain")
            source_id = answer.get("source_id", "")
            support_quote = answer.get("support_quote", "")
            context = source_windows.get((atom_id, source_id), "")
            # 扩大窗口只能补足支持证据，不能将证据不足直接升级为反证。
            if verdict == "unsupported":
                verdict = "uncertain"
            if verdict == "supported":
                missing_names = [name for name in names if name in atom["text"] and name not in context]
                if (not context or not support_quote or len(support_quote) > 120 or
                        support_quote not in context or missing_names):
                    verdict = "uncertain"
            atom["status"] = verdict
            atom["source_id"] = source_id if context else ""
            if verdict == "supported":
                atom["support_quote"] = support_quote
                atom["review_pass"] = "expanded_context"
        # 只在所有调用成功后落盘；中途失败保留先前档案。
    for claim in profile["claims"]:
        if claim.get("status") != "uncertain":
            continue
        checks = claim.get("atomic_checks") or []
        claim["status"] = (
            "unsupported" if any(item["status"] == "unsupported" for item in checks)
            else "supported" if checks and all(item["status"] == "supported" for item in checks)
            else "uncertain"
        )
        claim["review_note"] = {
            "supported": "补充原文后，全部要点均有证据",
            "unsupported": "补充原文后，至少一个要点不符",
            "uncertain": "补充原文后仍有要点证据不足",
        }[claim["status"]]
    profile["verification_counts"] = {
        status: sum(claim["status"] == status for claim in profile["claims"])
        for status in ("supported", "unsupported", "uncertain")
    }
    profile["evidence_expansion_version"] = 1
    target = _path(project_dir, subject, before_chapter)
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(target)
    return profile


def verify_profile(project_dir: str, subject: str, llm,
                   before_chapter: int | None = None, progress=None, audit=None) -> dict:
    """先拆解陈述，再逐要点核对原文；缺任何一步均不得判为支持。"""
    subject = canonical_wiki_subject(project_dir, subject)
    profile = read_profile(project_dir, subject, before_chapter)
    if not profile:
        raise ValueError("人物档案不存在或已过期，请先生成档案")
    evidence = {row["id"]: row for row in _evidence(project_dir, subject, before_chapter)}
    names = {str(fact.get(field) or "").strip() for fact in _valid_facts(project_dir, before_chapter)
             for field in ("subject", "target")}
    names = {name for name in names if name != subject and re.fullmatch(r"[\u4e00-\u9fff]{2,4}", name)}
    claims = profile["claims"]
    pending = []
    for index, claim in enumerate(claims, 1):
        claim["status"] = "unverified"
        claim["review_note"] = "尚未复核"
        claim["atomic_checks"] = []
        if _explicit_chapter_mismatch(claim):
            claim["status"] = "unsupported"
            claim["review_note"] = "正文所写章节号与所引原文章节不一致"
        else:
            pending.append((f"C{index}", claim))
    groups = [pending[i:i + 6] for i in range(0, len(pending), 6)]
    for index, group in enumerate(groups, 1):
        if progress:
            progress(f"人物档案：拆解并复核陈述 {index}/{len(groups)}")
        originals = [{"id": claim_id, "claim": claim["text"]} for claim_id, claim in group]
        split_prompt = (
            "将每条小说档案陈述拆成所有独立、可核对的最小要点。只依据陈述本身拆解，"
            "不要看原文、不要判断真假。人物身份、关系、动作、时间、地点、原因、转折和否定都必须保留；"
            "特别不能省略‘却’‘因而’前后的任一部分，也不能把两个不相关事件合并成一个要点。"
            "每条输出 1-8 个短句。只有完整覆盖原陈述时 complete 才为 true。"
            "只输出 JSON 数组，每项为 {id,complete,atoms}，atoms 是短句字符串数组，"
            "不要在字符串内使用英文双引号。\n"
            f"陈述：{json.dumps(originals, ensure_ascii=False)}")
        if audit:
            audit.write("profile_split_request", subject=subject, batch=index,
                        batches_total=len(groups), prompt=split_prompt)
        try:
            split_raw = llm.complete(split_prompt, system="只输出合法 JSON 数组。", temperature=0,
                                     num_predict=2800, disable_thinking=True)
        except Exception as exc:
            if audit:
                audit.write("profile_split_failed", subject=subject, batch=index,
                            error_type=type(exc).__name__, error=str(exc))
            raise
        if audit:
            audit.write("profile_split_response", subject=subject, batch=index,
                        raw_response=split_raw)
        try:
            decomposed = _parse_atoms(split_raw, {item[0] for item in group})
        except Exception as exc:
            decomposed = {}
            if audit:
                audit.write("profile_split_parse_failed", subject=subject, batch=index,
                            error_type=type(exc).__name__, error=str(exc))
        atom_questions = []
        atom_parents = {}
        for claim_id, claim in group:
            item = decomposed.get(claim_id)
            if not item or not item["complete"]:
                claim["status"] = "uncertain"
                claim["review_note"] = "陈述未能完整拆解为可核对要点"
                continue
            sources = [{"id": fact_id, "chapter": evidence[fact_id]["chapter"],
                        "context": evidence[fact_id]["context"]}
                       for fact_id in claim.get("fact_ids", []) if fact_id in evidence]
            if not sources:
                claim["status"] = "uncertain"
                claim["review_note"] = "原文证据已缺失"
                continue
            for atom_number, atom in enumerate(item["atoms"], 1):
                atom_id = f"{claim_id}A{atom_number}"
                atom_parents[atom_id] = (claim, atom, sources)
                atom_questions.append({"id": atom_id, "atom": atom, "sources": sources})
        if not atom_questions:
            continue
        prompt = (
            "你是独立小说原文核验员。每个 atom 独立判断，不得参考书外知识或原始整句。"
            "supported 仅当所选 source 的原文上下文直接证明该要点所有具体人名、关系方向、动作和时间；"
            "若只出现‘他’‘他们’等代词却无法从这段上下文确定所指，必须判 uncertain；"
            "若把人物说的话当成客观事实，也判 uncertain，除非要点只说‘某人声称’。"
            "unsupported 表示原文明确反驳该要点；其余证据不足一律 uncertain。"
            "只输出 JSON 数组，每项为 {id,verdict,source_id}。"
            "verdict 只能是 supported、unsupported、uncertain；"
            "supported 时 source_id 必须是实际支持它的一个来源 id，其他情况可留空。必须覆盖所有 id。\n"
            f"要点：{json.dumps(atom_questions, ensure_ascii=False)}")
        if audit:
            audit.write("profile_verify_request", subject=subject, batch=index,
                        batches_total=len(groups), prompt=prompt)
        try:
            raw = llm.complete(prompt, system="只输出合法 JSON 数组。", temperature=0,
                               num_predict=3500, disable_thinking=True)
        except Exception as exc:
            if audit:
                audit.write("profile_verify_failed", subject=subject, batch=index,
                            error_type=type(exc).__name__, error=str(exc))
            raise
        if audit:
            audit.write("profile_verify_response", subject=subject, batch=index,
                        raw_response=raw)
        try:
            verdicts = _parse_verdicts(raw, set(atom_parents))
        except Exception as exc:
            verdicts = {}
            if audit:
                audit.write("profile_verify_parse_failed", subject=subject, batch=index,
                            error_type=type(exc).__name__, error=str(exc))
        for atom_id, (claim, atom, sources) in atom_parents.items():
            result = verdicts.get(atom_id, {})
            verdict = result.get("verdict", "uncertain")
            source_id = result.get("source_id", "")
            source = next((item for item in sources if item["id"] == source_id), None)
            if verdict == "supported":
                if not source:
                    verdict = "uncertain"
                else:
                    missing_names = [name for name in names if name in atom and name not in source["context"]]
                    if missing_names:
                        verdict = "uncertain"
            claim["atomic_checks"].append({"text": atom, "status": verdict,
                                           "source_id": source_id if source else ""})
        for claim_id, claim in group:
            if claim["status"] == "uncertain":
                continue
            checks = claim["atomic_checks"]
            verdict = ("unsupported" if any(item["status"] == "unsupported" for item in checks)
                       else "supported" if checks and all(item["status"] == "supported" for item in checks)
                       else "uncertain")
            claim["status"] = verdict
            claim["review_note"] = {
                "supported": "逐项复核：每个要点均有对应原文",
                "unsupported": "逐项复核：至少一个要点与原文不符",
                "uncertain": "逐项复核：至少一个要点证据不足",
            }[verdict]
    counts = {status: sum(claim["status"] == status for claim in claims)
              for status in ("supported", "unsupported", "uncertain")}
    profile["verification_version"] = VERIFICATION_VERSION
    profile["verification_counts"] = counts
    profile["note"] = ("AI 逐项复核；‘原文支持’仅代表引用片段支持全部拆分要点，"
                       "不等同于程序已验证的当前状态。")
    path = _path(project_dir, subject, before_chapter)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return profile


def generate_profile(project_dir: str, subject: str, llm,
                     before_chapter: int | None = None, progress=None,
                     force: bool = False, audit=None) -> dict:
    """分批归纳，逐条绑定事实编号；模型不能直接改写底层事实。"""
    subject = canonical_wiki_subject(project_dir, subject)
    evidence = _evidence(project_dir, subject, before_chapter)
    if not evidence:
        raise ValueError(f"{subject} 尚无有效的逐章 Wiki 事实")
    existing = None if force else read_profile(project_dir, subject, before_chapter)
    if existing:
        return existing
    signature = _signature(evidence, before_chapter)
    checkpoint = _path(project_dir, subject, before_chapter).with_suffix(".partial.json")
    completed = {}
    if not force:
        try:
            partial = json.loads(checkpoint.read_text(encoding="utf-8"))
            if partial.get("signature") == signature and isinstance(partial.get("batches"), dict):
                completed = partial["batches"]
        except (OSError, ValueError):
            pass
    drafts = []
    batches = [evidence[i:i + 28] for i in range(0, len(evidence), 28)]
    for index, batch in enumerate(batches, 1):
        batch_key = str(index)
        if batch_key in completed:
            drafts.extend(completed[batch_key])
            if progress:
                progress(f"人物档案：复用已完成证据 {index}/{len(batches)}")
            continue
        if progress:
            progress(f"人物档案：整理证据 {index}/{len(batches)}")
        compact = [{key: row[key] for key in ("id", "chapter", "kind", "subject", "target",
                                           "predicate", "value", "context")} for row in batch]
        prompt = (f"为小说人物「{subject}」整理档案。只能依据下列原文上下文，不能使用书外知识。"
                  "事实字段可能抽取错误，须以原文上下文为准；含糊、比喻、人物猜测不要写成确定事实。"
                  "年龄、位置、伤势、持有物等只描述为‘第X章时’，不可推断至今有效。"
                  "关系须核实方向。每批最多输出 7 条有价值的简短陈述；无可靠陈述返回 []。"
                  "text 中如需引用称谓，使用中文引号「」；不要写未经转义的英文双引号。"
                  "仅输出 JSON 数组，每项为 {section,text,fact_ids}；section 只能是"
                  f"{list(SECTIONS)}；fact_ids 必须取自输入 id，最多 4 个。\n"
                  f"证据：{json.dumps(compact, ensure_ascii=False)}")
        if audit:
            audit.write("profile_batch_request", subject=subject, batch=index,
                        batches_total=len(batches), prompt=prompt)
        raw = llm.complete(prompt, system="你是谨慎的小说原文考据员，只输出 JSON。",
                           temperature=0.1, num_predict=2500, disable_thinking=True)
        if audit:
            audit.write("profile_batch_response", subject=subject, batch=index,
                        raw_response=raw)
        allowed = {row["id"] for row in batch}
        try:
            parsed = _parse_claims(raw, allowed)
        except (ValueError, json.JSONDecodeError) as exc:
            locally_repaired = _repair_text_quotes(raw)
            if locally_repaired != raw:
                try:
                    parsed = _parse_claims(locally_repaired, allowed)
                    if audit:
                        audit.write("profile_batch_local_repair", subject=subject, batch=index,
                                    error=str(exc))
                except (ValueError, json.JSONDecodeError):
                    parsed = None
            else:
                parsed = None
            if parsed is None:
                if audit:
                    audit.write("profile_batch_repair", subject=subject, batch=index,
                                error=str(exc))
                if progress:
                    progress(f"人物档案：修正第 {index} 批 JSON 格式")
                repair_prompt = (
                    "只修复下列 JSON 数组的语法错误，不增删或改写任何事实、文本和 fact_ids。"
                    "特别注意把字符串内部的英文双引号正确转义。只输出合法 JSON 数组，"
                    "不输出 Markdown 或解释。\n" + raw)
                repaired = llm.complete(repair_prompt,
                                        system="你是 JSON 格式修复器，只输出合法 JSON 数组。",
                                        temperature=0, num_predict=2500, disable_thinking=True)
                if audit:
                    audit.write("profile_batch_repaired_response", subject=subject,
                                batch=index, raw_response=repaired)
                try:
                    parsed = _parse_claims(repaired, allowed)
                except (ValueError, json.JSONDecodeError) as repair_exc:
                    raise ValueError(f"第 {index} 批人物档案 JSON 修复失败：{repair_exc}") from repair_exc
        if audit:
            audit.write("profile_batch_parsed", subject=subject, batch=index,
                        accepted=len(parsed))
        completed[batch_key] = parsed
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        temp_checkpoint = checkpoint.with_suffix(".tmp")
        temp_checkpoint.write_text(json.dumps({"signature": signature, "batches": completed},
                                              ensure_ascii=False), encoding="utf-8")
        temp_checkpoint.replace(checkpoint)
        drafts.extend(parsed)
    if not drafts:
        raise ValueError("模型未生成任何带有效证据编号的人物档案陈述")
    by_id = {row["id"]: row for row in evidence}
    claims = []
    seen = set()
    for draft in drafts:
        key = (draft["section"], draft["text"])
        if key in seen:
            continue
        seen.add(key)
        claims.append({**draft, "sources": [
            {"id": fid, "chapter": by_id[fid]["chapter"], "quote": by_id[fid]["quote"]}
            for fid in draft["fact_ids"]]})
    profile = {"schema_version": PROFILE_VERSION, "subject": subject,
               "before_chapter": before_chapter, "signature": signature,
               "evidence_count": len(evidence), "claims": claims,
               "note": "AI 归纳；引句可核对，但语义判断不保证正确。状态仅代表所引章节当时。"}
    path = _path(project_dir, subject, before_chapter)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    checkpoint.unlink(missing_ok=True)
    return profile
