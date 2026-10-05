"""加载随项目分发的智能体技能，不依赖 Codex 的技能运行环境。"""
import hashlib
import importlib.util
from pathlib import Path


def load_agent_skill(name, reference=None):
    """按用途加载项目 Skill；修订只加载当前模式的参考，不附带查证工具。"""
    if name not in {"history-research", "consistency-audit", "story-audit", "chapter-revision",
                    "chapter-planning", "chapter-writing", "prose-polish"}:
        raise ValueError("不支持的项目 Skill")
    skill_root = Path(__file__).resolve().parents[1] / "agent_skills"
    root = skill_root / name
    path = root / "SKILL.md"
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError("项目 Skill 缺少元数据")
    body = text.split("---", 2)[2].strip()
    content = path.read_bytes()
    if reference is not None:
        references = {"chapter-revision": {"local", "whole", "acceptance"},
                      "chapter-planning": {"draft", "review", "repair", "acceptance"},
                      "prose-polish": {"acceptance"}}
        if reference not in references.get(name, set()):
            raise ValueError("不支持的 Skill 参考")
        reference_path = root / "references" / f"{reference}.md"
        body += "\n\n" + reference_path.read_text(encoding="utf-8").strip()
        content += reference_path.read_bytes()
    return body, {"name": name, "version": hashlib.sha256(content).hexdigest(),
                  "path": str(path), "reference": reference}


def skill_guidance(name, reference=None, audit=None, stage=""):
    guidance, metadata = load_agent_skill(name, reference)
    if audit:
        audit.write("skill_loaded", stage=stage, **metadata)
    return guidance


def load_history_skill(name="history-research"):
    if name not in {"history-research", "consistency-audit", "story-audit"}:
        raise ValueError("不支持的审查 Skill")
    body, metadata = load_agent_skill(name)
    skill_root = Path(__file__).resolve().parents[1] / "agent_skills"
    script = skill_root / "history-research" / "scripts" / "history_tools.py"
    spec = importlib.util.spec_from_file_location("novel_history_tools", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    metadata["version"] = hashlib.sha256((skill_root / name / "SKILL.md").read_bytes() + script.read_bytes()).hexdigest()
    return body, module.HistoryTools, metadata
