# -*- coding: utf-8 -*-
"""按英语水平生成章节学习版；产物与正文、向量库和 Wiki 完全隔离。"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


LEARNING_VERSION = 1
LEVELS = {
    "primary": {"label": "小学高年级", "coverage": 0.18, "max_chars": 36,
                "max_annotations": 3, "guide": "CEFR A1-A2，使用短句和高频基础词"},
    "junior": {"label": "初中", "coverage": 0.35, "max_chars": 58,
               "max_annotations": 3, "guide": "CEFR A2-B1，使用自然的常见叙事表达"},
    "senior": {"label": "高中", "coverage": 0.55, "max_chars": 90,
               "max_annotations": 2, "guide": "CEFR B1-B2，保留适度的小说语气和句式变化"},
}
SENTENCE_RE = re.compile(r".*?(?:[。！？!?…]+[”’』」]?|$)", re.DOTALL)
ENGLISH_RE = re.compile(r"[A-Za-z]")
CHINESE_RE = re.compile(r"[\u3400-\u9fff]")


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _path(project_dir: str, number: int, level: str) -> Path:
    directory = Path(project_dir) / "learning_editions"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"chapter_{int(number)}_{level}.json"


def _plain_length(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def _split_body(text: str) -> list[dict]:
    """保留自然段与句子；标题由正文页原有标题区域显示。"""
    _, separator, body = text.partition("\n")
    source = body if separator else text
    blocks = []
    segment_id = 0
    for paragraph_index, paragraph in enumerate(re.split(r"\n\s*\n", source)):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        segments = []
        for match in SENTENCE_RE.finditer(paragraph):
            sentence = match.group(0).strip()
            if not sentence:
                continue
            segment_id += 1
            segments.append({"id": f"s{segment_id}", "source": sentence,
                             "text": sentence, "translated": False,
                             "annotations": []})
        if segments:
            blocks.append({"index": paragraph_index, "segments": segments})
    return blocks


def _select_segments(blocks: list[dict], level: str) -> list[dict]:
    preset = LEVELS[level]
    groups = []
    total = 0
    for block in blocks:
        candidates = []
        for segment in block["segments"]:
            length = _plain_length(segment["source"])
            total += length
            if (4 <= length <= preset["max_chars"] and
                    CHINESE_RE.search(segment["source"])):
                # 短句优先；对话和明确动作句稍优先，减少翻译长篇解释句。
                dialogue_bonus = -5 if any(mark in segment["source"] for mark in ('“', '”', '：')) else 0
                candidates.append((length + dialogue_bonus, segment))
        if candidates:
            groups.append([segment for _, segment in sorted(candidates, key=lambda item: item[0])])
    target = max(1, int(total * preset["coverage"])) if total else 0
    selected = []
    covered = 0
    # 每轮从各自然段取一个较简单句，使英文在章节中均匀出现。
    depth = 0
    while covered < target:
        added = False
        for group in groups:
            if depth >= len(group):
                continue
            segment = group[depth]
            selected.append(segment)
            covered += _plain_length(segment["source"])
            added = True
            if covered >= target:
                break
        if not added:
            break
        depth += 1
    return selected


def _extract_array(raw: str) -> list:
    cleaned = re.sub(r"<think(?:ing)?>.*?</think(?:ing)?>", "", raw or "",
                     flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"```(?:json)?", "", cleaned).strip()
    match = re.search(r"\[.*\]", cleaned, flags=re.DOTALL)
    if not match:
        raise ValueError("模型未返回英语学习版 JSON 数组")
    payload = match.group(0)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        try:
            from json_repair import repair_json
            data = json.loads(repair_json(payload))
        except Exception as exc:
            raise ValueError("模型返回的英语学习版 JSON 无法解析") from exc
    if not isinstance(data, list):
        raise ValueError("模型返回的英语学习版格式不正确")
    return data


def _annotated_text(translation: str, annotations: list[dict]) -> str:
    result = translation
    # 较长词优先，避免短词先替换破坏长词匹配。
    for item in sorted(annotations, key=lambda row: len(row["word"]), reverse=True):
        word = item["word"]
        meaning = item["meaning"]
        pattern = re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE)
        result, count = pattern.subn(lambda match: f"{match.group(0)}（{meaning}）", result, count=1)
        if not count:
            continue
    return result


def _translate_batch(llm, rows: list[dict], level: str) -> dict[str, dict]:
    preset = LEVELS[level]
    source = json.dumps([{"id": row["id"], "source": row["source"]} for row in rows],
                        ensure_ascii=False)
    prompt = f"""把下列小说句子制作成英语分级阅读材料。
目标水平：{preset['label']}（{preset['guide']}）。

规则：
1. 每项必须逐句翻译，不得增删剧情、人物、态度、时态或因果关系。
2. 人名、地名和专有名词保持统一；没有可靠译名时使用一致的拼音，不要虚构别名。
3. translation 只能写完整自然的英文句子，不要夹入中文释义。
4. annotations 只标注超出目标水平、但理解本句确有帮助的词；最多 {preset['max_annotations']} 个。
5. annotations 每项包含 word 和简短中文 meaning；word 必须原样出现在 translation 中。
6. 高频基础词不要注释；没有难词时 annotations 返回空数组。
7. 只输出 JSON 数组，保持输入 id，不要输出解释或 Markdown。

输出格式：
[{{"id":"s1","translation":"...","annotations":[{{"word":"...","meaning":"..."}}]}}]

待处理句子：
{source}"""
    raw = llm.complete(prompt, system="你是严谨的分级英语读物编辑。",
                       temperature=0.2, num_predict=4096, disable_thinking=True)
    data = _extract_array(raw)
    result = {}
    expected = {row["id"] for row in rows}
    for item in data:
        if not isinstance(item, dict) or str(item.get("id") or "") not in expected:
            continue
        translation = str(item.get("translation") or "").strip()
        if (not translation or not ENGLISH_RE.search(translation) or
                CHINESE_RE.search(translation)):
            continue
        annotations = []
        for note in item.get("annotations") or []:
            if not isinstance(note, dict):
                continue
            word = str(note.get("word") or "").strip()
            meaning = str(note.get("meaning") or "").strip()
            if (word and meaning and CHINESE_RE.search(meaning) and
                    re.search(rf"\b{re.escape(word)}\b", translation,
                              flags=re.IGNORECASE)):
                annotations.append({"word": word, "meaning": meaning})
            if len(annotations) >= preset["max_annotations"]:
                break
        result[item["id"]] = {"translation": translation, "annotations": annotations}
    missing = expected - result.keys()
    if missing:
        raise ValueError(f"模型漏译了 {len(missing)} 个句子")
    return result


def read_learning_edition(project_dir: str, number: int, level: str,
                          chapter_text: str) -> dict:
    if level not in LEVELS:
        raise ValueError("英语学习级别无效")
    target = _path(project_dir, number, level)
    empty = {"chapter": number, "level": level, "level_label": LEVELS[level]["label"],
             "exists": False, "valid": False, "blocks": [], "stats": {}}
    if not target.is_file():
        return empty
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    valid = (data.get("version") == LEARNING_VERSION and
             data.get("source_hash") == _digest(chapter_text))
    return {**data, "exists": True, "valid": valid,
            "level_label": LEVELS[level]["label"]}


def generate_learning_edition(project_dir: str, number: int, chapter_text: str,
                              level: str, llm, progress=None) -> dict:
    if level not in LEVELS:
        raise ValueError("英语学习级别无效")
    blocks = _split_body(chapter_text)
    selected = _select_segments(blocks, level)
    if not selected:
        raise ValueError("本章没有适合生成英语学习版的中文句子")
    translated = {}
    batches = [selected[index:index + 18] for index in range(0, len(selected), 18)]
    for index, batch in enumerate(batches, 1):
        last_error = None
        for attempt in range(2):
            if progress:
                progress(f"正在翻译学习句组 {index}/{len(batches)}"
                         + (f"（重试 {attempt}）" if attempt else ""),
                         {"done": index - 1, "total": len(batches),
                          "attempt": attempt + 1})
            try:
                translated.update(_translate_batch(llm, batch, level))
                last_error = None
                break
            except ValueError as exc:
                last_error = exc
        if last_error:
            raise last_error
    translated_chars = 0
    annotation_count = 0
    for block in blocks:
        for segment in block["segments"]:
            item = translated.get(segment["id"])
            if not item:
                continue
            segment["translated"] = True
            segment["translation"] = item["translation"]
            segment["annotations"] = item["annotations"]
            segment["text"] = _annotated_text(item["translation"], item["annotations"])
            translated_chars += _plain_length(segment["source"])
            annotation_count += len(item["annotations"])
    total_chars = sum(_plain_length(segment["source"])
                      for block in blocks for segment in block["segments"])
    data = {
        "version": LEARNING_VERSION,
        "chapter": int(number),
        "level": level,
        "level_label": LEVELS[level]["label"],
        "source_hash": _digest(chapter_text),
        "blocks": blocks,
        "stats": {"segments": sum(len(block["segments"]) for block in blocks),
                  "translated_segments": len(selected),
                  "coverage": round(translated_chars / max(total_chars, 1), 3),
                  "annotations": annotation_count},
    }
    target = _path(project_dir, number, level)
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(target)
    if progress:
        progress("英语学习版已保存", data["stats"])
    return {**data, "exists": True, "valid": True}
