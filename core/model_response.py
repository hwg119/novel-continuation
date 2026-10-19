"""结构化审查的空响应恢复：只补试当前请求一次，不重放工具。"""
import time


def complete_review(llm, prompt, *, audit=None, progress=None, event_prefix="research",
                    event_fields=None, response_event=None, **kwargs):
    fields = dict(event_fields or {})
    request_prompt = prompt
    request_kwargs = dict(kwargs)
    for attempt in (1, 2):
        started = time.monotonic()
        raw = llm.complete(request_prompt, **request_kwargs)
        diagnostics = getattr(llm, "last_response_diagnostics", {}) or {}
        if audit:
            audit.write(response_event or f"{event_prefix}_model_response", **fields, attempt=attempt,
                        raw_response=raw, elapsed_ms=round((time.monotonic() - started) * 1000),
                        response_diagnostics=diagnostics)
        finish_reason = str(diagnostics.get("finish_reason") or diagnostics.get("done_reason") or "").lower()
        truncated = finish_reason in {"length", "max_tokens"}
        if truncated and attempt == 1:
            configured = int(getattr(llm, "cfg", {}).get("max_tokens", 4096) or 4096)
            current = int(request_kwargs.get("num_predict") or 1600)
            request_kwargs["num_predict"] = min(configured, max(4096, current * 2))
            request_prompt = (prompt + "\n\n【输出长度修正】上次回答因达到输出上限而被截断。"
                              "请从头返回完整且更精简的 JSON；最多保留 4 个真正影响续写的问题，"
                              "每个字段只写结论和必要证据，不复述整幕或整段要求。")
            if progress:
                progress("审查结果在 JSON 完成前被截断，正在提高预算并精简重试")
            if audit:
                audit.write(f"{event_prefix}_model_retry", **fields,
                            reason="output_truncated", attempt=2,
                            previous_num_predict=current,
                            num_predict=request_kwargs["num_predict"])
            continue
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
                            attempt=2, prompt=request_prompt)
    raise ValueError("审查模型连续两次返回空内容，审查未完成")
