# -*- coding: utf-8 -*-
"""精简修订：一次生成、基础检查、一次全文验收；无自动重试。"""
import re
import time
from pathlib import Path
from typing import TypedDict


class RevisionState(TypedDict, total=False):
    source: str
    title: str
    requirements: str
    settings: dict
    chapter_number: int
    model_name: str
    candidate: str
    revision_mode: str
    workflow_version: int
    mechanical_issues: list[str]
    issues: list[str]


def checkpoint_path(project_dir):
    return Path(project_dir) / "runs" / "revision_checkpoints.sqlite"


def _graph(model, saver, update, audit):
    from langgraph.graph import StateGraph, START, END
    from core.chapter_revision import build_whole_chapter_prompt, check_chapter_candidate, review_candidate_requirements
    from core.continuation import clean_text
    from core.revision_patches import generate_local_patch

    def generate(state):
        if state.get("revision_mode") == "polish":
            from core.prose_polish import generate_polish
            update("正在使用表达润色 Skill，保留剧情与关键信息")
            candidate = generate_polish(model, state["source"], state["requirements"], state["settings"], audit)
        elif state.get("revision_mode") == "local":
            update("正在使用修订 Skill 一次性修订必要段落")
            candidate = generate_local_patch(model, state["source"], state["requirements"], audit, state.get("settings"))
        else:
            update("正在使用修订 Skill 生成整章修订稿")
            prompt = build_whole_chapter_prompt(state["source"], state["title"], state["settings"], state["requirements"], audit=audit)
            audit.write("revision_request", prompt=prompt)
            raw = model.complete(prompt, system="你是严谨的小说编辑，只输出完整章节正文。",
                                 temperature=0.3, num_predict=max(4096, int(len(state["source"]) * 1.9)), disable_thinking=True)
            audit.write("revision_response", raw_response=raw)
            candidate = clean_text(raw, state["settings"])
        return {"candidate": candidate}

    def mechanical(state):
        update("正在检查正文完整性与字面要求")
        return {"mechanical_issues": check_chapter_candidate(state["candidate"], state["source"], state["requirements"], state["settings"])}

    def acceptance(state):
        if state["mechanical_issues"]:
            issues = state["mechanical_issues"]
            update("基础检查发现问题，保留建议稿供核对")
        elif state.get("revision_mode") == "polish":
            from core.prose_polish import review_polish
            update("正在对照原文验收润色边界，不另行审校剧情")
            issues = review_polish(model, state["source"], state["candidate"], state["requirements"], audit)
        else:
            update("正在使用修订 Skill 验收原要求，不另行增加建议")
            issues = review_candidate_requirements(model, state["candidate"], state["requirements"],
                       audit=audit, stage="final_acceptance", review_scope="requirements")
        audit.write("revision_acceptance", issues=issues, automatic_retry=False,
                    review_scope="requirements", unrelated_issues_out_of_scope=True)
        return {"issues": issues}

    def node(name, fn):
        def run(state):
            started = time.monotonic()
            audit.write("node_started", stage=name)
            try:
                result = fn(state)
            except Exception as exc:
                audit.write("node_failed", stage=name, error=str(exc))
                raise
            audit.write("node_completed", stage=name, elapsed_ms=round((time.monotonic() - started) * 1000))
            return result
        return run

    graph = StateGraph(RevisionState)
    for name, fn in (("generate", generate), ("mechanical", mechanical), ("acceptance", acceptance)):
        graph.add_node(name, node(name, fn))
    graph.add_edge(START, "generate")
    graph.add_edge("generate", "mechanical")
    graph.add_edge("mechanical", "acceptance")
    graph.add_edge("acceptance", END)
    return graph.compile(checkpointer=saver)


def run_revision_workflow(project_dir, thread_id, model, update, audit, initial=None):
    from langgraph.checkpoint.sqlite import SqliteSaver
    if not re.fullmatch(r"[A-Za-z0-9_]+", thread_id):
        raise ValueError("无效修订任务编号")
    path = checkpoint_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 10}
    with SqliteSaver.from_conn_string(str(path)) as saver:
        graph = _graph(model, saver, update, audit)
        if initial is None:
            state = graph.get_state(config).values
            if not state or state.get("workflow_version") != 2:
                raise ValueError("旧版修订检查点不兼容精简流程，请重新生成；旧建议稿仍保留")
        else:
            initial = {**initial, "workflow_version": 2}
        result = graph.invoke(initial, config)
    return {"chapter_number": result["chapter_number"], "candidate": result["candidate"],
            "issues": result["issues"], "source": result["source"], "requirements": result["requirements"],
            "log_path": str(audit.path), "workflow": "langgraph", "workflow_thread": thread_id,
            "model_name": result["model_name"], "revision_mode": result.get("revision_mode", "whole")}


def read_revision_checkpoint(project_dir, thread_id):
    from langgraph.checkpoint.sqlite import SqliteSaver
    if not re.fullmatch(r"[A-Za-z0-9_]+", thread_id):
        raise ValueError("无效修订任务编号")
    path = checkpoint_path(project_dir)
    if not path.is_file():
        raise ValueError("此任务没有 LangGraph 检查点")
    with SqliteSaver.from_conn_string(str(path)) as saver:
        checkpoint = saver.get_tuple({"configurable": {"thread_id": thread_id}})
        if not checkpoint:
            raise ValueError("此任务没有可恢复的检查点")
        state = checkpoint.checkpoint["channel_values"]
        if state.get("workflow_version") != 2:
            raise ValueError("旧版检查点不兼容精简流程，请重新生成")
        return state
