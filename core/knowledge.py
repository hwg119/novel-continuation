# core/knowledge.py
# -*- coding: utf-8 -*-
"""把母本章节文件导入向量库。"""
import logging
import os
import traceback

from core.text_utils import split_sentences
from core.utils import read_file
from core.vectorstore import (
    init_vector_store, load_vector_store, replace_chapter_segments,
    add_segments,
)


def advanced_split_content(content: str, max_length: int = 500) -> list:
    """按句子聚合成不超过 max_length 的分段。"""
    sentences = split_sentences(content)
    if not sentences:
        return []

    segments = []
    current = []
    current_length = 0
    for sentence in sentences:
        length = len(sentence)
        if current_length + length > max_length:
            if current:
                segments.append(" ".join(current))
            current = [sentence]
            current_length = length
        else:
            current.append(sentence)
            current_length += length
    if current:
        segments.append(" ".join(current))
    return segments


def import_knowledge_file(embedding_api_key: str,
                          embedding_url: str,
                          embedding_interface_format: str,
                          embedding_model_name: str,
                          file_path: str,
                          filepath: str,
                          chapter_number: int = None) -> int:
    """把一个章节文件切段后写入向量库，返回新增分段数（失败返回 0）。

    ``chapter_number`` 传入时会写入 metadata，后续可按章替换。
    """
    if not os.path.exists(file_path):
        logging.warning("母本文件不存在: %s", file_path)
        return 0

    content = read_file(file_path)
    if not content.strip():
        logging.warning("母本文件内容为空: %s", file_path)
        return 0

    paragraphs = advanced_split_content(content)
    if not paragraphs:
        return 0

    from embedding_adapters import create_embedding_adapter
    adapter = create_embedding_adapter(
        embedding_interface_format,
        embedding_api_key,
        embedding_url if embedding_url else "http://localhost:11434/api",
        embedding_model_name,
    )

    store = load_vector_store(adapter, filepath)
    if not store:
        result = init_vector_store(adapter, paragraphs, filepath, chapter_number)
        if not result:
            logging.warning("向量库初始化失败，跳过 %s", file_path)
            return 0
        logging.info("向量库新建并写入 %d 段。", len(paragraphs))
        return len(paragraphs)

    try:
        added = add_segments(adapter, paragraphs, filepath, chapter_number)
        logging.info("向量库追加 %d 段。", added)
        return added
    except Exception as e:
        logging.warning("写入向量库失败: %s", e)
        traceback.print_exc()
        return 0


def replace_chapter_vector(embedding_api_key: str,
                           embedding_url: str,
                           embedding_interface_format: str,
                           embedding_model_name: str,
                           chapter_number: int,
                           file_path: str,
                           filepath: str,
                           chapter_text: str | None = None) -> int:
    """把单章向量整章替换：先按 chapter 删除旧段，再写入新段。

    用于「续写保存」/「重写已存章节」场景，确保库里不会出现同一章的两批 embedding。
    返回写入的新分段数（0 = 跳过/失败）。
    """
    if chapter_number is None:
        return 0
    if not os.path.exists(file_path):
        logging.warning("章节文件不存在: %s", file_path)
        return 0
    # 后台更新时优先使用保存瞬间捕获的正文，避免用户紧接着再次保存，
    # 导致前一个任务读到较新的文件内容。
    content = chapter_text if chapter_text is not None else read_file(file_path)
    if not content.strip():
        # 章节被清空 → 至少要把旧段删掉，避免历史脏数据残留。
        from embedding_adapters import create_embedding_adapter
        adapter = create_embedding_adapter(
            embedding_interface_format,
            embedding_api_key,
            embedding_url if embedding_url else "http://localhost:11434/api",
            embedding_model_name,
        )
        return 0 if replace_chapter_segments(adapter, "", chapter_number, filepath) else 0

    from embedding_adapters import create_embedding_adapter
    adapter = create_embedding_adapter(
        embedding_interface_format,
        embedding_api_key,
        embedding_url if embedding_url else "http://localhost:11434/api",
        embedding_model_name,
    )
    try:
        return replace_chapter_segments(adapter, content, chapter_number, filepath)
    except Exception as e:
        logging.warning("替换章节向量失败: %s", e)
        traceback.print_exc()
        return 0
