# core/utils.py
# -*- coding: utf-8 -*-
"""文件读写、JSON 存取与字数统计等通用工具。"""
import json
import os


def read_file(filename: str) -> str:
    """读取文本文件；不存在或异常时返回空字符串。"""
    try:
        with open(filename, "r", encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return ""
    except Exception as e:
        print(f"[read_file] 读取失败: {e}")
        return ""


def save_string_to_txt(content: str, filename: str) -> None:
    """覆盖写入文本文件，自动创建父目录。"""
    try:
        parent = os.path.dirname(os.path.abspath(filename))
        os.makedirs(parent, exist_ok=True)
        with open(filename, "w", encoding="utf-8") as fh:
            fh.write(content)
    except Exception as e:
        print(f"[save_string_to_txt] 保存失败: {e}")


def append_text_to_file(text: str, file_path: str) -> None:
    """在文件末尾追加文本（自动补换行）。"""
    if text and not text.startswith("\n"):
        text = "\n" + text
    try:
        with open(file_path, "a", encoding="utf-8") as fh:
            fh.write(text)
    except IOError as e:
        print(f"[append_text_to_file] 追加失败: {e}")


def clear_file_content(filename: str) -> None:
    """清空文件内容。"""
    try:
        with open(filename, "w", encoding="utf-8"):
            pass
    except IOError as e:
        print(f"[clear_file_content] 清空失败: {e}")


def save_data_to_json(data, file_path: str) -> bool:
    """将数据以 UTF-8 写入 JSON 文件，自动创建父目录。"""
    try:
        parent = os.path.dirname(os.path.abspath(file_path))
        os.makedirs(parent, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=4)
        return True
    except Exception as e:
        print(f"[save_data_to_json] 保存失败: {e}")
        return False


def load_json(file_path: str, default=None):
    """读取 JSON；文件缺失或格式错误时返回 default。"""
    try:
        with open(file_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def get_word_count(text: str) -> int:
    """中文按字符数统计。"""
    return len(text) if text else 0