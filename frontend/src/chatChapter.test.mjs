import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { chapterActions, followsLatest } from './chatChapter.ts'

const state = { number: 12, chapter_exists: false, has_beats: false, beats_count: 0, title: '' }
test('unwritten chapter without a plan offers outline construction, not continuation', () => {
  const actions = chapterActions(state)
  assert.ok(actions.some(a => a.label === '构建大纲' && a.prompt.includes('第 12 章')))
  assert.equal(actions.some(a => a.label === '开始续写'), false)
})
test('unwritten chapter with a plan offers continuation and outline viewing', () => {
  const actions = chapterActions({ ...state, has_beats: true, beats_count: 6 })
  assert.ok(actions.some(a => a.label === '开始续写'))
  assert.ok(actions.some(a => a.view === 'settings'))
})
test('existing chapter offers reading and review, not overwriting generation', () => {
  const actions = chapterActions({ ...state, chapter_exists: true })
  assert.ok(actions.some(a => a.view === 'write'))
  assert.ok(actions.some(a => a.label === '故事审校'))
  assert.equal(actions.some(a => a.label === '开始续写'), false)
})
test('stream follows the transcript bottom but does not pull readers down', () => {
  assert.equal(followsLatest({ scrollHeight: 1400, clientHeight: 400, scrollTop: 980 }), true)
  assert.equal(followsLatest({ scrollHeight: 1400, clientHeight: 400, scrollTop: 100 }), false)
})
test('chat transcript scrolls independently and composer is not an overlay', () => {
  const component = readFileSync(new URL('./ChatPanel.vue', import.meta.url), 'utf8')
  assert.match(component, /ref="transcript"/)
  assert.match(component, /\.chat-transcript\{[^}]*overflow-y:auto/)
  assert.match(component, /\.chat-composer\{position:static/)
  assert.doesNotMatch(component, /window\.scrollTo/)
})
test('chapter shortcuts stay above the composer and outside the scrolling transcript', () => {
  const component = readFileSync(new URL('./ChatPanel.vue', import.meta.url), 'utf8')
  const transcript = component.indexOf('<div ref="transcript"')
  const shortcuts = component.indexOf('<div class="chat-shortcuts"')
  const composer = component.indexOf('<div class="chat-composer"')
  assert.ok(transcript >= 0 && transcript < shortcuts && shortcuts < composer)
  assert.match(component, /<\/article>\s*<\/div>\s*<div class="chat-shortcuts"/)
})
