import test from 'node:test'
import assert from 'node:assert/strict'
import { taskEventLevel } from './taskEventLevel.ts'

test('quoted error words in progress are not task errors', () => {
  for (const message of ['正在核对疑点：记录无异常', '正在核对疑点：是否存在错误？', '正在核对疑点：未通过的手续是否补齐？']) {
    assert.equal(taskEventLevel({ message }), 'info')
  }
})
test('explicit backend severity takes precedence', () => {
  assert.equal(taskEventLevel({ message: '普通消息', level: 'error' }), 'error')
  assert.equal(taskEventLevel({ message: '引用任务失败', level: 'info' }), 'info')
  assert.equal(taskEventLevel({ message: '任务完成', level: 'success' }), 'success')
})
test('old task completion and failure records remain recognizable', () => {
  assert.equal(taskEventLevel({ message: '任务失败：连接超时' }), 'error')
  assert.equal(taskEventLevel({ message: '任务完成' }), 'success')
  assert.equal(taskEventLevel({ message: '正在核对：第一步已完成' }), 'info')
})
