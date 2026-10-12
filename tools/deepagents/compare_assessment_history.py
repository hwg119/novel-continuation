"""Controlled two-call assessment comparison. Requires explicit live-data approval."""
import argparse
import copy
import hashlib
import json
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

from core.config_manager import load_config,get_llm_config
from core.plan_run_log import PlanRunLogger
from llm_adapters import check_base_url
from langchain_openai import ChatOpenAI
from web.chat_memory import compact_dialogue_context


def comparison_contexts(context):
    """The sole intervention is removal of prior assistant opinions."""
    retained=copy.deepcopy(context)
    isolated=copy.deepcopy(context)
    isolated['conversation']=[row for row in isolated.get('conversation',[])
                              if row.get('role')!='assistant']
    return [('A_retained',retained),('B_isolated',isolated)]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-log',required=True)
    parser.add_argument('--model-config',default='deepseek-flash')
    parser.add_argument('--approved-live-fiction',action='store_true')
    parser.add_argument('--compact-context',action='store_true',help='用同一资料测试精简输入，只做一次评估')
    args=parser.parse_args()
    if not args.approved_live_fiction:
        parser.error('真实小说外发须事先获得用户明确批准，再使用 --approved-live-fiction')
    source=Path(args.source_log).resolve()
    rows=[json.loads(line) for line in source.read_text(encoding='utf-8').splitlines()]
    context=next(row['context'] for row in rows if row['event']=='trial_input')
    starts=[row['details'] for row in rows if row.get('event')=='agent_event'
            and row.get('details',{}).get('event')=='model_start']
    final_messages=starts[-1]['messages'][0]
    system=next(m['content'] for m in final_messages if m['type']=='system')
    tools=[json.loads(m['content']) for m in final_messages if m['type']=='tool']
    skill=next(item for item in tools if item.get('metadata',{}).get('name')=='chapter-planning')
    evidence=[item for item in tools if 'sources' in item]
    if not evidence: raise ValueError('来源日志未包含正文查阅结果，不能做本次固定证据对照')
    context={**context,'fixed_evidence':evidence}
    system+='\n【两组固定使用的专业 Skill】\n'+skill['instructions']
    system+='\n现在只做自然语言评估，资料已固定，不提出执行操作。只评价明确指定的当前工作对象。'
    request='重新评估当前生效的第208章分幕是否还需要优化。'
    config=get_llm_config(load_config(str(ROOT/'config.json')),args.model_config)
    if str(config.get('interface_format') or '').lower() not in ('openai','deepseek'):
        raise ValueError('对照实验只支持 OpenAI 兼容或 DeepSeek 接口')
    parameters={'temperature':0.1,'max_tokens':1600,'thinking':{'type':'disabled'}}
    model=ChatOpenAI(model=config['model_name'],api_key=config.get('api_key'),
        base_url=check_base_url(config.get('base_url') or '') or None,
        temperature=parameters['temperature'],max_tokens=parameters['max_tokens'],
        extra_body={'thinking':parameters['thinking']},timeout=180,max_retries=0,
        use_responses_api=False,stream_usage=False)
    output=ROOT/'workspace/agent_trials'/('assessment_ab_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6])
    audit=PlanRunLogger(str(output),'comparison',config['model_name'],208,secrets=(config.get('api_key',''),))
    audit.write('experiment_setup',source=str(source),source_hash=hashlib.sha256(source.read_bytes()).hexdigest(),
                intervention='B only removes conversation rows with role assistant; all other context unchanged',
                parameters=parameters,skill=skill['metadata'],request=request,
                limitation='One sample per group; retained historical action/task-result data may contain other anchoring sources.')
    results=[]
    groups=[('C_compact',compact_dialogue_context(context))] if args.compact_context else comparison_contexts(context)
    for group,group_context in groups:
        messages=[{'role':'system','content':system},{'role':'user',
                  'content':json.dumps(group_context,ensure_ascii=False)+'\n【本次用户消息】\n'+request}]
        audit.write('model_request',group=group,messages=messages,
                    conversation_rows=len(group_context.get('conversation',[])))
        started=time.monotonic()
        response=model.invoke(messages)
        elapsed=round(time.monotonic()-started,2)
        result={'group':group,'reply':response.content,'elapsed_seconds':elapsed,
                'finish_reason':response.response_metadata.get('finish_reason'),
                'usage':response.usage_metadata}
        audit.write('model_response',**result)
        results.append(result)
        print(json.dumps(result,ensure_ascii=False),flush=True)
    audit.write('experiment_complete',results=results)
    print(json.dumps({'log':str(audit.path),'fiction_modified':False},ensure_ascii=False),flush=True)


if __name__=='__main__': main()
