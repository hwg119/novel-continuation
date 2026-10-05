"""受限静态 SVG 校验与 resvg 子进程渲染，不执行脚本或读取外部资源。"""
import io
import json
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

NS = 'http://www.w3.org/2000/svg'
ET.register_namespace('', NS)
ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'core' / 'svg_render.mjs'
TAGS = {'svg','g','defs','path','rect','circle','ellipse','line','polygon','polyline',
        'linearGradient','radialGradient','stop','clipPath','title','desc'}
COMMON = {'id','fill','stroke','stroke-width','opacity','fill-opacity','stroke-opacity',
          'stroke-linecap','stroke-linejoin','stroke-miterlimit','stroke-dasharray',
          'stroke-dashoffset','fill-rule','clip-rule','clip-path','transform'}
SPECIFIC = {
    'svg': {'width','height','viewBox','preserveAspectRatio','version'},
    'path': {'d'}, 'rect': {'x','y','width','height','rx','ry'},
    'circle': {'cx','cy','r'},'ellipse': {'cx','cy','rx','ry'},
    'line': {'x1','y1','x2','y2'},'polygon': {'points'},'polyline': {'points'},
    'linearGradient': {'x1','y1','x2','y2','gradientUnits','gradientTransform','spreadMethod'},
    'radialGradient': {'cx','cy','r','fx','fy','fr','gradientUnits','gradientTransform','spreadMethod'},
    'stop': {'offset','stop-color','stop-opacity'},'clipPath': {'clipPathUnits'},
}


def validate_static_svg(raw):
    raw = str(raw or '')
    if len(raw) > 300000 or re.search(r'<!\s*(DOCTYPE|ENTITY)|<\?',raw,re.I):
        raise ValueError('SVG 包含不允许的声明或超过大小限制')
    match = re.search(r'<svg\b[\s\S]*?</svg\s*>',raw,re.I)
    if not match: raise ValueError('模型未返回完整 SVG')
    try: root = ET.fromstring(match.group(0))
    except ET.ParseError as exc: raise ValueError('SVG XML 格式无效') from exc
    if root.tag != f'{{{NS}}}svg': raise ValueError('SVG 缺少标准命名空间')
    nodes = list(root.iter())
    if len(nodes) > 2048: raise ValueError('SVG 元素过多')
    ids, refs = set(), []
    def visit(node, depth=0):
        if depth > 24: raise ValueError('SVG 分组过深')
        tag = node.tag.removeprefix(f'{{{NS}}}')
        if node.tag != f'{{{NS}}}{tag}' or tag not in TAGS or (tag=='svg' and depth):
            raise ValueError(f'SVG 不支持元素：{tag}')
        # Inline presentation styles are normalized; no CSS selectors/imports.
        style = node.attrib.pop('style', '')
        for declaration in style.split(';'):
            if not declaration.strip(): continue
            key, separator, value = declaration.partition(':')
            if not separator: raise ValueError('SVG 样式格式无效')
            key = key.strip(); value = value.strip()
            if key not in COMMON | SPECIFIC.get(tag,set()): raise ValueError(f'SVG 不支持样式：{key}')
            node.attrib.setdefault(key,value)
        for key, value in node.attrib.items():
            if key not in COMMON | SPECIFIC.get(tag,set()): raise ValueError(f'SVG 不支持属性：{key}')
            if len(value)>80000: raise ValueError('SVG 属性过长')
            if key == 'id':
                if not re.fullmatch(r'[A-Za-z_][\w.-]{0,79}', value) or value in ids:
                    raise ValueError('SVG ID 无效或重复')
                ids.add(value)
            elif key in ('fill','stroke','stop-color','clip-path'):
                ref = re.fullmatch(r'url\(\s*#([A-Za-z_][\w.-]*)\s*\)',value)
                if ref: refs.append(ref.group(1))
                elif not re.fullmatch(r'#[0-9a-fA-F]{3,8}|none|transparent|black|white|currentColor|[a-zA-Z]{1,24}|rgba?\([\d.,%\s]+\)',value):
                    raise ValueError('SVG 颜色或资源引用无效；不允许外部资源')
            elif key=='d':
                if not value.strip() or re.search(r'[^MmLlHhVvCcSsQqTtAaZz0-9eE+.,\s-]',value):
                    raise ValueError('SVG 路径指令无效')
            elif key in ('transform','gradientTransform'):
                if re.search(r'[^a-zA-Z0-9eE+.,()\s-]', value) or not re.fullmatch(
                    r'\s*(?:(?:matrix|translate|scale|rotate|skewX|skewY)\([0-9eE+.,\s-]+\)\s*)+',value):
                    raise ValueError('SVG 变换无效')
            elif key not in ('stroke-linecap','stroke-linejoin','fill-rule','clip-rule','gradientUnits',
                             'spreadMethod','clipPathUnits','preserveAspectRatio','version'):
                if re.search(r'[^0-9eE+.,%\s-]',value): raise ValueError('SVG 数值属性无效')
            if re.search(r'\b(?:NaN|Infinity)\b',value,re.I): raise ValueError('SVG 数值无效')
            if key not in ('id','fill','stroke','stop-color','clip-path'):
                for number in re.findall(r'[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?',value):
                    if abs(float(number)) > 100000: raise ValueError('SVG 数值超出范围')
        for child in node: visit(child,depth+1)
    visit(root)
    if any(ref not in ids for ref in refs): raise ValueError('SVG 引用了不存在的渐变或裁剪')
    if not any(n.tag.rsplit('}',1)[-1] in {'path','rect','circle','ellipse','line','polygon','polyline'} for n in nodes):
        raise ValueError('SVG 没有可绘制图形')
    root.attrib.update(width='1200',height='520',viewBox='0 0 1200 520')
    return ET.tostring(root,encoding='unicode')


def _node():
    node = shutil.which('node')
    if not node or not (ROOT/'frontend/node_modules/@resvg/resvg-js').is_dir():
        raise ValueError('resvg 未安装，请安装 Node.js 并运行 npm install --prefix frontend')
    return node


def ensure_resvg():
    try:
        result = subprocess.run([_node(),str(HELPER),'--check'],capture_output=True,timeout=10,
                                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise ValueError('resvg 启动失败，请检查 Node.js 和平台依赖') from exc
    if result.returncode: raise ValueError('resvg 平台依赖不可用，请重新运行 npm install --prefix frontend')


def render_resvg_png(svg, png_path, scale=2):
    if scale not in (1,2,3,4): raise ValueError('渲染倍率必须为 1–4')
    svg = validate_static_svg(svg)
    try:
        result = subprocess.run([_node(),str(HELPER)],
            input=json.dumps({'svg':svg,'scale':scale}).encode('utf-8'),capture_output=True,timeout=20,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    except (OSError,subprocess.TimeoutExpired) as exc: raise ValueError('SVG 渲染失败或超时') from exc
    if result.returncode:
        raise ValueError('resvg 无法渲染 SVG：'+result.stderr.decode('utf-8',errors='replace')[-700:])
    from PIL import Image
    with Image.open(io.BytesIO(result.stdout)) as image:
        if image.size != (1200*scale,520*scale): raise ValueError('SVG 渲染尺寸异常')
        image.verify()
    Path(png_path).write_bytes(result.stdout)
