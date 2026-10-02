# core/project_manager.py
# -*- coding: utf-8 -*-
"""多工程管理：一本小说一个目录，随意切换。

workspace 根目录下维护 projects.json 索引；每个工程目录结构：

    工程名/
      project.json      续写设定（本书专属，与全局 config.json 分离）
      chapters/         母本章节 + 续写章节（chapter_N.txt）
      vectorstore/      Chroma 向量库
      角色库/            角色设定、人物卡
"""
import os
import re
import time

from core.utils import load_json, save_data_to_json

INDEX_FILENAME = "projects.json"
SETTINGS_FILENAME = "project.json"

CHAPTER_FILE_RE = re.compile(r"^chapter_(\d+)\.txt$")

DEFAULT_PROJECT_SETTINGS = {
    "name": "",
    "source_novel": "",
    "language": "zh",
    "traditional": False,
    "background": "",
    "chapter_number": 101,
    "chapter_title": "",
    "chars_per_beat": 600,
    "beats_per_chapter": 6,
    "temperature": 0.8,
    "llm_config_name": "",
    "system_prompt": (
        "你是一位续写小说的作者。须严格模仿母本的语体、叙事节奏与人物口吻，"
        "承接前文自然续写。只输出正文，不写解释、不写标题、不使用 markdown。"
    ),
    "style_sample": {"chapter": 0, "start": 0, "length": 450},
    "beats": [],
    "chapter_brief": "",
    "chapter_requirements": "",
    "chapter_plans": {},
    "illustration_style": "auto",
    "illustration_style_notes": "",
    "forbidden_words": [],
    "character_voices": "",
    "extra_requirements": "",
    "closing_formula": "",
    "last_generated_chapter": 0,
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ----------------------------- workspace 索引 -----------------------------

def index_path(workspace_root: str) -> str:
    return os.path.join(workspace_root, INDEX_FILENAME)


def load_index(workspace_root: str) -> dict:
    """读取 workspace 索引；不存在则返回空结构。"""
    os.makedirs(workspace_root, exist_ok=True)
    data = load_json(index_path(workspace_root), None)
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("projects"), list):
        data["projects"] = []
    data.setdefault("last_opened_path", "")
    return data


def save_index(workspace_root: str, data: dict) -> bool:
    return save_data_to_json(data, index_path(workspace_root))


def add_project(workspace_root: str, project_path: str, name: str = None) -> dict:
    """把工程登记进索引；重复登记同一路径只刷新 last_seen_at。"""
    abs_path = os.path.abspath(project_path)
    data = load_index(workspace_root)

    entry = next(
        (p for p in data["projects"]
         if os.path.abspath(p.get("path", "")) == abs_path),
        None,
    )
    if entry is None:
        entry = {
            "name": name or os.path.basename(abs_path.rstrip(os.sep)) or abs_path,
            "path": abs_path,
            "added_at": _now(),
        }
        data["projects"].append(entry)
    elif name and entry.get("name") != name:
        entry["name"] = name
    entry["last_seen_at"] = _now()
    save_index(workspace_root, data)
    return entry


def list_projects(workspace_root: str) -> list:
    """返回索引中仍然存在的工程，按最近使用倒序。"""
    data = load_index(workspace_root)
    out = [p for p in data["projects"]
           if p.get("path") and os.path.isdir(p["path"])]
    out.sort(key=lambda p: p.get("last_seen_at", ""), reverse=True)
    return out


def remove_project(workspace_root: str, project_path: str) -> bool:
    """仅从索引移除登记，不删除磁盘目录。"""
    abs_path = os.path.abspath(project_path)
    data = load_index(workspace_root)
    before = len(data["projects"])
    data["projects"] = [
        p for p in data["projects"]
        if os.path.abspath(p.get("path", "")) != abs_path
    ]
    if data.get("last_opened_path") and \
            os.path.abspath(data["last_opened_path"]) == abs_path:
        data["last_opened_path"] = ""
    save_index(workspace_root, data)
    return len(data["projects"]) != before


def set_last_opened(workspace_root: str, project_path: str) -> None:
    data = load_index(workspace_root)
    data["last_opened_path"] = os.path.abspath(project_path)
    save_index(workspace_root, data)


def get_last_opened(workspace_root: str) -> str:
    return load_index(workspace_root).get("last_opened_path", "")


# ----------------------------- 工程目录 -----------------------------

def create_project(workspace_root: str, name: str, parent: str = None) -> str:
    """创建工程骨架并登记进索引，返回工程绝对路径。"""
    name = (name or "").strip()
    if not name:
        raise ValueError("工程名不能为空")
    if os.sep in name or "/" in name or "\\" in name:
        raise ValueError("工程名不能包含路径分隔符")

    base = os.path.abspath(parent or workspace_root)
    target = os.path.join(base, name)
    if os.path.exists(target):
        raise FileExistsError(f"目标已存在：{target}")

    for sub in ("chapters", "vectorstore", "角色库"):
        os.makedirs(os.path.join(target, sub), exist_ok=True)

    settings = dict(DEFAULT_PROJECT_SETTINGS)
    settings["name"] = name
    save_data_to_json(settings, os.path.join(target, SETTINGS_FILENAME))

    add_project(workspace_root, target, name=name)
    set_last_opened(workspace_root, target)
    return target


def import_existing_project(workspace_root: str, project_path: str) -> str:
    """登记已有工程目录，不复制或改动其中的数据。"""
    path = os.path.abspath(project_path)
    if not os.path.isdir(path):
        raise ValueError(f"工程目录不存在：{path}")
    config_path = settings_path(path)
    if not os.path.isfile(config_path):
        raise ValueError("所选目录不是已有工程：缺少 project.json")
    settings = load_json(config_path, None)
    if not isinstance(settings, dict):
        raise ValueError("project.json 格式无效，应为 JSON 对象")
    name = settings.get("name")
    if not isinstance(name, str) or not name.strip():
        name = os.path.basename(path)
    add_project(workspace_root, path, name=name.strip())
    return path


def settings_path(project_dir: str) -> str:
    return os.path.join(project_dir, SETTINGS_FILENAME)


def load_project_settings(project_dir: str) -> dict:
    """读取工程设定，缺失字段用默认值补齐。"""
    data = load_json(settings_path(project_dir), None)
    if not isinstance(data, dict):
        data = {}
    merged = dict(DEFAULT_PROJECT_SETTINGS)
    for key, value in data.items():
        merged[key] = value
    if not merged.get("name"):
        merged["name"] = os.path.basename(os.path.abspath(project_dir))
    return merged


def save_project_settings(project_dir: str, settings: dict) -> bool:
    return save_data_to_json(settings, settings_path(project_dir))


def settings_for_chapter(settings: dict, chapter_number: int) -> dict:
    """把当前章专属回目、目标与分幕覆盖到全书规则上。"""
    result = dict(settings)
    plans = settings.get("chapter_plans") or {}
    plan = plans.get(str(chapter_number)) if isinstance(plans, dict) else None
    if isinstance(plan, dict):
        for key in ("chapter_title", "chapter_brief", "chapter_requirements", "beats"):
            result[key] = plan.get(key, "" if key != "beats" else [])
    elif int(settings.get("chapter_number") or 0) != int(chapter_number):
        result["chapter_title"] = ""
        result["chapter_brief"] = ""
        result["chapter_requirements"] = ""
        result["beats"] = []
    return result


def chapters_dir(project_dir: str) -> str:
    return os.path.join(project_dir, "chapters")


def list_chapter_files(project_dir: str) -> list:
    """返回 [(章号, 文件名, 绝对路径)]，按章号升序。"""
    cdir = chapters_dir(project_dir)
    if not os.path.isdir(cdir):
        return []
    out = []
    for filename in os.listdir(cdir):
        m = CHAPTER_FILE_RE.match(filename)
        if m:
            out.append((int(m.group(1)), filename, os.path.join(cdir, filename)))
    out.sort(key=lambda item: item[0])
    return out


def next_chapter_number(project_dir: str, settings: dict = None) -> int:
    """下一章号：已存在最大章号 +1，否则用设定里的 chapter_number。"""
    existing = [num for num, _, _ in list_chapter_files(project_dir)]
    if existing:
        return max(existing) + 1
    settings = settings or load_project_settings(project_dir)
    try:
        return int(settings.get("chapter_number", 101))
    except (TypeError, ValueError):
        return 101


def chapter_path(project_dir: str, chapter_number: int) -> str:
    return os.path.join(chapters_dir(project_dir), f"chapter_{int(chapter_number)}.txt")
