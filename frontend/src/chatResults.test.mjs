import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resultCard, resultLink } from './chatResults.ts'

test('result cards separate requirements, enhancements, draft and review previews', () => {
  const component = readFileSync(new URL('./ChatPanel.vue',import.meta.url),'utf8')
  assert.match(component,/查看完整审校报告/)
  assert.match(component,/预览修订正文/)
  assert.match(component,/主线修订要求/)
  assert.match(component,/修订验收提醒/)
  assert.match(component,/RevisionApplyDialog v-if="applyingRevision"/)
  const dialog = readFileSync(new URL('./RevisionApplyDialog.vue',import.meta.url),'utf8')
  assert.match(dialog,/应用并保存正文/)
  assert.match(dialog,/apply-revision/)
  assert.doesNotMatch(component,/event.data.result.report \|\| event.data.result.candidate/)
})

test('plan save state is independent from generation completion', () => {
  const data = { tool:'plan',status:'completed',job_id:'j',result:{beats:[{}]} }
  assert.match(resultCard(data).state, /尚未保存/)
  assert.match(resultCard(data,true).state, /已保存/)
})
test('revision actions point to the specific generated version', () => {
  const card = resultCard({ tool:'revise',status:'completed',job_id:'j1' })
  assert.equal(card.actions.length,4)
  for (const action of card.actions.slice(0,3)) {
    assert.equal(new URL(resultLink('p',6,action),'http://local').searchParams.get('revision'),'j1')
  }
  assert.match(card.state,/应用状态请在修订页核对/)
})
test('review imports only actionable requirements and does not execute revision', () => {
  const data = { tool:'consistency',status:'completed',job_id:'j1',result:{} }
  assert.equal(resultCard(data).actions.some(a=>a.label==='导入要求并修订'),false)
  data.result.revision_requirements = '保留结尾，补充转场'
  const action = resultCard(data).actions.find(a=>a.label==='导入要求并修订')
  assert.equal(action.view,'quality')
  assert.equal(action.query.audit,'j1')
})
test('failed tasks offer logs but no save or application actions', () => {
  const card = resultCard({ tool:'revise',status:'failed',job_id:'j1' })
  assert.deepEqual(card.actions.map(a=>a.view),['runs'])
  assert.equal(card.completed,false)
})

test('illustration completion offers preview settings and chapter reading', () => {
  const card = resultCard({tool:'illustration',status:'completed',job_id:'i1'})
  assert.equal(card.title,'章节插图')
  assert.equal(card.state,'插图已保存')
  assert.ok(card.actions.some(a=>a.view==='settings'))
  assert.ok(card.actions.some(a=>a.view==='write'))
})

test('only selected valid enhancements are passed to the matching audit', () => {
  const data = { tool:'consistency',status:'completed',job_id:'audit1',result:{story_enhancements:[{id:'a'},{id:'b'}]} }
  assert.equal(resultCard(data).actions.some(a=>a.label==='导入要求并修订'),false)
  const action = resultCard(data,false,['b','unknown']).actions.find(a=>a.label==='导入要求并修订')
  const params = new URL(resultLink('p',6,action),'http://local').searchParams
  assert.equal(params.get('audit'),'audit1')
  assert.deepEqual(JSON.parse(params.get('enhancements')),['b'])
  assert.equal(params.get('import_requirements'),'1')
})
