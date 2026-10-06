import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { isCompositionEnter } from './chatInput.ts'

test('Enter is not intercepted outside composition', () => {
  assert.equal(isCompositionEnter({ key: 'Enter', isComposing: false, keyCode: 13 }), false)
})

test('new conversation and send use explicit chat button styles', () => {
  const component = readFileSync(new URL('./ChatPanel.vue', import.meta.url), 'utf8')
  assert.match(component, /class="chat-button chat-button-secondary" @click="create"/)
  assert.match(component, /class="chat-button chat-send-button"/)
  assert.match(component, /\.chat-button:disabled/)
  assert.match(component, /\.chat-button:focus-visible/)
})

test('entering the chat page focuses the sender after mounting', () => {
  const component = readFileSync(new URL('./ChatPanel.vue', import.meta.url), 'utf8')
  assert.match(component, /onMounted\(\(\) => \{ void nextTick\(\(\) => sender.value\?\.focus\('last'\)\)/)
})

test('committing a chapter selection returns focus to the chat input', () => {
  const component = readFileSync(new URL('./ChatPanel.vue', import.meta.url), 'utf8')
  assert.match(component, /v-model.number="chapter"[^>]*@change="focusChatInput"/)
  assert.match(component, /function focusChatInput\(\) \{ void nextTick\(\(\) => sender.value\?\.focus\('last'\)\)/)
})

test('conversation management is behind an accessible hover menu', () => {
  const component = readFileSync(new URL('./ChatPanel.vue', import.meta.url), 'utf8')
  assert.match(component, /v-if="sessionMenu === session.id"/)
  assert.match(component, /:aria-expanded="sessionMenu === session.id"/)
  assert.match(component, /\.chat-session-row:hover \.chat-more-trigger/)
  assert.match(component, /\.chat-session-row:focus-within \.chat-more-trigger/)
  assert.match(component, /@media\(hover:none\)/)
  assert.match(component, /await ElMessageBox.prompt\(/)
  assert.match(component, /await ElMessageBox.confirm\(/)
  assert.doesNotMatch(component, /window\.(confirm|prompt)\(/)
})

test('selecting or creating a conversation focuses the sender after loading', () => {
  const component = readFileSync(new URL('./ChatPanel.vue', import.meta.url), 'utf8')
  const choose = component.split('async function choose(id: string)')[1].split('async function create()')[0]
  const create = component.split('async function create()')[1].split('async function refreshList()')[0]
  assert.match(choose, /await nextTick\(\)\s*if \(version === loadVersion\) sender.value\?\.focus\('last'\)/)
  assert.match(create, /await choose\(result.id\)/)
})
test('IME confirmation Enter must not send', () => {
  assert.equal(isCompositionEnter({ key: 'Enter', isComposing: true, keyCode: 13 }), true)
  assert.equal(isCompositionEnter({ key: 'Enter', isComposing: false, keyCode: 229 }), true)
  assert.equal(isCompositionEnter({ key: 'Enter', isComposing: false, keyCode: 13 }, true), true)
  assert.equal(isCompositionEnter({ key: 'a', isComposing: true, keyCode: 229 }, true), false)
})
test('chat actions do not open confirmation modals; traditional pages retain them', () => {
  const component = readFileSync(new URL('./ChatPanel.vue', import.meta.url), 'utf8')
  const send = component.split('async function send()')[1].split('async function confirm(')[0]
  const confirm = component.split('async function confirm(')[1].split('async function stop(')[0]
  assert.equal(send.includes('askConfirm'), false)
  assert.equal(confirm.includes('askConfirm'), false)
  assert.equal(component.includes('askConfirm'), false)
  assert.match(confirm, /actionHandled\(data.action_id\) \|\| confirming.value/)
  const traditional = readFileSync(new URL('./QualityPanel.vue', import.meta.url), 'utf8')
  assert.match(traditional, /await askConfirm\(/)
  assert.match(component, /submit-type="enter"/)
  assert.match(component, /Shift\+Enter 换行/)
  assert.match(component, /@keydown\.capture="guardComposition"/)
})
test('conversation entry is separated from the feature navigation', () => {
  const component = readFileSync(new URL('./App.vue', import.meta.url), 'utf8')
  const groups = component.split('const navGroups:')[1].split('const configNav:')[0]
  assert.doesNotMatch(groups, /id: 'chat'/)
  assert.match(component, /class="conversation-entry"/)
  assert.match(component, /功能工作台/)
})
