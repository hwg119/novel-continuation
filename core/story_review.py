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
    return "【本次任务】\n整体审校本章故事，不改正文。按故事审校 Skill 返回结果。\n【分类证据目录】\n" + json.dumps(sources, ensure_ascii=False)


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
    prompt = """你是长篇小说的场景前提核对员，不做全面审校，也不修改正文。
只检查本章开场与结尾实际登场人物的场景是否能接上最近历史。重点是：人物所在地点或生活体系、明确行动限制、当天时间安排，以及寄宿/停职/拘留/受伤等持续状态。人物可以自然变化。

区分两类结论：
1. conflict：前后状态无法同时成立。
2. continuity_gap：变化并非不可能，但关键人物跨越了学校、家庭、工作场所或受限状态，正文没有交代离开、获准、接送或时间过渡，普通读者会直接觉得场景跳转。

只有过渡会影响读者理解“人物为何能在这里”时才报告。普通出门、上下班、同城移动及不影响场景成立的留白不报告。

同时检查开场或结尾自身是否出现直接不兼容的时间/行动表述。不要核对所有人物、物件和数字，不使用作品常识，不把未提及当冲突。最多返回2项；没有则为空。

输出 JSON：{"issues":[{"kind":"conflict或continuity_gap","severity":"high或medium","scope":"位置","problem":"冲突或缺失的必要过渡","impact":"对场景理解的影响","suggestion":"最小修改目标","citations":[{"source_id":"T01或C编号","quote":"连续准确原句"}],"evidence_state":"confirmed"}]}
跨章问题至少引用一段当前章和一段历史；同章冲突至少引用当前章两段互不兼容的原句。只复制输入中的原句。continuity_gap 只能建议补一句过渡、澄清地点或移除无必要登场，不得要求新增支线。

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
        if item["evidence_verified"]:
            if item["suggestion"] not in instructions:
                instructions.append(item["suggestion"])
                quotes = "；".join(f"[{c['source_id']}]“{c['quote']}”" for c in item["citations"])
                details.append(f"{len(details) + 1}. {item['problem']}\n故事影响：{item['impact']}\n依据：{quotes}")
        elif item.get("problem"):
            notes.append(f"待核对：{item['problem']}（{item['evidence_status']}）")
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
