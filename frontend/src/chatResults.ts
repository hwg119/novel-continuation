import { routePath, type View } from './routeState.ts'

export function resultCard(data: Record<string, any>, savedPlan = false, selectedEnhancements: string[] = []) {
  const result = data.result || {}
  const tool = data.tool || (result.beats?.length ? 'plan' : result.candidate ? 'revise' : result.report ? 'consistency' : '')
  const titles: Record<string, string> = { plan: '分幕规划', plan_revision: '分幕修订', generate: '章节续写', consistency: '故事审校', revise: '正文修订', illustration:'章节插图' }
  const completed = data.status === 'completed'
  const state = !completed ? ({ failed:'任务失败', cancelled:'任务已取消', interrupted:'服务中断' } as Record<string,string>)[data.status] || '任务未完成' : ['plan','plan_revision'].includes(tool)
    ? savedPlan ? '已保存分幕' : '候选分幕 · 尚未保存'
    : tool === 'revise' ? '修订稿已生成 · 应用状态请在修订页核对'
    : tool === 'illustration' ? '插图已保存' : tool === 'generate' ? '正文已保存' : '审校结果已保存'
  const actions: { label: string; view: View; query?: Record<string, string> }[] = []
  if (completed && tool === 'revise') {
    actions.push({ label: '查看修订稿', view: 'revision', query: { revision: data.job_id, mode: 'draft' } },
      { label: '比较差异', view: 'revision', query: { revision: data.job_id } },
      { label: '比较并应用', view: 'quality', query: { revision: data.job_id } })
  } else if (completed && tool === 'consistency') {
    actions.push({ label: '查看审校', view: 'quality', query: { audit: data.job_id } })
    const chosen = selectedEnhancements.filter(id => (result.story_enhancements || []).some((item: { id: string }) => item.id === id))
    if (result.revision_requirements?.trim() || chosen.length) actions.push({ label: '导入要求并修订', view: 'quality', query: { audit: data.job_id, import_requirements: '1', enhancements: JSON.stringify(chosen) } })
  } else if (completed && tool === 'illustration') actions.push({label:'打开插图设置',view:'settings'},{label:'查看章节',view:'write'})
  else if (completed && tool === 'generate') actions.push({ label: '阅读正文', view: 'write' }, { label: '打开审校与修订', view: 'quality' })
  else if (completed && ['plan','plan_revision'].includes(tool)) actions.push({ label: '打开续写设定', view: 'settings' })
  actions.push({ label: '查看运行记录', view: 'runs', query: { job: data.job_id } })
  return { title: titles[tool] || '处理结果', state, tool, completed, actions }
}

export function resultLink(project: string, chapter: number, action: { view: View; query?: Record<string, string> }) {
  const base = routePath(action.view, project, chapter)
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(action.query || {})) if (value) params.set(key, value)
  return base + (params.size ? `?${params}` : '')
}
