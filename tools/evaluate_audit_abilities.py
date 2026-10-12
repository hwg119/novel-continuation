"""Synthetic ability benchmark: fixed evidence, independent calls, human grading."""
import argparse
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def task_for(case, stage):
    if stage=='discover':
        return '独立审校本章，指出影响主线的明确问题与值得提出的可选增强。无需强行找问题。'
    if stage=='verify':
        return '定向核实，不预设有错：'+case['focus']+' 若成立说明依据，若不成立明确不需要修改。按 Skill 输出。'
    return ('请检查是否需要修改，并给出最小修订要求，不重写正文。核对目标：'+case['focus']+
            ' 保留其余已确立事实；无需修改时 requirements 为空。只输出 JSON：'
            '{"requirements":["简短可执行修改要求"],"basis":"准确原句与简短依据",'
            '"preserve":["保留事实"],"uncertainties":[]}。此格式替代 Skill 的输出格式。')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--model-config',default='deepseek-flash')
    args=parser.parse_args()
    if not args.live: parser.error('需要 --live 明确启用合成样本模型测试')
    from core.config_manager import load_config,get_llm_config
    from core.continuation import ContinuationLLM,_first_json_object
    from core.model_response import complete_review
    from core.agent_skill import load_agent_skill
    from core.plan_run_log import PlanRunLogger
    cfg=get_llm_config(load_config(str(ROOT/'config.json')),args.model_config)
    guidance,skill=load_agent_skill('story-audit')
    cases=json.loads((ROOT/'tools/deepagents/fixtures/story_audit_ability_cases.json').read_text(encoding='utf-8'))
    path=ROOT/'workspace/agent_trials'/('audit_abilities_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8])
    path.mkdir(parents=True)
    print('Logs: '+str(path),flush=True)
    for repeat in (1,2):
        for case in cases:
            sources=[{'id':'H01','kind':'saved_body','chapter':1,'scope':'full_synthetic_excerpt','text':case['previous']},
                     {'id':'T01','kind':'current_body','chapter':2,'scope':'full_synthetic_excerpt','text':case['text']}]
            for stage in ('discover','verify','repair'):
                audit=PlanRunLogger(str(path),f"{case['id']}_{stage}_{repeat}",cfg.get('model_name',''),2,secrets=(cfg.get('api_key',''),))
                prompt=guidance+'\n【本次任务】\n'+task_for(case,stage)+'\n【来源目录】\n'+json.dumps(sources,ensure_ascii=False)
                audit.write('benchmark_request',case=case['id'],stage=stage,repeat=repeat,skill=skill,
                            prompt=prompt,temperature=0.1,num_predict=1800,disable_thinking=True,
                            evaluation_criteria=case['criteria'],expected_issue=case['expected_issue'],
                            criteria_sent_to_model=False,independent_call=True)
                try:
                    raw=complete_review(ContinuationLLM(cfg),prompt,audit=audit,
                        system='你是故事编辑，只输出本次任务要求的 JSON，不输出思考过程。',
                        temperature=0.1,num_predict=1800,disable_thinking=True)
                    result=_first_json_object(raw)
                    audit.write('benchmark_result',result=result,human_review_required=True)
                    print(json.dumps({'case':case['id'],'stage':stage,'repeat':repeat,'result':result},ensure_ascii=False),flush=True)
                except Exception as exc:
                    audit.write('benchmark_error',error=str(exc))
                    print(json.dumps({'case':case['id'],'stage':stage,'repeat':repeat,'error':str(exc)},ensure_ascii=False),flush=True)


if __name__=='__main__': main()
