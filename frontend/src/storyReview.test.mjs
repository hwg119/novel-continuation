import test from 'node:test'
import assert from 'node:assert/strict'
import { importStoryRequirements, storyAuditDisplay } from './storyReview.ts'

const enhancements = [
  { id: 'E01', scope: '相遇', suggestion: '增加迟疑的动作。', preserve: '两人合作的结果。', citations: [], optional: true },
  { id: 'E02', scope: '结尾', suggestion: '突出离别的情绪。', preserve: '离开的决定。', citations: [], optional: true },
]

test('no selection imports only main requirements, never optional enhancements', () => {
  assert.equal(importStoryRequirements('  1. 统一去向。 ', enhancements, []), '1. 统一去向。')
})
test('enhancement alone is importable without a main conflict', () => {
  const result = importStoryRequirements('', enhancements, ['E02'])
  assert.match(result, /突出离别的情绪/)
  assert.match(result, /保留：离开的决定/)
  assert.doesNotMatch(result, /迟疑|主线修订/)
})
test('selected enhancements and main requirements are clearly separated', () => {
  const result = importStoryRequirements('统一去向。', enhancements, ['E01', 'E01', 'old-id'])
  assert.match(result, /【主线修订】/)
  assert.match(result, /【已选择的情节增强】/)
  assert.equal(result.split('增加迟疑的动作。').length - 1, 1)
  assert.doesNotMatch(result, /离别/)
})
test('empty or outdated selections do not manufacture requirements', () => {
  assert.equal(importStoryRequirements('', [], ['E01']), '')
})

test('old story report without issues shows summary once and hides empty details', () => {
  const summary = '两条人物线推进有效，尚有未解悬念。'
  const report = `【故事审校】\r\n${summary}\r\n\r\n【主线依据】\r\n未发现有充分依据的重大主线问题；不代表全文没有细节错误。`
  assert.deepEqual(storyAuditDisplay('story', summary, report), { summary, details: '' })
  assert.deepEqual(storyAuditDisplay('story', '', report, null), { summary, details: '' })
})

test('old story report preserves actual problems and quotes without repeating summary', () => {
  const summary = '关键行动存在矛盾。'
  const details = '1. 钥匙已经毁掉却仍开门。\n故事影响：进城无法成立\n依据：[T01]“钥匙已毁。”'
  const report = `【故事审校】\n${summary}\n\n【主线依据】\n${details}`
  assert.deepEqual(storyAuditDisplay('story', summary, report), { summary, details })
})

test('structured details take precedence, including explicitly empty values', () => {
  assert.deepEqual(storyAuditDisplay('story', '新的总评', '旧报告', '具体引文'), { summary: '新的总评', details: '具体引文' })
  assert.equal(storyAuditDisplay('story', '新的总评', '旧报告', '').details, '')
})

test('legacy consistency and unknown story formats retain their contents', () => {
  const report = '【同章跨场景核对】\n1. 行动顺序不符。\n【前文与证据核对】\n原文引文。'
  assert.equal(storyAuditDisplay('consistency', '', report).details, report)
  assert.equal(storyAuditDisplay('story', '', report).details, report)
  assert.equal(storyAuditDisplay('story', '只有总评', '只有总评').details, '')
})
