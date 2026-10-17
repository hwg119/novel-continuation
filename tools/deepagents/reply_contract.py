"""Small output-contract checks for the dialogue agent."""
import re


_WAITING = re.compile(r"(?:请|烦请)?(?:稍候|稍等|等候|耐心等待)")
_ACTION = re.compile(r"(?:提交|执行|启动|开始|生成|修改|修订|审校|保存|应用)")
_IMMEDIATE = re.compile(r"(?:我这就|现在就|马上|立即|正在|接下来(?:会|将)?|我会|我将)")


def unfulfilled_action_promise(text):
    """True when a final reply claims an operation is about to happen.

    Real operations are represented by a propose_action tool call. A plain final
    answer must never leave the UI looking as if invisible work is still running.
    """
    value = str(text or '').strip()
    return bool(value and _ACTION.search(value)
                and (_WAITING.search(value) or _IMMEDIATE.search(value)))
