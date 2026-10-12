"""Run the production story-audit Skill on synthetic regression cases (opt-in live)."""
import argparse
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def evaluate_case(case, result):
    """Check output shape only; semantic accuracy remains explicit human review."""
    found = bool(result.get("revision_requirements", "").strip())
    return {"case": case["id"], "expected": case["expected"],
            "issue_presence_matches": found == case["requires_issue"],
            "semantic_review_required": True, "result": result}


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="允许发送合成样本给配置模型")
    parser.add_argument("--model-config", default="deepseek-flash")
    parser.add_argument("--long", action="store_true", help="相同事实加入无关场景，测试长上下文")
    args = parser.parse_args()
    if not args.live:
        parser.error("必须显式传入 --live；默认不调用模型")
    from core.config_manager import load_config, get_llm_config
    from core.plan_run_log import PlanRunLogger
    from core.story_review import check_story
    config = get_llm_config(load_config(str(ROOT / "config.json")), args.model_config)
    cases = json.loads((ROOT / "tools/deepagents/fixtures/story_audit_cases.json").read_text(encoding="utf-8"))
    if args.long:
        cases = [expand_case(case) for case in cases]
    # Inputs contain only synthetic fiction; isolate artifacts from real projects.
    directory = ROOT / 'workspace/agent_trials' / ('story_audit_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8])
    directory.mkdir(parents=True)
    print(f'Logs: {directory}', flush=True)
    for case in cases:
        audit = PlanRunLogger(str(directory), case["id"], config.get("model_name", ""),
                              2, secrets=(config.get("api_key", ""),))
        audit.write('regression_input', case=case['id'], variant='long' if args.long else 'short',
                    text=case['text'], previous=case['previous'],
                    text_chars=len(case['text']), previous_chars=len(case['previous']))
        result = check_story(case["text"], {}, "", config,
                             previous_context=case["previous"], audit=audit,
                             project_dir=str(directory), chapter_number=2)
        report = evaluate_case(case, result)
        audit.write('regression_result', **report)
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
