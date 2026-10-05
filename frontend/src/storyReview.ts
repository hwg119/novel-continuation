export type StoryEnhancement = {
  id: string; scope: string; suggestion: string; preserve: string
  citations: { source_id: string; quote: string }[]; optional: boolean
}

// 新记录直接读取分离字段；旧故事记录只拆固定包装，不改写历史数据。
export function storyAuditDisplay(kind: string, summary: string, report: string, details?: string | null) {
  if (kind !== 'story') return { summary: summary.trim(), details: report.trim() }
  const normalized = report.replace(/\r\n/g, '\n').trim()
  const marker = '【主线依据】'
  const boundary = normalized.indexOf(marker)
  const heading = '【故事审校】'
  let resolvedSummary = summary.trim()
  if (!resolvedSummary && normalized.startsWith(heading)) {
    resolvedSummary = normalized.slice(heading.length, boundary >= 0 ? boundary : undefined).trim()
  }
  let resolvedDetails: string
  if (typeof details === 'string') resolvedDetails = details.trim()
  else if (boundary >= 0) resolvedDetails = normalized.slice(boundary + marker.length).trim()
  else {
    const content = normalized.startsWith(heading) ? normalized.slice(heading.length).trim() : normalized
    // 未识别格式宁可保留，只有明确相同的总评才隐藏。
    resolvedDetails = content === resolvedSummary ? '' : content
  }
  const emptyNotice = '未发现有充分依据的重大主线问题；不代表全文没有细节错误。'
  if (resolvedDetails === emptyNotice) resolvedDetails = ''
  return { summary: resolvedSummary, details: resolvedDetails }
}

// 只导入用户选中的增强，未选中意见和待核对提醒不成为修订要求。
export function importStoryRequirements(main: string, enhancements: StoryEnhancement[], selected: string[]) {
  const chosen = enhancements.filter(item => selected.includes(item.id))
  if (!chosen.length) return main.trim()
  const sections = main.trim() ? [`【主线修订】\n${main.trim()}`] : []
  sections.push('【已选择的情节增强】\n' + chosen.map((item, i) =>
    `${i + 1}. ${item.scope}：${item.suggestion}\n保留：${item.preserve}`).join('\n'))
  return sections.join('\n\n')
}
