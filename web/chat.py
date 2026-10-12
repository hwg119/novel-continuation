"""Conversation entry point; allowlisted tools delegate to existing feature routes."""
import asyncio
import hashlib
import json
import threading
from pathlib import Path

from fastapi import Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from core.agent_skill import load_agent_skill
from web.chat_navigation import FEATURES, navigation_links
from core.project_manager import chapter_path, list_chapter_files, load_project_settings, settings_for_chapter, save_project_settings
from web.chat_store import ChatStore
from web.chat_context import enrich_chat_context, PLAN_PREMISE_GUIDANCE
from web.chat_plan_baseline import chat_plan_baseline
from web.chat_artifacts import ChatArtifacts


class ChatMessage(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    chapter: int | None = Field(default=None, ge=1)
    model_name: str = ''


class AuditRevisionRequest(BaseModel):
    model_name: str = ''
    enhancements: list[str] = Field(default_factory=list, max_length=100)


class ReplyPlanRequest(BaseModel):
    event_id: int = Field(ge=1)
    model_name: str = ''


def audit_revision_requirements(result, selected):
    main = str(result.get('revision_requirements') or '').strip()
    chosen = [row for row in result.get('story_enhancements') or [] if row.get('id') in selected]
    parts = ['【主线修订】\n' + main] if main else []
    if chosen:
        parts.append('【已选择的情节增强】\n' + '\n'.join(
            f"{i + 1}. {row.get('scope') or ''}：{row.get('suggestion') or ''}\n保留：{row.get('preserve') or ''}"
            for i, row in enumerate(chosen)))
    return '\n\n'.join(parts)


def register_chat(app, workspace, project_path, llm_config, tools, jobs, write_required, resume_tools=None):
    resume_tools = resume_tools or {}
    store = ChatStore(Path(workspace) / 'conversations.sqlite3')
    base = '/api/projects/{project_id}/chat'
    def objects(path, ident):
        return ChatArtifacts(store,jobs,path,ident)

    def record_result_focus(path, ident, action, data, job):
        if job.get('status')!='completed': return
        kind = {'plan':'plan','plan_revision':'plan','revise':'text','consistency':'audit'}.get(data['tool'])
        if not kind: return
        backend = objects(path,ident)
        try:
            row = backend.resolve(data['chapter'],action,kind)
        except ValueError:
            return
        metadata = {key:value for key,value in row.items() if key!='content'}
        parent = data.get('work_object') or {}
        expected = parent.get('id') or data.get('work_focus_before',{}).get('id') or ''
        if kind not in store.work_focus(ident,data['chapter']):
            # Recover old journals without letting the first historical result
            # become the focus when a newer result already exists.
            if backend.resolve(data['chapter'],kind=kind)['id'] != action: return
            expected = ''
        store.set_work_focus(ident,data['chapter'],kind,metadata,expected_id=expected)
    def plan_hash(path, number):
        settings = settings_for_chapter(load_project_settings(path), number)
        return hashlib.sha256(json.dumps({k:settings.get(k) for k in
            ('chapter_title','chapter_brief','chapter_requirements','beats')},
            ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def reconcile(path, ident):
        """Recover terminal job state even when the monitoring thread died on restart."""
        events = store.events(ident)
        completed = {e['data'].get('job_id') for e in events if e['kind'] == 'task_result'}
        for action in store.actions(ident):
            if not action['job'] or action['job'] in completed: continue
            job = jobs.get(path, action['job'])
            if not job or job['status'] == 'running': continue
            data = json.loads(action['data'])
            record_result_focus(path,ident,action['id'],data,job)
            store.emit(ident, 'task_result', result_data(action['id'], data, job))

    def result_data(action, data, job):
        result = job.get('result') or {}
        return {'action_id':action,'job_id':job['id'],'status':job['status'],
            'text':job['message'] + (' · 修订基准：' + data['plan_baseline']['label'] if data.get('plan_baseline') else ''),'chapter':data['chapter'],
            'plan_baseline':data.get('plan_baseline'),
            'work_object':data.get('work_object'),
            'view':{'plan':'settings','plan_revision':'settings','generate':'write',
                    'consistency':'quality','revise':'quality','illustration':'settings'}[data['tool']],
            'tool':data['tool'],'model':data.get('model'),'can_resume':job.get('can_resume',False),
            'elapsed_seconds':job.get('elapsed_seconds'), 'result':{k:result[k] for k in
                ('chapter_title','beats','planning_review','report','revision_requirements','candidate',
                 'story_summary','story_enhancements','issues') if k in result}}

    def session(project_id, ident):
        path = project_path(project_id)
        result = store.get(project_id, ident)
        if not result: raise HTTPException(404, '会话不存在或不属于当前工程')
        return path, result

    @app.get(base)
    def sessions(project_id: str):
        project_path(project_id)
        return store.sessions(project_id)

    @app.post(base, dependencies=[Depends(write_required)])
    def create(project_id: str, payload: dict):
        project_path(project_id)
        chapter = payload.get('chapter')
        if chapter is not None and (not isinstance(chapter, int) or chapter < 1):
            raise HTTPException(400, '章号无效')
        return {'id': store.create(project_id, chapter)}

    @app.get(base + '/{ident}')
    def history(project_id: str, ident: str):
        path, info = session(project_id, ident)
        reconcile(path, ident)
        return {**info, 'events': store.events(ident),
                'work_focus':store.work_focus(ident,info['chapter']) if info.get('chapter') else {}}

    @app.patch(base + '/{ident}', dependencies=[Depends(write_required)])
    def rename_session(project_id: str, ident: str, payload: dict):
        session(project_id, ident)
        title = payload.get('title')
        if not isinstance(title, str): raise HTTPException(400, '请输入对话名称')
        try: store.rename(project_id, ident, title)
        except ValueError as exc: raise HTTPException(400, str(exc))
        return {'renamed': True}

    @app.delete(base + '/{ident}', dependencies=[Depends(write_required)])
    def delete_session(project_id: str, ident: str):
        session(project_id, ident)
        try: store.delete(project_id, ident)
        except ValueError as exc: raise HTTPException(409, str(exc))
        return {'deleted': True}

    @app.post(base + '/{ident}/messages', dependencies=[Depends(write_required)])
    def send(project_id: str, ident: str, payload: ChatMessage):
        path, info = session(project_id, ident)
        if not payload.text.strip(): raise HTTPException(400, '请输入消息')
        config = llm_config(payload.model_name)
        chapter = payload.chapter or info['chapter']
        try: turn = store.begin(ident, payload.text.strip(), chapter)
        except ValueError as exc: raise HTTPException(409, str(exc))

        def work():
            try:
                store.emit(ident, 'progress', {'turn_id': turn, 'text': '正在理解你的请求',
                                              'model': config.get('model_name'), 'chapter': chapter})
                events = store.events(ident)
                history_text = []
                for event in events:
                    if event['kind'] == 'delta':
                        if history_text and history_text[-1]['role'] == 'assistant' and history_text[-1]['data'].get('turn_id') == event['data']['turn_id']:
                            history_text[-1]['data']['text'] += event['data']['text']
                        else: history_text.append({'role':'assistant','data':dict(event['data'])})
                    elif event['kind'] in ('user', 'assistant', 'action', 'task_result'):
                        history_text.append({'role': event['kind'], 'data': event['data']})
                settings = load_project_settings(path)
                backend = objects(path,ident)
                files = list_chapter_files(path)
                context = {'current_chapter': chapter,
                    'chapters': [{'number': n, 'file': name} for n, name, _ in files[-15:]],
                    'plan': settings_for_chapter(settings, chapter) if chapter else {},
                    'conversation': history_text[-18:]}
                previous_context = enrich_chat_context(context, path, chapter)
                if chapter:
                    context['plan'], context['plan_baseline'] = chat_plan_baseline(
                        store, jobs, path, ident, chapter, context['plan'])
                    context.update(backend.context(chapter))
                    context['source_states']['plan'] = context['plan_baseline']['kind'] + '_plan_not_occurred'
                executed_turns = {json.loads(action['data']).get('turn_id') for action in store.actions(ident)}
                context['recent_user_requests'] = [{**event['data'], 'already_proposed':event['data'].get('turn_id') in executed_turns}
                    for event in events if event['kind'] == 'user'][-12:]
                store.emit(ident, 'progress', {'turn_id':turn,
                    'text':'已区分正文、分幕与讨论建议的资料状态',
                    'details':{'previous_chapter':previous_context, 'history_is_not_canon':True}})
                if chapter:
                    source = Path(chapter_path(path, chapter))
                    if source.is_file():
                        text = source.read_text(encoding='utf-8')
                        context['chapter_excerpt'] = text[:10000]
                        context['excerpt_truncated'] = len(text) > 10000
                # Never include global settings / credentials in model context.
                plan = context['plan']
                context['plan'] = {k: plan.get(k) for k in ('chapter_title','chapter_brief','beats')}
                guide, skill_metadata = load_agent_skill('project-guide')
                store.emit(ident, 'progress', {'turn_id': turn, 'text': '已加载项目功能导航 Skill', 'details': skill_metadata})
                context['project_features'] = FEATURES
                from web.deepagents_trial import run_trial
                decision = run_trial(config,context,payload.text,guide,backend,
                    lambda text,details:store.emit(ident,'progress',{'turn_id':turn,'text':text,'details':details}),
                    lambda:store.running(turn))
                if not isinstance(decision,dict): raise ValueError('对话引擎未返回有效结果')
                if not store.running(turn): return
                tool = decision.get('tool', 'reply')
                number = decision.get('chapter')
                if tool not in {'reply', 'navigate', 'view', *tools}: raise ValueError('模型提出了不支持的操作')
                links = navigation_links(decision.get('navigation'), number or chapter)
                if tool not in ('reply', 'navigate') and (isinstance(number, bool) or not isinstance(number, int) or number < 1):
                    tool = 'reply'
                    decision['message'] = '请明确要处理的章节编号。'
                if tool == 'navigate':
                    if not links: raise ValueError('未找到对应功能入口，请说明要打开的功能')
                    store.emit(ident, 'assistant', {'turn_id': turn, 'text': str(decision.get('message') or '可从下面入口打开对应功能。')})
                elif tool == 'view':
                    if not Path(chapter_path(path, number)).is_file(): raise ValueError('该章正文尚不存在')
                    store.emit(ident, 'assistant', {'turn_id': turn, 'text': f'可以打开第 {number} 章正文。'})
                    store.emit(ident, 'link', {'chapter': number, 'view': 'write', 'label': '查看正文'})
                elif tool in tools:
                    requirements = str(decision.get('requirements') or '').strip()[:5000]
                    if tool in ('revise','plan_revision') and not requirements:
                        raise ValueError('请说明需要修改什么')
                    if tool in ('plan','plan_revision','revise'):
                        # The agent resolves conversational intent. Raw turns are
                        # provenance, not executable writing requirements.
                        requirements = PLAN_PREMISE_GUIDANCE + '\n【用户本次要求】\n' + requirements
                    baseline_data = {}
                    if tool in ('plan_revision','revise'):
                        target = decision.get('target_artifact_id') or decision.get('plan_version_id')
                        baseline_data = backend.bind_operation(tool,number,target,
                            decision.get('artifact_selection_reason') or decision.get('plan_selection_reason') or '')
                        store.emit(ident,'progress',{'turn_id':turn,'text':'已确定工作对象', 'details':baseline_data['work_object']})
                    store.action(ident, {**baseline_data, 'tool': tool, 'chapter': number,
                        'requirements': requirements, 'model_name': payload.model_name,
                        'original_user_request': payload.text.strip(),
                        'source_request_turn':decision.get('source_request_turn'),
                        'model_interpretation': str(decision.get('requirements') or '')[:5000],
                        'plan_before': plan_hash(path, number),
                        'work_focus_before':store.work_focus(ident,number).get('plan' if tool=='plan' else 'audit',{}),
                        'model': config.get('model_name'),
                        'message': ('按你的原始要求重新构建回目和分幕，生成候选后由你保存。'
                                    if tool == 'plan' else '按你的要求修订；基准：' + baseline_data['plan_baseline']['label']
                                    if tool == 'plan_revision' else '按你的要求修订正文；基准：' + baseline_data['work_object']['title']
                                    if tool == 'revise' else str(decision.get('message') or '确认后执行')),
                        'turn_id': turn})
                else:
                    if isinstance(number, int) and not isinstance(number, bool) and number > 0 and number != chapter:
                        context['current_chapter'] = number
                        source = Path(chapter_path(path, number))
                        text = source.read_text(encoding='utf-8') if source.is_file() else ''
                        context['chapter_excerpt'] = text[:10000]
                        context['excerpt_truncated'] = len(text) > 10000
                        target_plan = settings_for_chapter(settings, number)
                        target_plan, context['plan_baseline'] = chat_plan_baseline(
                            store, jobs, path, ident, number, target_plan)
                        context['plan'] = {k:target_plan.get(k) for k in ('chapter_title','chapter_brief','beats')}
                        enrich_chat_context(context, path, number)
                        context['source_states']['plan'] = context['plan_baseline']['kind'] + '_plan_not_occurred'
                        context.update(backend.context(number))
                    target = decision.get('target_artifact_id') or decision.get('plan_version_id')
                    if target:
                        selected = backend.resolve(number or chapter,target)
                        origin = backend.select(selected,decision.get('artifact_selection_reason') or decision.get('plan_selection_reason') or '')
                        content = selected['content']
                        if selected['kind']=='text': content={'text':content['text'][:10000],'truncated':len(content['text'])>10000}
                        context['selected_work_object'] = {**origin,'content':content}
                        if selected['kind']=='plan':
                            context['plan'] = selected['content']
                        store.emit(ident,'progress',{'turn_id':turn,'text':'已确定讨论对象','details':origin})
                    store.emit(ident, 'progress', {'turn_id': turn, 'text': '正在组织回复'})
                    def chunk(text):
                        if not store.running(turn): raise InterruptedError('已停止回复')
                        store.emit(ident, 'delta', {'turn_id': turn, 'text': text})
                    response = str(decision.get('_reply_text') or decision.get('message') or '')
                    chunk(response)
                    if not response.strip(): raise ValueError('模型返回了空回复')
                if tool in ('reply', 'navigate'):
                    for target in links:
                        store.emit(ident, 'link', {'turn_id': turn,
                            'target_artifact_id':decision.get('target_artifact_id') or decision.get('plan_version_id'), **target})
                store.finish(ident, turn, 'done')
            except InterruptedError: pass
            except Exception as exc:
                store.finish(ident, turn, 'failed', str(exc).replace(str(config.get('api_key') or '\0'), '[已隐藏]'))
        threading.Thread(target=work, daemon=True).start()
        return {'turn_id': turn}

    @app.post(base + '/{ident}/turns/{turn}/stop', dependencies=[Depends(write_required)])
    def stop(project_id: str, ident: str, turn: str):
        session(project_id, ident)
        store.finish(ident, turn, 'cancelled', '已停止对话回复；已启动的后台任务不受影响。')
        return {'stopped': True}

    @app.post(base + '/{ident}/actions/{action}/save-plan', dependencies=[Depends(write_required)])
    def save_plan(project_id: str, ident: str, action: str):
        path, _ = session(project_id, ident)
        item = store.get_action(ident, action)
        if not item or item['data']['tool'] not in ('plan','plan_revision'):
            raise HTTPException(400, '该操作不是分幕生成')
        data = item['data']
        job = jobs.get(path, item['job']) if item['job'] else None
        if not job or job['status'] != 'completed' or not (job.get('result') or {}).get('beats'):
            raise HTTPException(400, '分幕尚未生成完成')
        if data['plan_before'] != plan_hash(path, data['chapter']):
            raise HTTPException(409, '当前分幕已变化，不能覆盖；请打开续写与分幕核对')
        settings = load_project_settings(path)
        from core.plan_inputs import saved_plan_input
        plan = saved_plan_input(settings_for_chapter(settings,data['chapter']), data)
        plan.update({k:job['result'].get(k) for k in ('chapter_title','beats','planning_review')})
        settings.setdefault('chapter_plans',{})[str(data['chapter'])] = plan
        if int(settings.get('chapter_number') or 0) == data['chapter']: settings.update(plan)
        if not save_project_settings(path,settings): raise HTTPException(500,'规划保存失败')
        store.emit(ident,'plan_saved',{'action_id':action,'text':f"第 {data['chapter']} 章分幕已保存"})
        return {'saved': True}

    @app.post(base + '/{ident}/actions/{action}/apply-revision', dependencies=[Depends(write_required)])
    def apply_chat_revision(project_id: str, ident: str, action: str, payload: dict):
        session(project_id,ident)
        item = store.get_action(ident,action)
        if not item or item['data'].get('tool') != 'revise' or not item['job']: raise HTTPException(400,'该卡片不是已生成的正文修订稿')
        revision = payload.get('revision')
        if not isinstance(revision,str): raise HTTPException(400,'请先查看差异')
        result = app.state.apply_chat_revision(project_id,item['job'],revision,confirm_warnings=payload.get('confirm_warnings') is True)
        backend = objects(project_path(project_id),ident)
        backend.select(backend.resolve(item['data']['chapter'],f"saved:text:{item['data']['chapter']}",'text'),
                       '修订稿已应用，继续工作以当前已保存正文为准')
        store.emit(ident,'revision_applied',{'action_id':action,'job_id':item['job'],'chapter':item['data']['chapter'],
                                         'text':'修订稿已应用并保存正文'})
        return result

    @app.post(base + '/{ident}/actions/{action}/cancel', dependencies=[Depends(write_required)])
    def cancel_action(project_id: str, ident: str, action: str):
        path, _ = session(project_id,ident)
        item = store.get_action(ident,action)
        if not item or not item['job']: raise HTTPException(400,'任务尚未启动')
        try: return jobs.cancel(path,item['job'])
        except ValueError as exc: raise HTTPException(409,str(exc))

    @app.post(base + '/{ident}/actions/{action}/resume', dependencies=[Depends(write_required)])
    def resume_action(project_id: str, ident: str, action: str):
        path, _ = session(project_id,ident)
        item = store.get_action(ident,action)
        if not item or not item['job']: raise HTTPException(400,'任务不存在')
        job = jobs.get(path,item['job'])
        tool = item['data']['tool']
        if not job or not job.get('can_resume') or tool not in resume_tools: raise HTTPException(400,'该任务没有可恢复的检查点')
        data = {**item['data'],'resume_job':item['job'],'plan_before':plan_hash(path,item['data']['chapter']),
                'message':'从原模型的检查点继续，候选稿不会自动应用。'}
        next_action = store.action(ident,data,dedupe_key='resume:'+action)
        return confirm(project_id,ident,next_action)

    @app.post(base + '/{ident}/revise-plan-from-reply', dependencies=[Depends(write_required)])
    def revise_plan_from_reply(project_id: str, ident: str, payload: ReplyPlanRequest):
        path, _ = session(project_id, ident)
        events = store.events(ident)
        link = next((row for row in events if row['id'] == payload.event_id), None)
        if not link or link['kind'] != 'link' or link['data'].get('view') != 'settings':
            raise HTTPException(400, '未找到这条分幕建议入口')
        number = link['data'].get('chapter')
        if type(number) is not int or number < 1: raise HTTPException(400, '请明确章节编号')
        turn = link['data'].get('turn_id')
        reply = ''.join(str(row['data'].get('text') or '') for row in events
                        if row['data'].get('turn_id') == turn and row['kind'] in ('delta', 'assistant'))
        if not turn or not reply.strip(): raise HTTPException(400, '该入口没有关联的修改建议')
        config = llm_config(payload.model_name)
        plan = settings_for_chapter(load_project_settings(path), number)
        try:
            plan, origin = chat_plan_baseline(store, jobs, path, ident, number, plan,
                link['data'].get('target_artifact_id') or link['data'].get('plan_version_id'))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        tool = 'plan_revision' if plan.get('beats') else 'plan'
        if tool not in tools: raise HTTPException(400, '当前服务不支持此分幕操作')
        data = {'tool':tool,'chapter':number,'model_name':payload.model_name,
            'model':config.get('model_name'), 'plan_before':plan_hash(path, number),
            'requirements':PLAN_PREMISE_GUIDANCE + '\n根据以下讨论生成或修改本章分幕。若建议包含备选方案，优先采用回复明确推荐的组合，不将全部备选同时叠加；忽略页面跳转说明。\n' + reply,
            'message':'在对话中按这条建议处理分幕，生成候选稿后由用户保存。', 'source_reply':turn}
        if tool == 'plan_revision':
            data.update(objects(path,ident).bind_operation(tool,number,origin['version_id'],'使用关联回复选定的工作对象'))
        action = store.action(ident, data, dedupe_key=f'plan-reply:{payload.event_id}:{payload.model_name}')
        return confirm(project_id, ident, action)

    @app.post(base + '/{ident}/actions/{action}/audit-chapter', dependencies=[Depends(write_required)])
    def audit_chat_chapter(project_id: str, ident: str, action: str, payload: AuditRevisionRequest):
        path, _ = session(project_id, ident)
        item = store.get_action(ident, action)
        if not item or item['data'].get('tool') not in ('generate', 'revise'):
            raise HTTPException(400, '该卡片不是正文生成或修订结果')
        job = jobs.get(path, item['job']) if item['job'] else None
        if not job or job['status'] != 'completed':
            raise HTTPException(400, '正文任务尚未完成')
        number = item['data']['chapter']
        source = Path(chapter_path(path, number))
        if not source.is_file(): raise HTTPException(400, '该章正文尚不存在')
        if item['data']['tool'] == 'revise':
            from core.chapter_revision import chapter_body
            current = source.read_text(encoding='utf-8')
            if chapter_body(current, current.splitlines()[0]).strip() != str((job.get('result') or {}).get('candidate') or '').strip():
                raise HTTPException(400, '请先在对话中比较并应用修订稿，再审校已保存正文')
        config = llm_config(payload.model_name)
        next_action = store.action(ident, {'tool':'consistency','chapter':number,
            'requirements':'','model_name':payload.model_name,'model':config.get('model_name'),
            'message':'在当前对话中审校已保存正文，结果与后续修订均在这里处理。',
            'work_focus_before':store.work_focus(ident,number).get('audit',{})})
        return confirm(project_id, ident, next_action)

    @app.post(base + '/{ident}/actions/{action}/revise-audit', dependencies=[Depends(write_required)])
    def revise_audit(project_id: str, ident: str, action: str, payload: AuditRevisionRequest):
        path, _ = session(project_id, ident)
        item = store.get_action(ident, action)
        if not item or item['data'].get('tool') != 'consistency': raise HTTPException(400, '该卡片不是故事审校结果')
        job = jobs.get(path, item['job']) if item['job'] else None
        if not job or job['status'] != 'completed': raise HTTPException(400, '审校尚未完成')
        result = job.get('result') or {}
        valid = {row.get('id') for row in result.get('story_enhancements') or []}
        if any(key not in valid for key in payload.enhancements): raise HTTPException(400, '所选增强意见不属于该审校记录')
        requirements = audit_revision_requirements(result, payload.enhancements)
        if not requirements: raise HTTPException(400, '没有主线修订要求，请先勾选增强意见')
        config = llm_config(payload.model_name)
        number = item['data']['chapter']
        data = {'tool':'revise','chapter':number,'requirements':requirements,
                'model_name':payload.model_name,'model':config.get('model_name'),
                'message':'按本次审校的主线要求及所选增强生成修订稿，不自动应用正文。',
                'audit_job':job['id'],'enhancements':sorted(set(payload.enhancements))}
        try:
            data.update(objects(path,ident).bind_operation('revise',number,reason='从审校卡片继续修订当前正文工作对象'))
        except ValueError as exc:
            raise HTTPException(400,str(exc)) from None
        key = hashlib.sha256(json.dumps({'audit':action,'model':payload.model_name,'selected':data['enhancements']},sort_keys=True).encode()).hexdigest()
        ident_action = store.action(ident, data, dedupe_key=key)
        return confirm(project_id, ident, ident_action)

    @app.post(base + '/{ident}/actions/{action}/confirm', dependencies=[Depends(write_required)])
    def confirm(project_id: str, ident: str, action: str):
        path, _ = session(project_id, ident)
        try: data = store.claim(ident, action)
        except ValueError as exc: raise HTTPException(409, str(exc))
        try:
            # Resolve the confirmed config name; never take arbitrary URLs or tool names from the browser.
            config = llm_config(data['model_name'])
            if config.get('model_name') != data['model']:
                raise ValueError('模型配置已改变，请重新发送请求并确认')
            result = resume_tools[data['tool']](project_id,data['resume_job'],data) if data.get('resume_job') else tools[data['tool']](project_id, data)
            store.action_status(ident, action, 'started', result['id'])
        except Exception:
            store.action_status(ident, action, 'failed')
            raise

        def watch():
            try:
                previous = []
                previous_status = None
                while True:
                    job = jobs.get(path, result['id'])
                    if not job: raise ValueError('后台任务记录不存在')
                    current = job.get('events', [])
                    overlap = 0
                    for count in range(min(len(previous),len(current)),0,-1):
                        if previous[-count:] == current[:count]:
                            overlap = count; break
                    for event in current[overlap:]:
                        store.emit(ident, 'task_progress', {'action_id': action, 'job_id': result['id'], **event})
                    previous = current
                    status = {key:job.get(key) for key in ('status','stage','cancel_requested','can_cancel','elapsed_seconds','can_resume')}
                    if status != previous_status:
                        store.emit(ident,'task_status',{'action_id':action,'job_id':result['id'],**status})
                        previous_status = status
                    if job['status'] != 'running':
                        record_result_focus(path,ident,action,data,job)
                        store.emit(ident, 'task_result', result_data(action,data,job))
                        break
                    threading.Event().wait(0.7)
            except Exception as exc:
                store.emit(ident, 'task_result', {'action_id': action, 'job_id': result['id'],
                                                'status': 'failed', 'text': str(exc)})
        threading.Thread(target=watch, daemon=True).start()
        return result

    @app.get(base + '/{ident}/events')
    async def stream(project_id: str, ident: str, request: Request, after: int = 0):
        session(project_id, ident)
        try: cursor = max(after, int(request.headers.get('Last-Event-ID') or 0))
        except ValueError: raise HTTPException(400, '事件序号无效')
        async def events():
            nonlocal cursor
            def stopping():
                signal = getattr(app.state, 'shutdown_requested', None)
                return signal is not None and signal.is_set()
            while not stopping() and not await request.is_disconnected():
                rows = await asyncio.to_thread(store.events, ident, cursor)
                if stopping(): break
                for row in rows:
                    cursor = row['id']
                    yield f'id: {cursor}\ndata: {json.dumps(row, ensure_ascii=False)}\n\n'
                if not rows: yield ': heartbeat\n\n'
                await asyncio.sleep(0.7)
        return StreamingResponse(events(), media_type='text/event-stream',
                                 headers={'Cache-Control':'no-cache', 'X-Accel-Buffering':'no'})
