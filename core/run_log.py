# -*- coding: utf-8 -*-
"""按次保存续写运行记录；默认不落盘完整 prompt 或生成正文。"""
import json
import os
import threading
import time
import uuid


class GenerationRunLogger:
    def __init__(self, project_dir: str, chapter_number: int):
        self.run_id = uuid.uuid4().hex[:12]
        self._lock = threading.Lock()
        directory = os.path.join(project_dir, "runs")
        os.makedirs(directory, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        self.path = os.path.join(directory, f"chapter_{chapter_number}_{stamp}_{self.run_id}.jsonl")

    def write(self, event: str, **data) -> None:
        record = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "run_id": self.run_id,
            "event": event,
            **data,
        }
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def latest_run_log(project_dir: str) -> str | None:
    directory = os.path.join(project_dir, "runs")
    if not os.path.isdir(directory):
        return None
    files = [os.path.join(directory, name) for name in os.listdir(directory)
             if name.endswith(".jsonl")]
    return max(files, key=os.path.getmtime) if files else None
