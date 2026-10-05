"""LangGraph Functional API：复用规划流程，将每次阶段调用保存为可恢复任务。"""
import hashlib
import json
import re
import time
from pathlib import Path


def planning_fingerprint(project_dir, chapter_number):
    """恢复前核对工程设定及近期正文、摘要，避免沿用过期输入。"""
    root = Path(project_dir)
    files = [root / "project.json"]
    # 主动查阅可使用所有历史正文，恢复时不能沿用已变更的早期证据。
    for number in range(1, chapter_number):
        files.extend(root / "chapters" / name for name in
                     (f"chapter_{number}.txt", f"chapter_summary_{number}.txt"))
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(root)).encode())
        digest.update(b"\0" + (path.read_bytes() if path.is_file() else b"<missing>") + b"\0")
    return digest.hexdigest()


def _paths(project_dir, thread_id):
    if not re.fullmatch(r"[A-Za-z0-9_]+", thread_id):
        raise ValueError("无效规划任务编号")
    root = Path(project_dir) / "runs"
    return root / "planning_checkpoints.sqlite", root / "plan_workflows" / f"{thread_id}.json"


def read_planning_checkpoint(project_dir, thread_id):
    from langgraph.checkpoint.sqlite import SqliteSaver
    database, manifest = _paths(project_dir, thread_id)
    if not database.is_file() or not manifest.is_file():
        raise ValueError("此规划没有可恢复的 LangGraph 检查点，请重新生成")
    state = json.loads(manifest.read_text(encoding="utf-8"))
    if state.get("workflow_version") != 1:
        raise ValueError("规划检查点版本不兼容，请重新生成")
    with SqliteSaver.from_conn_string(str(database)) as saver:
        if not saver.get_tuple({"configurable": {"thread_id": thread_id}}):
            raise ValueError("规划阶段尚未保存检查点，请重新生成")
    return state


def run_planning_workflow(project_dir, thread_id, model, update, audit, initial=None):
    from langgraph.func import entrypoint, task
    from langgraph.checkpoint.sqlite import SqliteSaver
    from core.continuation import suggest_chapter_plan
    database, manifest = _paths(project_dir, thread_id)
    database.parent.mkdir(parents=True, exist_ok=True)
    if initial is not None:
        initial = {**initial, "workflow_version": 1}
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps(initial, ensure_ascii=False), encoding="utf-8")
    else:
        read_planning_checkpoint(project_dir, thread_id)

    def run_stage(name, function):
        executed = False

        @task(name=f"planning_{name}")
        def execute():
            nonlocal executed
            executed = True
            started = time.monotonic()
            audit.write("node_started", stage=name)
            try:
                result = function()
                failed = (result.get("check_failed") if isinstance(result, dict) else
                          next((row.get("problem") for row in result
                                if isinstance(row, dict) and row.get("check_failed")), None)
                          if isinstance(result, list) else None)
                if failed:
                    raise ValueError(str(failed))
            except Exception as exc:
                optional = name in {"editorial_patch", "acceptance_editorial_patch"}
                audit.write("optional_node_failed" if optional else "node_failed",
                            stage=name, error=str(exc), optional=optional)
                raise
            audit.write("node_completed", stage=name,
                        elapsed_ms=round((time.monotonic() - started) * 1000))
            return result

        result = execute().result()
        audit.write("planning_stage_returned", stage=name, reused_checkpoint=not executed)
        if not executed:
            update(f"已恢复规划阶段：{name}，未重复调用模型")
        return result

    with SqliteSaver.from_conn_string(str(database)) as saver:
        @entrypoint(checkpointer=saver)
        def planning(state):
            return suggest_chapter_plan(
                state["settings"], model, state["num_beats"],
                state["previous_context"], state["wiki_history"],
                progress=update, audit=audit, stage_runner=run_stage, project_dir=project_dir)

        result = planning.invoke(initial, {"configurable": {"thread_id": thread_id}})
    return {**result, "workflow": "langgraph", "workflow_thread": thread_id,
            "log_path": str(audit.path)}
