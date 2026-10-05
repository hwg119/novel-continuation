"""结构化审查的空响应恢复：只补试当前请求一次，不重放工具。"""
import time


def complete_review(llm, prompt, *, audit=None, progress=None, event_prefix="research",
                    event_fields=None, **kwargs):
    fields = dict(event_fields or {})
    for attempt in (1, 2):
        started = time.monotonic()
        raw = llm.complete(prompt, **kwargs)
        if audit:
            audit.write(f"{event_prefix}_model_response", **fields, attempt=attempt,
                        raw_response=raw, elapsed_ms=round((time.monotonic() - started) * 1000),
                        response_diagnostics=getattr(llm, "last_response_diagnostics", {}))
        if isinstance(raw, str) and raw.strip():
            return raw
        if audit:
            audit.write(f"{event_prefix}_empty_response", **fields, attempt=attempt,
                        retrying=attempt == 1)
        if attempt == 1:
            if progress:
                progress("审查模型返回空内容，正在补试一次（保留已有查证结果）")
            if audit:
                audit.write(f"{event_prefix}_model_retry", **fields, reason="empty_response",
                            attempt=2, prompt=prompt)
    raise ValueError("审查模型连续两次返回空内容，审查未完成")
