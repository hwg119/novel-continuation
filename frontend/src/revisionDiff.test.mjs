import test from 'node:test'
import assert from 'node:assert/strict'
import { compareChapters, visibleDiffRows } from './revisionDiff.ts'

test('small Chinese edit is paired and highlighted inside a paragraph', () => {
  const rows = compareChapters('林舟望向窗外。', '林舟望向门外。')
  assert.equal(rows.length, 1)
  assert.equal(rows[0].kind, 'changed')
  assert.equal(rows[0].beforeParts.filter(part => part.changed).map(part => part.text).join(''), '窗')
  assert.equal(rows[0].afterParts.filter(part => part.changed).map(part => part.text).join(''), '门')
})

test('insertions and removals remain separate from aligned edits', () => {
  const rows = compareChapters('第一段原文。\n\n林舟望向窗外。\n\n最后一段。',
    '新加的开场。\n\n第一段原文。\n\n林舟望向门外。\n\n最后一段。')
  assert.deepEqual(rows.map(row => row.kind), ['added', 'same', 'changed', 'same'])
})

test('unrelated paragraphs are not forced into a changed pair', () => {
  const rows = compareChapters('城中档案室的旧卷宗。', '清晨厨房里的苹果派。')
  assert.equal(rows.length, 2)
  assert.ok(rows.some(row => row.kind === 'removed'))
  assert.ok(rows.some(row => row.kind === 'added'))
})

test('unchanged runs collapse by default and expand on request', () => {
  const rows = compareChapters('一。\n\n二。\n\n三。\n\n四。\n\n五。\n\n旧结尾。',
    '一。\n\n二。\n\n三。\n\n四。\n\n五。\n\n新结尾。')
  assert.ok(visibleDiffRows(rows).some(row => row.kind === 'collapsed'))
  assert.equal(visibleDiffRows(rows, true).length, rows.length)
})

test('paragraph-vs-sentence formatting does not turn the chapter into mass additions', () => {
  const dense = '林舟走进办公室。他打开旧卷宗。陈默敲了敲门。苏晴带来了新线索。'
  const spaced = '林舟走进办公室。\n\n他打开旧卷宗。\n\n陈默敲了敲门。\n\n苏晴带来了新线索。'
  const rows = compareChapters(dense, spaced)
  assert.equal(rows.filter(row => row.kind !== 'same').length, 0)
})
