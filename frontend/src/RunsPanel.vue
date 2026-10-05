<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api, writeOptions, type Job } from './api'
import { askConfirm } from './confirmService'

type GenerationLog = { id: string; chapter: number | null; title: string; created_at: string; events_count: number }
type LogEvent = Record<string, unknown>
type RecordItem = { id: string; type: 'job' | 'log'; title: string; created_at: string; status: string }
const props = defineProps<{ projectId: string; focusJob: string }>()
const jobs = ref<Job[]>([])
const logs = ref<GenerationLog[]>([])
const selected = ref<RecordItem | null>(null)
const logEvents = ref<LogEvent[]>([])
const wikiEntries = ref<LogEvent[]>([])
const wikiLoading = ref(false)
const wikiOpened = ref(false)
const planOpened = ref(false)
const error = ref('')
const resuming = ref(false)
const savedPlanJob = ref('')
let timer: ReturnType<typeof setInterval> | null = null

const jobTitles: Record<string, string> = {
  generate: '章节续写', revise: '整章修订', consistency: '故事审校',
  vectors: '构建向量库', chapter_vector: '更新章节向量', corpus: '导入母本', wiki: '编纂 Wiki',
  plan: '章节规划', plan_revision: '按问题修订分幕',
  book_rules: '全书规则提取', model_test: '模型连接测试',
  illustration: '生成章节插图',
  learning: '生成英语学习版',
}
const eventTitles: Record<string, string> = {
  run_started: '开始续写', summary_filled: '补齐前章摘要', retrieval_completed: '完成母本检索',
  beat_completed: '完成分幕', beat_failed: '分幕失败', run_failed: '续写失败',
  summary_created: '生成本章摘要', run_completed: '续写完成',
}
const fieldTitles: Record<string, string> = {
  chapter_number: '章号', chapter_title: '回目', model: '模型', temperature: '温度',
  beats: '幕数', beat_index: '幕序', beat_name: '幕名', chars: '字数',
  generated_chars: '本幕字数', total_chars: '累计字数', elapsed_ms: '耗时（毫秒）',
  dropped_duplicates: '去重段落', retrieved_chars: '检索字数', summary_chars: '摘要字数',
  quality_warnings: '质量提醒', error: '错误', segments: '分段数', processed_chapters: '处理章节数',
  path: '文件路径', run_log_path: '生成日志', reply: '测试回复', dimensions: '向量维度',
  action: '处理方式', chapter_segments: '本章分段', replaced_chapters: '替换章节',
  appended_chapters: '追加章节',
  context: '实际注入的 Wiki 证据包', mentioned_entities: '识别实体',
  candidate_count: '相关候选', selected_count: '最终采用', selected_chars: '注入字数',
  dropped_old_state: '淘汰旧状态', dropped_unrelated: '淘汰无关实体',
  dropped_low_relevance: '淘汰低相关事实', dropped_source_history: '淘汰母本历史状态',
  continuation_start_chapter: '续写起始章', facts: '采用事实明细',
}
const records = computed<RecordItem[]>(() => [
  ...jobs.value.map(job => ({ id: job.id, type: 'job' as const,
    title: jobTitles[job.kind] || job.kind, created_at: job.created_at || '', status: job.status })),
  ...logs.value.map(log => ({ id: log.id, type: 'log' as const,
    title: `第 ${log.chapter ?? '？'} 章 · ${log.title || '续写日志'}`,
    created_at: log.created_at, status: `${log.events_count} 条事件` })),
].sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id)))
const selectedJob = computed(() => selected.value?.type === 'job'
  ? jobs.value.find(job => job.id === selected.value?.id) : undefined)
const resumeModel = computed(() => String(selectedJob.value?.events.find(event => event.data?.workflow_thread)?.data?.model_name || ''))
async function resumeRevision() {
  const job = selectedJob.value
  if (!job || resuming.value || !resumeModel.value) return
  const isPlan = job.kind === 'plan'
  if (!await askConfirm({ title: isPlan ? '继续中断的规划任务？' : '继续中断的修订任务？', message: '从已保存的检查点继续，已完成的阶段不重复调用模型；未完成的调用可能重新执行。恢复的规划需在完成后确认保存到续写设定。', modelName: resumeModel.value, confirmLabel: isPlan ? '继续规划' : '继续修订', symbol: isPlan ? '纲' : '修' })) return
  resuming.value = true
  try {
    const route = isPlan ? `plan/jobs/${job.id}/resume` : `revisions/${job.id}/resume`
    const started = await api<{ id: string }>(`/api/projects/${props.projectId}/${route}`,
      writeOptions('POST', { model_name: resumeModel.value }))
    await refresh()
    const record = records.value.find(item => item.id === started.id)
    if (record) await choose(record)
  } catch (cause) { error.value = String(cause) }
  finally { resuming.value = false }
}
async function saveRecoveredPlan() {
  const job = selectedJob.value
  if (!job || resuming.value) return
  if (!await askConfirm({ title: '保存恢复的规划？', message: '将本次回目和分幕写入对应章节的续写设定，覆盖该章原规划，不修改小说正文。', confirmLabel: '确认保存', symbol: '纲' })) return
  resuming.value = true
  try {
    await api(`/api/projects/${props.projectId}/plan/jobs/${job.id}/apply`, writeOptions('POST', {}))
    savedPlanJob.value = job.id
    error.value = ''
  } catch (cause) { error.value = String(cause) }
  finally { resuming.value = false }
}
function displayTime(value: string) { return value.replace('T', ' ') || '时间未知' }
function label(key: string) { return fieldTitles[key] || key.replaceAll('_', ' ') }
function valueText(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (Array.isArray(value)) return value.length ? value.map(valueText).join('；') : '无'
  if (typeof value === 'object') return Object.entries(value).map(([key, item]) =>
    `${label(key)}：${valueText(item)}`).join('；') || '无'
  return String(value)
}
function detailFields(item: Record<string, unknown>, excluded: string[] = []) {
  return Object.entries(item).filter(([key]) => !excluded.includes(key))
    .map(([key, value]) => ({ key, label: label(key), value: valueText(value) }))
}
async function choose(record: RecordItem) {
  selected.value = record; logEvents.value = []; wikiEntries.value = []; wikiOpened.value = false; planOpened.value = false; error.value = ''
  if (record.type !== 'log') return
  try {
    const result = await api<{ events: LogEvent[] }>(
      `/api/projects/${props.projectId}/logs/${encodeURIComponent(record.id)}`)
    if (selected.value?.id === record.id) logEvents.value = result.events
  } catch (cause) { error.value = String(cause) }
}
async function loadWikiDetails() {
  if (!props.projectId || !selected.value || selected.value.type !== 'job') return
  wikiLoading.value = true; error.value = ''
  try {
    const id = selected.value.id
    const result = await api<{ entries: LogEvent[] }>(
      `/api/projects/${props.projectId}/wiki/logs/${encodeURIComponent(id)}`)
    if (selected.value?.id === id) { wikiEntries.value = result.entries; wikiOpened.value = true }
  } catch (cause) { error.value = String(cause) }
  finally { wikiLoading.value = false }
}
async function loadPlanDetails() {
  if (!props.projectId || !selected.value || selected.value.type !== 'job') return
  wikiLoading.value = true; error.value = ''
  try {
    const id = selected.value.id
    const result = await api<{ entries: LogEvent[] }>(
      `/api/projects/${props.projectId}/plan/logs/${encodeURIComponent(id)}`)
    if (selected.value?.id === id) { wikiEntries.value = result.entries; planOpened.value = true }
  } catch (cause) { error.value = String(cause) }
  finally { wikiLoading.value = false }
}
async function refresh() {
  if (!props.projectId) return
  try {
    const [newJobs, newLogs] = await Promise.all([
      api<Job[]>(`/api/projects/${props.projectId}/jobs`),
      api<GenerationLog[]>(`/api/projects/${props.projectId}/logs`),
    ])
    jobs.value = newJobs; logs.value = newLogs
    const target = props.focusJob && records.value.find(item => item.type === 'job' && item.id === props.focusJob)
    if (!selected.value && (target || records.value.length)) await choose(target || records.value[0]!)
    else if (selected.value && !records.value.some(item => item.id === selected.value?.id)) selected.value = null
  } catch (cause) { error.value = String(cause) }
}
watch(() => props.projectId, () => { selected.value = null; jobs.value = []; logs.value = []; void refresh() })
watch(() => props.focusJob, value => {
  const target = records.value.find(item => item.type === 'job' && item.id === value)
  if (target) void choose(target)
})
onMounted(() => { void refresh(); timer = setInterval(() => { void refresh() }, 2500) })
onUnmounted(() => { if (timer) clearInterval(timer) })
</script>

<template><div class="feature-page"><div class="feature-head"><span class="eyebrow">运行记录</span><h1>按时间回看每一次运行。</h1><p>任务与续写日志按记录时间倒序排列；选择一条可查看过程和结果。</p></div>
  <p v-if="error" class="notice error" role="alert">{{ error }}</p>
  <div class="runs-layout"><section class="panel runs-index"><h2>全部记录 <small>{{ records.length }}</small></h2>
    <button v-for="record in records" :key="`${record.type}:${record.id}`" class="run-record"
      :class="{ chosen: selected?.id === record.id }" @click="choose(record)">
      <span class="run-record-type">{{ record.type === 'job' ? '后台任务' : '续写日志' }}</span>
      <strong>{{ record.title }}</strong><time>{{ displayTime(record.created_at) }}</time>
      <span class="run-record-status">{{ record.status }}</span></button>
    <p v-if="!records.length">尚无运行记录。开始续写或导入母本后会在这里显示。</p></section>
    <section class="panel runs-detail"><template v-if="selected">
      <div class="run-detail-head"><span class="eyebrow">{{ selected.type === 'job' ? '后台任务' : '续写日志' }}</span>
        <h2>{{ selected.title }}</h2><time>{{ displayTime(selected.created_at) }}</time></div>
      <template v-if="selected.type === 'job'"><p class="run-summary">{{ selectedJob?.message }}</p>
        <div v-if="selectedJob?.kind === 'wiki'" class="wiki-log-access"><div><strong>完整编纂日志</strong><p>逐片段保存提示词、模型原始回复、解析结果及耗时；可能含小说正文，只在本机查看。</p></div><button :disabled="wikiLoading" @click="loadWikiDetails">{{ wikiLoading ? '读取中…' : wikiOpened ? '刷新完整日志' : '查看完整日志' }}</button></div>
        <div v-if="selectedJob && ['plan', 'consistency', 'revise'].includes(selectedJob.kind)" class="wiki-log-access"><div><strong>{{ selectedJob.kind === 'consistency' ? '完整审校日志' : selectedJob.kind === 'revise' ? '完整修订日志' : '完整规划日志' }}</strong><p>保存各阶段的提示词、模型原始回复及检查结果；可能含小说正文，只在本机查看。</p></div><button :disabled="wikiLoading" @click="loadPlanDetails">{{ wikiLoading ? '读取中…' : planOpened ? '刷新完整日志' : '查看完整日志' }}</button></div>
        <button v-if="selectedJob && ['revise', 'plan'].includes(selectedJob.kind) && ['failed', 'interrupted'].includes(selectedJob.status) && resumeModel" :disabled="resuming" @click="resumeRevision">{{ resuming ? '恢复中…' : selectedJob.kind === 'plan' ? '从检查点继续规划' : '从检查点继续修订' }}</button>
        <button v-if="selectedJob?.kind === 'plan' && selectedJob.status === 'completed' && selectedJob.result?.resumed" :disabled="resuming || savedPlanJob === selectedJob.id" @click="saveRecoveredPlan">{{ savedPlanJob === selectedJob.id ? '已保存到续写设定' : resuming ? '保存中…' : '保存恢复的规划到续写设定' }}</button>
        <div v-if="wikiOpened" class="wiki-log-details"><p v-if="!wikiEntries.length" class="muted-note">日志文件暂无记录。</p><details v-for="(entry, index) in wikiEntries" :key="index"><summary><span>{{ String(entry.event || '事件') }}</span><small>第 {{ entry.chapter ?? '—' }} 章 · 片段 {{ entry.chunk ?? '—' }} · {{ displayTime(String(entry.timestamp || '')) }}</small></summary><dl class="wiki-log-fields"><div v-for="field in detailFields(entry, ['timestamp', 'event'])" :key="field.key"><dt>{{ label(field.key) }}</dt><dd><pre v-if="field.key === 'prompt' || field.key === 'raw_response'">{{ field.value }}</pre><span v-else>{{ field.value }}</span></dd></div></dl></details></div>
        <div v-if="planOpened" class="wiki-log-details"><p v-if="!wikiEntries.length" class="muted-note">日志文件暂无记录。</p><details v-for="(entry, index) in wikiEntries" :key="index"><summary><span>{{ String(entry.event || '事件') }}</span><small>{{ String(entry.stage || '流程') }} · {{ displayTime(String(entry.timestamp || '')) }}</small></summary><dl class="wiki-log-fields"><div v-for="field in detailFields(entry, ['timestamp', 'event'])" :key="field.key"><dt>{{ field.label }}</dt><dd><pre v-if="field.key === 'prompt' || field.key === 'raw_response' || field.key === 'plan'">{{ field.value }}</pre><span v-else>{{ field.value }}</span></dd></div></dl></details></div>
        <h3>过程</h3><ol class="run-events"><li v-for="(event, index) in [...(selectedJob?.events || [])].reverse()" :key="index">
          <time>{{ event.time }}</time><div><strong>{{ event.message }}</strong>
            <dl v-if="Object.keys(event.data || {}).length" class="run-fields"><div v-for="field in detailFields(event.data)" :key="field.key">
              <dt>{{ field.label }}</dt><dd>{{ field.value }}</dd></div></dl></div></li></ol>
        <h3 v-if="selectedJob?.result">结果</h3><dl v-if="selectedJob?.result" class="run-fields result-fields">
          <div v-for="field in detailFields(selectedJob.result)" :key="field.key"><dt>{{ field.label }}</dt><dd>{{ field.value }}</dd></div></dl></template>
      <template v-else><p v-if="!logEvents.length" class="muted-note">暂无可读取的事件。</p>
        <ol class="run-events"><li v-for="(event, index) in [...logEvents].reverse()" :key="index">
          <time>{{ displayTime(String(event.timestamp || '')) }}</time><div>
            <strong>{{ eventTitles[String(event.event)] || String(event.event || '事件') }}</strong>
            <dl class="run-fields"><div v-for="field in detailFields(event, ['timestamp', 'run_id', 'event'])" :key="field.key">
              <dt>{{ field.label }}</dt><dd>{{ field.value }}</dd></div></dl></div></li></ol></template>
    </template><p v-else>从左侧选择一条记录。</p></section></div>
</div></template>
