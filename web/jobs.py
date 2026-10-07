# -*- coding: utf-8 -*-
"""本机长任务：状态落盘，浏览器关闭后仍可查看。"""
import json
import logging
import os
import tempfile
import threading
import time
import uuid
import sqlite3
from pathlib import Path


class JobConflictError(ValueError):
    """任务占用同一处理流程或共享写入资源。"""


class JobCancelled(Exception):
    """Cooperative stop at a task's progress boundary."""


CANCELLABLE_KINDS = {'plan', 'plan_revision', 'revise', 'consistency'}


def resumable_checkpoint(project_dir, job):
    if job.get('kind') not in {'plan','revise'} or job.get('status') not in {'failed','interrupted','cancelled'}: return False
    thread = job.get('workflow_thread') or next((e.get('data',{}).get('workflow_thread') for e in job.get('events',[]) if e.get('data',{}).get('workflow_thread')),None)
    if not thread: return False
    root = Path(project_dir)/'runs'
    database = root/('planning_checkpoints.sqlite' if job['kind'] == 'plan' else 'revision_checkpoints.sqlite')
    if not database.is_file(): return False
    if job['kind'] == 'plan' and not (root/'plan_workflows'/f'{thread}.json').is_file(): return False
    try:
        with sqlite3.connect(f'file:{database.as_posix()}?mode=ro',uri=True) as db:
            return db.execute('SELECT 1 FROM checkpoints WHERE thread_id=? LIMIT 1',(thread,)).fetchone() is not None
    except sqlite3.Error: return False


def task_resources(kind: str) -> set[str]:
    if kind in {"vectors", "chapter_vector", "generate"}:
        return {"vectors"}
    if kind in {"plan", "plan_revision"}:
        return {"plan"}
    return {kind}


class JobStore:
    def __init__(self):
        self._lock = threading.RLock()
        self._live = {}
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
            job['finished_epoch'] = time.time()
            self._save(project_dir, job)
        job['elapsed_seconds'] = max(0, round((job.get('finished_epoch') or time.time()) - job.get('started_epoch', time.time())))
        job['can_cancel'] = job.get('kind') in CANCELLABLE_KINDS and job.get('status') == 'running'
        job['can_resume'] = resumable_checkpoint(project_dir,job)
        return job

    def cancel(self, project_dir, job_id):
        with self._lock:
            live = self._live.get((project_dir, job_id))
            if not live: raise ValueError('任务已结束或服务已重启')
            job, signal = live
            if job['kind'] not in CANCELLABLE_KINDS: raise ValueError('该任务涉及文件写入，暂不支持安全取消')
            if job['status'] != 'running': raise ValueError('任务已结束')
            signal.set()
            job['cancel_requested'] = True
            job['message'] = '正在取消：等待当前调用返回后在安全节点停止'
            self._save(project_dir,job)
        return {'cancel_requested': True}

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
               "created_at": time.strftime("%Y-%m-%d %H:%M:%S"), "events": [], "result": None,
               'started_epoch':time.time(),'cancel_requested':False,'stage':'已开始'}
        cancel_signal = threading.Event()
        with self._lock: self._live[(project_dir,job_id)] = (job,cancel_signal)
        try:
            self._save(project_dir, job)
        except Exception:
            with self._lock:
                self._active.discard((project_dir, job_id))
                self._resources.pop((project_dir, job_id), None)
                self._live.pop((project_dir,job_id),None)
            raise

        def update(message: str, data: dict | None = None, *, level: str = "info"):
            if job['status'] == 'running' and cancel_signal.is_set(): raise JobCancelled()
            event = {"time": time.strftime("%H:%M:%S"), "message": message,
                     "data": data or {}, "level": level}
            job["events"].append(event)
            job["events"] = job["events"][-150:]
            job["message"] = message
            job['stage'] = message
            if (data or {}).get('workflow_thread'): job['workflow_thread'] = data['workflow_thread']
            self._save(project_dir, job)

        def runner():
            try:
                result = work(update, job_id) if pass_job_id else work(update)
                with self._lock:
                    if cancel_signal.is_set(): raise JobCancelled()
                    job["result"] = result
                    job["status"] = "completed"
                    job['finished_epoch'] = time.time()
                update("任务完成", level="success")
            except JobCancelled:
                job['status'] = 'cancelled'
                job['finished_epoch'] = time.time()
                update('任务已取消；已完成的检查点保留，正文未自动应用',level='info')
            except Exception as exc:
                job["status"] = "failed"
                job['finished_epoch'] = time.time()
                try:
                    update(f"任务失败：{exc}", level="error")
                except Exception:
                    logging.exception("无法写入后台任务失败状态：%s", job_id)
            finally:
                with self._lock:
                    self._active.discard((project_dir, job_id))
                    self._resources.pop((project_dir, job_id), None)
                    self._live.pop((project_dir,job_id),None)

        threading.Thread(target=runner, daemon=True).start()
        return {"id": job_id, "kind": kind, "status": "running"}


def re_job_id(value: str) -> bool:
    return bool(value) and all(char.isalnum() or char == "_" for char in value)
