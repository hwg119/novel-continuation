export type View = 'home' | 'write' | 'revision' | 'settings' | 'projects' |
  'quality' | 'wiki' | 'relationships' | 'runs' | 'config'
export type RouteState = { view: View; projectId: string; chapterNumber: number | null }

function chapter(value: string | undefined): number | null {
  if (!value || !/^\d+$/.test(value)) return null
  const number = Number(value)
  return Number.isSafeInteger(number) && number > 0 ? number : null
}

export function parseRoute(pathname: string): RouteState | null {
  const parts = pathname.split('/').filter(Boolean)
  if (!parts.length) return { view: 'home', projectId: '', chapterNumber: null }
  if (parts.length === 1 && parts[0] === 'config')
    return { view: 'config', projectId: '', chapterNumber: null }
  if (parts[0] !== 'projects' || !parts[1]) return null
  const projectId = parts[1]
  const base = { projectId, chapterNumber: null }
  if (parts.length === 3) {
    const pages: Record<string, View> = { home: 'home', novel: 'write',
      settings: 'settings', manage: 'projects', quality: 'quality', wiki: 'wiki', runs: 'runs' }
    const view = pages[parts[2]!]
    return view ? { ...base, view } : null
  }
  if (parts.length === 4 && parts[2] === 'wiki' && parts[3] === 'relationships')
    return { ...base, view: 'relationships' }
  if (parts.length === 4 && parts[2] === 'settings') {
    const number = chapter(parts[3])
    return number ? { view: 'settings', projectId, chapterNumber: number } : null
  }
  if (parts[2] === 'chapters') {
    const number = chapter(parts[3])
    if (!number) return null
    if (parts.length === 4) return { view: 'write', projectId, chapterNumber: number }
    if (parts.length === 5 && parts[4] === 'revisions')
      return { view: 'revision', projectId, chapterNumber: number }
    if (parts.length === 5 && parts[4] === 'quality')
      return { view: 'quality', projectId, chapterNumber: number }
  }
  return null
}

export function routePath(view: View, projectId: string, chapterNumber: number | null): string {
  if (view === 'config') return '/config'
  if (!projectId) return '/'
  const base = `/projects/${encodeURIComponent(projectId)}`
  if (view === 'write') return chapterNumber ? `${base}/chapters/${chapterNumber}` : `${base}/novel`
  if (view === 'revision') return chapterNumber ? `${base}/chapters/${chapterNumber}/revisions` : `${base}/novel`
  if (view === 'quality') return chapterNumber ? `${base}/chapters/${chapterNumber}/quality` : `${base}/quality`
  if (view === 'settings') return chapterNumber ? `${base}/settings/${chapterNumber}` : `${base}/settings`
  if (view === 'relationships') return `${base}/wiki/relationships`
  const pages: Record<Exclude<View, 'write' | 'revision' | 'quality' | 'settings' | 'config' | 'relationships'>, string> = {
    home: 'home', projects: 'manage', wiki: 'wiki', runs: 'runs',
  }
  return `${base}/${pages[view]}`
}
