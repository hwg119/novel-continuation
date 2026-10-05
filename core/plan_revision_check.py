"""定向规划修订的变化对比与要求验收，不加入题材关键词或硬相似度阈值。"""
import json
import re
import time
from core.agent_skill import skill_guidance


def plan_changes(original, candidate):
    def content(beat):
        return re.sub(r'\s+', '', str(beat.get('desc') or ''))
    old, new = original.get('beats') or [], candidate.get('beats') or []
    return {
        'content_changed_beats': [i+1 for i,b in enumerate(new)
                                 if i >= len(old) or content(b) != content(old[i])],
        'renamed_beats': [i+1 for i,b in enumerate(new)
                          if i >= len(old) or b.get('name') != old[i].get('name')],
        'title_changed': original.get('chapter_title') != candidate.get('chapter_title'),
    }


def check_revision(original, candidate, requirements, llm, parse_json,
                   previous_context='', audit=None, attempt=1):
    changes = plan_changes(original,candidate)
    guidance = skill_guidance('chapter-planning','user-acceptance',audit,'user_plan_acceptance')
    prompt = (guidance+'\n【用户要求】\n'+requirements+
              '\n【原规划】\n'+json.dumps(original,ensure_ascii=False)+
              '\n【修订候选】\n'+json.dumps(candidate,ensure_ascii=False)+
              '\n【程序变化对比】\n'+json.dumps(changes,ensure_ascii=False)+
              '\n注意：变化对比只是文本变化，不代表要求已落实。'
              '内容未变时，只能在用户仅要求改名称或表达时通过。'
              '\n【前章连续性依据，仅核对修改牵动部分】\n'+previous_context[:7500])
    if audit:
        audit.write('plan_revision_changes',attempt=attempt,**changes)
        audit.write('model_request',stage='user_plan_acceptance',attempt=attempt,prompt=prompt)
    started = time.monotonic()
    try:
        raw = llm.complete(prompt,system='你是定向修订验收员，只返回验收 JSON。',
                           temperature=0.1,num_predict=1400,disable_thinking=True)
        if audit:
            audit.write('model_response',stage='user_plan_acceptance',attempt=attempt,
                        raw_response=raw,elapsed_seconds=round(time.monotonic()-started,2))
        data = parse_json(raw)
        if (not isinstance(data,dict) or type(data.get('passed')) is not bool or
                data.get('requirement_scope') not in ('content','labels') or
                not isinstance(data.get('unresolved'),list) or
                not isinstance(data.get('summary'),str) or not data['summary'].strip()):
            raise ValueError('验收字段不完整')
        notes = []
        for note in data['unresolved'][:12]:
            if not isinstance(note,dict) or not isinstance(note.get('problem'),str) or not note['problem'].strip():
                raise ValueError('验收未解决问题格式无效')
            notes.append({key:str(note.get(key) or '')[:800] for key in ('scope','problem','suggestion')})
        if data['requirement_scope']=='content' and not changes['content_changed_beats']:
            notes.insert(0,{'scope':'全章','problem':'分幕内容没有变化，仅改回目或幕名不能落实内容要求。',
                            'suggestion':'针对用户要求改变必要幕的实际事件、人物选择或结果，并同步相关幕。'})
        if not data['passed'] and not notes:
            notes.append({'scope':'全章','problem':data['summary'][:800],
                          'suggestion':'围绕原要求补修，不只改变标题或表述。'})
        result = {'status':'passed' if data['passed'] and not notes else 'needs_review',
                  'summary':data['summary'][:800], 'unresolved':notes,'changes':changes,
                  'requirement_scope':data['requirement_scope']}
    except Exception as exc:
        result = {'status':'incomplete','summary':'要求验收未完成，候选规划不能视为修订通过。',
                  'unresolved':[{'scope':'要求验收','problem':str(exc)[:500],'suggestion':'重新验收或人工核对'}],
                  'changes':changes}
    if audit: audit.write('plan_revision_acceptance',attempt=attempt,**result)
    return result
