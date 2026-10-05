# core/consistency.py
# -*- coding: utf-8 -*-
"""使用 LLM 审校人物、设定与时间线一致性。"""

from core.continuation import ContinuationLLM
import json
import re


AUDIT_OUTPUT = """
最终只输出 JSON 对象：{"requirements": ["简短可执行修改要求"], "details": "准确引文与简短理由", "notes": ["需要核对但不能确定的提醒"]}。
先按三类处理：
1. 明确冲突，以及确定的笔误、计数错误、无歧义的指称不一致，进入requirements。低风险不等于不修改；可直接纠正的文字问题不要塞入notes。
2. 不确定事实、可能存在的歧义，放notes；不擅自选择事实。无法决定真实数量、身份等时不得给出未经证实的答案。
3. 合理省略、不同详略的概述和正常场景转换不报告，既不进入requirements也不进入notes。
requirements最多6条，每条不超过120字，只写改什么、保留什么，不写理由、不写二选一方案。事实无法选定但表达明确自相矛盾时，可要求保留已确立的关系并统一矛盾表述，不凭空设定答案。
details为每项要求保留引文与简短理由：剧情冲突引用互不兼容的原句；确定文字错误可只引用错误处并说明核验依据，不要求凑两处证据。
details不要重复展开同一根因。不重复列出前一轮已发现的问题。
概述不等于完整列举，未描写不等于未发生；背景起始时间不冻结后续时间。不得把合理省略当冲突。
输出前分别核对全文中的数量或位置关系、信息取得先后、记录与实物指称、字数表述。只检查原文实际出现的内容，不按清单强造问题；不能用笼统“证据不足”跳过明确矛盾。
没有明确冲突或确定文字错误时requirements为[]；details说明本轮未发现可确定问题，而非保证无错误。
"""


def parse_audit_output(raw):
    match = re.search(r"\{[\s\S]*\}", raw or "")
    if not match:
        raise ValueError("审校未返回结构化结果")
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        from json_repair import repair_json
        data = json.loads(repair_json(match.group()))
    if not isinstance(data, dict) or not isinstance(data.get("details"), str):
        raise ValueError("审校缺少详细依据")
    for key in ("requirements", "notes"):
        if not isinstance(data.get(key), list) or any(not isinstance(x, str) for x in data[key]):
            raise ValueError("审校要求或提醒格式无效")
    if len(data["requirements"]) > 6 or any(not x.strip() or len(x) > 120 for x in data["requirements"]):
        raise ValueError("简短修订要求超出长度或数量限制")
    return data

CONSISTENCY_PROMPT = """\
请检查下面的待检章节与既有设定之间是否存在明确冲突。先按时间顺序还原人物在上一章中的状态变化，再进行判断。

【本书背景与设定】
{background}

【上一章有效摘要与章末原文｜最高优先级】
{previous}

【最近 Wiki 事实｜中等优先级】
{wiki}

【语义检索补充片段｜可能来自母本或较早章节】
{retrieved}

【本次待检章节】
{chapter_text}

检查重点：
1. 人物：姓名、身份、称谓、性格口吻是否前后一致。
2. 设定：世界观、地理、器物、法术规则是否与母本冲突。
3. 时间线：事件先后、季节、行程是否自洽。
4. 情节：是否与前文设定或已发生事件矛盾。

证据与时间规则：
1. 证据优先级为：上一章章末原文 > 上一章有效摘要 > 最近 Wiki > 语义检索片段 > 较早背景。
2. 同一章内较晚发生的行动、决定和状态，会更新较早状态。人物先向西同行、后来因新事件分道南行，属于连续变化，不是冲突。
3. “准备、打算、暂不、尝试、听说”等不是永久状态；不得据此否定后续明确发生的行动。
4. 只有在按时间顺序仍无法同时成立时，才能判为冲突。证据不足时写“证据不足”，不得推断成冲突。
5. 必须说明依据来自“上一章章末”“上一章摘要”“Wiki”还是“补充检索”；不得把续写章节误称为母本原文。
6. 同一根因造成的多处表现合并为一条，不要把同一个去向问题拆成数条重复结论。

若存在冲突，请指出具体位置并给出修改建议；若没有明显冲突，请回复“无明显冲突”。
"""


def collect_audit_claims(llm, text, parse, audit=None, progress=None):
    """长文分段建立可定位的断言索引；索引不是新的事实来源或审校结论。"""
    from core.model_response import complete_review
    if len(text) < 3000:
        return []
    claims = []
    chunks = [(offset, text[offset:offset + 2400]) for offset in range(0, len(text), 2400)]
    for index, (offset, chunk) in enumerate(chunks, 1):
        if progress:
            progress(f"正在整理同章关键断言 {index}/{len(chunks)}")
        prompt = """只整理这段小说中的可核对断言，不审校、不判断冲突、不提出修改。
保留人物/对象、事件时段、状态或行为、信息来源与确定程度。优先提取限制后续行动的断言，以及同一对象被反复提及的记录；不能只写场景梗概。正常日常描写可省略，不限于特定题材。
说话人自述与叙述者事实要区分，推测不得改成事实。片段边界指向不明就标记不明，不猜人物身份。
第一人称行为的主体必须是说话人，不得因为他提到别人就把自己的亲历行为归给别人。优先保留会改变行动前提的行为与明确否定，不用大量普通日常占满索引。
输出 JSON {"claims":[{"subject":"原文对象","time":"原文时段或不明","claim":"简短断言","basis":"叙述/自述/转述/推测","quote":"本片段中一段连续准确原句"}]}，最多12项。没有可核对内容则为空数组。
【正文片段】
""" + chunk
        if audit:
            audit.write("audit_claim_request", chunk=index, total=len(chunks), offset=offset, prompt=prompt)
        raw = complete_review(llm, prompt, audit=audit, progress=progress,
                              event_prefix="audit_claim", event_fields={"chunk": index},
                              system="你是原文证据整理员，只输出 JSON，不做审校结论。",
                              temperature=0.1, num_predict=2400, disable_thinking=True)
        data = parse(raw)
        if not isinstance(data.get("claims"), list):
            raise ValueError("证据整理未返回 claims 数组")
        located, rejected = [], []
        for row in data["claims"][:12]:
            if not isinstance(row, dict):
                continue
            quote = str(row.get("quote") or "").strip()
            pos = chunk.find(quote) if quote else -1
            if pos < 0:
                rejected.append(row)
                continue
            located.append({**row, "source_id": "T01", "offset": offset + pos,
                            "quote": quote, "chunk": index})
        claims.extend(located)
        if audit:
            audit.write("audit_claim_index", chunk=index, claims=located, rejected=rejected)
    return claims


def check_consistency(chapter_text: str, settings: dict, retrieved_context: str,
                      llm_config: dict, temperature: float = 0.3,
                      previous_context: str = "", wiki_history: str = "",
                      progress=None, audit=None, structured=False,
                      project_dir=None, chapter_number=None):
    """保留旧调用入口，默认改为整体故事审校；细节实验不进入生产路径。"""
    from core.story_review import check_story
    output = check_story(chapter_text, settings, retrieved_context, llm_config,
                         previous_context, wiki_history, progress, audit,
                         project_dir, chapter_number)
    return output if structured else output["report"]


def verify_audit_issue(candidate, sources):
    """定位证据是程序职责，是否真正互斥仍由审校 Skill 判断。"""
    item = dict(candidate) if isinstance(candidate, dict) else {}
    normalized = lambda text: re.sub(r"\s+", "", str(text)).replace('“', '"').replace('”', '"')
    located = []
    by_id = {row["id"]: row for row in sources}
    for cite in item.get("citations", []) if isinstance(item.get("citations"), list) else []:
        if not isinstance(cite, dict):
            continue
        quote = str(cite.get("quote") or "").strip()
        claimed = str(cite.get("source_id") or "")
        row = by_id.get(claimed)
        if not (row and quote and normalized(quote) in normalized(row["text"])):
            # 不跨来源恢复：错误的历史归属本身可能正是冲突结论的前提。
            row = None
        located.append({"source_id": row["id"] if row else claimed, "quote": quote,
                        "verified": bool(row), "source_kind": row["kind"] if row else None})
    valid = [c for c in located if c["verified"]]
    has_current = any(c["source_kind"] == "current_body" for c in valid)
    count = len({(c["source_id"], normalized(c["quote"])) for c in valid})
    suggestion = str(item.get("suggestion") or "").strip()
    item.update(citations=located, suggestion=suggestion)
    item["evidence_verified"] = bool(item.get("problem") and suggestion and len(suggestion) <= 120
        and item.get("evidence_state") == "confirmed" and has_current
        and located and all(c["verified"] for c in located)
        and ((item.get("kind") == "conflict" and count >= 2) or
             (item.get("kind") == "text_error" and count >= 1)))
    item["evidence_status"] = "已定位，语义互斥判断由模型负责" if item["evidence_verified"] else "引文或确定性不足，不自动修订"
    return item
