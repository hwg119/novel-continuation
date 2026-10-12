"""Small live routing trial using synthetic artifacts; never runs writing tools."""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from core.config_manager import load_config, get_llm_config
from core.project_manager import save_project_settings
from web.chat_store import ChatStore
from web.chat_artifacts import ChatArtifacts
from web.deepagents_trial import run_trial


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--live',action='store_true',help='Explicitly enable up to three real-model routing trials')
    parser.add_argument('--model-config',default='')
    args=parser.parse_args()
    if not args.live: parser.error('Live calls require --live; use pytest for offline protocol tests')
    cfg=get_llm_config(load_config(str(ROOT/'config.json')),args.model_config or None)
    if str(cfg.get('interface_format','')).lower() not in ('openai','deepseek'):
        parser.error('Select an OpenAI-compatible or DeepSeek config')
    report_dir=ROOT/'workspace'/'agent_trials'/time.strftime('%Y%m%d_%H%M%S')
    report_dir.mkdir(parents=True,exist_ok=True)
    outcomes=[]
    with tempfile.TemporaryDirectory(prefix='novel_deepagents_') as tmp:
        path=Path(tmp)
        save_project_settings(tmp,{'chapter_number':208,'chapter_title':'袖扣与回信',
            'beats':[{'name':'旧幕','desc':'已保存的旧版本，哈利调查袖扣。'}]})
        records={}
        class Jobs:
            def get(self,path,ident): return records.get(ident)
        store=ChatStore(path/'chat.db')
        cases=[('同名标题选择','继续修改《痕与树影》的最后一版：补清金妮向哈利传递信息。'),
               ('接续澄清','最后一版'),
               ('查阅后修订','先读当前候选，再修正第五幕提前使用第六幕才取得的碎纸片，不改其他幕。')]
        for name,text in cases:
            session=store.create('trial',208); ids=[]
            for i,title in enumerate(('墨痕与树影','墨痕与树影','袖扣与回信')):
                action=store.action(session,{'tool':'plan','chapter':208}); ids.append(action)
                store.action_status(session,action,'started',action)
                beats=[{'name':'第五幕','desc':'哈利开始比对碎纸片上的笔迹。'},
                       {'name':'第六幕','desc':'哈利首次在门缝下捡到碎纸片。'}] if i==1 else [{'name':'旧幕','desc':'旧版本调查路线。'}]
                records[action]={'id':action,'status':'completed','created_at':f'2026-10-11 19:{i:02d}:00',
                    'result':{'chapter_title':title,'beats':beats}}
            backend=ChatArtifacts(store,Jobs(),tmp,session)
            backend.select(backend.resolve(208,ids[1],'plan'),'当前讨论的是墨痕与树影的最新候选')
            context={**backend.context(208),'current_chapter':208,'conversation':[], 'recent_user_requests':[]}
            previous=None
            if name=='接续澄清':
                previous=store.begin(session,'使用《墨痕与树影》这版，补清金妮向哈利传递信息。',208)
                store.finish(session,previous,'done')
                context['recent_user_requests']=[{'turn_id':previous,'chapter':208,
                    'text':'使用《墨痕与树影》这版，补清金妮向哈利传递信息。','already_proposed':False}]
                context['conversation']=[{'role':'user','data':context['recent_user_requests'][0]},
                    {'role':'assistant','data':{'text':'这个标题有两个版本，你要哪一版？'}}]
            events=[];started=time.monotonic()
            try:
                result=run_trial(cfg,context,text,'你是小说创作助手，讨论不授权修改，明确请求可提出修改分幕操作。',
                                 backend,lambda message,data:events.append({'text':message,'details':data}),lambda:True)
                passed=(result.get('tool')=='plan_revision' and result.get('target_artifact_id')==ids[1]
                    and (previous is None or result.get('source_request_turn')==previous))
                if name=='查阅后修订':
                    passed=passed and any(e['details'].get('arguments',{}).get('tool')=='read_artifact' for e in events)
                row={'case':name,'passed':passed,'decision':result}
            except Exception as exc: row={'case':name,'passed':False,'error':str(exc)}
            row['elapsed_seconds']=round(time.monotonic()-started,2)
            row['model_calls']=sum(e['details'].get('event')=='model_start' for e in events)
            outcomes.append(row)
            print(json.dumps(row,ensure_ascii=False),flush=True)
        for source in (path/'runs'/'plan_logs').glob('deepagents_*.jsonl'):
            (report_dir/source.name).write_bytes(source.read_bytes())
    (report_dir/'results.json').write_text(json.dumps(outcomes,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':sum(row['passed'] for row in outcomes),'total':len(outcomes),
                      'report_dir':str(report_dir)},ensure_ascii=False),flush=True)


if __name__=='__main__': main()
