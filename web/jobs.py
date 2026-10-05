# -*- coding: utf-8 -*-
"""本机长任务：状态落盘，浏览器关闭后仍可查看。"""
import json
import logging
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path


class JobConflictError(ValueError):
    """任务占用同一处理流程或共享写入资源。"""


def task_resources(kind: str) -> set[str]:
    if kind in {"vectors", "chapter_vector", "generate"}:
        return {"vectors"}
    if kind in {"plan", "plan_revision"}:
        return {"plan"}
    return {kind}


class JobStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._active: set[tuple[str, str]] = set()
        self._resources: dict[tuple[str, str], set[str]] = {}

    @staticmethod
    def _directory(project_dir: str) -> Path:
        directory = Path(project_dir) / "runs" / "web_jobs"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _save(self, project_dir: str, job: dict) -> None:
        directory = self._directory(project_dir)
        target = directory / f"{job['id']}.json"
        payload = json.dumps(job, ensure_ascii=False, default=str)
        with self._lock:
            fd, temp_name = tempfile.mkstemp(prefix=f".{job['id']}.", suffix=".tmp",
                                             dir=directory)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    stream.write(payload)
                # Windows 上文件刚被读取、索引或扫描时，替换目标文件可能暂时返回
                # WinError 5/32。保留同一份完整临时文件，短暂退避后重试。
                for attempt in range(8):
                    try:
                        os.replace(temp_name, target)
                        break
                    except OSError as exc:
                        transient = isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in (5, 32)
                        if not transient or attempt == 7:
                            raise
                        time.sleep(min(0.025 * 2 ** attempt, 0.4))
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)

    def get(self, project_dir: str, job_id: str) -> dict | None:
        if not re_job_id(job_id):
            return None
        path = self._directory(project_dir) / f"{job_id}.json"
        with self._lock:
            if not path.is_file():
                return None
            job = json.loads(path.read_text(encoding="utf-8"))
            interrupted = (job.get("status") == "running"
                           and (project_dir, job_id) not in self._active)
        if interrupted:
            job["status"] = "interrupted"
            job["message"] = "服务已重启；任务未确认完成，请检查产物。"
            self._save(project_dir, job)
        return job

    def list(self, project_dir: str, limit: int = 25) -> list[dict]:
        jobs = [job for path in self._directory(project_dir).glob("*.json")
                if (job := self.get(project_dir, path.stem))]
        jobs.sort(key=lambda job: (job.get("created_at") or "", job.get("id") or ""),
                  reverse=True)
        return jobs[:limit]

    def start(self, project_dir: str, kind: str, work, *, pass_job_id: bool = False) -> dict:
        resources = task_resources(kind)
        with self._lock:
            for key, occupied in self._resources.items():
                if key[0] == project_dir and ("corpus" in resources or "corpus" in occupied or resources & occupied):
                    raise JobConflictError("该处理流程或共享写入资源已有运行中的任务，请等待完成；其他独立流程可同时执行。")
            job_id = time.strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:8]
            self._active.add((project_dir, job_id))
            self._resources[(project_dir, job_id)] = resources
        job = {"id": job_id, "kind": kind, "status": "running", "message": "已开始",
               "created_at": time.strftime("%Y-%m-%d %H:%M:%S"), "events": [], "result": None}
        try:
            self._save(project_dir, job)
        except Exception:
            with self._lock:
                self._active.discard((project_dir, job_id))
                self._resources.pop((project_dir, job_id), None)
            raise

        def update(message: str, data: dict | None = None, *, level: str = "info"):
            event = {"time": time.strftime("%H:%M:%S"), "message": message,
                     "data": data or {}, "level": level}
            job["events"].append(event)
            job["events"] = job["events"][-150:]
            job["message"] = message
            self._save(project_dir, job)

        def runner():
            try:
                result = work(update, job_id) if pass_job_id else work(update)
                job["result"] = result
                job["status"] = "completed"
                update("任务完成", level="success")
            except Exception as exc:
                job["status"] = "failed"
                try:
                    update(f"任务失败：{exc}", level="error")
                except Exception:
                    logging.exception("无法写入后台任务失败状态：%s", job_id)
            finally:
                with self._lock:
                    self._active.discard((project_dir, job_id))
                    self._resources.pop((project_dir, job_id), None)

        threading.Thread(target=runner, daemon=True).start()
        return {"id": job_id, "kind": kind, "status": "running"}


def re_job_id(value: str) -> bool:
    return bool(value) and all(char.isalnum() or char == "_" for char in value)
