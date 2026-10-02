# -*- coding: utf-8 -*-
"""工作台功能的 Web 路由；实际工作由 core 模块执行。"""
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Callable

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.config_manager import (APP_ROOT, get_embedding_config, get_llm_config,
                                 load_config, save_config)
from core.project_manager import (DEFAULT_PROJECT_SETTINGS, chapter_path, create_project,
                                  import_existing_project, list_chapter_files,
                                  load_project_settings, remove_project, save_project_settings,
                                  settings_for_chapter)
from core.utils import read_file
from web.jobs import JobStore


class NamedConfig(BaseModel):
    name: str
    values: dict
    original_name: str = ""


class RenameConfig(BaseModel):
    new_name: str


class ConfigOrder(BaseModel):
    names: list[str] = []


class ProjectRequest(BaseModel):
    name: str = ""
    parent: str = ""
    path: str = ""


class RangeRequest(BaseModel):
    start: int
    end: int


class ChapterSearchRequest(BaseModel):
    query: str
    limit: int = 12


class GenerateRequest(BaseModel):
    number: int
    model_name: str = ""
    title: str = ""
    beats_limit: int | None = None
    overwrite: bool = False


class ChapterRequest(BaseModel):
    number: int
    model_name: str = ""
    requirements: str = ""
    text: str = ""
    revision: str = ""
    preview: dict = {}


class MiscConfig(BaseModel):
    workspace_root: str = ""
    proxy_setting: dict = {}
    default_llm_config_name: str = ""
    last_embedding_config_name: str = ""


class LocalPickerRequest(BaseModel):
    mode: str = "directory"


def _write_required(marker: str | None = Header(None, alias="X-Novel-Workbench")):
    if marker != "1":
        raise HTTPException(403, "缺少本机工作台请求标识")


def register_features(app: FastAPI, workspace_root: str,
                      project_path: Callable[[str], str], existing_chapter,
                      jobs: JobStore, config_file: str | None):
    config_path = config_file or os.path.join(APP_ROOT, "config.json")

    def cfg():
        return load_config(config_path)

    @app.post("/api/local-picker", dependencies=[Depends(_write_required)])
    def local_picker(payload: LocalPickerRequest):
        if payload.mode not in ("directory", "project_file"):
            raise HTTPException(400, "选择类型无效")
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            try:
                if payload.mode == "directory":
                    chosen = filedialog.askdirectory(parent=root, title="选择工程目录")
                else:
                    chosen = filedialog.askopenfilename(
                        parent=root, title="选择 project.json", filetypes=[("工程设定", "project.json")])
            finally:
                root.destroy()
        except Exception as exc:
            raise HTTPException(501, f"本机文件夹选择不可用，请手动填写路径：{exc}") from exc
        if payload.mode == "project_file" and chosen:
            if Path(chosen).name.lower() != "project.json":
                raise HTTPException(400, "请选择 project.json")
            chosen = str(Path(chosen).parent)
        return {"path": chosen or ""}

    def llm(name: str = ""):
        config = cfg()
        if name and name not in config.get("llm_configs", {}):
            raise HTTPException(400, "模型配置不存在")
        value = get_llm_config(config, name or None)
        if not value:
            raise HTTPException(400, "未配置大模型")
        if str(value.get("interface_format", "")).lower() != "ollama" and not value.get("api_key"):
            raise HTTPException(400, "模型配置缺少 API Key")
        return value

    @app.get("/api/config")
    def get_config():
        config = cfg()
        for group in ("llm_configs", "embedding_configs"):
            for value in config.get(group, {}).values():
                value["has_api_key"] = bool(value.get("api_key"))
                value["api_key"] = ""
        return {key: config.get(key) for key in (
            "llm_configs", "embedding_configs", "default_llm_config_name", "last_llm_config_name",
            "last_embedding_config_name", "workspace_root", "proxy_setting")}

    @app.put("/api/config/misc", dependencies=[Depends(_write_required)])
    def save_misc(payload: MiscConfig):
        config = cfg()
        if payload.workspace_root and not Path(payload.workspace_root).is_dir():
            raise HTTPException(400, "工作目录不存在")
        default_llm = (payload.default_llm_config_name
                       or config.get("default_llm_config_name")
                       or config.get("last_llm_config_name"))
        if default_llm not in config.get("llm_configs", {}):
            raise HTTPException(400, "默认续写模型不存在")
        embedding_name = (payload.last_embedding_config_name
                          or config.get("last_embedding_config_name"))
        if embedding_name not in config.get("embedding_configs", {}):
            raise HTTPException(400, "当前向量模型不存在")
        config["workspace_root"] = payload.workspace_root
        config["proxy_setting"] = payload.proxy_setting
        config["default_llm_config_name"] = default_llm
        config["last_embedding_config_name"] = embedding_name
        if not save_config(config, config_path):
            raise HTTPException(500, "配置保存失败")
        return {"saved": True, "restart_required": True}

    @app.post("/api/config/{group}/{name}/test", dependencies=[Depends(_write_required)])
    def test_named_config(group: str, name: str):
        config = cfg()
        if group not in ("llm_configs", "embedding_configs") or name not in config.get(group, {}):
            raise HTTPException(404, "配置不存在")
        value = config[group][name]

        def work(update):
            update("正在测试模型连接")
            if group == "llm_configs":
                from core.continuation import ContinuationLLM
                return {"reply": ContinuationLLM(value).complete(
                    "请只回复 OK", num_predict=64, disable_thinking=True)[:200]}
            from embedding_adapters import create_embedding_adapter
            adapter = create_embedding_adapter(
                value.get("interface_format", ""), value.get("api_key", ""),
                value.get("base_url", ""), value.get("model_name", ""))
            return {"dimensions": len(adapter.embed_query("测试文本"))}

        # 模型连接测试无需工程；以 workspace 根目录存储任务状态。
        return jobs.start(workspace_root, "model_test", work)

    @app.get("/api/config/jobs/{job_id}")
    def config_job(job_id: str):
        job = jobs.get(workspace_root, job_id)
        if not job:
            raise HTTPException(404, "任务不存在")
        return job

    @app.put("/api/config/{group}", dependencies=[Depends(_write_required)])
    def save_named_config(group: str, payload: NamedConfig):
        if group not in ("llm_configs", "embedding_configs"):
            raise HTTPException(404, "未知配置类别")
        if not payload.name.strip():
            raise HTTPException(400, "配置名不能为空")
        config = cfg()
        name = payload.name.strip()
        original_name = payload.original_name.strip()
        configs = config.setdefault(group, {})
        if original_name and original_name != name:
            if original_name not in configs:
                raise HTTPException(404, "原配置不存在")
            if name in configs:
                raise HTTPException(409, "配置名称已存在")
            current = dict(configs[original_name])
            # 保存和重命名一次完成，避免前端先改名、再保存时留下复制项。
            config[group] = {name if key == original_name else key: value
                             for key, value in configs.items()}
            configs = config[group]
            last_key = ("last_llm_config_name" if group == "llm_configs"
                        else "last_embedding_config_name")
            if config.get(last_key) == original_name:
                config[last_key] = name
            if group == "llm_configs" and config.get("default_llm_config_name") == original_name:
                config["default_llm_config_name"] = name
        else:
            current = dict(configs.get(name, {}))
        allowed = {"interface_format", "base_url", "model_name", "api_key",
                   "temperature", "max_tokens", "timeout", "num_ctx", "retrieval_k"}
        current.update({key: value for key, value in payload.values.items()
                        if key in allowed and (key != "api_key" or value)})
        current.pop("has_api_key", None)
        configs[name] = current
        if group == "llm_configs":
            config["last_llm_config_name"] = name
        elif config.get("last_embedding_config_name") not in configs:
            config["last_embedding_config_name"] = name
        if not save_config(config, config_path):
            raise HTTPException(500, "配置保存失败")
        return {"saved": True}

    @app.put("/api/config/{group}/order", dependencies=[Depends(_write_required)])
    def reorder_named_configs(group: str, payload: ConfigOrder):
        if group not in ("llm_configs", "embedding_configs"):
            raise HTTPException(404, "未知配置类别")
        config = cfg()
        configs = config.get(group, {})
        if len(payload.names) != len(configs) or set(payload.names) != set(configs):
            raise HTTPException(400, "排序列表与现有配置不一致，请刷新后重试")
        config[group] = {name: configs[name] for name in payload.names}
        if not save_config(config, config_path):
            raise HTTPException(500, "配置排序保存失败")
        return {"saved": True, "names": payload.names}

    @app.delete("/api/config/{group}/{name}", dependencies=[Depends(_write_required)])
    def delete_named_config(group: str, name: str):
        if group not in ("llm_configs", "embedding_configs"):
            raise HTTPException(404, "未知配置类别")
        config = cfg()
        if name not in config.get(group, {}):
            raise HTTPException(404, "配置不存在")
        if len(config[group]) <= 1:
            raise HTTPException(400, "至少保留一个配置")
        del config[group][name]
        last_key = "last_llm_config_name" if group == "llm_configs" else "last_embedding_config_name"
        if config.get(last_key) == name:
            config[last_key] = next(iter(config[group]))
        if group == "llm_configs" and config.get("default_llm_config_name") == name:
            config["default_llm_config_name"] = next(iter(config[group]))
        if not save_config(config, config_path):
            raise HTTPException(500, "配置删除失败")
        return {"deleted": True}

    @app.patch("/api/config/{group}/{name}", dependencies=[Depends(_write_required)])
    def rename_named_config(group: str, name: str, payload: RenameConfig):
        if group not in ("llm_configs", "embedding_configs"):
            raise HTTPException(404, "未知配置类别")
        new_name = payload.new_name.strip()
        if not new_name:
            raise HTTPException(400, "配置名不能为空")
        config = cfg()
        configs = config.get(group, {})
        if name not in configs:
            raise HTTPException(404, "配置不存在")
        if new_name != name and new_name in configs:
            raise HTTPException(409, "配置名称已存在")
        if new_name == name:
            return {"renamed": False, "name": name}
        # 重建字典以保留左侧配置列表原有顺序。
        config[group] = {new_name if key == name else key: value
                         for key, value in configs.items()}
        last_key = "last_llm_config_name" if group == "llm_configs" else "last_embedding_config_name"
        if config.get(last_key) == name:
            config[last_key] = new_name
        if group == "llm_configs" and config.get("default_llm_config_name") == name:
            config["default_llm_config_name"] = new_name
        if not save_config(config, config_path):
            raise HTTPException(500, "配置重命名失败")
        return {"renamed": True, "name": new_name}

    @app.post("/api/projects", dependencies=[Depends(_write_required)])
    def create_project_route(payload: ProjectRequest):
        try:
            path = create_project(workspace_root, payload.name, payload.parent or None)
        except (OSError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"path": path}

    @app.post("/api/projects/import", dependencies=[Depends(_write_required)])
    def import_project_route(payload: ProjectRequest):
        try:
            target = Path(payload.path).expanduser()
            if target.is_file() and target.name.lower() == "project.json":
                target = target.parent
            if not (target / "chapters").is_dir():
                raise ValueError("所选目录缺少 chapters 文件夹，不是完整的小说工程")
            return {"path": import_existing_project(workspace_root, str(target))}
        except (OSError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/api/projects/{project_id}", dependencies=[Depends(_write_required)])
    def remove_project_route(project_id: str):
        path = project_path(project_id)
        return {"removed": remove_project(workspace_root, path), "files_kept": True}

    @app.get("/api/projects/{project_id}/settings")
    def get_settings(project_id: str):
        return load_project_settings(project_path(project_id))

    @app.put("/api/projects/{project_id}/settings", dependencies=[Depends(_write_required)])
    def put_settings(project_id: str, payload: dict):
        path = project_path(project_id)
        settings = load_project_settings(path)
        allowed = set(DEFAULT_PROJECT_SETTINGS) | {
            "glossary", "glossary_hard_terms", "retrieval_in_continuation",
            "retrieval_k_for_continuation", "corpus_is_english",
            "use_previous_summaries", "summary_chars"}
        settings.update({key: value for key, value in payload.items() if key in allowed})
        if not save_project_settings(path, settings):
            raise HTTPException(500, "设定保存失败")
        return settings

    @app.post("/api/projects/{project_id}/plan", dependencies=[Depends(_write_required)])
    def suggest_plan(project_id: str, payload: ChapterRequest):
        from core.continuation import ContinuationLLM, suggest_chapter_plan
        from core.continuation import read_chapter_summary
        path = project_path(project_id)
        settings = settings_for_chapter(load_project_settings(path), payload.number)
        settings.update({key: value for key, value in payload.preview.items()
                         if key in ("background", "extra_requirements", "chapter_brief",
                                    "chapter_requirements", "beats_per_chapter")})
        previous_summary = read_chapter_summary(path, payload.number - 1) or ""
        previous_tail = read_file(chapter_path(path, payload.number - 1))[-2400:]
        previous = (("【有效摘要】\n" + previous_summary + "\n") if previous_summary else "") + \
            "【正文结尾】\n" + previous_tail
        from core.auto_wiki import wiki_context
        wiki = wiki_context(path, previous, before_chapter=payload.number)
        return jobs.start(path, "plan", lambda update: (
            update("正在规划章节") or suggest_chapter_plan(
                settings, ContinuationLLM(llm(payload.model_name)),
                int(settings.get("beats_per_chapter") or 6), previous, wiki)))

    @app.post("/api/projects/{project_id}/plan/revise", dependencies=[Depends(_write_required)])
    def revise_plan(project_id: str, payload: ChapterRequest):
        from core.continuation import ContinuationLLM, read_chapter_summary, revise_chapter_plan
        from core.auto_wiki import wiki_context
        path = project_path(project_id)
        settings = settings_for_chapter(load_project_settings(path), payload.number)
        settings.update({key: value for key, value in payload.preview.items()
                         if key in ("background", "extra_requirements", "chapter_brief",
                                    "chapter_requirements", "beats_per_chapter",
                                    "chapter_title", "beats")})
        current_beats = settings.get("beats") or []
        num_beats = len(current_beats) or int(settings.get("beats_per_chapter") or 6)
        previous_summary = read_chapter_summary(path, payload.number - 1) or ""
        previous_tail = read_file(chapter_path(path, payload.number - 1))[-2400:]
        previous = ((("【有效摘要】\n" + previous_summary + "\n") if previous_summary else "") +
                    "【正文结尾】\n" + previous_tail)
        wiki = wiki_context(path, previous, before_chapter=payload.number)
        return jobs.start(path, "plan_revision", lambda update: (
            update("正在按问题修订章节规划") or revise_chapter_plan(
                settings, ContinuationLLM(llm(payload.model_name)), num_beats,
                payload.requirements, previous, wiki)))

    @app.post("/api/projects/{project_id}/book-rules", dependencies=[Depends(_write_required)])
    def suggest_rules(project_id: str, payload: ChapterRequest):
        from core.book_rules import extract_book_rules
        from core.continuation import ContinuationLLM
        path = project_path(project_id)
        settings = load_project_settings(path)
        settings.update({key: value for key, value in payload.preview.items()
                         if key in ("background", "character_voices", "extra_requirements",
                                    "source_novel")})
        return jobs.start(path, "book_rules", lambda update: (
            update("正在提取全书规则") or extract_book_rules(
                path, ContinuationLLM(llm(payload.model_name)), settings)))

    @app.post("/api/projects/{project_id}/corpus", dependencies=[Depends(_write_required)])
    async def import_corpus(project_id: str, files: list[UploadFile] = File(...),
                            heading_pattern: str = Form(""), replace: bool = Form(False)):
        from core.corpus import prepare_corpus
        path = project_path(project_id)
        if list_chapter_files(path) and not replace:
            raise HTTPException(409, "工程已有章节；请明确选择覆盖并先备份")
        uploads = []
        staging = Path(tempfile.mkdtemp(prefix="corpus_upload_", dir=path))
        try:
            for index, file in enumerate(files):
                target = staging / f"{index}_{Path(file.filename or 'source.txt').name}"
                with target.open("wb") as stream:
                    while chunk := await file.read(1024 * 1024):
                        stream.write(chunk)
                uploads.append(str(target))
        except Exception:
            shutil.rmtree(staging)
            raise

        def work(update):
            try:
                if replace and list_chapter_files(path):
                    backup = Path(path) / "runs" / "import_backups" / time.strftime("%Y%m%d_%H%M%S")
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(Path(path) / "chapters", backup)
                    update("旧章节已备份", {"path": str(backup)})
                update("正在切分母本")
                return prepare_corpus(uploads, str(Path(path) / "chapters"), heading_pattern)
            finally:
                shutil.rmtree(staging, ignore_errors=True)

        return jobs.start(path, "corpus", work)

    @app.get("/api/projects/{project_id}/vectors")
    def vector_info(project_id: str):
        from core.vectorstore import count_segments, indexed_chapters, vectorstore_health
        path = project_path(project_id)
        chapters = indexed_chapters(path)
        return {"segments": count_segments(path), "health": vectorstore_health(path),
                "indexed_chapters": chapters,
                "first_indexed_chapter": chapters[0] if chapters else None,
                "last_indexed_chapter": chapters[-1] if chapters else None}

    @app.post("/api/projects/{project_id}/vectors", dependencies=[Depends(_write_required)])
    def build_vectors(project_id: str, payload: RangeRequest):
        from core.knowledge import replace_chapter_vector
        from core.vectorstore import count_segments, indexed_chapters
        path = project_path(project_id)
        files = [(n, p) for n, _, p in list_chapter_files(path)
                 if payload.start <= n <= payload.end]
        if not files:
            raise HTTPException(400, "章号范围内没有章节")
        embedding = get_embedding_config(cfg())

        def work(update):
            total = 0
            existed = set(indexed_chapters(path))
            replaced = 0
            appended = 0
            for index, (number, source) in enumerate(files, 1):
                action = "replace" if number in existed else "append"
                segments = replace_chapter_vector(
                    embedding.get("api_key", ""), embedding.get("base_url", ""),
                    embedding.get("interface_format", ""), embedding.get("model_name", ""),
                    number, source, path)
                if segments <= 0:
                    raise ValueError(f"第 {number} 章未生成有效向量分段，请检查向量模型配置")
                total += segments
                if action == "replace":
                    replaced += 1
                else:
                    appended += 1
                update(f"已处理 {index}/{len(files)} 章",
                       {"chapter": number,
                        "action": "替换" if action == "replace" else "追加",
                        "chapter_segments": segments, "segments": total})
            return {"segments": count_segments(path), "processed_chapters": len(files),
                    "replaced_chapters": replaced, "appended_chapters": appended}

        return jobs.start(path, "vectors", work)

    @app.delete("/api/projects/{project_id}/vectors", dependencies=[Depends(_write_required)])
    def clear_vectors(project_id: str):
        from core.vectorstore import clear_vector_store
        path = project_path(project_id)
        return {"cleared": clear_vector_store(path)}

    @app.post("/api/projects/{project_id}/search/chapters")
    def search_chapters(project_id: str, payload: ChapterSearchRequest):
        """使用现有向量库执行正文混合检索，不修改工程内容。"""
        from embedding_adapters import create_embedding_adapter
        from core.vectorstore import (glossary_terms_for_query, hybrid_search,
                                      vectorstore_health)
        path = project_path(project_id)
        query = payload.query.strip()
        if not query:
            raise HTTPException(400, "请输入要查找的内容")
        if len(query) > 500:
            raise HTTPException(400, "检索内容不能超过 500 字")
        health = vectorstore_health(path)
        if not health.get("healthy"):
            raise HTTPException(400, "当前向量库不可用，请先在“工程与母本”中构建或修复")
        embedding = get_embedding_config(cfg())
        settings = load_project_settings(path)
        corpus_is_english = bool(settings.get("corpus_is_english"))
        required, optional = glossary_terms_for_query(
            query, settings.get("glossary") or {}, source_query=query,
            corpus_is_english=corpus_is_english,
            hard_terms=settings.get("glossary_hard_terms") or [])
        try:
            adapter = create_embedding_adapter(
                embedding.get("interface_format", ""), embedding.get("api_key", ""),
                embedding.get("base_url", "") or "http://localhost:11434/api",
                embedding.get("model_name", ""))
            rows = hybrid_search(
                adapter, query, path, k=max(1, min(int(payload.limit or 12), 30)),
                required_terms=required, optional_terms=optional,
                semantic_weight=0.8, fallback_to_semantic=True)
        except Exception as exc:
            raise HTTPException(
                400, f"向量搜索失败，请检查当前向量模型配置：{exc}") from exc
        maximum = max((float(row.get("score") or 0) for row in rows), default=0.0)
        results = []
        for row in rows:
            score = float(row.get("score") or 0)
            results.append({
                "chapter": int(row.get("chapter") or 0),
                "segment": int(row.get("seg_index") or 0) + 1,
                "text": str(row.get("text") or ""),
                "relevance": round(score / maximum * 100) if maximum > 0 else 0,
                "semantic_score": row.get("semantic_score"),
                "matched_terms": row.get("matched_terms") or [],
                "constraint_mode": row.get("constraint_mode") or "none",
            })
        return {"query": query, "count": len(results), "results": results,
                "required_terms": required, "optional_terms": optional}

    @app.post("/api/projects/{project_id}/generate", dependencies=[Depends(_write_required)])
    def generate(project_id: str, payload: GenerateRequest):
        from core.continuation import ContinuationLLM, generate_chapter
        from core.chapter_revision import save_revision_snapshot
        from core.draft_review import save_review_session
        path = project_path(project_id)
        settings = settings_for_chapter(load_project_settings(path), payload.number)
        beats = settings.get("beats") or []
        if payload.beats_limit:
            beats = beats[:payload.beats_limit]
        if not beats:
            raise HTTPException(400, "请先设置分幕大纲")
        chapter_file = Path(chapter_path(path, payload.number))
        if chapter_file.exists() and not payload.overwrite:
            raise HTTPException(409, "章节已存在；如需重新生成，请明确允许覆盖")
        model_cfg = llm(payload.model_name)
        embedding = get_embedding_config(cfg()) if settings.get("retrieval_in_continuation") else None

        def work(update):
            if chapter_file.exists():
                save_revision_snapshot(path, payload.number, chapter_file.read_text(encoding="utf-8"))
                update("旧章节已留存快照")
            result = generate_chapter(
                path, settings, ContinuationLLM(model_cfg), chapter_number=payload.number,
                chapter_title=payload.title or settings.get("chapter_title") or "",
                beats=beats, embedding_cfg=embedding,
                progress=lambda stage, data: update(stage, data))
            save_review_session(path, payload.number, result["chapter_title"], result["beat_drafts"])
            # 工程已有向量库时，生成完成即按章替换，避免新章无法被后续 RAG 检索。
            store = Path(path) / "vectorstore"
            if (store / "vectors.faiss").is_file() and (store / "meta.db").is_file():
                try:
                    from core.knowledge import replace_chapter_vector
                    vector_cfg = get_embedding_config(cfg())
                    update(f"正在更新第 {payload.number} 章向量", {"chapter": payload.number})
                    segments = replace_chapter_vector(
                        vector_cfg.get("api_key", ""), vector_cfg.get("base_url", ""),
                        vector_cfg.get("interface_format", ""), vector_cfg.get("model_name", ""),
                        payload.number, str(chapter_file), path, chapter_text=result["text"])
                    if segments <= 0 and result["text"].strip():
                        raise ValueError("未生成有效向量分段")
                    result["vector_segments"] = segments
                    update(f"第 {payload.number} 章向量已更新",
                           {"chapter": payload.number, "segments": segments})
                except Exception as exc:
                    result["vector_segments"] = None
                    result["vector_error"] = str(exc)
                    update(f"第 {payload.number} 章正文已保存，但向量更新失败",
                           {"chapter": payload.number, "error": str(exc)})
            else:
                result["vector_segments"] = None
            return {key: value for key, value in result.items() if key not in ("text", "beat_drafts")}

        return jobs.start(path, "generate", work)

    @app.get("/api/projects/{project_id}/chapters/{number}/review")
    def get_review(project_id: str, number: int):
        from core.draft_review import load_review_session
        return load_review_session(project_path(project_id), number) or {"sections": []}

    @app.put("/api/projects/{project_id}/chapters/{number}/review",
             dependencies=[Depends(_write_required)])
    def put_review(project_id: str, number: int, payload: dict):
        from core.draft_review import save_review_session
        path = project_path(project_id)
        sections = payload.get("sections")
        if not isinstance(sections, list):
            raise HTTPException(400, "分幕状态无效")
        saved = save_review_session(path, number, str(payload.get("title") or ""), sections)
        return {"saved": saved}

    @app.get("/api/projects/{project_id}/chapters/{number}/summary")
    def get_chapter_summary(project_id: str, number: int):
        from core.continuation import validate_chapter_summary
        path, _chapter_file = existing_chapter(project_id, number)
        summary_file = Path(path) / "chapters" / f"chapter_summary_{number}.txt"
        text = summary_file.read_text(encoding="utf-8").strip() if summary_file.exists() else ""
        issues = validate_chapter_summary(text)
        return {"number": number, "text": text, "exists": summary_file.exists(),
                "valid": not issues, "issues": issues}

    @app.post("/api/projects/{project_id}/chapters/{number}/summary",
              dependencies=[Depends(_write_required)])
    def regenerate_chapter_summary(project_id: str, number: int, payload: ChapterRequest):
        from core.continuation import (ContinuationLLM, generate_chapter_summary,
                                       validate_chapter_summary, write_chapter_summary)
        path, chapter_file = existing_chapter(project_id, number)
        settings = load_project_settings(path)
        max_chars = int(settings.get("summary_chars", 150) or 150)
        model_cfg = llm(payload.model_name)

        def work(update):
            update(f"正在重新生成第 {number} 章摘要")
            summary = generate_chapter_summary(
                ContinuationLLM(model_cfg), chapter_file.read_text(encoding="utf-8"), max_chars)
            issues = validate_chapter_summary(summary)
            if issues:
                raise ValueError("摘要生成未通过：" + "；".join(issues))
            write_chapter_summary(path, number, summary)
            update(f"第 {number} 章摘要已更新", {"chars": len(summary)})
            return {"number": number, "text": summary, "valid": True, "issues": []}

        return jobs.start(path, "summary", work)

    @app.post("/api/projects/{project_id}/revise", dependencies=[Depends(_write_required)])
    def revise(project_id: str, payload: ChapterRequest):
        from core.chapter_revision import (build_whole_chapter_prompt,
                                           check_chapter_candidate, chapter_body,
                                           review_candidate_requirements)
        from core.continuation import ContinuationLLM, clean_text
        path, chapter_file = existing_chapter(project_id, payload.number)
        source_file = chapter_file.read_text(encoding="utf-8")
        source = chapter_body(payload.text or source_file, source_file.splitlines()[0])
        if not payload.requirements.strip():
            raise HTTPException(400, "请填写修订要求")
        model_cfg = llm(payload.model_name)
        settings = settings_for_chapter(load_project_settings(path), payload.number)
        title = source_file.splitlines()[0]

        def work(update):
            prompt = build_whole_chapter_prompt(source, title, settings, payload.requirements)
            model = ContinuationLLM(model_cfg)
            for attempt in range(2):
                update(f"正在生成修订建议（第 {attempt + 1} 次）")
                candidate = clean_text(model.complete(
                    prompt, system="你是严谨的小说编辑。只输出修订后的完整章节正文。",
                    temperature=0.3, num_predict=max(4096, int(len(source) * 1.9))), settings)
                issues = check_chapter_candidate(candidate, source, payload.requirements, settings)
                if not issues:
                    update("正在逐项核对修订要求")
                    issues = review_candidate_requirements(
                        model, candidate, payload.requirements, settings)
                if not issues:
                    break
                prompt += "\n【上次未通过】\n" + "；".join(issues[:6])
            return {"chapter_number": payload.number, "candidate": candidate,
                    "issues": issues, "source": source,
                    "requirements": payload.requirements}

        return jobs.start(path, "revise", work)

    @app.post("/api/projects/{project_id}/revise/check", dependencies=[Depends(_write_required)])
    def check_revision(project_id: str, payload: ChapterRequest):
        from core.chapter_revision import check_chapter_candidate, chapter_body
        path, chapter_file = existing_chapter(project_id, payload.number)
        source_file = chapter_file.read_text(encoding="utf-8")
        title = source_file.splitlines()[0]
        source = chapter_body(payload.text or source_file, title)
        settings = settings_for_chapter(load_project_settings(path), payload.number)
        return {"issues": check_chapter_candidate(payload.preview.get("candidate", ""),
                                                   source, payload.requirements, settings)}

    @app.post("/api/projects/{project_id}/consistency", dependencies=[Depends(_write_required)])
    def consistency(project_id: str, payload: ChapterRequest):
        from core.consistency import check_consistency
        from core.continuation import read_chapter_summary, retrieve_context_for_beat
        from core.auto_wiki import wiki_context
        path, chapter_file = existing_chapter(project_id, payload.number)
        settings = load_project_settings(path)
        text = payload.text or chapter_file.read_text(encoding="utf-8")
        wiki = wiki_context(path, text, before_chapter=payload.number)
        previous_context = ""
        if payload.number > 1:
            previous_number = payload.number - 1
            summary = read_chapter_summary(path, previous_number) or ""
            previous_file = Path(chapter_path(path, previous_number))
            previous_text = previous_file.read_text(encoding="utf-8") if previous_file.is_file() else ""
            tail = previous_text[-3500:].strip()
            blocks = []
            if summary:
                blocks.append("【有效摘要】\n" + summary)
            if tail:
                blocks.append("【章末原文（时间晚于本章前部内容）】\n" + tail)
            previous_context = "\n\n".join(blocks)
        if not payload.model_name:
            raise HTTPException(400, "一致性审校需要选择大模型")
        model_cfg = llm(payload.model_name)
        embedding = get_embedding_config(cfg())

        def work(update):
            update("正在整理上一章与补充证据")
            retrieved = retrieve_context_for_beat(
                path, embedding, text[:200], int(embedding.get("retrieval_k", 4) or 4),
                glossary=settings.get("glossary") or {}, source_query=text[:200],
                corpus_is_english=bool(settings.get("corpus_is_english")),
                glossary_hard_terms=settings.get("glossary_hard_terms") or [])
            update("正在进行 LLM 审校")
            return {"chapter_number": payload.number,
                    "report": check_consistency(
                        text, settings, retrieved, model_cfg,
                        previous_context=previous_context, wiki_history=wiki),
                    "retrieved_chars": len(retrieved),
                    "previous_context_chars": len(previous_context),
                    "wiki_chars": len(wiki)}

        return jobs.start(path, "consistency", work)

    @app.get("/api/projects/{project_id}/chapters/{number}/illustration")
    def get_chapter_illustration(project_id: str, number: int):
        from core.chapter_illustration import illustration_path, validate_illustration_svg
        target = illustration_path(project_path(project_id), number)
        if not target.is_file():
            return {"exists": False, "svg": ""}
        svg = validate_illustration_svg(target.read_text(encoding="utf-8"))
        return {"exists": True, "svg": svg}

    @app.post("/api/projects/{project_id}/chapters/{number}/illustration",
              dependencies=[Depends(_write_required)])
    def generate_illustration(project_id: str, number: int, payload: dict):
        from core.chapter_illustration import generate_chapter_illustration
        from core.continuation import ContinuationLLM
        path = project_path(project_id)
        if number < 1:
            raise HTTPException(400, "章号必须大于零")
        model_cfg = llm(str(payload.get("model_name") or ""))
        preview = payload.get("preview") if isinstance(payload.get("preview"), dict) else {}
        model_label = str(payload.get("model_name") or model_cfg.get("model_name") or "")

        def work(update):
            update(f"开始生成第 {number} 章插图", {"model": model_label})
            return generate_chapter_illustration(
                path, number, ContinuationLLM(model_cfg), preview=preview,
                progress=lambda message: update(message))

        return jobs.start(path, "illustration", work)

    @app.post("/api/projects/{project_id}/chapters/{number}/export",
              dependencies=[Depends(_write_required)])
    def export_word(project_id: str, number: int):
        from core.chapter_illustration import illustration_path
        from core.chapter_export import export_chapter_docx
        path, chapter_file = existing_chapter(project_id, number)
        text = chapter_file.read_text(encoding="utf-8")
        title, _, body = text.partition("\n")
        target = Path(path) / "exports" / f"chapter_{number}.docx"
        image = illustration_path(path, number)
        svg = image.read_text(encoding="utf-8") if image.is_file() else None
        return export_chapter_docx(title, body, str(target), chapter_number=number,
                                   illustration_svg=svg)

    @app.get("/api/projects/{project_id}/chapters/{number}/export")
    def download_word(project_id: str, number: int):
        path = project_path(project_id)
        target = Path(path) / "exports" / f"chapter_{number}.docx"
        if not target.is_file():
            raise HTTPException(404, "请先生成 Word 文件")
        return FileResponse(target, filename=target.name)

    @app.get("/api/projects/{project_id}/jobs")
    def list_jobs(project_id: str):
        return jobs.list(project_path(project_id))

    @app.get("/api/projects/{project_id}/jobs/{job_id}")
    def get_job(project_id: str, job_id: str):
        job = jobs.get(project_path(project_id), job_id)
        if not job:
            raise HTTPException(404, "任务不存在")
        return job

    @app.get("/api/projects/{project_id}/logs/latest")
    def latest_generation_log(project_id: str):
        from core.run_log import latest_run_log
        path = latest_run_log(project_path(project_id))
        if not path:
            return {"path": "", "events": []}
        events = []
        with open(path, "r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return {"path": path, "events": events}

    @app.get("/api/projects/{project_id}/logs")
    def list_generation_logs(project_id: str):
        directory = Path(project_path(project_id)) / "runs"
        records = []
        for path in directory.glob("chapter_*.jsonl"):
            try:
                with path.open("r", encoding="utf-8") as stream:
                    first = json.loads(stream.readline())
                    events_count = 1 + sum(1 for _ in stream)
                if not isinstance(first, dict):
                    continue
                records.append({"id": path.stem, "chapter": first.get("chapter_number"),
                                "title": first.get("chapter_title") or "",
                                "created_at": first.get("timestamp") or "",
                                "events_count": events_count})
            except (OSError, json.JSONDecodeError):
                continue
        records.sort(key=lambda item: (item["created_at"], item["id"]), reverse=True)
        return records

    @app.get("/api/projects/{project_id}/logs/{log_id}")
    def generation_log(project_id: str, log_id: str):
        if not log_id.startswith("chapter_") or not all(
                char.isalnum() or char in "_-" for char in log_id):
            raise HTTPException(404, "日志不存在")
        path = Path(project_path(project_id)) / "runs" / f"{log_id}.jsonl"
        if not path.is_file():
            raise HTTPException(404, "日志不存在")
        events = []
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    item = json.loads(line)
                    if isinstance(item, dict):
                        events.append(item)
                except json.JSONDecodeError:
                    continue
        return {"id": log_id, "events": events}

    @app.get("/api/projects/{project_id}/wiki")
    def wiki_index(project_id: str, query: str = ""):
        from core.auto_wiki import list_wiki_subjects
        return list_wiki_subjects(project_path(project_id), query)

    @app.get("/api/projects/{project_id}/wiki/relationships")
    def wiki_relationships(project_id: str, start_chapter: int | None = None,
                           end_chapter: int | None = None):
        from core.auto_wiki import wiki_relationship_graph
        if start_chapter is not None and start_chapter < 1:
            raise HTTPException(400, "起始章必须大于零")
        if end_chapter is not None and end_chapter < 1:
            raise HTTPException(400, "结束章必须大于零")
        if start_chapter and end_chapter and start_chapter > end_chapter:
            raise HTTPException(400, "章节范围无效")
        return wiki_relationship_graph(project_path(project_id), start_chapter, end_chapter)

    @app.get("/api/projects/{project_id}/wiki/subjects/{subject}")
    def wiki_subject(project_id: str, subject: str, before_chapter: int | None = None):
        from core.auto_wiki import wiki_entity_page
        if before_chapter is not None and before_chapter < 1:
            raise HTTPException(400, "章号必须大于零")
        return wiki_entity_page(project_path(project_id), subject, before_chapter)

    @app.get("/api/projects/{project_id}/wiki/subjects/{subject}/profile")
    def wiki_subject_profile(project_id: str, subject: str, before_chapter: int | None = None):
        from core.wiki_profiles import read_profile
        if before_chapter is not None and before_chapter < 1:
            raise HTTPException(400, "章号必须大于零")
        return {"profile": read_profile(project_path(project_id), subject, before_chapter)}

    @app.post("/api/projects/{project_id}/wiki/subjects/{subject}/profile",
              dependencies=[Depends(_write_required)])
    def generate_wiki_subject_profile(project_id: str, subject: str, payload: dict):
        from core.auto_wiki import wiki_entity_page
        from core.continuation import ContinuationLLM
        from core.wiki_profiles import (expand_uncertain_profile, generate_profile,
                                        read_profile, verify_profile)
        from core.wiki_run_log import WikiRunLogger
        path = project_path(project_id)
        before = payload.get("before_chapter")
        before = int(before) if before is not None else None
        if before is not None and before < 1:
            raise HTTPException(400, "章号必须大于零")
        if not wiki_entity_page(path, subject, before)["facts_count"]:
            raise HTTPException(400, "该人物尚无可用的 Wiki 事实")
        if payload.get("verify_only") and not read_profile(path, subject, before):
            raise HTTPException(400, "人物档案不存在或已过期，请先生成档案")
        if payload.get("expand_only") and not read_profile(path, subject, before):
            raise HTTPException(400, "人物档案不存在或已过期，请先生成档案")
        model_cfg = llm(str(payload.get("model_name") or ""))
        def work(update, job_id):
            audit = WikiRunLogger(path, job_id, model_cfg.get("model_name", ""),
                                  secrets=(model_cfg.get("api_key", ""),))
            update("开始编纂人物档案", {"subject": subject, "before_chapter": before})
            audit.write("profile_started", subject=subject, before_chapter=before)
            try:
                model = ContinuationLLM(model_cfg)
                if payload.get("expand_only"):
                    result = expand_uncertain_profile(
                        path, subject, model, before,
                        progress=lambda message: update(message), audit=audit)
                else:
                    if not payload.get("verify_only"):
                        generate_profile(path, subject, model, before,
                                         progress=lambda message: update(message),
                                         force=bool(payload.get("force")), audit=audit)
                    result = verify_profile(path, subject, model, before,
                                            progress=lambda message: update(message), audit=audit)
                audit.write("profile_completed", subject=subject, claims=len(result["claims"]),
                            evidence_count=result["evidence_count"],
                            verification_counts=result["verification_counts"])
                return {"subject": subject, "claims": len(result["claims"]),
                        "evidence_count": result["evidence_count"],
                        "verification_counts": result["verification_counts"]}
            except Exception as exc:
                audit.write("profile_failed", subject=subject,
                            error_type=type(exc).__name__, error=str(exc))
                raise
        return jobs.start(path, "wiki", work, pass_job_id=True)

    @app.get("/api/projects/{project_id}/wiki/pending")
    def wiki_pending(project_id: str):
        from core.auto_wiki import wiki_pending_chapters
        pending = wiki_pending_chapters(project_path(project_id))
        return {"pending": pending, "count": len(pending)}

    @app.get("/api/projects/{project_id}/wiki/review")
    def wiki_review_state(project_id: str):
        from core.wiki_review import review_status
        return review_status(project_path(project_id))

    @app.post("/api/projects/{project_id}/wiki/review", dependencies=[Depends(_write_required)])
    def review_wiki_route(project_id: str, payload: dict):
        from core.continuation import ContinuationLLM
        from core.wiki_review import review_high_risk_facts, review_status
        from core.wiki_run_log import WikiRunLogger
        path = project_path(project_id)
        state = review_status(path)
        if not state["counts"]["pending"] and not payload.get("force"):
            raise HTTPException(400, "没有待复核的高风险事实")
        model_cfg = llm(str(payload.get("model_name") or ""))
        def work(update, job_id):
            audit = WikiRunLogger(path, job_id, model_cfg.get("model_name", ""),
                                  secrets=(model_cfg.get("api_key", ""),))
            update("开始局部证据复核", {"pending": state["counts"]["pending"],
                                      "model": model_cfg.get("model_name", "")})
            try:
                result = review_high_risk_facts(
                    path, ContinuationLLM(model_cfg),
                    progress=lambda message, data=None: update(message, data), audit=audit,
                    only_pending=not bool(payload.get("force")))
                audit.write("review_completed", **result)
                return {**result, "log_path": str(audit.path)}
            except Exception as exc:
                audit.write("review_run_failed", error_type=type(exc).__name__, error=str(exc))
                raise
        return jobs.start(path, "wiki", work, pass_job_id=True)

    @app.post("/api/projects/{project_id}/wiki/pending", dependencies=[Depends(_write_required)])
    def build_pending_wiki_route(project_id: str, payload: dict):
        from core.auto_wiki import build_pending_wiki, wiki_pending_chapters
        from core.continuation import ContinuationLLM
        from core.wiki_run_log import WikiRunLogger
        path = project_path(project_id)
        start_raw = payload.get("start")
        end_raw = payload.get("end")
        start = int(start_raw) if start_raw is not None else None
        end = int(end_raw) if end_raw is not None else None
        force = bool(payload.get("force"))
        if start is not None and start < 1:
            raise HTTPException(400, "起始章必须大于零")
        if end is not None and end < 1:
            raise HTTPException(400, "结束章必须大于零")
        if start is not None and end is not None and end < start:
            raise HTTPException(400, "章号范围无效")
        pending = [item for item in wiki_pending_chapters(path)
                   if (start is None or item["chapter"] >= start)
                   and (end is None or item["chapter"] <= end)]
        if not force and not pending:
            message = "所选范围内没有待处理章节" if start is not None or end is not None else "Wiki 已是最新，无待处理章节"
            raise HTTPException(400, message)
        model_cfg = llm(str(payload.get("model_name") or ""))
        def work(update, job_id):
            audit = WikiRunLogger(path, job_id, model_cfg.get("model_name", ""),
                                  secrets=(model_cfg.get("api_key", ""),))
            update("已创建完整 Wiki 日志", {"log_path": str(audit.path)})
            try:
                result = build_pending_wiki(path, ContinuationLLM(model_cfg),
                                            progress=lambda message, data=None: update(message, data), audit=audit,
                                            start=start, end=end, force=force)
                audit.write("run_completed", **result)
                return {**result, "log_path": str(audit.path)}
            except Exception as exc:
                audit.write("run_failed", error_type=type(exc).__name__, error=str(exc))
                raise
        return jobs.start(path, "wiki", work, pass_job_id=True)

    @app.post("/api/projects/{project_id}/wiki/chapters/{number}/retry", dependencies=[Depends(_write_required)])
    def retry_sensitive_wiki_chapter(project_id: str, number: int, payload: dict):
        """用用户指定的替代模型，单独重试被服务端内容策略拦截的章节。"""
        from core.auto_wiki import extract_chapter_facts, wiki_pending_chapters
        from core.continuation import ContinuationLLM
        from core.wiki_run_log import WikiRunLogger
        path = project_path(project_id)
        if number < 1 or not any(item["chapter"] == number and item["reason"] == "sensitive"
                                 for item in wiki_pending_chapters(path)):
            raise HTTPException(400, "该章当前不处于“内容受限”待处理状态")
        model_cfg = llm(str(payload.get("model_name") or ""))
        def work(update, job_id):
            audit = WikiRunLogger(path, job_id, model_cfg.get("model_name", ""),
                                  secrets=(model_cfg.get("api_key", ""),))
            update(f"正用替代模型处理第 {number} 章", {"log_path": str(audit.path)})
            try:
                result = extract_chapter_facts(path, number, ContinuationLLM(model_cfg),
                                               progress=lambda message, data=None: update(message, data), audit=audit)
                audit.write("retry_completed", chapter=number, status=result.get("status"),
                            facts=len(result.get("facts", [])))
                return {"chapter": number, "facts": len(result.get("facts", [])),
                        "status": result.get("status"), "log_path": str(audit.path)}
            except Exception as exc:
                audit.write("retry_failed", chapter=number,
                            error_type=type(exc).__name__, error=str(exc))
                raise
        return jobs.start(path, "wiki", work, pass_job_id=True)

    @app.post("/api/projects/{project_id}/wiki", dependencies=[Depends(_write_required)])
    def build_wiki_route(project_id: str, payload: dict):
        from core.auto_wiki import build_wiki
        from core.continuation import ContinuationLLM
        from core.wiki_run_log import WikiRunLogger
        path = project_path(project_id)
        start = int(payload.get("start") or 1)
        end = int(payload.get("end") or start)
        if start < 1 or end < start:
            raise HTTPException(400, "章号范围无效")
        model_cfg = llm(str(payload.get("model_name") or ""))
        def work(update, job_id):
            audit = WikiRunLogger(path, job_id, model_cfg.get("model_name", ""),
                                  secrets=(model_cfg.get("api_key", ""),))
            update("已创建完整 Wiki 日志", {"log_path": str(audit.path)})
            try:
                result = build_wiki(path, ContinuationLLM(model_cfg), start, end,
                                    progress=lambda message, data=None: update(message, data), audit=audit)
                audit.write("run_completed", **result)
                return {**result, "log_path": str(audit.path)}
            except Exception as exc:
                audit.write("run_failed", error_type=type(exc).__name__, error=str(exc))
                raise
        return jobs.start(path, "wiki", work, pass_job_id=True)

    @app.get("/api/projects/{project_id}/wiki/logs/{job_id}")
    def wiki_full_log(project_id: str, job_id: str):
        from core.wiki_run_log import read_wiki_run_log
        path = project_path(project_id)
        job = jobs.get(path, job_id)
        if not job or job.get("kind") != "wiki":
            raise HTTPException(404, "Wiki 任务不存在")
        try:
            return {"id": job_id, "entries": read_wiki_run_log(path, job_id)}
        except (FileNotFoundError, ValueError):
            raise HTTPException(404, "此任务没有完整日志") from None
