# core/corpus.py
# -*- coding: utf-8 -*-
"""母本导入：把一整本原始小说文本切分成章节文件。

输出格式与工程目录约定一致：``<project>/chapters/chapter_N.txt``。
"""
import os
import re

CN_DIGITS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
    "八": 8, "九": 9, "○": 0, "零": 0, "〇": 0, "两": 2,
}

# 默认回目/章节标题：支持“第十四回”“第1章”“第 100 节”等写法。
DEFAULT_HEADING_RE = re.compile(
    r"^\s*第\s*([0-9０-９]+|[一二三四五六七八九十百千○零〇两]+)\s*[回章节卷]\s*(.*)$"
)

# 英文常见章节标题：`— CHAPTER ONE —`、`CHAPTER TWENTY-ONE`、`Chapter 1`。
# 连字符必须允许，否则 TWENTY-ONE 这类章号会被漏掉。
EN_HEADING_RE = re.compile(
    r"^\s*—?\s*CHAPTER\s+([0-9]+|[A-Za-z][A-Za-z\- ]*)\s*—?\s*$",
    re.IGNORECASE,
)

_FULLWIDTH = str.maketrans("０１２３４５６７８９", "0123456789")


def cn_to_int(token: str):
    """解析回目编号，支持古典写法（十四/一百零八）与逐位写法（一○=10）。"""
    token = (token or "").strip().translate(_FULLWIDTH)
    if not token:
        return None
    if token.isdigit():
        return int(token)

    if any(u in token for u in "十百千"):
        total = 0
        number = 0
        for ch in token:
            if ch in CN_DIGITS:
                number = CN_DIGITS[ch]
            elif ch == "十":
                total += (number or 1) * 10
                number = 0
            elif ch == "百":
                total += (number or 1) * 100
                number = 0
            elif ch == "千":
                total += (number or 1) * 1000
                number = 0
            else:
                return None
        return total + number

    if not all(ch in CN_DIGITS for ch in token):
        return None
    value = 0
    for ch in token:
        value = value * 10 + CN_DIGITS[ch]
    return value


def read_text(path: str) -> str:
    """按常见中文编码依次尝试读取文本。"""
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            with open(path, "r", encoding=encoding) as fh:
                return fh.read()
        except (UnicodeDecodeError, LookupError):
            continue
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        return fh.read()


def strip_boilerplate(text: str) -> str:
    """去掉 Project Gutenberg 之类的版权头尾。"""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines)
                  if ln.strip().startswith("*** START OF")), 0)
    end = next((i for i, ln in enumerate(lines)
                if ln.strip().startswith("*** END OF")), len(lines))
    return "\n".join(lines[start:end] if start or end < len(lines) else lines)


def _find_marks(lines: list, heading_re) -> list:
    """按标题正则扫描，返回 [(章号, 行号, 标题)]。"""
    marks = []
    for idx, line in enumerate(lines):
        m = heading_re.match(line)
        if not m:
            continue
        num = cn_to_int(m.group(1))
        title = (m.group(2).strip() if m.lastindex and m.lastindex >= 2 else "")
        marks.append((num, idx, title))
    return marks


def split_into_chapters(text: str, heading_re=None) -> list:
    """按标题切分，返回 [(章号, 标题, 正文)]。

    默认正则认不出中文回目时，再试英文 `CHAPTER ...` 写法；两者都找不到，
    才退化为按段落长度分块，章号从 1 顺序编号。
    """
    heading_re = heading_re or DEFAULT_HEADING_RE
    lines = text.splitlines()

    marks = _find_marks(lines, heading_re)
    if not marks and heading_re is DEFAULT_HEADING_RE:
        marks = _find_marks(lines, EN_HEADING_RE)
    if not marks:
        return _chunk_by_size(lines)

    chapters = []
    seen = set()
    for i, (num, idx, title) in enumerate(marks):
        stop = marks[i + 1][1] if i + 1 < len(marks) else len(lines)
        body = "\n".join(lines[idx:stop]).strip()
        if num is None or num in seen:
            num = (chapters[-1][0] + 1) if chapters else 1
        seen.add(num)
        chapters.append((num, title, body))
    return chapters


def _chunk_by_size(lines: list, target: int = 4000) -> list:
    """无标题文本的兜底分块。"""
    chapters = []
    buffer = []
    length = 0
    for line in lines:
        buffer.append(line)
        length += len(line)
        if length >= target and not line.strip():
            chapters.append((len(chapters) + 1, "", "\n".join(buffer).strip()))
            buffer = []
            length = 0
    if any(ln.strip() for ln in buffer):
        chapters.append((len(chapters) + 1, "", "\n".join(buffer).strip()))
    return chapters


def prepare_corpus(raw_paths, chapters_dir: str, heading_pattern: str = None,
                   clear_existing: bool = True) -> dict:
    """把一个或多个母本文件切成章节文件写入 chapters_dir，返回统计信息。

    ``raw_paths`` 可以是单个路径字符串，也可以是路径列表。多文件按给定顺序
    依次处理，章号尽量沿用原文回目号；若某个文件的章号与已写入的重复，则该
    文件整体顺延到当前最大章号之后，避免互相覆盖。
    """
    paths = [raw_paths] if isinstance(raw_paths, str) else list(raw_paths or [])
    if not paths:
        raise ValueError("未指定母本文件。")
    for path in paths:
        if not os.path.exists(path):
            raise FileNotFoundError(f"未找到母本文件：{path}")

    heading_re = DEFAULT_HEADING_RE
    if heading_pattern and heading_pattern.strip():
        heading_re = re.compile(heading_pattern.strip())

    if clear_existing and os.path.isdir(chapters_dir):
        for name in os.listdir(chapters_dir):
            if re.match(r"^chapter_\d+\.txt$", name):
                os.remove(os.path.join(chapters_dir, name))
    os.makedirs(chapters_dir, exist_ok=True)

    written = []       # (章号, 标题, 文件路径)
    used = set()       # 已占用的章号
    per_file = []

    for path in paths:
        text = strip_boilerplate(read_text(path))
        chapters = split_into_chapters(text, heading_re)
        if not chapters:
            raise ValueError(
                f"未能从 {os.path.basename(path)} 切分出任何章节，"
                f"请检查章节标题格式或自定义正则。"
            )

        nums = [c[0] for c in chapters]
        if used and min(nums) <= max(used):
            offset = max(used) - min(nums) + 1
            chapters = [(n + offset, t, b) for (n, t, b) in chapters]

        for num, title, body in chapters:
            header = f"第{num}章 {title}".rstrip() if title else ""
            content = (header + "\n\n" + body).strip() + "\n"
            target = os.path.join(chapters_dir, f"chapter_{num}.txt")
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(content)
            used.add(num)
            written.append((num, title, target))

        per_file.append({
            "file": path,
            "count": len(chapters),
            "first": chapters[0][0],
            "last": chapters[-1][0],
        })

    nums_all = [w[0] for w in written]
    duplicates = sorted({n for n in nums_all if nums_all.count(n) > 1})
    return {
        "count": len(written),
        "first": min(nums_all),
        "last": max(nums_all),
        "duplicates": duplicates,
        "chapters_dir": chapters_dir,
        "mode": "headings" if any(w[1] for w in written) else "chunks",
        "files": per_file,
    }