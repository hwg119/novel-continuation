# core/continuation.py
# -*- coding: utf-8 -*-
"""续写引擎：按“分幕”逐段生成一章，边生成边落盘。

小参数量本地模型（7B/8B）一次写整章容易陷入复读循环，因此把一章拆成若干
“幕”，每幕一次独立的 LLM 调用，并配合：

* 只带上一幕末尾 260 字作为衔接上下文（而非整章，避免回声复读）
* 禁写清单：把已写段落的开头列进提示词，明确禁止雷同
* 段落级 + 句子级去重
* Ollama 走原生 /api/chat，可传 repeat_penalty / think=false

每写完一幕就落盘一次，中断最多丢一幕。
"""
import json
import os
import re
import time
import urllib.request

from core.project_manager import chapter_path, chapters_dir, list_chapter_files
from core.utils import read_file


# ----------------------------- LLM 调用 -----------------------------

class ContinuationLLM:
    """续写统一调用入口：Ollama 走原生接口，其余走 llm_adapters。"""

    def __init__(self, llm_config: dict):
        self.cfg = dict(llm_config or {})
        self.fmt = str(self.cfg.get("interface_format", "")).strip().lower()
        self.is_ollama = self.fmt == "ollama"

    def complete(self, prompt: str, system: str = "",
                 temperature: float = None, num_predict: int = None,
                 disable_thinking: bool = False) -> str:
        temperature = (self.cfg.get("temperature", 0.8)
                       if temperature is None else temperature)
        if self.is_ollama:
            return self._ollama_chat(prompt, system, temperature, num_predict)
        return self._adapter_invoke(prompt, system, temperature, num_predict,
                                    disable_thinking=disable_thinking)

    # -- 原生 Ollama --------------------------------------------------

    def _ollama_root(self) -> str:
        url = str(self.cfg.get("base_url", "") or "http://localhost:11434").rstrip("/")
        for suffix in ("/v1", "/api"):
            if url.endswith(suffix):
                url = url[: -len(suffix)]
        return url

    def _ollama_chat(self, prompt, system, temperature, num_predict) -> str:
        payload = {
            "model": self.cfg.get("model_name", ""),
            "messages": [
                {"role": "system", "content": system or ""},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "think": False,
            "options": {
                "temperature": temperature,
                "top_p": 0.9,
                "repeat_penalty": 1.15,
                "repeat_last_n": 1024,
                "num_predict": int(num_predict or self.cfg.get("max_tokens", 4096)),
                "num_ctx": int(self.cfg.get("num_ctx", 8192) or 8192),
            },
        }
        req = urllib.request.Request(
            self._ollama_root() + "/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        timeout = int(self.cfg.get("timeout", 2400) or 2400)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return (data.get("message") or {}).get("content", "") or ""

    # -- 通用适配器 ----------------------------------------------------

    def _adapter_invoke(self, prompt, system, temperature, num_predict,
                        disable_thinking=False) -> str:
        from llm_adapters import create_llm_adapter
        model_name = str(self.cfg.get("model_name", "")).lower()
        extra_body = None
        if disable_thinking and self.fmt == "openai":
            if model_name == "minimax-m3" or model_name.startswith("mimo-v2.6-"):
                extra_body = {"thinking": {"type": "disabled"}}
            elif model_name.startswith(("qwen3.5-", "qwen3.6-", "qwen3.7-",
                                        "qwen3.8-", "deepseek-v4")):
                # 百炼 OpenAI 兼容接口要求此参数位于请求体顶层。
                extra_body = {"enable_thinking": False}
            elif model_name.startswith("glm-5.3"):
                # GLM 5.3 不能关闭思考；low 是结构化任务最接近关闭的模式。
                extra_body = {"reasoning_effort": "low"}
        adapter = create_llm_adapter(
            interface_format=self.cfg.get("interface_format", "OpenAI"),
            base_url=self.cfg.get("base_url", ""),
            model_name=self.cfg.get("model_name", ""),
            api_key=self.cfg.get("api_key", ""),
            temperature=temperature,
            max_tokens=int(num_predict or self.cfg.get("max_tokens", 4096)),
            timeout=int(self.cfg.get("timeout", 600) or 600),
            extra_body=extra_body,
        )
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        return adapter.invoke(full_prompt) or ""


# ----------------------------- 文本处理 -----------------------------

def read_style_sample(project_dir: str, settings: dict) -> str:
    """取一段语体样本。

    母本是英文时，读英文段落却要求「模仿语体写中文」是跨语言的；此时可用
    配置里的自定义中文语体锚（``style_sample.custom_text``），优先级高于母本章节。
    """
    sample = settings.get("style_sample") or {}
    length = int(sample.get("length") or 450)

    custom = (sample.get("custom_text") or "").strip()
    if custom:
        return custom[:length].strip()

    chapter = int(sample.get("chapter") or 0)
    start = int(sample.get("start") or 0)

    files = list_chapter_files(project_dir)
    if not files:
        return ""

    if chapter:
        path = chapter_path(project_dir, chapter)
        if not os.path.exists(path):
            path = files[-1][2]
    else:
        path = files[-1][2]

    text = read_file(path)
    if not text:
        return ""
    if start >= len(text):
        start = max(0, len(text) // 3)
    return text[start:start + length].strip()


def clean_text(text: str, settings: dict = None) -> str:
    """去掉思维链/markdown/拉丁噪声，按需转繁体。"""
    settings = settings or {}
    text = re.sub(r"<think(?:ing)?>.*?(?:</think(?:ing)?>|$)", "",
                  text or "", flags=re.DOTALL)
    text = text.replace("```", "").strip()

    lines = [ln for ln in text.splitlines() if ln.strip()]
    if lines and re.match(r"^第\s*[0-9一二三四五六七八九十百千○零〇两]+\s*[回章节]", lines[0].strip()):
        lines = lines[1:]
    text = "\n".join(lines)

    text = re.sub(r"[*#_`~]+", "", text)
    text = re.sub(r"[（(]\s*[A-Za-z]+\s*[）)]", "", text)
    text = re.sub(r"\s*[A-Za-z][A-Za-z0-9'’\-\s]*", "", text)

    if settings.get("traditional"):
        try:
            import zhconv
            text = zhconv.convert(text, "zh-hant").replace("裡", "裏")
        except Exception:
            pass

    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"^[ \t　]+", "", text, flags=re.MULTILINE)
    return text.strip()


def paragraphs(text: str) -> list:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def dedupe(paras: list) -> tuple:
    """段落级 + 句子级去重，返回 (保留段落, 丢弃数量)。"""
    seen_head = set()
    seen_sent = set()
    seen_dialogue = set()
    out = []
    dropped = 0
    for p in paras:
        flat = re.sub(r"\s+", "", p)
        head = flat[:16]
        if head and head in seen_head:
            dropped += 1
            continue
        dialogue = [re.sub(r"\s+", "", item) for item in
                    re.findall(r'[“\"]([^”\"]{8,})[”\"]', flat)]
        if any(item in seen_dialogue for item in dialogue):
            dropped += 1
            continue
        kept = []
        for s in re.split(r"(?<=[。！？])", p):
            s2 = re.sub(r"\s+", "", s)
            if len(s2) >= 8:
                if s2 in seen_sent:
                    dropped += 1
                    continue
                seen_sent.add(s2)
            if s2:
                kept.append(s)
        seen_dialogue.update(dialogue)
        body = "".join(kept).strip()
        if not body:
            continue
        seen_head.add(head)
        out.append(body)
    return out, dropped


def _planned_locations(description: str) -> list[str]:
    """从结构化分幕描述的“地点/场景”字段提取校验锚点。"""
    matches = re.findall(r"(?:地点|场景)\s*[:：]\s*([^】\n；。]+)", description or "")
    locations = []
    for value in matches:
        for item in re.split(r"[、,/，]|(?:\s+(?:与|和|及)\s+)", value):
            item = item.strip(" ｜|：:，,；;。")
            if len(item) >= 2:
                locations.append(item)
    return list(dict.fromkeys(locations))


def validate_beat_output(paras: list, beat: dict, next_beat: dict | None = None) -> list[str]:
    """生成后做轻量质量检查；只告警、记录，不擅自改写正文。"""
    text = "\n".join(paras)
    if not text.strip():
        return ["本幕正文为空，需要重新生成"]
    desc = str(beat.get("desc", "") or "")
    expected = _planned_locations(desc)
    warnings = [f"本幕预期地点未出现：{term}" for term in expected if term not in text]
    if next_beat:
        next_desc = str(next_beat.get("desc", "") or "")
        for term in _planned_locations(next_desc):
            if term not in expected and term in text:
                warnings.append(f"疑似提前进入下一幕地点：{term}")
    return warnings


def forbidden_list(paras: list) -> str:
    items = [re.sub(r"\s+", "", p)[:14] for p in paras[-14:]]
    return "\n".join(f"  - {it}…" for it in items) if items else "  (暂无)"


# ----------------------------- 提示词 -----------------------------

def build_beat_prompt(beat: dict, all_beats: list, written_paras: list,
                      style: str, settings: dict, chars_per_beat: int,
                      retrieved_context: str = "",
                      previous_summaries: list = None,
                      previous_chapter_tail: str = "") -> str:
    num = beat.get("num", "")
    name = beat.get("name", "")
    desc = beat.get("desc", "")

    outline = "\n".join(
        f"{b.get('num', '')}、{b.get('name', '')}：{(b.get('desc', '') or '')[:40]}…"
        for b in all_beats
    )
    if written_paras:
        # 章内衔接保留上一幕末尾的完整自然段。早期 260 字适合小模型防复读，
        # 但容易丢失一幕内较早出现的道具、决定和人物状态；约 1000 字更稳妥。
        recent_paras = []
        recent_chars = 0
        for paragraph in reversed(written_paras):
            addition = len(paragraph) + (2 if recent_paras else 0)
            if recent_paras and recent_chars + addition > 1000:
                break
            recent_paras.append(paragraph)
            recent_chars += addition
            if recent_chars >= 1000:
                break
        tail = "\n\n".join(reversed(recent_paras))
    else:
        tail = "(本幕为全章开头)"

    # 前几章摘要：按章号倒序拼成一段，标清楚是第几章
    summaries_block = ""
    if previous_summaries:
        lines = [f"  - 第{n}章：{s}" for n, s in previous_summaries]
        summaries_block = "\n【前几章剧情摘要】（保证跨章剧情连贯，请勿复述或改写其内容）\n" + "\n".join(lines) + "\n"

    previous_tail_block = ""
    if not written_paras and previous_chapter_tail.strip():
        previous_tail_block = (
            "\n【上一章正文结尾】（第一幕必须直接承接；不得复述、改写或跳过其未完成动作）\n"
            + previous_chapter_tail.strip()[-2400:] + "\n"
        )

    wiki_block = ""
    wiki_history = str(settings.get("_wiki_history_context") or "").strip()
    if wiki_history:
        wiki_block = (
            "\n【Wiki 连续性证据包】（只用于核对事实，不是本幕必须复述的素材）\n"
            "优先级低于本章已写正文、上一章正文结尾和有效摘要。只采用与本幕人物、地点、"
            "物品或未解决线索直接相关的条目；不得为了用上 Wiki 而生硬提及旧事。\n"
            "state/近期状态只证明对应章节当时如此；event/已发生事件只能作为历史；"
            "relationship 可能变化；stable 才可在无冲突时视为持续设定。"
            "不同条目冲突时采用章节较新的记录。绝不可把‘当前是否未知’补写成确定事实。\n"
            + wiki_history[:2200] + "\n"
        )

    # 术语表：强制固定译名，避免「翻倒巷」被写成「翻侧巷子」这类漂移
    glossary_block = ""
    glossary = settings.get("glossary") or {}
    if isinstance(glossary, dict):
        items = []
        for zh, en in glossary.items():
            zh = str(zh or "").strip()
            if not zh:
                continue
            en = str(en or "").strip()
            items.append(f"  - {zh}（{en}）" if en else f"  - {zh}")
        if items:
            glossary_block = ("\n【术语表】（必须原样使用以下固定译名，不得改写、不得另造译名）\n"
                              + "\n".join(items) + "\n")

    is_last = all_beats and num == all_beats[-1].get("num")
    current_index = next((i for i, item in enumerate(all_beats) if item is beat), -1)
    next_beat = all_beats[current_index + 1] if 0 <= current_index < len(all_beats) - 1 else None
    next_beat_rule = ""
    if next_beat:
        next_beat_rule = (
            f"不得进入下一幕「{next_beat.get('name', '')}」的场景或展开其事件"
            f"（下一幕任务：{next_beat.get('desc', '')}）。"
        )
    closing = settings.get("closing_formula", "")
    if is_last and closing:
        end_rule = f"本幕为全章末幕：末句必须以「{closing}」作结。"
    elif is_last:
        end_rule = "本幕为全章末幕：写足收束气象，自然收尾。"
    else:
        end_rule = "本幕写完即止，不要总结全章，不要写结束语。"

    script_rule = "全文使用繁体字" if settings.get("traditional") else "全文使用简体字，须与母本一致"

    voices = settings.get("character_voices", "").strip()
    voices_line = f"4. 人物口吻：{voices}\n" if voices else ""

    forbidden_words = settings.get("forbidden_words") or []
    forbid_line = ""
    if forbidden_words:
        forbid_line = "11. 严禁出现以下词句：" + "、".join(forbidden_words) + "\n"

    extra = settings.get("extra_requirements", "").strip()
    extra_line = f"\n【附加要求】\n{extra}\n" if extra else ""

    retrieved_block = ""
    if retrieved_context and retrieved_context.strip():
        retrieved_block = (
            f"\n【母本相关片段】（较早的历史参考；只用于补充人物/设定/世界观细节，"
            f"不得直接抄写，也不得覆盖最近章节已经发生的变化）\n{retrieved_context.strip()[:1200]}\n"
        )

    return f"""【本书背景】
{settings.get('background', '') or '(未填写)'}

【本章回目】
{settings.get('chapter_title', '') or '(未填写)'}
【本章目标】
{settings.get('chapter_brief', '') or '(按分幕大纲推进)'}
【本章专属要求】
{settings.get('chapter_requirements', '') or '(无)'}
{glossary_block}
【上下文优先级】
本章任务与专属要求 > 已写正文末尾与最近章节摘要 > Wiki 历史依据 > 母本相关片段。
历史资料只说明过去曾经如此；若没有较晚原文确认，不得写成当前仍然成立。

【本章分幕大纲】（仅供掌握全局，已写部分切勿重述）
{outline}

【语体样本】（只学习句法、节奏和氛围；严禁复用其中任何原句、对白、人物动作、地点或情节顺序）
{style or '(未提供)'}{retrieved_block}

【已写正文的末尾】（须自然衔接，但不得复述其字句）
{tail}
{previous_tail_block}{summaries_block}{wiki_block}

【已写过的段落开头（禁写清单，严禁写出雷同句子）】
{forbidden_list(written_paras)}

【本幕任务】第{num}幕「{name}」：{desc}

【硬性要求】
1. 只写本幕内容，约 {chars_per_beat} 字。{end_rule}{next_beat_rule}
2. {script_rule}；标点使用中文标点。
3. 严禁重复：不得与禁写清单雷同；同一景物描写、同一段对话不得出现两次。
{voices_line}5. 人名、称谓前后一致，不可张冠李戴。
6. 只准汉字与中文标点：严禁拉丁字母、英文单词、markdown 符号（* # _ ` 等）、括号注记。
7. 直接输出正文段落，不要标题、不要解释、不要 markdown。
8. 必须完整落实【本幕任务】中描述的全部要素（人物、地点、事件、动作），一个都不能遗漏。
9. 严禁引入【本章分幕大纲】与【本书背景】之外的人物、地点、机构、事件；不得编造原著中不存在的地名、机构名或道具名。
10. 若【本幕任务】要求出现搭档、任务、地点或道具，必须在正文中明确交代，不得回避、不得推迟到后续幕。
{forbid_line}{extra_line}"""


# ----------------------------- 生成主流程 -----------------------------

def retrieve_context_for_beat(project_dir: str, embedding_cfg: dict,
                              query: str, k: int, glossary: dict = None,
                              source_query: str = "",
                              corpus_is_english: bool = True,
                              glossary_hard_terms=None, return_details: bool = False):
    """从工程向量库检索与 query 最相关的 k 段母本片段，返回拼接文本。

    失败时返回空字符串（续写流程可以容忍检索为空）。
    """
    if not query or not query.strip():
        return ("", {}) if return_details else ""
    try:
        from embedding_adapters import create_embedding_adapter
        from core.vectorstore import glossary_terms_for_query, hybrid_search
        adapter = create_embedding_adapter(
            embedding_cfg.get("interface_format", ""),
            embedding_cfg.get("api_key", ""),
            embedding_cfg.get("base_url", "") or "http://localhost:11434/api",
            embedding_cfg.get("model_name", ""),
        )
        required_terms, optional_terms = glossary_terms_for_query(
            query, glossary, source_query=source_query,
            corpus_is_english=corpus_is_english,
            hard_terms=glossary_hard_terms,
        )
        results = hybrid_search(
            adapter, query, project_dir, k=k,
            required_terms=required_terms,
            optional_terms=optional_terms,
        )
        context = "\n".join(row["text"] for row in results)[:2000]
        details = {
            "query": query,
            "source_query": source_query,
            "required_terms": required_terms,
            "optional_terms": optional_terms,
            "results": [{
                key: row.get(key) for key in (
                    "id", "chapter", "seg_index", "score", "semantic_score",
                    "keyword_bm25", "constraint_mode", "matched_terms",
                )
            } | {"text_excerpt": (row.get("text") or "")[:180]} for row in results],
        }
        return (context, details) if return_details else context
    except Exception as e:
        logging = __import__("logging")
        logging.warning("续写检索母本失败：%s", e)
        return ("", {"error": str(e)}) if return_details else ""


# ----------------------------- 检索前翻译 -----------------------------

# 母本是英文原著时，中文 query 直接检索会有明显的跨语言语义损失
# （实测余弦相似度掉约 0.18）。先把 query 译成英文再检索可挽回约八成。
TRANSLATE_PROMPT = """把下面的中文检索意图翻译成英文，用于在英文原著语料中做语义检索。

要求：
1. 只输出一行英文译文，不要解释、不要引号、不要 markdown。
2. 人名、地名等专有名词使用该作品通行的英文或原文拼写；不确定时保持原词，不要臆造译名。
3. 保留关键名词、动作与场景，不要概括、不要扩写。

【中文】
{text}
"""


def translate_query_to_english(llm, text: str, max_chars: int = 300) -> str:
    """把中文检索 query 译成英文；失败返回空字符串，调用方回退用中文检索。"""
    if not text or not text.strip():
        return ""
    prompt = TRANSLATE_PROMPT.format(text=text.strip()[:800])
    try:
        raw = llm.complete(prompt, system="你是翻译，只输出英文译文。",
                           temperature=0.1, num_predict=max(64, max_chars))
        out = (raw or "").strip().strip("\"'`").strip()
        out = out.splitlines()[0].strip() if out else ""
        return out[:max_chars]
    except Exception as e:
        logging = __import__("logging")
        logging.warning("检索 query 翻译失败：%s", e)
        return ""


# ----------------------------- 摘要工具 -----------------------------

SUMMARY_PROMPT = """请把下面这段小说正文压缩为不超过 {max_chars} 字的中文剧情摘要。

要求：
1. 只写客观事件和结果，不写修饰语。
2. 保留人物姓名、关键地点、关键物品。
3. 时态保持过去式，不评价、不总结。
4. 不输出 markdown、标题、引号，直接写摘要文本。
5. 不得猜测或补造未明确出现的咒语、人物状态、死亡、动机或事件原因；不确定则省略。

【正文】
{text}
"""


def _chapter_summary_path(project_dir: str, chapter_number: int) -> str:
    return os.path.join(chapters_dir(project_dir), f"chapter_summary_{chapter_number}.txt")


def write_chapter_summary(project_dir: str, chapter_number: int,
                          summary_text: str) -> None:
    """持久化单章摘要到 chapters/chapter_summary_N.txt。"""
    path = _chapter_summary_path(project_dir, chapter_number)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(summary_text.strip() + "\n")


def validate_chapter_summary(summary_text: str) -> list[str]:
    """检查摘要是否能安全作为后续规划上下文。"""
    text = str(summary_text or "").strip()
    if not text:
        return ["摘要为空"]
    issues = []
    if re.search(r"<think(?:ing)?>|</think(?:ing)?>", text, flags=re.IGNORECASE):
        issues.append("摘要包含模型思考内容")
    chinese = len(re.findall(r"[\u3400-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if chinese < 12 or latin > max(24, chinese):
        issues.append("摘要缺少有效中文剧情")
    if text[-1:] not in "。！？…’”）】":
        issues.append("摘要末句不完整")
    return issues


def _normalize_chapter_summary(raw: str, max_chars: int) -> str:
    """清除推理与格式，并只在完整句边界内截断。"""
    text = clean_text(raw or "")
    text = re.sub(r"^\s*(?:剧情)?摘要\s*[：:]\s*", "", text).strip()
    text = re.sub(r"\s+", "", text)
    if len(text) > max_chars:
        prefix = text[:max_chars]
        ends = [match.end() for match in re.finditer(r"[。！？…]+[’”）】]?", prefix)]
        text = prefix[:ends[-1]] if ends and ends[-1] >= max(20, max_chars // 2) else ""
    return text.strip()


def read_chapter_summary(project_dir: str, chapter_number: int) -> str:
    """读取有效的 chapter_summary_N.txt；损坏或不存在均返回 None。"""
    path = _chapter_summary_path(project_dir, chapter_number)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read().strip()
            return None if validate_chapter_summary(text) else text
    except Exception:
        return None


def generate_chapter_summary(llm: "ContinuationLLM", chapter_text: str,
                             max_chars: int = 150) -> str:
    """让 LLM 把整章正文压缩为 ≤max_chars 字摘要；失败返回空字符串。"""
    if not chapter_text or not chapter_text.strip():
        return ""
    text = chapter_text.strip()
    # 原著末章常在结尾附有尾声；只取开头会遗漏决定续写时间线的关键信息。
    excerpt = text if len(text) <= 6000 else text[:3000] + "\n\n【章节结尾】\n" + text[-3000:]
    prompt = SUMMARY_PROMPT.format(max_chars=max_chars, text=excerpt)
    try:
        for attempt in range(2):
            request = prompt if not attempt else (
                "上次摘要无效。请只输出完整的中文剧情摘要，必须以完整句号结束，"
                "不要输出分析、思考过程、标题或 Markdown。\n\n" + prompt)
            raw = llm.complete(
                request, system="你是小说编辑，只输出中文剧情摘要，不输出思考过程。",
                temperature=0.3 if not attempt else 0.1,
                num_predict=max_chars * 3, disable_thinking=True)
            summary = _normalize_chapter_summary(raw, max_chars)
            if not validate_chapter_summary(summary):
                return summary
        return ""
    except Exception as e:
        logging = __import__("logging")
        logging.warning("生成章节摘要失败：%s", e)
        return ""


def load_previous_summaries(project_dir: str, before_chapter: int) -> list:
    """加载所有 chapter_summary_*.txt（章号 < before_chapter），按章号升序返回 [(num, summary), ...]。

    若没有摘要文件，自动增量生成：
      - 找到 chapters/chapter_N.txt 中章号最大的若干个，逐个调用 LLM 摘要并写盘。
      - 增量完成后再次扫描取 before_chapter 之前的全部。
    """
    # 先扫一遍已存在的
    out = _scan_existing_summaries(project_dir, before_chapter)
    if out:
        return out

    # 没找到就触发一次「补齐紧邻上一章」摘要，避免旧事件污染续写上下文。
    try:
        from core.project_manager import chapters_dir as _cd, list_chapter_files
        chap_dir = _cd(project_dir)
        existing_files = list_chapter_files(project_dir)
        candidates = [(n, p) for n, _name, p in existing_files if n < before_chapter]
        candidates.sort(key=lambda x: x[0])
        if not candidates:
            return []
        # 只取最近一章补齐
        to_fill = candidates[-1:]
        # 用 settings 没有就跳过（避免在 settings 缺失时报错）；实际生成由调用方在
        # generate_chapter 里已经准备好了 llm 和 progress，这里只做兜底懒加载
    except Exception:
        pass
    return _scan_existing_summaries(project_dir, before_chapter)


def _scan_existing_summaries(project_dir: str, before_chapter: int) -> list:
    """纯扫描：返回 [(num, summary), ...] 升序。"""
    chap_dir = chapters_dir(project_dir)
    if not os.path.isdir(chap_dir):
        return []
    import re
    pattern = re.compile(r"^chapter_summary_(\d+)\.txt$")
    out = []
    for name in os.listdir(chap_dir):
        m = pattern.match(name)
        if not m:
            continue
        n = int(m.group(1))
        if n >= before_chapter:
            continue
        s = read_chapter_summary(project_dir, n)
        if s:
            out.append((n, s))
    out.sort(key=lambda x: x[0])
    return out


def save_chapter(project_dir: str, chapter_number: int, title: str, body: str) -> str:
    """把正文写入 chapters/chapter_N.txt，返回文件路径。"""
    path = chapter_path(project_dir, chapter_number)
    header = (title or f"第{chapter_number}章").strip()
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"{header}\n\n{body.strip()}\n")
    return path


def generate_chapter(project_dir: str, settings: dict, llm: ContinuationLLM,
                     chapter_number: int = None, chapter_title: str = None,
                     beats: list = None, chars_per_beat: int = None,
                     temperature: float = None, embedding_cfg: dict = None,
                     progress=None) -> dict:
    """逐幕生成一章，返回统计信息。

    ``embedding_cfg`` 若提供且 ``settings.get('retrieval_in_continuation')`` 为 True，
    每幕会先从工程向量库检索最相关的母本片段塞进 prompt。

    流程：
      1. 启用前章摘要：扫描已有 chapter_summary_*.txt，缺的按需补齐
      2. 每幕循环：RAG 检索 → 拼 prompt → LLM 生成 → 清洗去重 → 落盘
      3. 整章结束后：把整章正文生成 ≤max_chars 摘要写入 chapter_summary_N.txt
    """
    beats = beats or settings.get("beats") or []
    if not beats:
        raise ValueError("没有分幕大纲（beats）。请先在“续写设定”里填写至少一幕。")

    chars_per_beat = int(chars_per_beat or settings.get("chars_per_beat", 600))
    chapter_number = int(chapter_number or settings.get("chapter_number", 101))
    chapter_title = chapter_title or settings.get("chapter_title", "") \
        or f"第{chapter_number}章"

    from core.run_log import GenerationRunLogger
    run_log = GenerationRunLogger(project_dir, chapter_number)
    run_log.write(
        "run_started", chapter_number=chapter_number, chapter_title=chapter_title,
        model=getattr(llm, "cfg", {}).get("model_name", ""),
        temperature=temperature, beats=len(beats),
    )

    style = read_style_sample(project_dir, settings)
    local_settings = dict(settings)
    local_settings["chapter_title"] = chapter_title

    # 是否启用 RAG 检索
    rag_enabled = bool(settings.get("retrieval_in_continuation", False)) and bool(embedding_cfg)
    rag_k = int(settings.get("retrieval_k_for_continuation", 3) or 3)

    # 母本为英文时，检索前把中文 query 译成英文（跨语言检索有损，翻译可挽回大部分）
    corpus_is_english = bool(settings.get("corpus_is_english", False))
    trans_cache = {}

    # 是否启用前章摘要
    summary_enabled = bool(settings.get("use_previous_summaries", True))
    summary_max_chars = int(settings.get("summary_chars", 150) or 150)

    # 一次性补齐缺失的最近几章摘要
    previous = []
    if summary_enabled:
        previous = _scan_existing_summaries(project_dir, chapter_number)
        # 找出 chapter_N.txt 中章号 < 当前 的紧邻章节；只为它补摘要。
        from core.project_manager import list_chapter_files
        existing = [(n, p) for n, _name, p in list_chapter_files(project_dir)
                    if n < chapter_number]
        existing.sort(key=lambda x: x[0])
        have_nums = {n for n, _ in previous}
        missing = [(n, p) for n, p in existing[-1:] if n not in have_nums]
        for n, p in missing:
            if progress:
                progress("summary_fill", {"index": n, "total": len(missing)})
            text = read_file(p)
            if text:
                summ = generate_chapter_summary(llm, text, summary_max_chars)
                if summ:
                    write_chapter_summary(project_dir, n, summ)
                    run_log.write("summary_filled", chapter_number=n, chars=len(summ))
        previous = _scan_existing_summaries(project_dir, chapter_number)

    # 第一幕直接看上一章原文结尾；后续幕改用当前章已写正文末尾，避免重复携带。
    previous_chapter_tail = ""
    if chapter_number > 1:
        previous_path = chapter_path(project_dir, chapter_number - 1)
        if os.path.isfile(previous_path):
            previous_chapter_tail = read_file(previous_path)[-2000:]

    written = []
    beat_drafts = []
    total_dropped = 0
    total_retrieved_chars = 0

    last_summary = previous[-1][1] if previous else ""

    for i, beat in enumerate(beats, 1):
        # 检索 query 只用本幕意图；上一章摘要仅供生成衔接，不能污染召回。
        retrieved = ""
        retrieval_details = {}
        if rag_enabled:
            beat_desc = beat.get("desc", "") or ""
            query_parts = [chapter_title, beat_desc]
            query = " ".join(p for p in query_parts if p).strip()
            source_query = query
            if corpus_is_english:
                if query not in trans_cache:
                    if progress:
                        progress("translate_start", {"chars": len(query)})
                    trans_cache[query] = translate_query_to_english(llm, query)
                translated = trans_cache[query]
                if translated:
                    query = translated
            retrieved, retrieval_details = retrieve_context_for_beat(
                project_dir, embedding_cfg, query, rag_k,
                glossary=settings.get("glossary") or {}, source_query=source_query,
                corpus_is_english=corpus_is_english,
                glossary_hard_terms=settings.get("glossary_hard_terms") or [],
                return_details=True,
            )
            total_retrieved_chars += len(retrieved)
            run_log.write("retrieval_completed", beat_index=i,
                          beat_name=beat.get("name", ""), **retrieval_details)

        from core.auto_wiki import wiki_context
        beat_settings = dict(local_settings)
        # 只用本幕任务检索。章标题常覆盖另一条人物线，会把案件事实塞进校园幕。
        wiki_query = str(beat.get("desc", "") or "")
        wiki, wiki_details = wiki_context(
            project_dir, wiki_query, before_chapter=chapter_number, return_details=True)
        if wiki:
            beat_settings["_wiki_history_context"] = wiki
        run_log.write("wiki_context", beat_index=i, beat_name=beat.get("name", ""),
                      context=wiki, **wiki_details)
        prompt = build_beat_prompt(
            beat, beats, written, style, beat_settings, chars_per_beat,
            retrieved_context=retrieved,
            previous_summaries=previous[-3:] if summary_enabled else None,
            previous_chapter_tail=previous_chapter_tail if i == 1 else "",
        )
        if progress:
            progress("beat_start", {"index": i, "total": len(beats),
                                    "num": beat.get("num", ""),
                                    "name": beat.get("name", "")})
        llm_started = time.monotonic()
        retry_count = max(0, int(local_settings.get("beat_retry_count", 2) or 0))
        min_beat_chars = int(local_settings.get("min_beat_chars", 0) or 0)
        if min_beat_chars <= 0:
            # 默认约为目标篇幅的六分之一，既能挡住空答/残答，也允许短收束幕。
            min_beat_chars = max(40, min(160, chars_per_beat // 6))
        paras = []
        dropped = 0
        last_raw_chars = 0
        last_clean_chars = 0
        try:
            for attempt in range(retry_count + 1):
                request_prompt = prompt
                if attempt:
                    request_prompt += (
                        "\n\n【重新生成】上一次回答在移除思考过程、标题和格式标记后没有足够的小说正文。"
                        f"请重新完成本幕，至少写 {min_beat_chars} 个中文字符。"
                        "只输出本幕小说正文，不要分析、解释、标题、提纲、Markdown 或思考过程。"
                    )
                raw = llm.complete(
                    request_prompt,
                    system=local_settings.get("system_prompt", ""),
                    temperature=temperature if not attempt else min(
                        float(temperature if temperature is not None else 0.8), 0.5),
                    num_predict=int(chars_per_beat * 2.6),
                    # 小说正文不需要暴露推理过程；从首次调用起就关闭 thinking。
                    # 这也避免推理占满输出额度，清洗后只剩空正文。
                    disable_thinking=True,
                )
                text = clean_text(raw, local_settings)
                candidate, candidate_dropped = dedupe(paragraphs(text))
                candidate_chars = sum(len(p) for p in candidate)
                last_raw_chars = len(raw or "")
                last_clean_chars = candidate_chars
                if candidate_chars >= min_beat_chars:
                    paras, dropped = candidate, candidate_dropped
                    break
                run_log.write(
                    "beat_output_rejected", beat_index=i,
                    beat_name=beat.get("name", ""), attempt=attempt + 1,
                    raw_chars=last_raw_chars, cleaned_chars=candidate_chars,
                    min_chars=min_beat_chars,
                )
                if attempt < retry_count and progress:
                    progress("beat_retry", {
                        "index": i, "total": len(beats), "attempt": attempt + 2,
                        "max_attempts": retry_count + 1, "cleaned_chars": candidate_chars,
                    })
        except Exception as e:
            run_log.write("beat_failed", beat_index=i, beat_name=beat.get("name", ""),
                          elapsed_ms=round((time.monotonic() - llm_started) * 1000), error=str(e))
            run_log.write("run_failed", error=str(e))
            raise
        if not paras:
            error = (f"第 {i} 幕“{beat.get('name', '')}”连续 {retry_count + 1} 次未返回有效正文"
                     f"（清洗前 {last_raw_chars} 字符，清洗后 {last_clean_chars} 字符，"
                     f"最低要求 {min_beat_chars} 字符）")
            run_log.write("beat_failed", beat_index=i, beat_name=beat.get("name", ""),
                          elapsed_ms=round((time.monotonic() - llm_started) * 1000), error=error)
            run_log.write("run_failed", error=error)
            raise ValueError(error)
        total_dropped += dropped
        written.extend(paras)
        next_beat = beats[i] if i < len(beats) else None
        quality_warnings = validate_beat_output(paras, beat, next_beat)
        beat_drafts.append({
            "beat_index": i,
            "num": beat.get("num", ""),
            "name": beat.get("name", ""),
            "desc": beat.get("desc", ""),
            "text": "\n\n".join(paras),
            "issues": quality_warnings,
            "status": "issues" if quality_warnings else "passed",
        })

        # 每幕落盘一次。
        body = "\n\n".join(written)
        save_chapter(project_dir, chapter_number, chapter_title, body)
        run_log.write(
            "beat_completed", beat_index=i, beat_name=beat.get("name", ""),
            elapsed_ms=round((time.monotonic() - llm_started) * 1000),
            generated_chars=sum(len(p) for p in paras), total_chars=len(body),
            dropped_duplicates=dropped, retrieved_chars=len(retrieved),
            quality_warnings=quality_warnings,
        )

        if progress:
            progress("beat_done", {
                "index": i, "total": len(beats),
                "beat_chars": sum(len(p) for p in paras),
                "total_chars": sum(len(p) for p in written),
                "dropped": dropped,
                "retrieved_chars": len(retrieved) if retrieved else 0,
                "quality_warnings": quality_warnings,
            })

    body = "\n\n".join(written)
    path = save_chapter(project_dir, chapter_number, chapter_title, body)

    # 生成并写入本章节摘要
    new_summary = ""
    if summary_enabled:
        if progress:
            progress("summary_start", {"chapter": chapter_number})
        new_summary = generate_chapter_summary(llm, body, summary_max_chars)
        if new_summary:
            write_chapter_summary(project_dir, chapter_number, new_summary)
            run_log.write("summary_created", chapter_number=chapter_number, chars=len(new_summary))
            if progress:
                progress("summary_done", {"chapter": chapter_number, "chars": len(new_summary)})

    run_log.write("run_completed", chars=len(body), beats=len(beats),
                  retrieved_chars=total_retrieved_chars, summary_chars=len(new_summary))
    return {
        "chapter_number": chapter_number,
        "chapter_title": chapter_title,
        "path": path,
        "text": body,
        "chars": len(body),
        "beats": len(beats),
        "dropped": total_dropped,
        "retrieved_chars": total_retrieved_chars,
        "summary_chars": len(new_summary) if new_summary else 0,
        "run_log_path": run_log.path,
        "beat_drafts": beat_drafts,
    }


def suggest_beats(settings: dict, llm: ContinuationLLM, num_beats: int = 6) -> list:
    """让模型根据背景与回目给出分幕大纲，返回 [{num,name,desc}]。"""
    prompt = f"""请为下面这一章续写内容设计 {num_beats} 幕的分幕大纲。

【本书背景】
{settings.get('background', '')}

【本章回目】
{settings.get('chapter_title', '')}

要求：
1. 每一幕推进一个新事件，避免各幕内容重复。
2. 输出严格的 JSON 数组，元素形如 {{"num": "一", "name": "四字幕名", "desc": "本幕要写的内容，60字内"}}。
3. 只输出 JSON，不要任何解释、不要 markdown 代码块。"""
    raw = llm.complete(prompt, system="你是小说结构编辑，只输出 JSON。",
                       temperature=0.7, num_predict=1200, disable_thinking=True)
    return _parse_beats(raw, num_beats)


def _first_json_object(raw: str) -> dict:
    """跳过说明或推理文本，返回回复中首个可完整解码的 JSON 对象。"""
    text = re.sub(r"<think(?:ing)?>.*?</think(?:ing)?>", "", raw or "",
                  flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"```(?:json)?", "", text).strip()
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("模型未返回章节规划 JSON。")


def suggest_chapter_plan(settings: dict, llm: ContinuationLLM, num_beats: int,
                         previous_context: str = "", wiki_history: str = "") -> dict:
    """为新章提出回目和分幕；只返回建议，不自动覆盖已保存计划。"""
    prompt = f"""你是长篇小说的章节结构编辑。请根据全书设定、上一章正文结尾、有效摘要、最近 Wiki 事实和当前章目标，规划下一章。

信息优先级：当前章专属要求 > 上一章正文结尾 > 有效摘要 > 最近 Wiki > 较早全书背景。
【全书背景】
{settings.get('background', '')}
【全书附加规则】
{settings.get('extra_requirements', '')}
【上一章有效摘要与正文结尾】
{previous_context[:3500] or '（未提供）'}
【最近 Wiki 原文依据】
{wiki_history[:2200] or '（未提供）'}
【本章目标】
{settings.get('chapter_brief', '') or '承接上一章，自然推进情节'}
【本章专属要求】
{settings.get('chapter_requirements', '') or '（无）'}

硬性规则：
1. 第一幕必须直接承接上一章结尾的人物、地点与未完成事件。
2. 本章最多两条主要人物线、两个主要视角；不得让上一章刚分别的所有人物立即重聚。
3. 人物只能使用自己亲眼所见、亲耳所闻或此前已被告知的信息；不同人物线的新信息不得自动共享。
4. 必须考虑路程与时间，不得让相隔很远的人物无过渡地在同一天抵达同一地点。
5. 允许新增人物、地点、事物或线索来保持新鲜感。普通路人、日常物件和短期障碍可自然加入；全章最多引入一个主要新增元素。
6. 主要新增元素（核心人物、重要组织、关键物品或重大设定）必须交代其出场入口、与现有剧情的连接及后续用途，不得改写原文或 Wiki 已确认事实。新线索首次出现时只能作为待验证信息，不能直接当作既定历史。
7. 不得无来源地发明会改变主线的旧事、亲属、武功、伤病、死亡或情报，也不得让新增人物取代上一章主角。
8. 每幕推进一个新事件并产生明确结束状态，不重复前幕，不一次解决全部悬念；主要新增元素不应在同一章内耗尽全部作用。
9. 规划前在内部检查人物信息权限、时间、距离、状态、事实来源和新增元素的合理性；不要输出分析过程。

输出严格 JSON 对象：
{{"title":"本章回目","beats":[{{"num":"一","name":"幕名","pov":"本幕视角人物","time":"相对上一幕的时间","location":"地点","known_before":["本幕开始前视角人物已知的信息"],"new_facts":["本幕通过可见渠道获得的新信息"],"new_elements":[{{"name":"新增元素名","type":"人物/地点/物品/组织/线索","source_or_entry":"如何自然进入当前剧情","future_use":"后续可能发挥的作用；不能当章全部解决"}}],"desc":"本幕具体事件","end_state":"幕末人物、地点与未完成事件"}}]}}。
没有主要新增元素的幕必须输出 "new_elements":[]；全章所有幕合计最多一个主要新增元素。
恰好 {num_beats} 幕。
只输出 JSON，不要 Markdown 或解释。"""
    data = None
    parse_error = None
    for attempt in range(2):
        request = prompt if attempt == 0 else (
            "上次规划未通过结构或连续性检查：" + "；".join(parse_error or ["JSON 无效"]) +
            "。请修正后直接给出完整 JSON 对象，不要输出分析过程。\n\n" + prompt)
        raw = llm.complete(
            request, system="你是小说结构编辑，只输出 JSON，不输出思考过程。",
            temperature=0.4 if attempt == 0 else 0.1,
            num_predict=max(2200, num_beats * 420), disable_thinking=True)
        try:
            data = _first_json_object(raw)
            parse_error = _chapter_plan_issues(data, num_beats, previous_context)
            if not parse_error:
                break
            data = None
        except ValueError as exc:
            parse_error = [str(exc)]
    if data is None:
        raise ValueError("章节规划未通过：" + "；".join(parse_error or ["模型未返回章节规划 JSON"]))
    title = str(data.get("title") or "").strip()
    beats = _parse_beats(json.dumps(data.get("beats"), ensure_ascii=False), num_beats)
    if not title or len(beats) != num_beats:
        raise ValueError("章节规划缺少回目或分幕数量不符。")
    return {"chapter_title": title, "beats": beats}


def revise_chapter_plan(settings: dict, llm: ContinuationLLM, num_beats: int,
                        issues: str, previous_context: str = "",
                        wiki_history: str = "") -> dict:
    """按用户指出的问题定向修订现有规划，不直接覆盖已保存计划。"""
    current_beats = settings.get("beats") or []
    if not current_beats:
        raise ValueError("当前章节还没有分幕规划，请先生成第一版规划。")
    issues = str(issues or "").strip()
    if not issues:
        raise ValueError("请先填写需要修改的问题点。")
    current = {
        "title": str(settings.get("chapter_title") or "").strip(),
        "beats": current_beats,
    }
    prompt = f"""你是长篇小说的章节结构编辑。请针对用户指出的问题，修订现有章节规划。

这是一项定向修订，不是从零规划：保留没有被问题影响的幕、人物线、有效线索和节奏；只修改解决问题所必需的部分。若调整一幕会影响后续幕，必须同步修正其时间、人物已知信息和结尾状态。

信息优先级：用户问题点 > 当前章专属要求 > 上一章正文结尾 > 有效摘要 > 最近 Wiki > 全书背景。
【用户指出的问题点】
{issues[:5000]}
【当前规划】
{json.dumps(current, ensure_ascii=False)}
【全书背景】
{settings.get('background', '')}
【全书附加规则】
{settings.get('extra_requirements', '')}
【上一章有效摘要与正文结尾】
{previous_context[:3500] or '（未提供）'}
【最近 Wiki 原文依据】
{wiki_history[:2200] or '（未提供）'}
【本章目标】
{settings.get('chapter_brief', '') or '承接上一章，自然推进情节'}
【本章专属要求】
{settings.get('chapter_requirements', '') or '（无）'}

修订规则：
1. 必须逐项解决用户指出的问题；问题之间冲突时，优先保证原文事实、时间线和人物信息权限。
2. 不得删除未被问题影响的重要情节，也不得借修订引入另一套无关剧情。
3. 第一幕必须承接上一章结尾；全章最多两条主要人物线、两个主要视角。
4. 每幕必须推进新事件并产生明确结束状态，不得用不同措辞重复同一功能。
5. 允许保留或调整原规划中的新增元素；全章主要新增元素仍不得超过一个。
6. 在内部逐项自检问题点，但不要输出分析、自检报告或修改说明，只输出修订后的完整规划。

输出严格 JSON 对象：
{{"title":"本章回目","beats":[{{"num":"一","name":"幕名","pov":"本幕视角人物","time":"相对上一幕的时间","location":"地点","known_before":["本幕开始前视角人物已知的信息"],"new_facts":["本幕通过可见渠道获得的新信息"],"new_elements":[{{"name":"新增元素名","type":"人物/地点/物品/组织/线索","source_or_entry":"如何自然进入当前剧情","future_use":"后续用途"}}],"desc":"本幕具体事件","end_state":"幕末人物、地点与未完成事件"}}]}}。
没有主要新增元素的幕输出 "new_elements":[]；恰好 {num_beats} 幕。只输出 JSON。"""
    data = None
    parse_error = None
    for attempt in range(2):
        request = prompt if attempt == 0 else (
            "上次修订未通过结构或连续性检查：" +
            "；".join(parse_error or ["JSON 无效"]) +
            "。请保持定向修订，修正后直接输出完整 JSON。\n\n" + prompt)
        raw = llm.complete(
            request, system="你是小说结构编辑，只输出 JSON，不输出思考过程。",
            temperature=0.3 if attempt == 0 else 0.1,
            num_predict=max(2400, num_beats * 460), disable_thinking=True)
        try:
            data = _first_json_object(raw)
            parse_error = _chapter_plan_issues(data, num_beats, previous_context)
            if not parse_error:
                break
            data = None
        except ValueError as exc:
            parse_error = [str(exc)]
    if data is None:
        raise ValueError("章节规划修订未通过：" +
                         "；".join(parse_error or ["模型未返回章节规划 JSON"]))
    title = str(data.get("title") or "").strip()
    beats = _parse_beats(json.dumps(data.get("beats"), ensure_ascii=False), num_beats)
    if not title or len(beats) != num_beats:
        raise ValueError("修订后的章节规划缺少回目或分幕数量不符。")
    return {"chapter_title": title, "beats": beats}


def _chapter_plan_issues(data: dict, num_beats: int, previous_context: str) -> list[str]:
    """对模型规划做轻量结构与连续性检查，失败时交给模型重写一次。"""
    issues = []
    beats = data.get("beats") if isinstance(data, dict) else None
    if not isinstance(beats, list) or len(beats) != num_beats:
        return [f"必须恰好输出 {num_beats} 幕"]
    povs = []
    required = ("pov", "time", "location", "known_before", "new_facts", "desc", "end_state")
    new_elements = []
    for index, beat in enumerate(beats, 1):
        if not isinstance(beat, dict):
            issues.append(f"第 {index} 幕不是对象")
            continue
        missing = [key for key in required if not beat.get(key)]
        if "new_elements" not in beat or not isinstance(beat.get("new_elements"), list):
            missing.append("new_elements")
        if missing:
            issues.append(f"第 {index} 幕缺少字段：{','.join(missing)}")
        for element in beat.get("new_elements") or []:
            if not isinstance(element, dict):
                issues.append(f"第 {index} 幕新增元素不是对象")
                continue
            element_missing = [key for key in ("name", "type", "source_or_entry", "future_use")
                               if not str(element.get(key) or "").strip()]
            if element_missing:
                issues.append(f"第 {index} 幕新增元素缺少字段：{','.join(element_missing)}")
            new_elements.append(element)
        pov = str(beat.get("pov") or "").strip()
        if pov and pov not in povs:
            povs.append(pov)
    if len(povs) > 2:
        issues.append(f"主要视角超过两个：{'、'.join(povs)}")
    if len(new_elements) > 1:
        issues.append(f"主要新增元素超过一个：{len(new_elements)} 个")
    first_pov = str((beats[0] or {}).get("pov") or "").strip() if beats else ""
    if first_pov and first_pov not in previous_context:
        issues.append(f"第一幕视角人物“{first_pov}”未出现在上一章上下文")
    return issues


def _parse_beats(raw: str, num_beats: int) -> list:
    """从模型输出里尽量解析出分幕 JSON。"""
    text = re.sub(r"```(?:json)?", "", raw or "").strip()
    match = re.search(r"\[.*\]", text, flags=re.DOTALL)
    if not match:
        raise ValueError("模型未返回可解析的分幕 JSON，请重试或手动填写。")
    data = json.loads(match.group(0))
    if not isinstance(data, list):
        raise ValueError("分幕 JSON 不是数组。")

    numerals = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
                "十一", "十二"]
    beats = []
    for i, item in enumerate(data[:num_beats]):
        if not isinstance(item, dict):
            continue
        beats.append({
            "num": str(item.get("num") or (numerals[i] if i < len(numerals) else i + 1)),
            "name": str(item.get("name", "")).strip(),
            "desc": _format_planned_beat(item),
        })
    if not beats:
        raise ValueError("分幕 JSON 为空。")
    return beats


def _format_planned_beat(item: dict) -> str:
    """把结构化规划压成现有 UI 可编辑、正文生成可读取的一行幕描述。"""
    meta = []
    for key, label in (("pov", "视角"), ("time", "时间"), ("location", "地点")):
        value = str(item.get(key) or "").strip()
        if value:
            meta.append(f"{label}：{value}")
    desc = str(item.get("desc") or "").strip()
    known = item.get("known_before") if isinstance(item.get("known_before"), list) else []
    new_facts = item.get("new_facts") if isinstance(item.get("new_facts"), list) else []
    new_elements = item.get("new_elements") if isinstance(item.get("new_elements"), list) else []
    end_state = str(item.get("end_state") or "").strip()
    parts = [f"【{'｜'.join(meta)}】" if meta else "", desc]
    if known:
        parts.append("已知信息：" + "；".join(str(value) for value in known if str(value).strip()))
    if new_facts:
        parts.append("新增信息：" + "；".join(str(value) for value in new_facts if str(value).strip()))
    if new_elements:
        formatted = []
        for element in new_elements:
            if not isinstance(element, dict):
                continue
            formatted.append(
                f"{element.get('name', '')}（{element.get('type', '')}；"
                f"出场：{element.get('source_or_entry', '')}；后续：{element.get('future_use', '')}）")
        if formatted:
            parts.append("主要新增元素：" + "；".join(formatted))
    if end_state:
        parts.append("结尾状态：" + end_state)
    return " ".join(part for part in parts if part)
