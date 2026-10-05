"""整体故事审校：主线修订与可选增强分开保存，不强制细节纠错。"""
import json


def build_story_prompt(sources):
    # 方法和输出结构由 Skill 维护，运行器只提供本次输入。
    return "【本次任务】\n整体审校本章故事，不改正文。按故事审校 Skill 返回结果。\n【分类证据目录】\n" + json.dumps(sources, ensure_ascii=False)


def assemble_story_result(data, sources, audit=None):
    from core.consistency import verify_audit_issue
    if not isinstance(data, dict) or not isinstance(data.get("issues"), list):
        raise ValueError("故事审校未返回主线问题数组")
    enhancements = data.get("enhancements", [])
    if not isinstance(enhancements, list):
        raise ValueError("故事审校情节增强格式无效")
    instructions, details, notes, accepted_enhancements, skipped = [], [], [], [], []
    issues = []
    for candidate in data["issues"][:6]:
        if not isinstance(candidate, dict):
            continue
        # 定位只能核验引文，重大影响是否成立仍是模型的判断，不伪称程序证明。
        if (candidate.get("kind") != "conflict" or candidate.get("severity") not in {"high", "medium"}
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
    for item in enhancements[:3]:
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
    output = {
        "audit_kind": "story", "audit_version": 2,
        "story_summary": summary,
        "story_details": "\n\n".join(details),
        "report": "【故事审校】\n" + (summary or "本轮审校已完成。") + "\n\n【主线依据】\n" +
                  ("\n\n".join(details) or "未发现有充分依据的重大主线问题；不代表全文没有细节错误。"),
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
                wiki_history="", progress=None, audit=None, project_dir=None, chapter_number=None):
    from core.continuation import _first_json_object
    from core.consistency import ContinuationLLM
    from core.plan_evidence import evidence_sources
    from core.planning_research import research_review
    sources = evidence_sources(previous_context, wiki_history, {})
    if retrieved_context:
        sources.append({"id": "R01", "kind": "retrieval", "label": "补充检索，历史时段需核对", "text": retrieved_context})
    if settings.get("background"):
        sources.append({"id": "B01", "kind": "background", "label": "作者背景，不冻结后续状态", "text": settings["background"]})
    sources.append({"id": "T01", "kind": "current_body", "label": "本章完整正文", "text": chapter_text})
    prompt = build_story_prompt(sources)
    if progress:
        progress("正在使用故事审校 Skill 整体阅读，评估主线与情节增强")
    if audit:
        audit.write("audit_request", stage="story_audit", prompt=prompt, prompt_chars=len(prompt))
        audit.write("audit_thinking_policy", final_review="disabled", workflow="story_editor",
                    claim_extraction=False, automatic_pair_judgment=False)
    try:
        data = research_review(ContinuationLLM(llm_config), prompt, sources, project_dir,
            int(chapter_number or 0), _first_json_object, audit=audit, progress=progress,
            num_predict=4200, skill_name="story-audit")
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
