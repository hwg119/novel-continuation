"""Compare two local synthetic story-audit evaluation runs without calling a model.

Only run directories under workspace/agent_trials are accepted.  The comparison
uses metadata and the regression report, not raw prompts or novel text.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRIALS = (ROOT / "workspace" / "agent_trials").resolve()


def run_dir(value):
    path = Path(value).resolve()
    if not path.is_relative_to(TRIALS) or not (path / "runs" / "plan_logs").is_dir():
        raise ValueError("评测目录必须位于 workspace/agent_trials，且包含 runs/plan_logs")
    return path


def rows(path):
    for item in sorted((path / "runs" / "plan_logs").glob("*.jsonl")):
        records = []
        for line in item.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                records.append(row)
        yield item.stem, records


def summarize(path):
    result = {}
    for case, records in rows(path):
        input_row = next((row for row in records if row.get("event") == "regression_input"), {})
        start = next((row for row in records if row.get("event") == "run_started"), {})
        skill = next((row for row in records if row.get("event") == "skill_loaded"), {})
        report = next((row for row in reversed(records) if row.get("event") == "regression_result"), {})
        responses = [row for row in records if row.get("event") == "research_model_response"]
        prompt = str(input_row.get("text", "")) + "\n" + str(input_row.get("previous", ""))
        report_result = report.get("result") if isinstance(report.get("result"), dict) else {}
        screening = report.get("screening") if isinstance(report.get("screening"), dict) else {}
        elapsed = sum(int(row.get("elapsed_ms") or 0) for row in responses)
        result[case] = {
            "model": start.get("model"),
            "skill_version": skill.get("version"),
            "variant": input_row.get("variant"),
            "input_chars": {"current": input_row.get("text_chars"), "previous": input_row.get("previous_chars")},
            "input_fingerprint": hashlib.sha256(prompt.encode()).hexdigest()[:12] if prompt.strip() else None,
            "model_calls": len(responses),
            "elapsed_ms": elapsed or None,
            "has_revision_requirement": bool(str(report_result.get("revision_requirements") or "").strip()),
            "screening": screening or {"status": "未记录（旧版评测日志）"},
        }
    return result


def compare(left, right):
    left_summary, right_summary = summarize(left), summarize(right)
    cases = sorted(set(left_summary) | set(right_summary))
    comparison = []
    for case in cases:
        before, after = left_summary.get(case), right_summary.get(case)
        if not before or not after:
            comparison.append({"case": case, "status": "仅一侧存在", "left": before, "right": after})
            continue
        changed = [key for key in ("model", "skill_version", "variant", "input_chars", "input_fingerprint",
                                   "has_revision_requirement", "screening") if before.get(key) != after.get(key)]
        comparison.append({"case": case, "status": "有差异" if changed else "元数据一致",
                           "changed": changed, "left": before, "right": after,
                           "elapsed_ms_delta": (after["elapsed_ms"] or 0) - (before["elapsed_ms"] or 0)})
    return {"left": str(left), "right": str(right), "cases": comparison,
            "note": "此报告只比较可机器读取的元数据与初筛；故事判断仍需人工审阅完整结果。"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left", help="基准评测目录（workspace/agent_trials 下）")
    parser.add_argument("right", help="对比评测目录（workspace/agent_trials 下）")
    args = parser.parse_args()
    try:
        print(json.dumps(compare(run_dir(args.left), run_dir(args.right)), ensure_ascii=False, indent=2))
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
