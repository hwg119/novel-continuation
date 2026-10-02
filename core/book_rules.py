# -*- coding: utf-8 -*-
"""从有限的母本章节样本提出可人工审阅的全书规则。"""
import json
import re

from core.project_manager import list_chapter_files
from core.utils import read_file


def extract_book_rules(project_dir: str, llm, existing: dict | None = None) -> dict:
    files = list_chapter_files(project_dir)
    if not files:
        raise ValueError("没有可供提取的章节，请先导入母本。")
    indices = sorted({0, len(files) // 2, len(files) - 1})
    excerpts = []
    for index in indices:
        number, _, path = files[index]
        text = read_file(path).strip()
        if text:
            excerpts.append(f"【第 {number} 章摘录】\n{text[:2500]}")
    if not excerpts:
        raise ValueError("章节文件为空，无法提取全书规则。")
    existing = existing or {}
    prompt = f"""请根据下列有限章节样本，提出小说续写的全书规则草案。
只归纳样本能支持的稳定事实与语体特征，不把单章事件写成全书设定。
样本不足以确定的事实不要编造；保留已有规则中未被样本否定的约束。
若母本是英文，也请用中文输出。不要给本章分幕或剧情规划。

【现有本书背景】\n{str(existing.get('background') or '')[:1600]}
【现有人物口吻】\n{str(existing.get('character_voices') or '')[:1200]}
【现有附加要求】\n{str(existing.get('extra_requirements') or '')[:1600]}

{chr(10).join(excerpts)}

只输出 JSON 对象，包含三个非空字符串字段：background（世界与时间线）、
character_voices（主要人物稳定口吻）、extra_requirements（续写的长期一致性规则）。
不要 Markdown、解释或来源声明。"""
    raw = llm.complete(prompt, system="你是小说设定编辑，只输出严格 JSON。",
                       temperature=0.2, num_predict=1800)
    cleaned = re.sub(r"```(?:json)?", "", raw or "").strip()
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        raise ValueError("模型未返回全书规则 JSON。")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("全书规则必须是 JSON 对象。")
    keys = ("background", "character_voices", "extra_requirements")
    result = {key: data.get(key) for key in keys}
    if any(not isinstance(value, str) or not value.strip() for value in result.values()):
        raise ValueError("模型返回的全书规则字段不完整。")
    return {key: value.strip() for key, value in result.items()}
