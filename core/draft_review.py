# -*- coding: utf-8 -*-
"""按幕保存续写草稿的审稿状态；正文仍以普通 chapter_N.txt 导出。"""
import os
import time

from core.utils import load_json, save_data_to_json


def review_path(project_dir: str, chapter_number: int) -> str:
    return os.path.join(project_dir, "runs", f"chapter_{int(chapter_number)}_review.json")


def save_review_session(project_dir: str, chapter_number: int, title: str,
                        sections: list[dict]) -> bool:
    """持久化分幕正文、质量问题和人工编辑状态，供下次打开继续审稿。"""
    data = {
        "chapter_number": int(chapter_number),
        "title": str(title or ""),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "sections": sections or [],
    }
    return save_data_to_json(data, review_path(project_dir, chapter_number))


def load_review_session(project_dir: str, chapter_number: int) -> dict | None:
    data = load_json(review_path(project_dir, chapter_number), None)
    if not isinstance(data, dict) or not isinstance(data.get("sections"), list):
        return None
    data["sections"] = normalize_sections(data["sections"])
    return data


def normalize_sections(sections: list[dict]) -> list[dict]:
    """兼容早期 UI 的边界标记错误：前一幕被保存成“本幕到全文末尾”时可无损拆回。"""
    normalized = [dict(section) for section in sections or []]
    for index in range(len(normalized) - 1):
        text = str(normalized[index].get("text") or "")
        next_text = str(normalized[index + 1].get("text") or "")
        if next_text and text != next_text and text.endswith(next_text):
            normalized[index]["text"] = text[:-len(next_text)].rstrip()
    # 旧版本可能把模型空答保存成 passed；读取时纠正，避免 UI 继续显示通过。
    for section in normalized:
        if not str(section.get("text") or "").strip():
            issues = [str(item) for item in section.get("issues") or [] if str(item).strip()]
            message = "本幕正文为空，需要重新生成"
            if message not in issues:
                issues.append(message)
            section["issues"] = issues
            section["status"] = "issues"
    return normalized
