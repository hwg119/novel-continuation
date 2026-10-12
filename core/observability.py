"""Optional, fail-open OTLP tracing. Importing this module needs only stdlib."""
import functools
import inspect
import json
import logging
import os
import threading
from contextlib import contextmanager

_log = logging.getLogger(__name__)
_lock = threading.Lock()
_provider = None
_tracer = None
_failed = False
_sensitive = {'api_key', 'secret', 'password', 'authorization', 'access_token', 'secrets'}


def enabled():
    return os.getenv('NOVEL_TRACE_ENABLED', '').lower() in {'1', 'true', 'yes'}


def _load_tracer():
    global _provider, _tracer, _failed
    if not enabled() or _failed:
        return None
    with _lock:
        if _tracer is not None:
            return _tracer
        try:
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            endpoint = os.getenv('NOVEL_TRACE_ENDPOINT', 'http://127.0.0.1:6006/v1/traces')
            # A private provider avoids changing other libraries' global tracing.
            _provider = TracerProvider(resource=Resource.create({
                'service.name':'novel-continuation',
                'openinference.project.name':os.getenv('NOVEL_TRACE_PROJECT', 'novel-continuation'),
            }))
            _provider.add_span_processor(BatchSpanProcessor(
                OTLPSpanExporter(endpoint=endpoint, timeout=1), max_queue_size=512,
                max_export_batch_size=32, schedule_delay_millis=1000))
            _tracer = _provider.get_tracer('novel-continuation')
            return _tracer
        except Exception:
            _failed = True
            _log.warning('可选模型追踪未启用：依赖缺失或初始化失败；继续使用本地日志。')
            return None


def _secret_values(value):
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in _sensitive:
                if isinstance(item, str) and item:
                    found.append(item)
                elif isinstance(item, (list, tuple)):
                    found.extend(x for x in item if isinstance(x, str) and x)
            else:
                found.extend(_secret_values(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.extend(_secret_values(item))
    return found


def redact(value, secrets=()):
    if isinstance(value, dict):
        return {str(k): '[REDACTED]' if str(k).lower() in _sensitive else redact(v, secrets)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(x, secrets) for x in value]
    if isinstance(value, str):
        for secret in sorted(set(secrets), key=len, reverse=True):
            if secret:
                value = value.replace(secret, '[REDACTED]')
        return value
    if value is None or isinstance(value, (int, float, bool)):
        return value
    # Never stringify arbitrary clients/configuration objects with credentials.
    return f'<{type(value).__name__}>'


def _serialized(value, secrets=()):
    text = json.dumps(redact(value, secrets), ensure_ascii=False)
    try:
        limit = max(4096, min(2_000_000, int(os.getenv('NOVEL_TRACE_MAX_CHARS', '200000'))))
    except ValueError:
        limit = 200000
    if len(text) > limit:
        return json.dumps({'truncated':True, 'total_chars':len(text), 'preview':text[:limit]}, ensure_ascii=False)
    return text


def _safe(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except Exception:
        return None


def trace_context():
    if not enabled():
        return None
    try:
        from opentelemetry.context import get_current
        return get_current()
    except Exception:
        return None


def trace_identifiers():
    if not enabled():
        return {}
    try:
        from opentelemetry.trace import get_current_span
        context = get_current_span().get_span_context()
        if context.is_valid:
            return {'trace_id':format(context.trace_id, '032x'),
                    'span_id':format(context.span_id, '016x')}
    except Exception:
        pass
    return {}


@contextmanager
def span(name, kind='CHAIN', input=None, parent=None, secrets=()):
    manager = active = None
    try:
        tracer = _load_tracer()
        if tracer is not None:
            manager = tracer.start_as_current_span(name, context=parent,
                record_exception=False, set_status_on_exception=False)
            active = manager.__enter__()
            active.set_attribute('openinference.span.kind', kind)
            active.set_attribute('input.mime_type', 'application/json')
            active.set_attribute('input.value', _serialized(input, secrets))
    except Exception:
        # If attribute recording fails after entering, still close the context.
        if manager is not None and active is not None:
            _safe(manager.__exit__, None, None, None)
        active = manager = None
    try:
        yield active
    except BaseException as exc:
        if active is not None:
            def mark_error():
                from opentelemetry.trace import Status, StatusCode
                active.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                active.add_event('business_error', {'error.type':type(exc).__name__,
                                                    'error.message':redact(str(exc), secrets)})
            _safe(mark_error)
        raise
    finally:
        if manager is not None:
            _safe(manager.__exit__, None, None, None)


def output(active, value, secrets=()):
    if active is not None:
        _safe(active.set_attribute, 'output.mime_type', 'application/json')
        _safe(lambda: active.set_attribute('output.value', _serialized(value, secrets)))


def observe(name, kind='CHAIN', parent=None):
    def decorate(function):
        signature = inspect.signature(function)
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            if not enabled():
                return function(*args, **kwargs)
            try:
                bound = signature.bind(*args, **kwargs)
                bound.apply_defaults()
                values = dict(bound.arguments)
                owner = values.pop('self', None)
                config = getattr(owner, 'cfg', {})
                secrets = _secret_values(config) + _secret_values(values)
                audit = values.get('audit')
                secrets.extend(getattr(audit, '_secrets', ()))
            except Exception:
                return function(*args, **kwargs)
            with span(name, kind, values, parent, secrets) as active:
                if active is not None:
                    initial = values.get('initial') or {}
                    settings = values.get('settings') or (initial.get('settings', {}) if isinstance(initial, dict) else {})
                    chapter = settings.get('chapter_number') if isinstance(settings, dict) else None
                    if chapter is not None:
                        _safe(active.set_attribute, 'metadata.chapter_number', str(chapter))
                    if values.get('thread_id'):
                        _safe(active.set_attribute, 'metadata.job_id', str(values['thread_id']))
                if active is not None and config:
                    _safe(active.set_attribute, 'llm.model_name', str(config.get('model_name') or ''))
                    parameters = {k:values.get(k) for k in ('temperature','num_predict','disable_thinking','thinking_effort')}
                    parameters['temperature'] = values.get('temperature') if values.get('temperature') is not None else config.get('temperature', .8)
                    parameters['max_tokens'] = values.get('num_predict') or config.get('max_tokens', 4096)
                    _safe(lambda: active.set_attribute('llm.invocation_parameters', _serialized(parameters, secrets)))
                result = function(*args, **kwargs)
                output(active, result, secrets)
                if active is not None and owner is not None:
                    diagnostics = getattr(owner, 'last_response_diagnostics', {})
                    _safe(lambda: active.set_attribute('metadata', _serialized(diagnostics, secrets)))
                return result
        return wrapped
    return decorate


def event(name, fields):
    if not enabled():
        return
    try:
        from opentelemetry.trace import get_current_span
        active = get_current_span()
        if active.is_recording():
            active.add_event(name, {'data':_serialized(fields, _secret_values(fields))})
    except Exception:
        pass


def flush(timeout_ms=1000):
    if _provider is not None:
        _safe(_provider.force_flush, timeout_millis=timeout_ms)
