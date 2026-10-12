"""Keep published evidence distinct from conversational inventions."""
from pathlib import Path
import hashlib
from core.project_manager import chapter_path, list_chapter_files
from core.continuation import read_chapter_summary


EVIDENCE_GUIDANCE = '''资料状态决定用途：已保存正文是已发生事件的主要依据；摘要是压缩参考，不能证明被省略的细节；已保存分幕是未来计划，不代表已经发生；聊天助手的建议和未保存候选稿只是创意备选。用户同意采用建议表示创作授权，不表示建议中的事件已经发生。
提出“再次、回来、归还、已经取得”等依赖历史前提的建议时，先按需核对正文。资料不支持时，把必要的发现、取得或转交过程作为待安排的新情节，或明确写成假设，不虚构过去。允许创造未来，不悄悄改写过去。'''

PLAN_PREMISE_GUIDANCE = '''【讨论建议的前提核对】
以下讨论是拟采用的创意，不是已发生事实。依据规划上下文中的已保存正文，核对建议依赖的取得、持有、转交及其他事件前提。正文不支持时，可以在本章安排必要的新过程，或改成发现、怀疑和待核实；不能直接把未发生的前提当成历史。不要为了核对而禁止新增创意，也不要强行同时采用互斥备选。'''


def enrich_chat_context(context, project, number):
    context.pop('previous_chapter', None)
    context['evidence_guidance'] = EVIDENCE_GUIDANCE
    context['source_states'] = {
        'chapter_excerpt':'saved_body_excerpt', 'plan':'saved_plan_not_occurred',
        'conversation':'discussion_and_execution_history_not_canon',
        'research_results':'check_each_source_kind',
    }
    for row in context.get('conversation', []):
        role = row.get('role')
        row['source_state'] = ('creative_suggestion_not_adopted' if role == 'assistant' else
                              'user_request_not_event_evidence' if role == 'user' else
                              'task_result_candidate_unless_saved' if role == 'task_result' else
                              'requested_action_not_event_evidence')
    if type(number) is not int or number < 1:
        return None
    previous = max((n for n, _, _ in list_chapter_files(project) if n < number), default=None)
    if previous is None:
        return None
    path = Path(chapter_path(project, previous))
    if not path.is_file():
        return None
    body = path.read_text(encoding='utf-8')
    summary = read_chapter_summary(project, previous) or ''
    context['previous_chapter'] = {
        'chapter':previous, 'kind':'saved_body_tail', 'text':body[-2400:],
        'id':f'body:{previous}', 'source_state':'saved_body',
        'hash':hashlib.sha256(body.encode('utf-8')).hexdigest(),
        'scope':'tail_excerpt', 'summary_source_state':'compressed_reference_not_full_body',
        'offset':max(0,len(body)-2400), 'total_chars':len(body), 'truncated':len(body)>2400,
        'summary':summary[:1200], 'summary_kind':'compressed_reference',
    }
    return {'chapter':previous, 'body_chars':min(len(body),2400),
            'summary_chars':min(len(summary),1200), 'truncated':len(body)>2400}
