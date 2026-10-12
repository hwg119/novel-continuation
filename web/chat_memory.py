"""Compact model inputs without changing stored conversations or work products."""
import copy
import json


OBJECT_KEYS=('id','kind','chapter','title','status','created_at','hash','derived_from',
             'compatible_with_saved','review_status','beat_names')


def compact_dialogue_context(context):
    result=copy.deepcopy(context)
    history=[]
    for row in result.get('conversation',[]):
        data=row.get('data') or {}
        role=row.get('role')
        if role=='user':
            history.append(row)
        elif role=='assistant':
            history.append({'role':role,'source_state':'historical_opinion_not_evidence',
                'data':{'turn_id':data.get('turn_id'),'available_via':'read_conversation_turn'}})
        else:
            allowed=('turn_id','action_id','job_id','chapter','tool','status','view','can_resume')
            history.append({'role':role,'source_state':row.get('source_state'),
                'data':{key:data[key] for key in allowed if key in data},
                'available_via':'list_artifacts/read_artifact; read_conversation_turn for original discussion'})
    result['conversation']=history
    current=(result.get('current_work_object') or {}).get('id')
    active=result.get('active_work_objects') or {}
    for kind,row in list(active.items()):
        if kind=='audit' or (current and row.get('id')!=current):
            active[kind]={key:value for key,value in row.items() if key!='content'}
            active[kind]['content_available_via']='read_artifact'
        if kind=='audit':
            active[kind]['source_state']='historical_assessment_not_evidence'
        elif kind=='plan':
            active[kind]['source_state']='creative_target_not_body_evidence'
    if active.get('plan'):
        plan=result.get('plan') or {}
        result['plan']={'artifact_id':active['plan']['id'],'chapter_title':plan.get('chapter_title'),
                        'chapter_brief':plan.get('chapter_brief'),
                        'source_state':'creative_target_not_body_evidence',
                        'content_location':('active_work_objects.plan.content' if active['plan'].get('content')
                                            else 'read_artifact')}
    result['artifacts']=[{key:row[key] for key in OBJECT_KEYS if key in row}
                         for row in result.get('artifacts',[])]
    number=result.get('current_chapter')
    excerpt=result.get('chapter_excerpt')
    if isinstance(excerpt,str) and excerpt:
        result['chapter_source']={'id':f'body:{number}', 'chapter':number,
            'kind':'saved_body_excerpt', 'source_state':'saved_body',
            'offset':0,'provided_chars':len(excerpt),'truncated':bool(result.get('excerpt_truncated')),
            'content_location':'chapter_excerpt', 'full_body_available_via':'read_chapter/read_artifact'}
    for request in result.get('recent_user_requests',[]):
        request['source_state']='historical_request_not_current_audit_goal'
        request['execution_state']='operation_proposed_not_proof_of_completion' if request.get('already_proposed') else 'not_verified'
    result['memory_guidance']=('当前稿在当前工作对象中完整提供一次；其他版本通过作品工具读取。'
        '旧助手意见和旧任务稿件不默认作为评价依据。需要承接之前的建议或澄清时，'
        '用 read_conversation_turn 读取相应 turn_id 的原话；不要凭索引猜测建议内容。'
        '重新审校先独立判断正文与历史原文，不默认读取旧审校来寻找答案。'
        '用户要求比较、解释或采用旧结论时可按需读取；旧结论仍需原文核验。'
        '分幕是创作目标，可用于检查目标落实，但不能替正文证明事件已发生。')
    result['memory_guidance'] += ('本次独立审校以本次用户消息及所选正文为目标；旧用户要求用于理解历史意图，'
        '不自动成为本轮待修清单。复核修改效果才按需读取旧要求与对应版本，不能仅凭提出过操作认定已完成。'
        '引用时沿用来源章号及范围；前章片段不是本章正文，检索命中不是完整章节。')
    return result


def read_conversation_turn(backend,turn_id,offset=0):
    if not isinstance(turn_id,str) or not turn_id:
        raise ValueError('需要当前会话中的 turn_id')
    if type(offset) is not int or offset<0:
        raise ValueError('偏移必须是非负整数')
    messages=[]
    for event in backend.store.events(backend.session):
        data=event.get('data') or {}
        if data.get('turn_id')!=turn_id or event['kind'] not in ('user','assistant','delta'):
            continue
        role='user' if event['kind']=='user' else 'assistant'
        if messages and role=='assistant' and messages[-1]['role']==role:
            messages[-1]['text']+=str(data.get('text') or '')
        else: messages.append({'role':role,'text':str(data.get('text') or '')})
    if not messages: raise ValueError('该轮对话不属于当前会话，或没有可读取的内容')
    body=json.dumps(messages,ensure_ascii=False)
    excerpt=body[offset:offset+8000]
    return {'turn_id':turn_id,'kind':'conversation_not_story_evidence','text':excerpt,'offset':offset,
            'next_offset':offset+len(excerpt),'total_chars':len(body),
            'truncated':offset>0 or offset+len(excerpt)<len(body)}
