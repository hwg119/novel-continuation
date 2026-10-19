"""Check targeted acceptance coverage without comparing model-repeated prose."""
import re


def _same_text_span(quote, source_text):
    """Locate a verbatim span while ignoring presentation-only whitespace."""
    compact_quote = re.sub(r"\s+", " ", str(quote or "")).strip()
    compact_source = re.sub(r"\s+", " ", str(source_text or "")).strip()
    return bool(compact_quote and compact_quote in compact_source)


def creative_requirement_items(request):
    """Give the complete current creative goal a program-owned stable identity."""
    request = str(request or '').strip()
    return [{'id': 'U01', 'text': request}] if request else []


def _located_plan_citations(check, sources):
    """Return verified candidate-plan citations; never accept the user request as proof."""
    plan_sources = {item['id']: item for item in sources if item.get('kind') == 'plan'}
    citations = check.get('plan_citations')
    if citations is None:
        # Compatibility for older model responses that supplied one real plan quote.
        quote = str(check.get('plan_quote') or '').strip()
        citations = ([{'source_id': source['id'], 'quote': quote}]
                     if quote and (source := next(
                         (item for item in plan_sources.values() if quote in item['text']), None))
                     else [])
    if not isinstance(citations, list):
        raise ValueError('创作目标核对的规划引文格式无效')
    located = []
    for citation in citations:
        if not isinstance(citation, dict):
            raise ValueError('创作目标核对的规划引文格式无效')
        source = plan_sources.get(citation.get('source_id'))
        quote = str(citation.get('quote') or '').strip()
        if not source or not _same_text_span(quote, source['text']):
            raise ValueError('创作目标核对所引规划原文无法定位')
        located.append({'source_id': source['id'], 'quote': quote, 'label': source['label']})
    return located


def goal_check_issues(response, request, sources):
    """Require explicit, located goal checks in an existing review/acceptance call."""
    requirements = creative_requirement_items(request)
    if not requirements:
        return []
    checks = response.get('requirement_checks')
    if not isinstance(checks, list) or len(checks) != len(requirements):
        raise ValueError('缺少本次创作目标验收，不能只核对初审问题')
    expected = {item['id']: item['text'] for item in requirements}
    ids = [item.get('id') for item in checks if isinstance(item, dict)]
    if len(ids) != len(checks) or len(set(ids)) != len(ids) or set(ids) != set(expected):
        raise ValueError('创作目标核对编号缺失、重复或不属于本次要求')
    issues = []
    for check in checks[:12]:
        if (not isinstance(check, dict) or type(check.get('met')) is not bool or
                not isinstance(check.get('evidence'), str) or not check['evidence'].strip()):
            raise ValueError('创作目标核对缺少落实状态或具体效果依据')
        located = _located_plan_citations(check, sources)
        # Presence needs positive proof in the candidate. Absence has no text to
        # quote; an unmet check may instead explain the missing effect directly.
        if check['met'] and not located:
            raise ValueError('已落实的创作目标缺少可定位的规划引文')
        if not check['met']:
            suggestion = str(check.get('suggestion') or '').strip()
            if not suggestion:
                raise ValueError('未落实的创作目标缺少修补方向')
            issues.append({'severity':'medium', 'category':'goal',
                'scope':located[0]['label'] if located else '完整创作目标',
                'problem':check['evidence'], 'requirement_id':check['id'],
                'requirement':expected[check['id']],
                'impact':'本次创作目标未落实', 'suggestion':suggestion,
                'citations':([{'source_id':check['id'],'quote':expected[check['id']]}] +
                             [{key:item[key] for key in ('source_id','quote')} for item in located])})
    return issues


def acceptance_coverage(issues, response):
    expected = {row['issue_id'] for row in issues if row.get('issue_id')}
    if not expected:
        return []  # Older checkpoints have no stable issue identifiers.
    checks = response.get('checked_issues')
    if not isinstance(checks, list):
        raise ValueError('验收缺少逐项问题核对记录')
    by_id = {}
    for row in checks:
        if (not isinstance(row, dict) or row.get('issue_id') not in expected or
                row.get('issue_id') in by_id or type(row.get('resolved')) is not bool or
                not isinstance(row.get('change'), str) or not row['change'].strip()):
            raise ValueError('逐项验收编号、解决状态或变化依据无效')
        by_id[row['issue_id']] = row
    if expected != set(by_id):
        raise ValueError('验收遗漏初审问题：' + '、'.join(sorted(expected - set(by_id))))
    unresolved_ids = {row.get('issue_id') for row in response.get('unresolved', []) if isinstance(row, dict)}
    if any(not row['resolved'] and key not in unresolved_ids for key, row in by_id.items()):
        raise ValueError('未解决的问题未进入补修清单')
    if any(row['resolved'] and key in unresolved_ids for key, row in by_id.items()):
        raise ValueError('逐项验收与未解决清单相互矛盾')
    return checks
