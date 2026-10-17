"""Read-only architecture and bounded, redacted run inspection. No model calls."""
import ast
import hashlib
import json
import re
from pathlib import Path

from fastapi import Header, HTTPException, Query

ROOT = Path(__file__).resolve().parents[1]


def source(path, symbol=None):
    return {'id': hashlib.sha256((path + ':' + (symbol or '')).encode()).hexdigest()[:16],
            'path': path, 'symbol': symbol}


def node(ident, label, layer, inputs, outputs, boundary, code, prompts=(), input_details=()):
    return {'id': ident, 'label': label, 'layer': layer, 'inputs': inputs,
            'outputs': outputs, 'boundary': boundary,
            'code': [source(*item) if isinstance(item, tuple) else source(item) for item in code],
            'prompts': [source(*item) if isinstance(item, tuple) else source(item) for item in prompts],
            'input_details': list(input_details)}


NODES = [
    node('context', '上下文与版本', '入口', '当前章、用户目标、正文与前章、作品目录',
         '压缩且标注用途的对话输入', '正文是事件依据；分幕是目标；旧审校只保留索引。',
         [('web/chat_context.py', 'enrich_chat_context'), ('web/chat_memory.py', 'compact_dialogue_context'),
          ('web/chat_artifacts.py', 'context')], input_details=(
             ('当前对话', 'conversations.sqlite3', '用户最近消息、当前候选分幕引用、最近操作状态；旧助手回复仅保留索引。'),
             ('章节正文', 'chapters/chapter_{n}.txt', '当前章全文、章节号、保存时间与内容哈希；正文才是已发生事件的依据。'),
             ('前章材料', '上一已保存章节与项目摘要', '上一章尾段、可用摘要、章节号和截断范围；不是把整部小说重复送入。'),
             ('候选与历史', 'project.json / 对话产物', '候选分幕或审校的 ID、状态、标题；历史审校不是当前事实证据。'))),
    node('agent', '对话智能体', '入口', '标注上下文、用户消息与模型配置',
         '查阅、Skill 加载、自然语言回复或操作提案', '讨论不自动执行；工具可自主选择。',
         [('web/deepagents_trial.py', 'run_trial'), ('tools/deepagents/worker.py', 'run')],
         [('tools/deepagents/worker.py', 'build_agent'), 'web/chat_skills.py'], (
             ('会话请求', '聊天输入框与当前工程选择', '用户本轮文字、当前章、工程 ID、选定模型；不会把页面上的未提交表单作为事实。'),
             ('标注上下文', '上下文与版本节点', '有来源标识的章节片段、候选版本元数据、近期请求以及可按需读取的索引。'),
             ('可用能力', 'agent_skills 与受限工具注册', 'Skill 名称、版本、路径；读取对话、候选或证据的工具声明，不直接写正文。'),
             ('模型设置', '项目配置', '模型名称、兼容接口、超时和思考开关；密钥不进入提示词或运行回放。'))),
    node('proposal', '操作提案', '入口', '操作类型、章号、目标作品 ID、完整要求',
         '经验证的业务请求', '模型判断意图；程序检查对象、范围与状态。',
         ['web/chat.py', ('web/chat_artifacts.py', 'bind_operation'), 'web/deepagents_trial.py'], (), (
             ('模型提案', '对话智能体的结构化工具调用', '功能类型、目标章、要求文本、可选基准候选 ID；自然语言回复本身不会触发。'),
             ('工程状态', 'project.json 与聊天产物索引', '章节是否存在、候选是否属于当前工程、保存状态和可应用条件。'),
             ('用户确认', '对话卡片或功能页面操作', '明确点击的执行动作；普通讨论没有此项。'))),
    node('api', '功能 API', '执行', '对话调度或页面按钮请求', '业务输入与后台任务',
         '两种入口复用功能服务；查看与导航不调用生成模型。', [('web/features.py', 'register_features')]),
    node('jobs', '后台任务', '执行', '业务工作函数、工程与任务 ID', '进度、失败、取消及完成结果',
         '取消与检查点恢复能力依任务类型而异。', ['web/jobs.py']),
    node('plan', '分幕规划', '功能', '规则、上下文、用户目标或指定基准分幕',
         '分幕候选、审查与验收结果', '新建与修订不同；未保存候选不等于正文历史。',
         ['core/plan_inputs.py', ('core/planning_workflow.py', 'run_planning_workflow'),
          ('core/continuation.py', 'suggest_chapter_plan'), ('core/continuation.py', 'revise_chapter_plan')],
         ['agent_skills/chapter-planning/SKILL.md', 'agent_skills/chapter-planning/references/draft.md',
          'agent_skills/chapter-planning/references/review.md', 'agent_skills/chapter-planning/references/repair.md',
          'agent_skills/chapter-planning/references/acceptance.md', 'agent_skills/chapter-planning/references/user-acceptance.md',
          ('core/continuation.py', '_request_plan_json')], (
             ('创作目标', '用户要求或对话操作提案', '章号、标题/走向、指定修改项以及是否以某份候选分幕为基准。'),
             ('连续性证据', '上下文与版本节点', '前章尾段、当前已保存正文、摘要和检索出的 Wiki 片段；均附来源与截断标记。'),
             ('规划规则', 'chapter-planning Skill 与全书规则', '结构、字数、人物边界、验收规则；Skill 不是用户事实。'),
             ('候选基准', '当前会话候选或已保存分幕', '分幕标题、幕列表、版本 ID、保存状态；修改时优先使用明确指定版本。'))),
    node('write', '章节续写', '功能', '分幕、已写段落、文风、规则与历史证据',
         '逐幕正文、检查记录与章节文件', '后幕会承接已写内容；不能只看单幕 Skill 还原实际提示词。',
         [('core/continuation.py', 'generate_chapter')],
         ['agent_skills/chapter-writing/SKILL.md', ('core/continuation.py', 'build_beat_prompt')], (
             ('分幕计划', '当前候选或已保存分幕', '幕标题、视角、时间地点、目标、限制和承接信息。'),
             ('已写正文', '本轮逐幕生成缓存', '前一幕已生成文字、当前章节累计文本与字数；用于后幕自然承接。'),
             ('作品依据', '章节、摘要、Wiki 与全书规则', '必要前史、人物设定、引用证据和文风约束；按幕挑选，不是整库拼接。'),
             ('写作规范', 'chapter-writing Skill', '叙事视角、节奏、避免重复和输出格式要求。'))),
    node('audit', '故事审校', '功能', '本章正文、前章、摘要、Wiki 与检索证据',
         '主线修订要求、可选增强与待确认项',
         '按钮走结构化审校；对话直接评估走智能体。引文定位不等于语义正确。',
         [('core/consistency.py', 'check_consistency'), ('core/story_review.py', 'check_story'),
          ('core/story_review.py', 'assemble_story_result')],
         ['agent_skills/story-audit/SKILL.md', ('core/story_review.py', 'build_story_prompt'),
          ('core/planning_research.py', 'research_review')], (
             ('审校正文', '当前章已保存正文或待审候选', '完整正文、章节号、内容哈希；审校结论应能回指这份文本。'),
             ('连续性材料', '前章、摘要与本章相关 Wiki', '前章片段、摘要和命中证据；每条含章节/条目来源与可用范围。'),
             ('审校目标', '页面选择或对话要求', '主线问题、连续性、情节增强等选择；不是要求模型找尽所有细节。'),
             ('审校方法', 'story-audit Skill 与按需查证', '观察、证词、推测的边界和问题分级；查证结果须保留证据归属。'))),
    node('revise', '正文修订', '功能', '所选基准、原保存正文、要求与修订模式',
         '候选稿、基础检查与要求验收', '局部、全文及表达润色分支不同；应用前还要验证原正文未变化。',
         [('core/revision_workflow.py', '_graph'), ('core/revision_workflow.py', 'run_revision_workflow')],
         ['agent_skills/chapter-revision/SKILL.md', 'agent_skills/chapter-revision/references/local.md',
          'agent_skills/chapter-revision/references/whole.md', 'agent_skills/chapter-revision/references/acceptance.md',
          ('core/chapter_revision.py', 'build_whole_chapter_prompt'), 'core/revision_patches.py',
          'agent_skills/prose-polish/SKILL.md'], (
             ('修订基准', '当前候选稿或已保存正文', '正文文本、来源 ID、内容哈希与段落结构；应用前会再次核对原正文是否变化。'),
             ('修改要求', '审校结果、用户输入或对话操作提案', '明确的需求条目、是否覆盖导入、主线/表达分类和可选增强选择。'),
             ('修订模式', '页面或对话操作参数', '局部、全文或润色；决定可动范围和输出形态。'),
             ('验收规则', 'chapter-revision / prose-polish Skill', '要求是否完成、段落结构限制、候选与原稿差异检查。'))),
    node('result', '结果与保存', '持久化', '候选、任务结果、用户应用操作',
         'project.json、chapters、conversations.sqlite3 与 runs',
         '分幕与修订应用是独立动作；续写会保存新章。不能统一理解为只生成候选。',
         ['web/chat_artifacts.py', 'web/chat_store.py', ('core/continuation.py', 'save_chapter'), 'web/api.py']),
    node('model', '模型调用与日志', '共用', '系统消息、Skill、模板、动态资料与参数',
         '原始输出、解析结果、耗时及 JSONL 记录',
         '对话使用 ChatOpenAI；业务使用 ContinuationLLM。追踪可选；日志可能含小说全文。',
         [('core/continuation.py', 'ContinuationLLM'), 'llm_adapters.py', 'core/plan_run_log.py', 'core/observability.py'],
         [('core/agent_skill.py', 'load_agent_skill')]),
]

EDGES = [('context','agent','标注输入'), ('agent','proposal','提出操作'), ('proposal','api','调度'),
         ('api','jobs','启动'), ('jobs','plan','规划'), ('jobs','write','续写'),
         ('jobs','audit','审校'), ('jobs','revise','修订'), ('plan','result','候选'),
         ('write','result','正文'), ('audit','result','要求 / 增强'), ('revise','result','候选'),
         ('agent','model','对话调用'), ('plan','model','生成 / 审查'),
         ('write','model','逐幕调用'), ('audit','model','审校 / 查证'), ('revise','model','修订 / 验收')]
SOURCES = {s['id']: s for n in NODES for key in ('code', 'prompts') for s in n[key]}


def architecture():
    return {'nodes': NODES, 'edges': [{'id':f'{a}:{b}', 'source':a, 'target':b, 'label':label}
                                    for a,b,label in EDGES],
            'scope':'分幕、续写、审校、修订的核心链路；导入、Wiki、学习版和插图尚未展开。',
            'version':hashlib.sha256(json.dumps(NODES, ensure_ascii=False).encode()).hexdigest()[:12]}


def read_source(ident):
    item = SOURCES.get(ident)
    if item is None: raise HTTPException(404, '源码来源未登记')
    path = (ROOT / item['path']).resolve()
    if not path.is_relative_to(ROOT) or not path.is_file(): raise HTTPException(404, '源码文件不可用')
    text = path.read_text(encoding='utf-8')
    lines = text.splitlines()
    start, end = 1, len(lines)
    if item['symbol']:
        found = next((n for n in ast.walk(ast.parse(text))
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                      and n.name == item['symbol']), None)
        if found is None: raise HTTPException(404, '源码符号已变化，请更新逻辑地图')
        start, end = found.lineno, found.end_lineno
    limit = min(end, start + 249)
    return {**item, 'start_line':start, 'end_line':limit, 'total_end_line':end,
            'truncated':limit < end, 'hash':hashlib.sha256(text.encode()).hexdigest(),
            'text':'\n'.join(lines[start-1:limit])}


def log_root(project):
    project=Path(project).resolve()
    root=(project/'runs'/'plan_logs').resolve()
    if not root.is_relative_to(project): raise HTTPException(403, '日志目录超出当前工程')
    return root


def log_file(project, ident):
    if not re.fullmatch(r'[A-Za-z0-9_]+', ident): raise HTTPException(400, '日志编号无效')
    root = log_root(project)
    path = (root / (ident + '.jsonl')).resolve()
    if not path.is_relative_to(root) or not path.is_file(): raise HTTPException(404, '日志不存在')
    if path.stat().st_size > 50_000_000: raise HTTPException(413, '该日志过大，请在本地查看')
    return path


def read_record(path, index):
    with path.open('r', encoding='utf-8') as stream:
        for number, line in enumerate(stream):
            if number == index:
                if len(line) > 4_000_000: raise HTTPException(413, '该条日志过大，请在本地查看')
                try: return json.loads(line)
                except ValueError: return {'event':'invalid_json', 'text':line[:1000]}
    raise HTTPException(404, '日志事件不存在')


def redactor(config):
    secrets = []
    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if re.search(r'api.?key|authorization|password|secret|token', key, re.I) and isinstance(item, str) and len(item)>=4:
                    secrets.append(item)
                else: collect(item)
        elif isinstance(value, list):
            for item in value: collect(item)
    collect(config)
    def redact(value):
        if isinstance(value, dict):
            return {key: '[REDACTED]' if re.search(r'api.?key|authorization|password|secret|access_token|refresh_token', key, re.I)
                    else redact(item) for key,item in value.items()}
        if isinstance(value, list): return [redact(item) for item in value]
        if isinstance(value, str):
            for secret in sorted(set(secrets), key=len, reverse=True): value=value.replace(secret,'[REDACTED]')
            value=re.sub(r'\bsk-[A-Za-z0-9_-]{12,}', '[REDACTED]', value)
            value=re.sub(r'(?i)(Bearer\s+)[A-Za-z0-9._-]+', r'\1[REDACTED]', value)
        return value
    return redact


def quality_root(create=False):
    """The quality suite has a separate, synthetic-only artifact boundary."""
    root = (ROOT / 'workspace' / 'agent_trials').resolve()
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def quality_run(name):
    if not re.fullmatch(r'story_audit_[A-Za-z0-9_]+', name):
        raise HTTPException(400, '质量评测编号无效')
    path = (quality_root() / name).resolve()
    if not path.is_relative_to(quality_root()) or not (path / 'runs' / 'plan_logs').is_dir():
        raise HTTPException(404, '质量评测不存在')
    return path


def quality_detail(name):
    """Return only synthetic inputs and model reports from one bounded trial."""
    path = quality_run(name)
    rows = {}
    for item in sorted((path / 'runs' / 'plan_logs').glob('*.jsonl')):
        if item.stat().st_size > 5_000_000:
            continue
        records = []
        for line in item.read_text(encoding='utf-8').splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                records.append(row)
        supplied = next((row for row in records if row.get('event') == 'regression_input'), {})
        report = next((row for row in reversed(records) if row.get('event') == 'regression_result'), {})
        rows[item.stem] = {
            'previous': str(supplied.get('previous') or ''),
            'text': str(supplied.get('text') or ''),
            'variant': supplied.get('variant'),
            'result': report.get('result') if isinstance(report.get('result'), dict) else {},
            'screening': report.get('screening') if isinstance(report.get('screening'), dict) else {},
        }
    return rows


def register_developer(app, project_path, config_path, jobs):
    @app.get('/api/developer/map')
    def get_map(): return architecture()

    @app.get('/api/developer/sources/{ident}')
    def get_source(ident: str): return read_source(ident)

    @app.get('/api/projects/{project_id}/developer/runs')
    def get_runs(project_id: str, limit: int = Query(60, ge=1, le=100)):
        root=log_root(project_path(project_id))
        files=[p for p in root.glob('*.jsonl') if p.is_file() and p.resolve().is_relative_to(root)] if root.is_dir() else []
        files=sorted(files,key=lambda p:p.stat().st_mtime,reverse=True)
        return [{'id':p.stem,'bytes':p.stat().st_size,'modified_at':p.stat().st_mtime}
                for p in files[:limit] if re.fullmatch(r'[A-Za-z0-9_]+',p.stem) and p.resolve().is_relative_to(root.resolve())]

    @app.get('/api/projects/{project_id}/developer/runs/{ident}')
    def get_events(project_id: str, ident: str, offset: int=Query(0,ge=0), limit: int=Query(60,ge=1,le=100)):
        path=log_file(project_path(project_id),ident)
        records=[]
        with path.open('r',encoding='utf-8') as stream:
            for index,line in enumerate(stream):
                if index<offset: continue
                if len(records)>=limit: return {'events':records,'next_offset':index}
                try: row=json.loads(line) if len(line)<=4_000_000 else {'event':'oversized_record'}
                except ValueError: row={'event':'invalid_json'}
                records.append({'index':index,'event':str(row.get('event') or 'unknown')[:100],
                    'timestamp':row.get('timestamp'), 'stage':str(row.get('stage') or '')[:80],
                    'fields':list(row), 'elapsed_ms':row.get('elapsed_ms')})
        return {'events':records,'next_offset':None}

    @app.get('/api/projects/{project_id}/developer/runs/{ident}/events/{index}')
    def get_event(project_id: str, ident: str, index: int,
                  marker: str | None=Header(None,alias='X-Novel-Workbench')):
        if marker!='1': raise HTTPException(403,'请从开发者页面主动展开日志详情')
        if index<0: raise HTTPException(400,'事件编号无效')
        from core.config_manager import load_config
        redact=redactor(load_config(str(config_path)))
        record=redact(read_record(log_file(project_path(project_id),ident),index))
        text=json.dumps(record,ensure_ascii=False,indent=2)
        return {'text':text[:160000], 'truncated':len(text)>160000, 'total_chars':len(text),
                'warning':'已隐藏已配置密钥和常见凭证；小说及其他个人内容仍可能存在，分享前请检查。'}

    @app.get('/api/developer/quality/cases')
    def get_quality_cases():
        from tools.evaluate_story_audit import expand_case, load_cases
        cases = load_cases()
        fixture = ROOT / 'tools' / 'deepagents' / 'fixtures' / 'story_audit_cases.json'
        def full_input(item):
            return '【前章材料】\n' + item['previous'] + '\n\n【待审文章】\n' + item['text']
        return {'suite': 'story-audit', 'fixture_version': hashlib.sha256(fixture.read_bytes()).hexdigest()[:12],
                'cases': [{'id': item['id'], 'dimension': item['dimension'], 'expected': item['expected'],
                           'previous': item['previous'], 'text': item['text'],
                           'short_article': full_input(item), 'long_article': full_input(expand_case(item)),
                           'requires_issue': item['requires_issue']} for item in cases],
                'boundary': '固定合成文本；不会读取、发送或修改任何小说章节。'}

    @app.get('/api/developer/quality/models')
    def get_quality_models():
        from core.config_manager import load_config
        config = load_config(str(config_path))
        names = sorted(str(name) for name, value in config.get('llm_configs', {}).items()
                       if isinstance(value, dict) and value.get('model_name'))
        return {'models': names, 'default': config.get('default_llm_config_name') if config.get('default_llm_config_name') in names else (names[0] if names else None)}

    @app.get('/api/developer/quality/runs')
    def get_quality_runs():
        from tools.compare_quality_runs import summarize
        items = []
        root = quality_root()
        if not root.is_dir():
            return []
        for path in root.glob('story_audit_*'):
            if not path.is_dir() or not re.fullmatch(r'story_audit_[A-Za-z0-9_]+', path.name):
                continue
            try:
                summary = summarize(quality_run(path.name))
            except (OSError, ValueError):
                continue
            items.append({'id': path.name, 'modified_at': path.stat().st_mtime, 'cases': len(summary),
                          'variants': sorted({str(row.get('variant') or '未知') for row in summary.values()}),
                          'screened_passes': sum(bool(row.get('screening', {}).get('passed')) for row in summary.values())})
        return sorted(items, key=lambda item: item['modified_at'], reverse=True)[:60]

    @app.get('/api/developer/quality/runs/compare')
    def compare_quality_runs(left: str, right: str):
        from tools.compare_quality_runs import compare
        return {**compare(quality_run(left), quality_run(right)),
                'details': {'left': quality_detail(left), 'right': quality_detail(right)}}

    @app.post('/api/projects/{project_id}/developer/quality/runs')
    def start_quality_run(project_id: str, payload: dict,
                          marker: str | None = Header(None, alias='X-Novel-Workbench')):
        if marker != '1':
            raise HTTPException(403, '请从开发者页面主动启动质量评测')
        model_config = str(payload.get('model_config') or 'deepseek-flash').strip()
        long = bool(payload.get('long'))
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', model_config):
            raise HTTPException(400, '模型配置名称无效')
        from tools.evaluate_story_audit import run_live_suite

        def work(update):
            update('正在校验合成用例；不会读取小说正文', {'variant': 'long' if long else 'short'})
            return run_live_suite(model_config, long, update)
        try:
            return jobs.start(project_path(project_id), 'quality_regression', work)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
