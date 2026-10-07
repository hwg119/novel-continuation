<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api, writeOptions, type Job } from './api'
import { compareChapters, visibleDiffRows } from './revisionDiff'
import { askConfirm } from './confirmService'
import { importStoryRequirements, storyAuditDisplay, type StoryEnhancement } from './storyReview'

type RevisionDraft = { id: string; kind: 'ai_draft' | 'previous_version'; created_at: string;
  candidate: string; source: string; issues: string[]; requirements?: string; revision_mode?: string }
type AuditRecord = { id: string; created_at: string; report: string;
  revision_requirements?: string; audit_notes?: string[]; audit_kind?: string;
  story_enhancements?: StoryEnhancement[];
  story_summary?: string;
  story_details?: string | null;
  retrieved_chars: number; previous_context_chars: number; wiki_chars: number }
const props = defineProps<{ projectId: string; chapterNumber: number | null;
  modelName: string; text: string; revision: string; dirty: boolean }>()
const emit = defineEmits<{ task: [id: string]; apply: [text: string] }>()
const requirements = ref('')
const revisionMode = ref('local')
const polishing = computed(() => revisionMode.value === 'polish')
function revisionModeChanged() { if (polishing.value) requirements.value = '' }
const suggestion = ref('')
const sourceAtStart = ref('')
const report = ref('')
const auditRequirements = ref('')
const auditNotes = ref<string[]>([])
const enhancements = ref<StoryEnhancement[]>([])
const selectedEnhancements = ref<string[]>([])
const auditKind = ref('story')
const storySummary = ref('')
const storyDetails = ref<string | null | undefined>(undefined)
const auditDisplay = computed(() => storyAuditDisplay(
  auditKind.value, storySummary.value, report.value, storyDetails.value))
const importRequirements = computed(() => importStoryRequirements(
  auditRequirements.value, enhancements.value, selectedEnhancements.value))
const issues = ref<string[]>([])
const error = ref('')
const feedback = ref('')
const running = ref(false)
const showDiff = ref(false)
const showAllDiff = ref(false)
const drafts = ref<RevisionDraft[]>([])
const selectedDraft = ref('')
const audits = ref<AuditRecord[]>([])
const selectedAudit = ref('')
const sourceBody = computed(() => sourceAtStart.value.replace(/^[^\n]*\n\s*/, ''))
const diffRows = computed(() => suggestion.value
  ? compareChapters(sourceBody.value, suggestion.value) : [])
const displayedDiffRows = computed(() => visibleDiffRows(diffRows.value, showAllDiff.value))
const changeCount = computed(() => diffRows.value.filter(row => row.kind !== 'same').length)
const selectedRecord = computed(() => drafts.value.find(item => item.id === selectedDraft.value))
const suggestionModified = computed(() => Boolean(selectedRecord.value) &&
  normalizeForMatch(suggestion.value) !== normalizeForMatch(selectedRecord.value?.candidate || ''))
const draftState = computed<'edited' | 'saved' | 'pending' | 'ready' | 'outdated'>(() => {
  if (suggestionModified.value) return 'edited'
  const draft = selectedRecord.value
  if (!draft) return 'ready'
  const current = normalizeForMatch(bodyOnly(props.text))
  const candidate = normalizeForMatch(draft.candidate)
  if (current && current === candidate) return props.dirty ? 'pending' : 'saved'
  if (!draft.source || current === normalizeForMatch(draft.source)) return 'ready'
  return 'outdated'
})
const draftStateLabel = computed(() => ({
  edited: '建议已手工调整', saved: '已保存为当前正文', pending: '已应用，等待保存',
  ready: '尚未应用', outdated: '正文已变化',
})[draftState.value])

function bodyOnly(value: string) { return value.replace(/^[^\n]*\n\s*/, '').trim() }
function normalizeForMatch(value: string) { return value.replace(/\s+/g, '') }
function restoreDraft(id: string) {
  const draft = drafts.value.find(item => item.id === id)
  if (!draft) return
  suggestion.value = draft.candidate || ''
  issues.value = draft.issues || []
  revisionMode.value = draft.revision_mode || 'whole'
  if (draft.requirements) requirements.value = draft.requirements
  const currentBody = bodyOnly(props.text)
  if (!draft.source || normalizeForMatch(currentBody) === normalizeForMatch(draft.source)) {
    sourceAtStart.value = props.text
  } else {
    const title = props.text.split(/\r?\n/, 1)[0] || `第${props.chapterNumber}章`
    sourceAtStart.value = `${title}\n\n${draft.source.trim()}\n`
  }
  showDiff.value = true; showAllDiff.value = false
  feedback.value = sourceAtStart.value === props.text
    ? '已恢复历史修订建议，应用状态见下方标记。'
    : '已恢复历史修订建议；当前正文已变化，只可比较，不可直接应用。'
}
async function loadDrafts(selectId = '') {
  if (!props.projectId || !props.chapterNumber) return
  try {
    const records = await api<RevisionDraft[]>(
      `/api/projects/${props.projectId}/chapters/${props.chapterNumber}/revisions`)
    drafts.value = records.filter(item => item.kind === 'ai_draft')
    const params = new URLSearchParams(window.location.search)
    const requested = selectId || params.get('revision') || ''
    const importingAudit = !!params.get('audit') && params.get('import_requirements') === '1'
    const next = requested ? drafts.value.find(item => item.id === requested)?.id || '' : importingAudit ? '' : drafts.value[0]?.id || ''
    if (requested && !next) feedback.value = '指定修订稿不存在或尚未完成，请从历史记录选择；没有自动切换到其他版本。'
    selectedDraft.value = next
    if (next) restoreDraft(next)
  } catch (cause) { error.value = String(cause) }
}

function restoreAudit(id: string) {
  const audit = audits.value.find(item => item.id === id)
  if (!audit) return
  report.value = audit.report || ''
  auditRequirements.value = audit.revision_requirements || ''
  auditNotes.value = audit.audit_notes || []
  enhancements.value = audit.story_enhancements || []
  selectedEnhancements.value = []
  auditKind.value = audit.audit_kind || 'consistency'
  storySummary.value = audit.story_summary || ''
  storyDetails.value = audit.story_details
}
async function loadAudits(selectId = '') {
  if (!props.projectId || !props.chapterNumber) return
  try {
    audits.value = await api<AuditRecord[]>(
      `/api/projects/${props.projectId}/chapters/${props.chapterNumber}/audits`)
    const params = new URLSearchParams(window.location.search)
    const requested = selectId || params.get('audit') || ''
    const next = requested ? audits.value.find(item => item.id === requested)?.id || '' : audits.value[0]?.id || ''
    if (requested && !next) feedback.value = '指定审校记录不存在或尚未完成，请从历史记录选择。'
    selectedAudit.value = next
    if (next) {
      restoreAudit(next)
      if (params.get('audit') === next && params.get('import_requirements') === '1') {
        try {
          const chosen = JSON.parse(params.get('enhancements') || '[]')
          if (Array.isArray(chosen)) selectedEnhancements.value = enhancements.value
            .filter(item => chosen.includes(item.id)).map(item => item.id)
        } catch { selectedEnhancements.value = [] }
        useAuditRequirements()
      }
    }
  } catch (cause) { error.value = String(cause) }
}

function payload() { return { number: props.chapterNumber, model_name: props.modelName,
  text: props.text, revision: props.revision, requirements: requirements.value, revision_mode: revisionMode.value } }
function suggestionEdited() { issues.value = []; error.value = '' }
function useAuditRequirements() {
  const currentReport = importRequirements.value
  if (!currentReport || running.value) return
  revisionMode.value = 'local'
  requirements.value = currentReport
  feedback.value = '已覆盖导入主线修订与选中的情节增强；未选中的意见和待核对提醒未带入。'
}
async function pollJob(id: string, done: (job: Job) => void) {
  const check = async () => {
    try { const job = await api<Job>(`/api/projects/${props.projectId}/jobs/${id}`)
      if (job.status === 'running') { setTimeout(check, 1500); return }
      running.value = false
      if (job.status !== 'completed') { error.value = job.message; return }
      done(job)
    } catch (cause) { running.value = false; error.value = String(cause) }
  }
  void check()
}
async function aiCheck() {
  if (!props.chapterNumber || !props.modelName || running.value) return
  if (!await askConfirm({ title: `审校第 ${props.chapterNumber} 章？`,
    message: '将整体阅读当前正文（包括未保存的修改），检查影响主线与后续发展的重大逻辑问题，并提出可选情节增强。不读取尚未应用的修订稿，不逐项纠缠细节。',
    symbol: '审', confirmLabel: '使用此模型审校', modelName: props.modelName })) return
  running.value = true; error.value = ''
  try { const started = await api<{ id: string }>(`/api/projects/${props.projectId}/consistency`,
    writeOptions('POST', payload()))
    emit('task', started.id)
    await pollJob(started.id, job => {
      report.value = String(job.result?.report || '')
      auditRequirements.value = String(job.result?.revision_requirements || '')
      auditNotes.value = (job.result?.audit_notes || []) as string[]
      enhancements.value = (job.result?.story_enhancements || []) as StoryEnhancement[]
      selectedEnhancements.value = []; auditKind.value = String(job.result?.audit_kind || 'story')
      storySummary.value = String(job.result?.story_summary || '')
      storyDetails.value = job.result?.story_details as string | null | undefined
      void loadAudits(job.id) }) }
  catch (cause) { running.value = false; error.value = String(cause) }
}
async function revise() {
  if (!props.chapterNumber || (!polishing.value && !requirements.value.trim()) || running.value || !props.modelName) return
  if (!await askConfirm({ title: `${polishing.value ? '润色' : '修订'}第 ${props.chapterNumber} 章？`,
    message: polishing.value ? '将使用表达润色 Skill 处理当前正文（包括未保存编辑），不读取尚未应用的修订稿。保留剧情、线索和人物声音；生成候选稿并对照原文验收，不自动覆盖，也不判断作者是否为 AI。' : revisionMode.value === 'local' ? '将使用修订 Skill 修改必要段落，可落实已导入的情节增强；仅验收输入框中的要求，不另加建议，不自动重试。' : '将使用修订 Skill 重写完整章节，落实输入框中的纠错或增强要求；仅验收这些要求，不另加建议，不自动重试。',
    symbol: '修', confirmLabel: polishing.value ? '使用此模型润色' : '使用此模型修订', modelName: props.modelName })) return
  running.value = true; error.value = ''; suggestion.value = ''; issues.value = []
  showAllDiff.value = false
  sourceAtStart.value = props.text
  try { const started = await api<{ id: string }>(`/api/projects/${props.projectId}/revise`,
    writeOptions('POST', payload()))
    emit('task', started.id)
    await pollJob(started.id, job => { suggestion.value = String(job.result?.candidate || '')
      requirements.value = String(job.result?.requirements || requirements.value)
      issues.value = (job.result?.issues || []) as string[]
      feedback.value = issues.value.length ? '建议稿仍有规则告警，请检查后再处理。' : '建议稿已生成，请对照原文复核。'
      void loadDrafts(job.id) }) }
  catch (cause) { running.value = false; error.value = String(cause) }
}
async function apply() {
  if (!suggestion.value) return
  if (props.text !== sourceAtStart.value) { error.value = '正文在生成建议后发生变化，请重新生成建议。'; return }
  if (issues.value.length && !await askConfirm({ title: '仍有验收提醒，确认应用？',
    message: '模型提醒可能包含误判。请先核对下方提醒；确认后仅填入正文编辑器，不直接保存章节。',
    symbol: '核', confirmLabel: '已核对，应用修订稿' })) return
  try {
    const checked = await api<{ issues: string[] }>(`/api/projects/${props.projectId}/revise/check`,
      writeOptions('POST', { ...payload(), preview: { candidate: suggestion.value } }))
    issues.value = checked.issues
    if (issues.value.length) { error.value = '当前建议稿未通过应用前校验，请修改后再试。'; return }
  } catch (cause) { error.value = String(cause); return }
  const title = props.text.split(/\r?\n/, 1)[0] || `第${props.chapterNumber}章`
  emit('apply', `${title}\n\n${suggestion.value.trim()}\n`)
  feedback.value = '修订稿已应用到正文编辑器，尚未保存；现在可点击故事审校检查修订后的全文。请记得保存章节。'
}
watch(selectedDraft, value => { if (value) restoreDraft(value) })
watch(selectedAudit, value => { if (value) restoreAudit(value) })
watch(() => [props.projectId, props.chapterNumber], () => {
  suggestion.value = ''; report.value = ''; auditRequirements.value = ''; auditNotes.value = []; issues.value = []; showAllDiff.value = false
  drafts.value = []; selectedDraft.value = ''; audits.value = []; selectedAudit.value = ''
  enhancements.value = []; selectedEnhancements.value = []; auditKind.value = 'story'
  storySummary.value = ''
  storyDetails.value = undefined
  void loadDrafts(); void loadAudits()
}, { immediate: true })
</script>

<template><div class="feature-page"><div class="feature-head"><span class="eyebrow">审校与修订{{ chapterNumber ? ` · 第 ${chapterNumber} 章` : '' }}</span><h1>{{ chapterNumber ? `第 ${chapterNumber} 章 · 审校与修订` : '让故事连贯，也让情节更有分量。' }}</h1><p>关注重大逻辑与故事连续性，提供可选的情节增强。审校和修订结果均保留在运行记录中。</p></div>
  <p v-if="!chapterNumber" class="notice">先从左侧选择章节。</p><p v-if="error" class="notice error" role="alert">{{ error }}</p><p v-if="feedback" class="notice success">{{ feedback }}</p>
  <div class="feature-grid"><section class="panel"><h2>审校</h2>
    <button :disabled="!chapterNumber || !modelName || running" @click="aiCheck">故事审校</button>
    <label v-if="audits.length" class="revision-history-picker">历史故事审校<select v-model="selectedAudit">
      <option v-for="audit in audits" :key="audit.id" :value="audit.id">{{ audit.created_at }}{{ audit.audit_kind !== 'story' ? ' · 旧版一致性记录' : '' }} · 上一章 {{ audit.previous_context_chars }} 字 · Wiki {{ audit.wiki_chars }} 字</option>
    </select></label>
    <template v-if="report"><p v-if="auditDisplay.summary" class="story-summary">{{ auditDisplay.summary }}</p><h3>{{ auditKind === 'story' ? '主线修订' : '旧版修改要求' }}</h3><pre v-if="auditRequirements" class="report">{{ auditRequirements }}</pre><p v-else class="muted-note">{{ auditKind === 'story' ? '本轮未发现有充分依据的重大主线问题，不代表全文没有细节错误。' : '旧记录没有可导入的简短要求，可重新进行故事审校。' }}</p>
    <div class="story-enhancements"><header><h3>情节增强</h3><span>可选 · 默认不执行</span></header>
      <p v-if="!enhancements.length" class="muted-note">{{ auditKind === 'story' ? '本轮没有提出可选增强，不为扩写而扩写。' : '旧版记录不包含情节增强，重新审校后可查看。' }}</p>
      <article v-for="item in enhancements" :key="item.id" class="story-enhancement" :class="{ selected: selectedEnhancements.includes(item.id) }">
        <label class="check"><input v-model="selectedEnhancements" type="checkbox" :value="item.id" :disabled="running"><strong>{{ item.scope }}</strong></label>
        <p>{{ item.suggestion }}</p><p class="story-preserve"><b>保留</b>{{ item.preserve }}</p>
        <details><summary>查看对应原文</summary><blockquote v-for="(cite, index) in item.citations" :key="index">{{ cite.quote }}</blockquote></details>
      </article>
    </div>
    <details v-if="auditNotes.length"><summary>待核对提醒（{{ auditNotes.length }} 项，不自动修订）</summary><ul><li v-for="(note, index) in auditNotes" :key="index">{{ note }}</li></ul></details><details v-if="auditDisplay.details"><summary>查看详细审校依据</summary><pre class="report">{{ auditDisplay.details }}</pre></details></template></section>
    <section class="panel"><h2>修订</h2><label>修订方式<select v-model="revisionMode" :disabled="running" @change="revisionModeChanged"><option value="local">局部修补（推荐）</option><option value="whole">整章重写 · 情节或风格大调整</option><option value="polish">表达润色 · 去 AI 味（可选）</option></select></label>
      <button type="button" class="subtle" :disabled="running || !importRequirements" :title="importRequirements ? '导入主线修订与勾选的情节增强，覆盖输入框已有内容' : '先进行故事审校或勾选情节增强意见'" @click="useAuditRequirements">一键导入修订要求</button>
      <p class="muted-note">{{ polishing ? '只润色当前正文的表达，不改变剧情。修订稿须先应用到正文编辑器才能继续润色；不会自动追加模型调用。' : '仅导入主线要求与勾选的增强意见；需要增删段落或重构场景时，选择整章重写。' }}</p>
      <label>{{ polishing ? '额外表达偏好（可不填）' : '修订要求' }}<textarea v-model="requirements" rows="7" :placeholder="polishing ? '例如保留含蓄口吻，减少重复解释；不填写则按润色 Skill 执行。' : '简短说明改什么、保留什么；一键导入会覆盖已有内容。'"></textarea></label>
      <button :disabled="!chapterNumber || !modelName || running || (!polishing && !requirements.trim())" @click="revise">{{ running ? '处理中…' : polishing ? '执行表达润色' : '执行修订' }}</button>
      <label v-if="drafts.length" class="revision-history-picker">历史修订建议<select v-model="selectedDraft">
        <option v-for="draft in drafts" :key="draft.id" :value="draft.id">{{ draft.created_at }}{{ draft.revision_mode === 'polish' ? ' · 表达润色' : '' }}{{ normalizeForMatch(bodyOnly(text)) === normalizeForMatch(draft.candidate) ? (dirty ? ' · 已应用待保存' : ' · 已保存') : ' · 未应用' }}{{ draft.issues.length ? ' · 有规则提醒' : '' }}</option>
      </select></label>
      <div v-if="suggestion" class="revision-apply-state" :class="draftState"><span aria-hidden="true"></span><div><strong>{{ draftStateLabel }}</strong>
        <small v-if="draftState === 'pending'">返回正文页检查后，点击“保存正文”才会写入章节文件。</small>
        <small v-else-if="draftState === 'saved'">当前章节文件与这份修订建议一致。</small>
        <small v-else-if="draftState === 'outdated'">生成建议后正文已发生变化，只可比较，不能直接覆盖。</small>
        <small v-else-if="draftState === 'edited'">当前建议与历史记录不同，应用后将采用这里的编辑版本。</small>
        <small v-else>这份建议尚未进入正文编辑器。</small></div></div>
      <p v-if="issues.length" class="issue-list">{{ issues.join('；') }}</p>
      <div v-if="suggestion"><label>建议稿（可修改）<textarea v-model="suggestion" rows="16" @input="suggestionEdited"></textarea></label>
        <button class="subtle" @click="showDiff = !showDiff">{{ showDiff ? '隐藏对照' : '查看原文对照' }}</button>
        <button :disabled="running || draftState === 'saved' || draftState === 'pending' || draftState === 'outdated'" @click="apply">{{ draftState === 'saved' ? '已保存到正文' : draftState === 'pending' ? '已应用，等待保存' : issues.length ? '核对提醒后应用' : '应用到正文编辑器' }}</button></div></section></div>
  <section v-if="showDiff && suggestion" class="quality-diff"><div class="quality-diff-head"><div><span class="eyebrow">文字校样</span><h2>当前正文 → 修订建议</h2></div>
    <div><span>{{ changeCount }} 处变动</span><label class="check"><input v-model="showAllDiff" type="checkbox">显示全部未改内容</label></div></div>
    <div class="revision-comparison quality-diff-grid"><div class="revision-column-head"><i class="diff-key removed-key"></i>当前正文</div><div class="revision-column-head"><i class="diff-key added-key"></i>修订建议</div>
      <template v-for="(row, index) in displayedDiffRows" :key="index">
        <div v-if="row.kind === 'collapsed'" class="revision-collapsed">已收起 {{ row.count }} 段未改动内容 <button @click="showAllDiff = true">展开全文</button></div>
        <template v-else><div class="revision-cell" :class="row.kind"><span v-for="(part, partIndex) in row.beforeParts" :key="partIndex" :class="{ 'diff-removed': part.changed }">{{ part.text }}</span></div>
          <div class="revision-cell" :class="row.kind"><span v-for="(part, partIndex) in row.afterParts" :key="partIndex" :class="{ 'diff-added': part.changed }">{{ part.text }}</span></div></template>
      </template>
    </div></section>
</div></template>
