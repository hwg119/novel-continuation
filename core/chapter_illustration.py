# -*- coding: utf-8 -*-
"""章节自由 SVG 插图：静态校验、resvg 渲染与按章保存。"""
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from core.chapter_export import SVG_NS
from core.project_manager import chapter_path, load_project_settings, settings_for_chapter
from core.utils import read_file


ILLUSTRATION_STYLES = {
    "auto": "依据小说的时代、题材和本章气质选择画风；不要默认儿童卡通风格。",
    "wuxia": "武侠绘本：突出人物动作、古风服饰、山川与江湖气息；以克制的矿物色和有力的剪影塑形，避免现代服装与童话摆件。",
    "ink": "水墨剪影：以留白、深浅墨色和少量朱砂色组织远近层次；强调自然轮廓和墨色层次，不画重复的椭圆云团。",
    "children": "儿童绘本：温暖、明快、友好，使用清晰柔和的色块与易读的人物姿态。",
    "custom": "遵照下方用户自定义画风说明。",
}


def illustration_path(project_dir: str, number: int) -> Path:
    if number < 1:
        raise ValueError("章号必须大于零")
    return Path(project_dir) / "illustrations" / f"chapter_{number}.svg"


def validate_illustration_svg(raw: str) -> str:
    """统一校验静态 SVG，不再使用旧几何图形渲染分支。"""
    from core.svg_renderer import validate_static_svg
    return validate_static_svg(raw)


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

    from core.svg_renderer import ensure_resvg, validate_static_svg, render_resvg_png
    from core.plan_run_log import PlanRunLogger
    ensure_resvg()  # Fail before a paid model call if runtime is unavailable.
    cfg = getattr(llm, 'cfg', {}) or {}
    log = PlanRunLogger(project_dir, 'illustration_' + uuid.uuid4().hex,
        str(cfg.get('model_name') or ''), number, secrets=(str(cfg.get('api_key') or ''),))
    prompt = f'''为以下章节创作一幅有独特构图的横向 SVG 意象插画。
先在画面设计中抓住核心冲突、情绪或转折，不要把顺带出现的信件当成固定主体。
无需使用素材目录或固定模板；用前中后景、光影、负空间及一个视觉焦点表达章节。
优先环境、物件、背影或剪影，不画写实人脸，不复刻全部人物动作，不新增关键设定。
画风：{style}
画布 1200×520，viewBox="0 0 1200 520"，xmlns="http://www.w3.org/2000/svg"。
可用 svg、g、defs、path（含曲线和圆弧）、rect、circle、ellipse、line、polygon、polyline、
linearGradient、radialGradient、stop、clipPath；允许描边、透明度和变换。
渐变及裁剪只能通过 url(#本图ID) 引用；样式尽量使用元素属性。
不要 script、image、use、foreignObject、text、filter、CSS 样式表、外链、字体、动画。
建议 30–100 个绘制元素。保持轮廓流畅、层次清晰，避免重复椭圆云朵和拼积木式人物。
只输出完整 SVG，无思考、解释或 Markdown。
【本章资料】
{context}'''
    if progress:
        progress(f'已创建自由插图日志：{log.path}')
    last_error = None
    for attempt in range(2):
        if progress: progress(f'正在生成第 {number} 章自由 SVG 意象图（第 {attempt+1} 次）')
        log.write('free_svg_request', prompt=prompt, attempt=attempt+1)
        started = time.monotonic()
        try:
            raw = llm.complete(prompt,system='你是小说 SVG 插画师，只返回完整静态 SVG。',
                temperature=0.3 if not attempt else 0.15,num_predict=7000,disable_thinking=True)
            log.write('free_svg_response',raw_response=raw,attempt=attempt+1,
                      elapsed_seconds=round(time.monotonic()-started,2))
            svg = validate_static_svg(raw)
            # Keep an explicit group for format identification across legacy loaders.
            root = ET.fromstring(svg)
            group = ET.SubElement(root, f'{{{SVG_NS}}}g')
            for child in list(root):
                if child is group or child.tag.endswith('}defs'): continue
                root.remove(child); group.append(child)
            svg = ET.tostring(root,encoding='unicode')
            target.parent.mkdir(parents=True,exist_ok=True)
            temporary = target.with_name(target.stem+'.'+uuid.uuid4().hex+'.tmp')
            raster = temporary.with_suffix('.png')
            try:
                render_resvg_png(svg,str(raster))
                from PIL import Image
                with Image.open(raster) as rendered:
                    if all(low == high for low, high in rendered.convert('RGB').getextrema()):
                        raise ValueError('SVG 渲染后只有单一底色，缺少可见画面主体')
                temporary.write_text(svg,encoding='utf-8')
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True); raster.unlink(missing_ok=True)
            log.write('free_svg_saved',path=str(target),renderer='resvg',attempt=attempt+1)
            if progress: progress(f'第 {number} 章自由插图已保存，resvg 渲染校验通过')
            return {'chapter':number,'path':str(target),'method':'free','style':style_key,
                    'renderer':'resvg','log_path':str(log.path)}
        except ValueError as exc:
            last_error = exc
            log.write('free_svg_validation_failed',error=str(exc),attempt=attempt+1)
            if progress: progress(f'自由插图第 {attempt+1} 次未通过校验：{exc}')
            prompt += f'\n上次校验失败：{exc}。保留画面意图，简化并修正 SVG，不输出解释。'
        except Exception as exc:
            log.write('free_svg_failed',error=str(exc),attempt=attempt+1)
            raise
    raise ValueError(f'自由插图两次未通过校验，原插图未改动：{last_error}')
