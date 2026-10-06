export type ChatEvent = { id: number; kind: string; time?: string; data: Record<string, any> }
export type ProcessEntry = { id: number; time: string; text: string; level: string; details: Record<string, unknown> }
export type ChatProcess = { key: string; label: string; status: string; entries: ProcessEntry[] }

/** Localize leaked internal navigation names in old and streamed assistant replies. */
export function chatReplyText(text: string): string {
  return text.replace(/\bnavigate\b/gi, '打开功能页面')
}

/** Group operational output without mixing it into the assistant's reply. */
export function chatTimeline(events: ChatEvent[]): ChatEvent[] {
  const rows: ChatEvent[] = []
  const groups = new Map<string, ChatEvent>()
  const replies = new Map<string, ChatEvent>()
  function group(event: ChatEvent, key: string, label: string) {
    let row = groups.get(key)
    if (!row) {
      row = { id: event.id, kind: 'process', data: { key, label, status: 'running', entries: [] } satisfies ChatProcess }
      groups.set(key, row); rows.push(row)
    }
    return row.data as ChatProcess
  }
  for (const event of events) {
    const data = event.data
    if (event.kind === 'delta') {
      const previous = replies.get(data.turn_id)
      if (previous) previous.data.text += data.text
      else {
        const row = { ...event, kind: 'reply', data: { ...data } }
        replies.set(data.turn_id, row); rows.push(row)
      }
    } else if (event.kind === 'progress' || event.kind === 'task_progress') {
      const isTask = event.kind === 'task_progress'
      const key = isTask ? `task:${data.action_id || data.job_id}` : `turn:${data.turn_id}`
      const process = group(event, key, isTask ? '任务执行' : '对话处理')
      const details = isTask ? data.data || {} : Object.fromEntries(Object.entries(data)
        .filter(([key]) => !['text','turn_id','time','level'].includes(key)))
      process.entries.push({ id: event.id, time: data.time || event.time || '',
        text: data.text || data.message || '', level: data.level || 'info', details })
    } else if (event.kind === 'action_status' && data.status === 'started') {
      group(event, `task:${data.action_id}`, '任务执行')
    } else if (['done','failed','cancelled','interrupted'].includes(event.kind)) {
      const process = groups.get(`turn:${data.turn_id}`)
      if (process) process.data.status = event.kind === 'done' ? 'completed' : event.kind
      if (event.kind !== 'done') rows.push(event)
    } else if (event.kind === 'task_result') {
      const process = groups.get(`task:${data.action_id || data.job_id}`)
      if (process) process.data.status = data.status
      rows.push(event)
    } else if (event.kind === 'action_status') {
      if (data.status === 'interrupted') rows.push(event)
    } else rows.push(event)
  }
  return rows
}
