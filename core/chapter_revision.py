# -*- coding: utf-8 -*-
"""整章修订提示、可核验要求与应用前版本快照。"""
import json
import os
import re
import time
import uuid


def chapter_body(text: str, title: str) -> str:
    """兼容直接载入 chapter_N.txt 时正文框含标题的旧行为。"""
    value = (text or "").strip()
    head, sep, tail = value.partition("\n")
    if sep and head.strip() == (title or "").strip():
        return tail.strip()
    return value


def build_whole_chapter_prompt(source: str, title: str, settings: dict,
                               requirements: str, audit=None) -> str:
    from core.agent_skill import load_agent_skill
    guidance, skill = load_agent_skill("chapter-revision", "whole")
    if audit:
        audit.write("skill_loaded", stage="revision_generation", **skill)
    beats = settings.get("beats") or []
    outline = "\n".join(
        f"{i}. 第{beat.get('num', i)}幕 {beat.get('name', '')}：{beat.get('desc', '')}"
        for i, beat in enumerate(beats, 1)
    )
    return f"""{guidance}

【章节】{title}
【工程设定与章节要求】
{settings.get('background', '')}
{settings.get('extra_requirements', '')}
{settings.get('chapter_requirements', '')}

【分幕大纲】
{outline}

【本次修订要求｜仅执行这些要求】
{requirements}

【当前完整正文】
{source}
"""


def matching_sections(source: str, sections: list[dict]) -> list[dict]:
    """仅在分幕拼接确实等于当前正文时使用原有幕边界。"""
    from core.draft_review import normalize_sections
    items = normalize_sections(sections)
    combined = "\n\n".join(str(item.get("text") or "").strip() for item in items).strip()
    return items if len(items) > 1 and combined == source.strip() else []


def build_beat_revision_prompt(section: dict, index: int, sections: list[dict],
                               revised: list[str], title: str, settings: dict,
                               requirements: str, *, rebuild: bool = False) -> str:
    """一个整章任务中的内部逐幕修订；用户只需提交一次整章要求。"""
    outline = "\n".join(
        f"{i + 1}. {item.get('name', '')}：{item.get('desc', '')}"
        for i, item in enumerate(sections)
    )
    previous = (revised[-1][-700:] if revised else "（本幕是开篇）")
    next_beat = sections[index + 1] if index + 1 < len(sections) else None
    next_rule = (f"下一幕是「{next_beat.get('name', '')}」：{next_beat.get('desc', '')}。不要提前写它的事件。"
                 if next_beat else "本幕为结尾，请收束本章。")
    scope_note = ("修订要求若明确限定某一幕，只在对应幕落实；其他幕仅把它作为情节边界，"
                  "不得提前、重复或挪用事件。")
    source_text = _revision_source(section.get("text", ""), requirements, rebuild)
    method_note = ("本次是纠错重写：不要沿用旧段落的句子或事件，只根据本幕目标、前文和修订要求写足本幕。"
                   if rebuild else "正确段落可保留；已剔除的偏题段落不要补回。")
    glossary = settings.get("glossary") or {}
    fixed_names = "、".join(str(name) for name in glossary if len(str(name)) >= 2)[:450]
    return f"""你在修订第 {index + 1}/{len(sections)} 幕。只输出本幕修订后的小说正文，不写标题、说明、清单或 Markdown。
保持本幕原有篇幅、正确情节和与前文的衔接；确有违背要求的段落应重写，不能只替换几个禁词。
{method_note}

【章节】{title}
【世界与人物事实】
{settings.get('background', '')}
{settings.get('extra_requirements', '')}
{settings.get('chapter_requirements', '')}
【固定专名】
{fixed_names}

【全章分幕顺序】
{outline}

【用户一次提交的整章修订要求】
{requirements}
其中提到其他幕的情节仅用来保持边界；当前只写本幕。
{scope_note}

【前一幕修订后的末尾】
{previous}

【本幕目标】
{section.get('desc', '')}
【下幕边界】
{next_rule}

【待修订的本幕原文】
{source_text}

请核对人物、时间、地点和用户要求，然后只输出本幕完整正文。"""


def _revision_source(text: str, requirements: str, rebuild: bool) -> str:
    if rebuild:
        return "（旧稿未通过检查。请根据大纲重新完成本幕，不要复用旧稿情节和句子。）"
    value = str(text or "").strip()
    _, forbidden = explicit_checks(requirements)
    if not forbidden:
        return value
    paragraphs = re.split(r"\n\s*\n", value)
    kept = [paragraph for paragraph in paragraphs
            if not any(term and term in paragraph for term in forbidden)]
    removed = len(paragraphs) - len(kept)
    if not removed:
        return value
    return ("\n\n".join(kept).strip()
            + f"\n\n（已按修订要求剔除 {removed} 段含禁用内容的旧稿，请根据本幕目标补全。）")


def explicit_checks(requirements: str) -> tuple[list[str], list[str]]:
    """仅抽取明确的字面要求。其余语义要求仍需人工审阅。"""
    required, forbidden = [], []
    for line in (requirements or "").splitlines():
        line = line.strip().lstrip("-• ")
        for match in re.finditer(r"(?:使用|必须包含|必须出现)\s*[“\"「]([^”\"」]+)[”\"」]", line):
            required.append(match.group(1).strip())
        match = re.search(r"(?:不得出现|禁止出现|不得写出)\s*([^；。;]+)", line)
        if match:
            forbidden.extend(
                item.strip().strip("“”\"「」 ")
                for item in re.split(r"[、,，]", match.group(1))
            )
        forbidden.extend(re.findall(r"不是\s*[“\"「]?([^，、；。\s”\"」]+)", line))
    return list(dict.fromkeys(x for x in required if x)), list(dict.fromkeys(x for x in forbidden if x))


def _project_term_issues(text: str, settings: dict | None) -> list[str]:
    """应用工程显式提供的术语纠正规则，不在核心代码内置作品知识。"""
    settings = settings or {}
    glossary = settings.get("glossary") or {}
    issues = []
    if not isinstance(glossary, dict):
        glossary = {}
    corrections = settings.get("term_corrections") or {}
    if isinstance(corrections, dict):
        for typo, expected in corrections.items():
            if str(typo) and str(typo) in text:
                issues.append(f"疑似术语错字：{typo}（应为 {expected}）")
    if "不引入有姓名的新角色" in str(settings.get("extra_requirements") or ""):
        for match in re.finditer(r"名叫([^，。；\s的]{2,14})", text):
            name = match.group(1)
            if name not in glossary:
                issues.append(f"出现新命名角色：{name}")
    return list(dict.fromkeys(issues))


def check_chapter_candidate(text: str, source: str, requirements: str,
                            settings: dict | None = None) -> list[str]:
    """筛掉明显未完成或触犯字面约束的建议。"""
    candidate = (text or "").strip()
    issues = []
    if not candidate:
        return ["模型未返回正文"]
    if len(candidate) < len((source or "").strip()) * 0.65:
        issues.append("修订稿不足原文字数的 65%，可能遗漏了部分幕")
    required, forbidden = explicit_checks(requirements)
    issues.extend(f"缺少必含词：{term}" for term in required if term not in candidate)
    issues.extend(f"仍含禁词：{term}" for term in forbidden if term in candidate)
    if re.search(r"(?m)^\s*(?:修改说明|自检报告|修订说明)[:：]", candidate):
        issues.append("模型输出了修订说明而非纯正文")
    issues.extend(_project_term_issues(candidate, settings))
    return issues


def locate_evidence_quote(text: str, quote: str) -> str | None:
    """允许模型省略段间空白，但返回逐字来源；不容忍文字或标点改写。"""
    if not quote or not quote.strip():
        return None
    if quote in text:
        return quote
    positions = [i for i, char in enumerate(text) if not char.isspace()]
    compact = ''.join(text[i] for i in positions)
    target = ''.join(char for char in quote if not char.isspace())
    start = compact.find(target)
    if start < 0:
        return None
    return text[positions[start]:positions[start + len(target) - 1] + 1]


def extract_revision_evidence(model, text: str, audit=None, stage="evidence") -> list[dict]:
    """只抽取有逐字引文的重点记录，不把模型概括当成新的事实。"""
    prompt = """从下方小说正文抽取精简证据表，用于跨场景核对，不作审校结论。
优先选择反复出现或驱动情节的对象、记录、信息。按原文顺序列出其数量、相对位置、来源和状态变化；记录与实物、不同栏位分开标识，未明确同一对象不要合并。
输出 JSON 数组，每项只包含 object（对象或栏位）、state（当时状态，区分已知、猜测、待验）、quote（原文逐字连续引文）。最多24项，不补充书外知识，quote必须直接复制原文。无法定位证据不列入。
【正文】
""" + text
    if audit:
        audit.write("revision_evidence_request", stage=stage, prompt=prompt)
    raw = model.complete(prompt, system="你是原文证据整理员，只输出 JSON 数组。",
                         temperature=0, num_predict=3000, disable_thinking=True)
    if audit:
        audit.write("revision_evidence_response", stage=stage, raw_response=raw)
    match = re.search(r"\[[\s\S]*\]", raw or "")
    if not match:
        raise ValueError("证据整理未返回数组，保留检查点后可重试")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        from json_repair import repair_json
        data = json.loads(repair_json(match.group(0)))
    if not isinstance(data, list) or len(data) > 24:
        raise ValueError("证据表格式或数量不符合要求")
    rows = []
    for item in data:
        if not isinstance(item, dict) or any(not isinstance(item.get(key), str) or not item[key].strip()
                                            for key in ("object", "state", "quote")):
            raise ValueError("证据项缺少对象、状态或引文")
        located = locate_evidence_quote(text, item["quote"])
        if located is None:
            raise ValueError("证据引文无法在正文逐字定位，不能用于验收")
        if audit and located != item["quote"]:
            audit.write("revision_evidence_whitespace_aligned", stage=stage,
                        object=item["object"], model_quote=item["quote"], source_quote=located)
        rows.append({"object": item["object"], "state": item["state"], "quote": located})
    if text.strip() and not rows:
        raise ValueError("非空正文未提取到证据，不能按无问题放行")
    if audit:
        audit.write("revision_evidence_validated", stage=stage, evidence=rows)
    return rows


def review_candidate_requirements(model, text: str, requirements: str,
                                  settings: dict | None = None, audit=None,
                                  stage: str = "revision_acceptance",
                                  review_scope: str = "requirements",
                                  evidence_rows=None, source_evidence=None,
                                  source_text: str = "") -> list[str]:
    """用当前模型复核难以机械判断的自然语言要求，替代作品专用规则。"""
    if not (text or "").strip() or (review_scope == "requirements" and not (requirements or "").strip()):
        return []
    settings = settings or {}
    instruction = (
        "按修订 Skill 验收本次要求。"
        if review_scope == "requirements" else
        "独立审查修订稿内部一致性，不验收先前的修改要求。按全文出现顺序追踪同一对象、信息与证据：判断是否先于证据取得；数量、编号和相对位置是否一致；记录、原件、副本与实物是否被混为一谈。只报告明确矛盾。"
    )
    if review_scope == "combined":
        instruction = "一次通读完整建议稿，核对用户明确修订要求是否落实，以及全文内部是否出现明确矛盾。仅报告有准确原句支持的问题；不检查书外设定、不新增修改要求，不把文风偏好或合理省略当错误。场景切换后重新判断代词指代。"
    if review_scope == "requirements_and_regressions":
        instruction = """本次任务是修订验收，不是重新进行全文一致性审校。
逐项核对用户修订要求：检查对应内容及其关联段落是否落实，只报告仍未满足的明确要求。
另对照原稿检查本次修改是否引入新的明显矛盾或无关事实改变。必须能指出原稿与修订稿的准确原句，以及本次具体修改如何造成问题；不能确定由本次修改引入的，不报告。
原稿已经存在、但用户未要求修改的问题，即使在修订稿中仍存在，也不属于本轮验收问题。不得扩大原要求，不得因要求保留的原文自身存在旧问题而判定修订失败。
不做全书设定审校，不输出新修订建议；合理省略、概述与完整引文的详略差异、文风偏好均不算问题。
每项输出以“要求未落实：”或“修改引入问题：”开头，附准确引文与简短原因；两类均无明确问题时输出 []。"""
    evidence = ("【用户修订要求】\n" + requirements if review_scope in ("requirements", "combined", "requirements_and_regressions") else
                "本轮唯一证据是下方修订稿。每项冲突必须引用稿内两处互不兼容的准确原句；没有第二处证据时不列为明确矛盾。不得推测外部背景日期、未提供的历史或人物是否首次登场。")
    prompt = f"""{instruction} 不调用书外知识。
输出 JSON 字符串数组；每项是一条明确、可修改的不符合之处。全部满足时输出 []。不要输出说明或 Markdown。
不得因为个人文风偏好报错；只有明确违反、遗漏、人物动作归属错误或幕范围错误才报告。
尚待验证的信息不得提前当作已确认事实。明确的假设和疑问不算提前断言。
每个问题必须附准确原句和无法同时成立的原因。概述不是完整列举，未描写不等于未发生；不要把合理省略、比喻、可解释的状态变化或证据不足作为错误。不能仅因补充了原先未提及的细节而报错。

{evidence}

{('【修订前原稿｜仅用于对照本次修改，不重新审校原稿】' + chr(10) + source_text) if review_scope == 'requirements_and_regressions' else ''}

【本稿证据索引｜仅辅助定位，结论仍需核对完整正文】
{json.dumps(evidence_rows or [], ensure_ascii=False)}
{('【原稿关键证据｜未被要求修改且不自相矛盾的事实应保留；检查是否靠改变这些事实绕过原问题。原稿有矛盾时允许必要更正，不要求照搬错误】' + chr(10) + json.dumps(source_evidence or [], ensure_ascii=False)) if review_scope == 'requirements' else ''}

【修订稿】
{text}
"""
    if review_scope == "requirements":
        from core.agent_skill import load_agent_skill
        guidance, skill = load_agent_skill("chapter-revision", "acceptance")
        if audit:
            audit.write("skill_loaded", stage=stage, **skill)
        prompt = (guidance + "\n【本次用户修订要求】\n" + requirements +
                  "\n【修订稿】\n" + text)
    if audit:
        audit.write("revision_review_request", stage=stage, prompt=prompt)
    raw = model.complete(
        prompt, system="你是小说修订验收员，只输出 JSON 数组。",
        temperature=0, num_predict=1200, disable_thinking=True)
    if audit:
        audit.write("revision_review_response", stage=stage, raw_response=raw)
    match = re.search(r"\[[\s\S]*\]", raw or "")
    if not match:
        return ["验收未完成：模型未返回问题数组，请重新验收"]
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        try:
            from json_repair import repair_json
            data = json.loads(repair_json(match.group(0)))
        except Exception:
            return ["验收未完成：模型问题数组无法解析，请重新验收"]
    if not isinstance(data, list):
        return ["验收未完成：模型返回的不是问题数组，请重新验收"]
    if any(not isinstance(item, str) or not item.strip() for item in data):
        return ["验收未完成：问题数组必须仅包含非空字符串，请重新验收"]
    return list(dict.fromkeys(str(item).strip() for item in data
                              if isinstance(item, str) and item.strip()))[:10]


def check_beat_candidate(text: str, source: str, requirements: str,
                         beat_index: int, settings: dict | None = None) -> list[str]:
    candidate = (text or "").strip()
    if not candidate:
        return ["本幕没有返回正文"]
    issues = []
    if len(candidate) < len((source or "").strip()) * 0.65:
        issues.append("本幕篇幅不足原文的 65%")
    required, forbidden = explicit_checks(requirements)
    issues.extend(f"仍含禁词：{term}" for term in forbidden if term in candidate)
    issues.extend(f"缺少必含词：{term}" for term in required if term not in candidate)
    issues.extend(_project_term_issues(candidate, settings))
    return issues


def save_revision_snapshot(project_dir: str, chapter_number: int, text: str,
                           sections: list[dict] | None = None) -> str:
    """在应用建议前保存编辑器全文及原分幕状态，可随时恢复。"""
    directory = os.path.join(project_dir, "runs", "revisions")
    os.makedirs(directory, exist_ok=True)
    name = (f"chapter_{int(chapter_number)}_{time.strftime('%Y%m%d_%H%M%S')}_"
            f"{uuid.uuid4().hex[:8]}_before.json")
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"text": text, "sections": sections or []}, fh, ensure_ascii=False, indent=2)
    return path


def latest_revision_snapshot(project_dir: str, chapter_number: int) -> str | None:
    directory = os.path.join(project_dir, "runs", "revisions")
    if not os.path.isdir(directory):
        return None
    prefix = f"chapter_{int(chapter_number)}_"
    paths = [os.path.join(directory, name) for name in os.listdir(directory)
             if name.startswith(prefix) and name.endswith("_before.json")]
    return max(paths, key=os.path.getmtime) if paths else None


def load_revision_snapshot(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or not isinstance(data.get("text"), str):
        raise ValueError("版本快照格式无效")
    return data


def revision_request_path(project_dir: str, chapter_number: int) -> str:
    return os.path.join(project_dir, "runs", f"chapter_{int(chapter_number)}_revision_request.txt")


def archive_outdated_summary(project_dir: str, chapter_number: int) -> str | None:
    """正文修订后归档旧摘要，防止下一章继续引用过期剧情。"""
    source = os.path.join(project_dir, "chapters", f"chapter_summary_{int(chapter_number)}.txt")
    if not os.path.isfile(source):
        return None
    directory = os.path.join(project_dir, "runs", "revisions")
    os.makedirs(directory, exist_ok=True)
    target = os.path.join(
        directory,
        f"chapter_{int(chapter_number)}_{time.strftime('%Y%m%d_%H%M%S')}_"
        f"{uuid.uuid4().hex[:8]}_summary_before.txt",
    )
    os.replace(source, target)
    return target
