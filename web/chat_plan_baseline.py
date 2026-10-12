"""Compatibility adapter for the shared conversation work-product backend."""
import copy
from web.chat_artifacts import ChatArtifacts, ARTIFACT_GUIDANCE


PLAN_SELECTION_GUIDANCE = ARTIFACT_GUIDANCE


def planning_origin(row):
    return {'kind':'chat_candidate' if row['status']!='saved' else 'saved',
            'version_id':row['id'], 'action_id':row['id'] if row['status']!='saved' else None,
            'job_id':row.get('job_id'), 'chapter':row['chapter'], 'created_at':row.get('created_at'),
            'chapter_title':row['title'], 'hash':row['hash'],
            'label':'选定分幕：' + row['title'] + ' · ' + str(row.get('created_at') or '已保存')}


def plan_versions(store, jobs, path, session, chapter, saved):
    candidates = []
    for row in ChatArtifacts(store,jobs,path,session).list(chapter,'plan'):
        plan = copy.deepcopy(saved)
        plan.update(row['content'])
        candidates.append((plan,planning_origin(row)))
    saved_origin = {'kind': 'saved', 'version_id':f'saved:{chapter}', 'chapter': chapter, 'chapter_title': saved.get('chapter_title'),
                    'label': '已保存分幕：' + str(saved.get('chapter_title') or '')}
    return candidates if any(origin['kind']=='saved' for _,origin in candidates) else candidates + [(copy.deepcopy(saved),saved_origin)]


def version_catalog(versions):
    return [{**origin, 'beat_names':[beat.get('name') for beat in plan.get('beats') or []],
             'preview':[str(beat.get('desc') or '')[:160] for beat in plan.get('beats') or []]}
            for plan, origin in versions]


def chat_plan_baseline(store, jobs, path, session, chapter, saved, version_id=None):
    backend = ChatArtifacts(store,jobs,path,session)
    try:
        row = backend.resolve(chapter,version_id,'plan')
    except ValueError:
        # Legacy links can refer to an empty saved plan; it means build a plan.
        if version_id not in (None,f'saved:{chapter}',f'saved:plan:{chapter}'):
            raise
        return copy.deepcopy(saved), {'kind':'saved','chapter':chapter,'label':'已保存分幕'}
    plan = copy.deepcopy(saved)
    plan.update(row['content'])
    origin = planning_origin(row)
    if version_id: origin['selection']='model_version_id'
    return plan,origin


def revision_baseline_data(plan, origin):
    # Snapshot at proposal time: later tasks cannot silently change this action's
    # baseline while it awaits execution. This is server-owned action data.
    return {'plan_baseline': origin, 'plan_baseline_preview': {
        key: copy.deepcopy(plan[key]) for key in ('chapter_title', 'beats') if key in plan}}
