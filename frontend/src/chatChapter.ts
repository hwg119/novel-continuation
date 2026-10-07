export type ChatChapterState = { number: number; chapter_exists: boolean; has_beats: boolean; beats_count: number; title: string }
export function followsLatest(panel: { scrollHeight: number; scrollTop: number; clientHeight: number } | undefined) {
  return !panel || panel.scrollHeight - panel.scrollTop - panel.clientHeight < 100
}
export function chapterActions(state: ChatChapterState) {
  if (state.chapter_exists) return [
    { label: '查看正文', view: 'write', prompt: '' },
    { label: '故事审校', view: '', prompt: `请对第 ${state.number} 章执行故事审校，重点检查主线连续性，并提供可选的情节增强意见。` },
    { label: '生成插图', view: '', prompt: `请为第 ${state.number} 章生成插图，使用工程设置的画风。` },
    { label: '讨论改进方向', view: '', prompt: `请分析第 ${state.number} 章的情节和节奏，给出改进建议，先不要修改正文。` },
  ]
  if (state.has_beats) return [
    { label: '查看大纲', view: 'settings', prompt: '' },
    { label: '开始续写', view: '', prompt: `请根据已保存的分幕大纲续写第 ${state.number} 章。` },
    { label: '生成插图', view: '', prompt: `请根据第 ${state.number} 章已保存的分幕生成插图，使用工程设置的画风。` },
    { label: '讨论大纲', view: '', prompt: `请评价第 ${state.number} 章已有分幕大纲，提出优化意见，先不要修改。` },
  ]
  return [
    { label: '构建大纲', view: '', prompt: `请为第 ${state.number} 章构建大纲，生成回目和分幕，承接前文推进故事。` },
    { label: '讨论续写方向', view: '', prompt: `请结合前文讨论第 ${state.number} 章可以怎样推进，先给建议，不执行生成。` },
  ]
}
