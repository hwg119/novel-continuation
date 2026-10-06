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
from collections.abc import Callable

from core.project_manager import chapter_path, chapters_dir, list_chapter_files
from core.utils import read_file


# ----------------------------- LLM 调用 -----------------------------

class ContinuationLLM:
    """续写统一调用入口：Ollama 走原生接口，其余走 llm_adapters。"""

    def __init__(self, llm_config: dict):
        self.cfg = dict(llm_config or {})
        self.fmt = str(self.cfg.get("interface_format", "")).strip().lower()
        self.is_ollama = self.fmt == "ollama"
        self.last_response_diagnostics = {}

    def complete(self, prompt: str, system: str = "",
                 temperature: float = None, num_predict: int = None,
                 disable_thinking: bool = False, thinking_effort: str = None,
                 on_chunk=None) -> str:
        temperature = (self.cfg.get("temperature", 0.8)
                       if temperature is None else temperature)
        self.last_response_diagnostics = {"model_name": self.cfg.get("model_name"),
                                          "interface_format": self.cfg.get("interface_format"),
                                          "requested_max_tokens": int(num_predict or self.cfg.get("max_tokens", 4096))}
        if self.is_ollama:
            return self._ollama_chat(prompt, system, temperature, num_predict, on_chunk)
        return self._adapter_invoke(prompt, system, temperature, num_predict,
                                    disable_thinking=disable_thinking, thinking_effort=thinking_effort,
                                    on_chunk=on_chunk)

    # -- 原生 Ollama --------------------------------------------------

    def _ollama_root(self) -> str:
        url = str(self.cfg.get("base_url", "") or "http://localhost:11434").rstrip("/")
        for suffix in ("/v1", "/api"):
            if url.endswith(suffix):
                url = url[: -len(suffix)]
        return url

    def _ollama_chat(self, prompt, system, temperature, num_predict, on_chunk=None) -> str:
        payload = {
            "model": self.cfg.get("model_name", ""),
            "messages": [
                {"role": "system", "content": system or ""},
                {"role": "user", "content": prompt},
            ],
            "stream": on_chunk is not None,
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
            if on_chunk is not None:
                parts = []
                for line in resp:
                    data = json.loads(line)
                    if data.get('error'): raise ValueError(data['error'])
                    content = (data.get('message') or {}).get('content') or ''
                    if content:
                        parts.append(content)
                        on_chunk(content)
                return ''.join(parts)
            data = json.loads(resp.read().decode("utf-8"))
        self.last_response_diagnostics.update({key: data.get(key) for key in
            ("done_reason", "prompt_eval_count", "eval_count", "total_duration")})
        return (data.get("message") or {}).get("content", "") or ""

    # -- 通用适配器 ----------------------------------------------------

    def _adapter_invoke(self, prompt, system, temperature, num_predict,
                        disable_thinking=False, thinking_effort=None, on_chunk=None) -> str:
        from llm_adapters import create_llm_adapter
        model_name = str(self.cfg.get("model_name", "")).lower()
        extra_body = None
        if thinking_effort is not None:
            if disable_thinking:
                raise ValueError("不能同时关闭思考并指定思考强度")
            if thinking_effort != "low" or model_name != "deepseek-flash" or self.fmt not in {"openai", "deepseek"}:
                raise ValueError("当前显式思考策略仅支持 DeepSeek flash 的 low 模式")
            extra_body = {"thinking": {"type": "enabled"}, "reasoning_effort": "low"}
        if disable_thinking and self.fmt in {"openai", "deepseek"}:
            if self.fmt == "deepseek" or model_name == "deepseek-flash":
                extra_body = {"thinking": {"type": "disabled"}}
            elif model_name == "minimax-m3" or model_name.startswith("mimo-v2.6-"):
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
        client = getattr(adapter, '_client', None)
        if on_chunk is not None and callable(getattr(client, 'stream', None)):
            parts = []
            for chunk in client.stream(full_prompt):
                content = chunk.content
                if isinstance(content, list):
                    content = ''.join(item.get('text', '') for item in content
                                      if isinstance(item, dict) and item.get('type') == 'text')
                if content:
                    parts.append(content)
                    on_chunk(content)
            return ''.join(parts)
        result = adapter.invoke(full_prompt) or ""
        if on_chunk is not None and result:
            on_chunk(result)  # Non-streaming providers return one complete response.
        self.last_response_diagnostics.update(getattr(adapter, "last_response_diagnostics", {}))
        self.last_response_diagnostics["requested_thinking_parameters"] = extra_body
        return result


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
    """去掉思维链和 Markdown 外壳，保留正文中的字母、名称与代号。"""
    settings = settings or {}
    text = re.sub(r"<think(?:ing)?>.*?(?:</think(?:ing)?>|$)", "",
                  text or "", flags=re.DOTALL)
    text = re.sub(r"(?m)^[ \t]*```[A-Za-z0-9_-]*[ \t]*(?:\r?\n|$)", "", text)
    text = text.replace("```", "").strip()

    lines = [ln for ln in text.splitlines() if ln.strip()]
    if lines and re.match(r"^第\s*[0-9一二三四五六七八九十百千○零〇两]+\s*[回章节]", lines[0].strip()):
        lines = lines[1:]
    text = "\n".join(lines)

    # 字母可能是姓名首字母、线索代号或正文语言，不能按字符集删除。
    text = re.sub(r"(?m)^[ \t]{0,3}#{1,6}[ \t]+", "", text)
    text = text.replace("**", "").replace("__", "").replace("~~", "").replace("`", "")
    text = re.sub(r"(?<!\w)[*_]([^*_\n]+)[*_](?!\w)", r"\1", text)

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
                      previous_chapter_tail: str = "", audit=None) -> str:
    from core.agent_skill import skill_guidance
    guidance = skill_guidance("chapter-writing", audit=audit, stage=f"beat_{beat.get('num', '')}")
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

    # 术语表：强制使用工程固定译名，避免译名漂移
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

    forbidden_words = settings.get("forbidden_words") or []
    forbid_line = ""
    if forbidden_words:
        forbid_line = "作者禁用词句：" + "、".join(forbidden_words) + "\n"

    extra = settings.get("extra_requirements", "").strip()
    extra_line = f"\n【附加要求】\n{extra}\n" if extra else ""

    retrieved_block = ""
    if retrieved_context and retrieved_context.strip():
        retrieved_block = (
            f"\n【母本相关片段】（较早的历史参考；只用于补充人物/设定/世界观细节，"
            f"不得直接抄写，也不得覆盖最近章节已经发生的变化）\n{retrieved_context.strip()[:1200]}\n"
        )

    return f"""{guidance}

【本书背景】
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

【语体样本】（只学习句法、节奏和氛围，不照抄句子与情节；已有设定仍以实际上下文为准）
{style or '(未提供)'}{retrieved_block}

【已写正文的末尾】（须自然衔接，但不得复述其字句）
{tail}
{previous_tail_block}{summaries_block}{wiki_block}

【已写过的段落开头（禁写清单，严禁写出雷同句子）】
{forbidden_list(written_paras)}

【本幕任务】第{num}幕「{name}」：{desc}
【本幕状态卡】
{json.dumps({key: beat.get(key, '') for key in ('pov', 'time', 'location', 'known_before', 'new_facts', 'end_state')}, ensure_ascii=False)}

【本次交付要求】
- 本幕约 {chars_per_beat} 字。{end_rule}{next_beat_rule}
- {script_rule}；使用中文标点，直接输出自然段，无标题、Markdown 或说明。
- 人物口吻：{voices or '沿用上下文中的人物声音'}。
- 落实本幕事件与结束状态，可补充服务场景的动作、互动与细节，不凭空改变主线事实。
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
            audit=run_log,
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
                run_log.write("beat_model_request", beat_index=i, attempt=attempt + 1,
                              model=getattr(llm, "cfg", {}).get("model_name", ""),
                              prompt=request_prompt, disable_thinking=True)
                attempt_started = time.monotonic()
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
                run_log.write("beat_model_response", beat_index=i, attempt=attempt + 1,
                              raw_response=raw, elapsed_ms=round((time.monotonic() - attempt_started) * 1000),
                              response_diagnostics=getattr(llm, "last_response_diagnostics", {}))
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




def _request_plan_json(llm: ContinuationLLM, prompt: str, num_beats: int,
                       temperature: float = 0.3, audit=None,
                       stage: str = "draft") -> dict:
    """请求并解析一份章节规划；这里只处理传输格式，不替代剧情审查。"""
    last_error = "模型未返回章节规划 JSON"
    from core.agent_skill import skill_guidance
    reference = "draft" if stage == "draft" else "repair"
    prompt = skill_guidance("chapter-planning", reference, audit, stage) + "\n\n" + prompt
    if reference == "repair" and audit:
        audit.write("repair_evidence_policy", stage=stage,
                    policy="clarify_supported_or_show_new_discovery_not_invent_past")
    attempts = 1 if stage in {"editorial_patch", "acceptance_editorial_patch"} else 2
    for attempt in range(attempts):
        request = prompt if attempt == 0 else (
            f"上次输出无法解析或结构不完整：{last_error}。"
            "请保留原任务，只修复输出格式并给出完整 JSON 对象。\n\n" + prompt)
        request_temperature = temperature if attempt == 0 else 0.1
        if audit:
            audit.write("model_request", stage=stage, attempt=attempt + 1,
                        temperature=request_temperature, prompt_chars=len(request),
                        prompt=request)
        started = time.monotonic()
        try:
            raw = llm.complete(
                request, system="你是小说结构编辑，只输出 JSON，不输出思考过程。",
                temperature=request_temperature,
                num_predict=max(2200, num_beats * 460), disable_thinking=True)
        except Exception as exc:
            if audit:
                audit.write("model_request_failed", stage=stage, attempt=attempt + 1,
                            elapsed_ms=round((time.monotonic() - started) * 1000),
                            error_type=type(exc).__name__, error=str(exc))
            raise
        if audit:
            audit.write("model_response", stage=stage, attempt=attempt + 1,
                        elapsed_ms=round((time.monotonic() - started) * 1000),
                        response_chars=len(raw or ""), raw_response=raw)
        try:
            data = _first_json_object(raw)
            issues = _chapter_plan_issues(data, num_beats, "")
            if audit:
                audit.write("structure_check", stage=stage, attempt=attempt + 1,
                            passed=not issues, issues=issues,
                            advisory_notes=_chapter_plan_advisories(data),
                            title=str(data.get("title") or ""),
                            beats_count=len(data.get("beats") or []))
            if not issues:
                return data
            last_error = "；".join(issues)
        except ValueError as exc:
            last_error = str(exc)
            if audit:
                audit.write("parse_failed", stage=stage, attempt=attempt + 1,
                            error=last_error)
    raise ValueError(last_error)


def _review_chapter_plan(llm: ContinuationLLM, draft: dict, num_beats: int,
                         previous_context: str, wiki_history: str,
                         chapter_goal: str, audit=None, project_dir=None,
                         chapter_number=None, progress=None) -> list[dict]:
    """让独立审查轮只找问题，不让它一边批评一边重写。"""
    from core.plan_evidence import evidence_sources, verify_issue
    sources = evidence_sources(previous_context[:7500], wiki_history[:1800], draft)
    from core.agent_skill import skill_guidance
    guidance = skill_guidance("chapter-planning", "review", audit, "review")
    prompt = f"""{guidance}

【本章目标】
{chapter_goal or '承接上一章，自然推进情节'}
【分类证据目录｜逐条引用对应来源编号】
{json.dumps(sources, ensure_ascii=False)}
本次审查恰好包含 {num_beats} 幕；只返回本步骤的 JSON 审查结果。"""

    try:
        if audit:
            audit.write("review_request", prompt_chars=len(prompt), prompt=prompt)
        started = time.monotonic()
        if project_dir and chapter_number:
            from core.planning_research import research_review
            data = research_review(llm, prompt, sources, project_dir, int(chapter_number),
                                   _first_json_object, audit=audit, progress=progress,
                                   num_predict=max(1200, num_beats * 240))
        else:
            raw = llm.complete(
                prompt, system="你只负责审查章节规划并输出 JSON，不负责续写正文。",
                temperature=0.1, num_predict=max(1200, num_beats * 240),
                disable_thinking=True)
            if audit:
                audit.write("review_response",
                            elapsed_ms=round((time.monotonic() - started) * 1000),
                            response_chars=len(raw or ""), raw_response=raw)
            data = _first_json_object(raw)
        issues = data.get("issues")
        if not isinstance(issues, list):
            if audit:
                audit.write("review_parse_failed", error="issues 不是数组")
            return [{"severity": "low", "scope": "审查流程", "problem": "初审未完成：issues 不是数组",
                     "check_failed": True}]
        result = [item for item in issues if isinstance(item, dict) and item.get("problem")][:8]
        for item in result:
            verify_issue(item, sources)
        # 未回答的疑问进入持久化提醒，但不参与自动修补。
        uncertainties = data.get("uncertainties")
        if isinstance(uncertainties, list):
            for note in uncertainties[:8]:
                problem = str(note.get("problem") or "") if isinstance(note, dict) else str(note or "")
                if problem.strip():
                    result.append({"severity": "low", "scope": "历史查证待确认",
                                   "problem": problem, "evidence_state": "uncertain",
                                   "evidence_verified": False, "citations": [],
                                   "evidence_status": "查阅后仍有疑问，仅提醒，不自动修补"})
        if audit:
            audit.write("review_evidence_sources", sources=sources)
            audit.write("review_parsed", issues_count=len(result), issues=result)
        return result
    except Exception as exc:
        # 审查轮失败不应抹掉一份结构有效的初稿。
        if audit:
            audit.write("review_parse_failed", error_type=type(exc).__name__, error=str(exc))
        return [{"severity": "low", "scope": "审查流程", "problem": f"初审未完成：{exc}",
                 "check_failed": True}]


def _validate_rewritten_plan(llm: ContinuationLLM, draft: dict, rewritten: dict,
                             review_issues: list[dict], num_beats: int,
                             previous_context: str, audit=None) -> dict:
    """重写后只做验收，避免审查意见在改写过程中被解决一半或引入新问题。"""
    from core.agent_skill import skill_guidance
    guidance = skill_guidance("chapter-planning", "acceptance", audit, "acceptance")
    prompt = f"""{guidance}

【近期连续性上下文】
{previous_context[:7500] or '（未提供）'}
【初稿】
{json.dumps(draft, ensure_ascii=False)}
【初审意见】
{json.dumps(review_issues, ensure_ascii=False)}
【重写稿】
{json.dumps(rewritten, ensure_ascii=False)}

只返回本步骤验收 JSON，不重写规划。"""

    if audit:
        audit.write("acceptance_request", prompt_chars=len(prompt), prompt=prompt)
    started = time.monotonic()
    try:
        raw = llm.complete(
            prompt, system="你只验收章节规划并输出 JSON，不重写正文或规划。",
            temperature=0.1, num_predict=max(1200, num_beats * 220),
            disable_thinking=True)
        if audit:
            audit.write("acceptance_response",
                        elapsed_ms=round((time.monotonic() - started) * 1000),
                        response_chars=len(raw or ""), raw_response=raw)
        data = _first_json_object(raw)
        if any(not isinstance(data.get(key), list) for key in
               ("unresolved", "new_issues", "editorial_notes")):
            raise ValueError("验收结果缺少完整问题数组")
        unresolved = [item for item in data["unresolved"] if isinstance(item, dict) and item.get("problem")]
        new_issues = [item for item in data["new_issues"] if isinstance(item, dict) and item.get("problem")]
        notes = data["editorial_notes"]
        result = {"passed": not (unresolved or new_issues),
                  "unresolved": unresolved[:8], "new_issues": new_issues[:8],
                  "editorial_notes": notes[:8]}
        if audit:
            audit.write("acceptance_parsed", **result)
        return result
    except Exception as exc:
        if audit:
            audit.write("acceptance_failed", error_type=type(exc).__name__, error=str(exc))
        return {"passed": None, "unresolved": [], "new_issues": [],
                "editorial_notes": [], "check_failed": str(exc)}


def suggest_chapter_plan(settings: dict, llm: ContinuationLLM, num_beats: int,
                         previous_context: str = "", wiki_history: str = "",
                         progress: Callable[[str], None] | None = None,
                         audit=None, stage_runner=None, project_dir=None) -> dict:
    """为新章提出回目和分幕；只返回建议，不自动覆盖已保存计划。"""
    def run_stage(name, function, *args, **kwargs):
        if stage_runner:
            return stage_runner(name, lambda: function(*args, **kwargs))
        return function(*args, **kwargs)
    prompt = f"""【程序指定目标章节】
第 {settings.get('chapter_number', '')} 章；恰好 {num_beats} 幕。
【全书背景】
{settings.get('background', '')}
【全书附加规则】
{settings.get('extra_requirements', '')}
【近期连续性上下文】
{previous_context[:7500] or '（未提供）'}
【最近 Wiki 原文依据】
{wiki_history[:2200] or '（未提供）'}
【本章目标】
{settings.get('chapter_brief', '') or '承接上一章，自然推进情节'}
【本章专属要求】
{settings.get('chapter_requirements', '') or '（无）'}
只返回完整规划 JSON。"""

    if progress:
        progress("正在生成章节规划初稿（第 1/4 阶段）")
    if audit:
        audit.write("context_prepared", num_beats=num_beats,
                    target_chapter_number=settings.get("chapter_number"),
                    previous_summary_available="【上一章摘要】" in previous_context,
                    previous_opening_available="正文开头定位】" in previous_context,
                    previous_plan_reference_used="【上一章规划参考" in previous_context,
                    background_chars=len(str(settings.get('background') or '')),
                    rules_chars=len(str(settings.get('extra_requirements') or '')),
                    continuity_chars=len(previous_context), wiki_chars=len(wiki_history),
                    goal=str(settings.get('chapter_brief') or ''),
                    chapter_requirements=str(settings.get('chapter_requirements') or ''))
    try:
        draft = run_stage("draft", _request_plan_json, llm, prompt, num_beats, temperature=0.4,
                                   audit=audit, stage="draft")
    except ValueError as exc:
        raise ValueError(f"章节规划初稿未通过：{exc}") from exc

    if progress:
        progress("正在独立审查重复情节与连续性（第 2/4 阶段）")
    review_issues = run_stage("review", _review_chapter_plan,
        llm, draft, num_beats, previous_context, wiki_history,
        str(settings.get('chapter_brief') or ''), audit=audit, project_dir=project_dir,
        chapter_number=settings.get('chapter_number'), progress=progress)
    data = draft
    substantive_issues = [item for item in review_issues
                          if str(item.get("severity") or "low").lower() in ("high", "medium")
                          and item.get("evidence_verified")]
    editorial_issues = [item for item in review_issues if item.get("severity") == "low"
                        and item.get("evidence_verified") and not item.get("check_failed")]
    if substantive_issues:
        if progress:
            progress(f"审查发现 {len(substantive_issues)} 个剧情问题、"
                     f"{len(review_issues) - len(substantive_issues)} 个编辑提醒，"
                     "正在定向重写（第 3/4 阶段）")
            for item in review_issues:
                scope = str(item.get("scope") or "相关分幕").strip()
                severity = str(item.get("severity") or "medium").lower()
                progress(f"规划审查提醒 · {scope} · {severity}")
        rewrite_prompt = f"""你是长篇小说的章节主编。请根据独立审查意见重写规划初稿。

【原始任务与依据】
{prompt}

【规划初稿】
{json.dumps(draft, ensure_ascii=False)}
【独立审查意见】
{json.dumps(substantive_issues, ensure_ascii=False)}

重写要求：
1. 逐项解决审查意见，同时保留初稿中未受影响的有效人物线和线索。
2. 对重复既有内容的幕，不能只换幕名、地点、措辞或时间；必须替换其剧情功能，使其产生新的信息、决定、阻碍或关系变化。
3. 可以合并功能重复的内容，但最终仍须恰好 {num_beats} 幕；空出的幕用于推进尚未完成的线索或引入一个自然的新变化。
4. 不输出修改说明。严格使用原始任务要求的 JSON 结构，只输出完整 JSON。"""
        try:
            data = run_stage("rewrite", _request_plan_json, llm, rewrite_prompt, num_beats, temperature=0.2,
                                      audit=audit, stage="rewrite")
        except ValueError as exc:
            if stage_runner:
                raise
            if progress:
                progress(f"提醒：定向重写未通过结构检查，保留可用初稿：{exc}")
            data = draft
    elif progress:
        progress("初审未发现证据充分的剧情问题，保留初稿；低风险提醒见完整日志")

    final_source = "rewrite" if data is not draft else "draft"
    validation_status = "initial_review_only"
    all_editorial_notes = [dict(item, review_stage="initial") for item in review_issues
                          if item.get("severity") == "low" or not item.get("evidence_verified")]
    if any(item.get("check_failed") for item in review_issues):
        validation_status = "incomplete"
    if not substantive_issues and editorial_issues:
        if progress:
            progress(f"正在规范 {len(editorial_issues)} 个文字问题（一次最小修补）")
        editorial_prompt = f"""你是规划校对编辑。只修正文稿中证据明确的文字问题。
【当前规划】
{json.dumps(draft, ensure_ascii=False)}
【文字校对意见】
{json.dumps(editorial_issues, ensure_ascii=False)}
只修改意见涉及的名称、计数、称谓或错字，并同步涉及字段。不得改变剧情、幕顺序、线索、人物关系与事件结果。计数不能确认时使用不带序号的称谓。恰好保留 {num_beats} 幕，输出相同结构的完整 JSON。"""
        try:
            data = run_stage("editorial_patch", _request_plan_json, llm, editorial_prompt, num_beats,
                                      temperature=0.1, audit=audit, stage="editorial_patch")
            final_source = "editorial_patch"
            validation_status = "editorial_pending_review"
            if audit:
                audit.write("editorial_patch_applied", requested_fixes=editorial_issues)
        except Exception as exc:
            if audit:
                audit.write("editorial_patch_rejected", error_type=type(exc).__name__, error=str(exc))
            if progress:
                progress("提醒：文字修补未完成，保留初稿；详见完整日志")
    if data is not draft and final_source != "editorial_patch":
        if progress:
            progress("正在验收重写后的规划（第 4/4 阶段）")
        acceptance = run_stage("acceptance", _validate_rewritten_plan,
            llm, draft, data, [item for item in review_issues if item.get("evidence_verified")],
            num_beats, previous_context, audit=audit)
        acceptance_issues = list(acceptance.get("unresolved") or []) + list(
            acceptance.get("new_issues") or [])
        editorial_notes = list(acceptance.get("editorial_notes") or [])
        all_editorial_notes.extend(dict(item, review_stage="acceptance") for item in editorial_notes
                                   if isinstance(item, dict))
        if acceptance.get("check_failed"):
            validation_status = "incomplete"
            if progress:
                progress("提醒：重写后验收调用未完成，保留结构有效的重写稿；详见完整规划日志")
        elif acceptance_issues:
            validation_status = "needs_review"
            if progress:
                progress(f"后验收发现 {len(acceptance_issues)} 个待修补问题，"
                         "正在进行一次局部修补（条件阶段）")
                for item in acceptance_issues:
                    scope = str(item.get("scope") or "相关分幕").strip()
                    progress(f"后验收提醒 · {scope}")
            patch_prompt = f"""你是章节规划的修订编辑。只对验收未通过之处做一次最小局部修补。

【近期连续性上下文】
{previous_context[:7500] or '（未提供）'}
【当前重写稿】
{json.dumps(data, ensure_ascii=False)}
【验收未通过项】
{json.dumps(acceptance_issues, ensure_ascii=False)}
【低风险编辑提醒】
{json.dumps(editorial_notes, ensure_ascii=False)}

要求：
1. 逐项修复验收问题；未涉及的幕、线索和节奏保持不变。
2. 只能做解决问题所需的最小改动，不重新构思整章，不增加新的主要人物线或设定。
3. 修改幕内时间、地点或决定时，同步修正该幕的 known_before、new_facts、desc 和 end_state。
4. 恰好保留 {num_beats} 幕，并使用原规划相同的完整 JSON 结构。只输出 JSON。"""
            try:
                data = run_stage("local_patch", _request_plan_json,
                    llm, patch_prompt, num_beats, temperature=0.1,
                    audit=audit, stage="local_patch")
                final_source = "local_patch"
                validation_status = "patched_pending_review"
                if audit:
                    audit.write("local_patch_applied", fixed_issues=acceptance_issues,
                                editorial_notes=editorial_notes)
            except ValueError as exc:
                if stage_runner:
                    raise
                if progress:
                    progress(f"提醒：局部修补未通过结构检查，保留重写稿：{exc}")
                if audit:
                    audit.write("local_patch_rejected", error=str(exc))
        else:
            validation_status = "passed"
            if progress:
                progress(f"重写后验收通过；另有 {len(editorial_notes)} 个低风险编辑提醒")
            if editorial_notes:
                if progress:
                    progress("正在规范后验收的文字提醒（一次最小修补）")
                editorial_prompt = f"""你是规划校对编辑，仅处理终审的文字提醒。
【当前规划】
{json.dumps(data, ensure_ascii=False)}
【终审文字提醒】
{json.dumps(editorial_notes, ensure_ascii=False)}
只改提醒涉及的文字并同步对应字段。不得增加事件或改变剧情、人物状态、线索、幕顺序；不能确认的表述使用限定范围的措辞。不为普通材料补充 new_elements，保持现有新增元素登记。保留 {num_beats} 幕，输出相同结构的完整 JSON。"""
                try:
                    data = run_stage("acceptance_editorial_patch", _request_plan_json, llm, editorial_prompt, num_beats,
                                              temperature=0.1, audit=audit, stage="acceptance_editorial_patch")
                    final_source = "acceptance_editorial_patch"
                    validation_status = "editorial_pending_review"
                    if audit:
                        audit.write("editorial_patch_applied", review_stage="acceptance",
                                    requested_fixes=editorial_notes)
                except Exception as exc:
                    if audit:
                        audit.write("editorial_patch_rejected", review_stage="acceptance", error=str(exc))
                    if progress:
                        progress("提醒：后验收文字规范未完成，保留通过剧情验收的版本和提醒")
    elif audit:
        audit.write("acceptance_skipped", reason="初审未触发有效重写")
    if audit:
        audit.write("final_plan_selected", source=final_source,
                    validation_status=validation_status,
                    review_issues_count=len(review_issues), editorial_notes=all_editorial_notes, plan=data)
    all_editorial_notes.extend({"scope": "规划建议", "problem": note,
                                "review_stage": "structure_advisory"}
                               for note in _chapter_plan_advisories(data))
    title = _plan_title(data.get("title"), settings.get("chapter_number"))
    if audit:
        audit.write("plan_title_normalized", chapter_number=settings.get("chapter_number"),
                    model_title=data.get("title"), chapter_title=title)
    beats = _parse_beats(json.dumps(data.get("beats"), ensure_ascii=False), num_beats)
    if not title or len(beats) != num_beats:
        raise ValueError("章节规划缺少回目或分幕数量不符。")
    return {"chapter_title": title, "beats": beats,
            "planning_review": {"status": validation_status, "source": final_source,
                                "editorial_notes": all_editorial_notes}}


def revise_chapter_plan(settings: dict, llm: ContinuationLLM, num_beats: int,
                        issues: str, previous_context: str = "",
                        wiki_history: str = "",
                        progress: Callable[[str], None] | None = None, audit=None) -> dict:
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
    from core.agent_skill import skill_guidance
    guidance = skill_guidance("chapter-planning", "repair", audit, "user_plan_revision")
    prompt = f"""{guidance}

程序指定目标为第 {settings.get('chapter_number', '')} 章；title 只输出回目文字，不含章号。

这是一项定向修订，不是从零规划：保留未受影响的人物线、有效线索和故事基础。用户要求改善节奏、趣味或波折时，可以改变必要幕的实际事件、阻力、人物选择或结果，不能仅更换回目、幕名或形容词，也不能以保留原稿为由拒绝实质调整。若调整一幕影响后续幕，必须同步修正其时间、人物已知信息和结尾状态。仅要求改名称或文字时，不额外改变剧情。

信息优先级：用户问题点 > 当前章专属要求 > 上一章正文结尾 > 有效摘要 > 最近 Wiki > 全书背景。
【用户指出的问题点】
{issues[:5000]}
【当前规划】
{json.dumps(current, ensure_ascii=False)}
【全书背景】
{settings.get('background', '')}
【全书附加规则】
{settings.get('extra_requirements', '')}
【近期连续性上下文】
{previous_context[:7500] or '（未提供）'}
【最近 Wiki 原文依据】
{wiki_history[:2200] or '（未提供）'}
【本章目标】
{settings.get('chapter_brief', '') or '承接上一章，自然推进情节'}
【本章专属要求】
{settings.get('chapter_requirements', '') or '（无）'}

修订规则：
1. 必须逐项解决用户指出的问题；问题之间冲突时，优先保证原文事实、时间线和人物信息权限。
2. 不得删除未被问题影响的重要情节，也不得借修订引入另一套无关剧情。
3. 第一幕须与上一章保持可解释的时间线或事件连续性，允许自然切换人物线；通常聚焦一至两条主要人物线，必要时可增加视角。
4. 每幕必须推进新事件并产生明确结束状态，不得用不同措辞重复同一功能。
5. 允许保留或调整原规划中的新增元素，通常聚焦一个，但不以数量限制合理创作；普通线索材料放入 new_facts 即可。
6. 在内部逐项自检问题点，但不要输出分析、自检报告或修改说明，只输出修订后的完整规划。
7. 动态上下文中已经完成的内容不得原样重演；章末状态是修订起点，若发生变化必须保留自然过程。若分幕记录与正文摘要冲突，以正文摘要和正文结尾为准。
8. 根据上一章开头定位和结尾状态承接时间，次日不能沿用前一日日期；倒叙或并行场景须明确说明，无法确定日期时用相对时间。情绪未解决不等于既有信息尚未告知，不得把新态度写成首次获知。

输出严格 JSON 对象：
{{"title":"本章回目","beats":[{{"num":"一","name":"幕名","pov":"本幕视角人物","time":"相对上一幕的时间","location":"地点","known_before":["本幕开始前视角人物已知的信息"],"new_facts":["本幕获得的新信息"],"new_elements":[{{"name":"新增元素名","type":"简短类别","source_or_entry":"如何自然进入当前剧情","future_use":"后续用途"}}],"desc":"本幕具体事件","end_state":"幕末状态与未完成线索"}}]}}。
没有主要新增元素的幕输出 "new_elements":[]；恰好 {num_beats} 幕。只输出 JSON。"""
    data = None
    parse_error = None
    from core.plan_revision_check import check_revision
    original_plan = {'chapter_title':current['title'],'beats':current_beats}
    candidate = None
    acceptance = None
    for attempt in range(2):
        data = None
        request = prompt if attempt == 0 else (
            "上次修订未落实要求或未通过结构检查：" +
            "；".join(parse_error or ["JSON 无效"]) +
            "。请保持定向修订，修正后直接输出完整 JSON。\n\n" + prompt)
        if audit:
            audit.write("model_request", stage="user_plan_revision", attempt=attempt + 1, prompt=request)
        if progress:
            progress('正在按用户问题修订分幕' if attempt == 0 else '正在定向补修未落实要求（最多一次）')
        raw = llm.complete(
            request, system="你是小说结构编辑，只输出 JSON，不输出思考过程。",
            temperature=0.3 if attempt == 0 else 0.1,
            num_predict=max(2400, num_beats * 460), disable_thinking=True)
        if audit:
            audit.write("model_response", stage="user_plan_revision", attempt=attempt + 1, raw_response=raw)
        try:
            data = _first_json_object(raw)
            parse_error = _chapter_plan_issues(data, num_beats, previous_context)
            if not parse_error:
                title = _plan_title(data.get('title'),settings.get('chapter_number'))
                beats = _parse_beats(json.dumps(data.get('beats'),ensure_ascii=False),num_beats)
                if not title or len(beats)!=num_beats:
                    raise ValueError('修订后的章节规划缺少回目或分幕数量不符。')
                candidate = {'chapter_title':title,'beats':beats}
                if progress: progress('正在对比实际变化并验收用户修订要求')
                acceptance = check_revision(original_plan,candidate,issues[:5000],llm,
                    _first_json_object,previous_context,audit,attempt+1)
                if acceptance['status'] in ('passed','incomplete'): break
                parse_error = [f"{n['scope']}：{n['problem']}；补修：{n['suggestion']}"
                               for n in acceptance['unresolved']]
                if progress:
                    progress('要求尚未落实：'+ '；'.join(n['problem'] for n in acceptance['unresolved'])[:500])
                if attempt == 1: break
            data = None
        except ValueError as exc:
            data = None
            parse_error = [str(exc)]
    if candidate is None:
        raise ValueError("章节规划修订未通过：" +
                         "；".join(parse_error or ["模型未返回章节规划 JSON"]))
    if data is None:
        acceptance = {'status':'incomplete','summary':'补修结构检查失败，保留前一版候选供核对。',
                      'unresolved':[{'scope':'补修','problem':'；'.join(parse_error or []),'suggestion':'重新修订'}]}
    review = {**(acceptance or {}),'source':'user_revision','requirements':issues[:5000],
              'generation_attempts':attempt+1,'editorial_notes':[]}
    if progress:
        progress('分幕修订要求验收通过；候选已生成，保存后生效' if review.get('status')=='passed'
                 else '分幕候选已生成，但要求验收未通过或未完成，请核对后再保存')
    if audit:
        audit.write('user_plan_revision_result',planning_review=review,
                    chapter_title=candidate['chapter_title'])
    return {**candidate,'planning_review':review,
            'log_path':str(getattr(audit,'path','')) if audit else ''}


def _plan_title(value, chapter_number=None):
    """回目仅保留标题文字，兼容模型仍输出旧编号的情况。"""
    title = str(value or "").strip()
    if not title:
        return title
    title = re.sub(r"^第\s*[\d零〇一二三四五六七八九十百千万两]+\s*[章回节]\s*[：:·.、—-]*\s*", "", title)
    title = re.sub(r"^chapter\s+\d+\s*[:.\-–—]*\s*", "", title, flags=re.IGNORECASE).strip()
    if not title:
        raise ValueError("章节规划缺少回目文字")
    return title


def _chapter_plan_issues(data: dict, num_beats: int, previous_context: str) -> list[str]:
    """对模型规划做轻量结构检查，失败时交给模型重写一次。"""
    issues = []
    beats = data.get("beats") if isinstance(data, dict) else None
    if not isinstance(beats, list) or len(beats) != num_beats:
        return [f"必须恰好输出 {num_beats} 幕"]
    if not str(data.get("title") or "").strip():
        issues.append("章节规划缺少回目文字")
    required = ("desc",)
    for index, beat in enumerate(beats, 1):
        if not isinstance(beat, dict):
            issues.append(f"第 {index} 幕不是对象")
            continue
        missing = [key for key in required if not beat.get(key)]
        if beat.get("desc") and (not isinstance(beat["desc"], str) or not beat["desc"].strip()):
            issues.append(f"第 {index} 幕 desc 必须是有效文本")
        for key in ("known_before", "new_facts", "new_elements"):
            if key in beat and not isinstance(beat[key], list):
                issues.append(f"第 {index} 幕字段 {key} 必须是数组")
        if missing:
            issues.append(f"第 {index} 幕缺少字段：{','.join(missing)}")
        elements = beat.get("new_elements")
        for element in elements if isinstance(elements, list) else []:
            if not isinstance(element, dict):
                issues.append(f"第 {index} 幕新增元素不是对象")
                continue
    return issues


def _chapter_plan_advisories(data):
    """创作建议不参与结构失败与自动重试。"""
    notes, povs, elements = [], set(), []
    for index, beat in enumerate(data.get("beats") or [], 1):
        if not isinstance(beat, dict):
            continue
        if beat.get("pov"):
            povs.add(str(beat["pov"]))
        for key in ("pov", "time", "location", "end_state"):
            if not beat.get(key):
                notes.append(f"第{index}幕未说明{key}，按需补充")
        if isinstance(beat.get("new_elements"), list):
            elements.extend(beat["new_elements"])
    if len(povs) > 2:
        notes.append("视角多于两个，请留意叙事聚焦；不阻止生成")
    if len(elements) > 1:
        notes.append("登记的主要新增元素多于一个，请区分主要设定与普通线索；不阻止生成")
    return notes


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
