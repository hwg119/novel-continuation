"""Check targeted acceptance coverage, not literary quality by keywords."""


def goal_check_issues(response, request, sources):
    """Require explicit, located goal checks in an existing review/acceptance call."""
    if not request.strip():
        return []
    checks = response.get('requirement_checks')
    if not isinstance(checks, list) or not checks:
        raise ValueError('缺少本次创作目标验收，不能只核对初审问题')
    issues = []
    for check in checks[:12]:
        if (not isinstance(check, dict) or type(check.get('met')) is not bool or
                not isinstance(check.get('requirement'), str) or not check['requirement'].strip() or
                check['requirement'] not in request or
                not isinstance(check.get('evidence'), str) or not check['evidence'].strip()):
            raise ValueError('创作目标核对缺少原要求或具体效果依据')
        quote = str(check.get('plan_quote') or '').strip()
        found = next((s for s in sources if s['kind']=='plan' and quote and quote in s['text']), None)
        if not found:
            raise ValueError('创作目标核对缺少可定位的规划引文')
        if not check['met']:
            suggestion = str(check.get('suggestion') or '').strip()
            if not suggestion:
                raise ValueError('未落实的创作目标缺少修补方向')
            issues.append({'severity':'medium', 'category':'goal', 'scope':found['label'],
                'problem':check['evidence'], 'requirement':check['requirement'],
                'impact':'本次创作目标未落实', 'suggestion':suggestion,
                'citations':[{'source_id':'U01','quote':check['requirement']},
                             {'source_id':found['id'],'quote':quote}]})
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
