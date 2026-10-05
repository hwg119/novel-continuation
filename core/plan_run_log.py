# -*- coding: utf-8 -*-
"""章节规划的完整审计日志；仅保存在本机工程内。"""
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path


class PlanRunLogger:
    def __init__(self, project_dir: str, job_id: str, model_name: str = "",
                 chapter_number: int = 0, secrets: tuple[str, ...] = ()):
        if not re.fullmatch(r"[A-Za-z0-9_]+", job_id):
            raise ValueError("无效的任务编号")
        self.path = Path(project_dir) / "runs" / "plan_logs" / f"{job_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._secrets = tuple(value for value in secrets if value)
        self.write("run_started", model=model_name, chapter_number=chapter_number)

    def write(self, event: str, **fields) -> None:
        def redact(value):
            if isinstance(value, str):
                for secret in self._secrets:
                    value = value.replace(secret, "[REDACTED_API_KEY]")
                return value
            if isinstance(value, dict):
                return {key: redact(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [redact(item) for item in value]
            return value

        entry = {"timestamp": datetime.now(timezone.utc).isoformat(),
                 "event": event, **redact(fields)}
        with self._lock, self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
            stream.flush()


def read_plan_run_log(project_dir: str, job_id: str) -> list[dict]:
    if not re.fullmatch(r"[A-Za-z0-9_]+", job_id):
        raise ValueError("无效的任务编号")
    path = Path(project_dir) / "runs" / "plan_logs" / f"{job_id}.jsonl"
    if not path.is_file():
        raise FileNotFoundError(path)
    entries = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                entries.append({"event": "invalid_log_line", "raw": line.rstrip("\n")})
    return entries
