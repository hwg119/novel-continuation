import test from 'node:test'
import assert from 'node:assert/strict'
import { beatPreview } from './beatPreview.ts'

test('metadata is extracted without changing the scene body', () => {
  const view = beatPreview({ name:'交班之后', desc:'【视角：林澈｜时间：傍晚｜地点：旧港】第一段。\n\n第二段。' },0)
  assert.equal(view.number,1)
  assert.deepEqual(view.meta.map(m=>m.value),['林澈','傍晚','旧港'])
  assert.deepEqual(view.paragraphs,['第一段。','第二段。'])
})
test('unstructured and partial headers remain visible without lost content', () => {
  const desc = '【视角：林澈】调查开始。'
  assert.deepEqual(beatPreview({desc},2).paragraphs,[desc])
  assert.equal(beatPreview({desc},2).number,3)
  assert.deepEqual(beatPreview({},0).paragraphs,[])
})
