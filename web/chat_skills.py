"""Read-only discovery of project skills for the dialogue agent."""
from core.agent_skill import load_agent_skill


SKILL_MODES = {
    'project-guide': {'': '项目功能与页面使用咨询'},
    'chapter-planning': {
        'discussion': '讨论或评估分幕：故事推进、连续性、因果与改进方向，自然语言回复',
        'draft': '设计章节与分幕', 'review': '结构化规划审查',
        'repair': '按明确目标定向修订规划', 'user-acceptance': '对照完整目标验收分幕修订'},
    'chapter-writing': {'': '根据分幕续写章节'},
    'story-audit': {'': '评估正文的主线逻辑与可选情节增强'},
    'chapter-revision': {'local': '局部正文修订', 'whole': '全文修订', 'acceptance': '正文修订验收'},
    'prose-polish': {'': '改善表达与行文风格'},
    'history-research': {'': '按需查证历史正文与上下文'},
}


def skill_catalog():
    return [{'name':name,'modes':[{'reference':ref or None,'purpose':purpose}
             for ref,purpose in modes.items()]} for name,modes in SKILL_MODES.items()]


def read_dialogue_skill(name, reference=None):
    if not isinstance(name,str) or name not in SKILL_MODES:
        raise ValueError('技能不存在，请从技能目录选择')
    if reference is not None and not isinstance(reference,str):
        raise ValueError('技能模式无效')
    if (reference or '') not in SKILL_MODES[name]:
        raise ValueError('技能模式不存在，请从技能目录选择')
    guidance,metadata=load_agent_skill(name,reference or None)
    return {'instructions':guidance,'metadata':metadata,
            'usage':'规则用于理解和评估，不授予写入权限。讨论使用自然语言；结构化格式仅用于相应业务任务。'}
