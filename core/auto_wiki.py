# -*- coding: utf-8 -*-
"""模型自动编纂小说 Wiki；每条事实必须有可核对的本章原文。"""
import hashlib
import json
import math
import re
import time
from pathlib import Path

from core.project_manager import chapter_path, list_chapter_files, load_project_settings
from core.utils import read_file

WIKI_SCHEMA_VERSION = 2
ADDRESS_PREDICATE_RE = re.compile(r"称呼|称作|称为|叫作|叫做|唤作")
STABLE_CATEGORIES = {
    "identity_origin",       # 姓名、出生或明确的身份来源
    "biological_kinship",   # 明确的生物学亲缘
    "intrinsic_property",   # 原文明示不可自然改变的固有属性
    "object_provenance",    # 物品的制造者或来历
    "world_rule",           # 作品世界明确陈述的恒常规则
}

ENTITY_LIST_SPLIT_RE = re.compile(r"\s*(?:,|，|、|&|\band\b|和|与)\s*", re.IGNORECASE)
GENERIC_ENGLISH_SUFFIXES = {
    "academy", "alley", "army", "battle", "cauldron", "company", "department",
    "eaters", "empire", "guild", "hospital", "inn", "kingdom", "lived", "magic",
    "ministry", "office", "order", "phoenix", "quarters", "republic", "school",
    "station", "street", "tavern", "village",
}

TYPOGRAPHIC_CHAR_MAP = {
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-",
    "\u00a0": " ", "\u202f": " ",
}
IGNORED_TYPOGRAPHIC_CHARS = {"\u00ad", "\u200b", "\u200c", "\u200d", "\ufeff"}


def _normalize_evidence(value: str, *, with_offsets: bool = False):
    """仅消除排版差异；保留标点与内容边界，避免把改写误当原文证据。"""
    normalized = []
    offsets = []
    previous_space = False
    for index, char in enumerate(str(value or "")):
        if char in IGNORED_TYPOGRAPHIC_CHARS:
            continue
        char = TYPOGRAPHIC_CHAR_MAP.get(char, char)
        if char.isspace():
            if previous_space:
                continue
            char = " "
            previous_space = True
        else:
            previous_space = False
        normalized.append(char)
        offsets.append(index)
    result = "".join(normalized)
    return (result, offsets) if with_offsets else result


def _ground_quote(text: str, quote: str) -> tuple[str, int] | None:
    """返回原文中的连续证据及偏移；不支持省略号跨段拼接。"""
    exact_offset = text.find(quote)
    if exact_offset >= 0:
        return quote, exact_offset
    normalized_text, offsets = _normalize_evidence(text, with_offsets=True)
    normalized_quote = _normalize_evidence(quote)
    if not normalized_quote:
        return None
    normalized_offset = normalized_text.find(normalized_quote)
    if normalized_offset < 0:
        return None
    start = offsets[normalized_offset]
    end = offsets[normalized_offset + len(normalized_quote) - 1] + 1
    grounded = text[start:end]
    return (grounded, start) if len(grounded) <= 120 else None


def _entity_aliases(project_dir: str) -> tuple[dict[str, str], dict[str, set[str]]]:
    """从术语表构造别名到中文规范名的映射，不改写原始 Wiki 文件。"""
    glossary = load_project_settings(project_dir).get("glossary") or {}
    if not isinstance(glossary, dict):
        glossary = {}

    pairs = [(str(zh).strip(), str(en).strip()) for zh, en in glossary.items()
             if str(zh).strip() and str(en).strip()]
    # 同一英文名存在长短两个中文译名时，优先把较完整的中文名作为规范名。
    english_canonical = {}
    for zh, en in sorted(pairs, key=lambda pair: len(pair[0]), reverse=True):
        english_canonical.setdefault(en.casefold(), zh)

    alias_to_canonical: dict[str, str] = {}
    canonical_aliases: dict[str, set[str]] = {}
    for zh, en in pairs:
        canonical = english_canonical[en.casefold()]
        canonical_aliases.setdefault(canonical, set()).update((canonical, zh, en))
        for alias in (canonical, zh, en):
            alias_to_canonical.setdefault(alias.casefold(), canonical)

    # 英文全名的末尾姓氏在全表唯一时也可作为别名。
    # 只取末尾且排除通用机构词，避免把 Magic、Office 等普通词误认成实体。
    token_owners: dict[str, set[str]] = {}
    token_spellings: dict[str, str] = {}
    for _, en in pairs:
        canonical = english_canonical[en.casefold()]
        tokens = re.findall(r"[A-Za-z][A-Za-z'’-]*", en)
        if len(tokens) < 2:
            continue
        token = tokens[-1]
        folded = token.casefold()
        if folded in GENERIC_ENGLISH_SUFFIXES:
            continue
        token_owners.setdefault(folded, set()).add(canonical)
        token_spellings.setdefault(folded, token)
    for folded, owners in token_owners.items():
        if len(owners) != 1 or len(folded) < 3:
            continue
        canonical = next(iter(owners))
        alias_to_canonical.setdefault(folded, canonical)
        canonical_aliases.setdefault(canonical, set()).add(token_spellings[folded])
    return alias_to_canonical, canonical_aliases


def _canonical_entity(name: str, aliases: dict[str, str]) -> str:
    """把单个人物/实体名归一；允许简称匹配带姓全名。"""
    value = str(name or "").strip()
    if not value:
        return ""
    exact = aliases.get(value.casefold())
    if exact:
        return exact
    folded = value.casefold()
    prefixes = []
    suffixes = []
    for alias, canonical in aliases.items():
        if len(alias) < 3:
            continue
        if folded.startswith(alias + " "):
            prefixes.append((len(alias), canonical))
        elif folded.endswith(" " + alias):
            suffixes.append((len(alias), canonical))
    if prefixes:
        return max(prefixes)[1]
    return max(suffixes)[1] if suffixes else value


def _canonical_entities(name: str, aliases: dict[str, str]) -> list[str]:
    """只在每个成员均可识别时拆分多人主体，避免误拆普通描述。"""
    value = str(name or "").strip()
    parts = [part.strip() for part in ENTITY_LIST_SPLIT_RE.split(value)
             if part.strip() and part.strip().casefold() != "and"]
    if len(parts) > 1:
        resolved = [_canonical_entity(part, aliases) for part in parts]
        if not any(result == part for result, part in zip(resolved, parts)):
            return list(dict.fromkeys(resolved))
    direct = _canonical_entity(value, aliases)
    return [direct] if direct else []


def _query_entities(query: str, aliases: dict[str, str]) -> set[str]:
    """识别查询中出现的中英文实体别名。"""
    query = str(query or "")
    folded = query.casefold()
    found = set()
    for alias, canonical in aliases.items():
        if not alias:
            continue
        if re.fullmatch(r"[a-z0-9'’ -]+", alias):
            if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", folded):
                found.add(canonical)
        elif alias in folded:
            found.add(canonical)
    return found


def canonical_wiki_subject(project_dir: str, subject: str) -> str:
    """返回供 Wiki 页面、人物档案和续写共同使用的规范实体名。"""
    aliases, _ = _entity_aliases(project_dir)
    return _canonical_entity(subject, aliases)


def _emit_progress(progress, message: str, data: dict | None = None) -> None:
    """兼容旧的一参数回调，同时允许 Web 任务记录结构化进度。"""
    if not progress:
        return
    try:
        progress(message, data or {})
    except TypeError:
        progress(message)


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f"约 {seconds} 秒"
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"约 {hours} 小时 {minutes} 分"
    return f"约 {minutes} 分 {secs} 秒" if secs else f"约 {minutes} 分"


class _WikiEta:
    """按实际完成的模型片段动态估算剩余时间。"""
    def __init__(self, total: int):
        self.total = max(0, total)
        self.started = time.monotonic()

    def data(self, completed: int) -> dict:
        completed = min(max(0, completed), self.total)
        percent = round(completed * 100 / self.total) if self.total else 100
        data = {"completed_units": completed, "total_units": self.total,
                "progress_percent": f"{percent}%"}
        if completed <= 0:
            data["remaining_time"] = "估算中（完成首个片段后更新）"
        elif completed >= self.total:
            data["remaining_time"] = "不足 1 分钟"
        else:
            elapsed = max(0.001, time.monotonic() - self.started)
            remaining_seconds = round(elapsed / completed * (self.total - completed))
            data["remaining_time"] = _format_duration(remaining_seconds)
        return data


def _chapter_work_units(project_dir: str, number: int) -> int:
    """返回需要请求模型的片段数；有效缓存不计入耗时工作量。"""
    text = read_file(chapter_path(project_dir, number))
    if not text.strip():
        return 0
    target = _wiki_dir(project_dir) / f"chapter_{number}.json"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    try:
        cached = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cached = {}
    if (cached.get("source_hash") == digest and
            cached.get("schema_version") == WIKI_SCHEMA_VERSION and
            cached.get("status") != "blocked_sensitive" and
            not _has_invalid_address_fact(cached)):
        return 0
    return max(1, math.ceil(len(text) / 4300))


def _is_sensitive_input_error(error: Exception) -> bool:
    """识别模型服务端对原文输入的安全拦截，不把它当作解析失败重试。"""
    message = str(error).lower()
    return "new_sensitive" in message or "input_sensitive" in message


def _is_address_fact(fact: dict) -> bool:
    return bool(ADDRESS_PREDICATE_RE.search(str(fact.get("predicate") or "")))


def _has_invalid_address_fact(data: dict) -> bool:
    """旧缓存中称谓若没有明确对象，无法判断说话人方向，必须重编纂。"""
    for fact in data.get("facts", []):
        if not isinstance(fact, dict) or not _is_address_fact(fact):
            continue
        if (fact.get("kind") != "relationship" or
                not str(fact.get("target") or "").strip()):
            return True
    return False


def _wiki_dir(project_dir: str) -> Path:
    path = Path(project_dir) / "wiki" / "chapters"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _extract_json(raw: str) -> list:
    # 推理模型可能把分析放进 <think>；这里只解析最终答复，避免误把
    # 分析中的 JSON 示例或列表当成可验证的事实。
    cleaned = re.sub(r"<think(?:ing)?>.*?</think(?:ing)?>", "", raw or "",
                     flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"```(?:json)?", "", cleaned).strip()
    if re.search(r"<think(?:ing)?>", cleaned, flags=re.IGNORECASE):
        raise ValueError("模型只返回了未结束的思考内容，未输出事实数组")
    match = re.search(r"\[.*\]", cleaned, flags=re.DOTALL)
    if not match:
        if re.search(r"<think(?:ing)?>", raw or "", flags=re.IGNORECASE):
            raise ValueError("模型只返回思考内容，未输出事实数组；请提高最大 tokens 或换用非推理模型")
        raise ValueError("模型未返回事实数组")
    data = json.loads(match.group(0))
    if not isinstance(data, list):
        raise ValueError("事实提取结果不是数组")
    return data


def extract_chapter_facts(project_dir: str, number: int, llm, progress=None, audit=None) -> dict:
    text = read_file(chapter_path(project_dir, number))
    if not text.strip():
        raise ValueError(f"第 {number} 章为空或不存在")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    target = _wiki_dir(project_dir) / f"chapter_{number}.json"
    if target.is_file():
        cached = json.loads(target.read_text(encoding="utf-8"))
        if (cached.get("source_hash") == digest and
                cached.get("schema_version") == WIKI_SCHEMA_VERSION and
                cached.get("status") != "blocked_sensitive" and
                not _has_invalid_address_fact(cached)):
            if audit:
                audit.write("chapter_cache_hit", chapter=number, source_hash=digest,
                            facts=len(cached.get("facts", [])))
            return cached
    if audit:
        audit.write("chapter_started", chapter=number, source_hash=digest,
                    source_chars=len(text))
    facts = []
    blocked_chunks = []
    # 分块覆盖整章；重叠区域减少跨边界事实遗漏。
    chunks = [text[start:start + 4600] for start in range(0, len(text), 4300)]
    for index, chunk in enumerate(chunks, 1):
        _emit_progress(progress, f"第 {number} 章片段 {index}/{len(chunks)}")
        prompt = f"""从下面的小说原文中提取能影响后续续写的稳定事实。
只依据所给文本，不调用你已知的书外知识；不把猜测、比喻或人物谎言当作确定事实。
优先提取人物关系与称谓、物品状态、时间地点、已发生的重要事件和未解决线索。
只保留可能影响后续续写的事实；普通天气、瞬时语气、无剧情后果的日常动作不要提取。
输出 JSON 数组，每项包含 subject、predicate、value、quote、kind、target、story_time、stable_category 八个字符串。
kind 只能是 stable（较稳定设定）、state（年龄、位置、持有物、伤势等当时状态）、event（一次性经历）、relationship（人物间关系）。
若 kind 为 relationship，target 必须写另一方的人名；无法确认 target 就不要输出该项。否则 target 留空。story_time 仅在原文明确给出故事时间时填写，否则留空。
stable 只用于少数类别，stable_category 必须写 identity_origin、biological_kinship、intrinsic_property、object_provenance、world_rule 之一；其它 kind 的 stable_category 留空。
能力、强弱比较、认知、职位、立场、资源、关系现状等只要可能变化，一律写 state；没有合法稳定类别的内容绝不可写 stable。
某件事已经发生过则写 event。无法确定是否长期成立时，保守写 state 或 event，不要写 stable。
涉及“称呼”时，只在同一条 quote 内同时存在称谓和足以确认说话人的叙述标记时输出：subject 必须是说话人，predicate 固定写“称呼”，target 必须是被称呼者，value 是实际说出的称谓，kind 必须为 relationship。
例如原文 `“小心，林舟。”陈老师喊道` 应写 subject=陈老师、target=林舟、value=林舟；绝不能把林舟写成说话人。
如果 quote 只有称谓、只有 `said he/she`、只有 `said to 某人`，或必须依赖 quote 外的上下文才能判断说话人和对象，则不要提取称呼。不能分清方向时宁可遗漏，绝不可猜测或反转 subject/target。
quote 必须逐字复制原文中连续出现的一段最短引句，不超过 120 字；不得改写字词、大小写、空格或标点，不得用省略号拼接两处文字，不得把不连续的句子合并成一条 quote。找不到能够直接复制的确切依据就不要输出该项。
每段最多 15 条，若无事实返回 []。只输出 JSON。

【第 {number} 章原文片段】\n{chunk}"""
        system_prompt = "你是小说资料编纂员，只输出可溯源的 JSON，不输出思考过程。"
        # 不能固定为 2400：推理模型的思考会消耗输出额度，导致最终 JSON 根本没生成。
        configured = getattr(llm, "cfg", {}).get("max_tokens", 2400)
        num_predict = max(2400, int(configured or 2400))
        cfg = getattr(llm, "cfg", {})
        model_name = str(cfg.get("model_name", "")).lower()
        disable_thinking = (str(cfg.get("interface_format", "")).lower() == "deepseek" or
                           (str(cfg.get("interface_format", "")).lower() == "openai"
                            and (model_name == "minimax-m3" or
                                 model_name == "deepseek-flash" or
                                 model_name.startswith("mimo-v2.6-") or
                                 model_name.startswith(("qwen3.5-", "qwen3.6-",
                                                        "qwen3.7-", "qwen3.8-",
                                                        "deepseek-v4", "glm-5.3")))))
        blocked = None
        for attempt in (1, 2):
            request_prompt = prompt if attempt == 1 else (
                "上次回复没有给出最终事实数组。请直接输出 JSON 数组，不要解释、"
                "不要输出 <think> 或分析过程。若无可验证事实，请输出 []。\n\n" + prompt)
            if audit:
                audit.write("chunk_request", chapter=number, chunk=index, attempt=attempt,
                            chunks_total=len(chunks), prompt=request_prompt, system=system_prompt,
                            temperature=0.1, num_predict=num_predict,
                            thinking_disabled=disable_thinking)
            started = time.monotonic()
            try:
                kwargs = {"system": system_prompt, "temperature": 0.1,
                          "num_predict": num_predict}
                if disable_thinking:
                    kwargs["disable_thinking"] = True
                raw = llm.complete(request_prompt, **kwargs)
                if audit:
                    audit.write("chunk_response", chapter=number, chunk=index,
                                attempt=attempt,
                                elapsed_ms=round((time.monotonic() - started) * 1000),
                                raw_response=raw,
                                response_diagnostics=getattr(llm, "last_response_diagnostics", {}))
                data = _extract_json(raw)
                break
            except Exception as exc:
                if _is_sensitive_input_error(exc):
                    blocked = {"chunk": index, "reason": "sensitive_input"}
                    if audit:
                        audit.write("chunk_blocked_sensitive", chapter=number, chunk=index,
                                    attempt=attempt, error_type=type(exc).__name__, error=str(exc))
                    break
                if attempt == 1 and isinstance(exc, ValueError):
                    if audit:
                        audit.write("chunk_retry", chapter=number, chunk=index,
                                    attempt=attempt, error=str(exc))
                    continue
                if audit:
                    audit.write("chunk_failed", chapter=number, chunk=index,
                                attempt=attempt,
                                elapsed_ms=round((time.monotonic() - started) * 1000),
                                error_type=type(exc).__name__, error=str(exc))
                raise
        if blocked:
            blocked_chunks.append(blocked)
            _emit_progress(progress, f"第 {number} 章片段 {index}/{len(chunks)} 被模型内容策略拦截，继续其余片段")
            continue
        accepted = 0
        rejected = {"not_object": 0, "missing_fields_or_long_quote": 0,
                    "quote_not_in_source": 0, "relationship_missing_target": 0,
                    "duplicate": 0}
        for item in data:
            if not isinstance(item, dict):
                rejected["not_object"] += 1
                continue
            fields = {key: str(item.get(key) or "").strip()
                      for key in ("subject", "predicate", "value", "quote",
                                  "kind", "target", "story_time", "stable_category")}
            if not all(fields[key] for key in ("subject", "predicate", "value", "quote")) or len(fields["quote"]) > 120:
                rejected["missing_fields_or_long_quote"] += 1
                continue
            grounded = _ground_quote(text, fields["quote"])
            if grounded is None:
                rejected["quote_not_in_source"] += 1
                continue
            fields["quote"], quote_offset = grounded
            if (fields["kind"] == "relationship" or _is_address_fact(fields)) and not fields["target"]:
                rejected["relationship_missing_target"] += 1
                continue
            fields["kind"] = classify_fact(fields)
            if fields["kind"] != "relationship":
                fields["target"] = ""
            if fields["kind"] != "stable":
                fields["stable_category"] = ""
            fields["story_time"] = fields["story_time"][:80]
            fact = {**fields, "chapter": number, "offset": quote_offset,
                    "source_hash": digest}
            if fact not in facts:
                facts.append(fact)
                accepted += 1
            else:
                rejected["duplicate"] += 1
        if audit:
            audit.write("chunk_parsed", chapter=number, chunk=index,
                        returned=len(data), accepted=accepted, rejected=rejected)
    result = {"chapter": number, "source_hash": digest,
              "schema_version": WIKI_SCHEMA_VERSION, "facts": facts,
              "status": "blocked_sensitive" if blocked_chunks else "complete"}
    if blocked_chunks:
        result["blocked_chunks"] = blocked_chunks
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(target)
    if audit:
        audit.write("chapter_completed", chapter=number, facts=len(facts),
                    status=result["status"], blocked_chunks=blocked_chunks,
                    cache_path=str(target))
    return result


def build_wiki(project_dir: str, llm, start: int, end: int, progress=None, audit=None) -> dict:
    files = [number for number, _, _ in list_chapter_files(project_dir)
             if start <= number <= end]
    if not files:
        raise ValueError("范围内没有章节")
    count = 0
    completed = 0
    blocked = []
    units = {number: _chapter_work_units(project_dir, number) for number in files}
    eta = _WikiEta(sum(units.values()))
    done_units = 0
    for index, number in enumerate(files, 1):
        chapter_units = units[number]
        def chapter_progress(message, _data=None, base=done_units):
            match = re.search(r"片段\s+(\d+)/(\d+)", message)
            current = base + (int(match.group(1)) - 1 if match else 0)
            _emit_progress(progress, message, eta.data(current))
        result = extract_chapter_facts(project_dir, number, llm, chapter_progress, audit)
        done_units += chapter_units
        count += len(result["facts"])
        if result.get("status") == "blocked_sensitive":
            blocked.append(number)
        else:
            completed += 1
        _emit_progress(progress, f"已编纂 {index}/{len(files)} 章，共 {count} 条事实",
                       eta.data(done_units))
    result = {"chapters": completed, "facts": count}
    if blocked:
        result["skipped_sensitive"] = blocked
    return result


def wiki_pending_chapters(project_dir: str) -> list[dict]:
    """找出尚未编纂或正文已变化的章节，不调用模型。"""
    pending = []
    directory = _wiki_dir(project_dir)
    for number, _, source in list_chapter_files(project_dir):
        text = read_file(source)
        if not text.strip():
            continue
        target = directory / f"chapter_{number}.json"
        if not target.is_file():
            pending.append({"chapter": number, "reason": "new"})
            continue
        try:
            cached = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pending.append({"chapter": number, "reason": "changed"})
            continue
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if cached.get("source_hash") != digest:
            pending.append({"chapter": number, "reason": "changed"})
        elif cached.get("schema_version") != WIKI_SCHEMA_VERSION or _has_invalid_address_fact(cached):
            pending.append({"chapter": number, "reason": "upgrade"})
        elif cached.get("status") == "blocked_sensitive":
            pending.append({"chapter": number, "reason": "sensitive"})
    return pending


def build_pending_wiki(project_dir: str, llm, progress=None, audit=None,
                       start: int | None = None, end: int | None = None,
                       force: bool = False) -> dict:
    """更新待处理章节；可限制范围，或显式强制重建范围内全部章节。"""
    if force:
        pending = [
            {"chapter": number, "reason": "force"}
            for number, _, source in list_chapter_files(project_dir)
            if (start is None or number >= start) and (end is None or number <= end)
            and read_file(source).strip()
        ]
    else:
        pending = [
            item for item in wiki_pending_chapters(project_dir)
            if (start is None or item["chapter"] >= start)
            and (end is None or item["chapter"] <= end)
        ]
    if force and not pending:
        raise ValueError("范围内没有可编纂的章节")
    count = 0
    completed = 0
    eligible = [item for item in pending if item["reason"] != "sensitive"]
    skipped = [item["chapter"] for item in pending if item["reason"] == "sensitive"]
    units = {item["chapter"]: _chapter_work_units(project_dir, item["chapter"])
             for item in eligible}
    eta = _WikiEta(sum(units.values()))
    done_units = 0
    for index, item in enumerate(eligible, 1):
        number = item["chapter"]
        chapter_units = units[number]
        def chapter_progress(message, _data=None, base=done_units):
            match = re.search(r"片段\s+(\d+)/(\d+)", message)
            current = base + (int(match.group(1)) - 1 if match else 0)
            _emit_progress(progress, message, eta.data(current))
        result = extract_chapter_facts(project_dir, number, llm, chapter_progress, audit)
        done_units += chapter_units
        count += len(result["facts"])
        if result.get("status") == "blocked_sensitive":
            skipped.append(number)
        else:
            completed += 1
        _emit_progress(progress, f"已更新 {index}/{len(eligible)} 章，共 {count} 条事实",
                       eta.data(done_units))
    result = {"chapters": completed, "facts": count,
              "remaining": len(wiki_pending_chapters(project_dir))}
    if skipped:
        result["skipped_sensitive"] = skipped
    return result


def classify_fact(fact: dict) -> str:
    """兼容旧缓存，并保守地把可变属性视为历史状态。"""
    predicate = str(fact.get("predicate") or "")
    if _is_address_fact(fact) and str(fact.get("target") or "").strip():
        return "relationship"
    if re.search(r"年龄|几岁|岁数|所在地|位置|持有|拥有|伤势|健康|生死|存活|状态|身高|外貌", predicate):
        return "state"
    kind = str(fact.get("kind") or "")
    if kind == "relationship" and str(fact.get("target") or "").strip():
        return "relationship"
    if kind == "stable":
        return ("stable" if str(fact.get("stable_category") or "") in STABLE_CATEGORIES
                else "state")
    return kind if kind in ("state", "event") else "event"


def _valid_facts(project_dir: str, before_chapter: int | None = None) -> list[dict]:
    facts = []
    seen = set()
    aliases, _ = _entity_aliases(project_dir)
    # 复核记录独立保存；章节重新编纂后仍按原事实指纹恢复修正或屏蔽结论。
    try:
        from core.wiki_review import _fact_id, _load_reviews
        review_items = _load_reviews(project_dir).get("items", {})
    except (ImportError, OSError, ValueError):
        review_items = {}
    for path in _wiki_dir(project_dir).glob("chapter_*.json"):
        match = re.fullmatch(r"chapter_(\d+)\.json", path.name)
        if not match:
            continue
        number = int(match.group(1))
        if before_chapter is not None and number >= before_chapter:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("chapter") != number:
            continue
        source = Path(chapter_path(project_dir, number))
        if not source.is_file():
            continue
        current_hash = hashlib.sha256(read_file(str(source)).encode("utf-8")).hexdigest()
        if current_hash != data.get("source_hash"):
            continue
        for fact in data.get("facts", []):
            if not isinstance(fact, dict):
                continue
            review = review_items.get(_fact_id(fact)) if review_items else None
            if review and review.get("status") in ("rejected", "uncertain"):
                continue
            if review and review.get("status") == "corrected" and review.get("corrected"):
                fact = {**fact, **review["corrected"], "review_status": "corrected"}
            if fact.get("review_status") in ("rejected", "uncertain"):
                continue
            base = dict(fact)
            base["chapter"] = number
            base["kind"] = classify_fact(base)
            base.setdefault("target", "")
            base.setdefault("story_time", "")
            base.setdefault("stable_category", "")
            if _is_address_fact(base) and (base["kind"] != "relationship" or
                                           not str(base["target"]).strip()):
                continue
            source_subject = str(base.get("subject") or "").strip()
            source_target = str(base.get("target") or "").strip()
            subjects = _canonical_entities(source_subject, aliases)
            targets = (_canonical_entities(source_target, aliases)
                       if base["kind"] == "relationship" else [""])
            for subject in subjects:
                for target in targets:
                    if not subject or (base["kind"] == "relationship" and not target):
                        continue
                    enriched = dict(base)
                    enriched["subject"] = subject
                    enriched["target"] = target
                    if subject != source_subject:
                        enriched["source_subject"] = source_subject
                    if target != source_target and source_target:
                        enriched["source_target"] = source_target
                    key = (number, enriched.get("offset", 0), subject, target,
                           enriched.get("predicate", ""), enriched.get("value", ""),
                           enriched.get("kind", ""), enriched.get("quote", ""))
                    if key in seen:
                        continue
                    seen.add(key)
                    facts.append(enriched)
    return sorted(facts, key=lambda fact: (fact["chapter"], fact.get("offset", 0)))


def list_wiki_subjects(project_dir: str, query: str = "",
                       before_chapter: int | None = None) -> list[dict]:
    grouped = {}
    aliases, canonical_aliases = _entity_aliases(project_dir)
    query_folded = query.casefold().strip()
    query_canonical = _canonical_entity(query.strip(), aliases) if query_folded else ""
    for fact in _valid_facts(project_dir, before_chapter):
        subject = str(fact.get("subject") or "").strip()
        searchable = {subject, *canonical_aliases.get(subject, set())}
        if not subject or (query_folded and subject != query_canonical and not any(
                query_folded in name.casefold() for name in searchable)):
            continue
        grouped.setdefault(subject, []).append(fact)
    return [{"subject": name, "facts": items} for name, items in
            sorted(grouped.items(), key=lambda pair: (-len(pair[1]), pair[0]))]


def wiki_entity_page(project_dir: str, subject: str,
                     before_chapter: int | None = None) -> dict:
    subject = canonical_wiki_subject(project_dir, subject)
    facts = _valid_facts(project_dir, before_chapter)
    own = [fact for fact in facts if fact.get("subject") == subject]
    relations = []
    seen = set()
    for fact in facts:
        if fact.get("kind") != "relationship":
            continue
        source = str(fact.get("subject") or "").strip()
        target = str(fact.get("target") or "").strip()
        if subject not in (source, target) or not source or not target or source == target:
            continue
        key = (source, target, fact.get("predicate"), fact.get("chapter"))
        if key not in seen:
            seen.add(key)
            relations.append({"source": source, "target": target,
                              "predicate": fact.get("predicate", ""),
                              "value": fact.get("value", ""),
                              "story_time": fact.get("story_time", ""),
                              "chapter": fact["chapter"], "quote": fact.get("quote", "")})
    stable = [fact for fact in own if fact["kind"] == "stable"]
    timeline = [fact for fact in own if fact["kind"] in ("event", "state")]
    return {"subject": subject, "facts_count": len(own), "stable": stable,
            "timeline": timeline, "relationships": relations}


def wiki_relationship_graph(project_dir: str, start_chapter: int | None = None,
                            end_chapter: int | None = None) -> dict:
    """返回全量关系网络；同一对实体的多条原文记录聚合到一条边。"""
    facts = _valid_facts(project_dir, (end_chapter + 1) if end_chapter else None)
    grouped = {}
    degrees = {}
    for fact in facts:
        chapter = int(fact.get("chapter") or 0)
        if fact.get("kind") != "relationship" or (start_chapter and chapter < start_chapter):
            continue
        source = str(fact.get("subject") or "").strip()
        target = str(fact.get("target") or "").strip()
        if not source or not target or source == target:
            continue
        pair = tuple(sorted((source, target)))
        grouped.setdefault(pair, []).append({
            "source": source, "target": target, "predicate": fact.get("predicate", ""),
            "value": fact.get("value", ""), "chapter": chapter,
            "story_time": fact.get("story_time", ""), "quote": fact.get("quote", ""),
        })
    edges = []
    for index, (pair, records) in enumerate(sorted(grouped.items()), 1):
        source, target = pair
        degrees[source] = degrees.get(source, 0) + len(records)
        degrees[target] = degrees.get(target, 0) + len(records)
        edges.append({"id": f"r{index}", "source": source, "target": target,
                      "count": len(records), "chapters": sorted({r["chapter"] for r in records}),
                      "predicates": sorted({str(r["predicate"]) for r in records if r["predicate"]}),
                      "records": records})
    nodes = [{"id": name, "label": name, "degree": degree}
             for name, degree in sorted(degrees.items(), key=lambda item: (-item[1], item[0]))]
    return {"nodes": nodes, "edges": edges, "records_count": sum(edge["count"] for edge in edges)}


def _wiki_query_terms(query: str) -> set[str]:
    """只取可帮助区分情节的词；短虚词不参与事实排序。"""
    english = re.findall(r"[A-Za-z][A-Za-z'’-]{2,}", query.casefold())
    chinese = []
    stop = {"信息", "新增", "已知", "状态", "本幕", "人物", "地点", "时间",
            "视角", "结尾", "当前", "已经", "一个", "同一", "可以", "不能"}
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", query):
        for size in (2, 3, 4):
            chinese.extend(run[index:index + size] for index in range(len(run) - size + 1))
    return {term for term in english + chinese if term not in stop}


def wiki_context(project_dir: str, query: str, max_chars: int = 2200,
                 before_chapter: int | None = None,
                 return_details: bool = False):
    """构造供续写使用的精简证据包；近期有效状态优先，旧临时状态默认淘汰。"""
    aliases, _ = _entity_aliases(project_dir)
    mentioned = _query_entities(query, aliases)
    entity_terms = _wiki_query_terms(" ".join(mentioned))
    query_terms = _wiki_query_terms(query) - entity_terms
    latest = max(0, int(before_chapter or 0) - 1)
    settings = load_project_settings(project_dir)
    planned = [int(key) for key in (settings.get("chapter_plans") or {})
               if str(key).isdigit()]
    continuation_start = min(planned) if planned else max(1, latest - 5)
    candidates = []
    dropped_old_state = 0
    dropped_unrelated = 0
    dropped_low_relevance = 0
    dropped_source_history = 0
    for fact in _valid_facts(project_dir, before_chapter):
        subject = str(fact.get("subject") or "").strip()
        target = str(fact.get("target") or "").strip()
        if subject not in mentioned and target not in mentioned:
            dropped_unrelated += 1
            continue
        chapter = int(fact.get("chapter") or 0)
        age = max(0, latest - chapter) if latest else 0
        kind = str(fact.get("kind") or "event")
        in_continuation = chapter >= continuation_start
        asks_history = bool(re.search(r"回忆|当年|过去|历史|曾经|旧事|往事", query))
        # 母本中的一次性事件和临时状态是背景历史，不因章号临近就视为续写当前状态。
        if not in_continuation and kind in ("state", "event") and not asks_history:
            dropped_source_history += 1
            continue
        if not in_continuation and kind == "relationship" and not asks_history:
            predicate = str(fact.get("predicate") or "")
            if not re.search(r"父|母|亲属|家庭关系|夫妻|配偶|兄|弟|姐|妹|子女", predicate):
                dropped_source_history += 1
                continue
        # 位置、伤势、阵营局势等旧状态最容易污染续写；超过八章不再注入。
        if kind == "state" and age > 8:
            dropped_old_state += 1
            continue
        # 主体姓名用于召回，但不计入情节相关度；否则高频人物的所有记录容易同分。
        content = " ".join(str(fact.get(key) or "") for key in
                           ("predicate", "value", "quote")).casefold()
        fact_terms = _wiki_query_terms(content) - entity_terms
        overlap_terms = query_terms & fact_terms
        overlap = len(overlap_terms)
        if _is_address_fact(fact) and not re.search(r"称呼|叫法|口吻", query):
            dropped_low_relevance += 1
            continue
        # 陈年事件/关系必须与当前幕有明确内容重合；稳定设定也不能仅因实体同名入选。
        if age > 8 and kind in ("event", "relationship", "stable") and overlap < 4:
            dropped_low_relevance += 1
            continue
        unresolved = bool(re.search(r"待|未解决|尚未|不明|失踪|寻找|线索|没有找到|查清",
                                    str(fact.get("predicate") or "") + str(fact.get("value") or "")))
        # 不为凑数加入只命中人物姓名的记录；未解决线索允许较弱的文本重合。
        minimum_overlap = 2 if unresolved and in_continuation else 3
        if overlap < minimum_overlap:
            dropped_low_relevance += 1
            continue
        recency = 95 if age <= 1 else 72 if age <= 3 else 46 if age <= 5 else 20 if age <= 8 else 0
        kind_score = {"stable": 38, "relationship": 28, "state": 24, "event": 18}.get(kind, 10)
        score = recency + kind_score + min(overlap, 18) * 9 + (28 if unresolved else 0)
        if fact.get("review_status") == "corrected":
            score += 18
        candidates.append((score, age, fact, overlap, unresolved))

    # 同一主体的可变属性只保留较晚记录，避免“过去的位置/伤势”挤进当前上下文。
    candidates.sort(key=lambda item: (-item[0], item[1], -int(item[2].get("chapter") or 0)))
    chosen = []
    seen_mutable = set()
    for score, age, fact, overlap, unresolved in candidates:
        mutable_key = (fact.get("subject"), fact.get("predicate"))
        if fact.get("kind") == "state" and mutable_key in seen_mutable:
            continue
        if fact.get("kind") == "state":
            seen_mutable.add(mutable_key)
        chosen.append((score, age, fact, overlap, unresolved))
        if len(chosen) >= 8:
            break

    labels = {"state": "近期状态", "event": "已发生事件",
              "relationship": "关系记录", "stable": "稳定设定"}
    lines = []
    selected_facts = []
    used_chars = 0
    for index, (score, age, fact, overlap, unresolved) in enumerate(chosen, 1):
        quote = str(fact.get("quote") or "").strip()[:120]
        applicability = ("仅说明当时状态，不得自动视为当前状态"
                         if fact.get("kind") == "state" else
                         "关系可能随后文变化" if fact.get("kind") == "relationship" else
                         "历史事件，不得改写为当前正在发生" if fact.get("kind") == "event" else
                         "可作为持续设定，若与近期正文冲突则以近期正文为准")
        line = (f"[W{index:02d}] 第{fact['chapter']}章·{labels.get(fact.get('kind'), '历史记录')}｜"
                f"{fact.get('subject', '')}·{fact.get('predicate', '')}：{fact.get('value', '')}｜"
                f"适用：{applicability}｜原文：{quote}")
        addition = len(line) + (1 if lines else 0)
        if lines and used_chars + addition > max_chars:
            break
        lines.append(line)
        used_chars += addition
        selected_facts.append({
            "id": f"W{len(selected_facts) + 1:02d}", "chapter": fact["chapter"],
            "kind": fact.get("kind", ""), "subject": fact.get("subject", ""),
            "target": fact.get("target", ""), "predicate": fact.get("predicate", ""),
            "value": fact.get("value", ""), "quote": quote,
            "review_status": fact.get("review_status", ""), "score": score,
            "age": age, "query_overlap": overlap, "unresolved": unresolved,
        })
    context = "\n".join(lines)
    details = {
        "query": query, "mentioned_entities": sorted(mentioned),
        "continuation_start_chapter": continuation_start,
        "candidate_count": len(candidates), "selected_count": len(selected_facts),
        "selected_chars": len(context), "dropped_old_state": dropped_old_state,
        "dropped_unrelated": dropped_unrelated,
        "dropped_low_relevance": dropped_low_relevance,
        "dropped_source_history": dropped_source_history, "facts": selected_facts,
    }
    return (context, details) if return_details else context
