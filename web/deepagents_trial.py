"""Default dialogue agent host; owned subprocess permits reliable cancellation."""
import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from core.plan_run_log import PlanRunLogger
from web.chat_artifacts import ARTIFACT_GUIDANCE
from web.chat_research import ChatResearch, RESEARCH_GUIDANCE
from web.chat_skills import skill_catalog, read_dialogue_skill
from web.chat_memory import compact_dialogue_context, read_conversation_turn


def run_trial(config,context,text,guide,backend,emit,running,command=None):
    original_chars=len(json.dumps(context,ensure_ascii=False))
    context={**compact_dialogue_context(context),'available_skills':skill_catalog()}
    root=Path(__file__).resolve().parents[1]
    interpreter=os.environ.get('NOVEL_DEEPAGENTS_PYTHON') or sys.executable
    if command is None and not Path(interpreter).is_file():
        raise ValueError('Deep Agents 解释器不存在，请检查安装；未自动回退到旧入口')
    audit=PlanRunLogger(backend.path,'deepagents_'+uuid.uuid4().hex,config.get('model_name',''),
                        context.get('current_chapter') or 0,secrets=(config.get('api_key',''),))
    secret=str(config.get('api_key') or '')
    def safe(value):
        if isinstance(value,str): return value.replace(secret,'[已隐藏]') if secret else value
        if isinstance(value,dict): return {k:safe(v) for k,v in value.items()}
        if isinstance(value,list): return [safe(v) for v in value]
        return value
    emit('已启用 Deep Agents 对话引擎',{'log_path':str(audit.path),'model_name':config.get('model_name')})
    audit.write('context_compacted',before_chars=original_chars,after_chars=len(json.dumps(context,ensure_ascii=False)))
    audit.write('trial_input',context=context,user_request=text)
    env={**os.environ,'PYTHONIOENCODING':'utf-8','PYTHONUTF8':'1',
         'LANGSMITH_TRACING':'false','LANGCHAIN_TRACING_V2':'false'}
    options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
    process=subprocess.Popen(command or [interpreter,'-u',str(root/'tools'/'deepagents'/'worker.py')],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        text=True,encoding='utf-8',cwd=str(root),env=env,**options)
    messages=queue.Queue()
    errors=[]
    def stdout():
        try:
            for line in process.stdout:
                messages.put(json.loads(line))
        except Exception as exc: messages.put({'type':'error','message':str(exc)})
        finally: messages.put({'type':'eof'})
    def stderr():
        for line in process.stderr:
            errors.append(safe(line[-1000:]))
            del errors[:-8]
    output_reader=threading.Thread(target=stdout,daemon=True)
    error_reader=threading.Thread(target=stderr,daemon=True)
    output_reader.start();error_reader.start()
    research=ChatResearch(backend.path,backend)
    decision=None
    loaded_skills={}
    history_reads=0
    started=time.monotonic()
    try:
        process.stdin.write(json.dumps({'config':config,'context':context,'text':text,
            'system_prompt':guide+'\n'+ARTIFACT_GUIDANCE+'\n'+RESEARCH_GUIDANCE},ensure_ascii=False)+'\n')
        process.stdin.flush()
        while True:
            if not running(): raise InterruptedError('已停止 Deep Agents 回复')
            if time.monotonic()-started>480: raise ValueError('Deep Agents 试点达到本轮时间上限')
            try: item=messages.get(timeout=.2)
            except queue.Empty: continue
            kind=item.get('type')
            if kind=='progress':
                details=safe(item.get('details') or {})
                audit.write('agent_event',details=details)
                emit(item['text'],{key:details[key] for key in ('event','call') if key in details})
            elif kind=='tool_call':
                args=item.get('arguments') or {}
                audit.write('tool_request',tool=item.get('name'),arguments=args)
                try:
                    if item['name']=='skill':
                        key=(args.get('name'),args.get('reference') or None)
                        if key not in loaded_skills:
                            if len(loaded_skills)>=4: raise ValueError('本轮技能加载已达上限，请使用已加载方法完成任务')
                            loaded_skills[key]=read_dialogue_skill(*key)
                            metadata=loaded_skills[key]['metadata']
                            audit.write('skill_loaded',stage='dialogue',**metadata)
                            emit('已加载专业 Skill',metadata)
                        result=loaded_skills[key]
                    elif item['name']=='conversation':
                        if history_reads>=3: raise ValueError('本轮历史对话查阅已达上限')
                        history_reads+=1
                        result=read_conversation_turn(backend,args.get('turn_id'),args.get('offset',0))
                    elif item['name']=='read': result={'sources':research.execute(args)}
                    elif item['name']=='propose':
                        if decision is not None: raise ValueError('本轮已有操作提案，不重复提交')
                        tool=args.get('tool'); number=args.get('chapter')
                        if tool not in {'plan','plan_revision','generate','consistency','revise','illustration','view','navigate'}:
                            raise ValueError('操作名无效；可选 plan/plan_revision/generate/consistency/revise/illustration/view/navigate。修改已有分幕使用 plan_revision')
                        if type(number) is not int or number<1: raise ValueError('章号无效')
                        if tool=='plan' and args.get('target_artifact_id'):
                            raise ValueError('plan 从零构建不使用旧稿；如修改选定的分幕请用 plan_revision')
                        if tool in ('plan_revision','revise'):
                            expected={'plan_revision':'plan','revise':'text'}[tool]
                            selected=backend.resolve(number,args.get('target_artifact_id'),expected)
                            if not selected.get('compatible_with_saved',True): raise ValueError('保存稿已变化，请重新选择工作对象')
                            args={**args,'target_artifact_id':selected['id']}
                        source=args.get('source_request_turn')
                        if source and not any(row.get('turn_id')==source and row.get('chapter')==number
                            for row in context.get('recent_user_requests',[])):
                            raise ValueError('来源请求不属于当前会话章节')
                        decision={**args,'research':[]}
                        result={'accepted':True,'message':'已核验提案；尚未启动任务或修改文件'}
                    else: raise ValueError('工具未开放')
                except (ValueError,TypeError,OSError) as exc: result={'error':safe(str(exc))}
                audit.write('tool_result',result=result)
                display_result={'metadata':result['metadata']} if item['name']=='skill' and 'metadata' in result else result
                emit('Deep Agents 工具调用',{'tool':item.get('name'),'arguments':safe(args),'result':safe(display_result)})
                process.stdin.write(json.dumps({'id':item['id'],'result':result},ensure_ascii=False)+'\n');process.stdin.flush()
            elif kind=='result':
                reply=str(item.get('reply') or '').strip()
                audit.write('trial_result',decision=decision,reply=reply,model_calls=item.get('model_calls'),
                            skills_used=[entry['metadata'] for entry in loaded_skills.values()],
                            elapsed_seconds=round(time.monotonic()-started,2))
                if decision: return {**decision,'message':reply,'_agent_runtime':'deepagents'}
                if not reply: raise ValueError('Deep Agents 返回了空回复')
                return {'tool':'reply','chapter':context.get('current_chapter'),'research':[],
                        'message':reply,'_reply_text':reply,'_agent_runtime':'deepagents'}
            elif kind in ('error','eof'):
                raise ValueError(safe(item.get('message') or ''.join(errors) or 'Deep Agents 进程提前结束'))
    except BaseException as exc:
        audit.write('trial_error',error=safe(str(exc)),elapsed_seconds=round(time.monotonic()-started,2))
        raise
    finally:
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=2)
            except subprocess.TimeoutExpired: process.kill();process.wait(timeout=2)
        output_reader.join(timeout=2);error_reader.join(timeout=2)
        for stream in (process.stdin,process.stdout,process.stderr): stream.close()
