# -*- coding: utf-8 -*-
"""章节 SVG 插图：模型生成、严格校验，并按章节持久化。"""
import math
import html
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from core.chapter_export import HEIGHT, SVG_NS, WIDTH
from core.project_manager import chapter_path, load_project_settings, settings_for_chapter
from core.utils import read_file


SHAPES = {
    "rect": ("x", "y", "width", "height", "fill"),
    "circle": ("cx", "cy", "r", "fill"),
    "ellipse": ("cx", "cy", "rx", "ry", "fill"),
    "polygon": ("points", "fill"),
    "line": ("x1", "y1", "x2", "y2", "stroke", "stroke-width"),
}
COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")
NUMBER = re.compile(r"-?\d+(?:\.\d+)?\Z")
ILLUSTRATION_STYLES = {
    "auto": "依据小说的时代、题材和本章气质选择画风；不要默认儿童卡通风格。",
    "wuxia": "武侠绘本：突出人物动作、古风服饰、山川与江湖气息；以克制的矿物色和有力的剪影塑形，避免现代服装与童话摆件。",
    "ink": "水墨剪影：以留白、深浅墨色和少量朱砂色组织远近层次；用简洁几何形状模拟笔墨，不画重复的椭圆云团。",
    "children": "儿童绘本：温暖、明快、友好，使用清晰柔和的色块与易读的人物姿态。",
    "custom": "遵照下方用户自定义画风说明。",
}


def illustration_path(project_dir: str, number: int) -> Path:
    if number < 1:
        raise ValueError("章号必须大于零")
    return Path(project_dir) / "illustrations" / f"chapter_{number}.svg"


def _number(value: str) -> str:
    if not NUMBER.fullmatch(value):
        raise ValueError("SVG 包含无效坐标")
    number = float(value)
    if not math.isfinite(number) or abs(number) > 5000:
        raise ValueError("SVG 坐标超出范围")
    return value


def validate_illustration_svg(raw: str) -> str:
    """只保留本地栅格化器支持的几何图形，不允许脚本/外链/文本。"""
    raw = html.unescape(str(raw or ""))
    if len(raw) > 100_000 or re.search(r"<!\s*(?:DOCTYPE|ENTITY)", raw, re.I):
        raise ValueError("SVG 包含不允许的内容")
    match = re.search(r"<svg\b[\s\S]*?</svg\s*>", raw, re.I)
    if not match:
        # 一些推理模型会遗漏或截断最外层 svg 标签，但已经给出了完整的
        # 白名单图形。只重建外壳，后续仍逐元素执行全部安全校验。
        shapes = re.findall(
            r"<(?:rect|circle|ellipse|polygon|line)\b[^<>]*?/\s*>", raw,
            flags=re.I,
        )
        if not shapes:
            raise ValueError("模型未返回 SVG 图像")
        raw = (f'<svg xmlns="{SVG_NS}" width="{WIDTH}" height="{HEIGHT}" '
               f'viewBox="0 0 {WIDTH} {HEIGHT}">' + "".join(shapes) + "</svg>")
        match = re.search(r"<svg\b[\s\S]*?</svg\s*>", raw, re.I)
    try:
        source = ET.fromstring(match.group(0))
    except ET.ParseError as exc:
        raise ValueError("SVG 格式无效") from exc
    if source.tag != f"{{{SVG_NS}}}svg":
        raise ValueError("SVG 缺少标准命名空间")
    children = list(source)
    if not children or len(children) > 120:
        raise ValueError("SVG 应包含 1–120 个简单图形")
    output = ET.Element(f"{{{SVG_NS}}}svg", {
        "width": str(WIDTH), "height": str(HEIGHT), "viewBox": f"0 0 {WIDTH} {HEIGHT}"})
    for child in children:
        if not isinstance(child.tag, str) or not child.tag.startswith(f"{{{SVG_NS}}}"):
            raise ValueError("SVG 含有不支持的元素")
        kind = child.tag.rsplit("}", 1)[-1]
        required = SHAPES.get(kind)
        if required is None or list(child):
            raise ValueError(f"SVG 含有不支持的元素：{kind}")
        missing = [key for key in required if key not in child.attrib]
        if missing:
            raise ValueError(f"SVG 元素 {kind} 缺少必要属性：{', '.join(missing)}")
        # 输出会重建为白名单 SVG，因此模型偶尔附加的 rx、opacity、class、
        # onclick 等属性可以安全丢弃，无需为一个装饰属性重生成整幅插图。
        attrs = {}
        for key in required:
            value = child.attrib[key].strip()
            if key in ("fill", "stroke"):
                if not COLOR.fullmatch(value):
                    raise ValueError("SVG 颜色必须是六位十六进制色值")
            elif key == "points":
                pairs = value.split()
                if not 3 <= len(pairs) <= 40:
                    raise ValueError("SVG 多边形顶点数无效")
                value = " ".join(",".join(_number(part) for part in pair.split(","))
                                 for pair in pairs if pair.count(",") == 1)
                if len(value.split()) != len(pairs):
                    raise ValueError("SVG 多边形坐标无效")
            else:
                _number(value)
                if key in ("width", "height", "r", "rx", "ry", "stroke-width") and float(value) <= 0:
                    raise ValueError("SVG 图形尺寸必须大于零")
            attrs[key] = value
        ET.SubElement(output, child.tag, attrs)
    return ET.tostring(output, encoding="unicode")


def generate_chapter_illustration(project_dir: str, number: int, llm,
                                  preview: dict | None = None, progress=None) -> dict:
    target = illustration_path(project_dir, number)
    settings = settings_for_chapter(load_project_settings(project_dir), number)
    preview = preview or {}
    title = str(preview.get("chapter_title") or settings.get("chapter_title") or "")[:150]
    brief = str(preview.get("chapter_brief") or settings.get("chapter_brief") or "")[:900]
    beats = preview.get("beats") if isinstance(preview.get("beats"), list) else settings.get("beats") or []
    outline = "\n".join(str(beat.get("desc") or beat.get("name") or "")[:180]
                        for beat in beats[:8] if isinstance(beat, dict))
    source = Path(chapter_path(project_dir, number))
    chapter_text = read_file(str(source)) if source.is_file() else ""
    if not title and chapter_text:
        title = chapter_text.splitlines()[0].strip()[:150]
    # 插图表现章节意象，不复述整章。章纲优先，正文只提供少量首、中、尾锚点，
    # 防止模型被枝节人物和道具牵引成逐场景复刻。
    if len(chapter_text) > 900:
        middle = max(0, len(chapter_text) // 2 - 125)
        excerpt = (chapter_text[:350] + "\n……〔中段锚点〕……\n" +
                   chapter_text[middle:middle + 250] + "\n……〔结尾锚点〕……\n" +
                   chapter_text[-300:])
    else:
        excerpt = chapter_text
    if not any((title.strip(), brief.strip(), outline.strip(), excerpt.strip())):
        raise ValueError("请先填写本章规划，或生成并保存本章正文")
    style_key = str(preview.get("illustration_style") or settings.get("illustration_style") or "auto")
    if style_key not in ILLUSTRATION_STYLES:
        style_key = "auto"
    style_notes = str(preview.get("illustration_style_notes") or
                      settings.get("illustration_style_notes") or "")[:500].strip()
    style = ILLUSTRATION_STYLES[style_key]
    if style_key == "custom":
        style = style_notes or ILLUSTRATION_STYLES["auto"]
    elif style_notes:
        style += f" 补充要求：{style_notes}"
    context = f"""第 {number} 章：{title}
章节目标：{brief}
分幕：{outline[:900]}
正文意象锚点：{excerpt[:900]}"""
    if progress:
        progress(f"正在确定第 {number} 章插图场景")
    scene = llm.complete(
        "提炼本章最核心的情绪、冲突或转折，并转化为一幅简洁的章节意象图。"
        "不必逐项还原正文，不要罗列人物和道具；选择一个有原文依据的主体，"
        "配合环境、光影或一件象征性物件表达主题。不得借用其他章节或书外知识。"
        "用不超过 90 字说明画面主体、空间层次、主色调和视觉焦点。"
        "只输出画面描述，不要 SVG 或解释。\n\n" + context,
        system="你是小说章节图版编辑，用克制、抽象而有原文依据的画面概括一章。",
        temperature=0.1, num_predict=170, disable_thinking=True)
    scene = re.sub(r"<think(?:ing)?>.*?</think(?:ing)?>", "", str(scene or ""),
                   flags=re.DOTALL | re.IGNORECASE)
    scene = re.sub(r"<[^>]*>", "", scene).strip()[:350]
    if not scene:
        candidates = [
            next((str(beat.get("desc") or beat.get("name") or "").strip()
                  for beat in beats if isinstance(beat, dict)
                  and str(beat.get("desc") or beat.get("name") or "").strip()), ""),
            brief.strip(),
            next((line.strip() for line in excerpt.splitlines()
                  if line.strip() and line.strip() != title.strip()), ""),
            title.strip(),
        ]
        scene = next((candidate[:350] for candidate in candidates if candidate), "")
        if not scene:
            raise ValueError("模型未选出插图场景，章纲和正文中也没有可用内容")
        if progress:
            progress(f"模型未返回场景描述，已根据第 {number} 章章纲或正文确定场景")
    prompt = f"""根据已选定的章节意象绘制一幅横向抽象图版。以大色块、剪影、留白和空间层次表达本章气质；保留一个清晰视觉焦点，不必逐项复刻人物动作与道具。不要套用固定模板，不要文字、标志或写实人脸，也不要增加资料中没有的关键设定。
画风：{style}
已选场景：{scene}
回答的第一个字符必须是 <，最后一个标签必须是 </svg>；只输出完整 SVG 代码，不要解释、分析、Markdown 或代码围栏。
画布固定 1200×520，必须使用 xmlns="http://www.w3.org/2000/svg"。
只允许直接在 svg 下使用 rect、circle、ellipse、polygon、line；使用 18–48 个元素完成画面。
每个元素只能使用下列属性，不得使用 path、g、style、渐变、滤镜、图片、链接、脚本或其它属性：
rect: x y width height fill；circle: cx cy r fill；ellipse: cx cy rx ry fill；
polygon: points fill（坐标格式 x,y x,y x,y）；line: x1 y1 x2 y2 stroke stroke-width。
所有颜色使用 #RRGGBB，所有坐标使用普通数字。用多层色块和剪影构成完整画面，避免大面积重复云朵和过小的人物。"""
    last_error = None
    for attempt in range(2):
        if progress:
            progress(f"正在生成第 {number} 章插图（第 {attempt + 1} 次）")
        response = llm.complete(
            prompt,
            system=("你是 SVG 插画师。不要解释和思考，立即从 <svg 开始输出；"
                    "严格遵守图形与属性白名单，只返回 SVG。"),
            temperature=0.2 if not attempt else 0.1,
            num_predict=5200,
            disable_thinking=True,
        )
        try:
            svg = validate_illustration_svg(response)
            break
        except ValueError as exc:
            last_error = exc
            if progress:
                compact = re.sub(r"\s+", " ", str(response or "")).strip()
                preview_text = compact[:100] if compact else "（空返回）"
                progress(
                    f"第 {number} 章插图第 {attempt + 1} 次校验未通过：{exc}；"
                    f"返回 {len(str(response or ''))} 字符，开头：{preview_text}"
                )
            prompt += (f"\n上一次输出无法使用：{exc}。这次将画面简化为 12–36 个元素，"
                       "不要说明原因；第一个字符直接输出 <svg，务必以 </svg> 结束。")
    else:
        raise ValueError(f"模型两次返回的 SVG 均无法用于 Word：{last_error}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(svg, encoding="utf-8")
    temporary.replace(target)
    if progress:
        progress(f"第 {number} 章插图已保存")
    return {"chapter": number, "path": str(target), "scene": scene,
            "style": style_key, "elements": len(list(ET.fromstring(svg)))}
