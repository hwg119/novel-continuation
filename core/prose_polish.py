"""可选表达润色：复用补丁及候选稿流程，不判断作者来源、不改剧情。"""
import json

from core.agent_skill import skill_guidance
from core.revision_patches import numbered_paragraphs, array_call, apply_patches


POLISH_REQUIREMENTS = "仅润色表达，保留全部剧情事实、关键信息、线索、人物声音和事实确定程度；没有改善收益的段落不改。"


def polish_requirements(extra=""):
    extra = str(extra or "").strip()
    if extra.startswith(POLISH_REQUIREMENTS):
        return extra
    return POLISH_REQUIREMENTS + ("\n【额外表达偏好，不改变剧情】\n" + extra if extra else "")


def generate_polish(model, source, requirements, settings, audit):
    guidance = skill_guidance("prose-polish", audit=audit, stage="polish_generation")
    rows = numbered_paragraphs(source)
    prompt = guidance + "\n【本次表达要求】\n" + requirements
    prompt += "\n【作者设定】\n" + json.dumps({key: settings.get(key, "") for key in (
        "character_voices", "glossary", "forbidden_words", "extra_requirements", "chapter_requirements")}, ensure_ascii=False)
    prompt += "\n【完整编号正文】\n" + json.dumps(rows, ensure_ascii=False)
    patches = array_call(model, prompt, audit, "prose_polish", temperature=0.3,
                         system="你是小说表达润色编辑，只输出段落补丁 JSON 数组。")
    candidate = apply_patches(source, patches, [row["id"] for row in rows])
    audit.write("polish_patches_validated", patches=patches, changed_paragraphs=len(patches),
                automatic_apply=False)
    return candidate


def review_polish(model, source, candidate, requirements, audit):
    if candidate == source:
        audit.write("polish_acceptance_skipped", reason="无文字变化，不重复调用模型")
        return []
    guidance = skill_guidance("prose-polish", "acceptance", audit, "polish_acceptance")
    prompt = guidance + "\n【本次要求】\n" + requirements
    prompt += "\n【原文】\n" + source + "\n【润色候选稿】\n" + candidate
    issues = array_call(model, prompt, audit, "polish_acceptance",
                        system="你只验收表达润色的边界，只输出提醒字符串 JSON 数组，不输出补丁。")
    if any(not isinstance(item, str) or not item.strip() for item in issues):
        raise ValueError("润色验收未返回完整提醒数组")
    return issues
