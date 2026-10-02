import test from 'node:test'
import assert from 'node:assert/strict'
import { parseRoute, routePath } from './routeState.ts'

test('each workbench page has a stable project route', () => {
  const cases = [
    ['home', '/projects/abc/home'],
    ['write', '/projects/abc/chapters/200'],
    ['revision', '/projects/abc/chapters/200/revisions'],
    ['settings', '/projects/abc/settings/200'],
    ['projects', '/projects/abc/manage'],
    ['quality', '/projects/abc/chapters/200/quality'],
    ['wiki', '/projects/abc/wiki'],
    ['runs', '/projects/abc/runs'],
  ]
  for (const [view, path] of cases) {
    assert.equal(routePath(view, 'abc', 200), path)
    assert.equal(parseRoute(path).view, view)
    assert.equal(parseRoute(path).projectId, 'abc')
  }
})

test('routes without a selected chapter and global config restore correctly', () => {
  assert.deepEqual(parseRoute('/'), { view: 'home', projectId: '', chapterNumber: null })
  assert.deepEqual(parseRoute('/config'), { view: 'config', projectId: '', chapterNumber: null })
  assert.deepEqual(parseRoute('/projects/abc/novel'),
    { view: 'write', projectId: 'abc', chapterNumber: null })
  assert.deepEqual(parseRoute('/projects/abc/quality'),
    { view: 'quality', projectId: 'abc', chapterNumber: null })
  assert.equal(routePath('config', 'abc', 200), '/config')
})

test('invalid chapter routes are rejected', () => {
  for (const path of ['/projects/abc/chapters/0', '/projects/abc/chapters/no',
    '/projects/abc/settings/-1', '/projects/abc/chapters/2/other']) {
    assert.equal(parseRoute(path), null)
  }
})
