/** Extract only the recognized leading metadata, preserving all other prose. */
export function beatPreview(beat: { num?: string | number; name?: string; desc?: string }, index: number) {
  const desc = String(beat.desc || '')
  const header = desc.match(/^\s*【视角[：:]\s*(.*?)\s*[｜|]\s*时间[：:]\s*(.*?)\s*[｜|]\s*地点[：:]\s*(.*?)】\s*/)
  const body = header ? desc.slice(header[0].length) : desc
  return { key: index, number: index + 1, name: beat.name || '未命名分幕',
    meta: header ? [{ label: '视角', value: header[1] }, { label: '时间', value: header[2] }, { label: '地点', value: header[3] }] : [],
    paragraphs: body.split(/\r?\n\s*\r?\n|\r?\n/).filter(line => line.trim()) }
}
