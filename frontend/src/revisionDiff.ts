export type DiffPart = { text: string; changed: boolean }
export type DiffRow = {
  kind: 'same' | 'changed' | 'removed' | 'added'
  before: string
  after: string
  beforeParts: DiffPart[]
  afterParts: DiffPart[]
}
export type VisibleRow = DiffRow | { kind: 'collapsed'; count: number }

function paragraphs(text: string): string[] {
  return text.trim().split(/\n\s*\n/).map(part => part.trim()).filter(Boolean)
}

function sentences(text: string): string[] {
  const normalized = text.replace(/\r\n/g, '\n')
  const result: string[] = []
  const boundary = /[。！？!?；;]+[”’"」』]?|\n+/g
  let start = 0
  for (const match of normalized.matchAll(boundary)) {
    const end = (match.index || 0) + match[0].length
    const part = normalized.slice(start, end).trim()
    if (part) result.push(part)
    start = end
  }
  const tail = normalized.slice(start).trim()
  if (tail) result.push(tail)
  return result
}

function bigrams(text: string): Map<string, number> {
  const chars = Array.from(text.replace(/\s+/g, ''))
  const result = new Map<string, number>()
  if (chars.length === 1) result.set(chars[0]!, 1)
  for (let i = 0; i < chars.length - 1; i++) {
    const gram = chars[i]! + chars[i + 1]!
    result.set(gram, (result.get(gram) || 0) + 1)
  }
  return result
}

function similarity(left: Map<string, number>, right: Map<string, number>): number {
  let leftCount = 0; let rightCount = 0; let overlap = 0
  for (const count of left.values()) leftCount += count
  for (const count of right.values()) rightCount += count
  for (const [gram, count] of left) overlap += Math.min(count, right.get(gram) || 0)
  return leftCount + rightCount ? 2 * overlap / (leftCount + rightCount) : 1
}

function addPart(parts: DiffPart[], text: string, changed: boolean) {
  if (!text) return
  const last = parts.at(-1)
  if (last && last.changed === changed) last.text += text
  else parts.push({ text, changed })
}

function inlineParts(before: string, after: string): [DiffPart[], DiffPart[]] {
  const a = Array.from(before); const b = Array.from(after)
  let prefix = 0
  while (prefix < a.length && prefix < b.length && a[prefix] === b[prefix]) prefix++
  let suffix = 0
  while (suffix < a.length - prefix && suffix < b.length - prefix &&
         a[a.length - suffix - 1] === b[b.length - suffix - 1]) suffix++
  const old = a.slice(prefix, a.length - suffix)
  const next = b.slice(prefix, b.length - suffix)
  const beforeParts: DiffPart[] = []; const afterParts: DiffPart[] = []
  const start = a.slice(0, prefix).join(''); const end = a.slice(a.length - suffix).join('')
  addPart(beforeParts, start, false); addPart(afterParts, start, false)
  // Extra-long paragraphs use a bounded fallback so a whole novel cannot freeze the UI.
  if (old.length > 500 || next.length > 500) {
    addPart(beforeParts, old.join(''), true); addPart(afterParts, next.join(''), true)
  } else {
    const dp = Array.from({ length: old.length + 1 }, () => new Uint16Array(next.length + 1))
    for (let i = old.length - 1; i >= 0; i--) for (let j = next.length - 1; j >= 0; j--)
      dp[i]![j] = old[i] === next[j] ? dp[i + 1]![j + 1]! + 1
        : Math.max(dp[i + 1]![j]!, dp[i]![j + 1]!)
    let i = 0; let j = 0
    while (i < old.length || j < next.length) {
      if (i < old.length && j < next.length && old[i] === next[j]) {
        addPart(beforeParts, old[i]!, false); addPart(afterParts, next[j]!, false); i++; j++
      } else if (i < old.length && (j === next.length || dp[i + 1]![j]! >= dp[i]![j + 1]!)) {
        addPart(beforeParts, old[i]!, true); i++
      } else { addPart(afterParts, next[j]!, true); j++ }
    }
  }
  addPart(beforeParts, end, false); addPart(afterParts, end, false)
  return [beforeParts, afterParts]
}

export function compareChapters(currentText: string, revisionText: string): DiffRow[] {
  const currentParagraphs = paragraphs(currentText)
  const revisedParagraphs = paragraphs(revisionText)
  const ratio = Math.max(currentParagraphs.length, revisedParagraphs.length) /
    Math.max(1, Math.min(currentParagraphs.length, revisedParagraphs.length))
  const useSentences = ratio > 2 || [...currentParagraphs, ...revisedParagraphs].some(part => part.length > 700)
  const current = useSentences ? sentences(currentText) : currentParagraphs
  const revised = useSentences ? sentences(revisionText) : revisedParagraphs
  const currentGrams = current.map(bigrams); const revisedGrams = revised.map(bigrams)
  const m = current.length; const n = revised.length
  const dp = Array.from({ length: m + 1 }, () => new Float32Array(n + 1))
  const choice = Array.from({ length: m }, () => new Uint8Array(n))
  for (let i = m - 1; i >= 0; i--) {
    dp[i]![n] = m - i
    for (let j = n - 1; j >= 0; j--) {
      if (i === m - 1) dp[m]![j] = n - j
      let best = 1 + dp[i + 1]![j]!
      let action = 1 // remove
      const add = 1 + dp[i]![j + 1]!
      if (add < best) { best = add; action = 2 }
      const score = current[i] === revised[j] ? 1 : similarity(currentGrams[i]!, revisedGrams[j]!)
      if (score >= 0.32) {
        const match = (current[i] === revised[j] ? 0 : 2 * (1 - score)) + dp[i + 1]![j + 1]!
        if (match <= best) { best = match; action = 3 }
      }
      dp[i]![j] = best; choice[i]![j] = action
    }
  }
  const rows: DiffRow[] = []
  let i = 0; let j = 0
  while (i < m || j < n) {
    if (i < m && j < n && choice[i]![j] === 3) {
      const before = current[i]!; const after = revised[j]!
      const same = before === after
      const [beforeParts, afterParts] = same
        ? [[{ text: before, changed: false }], [{ text: after, changed: false }]]
        : inlineParts(before, after)
      rows.push({ kind: same ? 'same' : 'changed', before, after, beforeParts, afterParts })
      i++; j++
    } else if (i < m && (j === n || choice[i]![j] === 1)) {
      const before = current[i++]!
      rows.push({ kind: 'removed', before, after: '', beforeParts: [{ text: before, changed: true }], afterParts: [] })
    } else {
      const after = revised[j++]!
      rows.push({ kind: 'added', before: '', after, beforeParts: [], afterParts: [{ text: after, changed: true }] })
    }
  }
  return rows
}

export function visibleDiffRows(rows: DiffRow[], showAll = false): VisibleRow[] {
  if (showAll) return rows
  const visible: VisibleRow[] = []
  for (let i = 0; i < rows.length;) {
    if (rows[i]!.kind !== 'same') { visible.push(rows[i]!); i++; continue }
    let end = i
    while (end < rows.length && rows[end]!.kind === 'same') end++
    const count = end - i
    if (count <= 2) visible.push(...rows.slice(i, end))
    else {
      if (i > 0) visible.push(rows[i]!)
      const hidden = count - (i > 0 ? 1 : 0) - (end < rows.length ? 1 : 0)
      if (hidden > 0) visible.push({ kind: 'collapsed', count: hidden })
      if (end < rows.length) visible.push(rows[end - 1]!)
    }
    i = end
  }
  return visible
}
