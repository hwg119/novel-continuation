# -*- coding: utf-8 -*-
"""按英语水平生成章节学习版；产物与正文、向量库和 Wiki 完全隔离。"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


LEARNING_VERSION = 3
LOADS = {
    'light': {'label': '轻量', 'coverage': .10, 'words': 5},
    'standard': {'label': '中量', 'coverage': .20, 'words': 8},
    'challenge': {'label': '挑战', 'coverage': .30, 'words': 12},
}
LEVELS = {
    "primary": {"label": "小学高年级", "max_chars": 36,
                "max_annotations": 3, "guide": "CEFR A1-A2，使用短句和高频基础词"},
    "junior": {"label": "初中", "max_chars": 58,
               "max_annotations": 3, "guide": "CEFR A2-B1，使用自然的常见叙事表达"},
    "senior": {"label": "高中", "max_chars": 90,
               "max_annotations": 2, "guide": "CEFR B1-B2，保留适度的小说语气和句式变化"},
}
SENTENCE_RE = re.compile(r".*?(?:[。！？!?…]+[”’』」]?|$)", re.DOTALL)
ENGLISH_RE = re.compile(r"[A-Za-z]")
CHINESE_RE = re.compile(r"[\u3400-\u9fff]")
SINGLE_ENGLISH_WORD_RE = re.compile(r"^[A-Za-z]+(?:'[A-Za-z]+)?$")


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _path(project_dir: str, number: int, level: str, load: str = 'light') -> Path:
    directory = Path(project_dir) / "learning_editions"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"chapter_{int(number)}_{level}_{load}.json"


def _uses_word_mode(level: str, load: str) -> bool:
    return level == "primary" and load in {"light", "standard"}


def _plain_length(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def _split_body(text: str) -> list[dict]:
    """保留自然段与句子；标题由正文页原有标题区域显示。"""
    _, separator, body = text.partition("\n")
    source = body if separator else text
    blocks = []
    segment_id = 0
    # Chapter files use one non-empty line per prose paragraph.  Splitting only
    # on blank lines collapses the whole chapter into one group and leaves the
    # "one selection per paragraph" policy with a single English item.
    for paragraph_index, paragraph in enumerate(re.split(r"\r?\n+", source)):
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


def _select_segments(blocks: list[dict], level: str, load: str = 'light') -> list[dict]:
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
    target = int(total * LOADS[load]["coverage"])
    selected = []
    covered = 0
    # 在整章各处取样，而不是把英文预算全部用在开头。
    ordered = []
    def spread(start, end):
        if start >= end: return
        middle = (start + end) // 2
        ordered.append(groups[middle])
        spread(start, middle)
        spread(middle + 1, end)
    spread(0, len(groups))
    groups = ordered
    # 每轮从各自然段取一个较简单句，使英文在章节中均匀出现。
    depth = 0
    while covered < target:
        added = False
        for group in groups:
            if depth >= len(group):
                continue
            segment = group[depth]
            length = _plain_length(segment['source'])
            if covered + length > target:
                continue
            selected.append(segment)
            covered += length
            added = True
            if covered >= target:
                break
        if not added:
            break
        depth += 1
        break  # 每个自然段最多一句英文，不为凑占比继续翻译。
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
   尽量用目标水平已知的常用词表达原意，不刻意引入难词；简化措辞但不能简化事实。
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


def _word_batch(llm, rows: list[dict]) -> dict[str, dict]:
    """Choose one concrete A1-A2 word per sentence for primary light/medium mode."""
    source = json.dumps([{"id": row["id"], "source": row["source"]} for row in rows],
                        ensure_ascii=False)
    prompt = f"""从每个中文小说句子中选择一个适合小学高年级学习的具体词，替换成一个英文单词。

规则：
1. 每项只选一个中文词，source_word 必须逐字出现在原句中，长度通常为1—4个汉字。
2. word 必须是单个 CEFR A1-A2 高频英文单词，不能包含空格，不能输出短语或完整句子。
3. 优先选择动作、物品、颜色、情绪等容易结合上下文理解的词；不要选择人名、地名、专有名词、助词、“行”“好”等含义依赖语气的单字。
4. 英文必须准确对应原句中的 source_word，不得改变原句事实。
5. 同一批次尽量不要重复相同英文单词。
6. 只输出 JSON 数组，保持输入 id，不要解释或输出 Markdown。

输出格式：
[{{"id":"s1","source_word":"门","word":"door"}}]

待处理句子：
{source}"""
    raw = llm.complete(prompt, system="你是小学英语词汇编辑，只输出 JSON 数组。",
                       temperature=0.15, num_predict=2600, disable_thinking=True)
    data = _extract_array(raw)
    expected = {row["id"]: row["source"] for row in rows}
    result = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "")
        source_word = str(item.get("source_word") or "").strip()
        word = str(item.get("word") or "").strip()
        if (item_id not in expected or source_word not in expected[item_id]
                or not CHINESE_RE.search(source_word)
                or not SINGLE_ENGLISH_WORD_RE.fullmatch(word)):
            continue
        result[item_id] = {"word": word, "source_word": source_word}
    missing = set(expected) - result.keys()
    if missing:
        raise ValueError(f"模型漏选了 {len(missing)} 个单词")
    return result


def read_learning_edition(project_dir: str, number: int, level: str,
                          chapter_text: str, load: str = 'light') -> dict:
    if level not in LEVELS:
        raise ValueError("英语学习级别无效")
    if load not in LOADS: raise ValueError('学习负担无效')
    target = _path(project_dir, number, level, load)
    empty = {"chapter": number, "level": level, "level_label": LEVELS[level]["label"],
             "exists": False, "valid": False, "blocks": [], "stats": {}}
    if not target.is_file():
        return empty
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    version_valid = (data.get("version") == LEARNING_VERSION or
                     (data.get("version") in {1, 2} and not _uses_word_mode(level, load)))
    valid = (version_valid and
             data.get("source_hash") == _digest(chapter_text))
    return {**data, "exists": True, "valid": valid,
            "level_label": LEVELS[level]["label"]}


def generate_learning_edition(project_dir: str, number: int, chapter_text: str,
                              level: str, llm, progress=None, load: str = 'light') -> dict:
    if level not in LEVELS:
        raise ValueError("英语学习级别无效")
    if load not in LOADS: raise ValueError('学习负担无效')
    blocks = _split_body(chapter_text)
    word_mode = _uses_word_mode(level, load)
    selected = _select_segments(blocks, level, load)
    if word_mode:
        # Ask for a small surplus so repeated/invalid choices can be skipped,
        # without translating dozens of sentences to keep only 5–8 words.
        selected = selected[:LOADS[load]['words'] * 2]
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
                translated.update(_word_batch(llm, batch) if word_mode
                                  else _translate_batch(llm, batch, level))
                last_error = None
                break
            except ValueError as exc:
                last_error = exc
        if last_error:
            raise last_error
    translated_chars = 0
    annotation_count = 0
    introduced = set()
    for block in blocks:
        for segment in block["segments"]:
            item = translated.get(segment["id"])
            if not item:
                continue
            if word_mode:
                word = item["word"]
                source_word = item["source_word"]
                key = word.casefold()
                if key in introduced or len(introduced) >= LOADS[load]['words']:
                    continue
                introduced.add(key)
                segment["translated"] = True
                segment["translation"] = word
                segment["annotations"] = [{"word": word, "meaning": source_word}]
                before, after = segment["source"].split(source_word, 1)
                segment["word_replacement"] = {
                    "before": before, "word": word,
                    "meaning": source_word, "after": after,
                }
                segment["text"] = before + word + after
                translated_chars += _plain_length(source_word)
                annotation_count += 1
                continue
            new_words = {note['word'].casefold() for note in item['annotations']} - introduced
            if len(introduced | new_words) > LOADS[load]['words']:
                continue  # 保留中文，不通过隐藏难词释义假装降低学习负担。
            introduced.update(new_words)
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
        "load": load,
        "load_label": LOADS[load]['label'],
        "level_label": LEVELS[level]["label"],
        "display_mode": "words" if word_mode else "sentences",
        "source_hash": _digest(chapter_text),
        "blocks": blocks,
        "stats": {"segments": sum(len(block["segments"]) for block in blocks),
                  "translated_segments": sum(s['translated'] for b in blocks for s in b['segments']),
                  "new_words": len(introduced),
                  "coverage": round(translated_chars / max(total_chars, 1), 3),
                  "annotations": annotation_count},
    }
    target = _path(project_dir, number, level, load)
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(target)
    if progress:
        progress("英语学习版已保存", data["stats"])
    return {**data, "exists": True, "valid": True}
