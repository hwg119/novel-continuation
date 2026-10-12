"""Separate durable requirements from generated discussion without rewriting user data."""
import json


DISCUSSION_MARKER = '【讨论建议的前提核对】'


def current_creation_request(settings):
    request = str(settings.get('_planning_request') or '')
    # Program guidance is not a creative objective. Preserve the human's exact
    # wording after our wrapper, rather than the router's paraphrase.
    return request.split('【用户本次要求】', 1)[-1].strip()


def planning_inputs(settings):
    raw = str(settings.get('chapter_requirements') or '')
    # Only split the exact marker emitted by our chat integration. Unmarked user
    # requirements are preserved; we cannot reliably infer their authorship.
    explicit, marker, legacy = raw.partition(DISCUSSION_MARKER)
    request = str(settings.get('_planning_request') or '')
    return {
        'user_requirements': explicit.strip(),
        'current_request': request,
        'current_request_kind': ('discussion_proposal' if DISCUSSION_MARKER in request
                                 and '【用户本次要求】' not in request else 'user_request'),
        'legacy_discussion': (marker + legacy).strip() if marker else '',
    }


def planning_input_prompt(settings):
    layers = planning_inputs(settings)
    legacy = layers.pop('legacy_discussion')
    layers['legacy_discussion_omitted_chars'] = len(legacy)
    layers['mode'] = settings.get('_planning_mode', 'rebuild')
    return ('【分层创作输入】\n' + json.dumps(layers, ensure_ascii=False) +
            '\nuser_requirements 是长期创作要求；current_request 只用于本次任务；'
            '旧聊天方案全文已隔离，不作为本次输入；rebuild 从正文和本次目标重新构思，'
            'revision 才以单独提供的当前规划为修改基础。'
            '讨论中的历史事实概括必须按正文核对；采用创意不表示其前提已经发生。'
            '创作要求决定未来写什么，正文决定过去发生过什么。'
            '未标记的旧要求也不作为历史证据；只有明确要求改写历史时才处理历史变更。')


def saved_plan_input(previous, action):
    """Keep task provenance, not a growing imperative assembled from AI replies."""
    plan = {key: previous.get(key, '') for key in ('chapter_brief', 'chapter_requirements')}
    plan['planning_input_origin'] = {
        'kind': 'discussion_proposal' if action.get('source_reply') else 'user_request',
        'request': str(action.get('requirements') or ''),
        'source_turn': action.get('source_reply') or action.get('turn_id'),
    }
    return plan
