"""Offline regression checks for the shipped writing-quality baseline.

Uses synthetic text and standard-library unittest only.  It deliberately never
loads a model configuration or calls a remote model.
"""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.evaluate_story_audit import expand_case, validate_case


CASES = json.loads((ROOT / "tools/deepagents/fixtures/story_audit_cases.json").read_text(encoding="utf-8"))


class QualityRegressionTests(unittest.TestCase):
    def test_story_audit_catalog_is_small_balanced_and_valid(self):
        self.assertEqual(len(CASES), 4)
        self.assertEqual(sum(case["requires_issue"] for case in CASES), 2)
        self.assertEqual(
            {case["dimension"] for case in CASES},
            {"弱线索边界", "跨章时间线", "避免重复增强", "人物知情边界"},
        )
        for case in CASES:
            validate_case(case)

    def test_long_context_variant_preserves_the_single_target_fact(self):
        original = copy.deepcopy(CASES)
        for case in CASES:
            expanded = expand_case(case)
            self.assertGreater(len(expanded["text"]), 8000)
            self.assertGreater(len(expanded["previous"]), 2500)
            self.assertEqual(expanded["text"].count(case["text"]), 1)
            self.assertEqual(expanded["previous"].count(case["previous"]), 1)
        self.assertEqual(CASES, original)

    def test_production_story_audit_receives_current_and_previous_evidence(self):
        import core.consistency
        import core.planning_research
        from core.story_review import check_story

        calls = []

        def review(_llm, prompt, sources, _project_dir, _chapter, _parse, **kwargs):
            calls.append((prompt, sources, kwargs))
            return {"issues": [], "enhancements": [], "decision_summary": "测试", "uncertainties": []}

        original_llm = core.consistency.ContinuationLLM
        original_review = core.planning_research.research_review
        core.consistency.ContinuationLLM = lambda _config: object()
        core.planning_research.research_review = review
        try:
            for case in CASES:
                result = check_story(case["text"], {}, "", {}, previous_context=case["previous"], chapter_number=2)
                prompt, sources, kwargs = calls[-1]
                self.assertEqual(kwargs["skill_name"], "story-audit")
                self.assertIn(case["previous"], prompt)
                self.assertEqual(next(row["text"] for row in sources if row["id"] == "T01"), case["text"])
                self.assertEqual(result["revision_requirements"], "")
        finally:
            core.consistency.ContinuationLLM = original_llm
            core.planning_research.research_review = original_review

    def test_planning_and_revision_guards_keep_versions_distinct(self):
        from core.plan_inputs import DISCUSSION_MARKER, planning_input_prompt
        from core.plan_revision_check import plan_changes

        settings = {"chapter_requirements": "保留人物关系\n" + DISCUSSION_MARKER + "\n旧六幕完整方案XYZ",
                    "_planning_request": "重新构建，加强推进"}
        prompt = planning_input_prompt(settings)
        self.assertIn("保留人物关系", prompt)
        self.assertIn("重新构建，加强推进", prompt)
        self.assertNotIn("旧六幕完整方案XYZ", prompt)
        changes = plan_changes({"chapter_title": "旧稿", "beats": [{"name": "旧幕", "desc": "事件不变。"}]},
                             {"chapter_title": "新稿", "beats": [{"name": "新幕", "desc": "事件不变。"}]})
        self.assertEqual(changes["content_changed_beats"], [])
        self.assertEqual(changes["renamed_beats"], [1])

    def test_comparison_reports_configuration_and_result_deltas_without_raw_text(self):
        from tools.compare_quality_runs import compare

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, requirement in (("before", ""), ("after", "统一日期")):
                log_dir = root / name / "runs" / "plan_logs"
                log_dir.mkdir(parents=True)
                rows = [
                    {"event": "run_started", "model": "synthetic-model"},
                    {"event": "regression_input", "variant": "short", "text": "合成正文", "previous": "合成前章",
                     "text_chars": 4, "previous_chars": 4},
                    {"event": "skill_loaded", "version": "skill-v1"},
                    {"event": "research_model_response", "elapsed_ms": 12},
                    {"event": "regression_result", "result": {"revision_requirements": requirement},
                     "screening": {"passed": bool(requirement)}},
                ]
                (log_dir / "date_conflict.jsonl").write_text(
                    "\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
            report = compare(root / "before", root / "after")
            row = report["cases"][0]
            self.assertEqual(row["case"], "date_conflict")
            self.assertIn("has_revision_requirement", row["changed"])
            self.assertNotIn("合成正文", json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
