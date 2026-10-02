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
                               requirements: str) -> str:
    beats = settings.get("beats") or []
    outline = "\n".join(
        f"{i}. 第{beat.get('num', i)}幕 {beat.get('name', '')}：{beat.get('desc', '')}"
        for i, beat in enumerate(beats, 1)
    )
    return f"""请修订下列小说章节。通读全章，逐项落实用户要求，保持六幕的先后顺序、已经正确的情节与人物关系。
只输出完整的修订后正文，不要标题、幕名、修改说明、自检报告或 Markdown。不得把原章压缩成摘要。

【章节】{title}
【既定世界与人物事实】
{settings.get('background', '')}
{settings.get('extra_requirements', '')}
{settings.get('chapter_requirements', '')}

【分幕大纲】
{outline}

【本次整章修订要求】
{requirements}

【当前完整正文】
{source}

输出前在内部逐项核对修订要求，并确认每幕都有正文；若发现违反要求，请先修正再输出完整正文。"""


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


def review_candidate_requirements(model, text: str, requirements: str,
                                  settings: dict | None = None) -> list[str]:
    """用当前模型复核难以机械判断的自然语言要求，替代作品专用规则。"""
    if not (requirements or "").strip() or not (text or "").strip():
        return []
    settings = settings or {}
    prompt = f"""逐项检查修订稿是否满足用户要求。只依据给出的要求、工程设定和修订稿，不调用书外知识。
输出 JSON 字符串数组；每项是一条明确、可修改的不符合之处。全部满足时输出 []。不要输出说明或 Markdown。
不得因为个人文风偏好报错；只有明确违反、遗漏、人物动作归属错误或幕范围错误才报告。

【工程设定】
{settings.get('background', '')}
{settings.get('extra_requirements', '')}
{settings.get('chapter_requirements', '')}

【用户修订要求】
{requirements}

【修订稿】
{text}
"""
    raw = model.complete(
        prompt, system="你是小说修订验收员，只输出 JSON 数组。",
        temperature=0, num_predict=1200, disable_thinking=True)
    match = re.search(r"\[[\s\S]*\]", raw or "")
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        try:
            from json_repair import repair_json
            data = json.loads(repair_json(match.group(0)))
        except Exception:
            return []
    if not isinstance(data, list):
        return []
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
