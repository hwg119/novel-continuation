# -*- coding: utf-8 -*-
"""第一批 Web API：复用既有工程索引与章节文件。"""
import hashlib
import logging
import os
import tempfile
import threading
import time
import re
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from core.chapter_revision import save_revision_snapshot
from core.config_manager import APP_ROOT, get_embedding_config, load_config
from core.project_manager import (chapter_path, list_chapter_files, list_projects,
                                  load_project_settings, settings_for_chapter)
from web.jobs import JobStore, JobConflictError


logger = logging.getLogger(__name__)


class ChapterSave(BaseModel):
    text: str
    revision: str


def _revision(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _project_id(path: str) -> str:
    return hashlib.sha256(os.path.normcase(os.path.abspath(path)).encode("utf-8")).hexdigest()[:16]


def create_app(workspace_root: str, frontend_dist: str | None = None,
               config_file: str | None = None) -> FastAPI:
    app = FastAPI(title="小说续写工作台", version="0.1.0")
    save_lock = threading.Lock()
    jobs = JobStore()
    config_path = config_file or os.path.join(APP_ROOT, "config.json")

    @app.exception_handler(JobConflictError)
    async def job_conflict_error(_request, exc: JobConflictError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(Exception)
    async def json_internal_error(_request, exc: Exception):
        """API 意外异常也必须返回 JSON，避免前端收到不可展示的纯文本 500。"""
        logger.exception("Unhandled API error", exc_info=exc)
        detail = str(exc).strip() or exc.__class__.__name__
        return JSONResponse(status_code=500, content={"detail": f"服务内部错误：{detail}"})

    def vector_store_initialized(project: str) -> bool:
        store = Path(project) / "vectorstore"
        return (store / "vectors.faiss").is_file() and (store / "meta.db").is_file()

    def start_chapter_vector_update(project: str, number: int, path: Path,
                                    chapter_text: str) -> dict | None:
        """已有索引时后台替换单章；正文保存成功不依赖该任务成功。"""
        if not vector_store_initialized(project):
            return None
        embedding = get_embedding_config(load_config(config_path))

        def work(update):
            from core.knowledge import replace_chapter_vector
            update(f"正在更新第 {number} 章向量", {"chapter": number})
            segments = replace_chapter_vector(
                embedding.get("api_key", ""), embedding.get("base_url", ""),
                embedding.get("interface_format", ""), embedding.get("model_name", ""),
                number, str(path), project, chapter_text=chapter_text)
            if segments <= 0 and chapter_text.strip():
                raise ValueError("章节向量更新失败或未生成有效分段，请检查向量模型配置")
            update(f"第 {number} 章向量已更新",
                   {"chapter": number, "segments": segments})
            return {"chapter": number, "segments": segments}

        return jobs.start(project, "chapter_vector", work)

    def project_path(project_id: str) -> str:
        for entry in list_projects(workspace_root):
            if _project_id(entry["path"]) == project_id:
                return entry["path"]
        raise HTTPException(404, "工程不存在或未登记")

    def existing_chapter(project_id: str, number: int) -> tuple[str, Path]:
        project = project_path(project_id)
        if number < 1:
            raise HTTPException(400, "章号必须大于零")
        path = Path(chapter_path(project, number))
        if not path.is_file():
            raise HTTPException(404, "章节不存在")
        return project, path

    @app.get("/api/projects")
    def projects():
        return [{"id": _project_id(item["path"]), "name": item["name"],
                 "path": item["path"]} for item in list_projects(workspace_root)]

    @app.get("/api/projects/{project_id}/chapters")
    def chapters(project_id: str):
        project = project_path(project_id)
        run_times = {}
        for run in (Path(project) / "runs").glob("chapter_*_*.jsonl"):
            match = re.match(r"chapter_(\d+)_(\d{8}_\d{6})_", run.name)
            if match:
                number = int(match.group(1))
                try:
                    stamp = time.strftime("%Y-%m-%d %H:%M:%S",
                                          time.strptime(match.group(2), "%Y%m%d_%H%M%S"))
                except ValueError:
                    continue
                run_times[number] = max(run_times.get(number, ""), stamp)
        result = []
        for number, name, filename in list_chapter_files(project):
            source = Path(filename)
            with source.open("r", encoding="utf-8") as stream:
                title = stream.readline().strip()
            result.append({"number": number, "filename": name, "title": title,
                           "generated_at": run_times.get(number),
                           "modified_at": time.strftime("%Y-%m-%d %H:%M:%S",
                                                        time.localtime(source.stat().st_mtime))})
        return result

    @app.get("/api/projects/{project_id}/chapters/{number}/plan-status")
    def plan_status(project_id: str, number: int):
        if number < 1:
            raise HTTPException(400, "章号必须大于零")
        project = project_path(project_id)
        settings = settings_for_chapter(load_project_settings(project), number)
        beats = settings.get("beats") or []
        return {"number": number, "chapter_exists": Path(chapter_path(project, number)).is_file(),
                "has_beats": bool(beats), "beats_count": len(beats),
                "title": settings.get("chapter_title") or ""}

    @app.get("/api/projects/{project_id}/chapters/{number}/revisions")
    def revisions(project_id: str, number: int):
        project, _ = existing_chapter(project_id, number)
        records = []
        for job in jobs.list(project, limit=1000):
            if job.get("kind") != "revise" or job.get("status") != "completed":
                continue
            result = job.get("result") or {}
            if result.get("chapter_number") != number or not result.get("candidate"):
                continue
            records.append({"id": job["id"], "created_at": job.get("created_at"),
                            "kind": "ai_draft",
                            "candidate": result["candidate"], "source": result.get("source") or "",
                            "issues": result.get("issues") or [],
                            "requirements": result.get("requirements") or "",
                            "revision_mode": result.get("revision_mode") or "whole"})
        from core.chapter_revision import load_revision_snapshot
        for path in (Path(project) / "runs" / "revisions").glob(f"chapter_{number}_*_before.json"):
            try:
                snapshot = load_revision_snapshot(str(path))
            except (OSError, ValueError):
                continue
            records.append({"id": path.stem, "kind": "previous_version",
                            "created_at": time.strftime("%Y-%m-%d %H:%M:%S",
                                                        time.localtime(path.stat().st_mtime)),
                            "candidate": snapshot["text"], "source": "", "issues": []})
        records.sort(key=lambda item: (item["created_at"] or "", item["id"]), reverse=True)
        return records

    @app.get("/api/projects/{project_id}/chapters/{number}/audits")
    def chapter_audits(project_id: str, number: int):
        """返回该章已完成的 LLM 审校；任务结果本身已经持久化。"""
        project, _ = existing_chapter(project_id, number)
        records = []
        for job in jobs.list(project, limit=1000):
            if job.get("kind") != "consistency" or job.get("status") != "completed":
                continue
            result = job.get("result") or {}
            if result.get("chapter_number") != number or not result.get("report"):
                continue
            records.append({"id": job["id"], "created_at": job.get("created_at"),
                            "report": result["report"],
                            "revision_requirements": result.get("revision_requirements", ""),
                            "audit_notes": result.get("audit_notes", []),
                            "audit_kind": result.get("audit_kind", "consistency"),
                            "story_summary": result.get("story_summary", ""),
                            "story_details": result.get("story_details"),
                            "story_enhancements": result.get("story_enhancements", []),
                            "retrieved_chars": result.get("retrieved_chars") or 0,
                            "previous_context_chars": result.get("previous_context_chars") or 0,
                            "wiki_chars": result.get("wiki_chars") or 0})
        return records

    @app.get("/api/projects/{project_id}/chapters/{number}")
    def read_chapter(project_id: str, number: int):
        _, path = existing_chapter(project_id, number)
        text = path.read_text(encoding="utf-8")
        return {"number": number, "text": text, "revision": _revision(text)}

    @app.put("/api/projects/{project_id}/chapters/{number}")
    def save_chapter(project_id: str, number: int, payload: ChapterSave,
                     local_header: str | None = Header(default=None, alias="X-Novel-Workbench")):
        if local_header != "1":
            raise HTTPException(403, "缺少本机工作台请求标识")
        project, path = existing_chapter(project_id, number)
        if not payload.text.strip():
            raise HTTPException(400, "章节正文不能为空")
        with save_lock:
            old_text = path.read_text(encoding="utf-8")
            if payload.revision != _revision(old_text):
                raise HTTPException(409, "章节已被其他窗口修改，请重新载入")
            if payload.text == old_text:
                return {"number": number, "revision": _revision(old_text), "saved": False}
            save_revision_snapshot(project, number, old_text)
            fd, temp_path = tempfile.mkstemp(prefix=f"chapter_{number}_", suffix=".tmp", dir=path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    stream.write(payload.text)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_path, path)
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
            from core.chapter_revision import archive_outdated_summary
            archive_outdated_summary(project, number)
        vector_job = None
        vector_status = "not_initialized"
        try:
            vector_job = start_chapter_vector_update(project, number, path, payload.text)
            if vector_job:
                vector_status = "scheduled"
        except ValueError:
            # 工程内已有其它后台任务时，正文仍已安全保存；向量保持待更新状态。
            vector_status = "deferred"
        return {"number": number, "revision": _revision(payload.text), "saved": True,
                "vector_status": vector_status,
                "vector_job_id": vector_job.get("id") if vector_job else None}

    from web.features import register_features
    register_features(app, workspace_root, project_path, existing_chapter,
                      jobs, config_file)

    if frontend_dist and Path(frontend_dist, "index.html").is_file():
        dist = Path(frontend_dist).resolve()

        @app.get("/{page:path}", include_in_schema=False)
        def frontend(page: str):
            if page == "api" or page.startswith("api/"):
                raise HTTPException(404, "API 路由不存在；请检查后端版本")
            target = (dist / page).resolve()
            if target.is_file() and target.is_relative_to(dist):
                return FileResponse(target)
            return FileResponse(dist / "index.html")

    return app
