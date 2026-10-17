"""Run the production story-audit Skill on synthetic regression cases (opt-in live)."""
import argparse
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def validate_case(case):
    """Keep the human-authored regression suite explicit and safe to send live."""
    required = ("id", "dimension", "previous", "text", "expected", "requires_issue", "screen")
    missing = [key for key in required if key not in case]
    if missing:
        raise ValueError(f"用例缺少字段：{case.get('id', '<unknown>')} · {', '.join(missing)}")
    if not isinstance(case["requires_issue"], bool):
        raise ValueError(f"requires_issue 必须是布尔值：{case['id']}")
    if not all(isinstance(case[key], str) and case[key].strip()
               for key in ("id", "dimension", "previous", "text", "expected")):
        raise ValueError(f"用例文字字段无效：{case['id']}")
    screen = case["screen"]
    if not isinstance(screen, dict) or not all(isinstance(screen.get(key, []), list)
                                               for key in ("must_mention_any", "must_not_mention_any")):
        raise ValueError(f"screen 格式无效：{case['id']}")
    if not all(isinstance(word, str) and word.strip()
               for words in screen.values() if isinstance(words, list) for word in words):
        raise ValueError(f"screen 信号必须是非空文字：{case['id']}")


def screen_result(case, result):
    """Cheap signal screen, deliberately not a substitute for editorial judgment."""
    requirements = str(result.get("revision_requirements") or "").strip()
    searchable = "\n".join(str(result.get(key) or "") for key in
                             ("revision_requirements", "story_details", "story_summary", "report"))
    required = case["screen"]["must_mention_any"]
    forbidden = case["screen"]["must_not_mention_any"]
    missing = required if case["requires_issue"] and required and not any(word in searchable for word in required) else []
    unexpected = [word for word in forbidden if word in searchable]
    return {
        "issue_presence_matches": bool(requirements) == case["requires_issue"],
        "missing_expected_signals": missing,
        "unexpected_signals": unexpected,
        "passed": bool(requirements) == case["requires_issue"] and not missing and not unexpected,
        "human_review_required": True,
    }


def evaluate_case(case, result):
    """Report machine signals and preserve the full output for human scoring."""
    return {"case": case["id"], "dimension": case["dimension"], "expected": case["expected"],
            "screening": screen_result(case, result), "semantic_review_required": True, "result": result}


def expand_case(case):
    """Keep target facts intact, surround them with unrelated synthetic scenes."""
    places = ['河边茶铺', '街角书店', '桥头花店', '小巷面包房', '广场修鞋摊',
              '屋后洗衣房', '市集布料铺', '车站候车室']
    objects = ['瓷杯', '旧书', '花盆', '竹篮', '皮鞋', '木夹', '布卷', '长凳']
    scenes = []
    for index in range(48):
        place, item = places[index % len(places)], objects[index % len(objects)]
        scenes.append(f'【旁线片段{index + 1}】{place}里，店员把{item}摆回原处，给窗边留出一点空隙。'
                      '一个客人走进来，先收好伞，再问有没有靠窗的位置。店员指了指角落，没有催促。'
                      '街上的送货车缓缓经过，车轮压过浅浅的积水。客人等声音远了，才把话说完。'
                      '他们谈起店面的清扫和院里的花，随后各自去忙手上的事。窗帘被风吹起，'
                      '又落回窗台。这段日常没有涉及档案调查、学校同学或先前的来信。')
    before = dict(case)
    before['text'] = '\n\n'.join(scenes[:24] + [case['text']] + scenes[24:])
    # Historical evidence is also away from the edges, without changing its content.
    before['previous'] = '\n\n'.join(scenes[:8] + [case['previous']] + scenes[8:16])
    return before


def load_cases():
    """Read and validate the deliberately synthetic suite."""
    cases = json.loads((ROOT / "tools/deepagents/fixtures/story_audit_cases.json").read_text(encoding="utf-8"))
    for case in cases:
        validate_case(case)
    return cases


def run_live_suite(model_config="deepseek-flash", long=False, progress=None):
    """Run the suite only when a caller has explicitly requested a live evaluation.

    ``progress`` intentionally receives metadata only; raw synthetic prompts stay in
    the local JSONL artifacts so the web UI never needs to replay them.
    """
    from core.config_manager import load_config, get_llm_config
    from core.plan_run_log import PlanRunLogger
    from core.story_review import check_story

    cases = load_cases()
    config = get_llm_config(load_config(str(ROOT / "config.json")), model_config)
    if not config.get("model_name"):
        raise ValueError(f"未找到模型配置：{model_config}")
    if long:
        cases = [expand_case(case) for case in cases]
    directory = ROOT / 'workspace/agent_trials' / ('story_audit_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8])
    directory.mkdir(parents=True)
    reports = []
    for index, case in enumerate(cases, 1):
        if progress:
            progress(f"正在评测 {index}/{len(cases)}：{case['dimension']}",
                     {"case": case["id"], "dimension": case["dimension"], "index": index, "total": len(cases)})
        audit = PlanRunLogger(str(directory), case["id"], config.get("model_name", ""),
                              2, secrets=(config.get("api_key", ""),))
        audit.write('regression_input', case=case['id'], variant='long' if long else 'short',
                    text=case['text'], previous=case['previous'],
                    text_chars=len(case['text']), previous_chars=len(case['previous']))
        result = check_story(case["text"], {}, "", config,
                             previous_context=case["previous"], audit=audit,
                             project_dir=str(directory), chapter_number=2)
        report = evaluate_case(case, result)
        audit.write('regression_result', **report)
        reports.append(report)
    return {"run_id": directory.name, "cases": len(reports), "variant": "long" if long else "short",
            "screened_passes": sum(1 for report in reports if report["screening"]["passed"]),
            "human_review_required": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="允许发送合成样本给配置模型")
    parser.add_argument("--model-config", default="deepseek-flash")
    parser.add_argument("--long", action="store_true", help="相同事实加入无关场景，测试长上下文")
    parser.add_argument("--validate", action="store_true", help="只校验用例结构，不调用模型")
    args = parser.parse_args()
    cases = load_cases()
    if args.validate:
        print(json.dumps({"suite": "story-audit", "cases": len(cases),
                          "dimensions": sorted({case["dimension"] for case in cases}),
                          "model_called": False}, ensure_ascii=False, indent=2))
        return
    if not args.live:
        parser.error("必须显式传入 --live；默认不调用模型。可使用 --validate 校验用例。")
    report = run_live_suite(args.model_config, args.long,
                            lambda message, _: print(message, flush=True))
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
