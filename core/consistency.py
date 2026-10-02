# core/consistency.py
# -*- coding: utf-8 -*-
"""使用 LLM 审校人物、设定与时间线一致性。"""

from core.continuation import ContinuationLLM

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


def check_consistency(chapter_text: str, settings: dict, retrieved_context: str,
                      llm_config: dict, temperature: float = 0.3,
                      previous_context: str = "", wiki_history: str = "") -> str:
    """调用 LLM 做一致性审校，返回报告文本。"""
    prompt = CONSISTENCY_PROMPT.format(
        background=settings.get("background", "") or "(未填写)",
        previous=previous_context or "(未提供上一章资料)",
        wiki=wiki_history or "(未提供 Wiki 事实)",
        retrieved=retrieved_context or "(未检索到相关片段)",
        chapter_text=chapter_text,
    )
    llm = ContinuationLLM(llm_config)
    result = llm.complete(prompt, system="你是严谨的小说审校编辑，只输出审校结论。",
                          temperature=temperature, num_predict=2048)
    return result or "审校模型无回复。"
