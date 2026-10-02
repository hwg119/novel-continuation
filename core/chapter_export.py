# -*- coding: utf-8 -*-
"""把章节正文及其已保存插图导出为 Word 文档。"""
import re
import xml.etree.ElementTree as ET
from pathlib import Path


WIDTH, HEIGHT = 1200, 520
SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)


def render_svg_png(svg: str, png_path: str, scale: int = 2) -> None:
    """把本模块支持的安全 SVG 几何元素栅格化，无外部渲染进程。"""
    from PIL import Image, ImageDraw

    root = ET.fromstring(svg)
    if root.tag != f"{{{SVG_NS}}}svg":
        raise ValueError("无效的 SVG 根元素")
    image = Image.new("RGB", (WIDTH * scale, HEIGHT * scale), "#ffffff")
    draw = ImageDraw.Draw(image)
    def n(element, key):
        return float(element.attrib[key]) * scale
    for element in root:
        kind = element.tag.rsplit("}", 1)[-1]
        fill = element.attrib.get("fill", "#000000")
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", fill):
            raise ValueError("SVG 颜色无效")
        if kind == "rect":
            x, y = n(element, "x"), n(element, "y")
            draw.rectangle((x, y, x + n(element, "width"), y + n(element, "height")),
                           fill=fill)
        elif kind in ("circle", "ellipse"):
            cx, cy = n(element, "cx"), n(element, "cy")
            rx = n(element, "r") if kind == "circle" else n(element, "rx")
            ry = n(element, "r") if kind == "circle" else n(element, "ry")
            draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=fill)
        elif kind == "polygon":
            points = [tuple(float(v) * scale for v in pair.split(","))
                      for pair in element.attrib["points"].split()]
            draw.polygon(points, fill=fill)
        elif kind == "line":
            draw.line((n(element, "x1"), n(element, "y1"),
                       n(element, "x2"), n(element, "y2")),
                      fill=element.attrib["stroke"],
                      width=round(n(element, "stroke-width")))
        else:
            raise ValueError(f"不支持的 SVG 元素：{kind}")
    image.save(png_path, format="PNG")


def export_chapter_docx(title: str, body: str, output_path: str,
                        chapter_number: int | None = None,
                        illustration_svg: str | None = None) -> dict:
    """导出 Word；仅在提供已保存插图时才在标题后插图。"""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt, RGBColor

    def set_chinese_font(style, family):
        """显式设置东亚字体，避免 Word 用西文字体替代中文。"""
        style.font.name = family
        properties = style._element.get_or_add_rPr()
        fonts = properties.rFonts
        if fonts is None:
            fonts = OxmlElement("w:rFonts")
            properties.insert(0, fonts)
        for kind in ("ascii", "hAnsi", "eastAsia", "cs"):
            fonts.set(qn(f"w:{kind}"), family)

    title = (title or "").strip() or "未命名章节"
    if chapter_number is not None:
        if chapter_number < 1:
            raise ValueError("章号必须大于零")
        chapter_label = f"第 {chapter_number} 章"
        display_title = title if re.match(r"^第\s*\d+\s*章", title) else f"{chapter_label}  {title}"
    else:
        display_title = title
    body = (body or "").strip()
    if not body:
        raise ValueError("当前章正文为空")
    path = Path(output_path)
    if path.suffix.lower() != ".docx":
        raise ValueError("导出路径必须以 .docx 结尾")
    path.parent.mkdir(parents=True, exist_ok=True)
    png_path = path.with_suffix(".png") if illustration_svg else None
    if illustration_svg and png_path:
        from core.chapter_illustration import validate_illustration_svg
        render_svg_png(validate_illustration_svg(illustration_svg), str(png_path))

    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Inches(8.5), Inches(11)
    section.top_margin = section.bottom_margin = Inches(0.8)
    section.left_margin = section.right_margin = Inches(0.85)
    normal = doc.styles["Normal"]
    set_chinese_font(normal, "Microsoft YaHei")
    normal.font.size = Pt(12.5)
    normal.font.color.rgb = RGBColor(40, 43, 46)
    # 正文使用较紧凑的行距；自然段之间仍由段后间距保持层次。
    normal.paragraph_format.line_spacing = 1.2
    normal.paragraph_format.space_after = Pt(9)
    normal.paragraph_format.widow_control = True
    title_style = doc.styles["Title"]
    set_chinese_font(title_style, "Microsoft YaHei")
    title_style.font.color.rgb = RGBColor(0, 0, 0)
    heading = doc.add_paragraph(style="Title")
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    heading.paragraph_format.space_after = Pt(12)
    run = heading.add_run(display_title)
    run.font.name = "Microsoft YaHei"
    run.font.size = Pt(20)
    run.font.bold = True
    run.font.color.rgb = RGBColor(0, 0, 0)
    if png_path:
        picture = doc.add_paragraph()
        picture.alignment = WD_ALIGN_PARAGRAPH.CENTER
        picture.paragraph_format.space_after = Pt(18)
        picture.add_run().add_picture(str(png_path), width=Inches(6.6))
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer_run = footer.add_run("第 ")
    footer_run.font.name = "Microsoft YaHei"
    footer_run.font.size = Pt(9)
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    footer._p.append(field)
    footer_run = footer.add_run(" 页")
    footer_run.font.name = "Microsoft YaHei"
    footer_run.font.size = Pt(9)
    for block in re.split(r"\n\s*\n", body):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        for index, line in enumerate(lines):
            para = doc.add_paragraph(line.strip())
            para.paragraph_format.first_line_indent = Pt(25 if index == 0 else 0)
            para.paragraph_format.space_after = Pt(9 if index == len(lines) - 1 else 0)
    doc.save(str(path))
    return {"docx": str(path), "png": str(png_path) if png_path else None,
            "illustrated": bool(png_path)}
