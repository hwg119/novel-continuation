export type TaskEventLevel = 'info' | 'success' | 'error'

export function taskEventLevel(event: { message: string; level?: string; data?: Record<string, unknown> }): TaskEventLevel {
  const explicit = event.level ?? event.data?.level
  if (explicit === 'info' || explicit === 'success' || explicit === 'error') return explicit
  // 兼容旧记录只识别任务状态前缀，不扫描模型引用的正文或候选问题。
  if (/^(?:任务失败|任务中断|服务内部错误|请求失败|Error:)/.test(event.message)) return 'error'
  if (/^任务完成(?:$|[：:])/u.test(event.message)) return 'success'
  return 'info'
}
