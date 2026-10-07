import test from 'node:test'
import assert from 'node:assert/strict'
import { chatTimeline, chatReplyText } from './chatTimeline.ts'

const event = (id, kind, data) => ({ id, kind, time: '10:00:00', data })
test('assistant navigation terminology is localized including restored history', () => {
  assert.equal(chatReplyText('请选择 navigate。'), '请选择 打开功能页面。')
  assert.equal(chatReplyText('NAVIGATE 打开续写设定'), '打开功能页面 打开续写设定')
  assert.equal(chatReplyText('导航按钮'), '导航按钮')
})
test('dialogue progress collapses into one process, separate from reply', () => {
  const rows = chatTimeline([
    event(1, 'user', { turn_id: 't', text: '请求' }),
    event(2, 'progress', { turn_id: 't', text: '理解请求', model: 'test-model' }),
    event(3, 'progress', { turn_id: 't', text: '组织回复' }),
    event(4, 'delta', { turn_id: 't', text: '答' }),
    event(5, 'delta', { turn_id: 't', text: '复' }),
    event(6, 'done', { turn_id: 't' }),
  ])
  assert.deepEqual(rows.map(r => r.kind), ['user','process','reply'])
  assert.equal(rows[1].data.entries.length, 2)
  assert.equal(rows[1].data.status, 'completed')
  assert.equal(rows[1].data.entries[0].time, '10:00:00')
  assert.equal(rows[2].data.text, '答复')
})
test('parallel tasks retain independent logs and completion state', () => {
  const rows = chatTimeline([
    event(1, 'action_status', { action_id: 'a', status: 'started' }),
    event(2, 'action_status', { action_id: 'b', status: 'started' }),
    event(3, 'task_progress', { action_id: 'a', message: '第一步', level: 'info', data: { model: 'x' } }),
    event(4, 'task_progress', { action_id: 'b', message: '失败', level: 'error', data: {} }),
    event(5, 'task_result', { action_id: 'b', status: 'failed' }),
  ])
  const processes = rows.filter(r => r.kind === 'process')
  assert.equal(processes[0].data.status, 'running')
  assert.equal(processes[1].data.status, 'failed')
  assert.equal(processes[0].data.entries[0].details.model, 'x')
  assert.equal(processes[1].data.entries[0].level, 'error')
  assert.ok(rows.some(r => r.kind === 'task_result'))
})
test('cancelled replies and interrupted tasks remain visible', () => {
  const rows = chatTimeline([
    event(1, 'progress', { turn_id: 't', text: '处理' }),
    event(2, 'cancelled', { turn_id: 't', text: '已停止' }),
    event(3, 'action_status', { action_id: 'a', status: 'interrupted', text: '服务重启' }),
  ])
  assert.equal(rows[0].data.status, 'cancelled')
  assert.ok(rows.some(r => r.kind === 'cancelled'))
  assert.ok(rows.some(r => r.kind === 'action_status'))
})
test('rendering restored history does not modify stored events', () => {
  const history = [event(1,'delta',{turn_id:'t',text:'a'}),event(2,'delta',{turn_id:'t',text:'b'})]
  const original = JSON.stringify(history)
  assert.deepEqual(chatTimeline(history), chatTimeline(history))
  assert.equal(JSON.stringify(history), original)
})

test('task cancellation remains pending until the worker actually stops', () => {
  const rows = chatTimeline([
    event(1,'task_status',{action_id:'a',status:'running',cancel_requested:true,can_cancel:true,elapsed_seconds:32}),
  ])
  assert.equal(rows[0].data.status,'cancelling')
  assert.equal(rows[0].data.elapsed_seconds,32)
  assert.equal(rows[0].data.action_id,'a')
  const finished = chatTimeline([
    event(1,'task_status',{action_id:'a',status:'running',cancel_requested:true}),
    event(2,'task_result',{action_id:'a',status:'cancelled'}),
  ])
  assert.equal(finished[0].data.status,'cancelled')
})
