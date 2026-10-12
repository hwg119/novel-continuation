"""Conversation-scoped work products, independent of feature-specific routing.

Design references (not copied implementation): Open Canvas's artifact/current
version split and Deep Agents' backend/state/tool separation. Existing files and
jobs remain the source of truth; only work-focus pointers live in ChatStore.
"""
import copy
import hashlib
import json
from pathlib import Path

from core.project_manager import chapter_path, load_project_settings, settings_for_chapter


ARTIFACT_GUIDANCE = '''工作对象统一在 artifacts 中提供：id 标识版本，kind 区分 plan（分幕）、text（正文）、audit（审校）、illustration（插图）。标题可重复；时间、来源、状态、preview 和 work_focus 帮助理解指代。候选不代表已保存，也不代表历史已发生。
结合本次要求及对话自行选 target_artifact_id，简述 artifact_selection_reason。不按同名数量机械追问；简称、最后一版、继续改可结合对话与当前工作对象理解。没有指定时优先延续相应类型当前工作对象，否则选择最新可用候选，再退回已保存内容。真正无法判断且不同选择会实质影响结果时才追问。
分幕修订目标须 kind=plan，正文修订须 kind=text。讨论指定版本也选 ID，程序将读取该对象而不是其他稿。重新构建不沿用旧稿。需要更多内容时用 list_artifacts、read_artifact 工具，不凭聊天里旧描述冒充当前内容。
承接先前讨论、要求或澄清时，自主结合上下文整理具体 requirements，不能只转抄当前短句。source_request_turn 可标明来源请求 ID，仅作追溯，程序不拼接聊天原文替代操作要求。用户未授权新操作时不要重复执行。'''


def fingerprint(content):
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class ChatArtifacts:
    """Small backend adapter over the project's existing durable data."""
    def __init__(self, store, jobs, path, session):
        self.store, self.jobs, self.path, self.session = store, jobs, path, session

    def list(self, chapter, kind=None):
        if type(chapter) is not int or chapter < 1:
            raise ValueError('工作对象章号无效')
        saved = settings_for_chapter(load_project_settings(self.path), chapter)
        source = Path(chapter_path(self.path, chapter))
        saved_text = source.read_text(encoding='utf-8') if source.is_file() else ''
        from core.chapter_revision import chapter_body
        title = saved_text.splitlines()[0] if saved_text else str(saved.get('chapter_title') or '')
        saved_body = chapter_body(saved_text, title) if saved_text else ''
        rows = []
        applied = {e['data'].get('action_id') for e in self.store.events(self.session)
                   if e['kind'] in ('plan_saved', 'revision_applied')}
        for action in reversed(self.store.actions(self.session)):
            data = json.loads(action['data'])
            if data.get('chapter') != chapter or not action.get('job'):
                continue
            job = self.jobs.get(self.path, action['job'])
            if not job or job.get('status') != 'completed':
                continue
            result = job.get('result') or {}
            tool = data.get('tool')
            if tool in ('plan', 'plan_revision') and result.get('beats'):
                content = {key: copy.deepcopy(result.get(key)) for key in ('chapter_title','beats')}
                object_kind, object_title = 'plan', content['chapter_title']
            elif tool == 'revise' and isinstance(result.get('candidate'), str) and result['candidate'].strip():
                content = {'text':result['candidate'], 'original_source':result.get('original_source',result.get('source',''))}
                object_kind, object_title = 'text', result.get('title') or title
            elif tool == 'consistency':
                content = {key:copy.deepcopy(result[key]) for key in
                           ('story_summary','revision_requirements','story_enhancements','issues','report') if key in result}
                if not content: continue
                object_kind, object_title = 'audit', '故事审校'
            else:
                continue
            parent = data.get('work_object') or data.get('plan_baseline') or result.get('baseline') or {}
            row = self._row(action['id'], object_kind, chapter, object_title, content,
                            'applied' if action['id'] in applied else 'candidate',
                            job_id=job['id'], created_at=job.get('created_at'),
                            derived_from=parent.get('id') or parent.get('version_id') or parent.get('action_id'),
                            review_status=(result.get('planning_review') or {}).get('status'))
            if object_kind == 'text':
                row['compatible_with_saved'] = str(content['original_source']).strip() == saved_body.strip()
            rows.append(row)
        if saved.get('beats'):
            rows.append(self._row(f'saved:plan:{chapter}', 'plan', chapter, saved.get('chapter_title'),
                {key:copy.deepcopy(saved.get(key)) for key in ('chapter_title','beats')}, 'saved'))
        if saved_text:
            rows.append(self._row(f'saved:text:{chapter}', 'text', chapter, title,
                                 {'text':saved_body,'original_source':saved_body}, 'saved', compatible_with_saved=True))
        svg = Path(self.path) / 'illustrations' / f'chapter_{chapter}.svg'
        if svg.is_file():
            rows.append(self._row(f'saved:illustration:{chapter}', 'illustration', chapter, '章节插图',
                                 {'svg':svg.read_text(encoding='utf-8')}, 'saved'))
        return [row for row in rows if kind is None or row['kind'] == kind]

    @staticmethod
    def _row(ident, kind, chapter, title, content, status, **metadata):
        return {'id':ident,'kind':kind,'chapter':chapter,'title':title or '', 'status':status,
                'hash':fingerprint(content),'content':content, **metadata}

    @staticmethod
    def catalog(rows):
        result = []
        for row in rows:
            metadata = {key:value for key,value in row.items() if key != 'content'}
            content = row['content']
            if row['kind']=='plan':
                metadata['beat_names'] = [beat.get('name') for beat in content.get('beats') or []]
                metadata['preview'] = [str(beat.get('desc') or '')[:160] for beat in content.get('beats') or []]
            else:
                metadata['preview'] = str(content.get('text') or content.get('story_summary') or content.get('svg') or json.dumps(content,ensure_ascii=False))[:800]
            result.append(metadata)
        return result

    def resolve(self, chapter, ident=None, kind=None):
        rows = self.list(chapter, kind)
        if ident:
            # Read-only compatibility for old planning links/actions.
            if ident == f'saved:{chapter}': ident = f'saved:plan:{chapter}'
            row = next((row for row in rows if row['id']==ident), None)
            if row is None: raise ValueError('工作对象不存在或不属于当前对话、章节和类型')
        else:
            focus = self.store.work_focus(self.session, chapter).get(kind, {}) if kind else {}
            row = next((row for row in rows if row['id']==focus.get('id') and row.get('compatible_with_saved',True)), None)
            row = row or next((row for row in rows if row.get('compatible_with_saved',True)), None)
            if row is None: raise ValueError('当前章节没有可用的工作对象')
        return copy.deepcopy(row)

    def select(self, row, reason=''):
        metadata = {key:value for key,value in row.items() if key!='content'}
        metadata['selection_reason'] = str(reason)[:500]
        self.store.set_work_focus(self.session, row['chapter'], row['kind'], metadata)
        return metadata

    def context(self, chapter):
        rows = self.list(chapter)
        focus = self.store.work_focus(self.session,chapter)
        active = {}
        for kind, metadata in focus.items():
            row = next((row for row in rows if row['id']==metadata.get('id') and row['kind']==kind
                        and row.get('compatible_with_saved',True)), None)
            if row is None: continue
            content = copy.deepcopy(row['content'])
            if kind=='text':
                content = {'text':content['text'][:10000], 'truncated':len(content['text'])>10000}
            active[kind] = {**metadata, **{key:value for key,value in row.items() if key!='content'},
                            'content':content}
        current = max(active.values(), key=lambda row:row.get('focused_at',0), default=None)
        return {'artifacts':self.catalog(rows),'work_focus':focus,'active_work_objects':active,
                'current_work_object':{key:value for key,value in current.items() if key!='content'} if current else None}

    def bind_operation(self, tool, chapter, ident=None, reason=''):
        expected_kind = {'plan_revision':'plan', 'revise':'text'}[tool]
        row = self.resolve(chapter,ident,expected_kind)
        metadata = {key:value for key,value in row.items() if key!='content'}
        metadata['selection_reason'] = str(reason)[:500]
        snapshot = {'work_object':metadata}
        if tool=='plan_revision':
            from web.chat_plan_baseline import planning_origin, revision_baseline_data
            snapshot.update(revision_baseline_data(row['content'],planning_origin(row)))
        else:
            if not row.get('compatible_with_saved',False):
                raise ValueError('该正文候选对应的已保存正文已变化，请选择当前正文或核对后再修订')
            snapshot['revision_source'] = copy.deepcopy(row['content'])
        self.select(row,reason)
        return snapshot

    def read(self, call):
        chapter, ident = call.get('chapter'), call.get('artifact_id')
        if call.get('tool')=='list_artifacts':
            return [{'chapter':chapter,'kind':'artifact_catalog',
                     'text':json.dumps(self.catalog(self.list(chapter,call.get('kind'))),ensure_ascii=False)}]
        if not isinstance(ident,str) or not ident: raise ValueError('请给出工作对象 ID')
        row = self.resolve(chapter, ident)
        offset = call.get('offset',0)
        if type(offset) is not int or offset < 0: raise ValueError('偏移必须是非负整数')
        body = json.dumps(row['content'],ensure_ascii=False) if row['kind']!='text' else row['content']['text']
        text = body[offset:offset+16000]
        return [{'chapter':chapter,'kind':'artifact_'+row['kind'],'artifact_id':row['id'],
                 'source_state':{'plan':'creative_target_not_body_evidence',
                                 'audit':'historical_assessment_not_evidence',
                                 'text':'saved_body' if row['status']=='applied' else 'candidate_body'}.get(row['kind'],'creative_artifact'),
                 'status':row['status'],'hash':row['hash'],'text':text,'offset':offset,
                 'next_offset':offset+len(text),'total_chars':len(body),
                 'truncated':offset>0 or offset+len(text)<len(body)}]
