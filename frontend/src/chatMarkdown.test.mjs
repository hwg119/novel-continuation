import assert from 'node:assert/strict'
import test from 'node:test'
import { renderChatMarkdown } from './chatMarkdown.ts'

test('renders common chat markdown', () => {
  const html = renderChatMarkdown('**导入新书**\n\n1. 新建工程\n2. 导入 `project.json`\n\n> 操作前请确认')
  assert.match(html, /<strong>导入新书<\/strong>/)
  assert.match(html, /<ol>/)
  assert.match(html, /<code>project\.json<\/code>/)
  assert.match(html, /<blockquote>/)
})

test('does not render raw html or unsafe links', () => {
  const html = renderChatMarkdown('<script>alert(1)</script> [危险](javascript:alert(1))')
  assert.doesNotMatch(html, /<script>/)
  assert.doesNotMatch(html, /href="javascript:/)
})
