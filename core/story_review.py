"""整体故事审校：主线修订与可选增强分开保存，不强制细节纠错。"""
import json
import re
import hashlib


def story_sources(previous, wiki, chapter_number, previous_chapter_number=None):
    """Split only application-owned wrappers, never headings inside fiction."""
    sources = []
    headings = list(re.finditer(r'(?m)^【((?:有效摘要|章末原文)[^】]*)】\s*\n', previous))
    blocks = [(m.group(1), previous[m.end():headings[i+1].start() if i+1<len(headings) else len(previous)].strip())
              for i,m in enumerate(headings)] if headings else [('前章提供资料', previous)]
    for label, text in blocks:
        if not text:
            continue
        number_match = re.search(r'第(\d+)章', label)
        source_chapter = int(number_match.group(1)) if number_match else previous_chapter_number
        is_summary = label.startswith('有效摘要')
        sources.append({'id':f'C{len(sources)+1:02}',
            'kind':'summary' if is_summary else 'body',
            'label':'前章资料｜'+label, 'chapter':source_chapter,
            'chapter_relation':'previous_available_chapter',
            'scope':'summary' if is_summary else 'provided_excerpt',
            'source_state':'compressed_reference' if is_summary else 'saved_body_excerpt',
            'text':text, 'hash':hashlib.sha256(text.encode()).hexdigest()})
    if wiki:
        from core.plan_evidence import evidence_sources
        sources.extend({**row,'chapter':None,'scope':'provided_records',
                        'source_state':'historical_reference'} for row in evidence_sources('',wiki,{}))
    return sources


def build_story_prompt(sources):
    # 方法和输出结构由 Skill 维护，运行器只提供本次输入。
    return ("【本次任务】\n整体审校本章故事，不改正文。按故事审校 Skill 返回结果。\n"
            "若存在‘合并人物关系状态’资料，只将其作为截至前章的局部关系参考；"
            "其中 relation_status=unverified 的记录只是待核线索，不得据此判定亲属身份或正文错误；"
            "只有 confirmed 且明确双方角色的记录可作为身份依据。"
            "当前正文出现关系变化时，区分明确冲突与必要过渡缺口，不把推断关系当事实。\n"
            "【分类证据目录】\n" + json.dumps(sources, ensure_ascii=False))


def active_scene_continuity_check(llm, sources, parse, audit=None, progress=None):
    """Small preflight for scene premises that broad editorial reading can miss."""
    body = next((row.get("text", "") for row in sources if row.get("id") == "T01"), "")
    historical = [row for row in sources if row.get("id") != "T01" and row.get("kind") in {"summary", "body"}]
    if not body or not historical:
        return []
    # This check deliberately sees much less than the broad editor. Reasoning
    # models otherwise spend their response budget re-auditing the whole plot
    # and never reach the small JSON result this preflight needs.
    current_window = body if len(body) <= 2600 else body[:1250] + "\n……【中段省略】……\n" + body[-1250:]
    summaries = [row for row in historical if row.get("kind") == "summary"][-2:]
    prior_bodies = [row for row in historical if row.get("kind") == "body"][-1:]
    selected = summaries + prior_bodies
    compact_sources = []
    for row in selected:
        text = str(row.get("text") or "")
        if row.get("kind") == "body" and len(text) > 1400:
            text = text[-1400:]
        elif len(text) > 900:
            text = text[-900:]
        compact_sources.append({
            "id": row.get("id"), "chapter": row.get("chapter"),
            "kind": row.get("kind"), "text": text,
        })
    relationship_source = next((row for row in sources if row.get("id") == "G01"), None)
    if relationship_source:
        try:
            relation_rows = json.loads(relationship_source.get("text") or "[]")
        except (TypeError, ValueError):
            relation_rows = []
        def mentioned(name):
            value = str(name or "")
            short = value.replace("・", "·").split("·", 1)[0]
            return bool(len(short) >= 2 and (value in current_window or short in current_window))
        confirmed = [row for row in relation_rows if row.get("relation_status") == "confirmed"
                     and (mentioned(row.get("subject")) or mentioned(row.get("target")))]
        if confirmed:
            compact_sources.append({"id": "G01", "kind": "relationship_state",
                                    "text": json.dumps(confirmed[:16], ensure_ascii=False)})
    prompt = """你是长篇小说的场景前提核对员，不做全面审校，也不修改正文。
只检查本章首尾实际登场人物的既有身份和处境是否能接上最近历史与已确认的人物关系。抓住会改变读者对人物认知的明显断裂，不逐项核对资料。人物可以自然变化。

区分两类结论：
1. conflict：前后状态无法同时成立。
2. continuity_gap：变化并非不可能，但现有正文与已确认资料之间缺少必要交代，普通读者会觉得人物身份或处境突然变了。

只报告会影响场景成立或人物身份的明显问题。没有提及某事不等于它不存在；资料只证明局部关系时，不要推断完整家谱。结论中的地点和身份称呼只能沿用引文的表述，不得将泛称自行具体化。不使用作品常识。最多返回2项；没有则为空。

输出 JSON：{"issues":[{"kind":"conflict或continuity_gap","severity":"high或medium","scope":"位置","problem":"冲突或缺失的必要过渡","impact":"对场景理解的影响","suggestion":"最小修改目标","citations":[{"source_id":"T01或C编号","quote":"连续准确原句"}],"evidence_state":"confirmed"}]}
跨章问题至少引用一段当前章和一段历史或已确认关系；同章冲突至少引用当前章两段互不兼容的原句。只复制输入中的原句。建议只涉及澄清已有情节或人物，不得要求新增支线。

【最近历史】
""" + json.dumps(compact_sources, ensure_ascii=False) + "\n【本章首尾窗口】\n" + current_window
    from core.model_response import complete_review
    if progress:
        progress("正在核对本章首尾场景与活跃人物的近期状态")
    if audit:
        audit.write("active_continuity_request", prompt=prompt)
    raw = complete_review(llm, prompt, audit=audit, progress=progress,
                          event_prefix="active_continuity", system="只输出 JSON。",
                          temperature=0.1, num_predict=1800, disable_thinking=True)
    if audit:
        audit.write("active_continuity_response", raw_response=raw)
    data = parse(raw)
    issues = data.get("issues", []) if isinstance(data, dict) else []
    return issues[:2] if isinstance(issues, list) else []


def review_relationship_claims(llm, sources, parse, audit=None, progress=None):
    """Read the full chapter's explicit durable relations, then compare with canon."""
    from core.model_response import complete_review

    body = next((row.get("text", "") for row in sources if row.get("id") == "T01"), "")
    reference = next((row.get("text", "") for row in sources if row.get("id") == "G01"), "")
    if not body or not reference:
        return []
    try:
        rows = json.loads(reference)
    except (TypeError, ValueError):
        return []
    confirmed = [row for row in rows if row.get("relation_status") == "confirmed"]
    if not confirmed:
        return []
    if progress:
        progress("正在提取本章明确的人物关系并核对已确认资料")
    extraction_prompt = ("阅读本章完整正文，提取原文足以支持、会影响后续续写的稳定人物关系。"
                         "同时保留原文明确给出的关系人数；证据不足时不要补写或推断。只输出 JSON："
                         '{"claims":[{"subject":"人物","relation":"关系","target":"人物或未具名对象",'
                         '"count":null,"quote":"正文连续原句"}]}。count 只在正文明确给出人数时填写整数。\n'
                         "【本章正文】\n" + body)
    raw = complete_review(llm, extraction_prompt, audit=audit, progress=progress,
                          event_prefix="relationship_extract", system="只输出 JSON。",
                          temperature=0.1, num_predict=2600, disable_thinking=True)
    parsed = parse(raw)
    claims = [row for row in parsed.get("claims", []) if isinstance(row, dict)
              and str(row.get("quote") or "").strip() in body] if isinstance(parsed, dict) else []
    if not claims:
        return []
    # A second reading of the copied source sentences corrects extraction
    # errors without embedding a fixed dictionary of relationships or numbers.
    quote_contexts = []
    seen_quotes = set()
    for row in claims:
        quote = str(row.get("quote") or "").strip()
        if quote in seen_quotes:
            continue
        seen_quotes.add(quote)
        position = body.find(quote)
        start = body.rfind("\n\n", 0, position)
        end = body.find("\n\n", position + len(quote))
        paragraph = body[(start + 2 if start >= 0 else 0):(end if end >= 0 else len(body))]
        quote_contexts.append({"quote": quote, "paragraph": paragraph[:1200]})
    quote_prompt = ("只依据以下正文原句及其所在段落，独立核读原文足以支持的稳定人物关系。"
                    "重新判断指代和关系，不沿用第一次提取的结论；证据不足的关系省略。"
                    "逐句返回人物主体、关系、对象及原文明确给出的人数；没有明确人数则 count=null。"
                    '只输出 JSON：{"claims":[{"subject":"人物","relation":"关系",'
                    '"target":"人物或未具名对象","count":null,"quote":"原句"}]}。\n'
                    "【待核读原句及所在段落】\n" + json.dumps(quote_contexts, ensure_ascii=False))
    try:
        raw = complete_review(llm, quote_prompt, audit=audit, progress=progress,
                              event_prefix="relationship_quote_read", system="只输出 JSON。",
                              temperature=0.1, num_predict=1800, disable_thinking=True)
        parsed = parse(raw)
        allowed_quotes = {str(row.get("quote") or "").strip() for row in claims}
        reread = [row for row in parsed.get("claims", []) if isinstance(row, dict)
                  and str(row.get("quote") or "").strip() in allowed_quotes] if isinstance(parsed, dict) else []
        if reread:
            claims = reread
    except Exception as exc:
        if audit:
            audit.write("relationship_quote_read_failed", error=str(exc),
                        fallback="use_first_extraction")
    claims = list({(str(row.get("subject") or ""), str(row.get("quote") or ""),
                    str(row.get("relation") or "")): row for row in claims}.values())
    if audit:
        audit.write("relationship_claims_extracted", claims=claims)
    notes = []
    comparison_prompt = ("你是小说人物关系审校员。逐项比较本章关系主张与截至前章已确认的关系。"
                         "只返回两类待核对项：unverified_count 表示正文明确的人数多于目前具名的同类关系，"
                         "需核实是否有未交代的人物；direct_conflict 表示同一对人物的身份称谓直接矛盾。"
                         "关系资料不代表完整家谱，人数差异不是已证实错误。"
                         "只能引用对应数组的下标，不要自行解释其他人的身份。"
                         '只输出 JSON：{"questions":[{"type":"unverified_count或direct_conflict",'
                         '"claim_index":0,"reference_index":0}]}。最多3项，没有则返回空数组。\n'
                         "【本章关系主张，索引从0开始】\n" + json.dumps(claims, ensure_ascii=False) +
                         "\n【已确认关系，索引从0开始】\n" + json.dumps(confirmed, ensure_ascii=False))
    try:
        raw = complete_review(llm, comparison_prompt, audit=audit, progress=progress,
                              event_prefix="relationship_compare", system="只输出 JSON。",
                              temperature=0.1, num_predict=1500, disable_thinking=True)
        parsed = parse(raw)
        questions = parsed.get("questions", []) if isinstance(parsed, dict) else []
    except Exception as exc:
        questions = []
        if audit:
            audit.write("relationship_compare_failed", error=str(exc),
                            fallback="no_comparison_notes")
    for item in questions if isinstance(questions, list) else []:
        if not isinstance(item, dict):
            continue
        ci, ri = item.get("claim_index"), item.get("reference_index")
        if not isinstance(ci, int) or not isinstance(ri, int) or not 0 <= ci < len(claims) or not 0 <= ri < len(confirmed):
            continue
        claim, known = claims[ci], confirmed[ri]
        claim_quote = str(claim.get("quote") or "").strip()
        subject = str(claim.get("subject") or "").replace("・", "·").split("·", 1)[0]
        known_names = [str(known.get(key) or "").replace("・", "·").split("·", 1)[0]
                       for key in ("subject", "target")]
        if not claim_quote or claim_quote not in body or len(subject) < 2 or subject not in known_names:
            continue
        kind = item.get("type")
        if kind == "unverified_count":
            count = claim.get("count")
            if not isinstance(count, int) or count < 2:
                continue
            summary = "正文给出了明确的关系人数，已确认资料只记录部分具名关系；请核实是否另有未交代的人物。"
        elif kind == "direct_conflict":
            target = str(claim.get("target") or "").replace("・", "·").split("·", 1)[0]
            if len(target) < 2 or target not in known_names or target == subject:
                continue
            summary = "正文对同一对人物的身份称谓与已确认关系可能不一致，请核实。"
        else:
            continue
        notes.append(f"人物关系待核对：{summary}｜本章：“{claim_quote}”｜已确认："
                     f"{known.get('subject')}—{known.get('predicate')}—{known.get('target')}")
    if audit:
        audit.write("relationship_claims_compared", questions=questions, accepted_notes=notes)
    return notes[:3]


def assemble_story_result(data, sources, audit=None):
    from core.consistency import verify_audit_issue
    if not isinstance(data, dict) or not isinstance(data.get("issues"), list):
        raise ValueError("故事审校未返回主线问题数组")
    enhancements = data.get("enhancements", [])
    if not isinstance(enhancements, list):
        raise ValueError("故事审校情节增强格式无效")
    instructions, details, notes, accepted_enhancements, skipped = [], [], [], [], []
    issues = []
    for candidate in data["issues"][:2]:
        if not isinstance(candidate, dict):
            continue
        # 定位只能核验引文，重大影响是否成立仍是模型的判断，不伪称程序证明。
        if (candidate.get("kind") not in {"conflict", "continuity_gap"}
                or candidate.get("severity") not in {"high", "medium"}
                or not str(candidate.get("impact") or "").strip()):
            skipped.append({"item": candidate, "reason": "不属于有具体故事影响的重大主线问题"})
            continue
        item = verify_audit_issue(candidate, sources)
        issues.append(item)
        if any(c.get("source_kind") == "relationship_state" for c in item.get("citations", [])):
            # The merged graph is intentionally partial. A source quote may be
            # located while the model's interpretation of its kinship is wrong.
            # Leave relationship conclusions to the grounded pair comparison.
            continue
        if item["evidence_verified"]:
            if item["suggestion"] not in instructions:
                instructions.append(item["suggestion"])
                quotes = "；".join(f"[{c['source_id']}]“{c['quote']}”" for c in item["citations"])
                details.append(f"{len(details) + 1}. {item['problem']}\n故事影响：{item['impact']}\n依据：{quotes}")
        elif item.get("problem"):
            # A citation can be real while the model's explanation adds an
            # unsupported proper name or identity. Show located source text
            # instead of repeating that unverified explanation.
            grounded = [f"[{cite['source_id']}]“{cite['quote']}”" for cite in item.get("citations", [])
                        if cite.get("verified")]
            current_body = next((source.get("text", "") for source in sources
                                 if source.get("id") == "T01"), "")
            current_quotes = [f"[本章原文]“{cite['quote']}”" for cite in item.get("citations", [])
                              if not cite.get("verified") and cite.get("quote") and
                              cite["quote"] in current_body]
            notes.append("待核对：" + ("；".join(grounded) if grounded else "审校结论的引文未能定位") +
                         ("；" + "；".join(current_quotes) if current_quotes else "") +
                         "。请对照本章原文核实衔接；该结论未通过证据校验，不自动修订。")
    body = next((row["text"] for row in sources if row["id"] == "T01"), "")
    for item in enhancements[:2]:
        if not isinstance(item, dict):
            skipped.append({"item": item, "reason": "增强意见格式无效"})
            continue
        scope = str(item.get("scope") or "").strip()
        suggestion = str(item.get("suggestion") or "").strip()
        preserve = str(item.get("preserve") or "").strip()
        cites = item.get("citations")
        located = (isinstance(cites, list) and bool(cites) and all(
            isinstance(c, dict) and c.get("source_id") == "T01" and
            isinstance(c.get("quote"), str) and c["quote"].strip() and c["quote"] in body for c in cites))
        if not (scope and suggestion and len(suggestion) <= 200 and preserve and len(preserve) <= 200 and located):
            skipped.append({"item": item, "reason": "增强意见缺少定位、改法、保留项或原文引文"})
            continue
        if any(row["suggestion"] == suggestion for row in accepted_enhancements):
            continue
        accepted_enhancements.append({"id": f"E{len(accepted_enhancements) + 1:02}",
            "scope": scope, "suggestion": suggestion, "preserve": preserve,
            "citations": cites, "optional": True})
    uncertainties = data.get("uncertainties", [])
    if isinstance(uncertainties, list):
        for row in uncertainties[:3]:
            note = str(row.get("problem") or "") if isinstance(row, dict) else str(row)
            if note.strip():
                notes.append(note.strip())
    summary = str(data.get("decision_summary") or "").strip()
    report_parts = ["【故事审校】\n" + (summary or "本轮审校已完成。"),
                    "【主线依据】\n" + ("\n\n".join(details) or "未发现有充分依据的重大主线问题；不代表全文没有细节错误。")]
    output = {
        "audit_kind": "story", "audit_version": 4,
        "story_summary": summary,
        "story_details": "\n\n".join(details),
        "report": "\n\n".join(report_parts),
        "revision_requirements": "\n".join(f"{i}. {x}" for i, x in enumerate(instructions, 1)),
        "story_enhancements": accepted_enhancements,
        "audit_notes": list(dict.fromkeys(notes)),
    }
    if audit:
        audit.write("audit_candidates_verified", issues=issues)
        audit.write("story_enhancements_verified", enhancements=accepted_enhancements, skipped=skipped,
                    creative_proposals_not_canon=True)
        audit.write("audit_structured_result", **output)
    return output


def check_story(chapter_text, settings, retrieved_context, llm_config, previous_context="",
                wiki_history="", progress=None, audit=None, project_dir=None, chapter_number=None,
                previous_chapter_number=None):
    from core.continuation import _first_json_object
    from core.consistency import ContinuationLLM
    from core.planning_research import research_review
    sources = story_sources(previous_context, wiki_history, chapter_number, previous_chapter_number)
    if project_dir:
        from core.story_state import review_relationship_context
        relation_rows = review_relationship_context(project_dir, chapter_number, chapter_text, limit=32)
        if relation_rows:
            sources.append({"id": "G01", "kind": "relationship_state",
                            "label": "截至前章、与本章人物相关的合并关系状态",
                            "chapter": previous_chapter_number,
                            "scope": "identity_resolved_relevant_relations",
                            "source_state": "mixed_confirmed_and_unverified_relationship_evidence",
                            "text": json.dumps(relation_rows, ensure_ascii=False)})
    if retrieved_context:
        sources.append({"id": "R01", "kind": "retrieval", "label": "补充检索，历史时段需核对", "text": retrieved_context})
    if settings.get("background"):
        sources.append({"id": "B01", "kind": "background", "label": "作者背景，不冻结后续状态", "text": settings["background"]})
    sources.append({"id": "T01", "kind": "current_body", "label": "本章完整正文",
                    'chapter':chapter_number, 'scope':'full_body', 'offset':0,
                    'total_chars':len(chapter_text),'source_state':'body_under_review',
                    'hash':hashlib.sha256(chapter_text.encode()).hexdigest(), "text": chapter_text})
    prompt = build_story_prompt(sources)
    if progress:
        progress("正在使用故事审校 Skill 整体阅读，评估主线与情节增强")
    if audit:
        audit.write("audit_request", stage="story_audit", prompt=prompt, prompt_chars=len(prompt))
        audit.write("audit_thinking_policy", final_review="disabled", workflow="story_editor",
                    claim_extraction=False, automatic_pair_judgment=False)
    try:
        model = ContinuationLLM(llm_config)
        try:
            relation_notes = review_relationship_claims(
                model, sources, _first_json_object, audit=audit, progress=progress)
        except Exception as relation_exc:
            relation_notes = []
            if audit:
                audit.write("relationship_claim_review_failed", error=str(relation_exc),
                            fallback="continue_story_editor")
        try:
            preflight_issues = active_scene_continuity_check(
                model, sources, _first_json_object, audit=audit, progress=progress)
        except Exception as preflight_exc:
            # This is a narrow safety net, not the primary review. A malformed
            # auxiliary response must not discard a valid whole-chapter audit.
            preflight_issues = []
            if audit:
                audit.write("active_continuity_failed", error=str(preflight_exc),
                            fallback="continue_story_editor")
        data = research_review(model, prompt, sources, project_dir,
            int(chapter_number or 0), _first_json_object, audit=audit, progress=progress,
            num_predict=4200, skill_name="story-audit")
        main_issues = data.get("issues", []) if isinstance(data.get("issues"), list) else []
        # Scene-premise conflicts are evaluated first because the broad editor
        # may otherwise spend its limited issue budget on the dominant plot.
        data["issues"] = (preflight_issues + main_issues)[:2]
        data["uncertainties"] = [*relation_notes,
                                 *(data.get("uncertainties") if isinstance(data.get("uncertainties"), list) else [])]
        if audit:
            audit.write("active_continuity_candidates", issues=preflight_issues,
                        merged_issue_count=len(data["issues"]))
        if audit:
            audit.write("audit_evidence_sources", sources=sources)
            checks = []
            for row in data.get("internal_checks", []) if isinstance(data.get("internal_checks"), list) else []:
                if isinstance(row, dict):
                    quotes = row.get("quotes", [])
                    checks.append({**row, "quotes_located": isinstance(quotes, list) and len(quotes) >= 2 and all(
                        isinstance(q, str) and q.strip() and q in chapter_text for q in quotes)})
            audit.write("audit_internal_coverage", checks=checks,
                        located_pairs=sum(row["quotes_located"] for row in checks),
                        coverage_note="整体故事审校，不要求穷举或核对所有细节")
        return assemble_story_result(data, sources, audit)
    except Exception as exc:
        if audit:
            audit.write("audit_failed", stage="story_audit", error=str(exc))
        raise ValueError(f"故事审校未完成：{exc}") from exc
