<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api, writeOptions, type Job } from './api'
import { compareChapters, visibleDiffRows } from './revisionDiff'

type RevisionDraft = { id: string; kind: 'ai_draft' | 'previous_version'; created_at: string;
  candidate: string; source: string; issues: string[]; requirements?: string }
type AuditRecord = { id: string; created_at: string; report: string;
  retrieved_chars: number; previous_context_chars: number; wiki_chars: number }
const props = defineProps<{ projectId: string; chapterNumber: number | null;
  modelName: string; text: string; revision: string; dirty: boolean }>()
const emit = defineEmits<{ task: [id: string]; apply: [text: string] }>()
const requirements = ref('')
const suggestion = ref('')
const sourceAtStart = ref('')
const report = ref('')
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
    const next = selectId && drafts.value.some(item => item.id === selectId)
      ? selectId : drafts.value[0]?.id || ''
    selectedDraft.value = next
    if (next) restoreDraft(next)
  } catch (cause) { error.value = String(cause) }
}

function restoreAudit(id: string) {
  const audit = audits.value.find(item => item.id === id)
  if (!audit) return
  report.value = audit.report || ''
}
async function loadAudits(selectId = '') {
  if (!props.projectId || !props.chapterNumber) return
  try {
    audits.value = await api<AuditRecord[]>(
      `/api/projects/${props.projectId}/chapters/${props.chapterNumber}/audits`)
    const next = selectId && audits.value.some(item => item.id === selectId)
      ? selectId : audits.value[0]?.id || ''
    selectedAudit.value = next
    if (next) restoreAudit(next)
  } catch (cause) { error.value = String(cause) }
}

function payload() { return { number: props.chapterNumber, model_name: props.modelName,
  text: props.text, revision: props.revision, requirements: requirements.value } }
function suggestionEdited() { issues.value = []; error.value = '' }
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
  if (!props.chapterNumber || !props.modelName) return
  running.value = true; error.value = ''
  try { const started = await api<{ id: string }>(`/api/projects/${props.projectId}/consistency`,
    writeOptions('POST', payload()))
    emit('task', started.id)
    await pollJob(started.id, job => {
      report.value = String(job.result?.report || ''); void loadAudits(job.id) }) }
  catch (cause) { running.value = false; error.value = String(cause) }
}
async function revise() {
  if (!props.chapterNumber || !requirements.value.trim()) return
  running.value = true; error.value = ''; suggestion.value = ''; issues.value = []
  showAllDiff.value = false
  sourceAtStart.value = props.text
  try { const started = await api<{ id: string }>(`/api/projects/${props.projectId}/revise`,
    writeOptions('POST', payload()))
    emit('task', started.id)
    await pollJob(started.id, job => { suggestion.value = String(job.result?.candidate || '')
      issues.value = (job.result?.issues || []) as string[]
      feedback.value = issues.value.length ? '建议稿仍有规则告警，请检查后再处理。' : '建议稿已生成，请对照原文复核。'
      void loadDrafts(job.id) }) }
  catch (cause) { running.value = false; error.value = String(cause) }
}
async function apply() {
  if (!suggestion.value || issues.value.length) return
  if (props.text !== sourceAtStart.value) { error.value = '正文在生成建议后发生变化，请重新生成建议。'; return }
  try {
    const checked = await api<{ issues: string[] }>(`/api/projects/${props.projectId}/revise/check`,
      writeOptions('POST', { ...payload(), preview: { candidate: suggestion.value } }))
    issues.value = checked.issues
    if (issues.value.length) { error.value = '当前建议稿未通过应用前校验，请修改后再试。'; return }
  } catch (cause) { error.value = String(cause); return }
  const title = props.text.split(/\r?\n/, 1)[0] || `第${props.chapterNumber}章`
  emit('apply', `${title}\n\n${suggestion.value.trim()}\n`)
  feedback.value = '建议已进入编辑器；请再检查并保存章节。'
}
watch(selectedDraft, value => { if (value) restoreDraft(value) })
watch(selectedAudit, value => { if (value) restoreAudit(value) })
watch(() => [props.projectId, props.chapterNumber], () => {
  suggestion.value = ''; report.value = ''; issues.value = []; showAllDiff.value = false
  drafts.value = []; selectedDraft.value = ''; audits.value = []; selectedAudit.value = ''
  void loadDrafts(); void loadAudits()
}, { immediate: true })
</script>

<template><div class="feature-page"><div class="feature-head"><span class="eyebrow">审校与修订</span><h1>每一处修改，都能回看原句。</h1><p>AI 审校和整章修订在后台运行，结果会保留在运行记录中。</p></div>
  <p v-if="!chapterNumber" class="notice">先从左侧选择章节。</p><p v-if="error" class="notice error" role="alert">{{ error }}</p><p v-if="feedback" class="notice success">{{ feedback }}</p>
  <div class="feature-grid"><section class="panel"><h2>一致性审校</h2>
    <button :disabled="!chapterNumber || !modelName || running" @click="aiCheck">LLM 一致性审校</button>
    <label v-if="audits.length" class="revision-history-picker">历史审校记录<select v-model="selectedAudit">
      <option v-for="audit in audits" :key="audit.id" :value="audit.id">{{ audit.created_at }} · 上一章 {{ audit.previous_context_chars }} 字 · Wiki {{ audit.wiki_chars }} 字</option>
    </select></label>
    <pre v-if="report" class="report">{{ report }}</pre></section>
    <section class="panel"><h2>整章修订</h2><label>修订要求<textarea v-model="requirements" rows="7" placeholder="例如：第一幕只写送站；不要安排调查支线。"></textarea></label>
      <button :disabled="!chapterNumber || !modelName || running || !requirements.trim()" @click="revise">生成整章建议</button>
      <label v-if="drafts.length" class="revision-history-picker">历史修订建议<select v-model="selectedDraft">
        <option v-for="draft in drafts" :key="draft.id" :value="draft.id">{{ draft.created_at }}{{ normalizeForMatch(bodyOnly(text)) === normalizeForMatch(draft.candidate) ? (dirty ? ' · 已应用待保存' : ' · 已保存') : ' · 未应用' }}{{ draft.issues.length ? ' · 有规则提醒' : '' }}</option>
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
        <button :disabled="issues.length > 0 || draftState === 'saved' || draftState === 'pending' || draftState === 'outdated'" @click="apply">{{ draftState === 'saved' ? '已保存到正文' : draftState === 'pending' ? '已应用，等待保存' : '应用到正文编辑器' }}</button></div></section></div>
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
