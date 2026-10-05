"""按稳定段落编号生成补丁；未选中的文字与段间空白保持原样。"""
import json
import re


def paragraph_spans(text):
    return [match.span() for match in re.finditer(r"[^\r\n]+", text) if match.group().strip()]


def numbered_paragraphs(text):
    return [{"id": i + 1, "text": text[a:b]} for i, (a, b) in enumerate(paragraph_spans(text))]


def array_call(model, prompt, audit, stage, temperature=0,
               system="你是局部修订编辑，只输出 JSON 数组。"):
    audit.write("patch_request", stage=stage, prompt=prompt)
    raw = model.complete(prompt, system=system,
                         temperature=temperature, num_predict=3500, disable_thinking=True)
    audit.write("patch_response", stage=stage, raw_response=raw)
    match = re.search(r"\[[\s\S]*\]", raw or "")
    if not match:
        raise ValueError("局部修订未返回数组")
    try:
        result = json.loads(match.group())
    except json.JSONDecodeError:
        from json_repair import repair_json
        result = json.loads(repair_json(match.group()))
    if not isinstance(result, list):
        raise ValueError("局部修订结果不是数组")
    return result


def plan_groups(model, text, requirements, audit):
    rows = numbered_paragraphs(text)
    prompt = """将修改要求整理为最多4个关联问题组，同一对象的跨段问题放在一组。
只输出数组，每项含 instruction（简短说明怎么改，不复述分析理由）、paragraph_ids（全部需要一起核对的段落编号数组）。覆盖明确要求；报告里的证据不足与可选提醒不强制修改。不要将猜测当事实。无法确定事实时要求澄清表达，不编造答案。
不得凭背景或书外知识新增问题。仅需局部修改，不把无关段落选入。如果没有可修改的明确要求，输出[]。
【修改要求】\n""" + requirements + "\n【编号原稿】\n" + json.dumps(rows, ensure_ascii=False)
    groups = array_call(model, prompt, audit, "patch_groups")
    if len(groups) > 4:
        raise ValueError("局部问题组超过4组")
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("instruction"), str) or not group["instruction"].strip():
            raise ValueError("问题组缺少修改指令")
        ids = group.get("paragraph_ids")
        if not isinstance(ids, list) or not ids or any(type(i) is not int or i < 1 or i > len(rows) for i in ids):
            raise ValueError("问题组段落编号无效")
        group["paragraph_ids"] = sorted(set(ids))
    audit.write("patch_groups_validated", groups=groups)
    return groups


def apply_patches(text, patches, allowed_ids):
    spans = paragraph_spans(text)
    updates = {}
    for patch in patches:
        if not isinstance(patch, dict):
            raise ValueError("补丁格式错误")
        i, before, after = patch.get("id"), patch.get("before"), patch.get("after")
        if type(i) is not int or i not in allowed_ids or i in updates:
            raise ValueError("补丁越界或段落重复")
        a, b = spans[i - 1]
        if before != text[a:b]:
            raise ValueError("补丁原文与当前段落不一致，未应用")
        if not isinstance(after, str) or not after.strip() or '\n' in after or '\r' in after:
            raise ValueError("补丁不得删除段落或改变段落编号")
        updates[i] = after
    result = text
    for i in sorted(updates, reverse=True):
        a, b = spans[i - 1]
        result = result[:a] + updates[i] + result[b:]
    return result


def generate_group_patch(model, text, group, audit, stage, issues):
    rows = numbered_paragraphs(text)
    ids = group["paragraph_ids"]
    nearby = {j for i in ids for j in (i - 1, i, i + 1) if 1 <= j <= len(rows)}
    prompt = """仅修改允许的段落，解决本组问题，不改其他事实、人物或剧情。相邻段落仅为只读上下文。
输出补丁数组，每项含 id、before（逐字复制当前完整段落）、after（替换后完整段落，不含换行）。无须修改的段落不返回。不得删除、增添或合并段落。
保留未涉及的关键线索。只做有证据的必要更正，无法确定时不编造新的事实。
""" + "\n【简短指令】\n" + group["instruction"] + "\n【允许修改编号】\n" + json.dumps(ids)
    prompt += "\n【上次未通过】\n" + json.dumps(issues, ensure_ascii=False)
    prompt += "\n【当前相关段落】\n" + json.dumps([r for r in rows if r["id"] in nearby], ensure_ascii=False)
    patches = array_call(model, prompt, audit, stage)
    result = apply_patches(text, patches, ids)
    audit.write("patch_applied", stage=stage, patches=patches)
    return result


def generate_local_patch(model, text, requirements, audit, settings=None):
    from core.agent_skill import load_agent_skill
    guidance, skill = load_agent_skill("chapter-revision", "local")
    audit.write("skill_loaded", stage="revision_generation", **skill)
    rows = numbered_paragraphs(text)
    prompt = guidance + "\n【本次修订要求｜仅执行这些要求】\n" + requirements + "\n【完整编号正文】\n" + json.dumps(rows, ensure_ascii=False)
    settings = settings or {}
    prompt += "\n【工程设定与章节要求】\n" + json.dumps({key: settings.get(key, "") for key in (
        "background", "extra_requirements", "chapter_requirements")}, ensure_ascii=False)
    patches = array_call(model, prompt, audit, "local_patch", temperature=0.3)
    candidate = apply_patches(text, patches, [row["id"] for row in rows])
    audit.write("patch_applied", stage="local_patch", patches=patches)
    return candidate
