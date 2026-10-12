"""定向规划修订的变化对比与要求验收，不加入题材关键词或硬相似度阈值。"""
import json
import re
import time
from core.agent_skill import skill_guidance


def revision_requirements(text):
    """Preserve the complete goal, including examples, alternatives and limits."""
    text = text.split('【用户本次要求】', 1)[-1].strip()
    return [{'id':'U01','text':text}] if text else []


def verified_requirement_checks(data, candidate, requirements, full_scope=True):
    checks = data.get('requirement_checks')
    if not isinstance(checks, list) or len(checks) != len(requirements):
        raise ValueError('验收未逐项覆盖原要求')
    expected = {row['id'] for row in requirements}
    if {row.get('id') for row in checks if isinstance(row, dict)} != expected:
        raise ValueError('验收要求编号缺失或重复')
    beats = candidate.get('beats') or []
    for row in checks:
        if row.get('status') not in ('met', 'unmet', 'uncertain') or not str(row.get('reason') or '').strip():
            raise ValueError('逐项验收结果不完整')
        evidence = row.get('evidence')
        if not isinstance(evidence, list) or not evidence:
            raise ValueError('验收缺少当前候选稿依据')
        cited = set()
        for item in evidence:
            if not isinstance(item, dict): raise ValueError('验收证据格式无效')
            index, field, quote = item.get('beat'), item.get('field'), item.get('quote')
            if field == 'chapter_title' and type(index) is int and index == 0:
                if not isinstance(quote, str) or not quote.strip() or quote not in str(candidate.get('chapter_title') or ''):
                    raise ValueError('验收回目引文不在当前候选稿中')
                continue
            if type(index) is not int or not 1 <= index <= len(beats) or field not in ('desc', 'name'):
                raise ValueError('验收证据幕号或字段无效')
            # Ignore layout and terminal punctuation, never paraphrase facts.
            normalized = re.sub(r'\s+', '', quote).rstrip('。；;.!！?？') if isinstance(quote,str) else ''
            source = re.sub(r'\s+', '', str(beats[index-1].get(field) or ''))
            if not normalized or normalized not in source:
                raise ValueError('部分验收引用与当前候选稿不一致，无法确认修改是否落实')
            cited.add(index)
        if row.get('judgment') not in ('present', 'missing'):
            raise ValueError('验收需区分存在问题与缺失判断')
        if row['judgment'] == 'missing':
            reviewed = row.get('reviewed_beats')
            if (not isinstance(reviewed,list) or not reviewed or
                    any(type(i) is not int or not 1 <= i <= len(beats) for i in reviewed)):
                raise ValueError('缺失判断缺少有效的核对范围')
            if full_scope and set(reviewed) != set(range(1,len(beats)+1)):
                raise ValueError('缺失判断必须核对整份候选稿，不能由单句推断')
            if not full_scope and not cited.issubset(set(reviewed)):
                raise ValueError('修改影响核对范围没有覆盖引用的相关幕')
        if row['status'] == 'unmet' and not str(row.get('suggestion') or '').strip():
            raise ValueError('未落实要求缺少可执行修补方向')
    return checks


def verified_continuity_checks(data, candidate):
    checks = data.get('continuity_checks')
    if not isinstance(checks,list):
        raise ValueError('缺少修改影响核对结果')
    if not checks and data.get('requirement_scope')=='content':
        raise ValueError('内容修订需核对修改牵动的逻辑')
    for row in checks:
        if not isinstance(row,dict) or not str(row.get('focus') or '').strip():
            raise ValueError('修改影响核对缺少核对目标')
        probe={**row,'id':'C01'}
        verified_requirement_checks({'requirement_checks':[probe]},candidate,[{'id':'C01'}],full_scope=False)
    return checks


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
    items = revision_requirements(requirements)
    guidance = skill_guidance('chapter-planning','user-acceptance',audit,'user_plan_acceptance')
    prompt = (guidance+'\n【用户要求】\n'+requirements+
              '\n【完整修订目标，不按标点拆解】\n'+json.dumps(items,ensure_ascii=False)+
              '\n【原规划，仅比较变化，不是待验收对象】\n'+json.dumps(original,ensure_ascii=False)+
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
                           temperature=0.1,num_predict=3000,disable_thinking=True)
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
        checks = verified_requirement_checks(data, candidate, items)
        continuity = verified_continuity_checks(data,candidate)
        for row in checks + continuity:
            if row['status'] == 'unmet':
                notes.append({'scope': row.get('id') or '修改影响：'+row['focus'], 'problem':str(row['reason'])[:800],
                              'suggestion':str(row['suggestion'])[:800], 'evidence':row['evidence']})
        if any(row['status'] == 'uncertain' for row in checks + continuity):
            raise ValueError('部分要求无法确认，不自动补修或判定通过')
        if data['requirement_scope']=='content' and not changes['content_changed_beats']:
            notes.insert(0,{'scope':'全章','problem':'分幕内容没有变化，仅改回目或幕名不能落实内容要求。',
                            'suggestion':'针对用户要求改变必要幕的实际事件、人物选择或结果，并同步相关幕。'})
        if data['passed'] != (not notes):
            # A deterministic unchanged-content failure can override model pass.
            if not (notes and not changes['content_changed_beats'] and data['requirement_scope']=='content'):
                raise ValueError('验收总判定与逐项结果不一致')
        result = {'status':'passed' if not notes else 'needs_review',
                  'summary':'；'.join((row.get('id') or row['focus']) + '：' + str(row['reason']) for row in checks + continuity)[:800],
                  'unresolved':notes,'changes':changes, 'requirement_checks':checks,
                  'continuity_checks':continuity,
                  'requirement_scope':data['requirement_scope']}
    except Exception as exc:
        result = {'status':'incomplete','summary':'候选分幕已生成，但自动验收未能确认结果。请人工核对修改是否落实后再保存。',
                  'unresolved':[{'scope':'要求验收','problem':str(exc)[:500],'suggestion':'重新验收或人工核对'}],
                  'changes':changes}
    if audit: audit.write('plan_revision_acceptance',attempt=attempt,**result)
    return result
