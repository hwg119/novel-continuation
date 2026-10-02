<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { api, type Job } from './api'

const props = defineProps<{ projectId: string; focusJob: string }>()
const emit = defineEmits<{
  view: []
  layout: [state: { visible: boolean; expanded: boolean }]
}>()
const job = ref<Job | null>(null)
const expanded = ref(false)
const dismissed = ref('')
const error = ref('')
const logBox = ref<HTMLElement | null>(null)
let timer: ReturnType<typeof setInterval> | null = null
let loading = false

const stages: Record<string, string> = {
  summary_fill: '补充前章摘要', translate_start: '翻译检索词', beat_start: '开始生成分幕',
  beat_done: '分幕已生成', summary_start: '生成章节摘要', summary_done: '摘要已生成',
  retrieval_completed: '母本检索完成',
}
const dataLabels: Record<string, string> = {
  index: '进度', total: '总数', name: '幕名', chapter: '章节', segments: '分段',
  beat_chars: '本幕字数', total_chars: '累计字数', dropped: '去重', retrieved_chars: '检索字数',
  quality_warnings: '质量提醒', chars: '字数', path: '路径',
  completed_units: '已完成片段', total_units: '总片段', progress_percent: '进度',
  remaining_time: '预计剩余', action: '处理方式', chapter_segments: '本章分段',
}
function stage(value: string) { return stages[value] || value }
function eventLevel(value: string) {
  if (/失败|错误|异常|警告|未通过|中断/.test(value)) return 'error'
  if (/完成|成功|通过|已生成/.test(value)) return 'success'
  return 'info'
}
function valueText(value: unknown): string {
  if (Array.isArray(value)) return value.map(valueText).join('；') || '无'
  if (value && typeof value === 'object') return Object.entries(value).map(([key, part]) =>
    `${dataLabels[key] || key}：${valueText(part)}`).join(' · ')
  return String(value ?? '')
}
function details(data: Record<string, unknown>) {
  return Object.entries(data || {}).map(([key, value]) => `${dataLabels[key] || key}：${valueText(value)}`).join(' · ')
}
async function refresh() {
  if (!props.projectId || loading) return
  loading = true
  try {
    let next: Job | undefined
    if (props.focusJob && props.focusJob !== dismissed.value) {
      const focused = await api<Job>(`/api/projects/${props.projectId}/jobs/${props.focusJob}`)
      if (focused.status === 'running') next = focused
      else {
        const recent = await api<Job[]>(`/api/projects/${props.projectId}/jobs`)
        next = recent.find(item => item.status === 'running' && item.id !== dismissed.value) || focused
      }
    } else if (job.value && job.value.id !== dismissed.value) {
      const current = await api<Job>(`/api/projects/${props.projectId}/jobs/${job.value.id}`)
      if (current.status === 'running') next = current
      else {
        const recent = await api<Job[]>(`/api/projects/${props.projectId}/jobs`)
        next = recent.find(item => item.status === 'running' && item.id !== dismissed.value) || current
      }
    } else {
      const recent = await api<Job[]>(`/api/projects/${props.projectId}/jobs`)
      next = recent.find(item => item.status === 'running' && item.id !== dismissed.value)
    }
    if (next) {
      if (job.value?.id !== next.id) expanded.value = true
      job.value = next
    }
    error.value = ''
  } catch (cause) { error.value = String(cause) }
  finally { loading = false }
}
function close() { if (job.value) dismissed.value = job.value.id; job.value = null; expanded.value = false }
watch(() => props.projectId, () => { job.value = null; dismissed.value = ''; void refresh() })
watch(() => props.focusJob, () => { dismissed.value = ''; void refresh() })
watch([() => !!job.value, expanded], ([visible, isExpanded]) => {
  emit('layout', { visible, expanded: visible && isExpanded })
}, { immediate: true })
watch(() => job.value?.events.length, async () => {
  if (!expanded.value) return
  await nextTick()
  if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight
})
onMounted(() => { void refresh(); timer = setInterval(() => { void refresh() }, 1500) })
onUnmounted(() => { if (timer) clearInterval(timer); emit('layout', { visible: false, expanded: false }) })
</script>

<template><aside v-if="job" class="task-drawer" :class="{ expanded }" aria-label="任务实时日志">
  <div class="task-drawer-bar"><span class="terminal-mark" aria-hidden="true">&gt;_</span>
    <span class="terminal-title">任务控制台</span><span class="task-state" :class="job.status">{{ job.status === 'running' ? '运行中' : job.status === 'completed' ? '已完成' : job.status === 'failed' ? '失败' : '已中断' }}</span>
    <strong>{{ stage(job.message) }}</strong><span class="task-count">{{ job.events.length }} 行</span>
    <button type="button" class="task-icon" :aria-label="expanded ? '收起日志' : '展开日志'" :aria-expanded="expanded" @click="expanded = !expanded">{{ expanded ? '收起' : '展开' }} <span aria-hidden="true">{{ expanded ? '⌄' : '⌃' }}</span></button>
    <button type="button" class="task-icon task-close" aria-label="关闭任务抽屉" @click="close">×</button></div>
  <div v-if="expanded" ref="logBox" class="task-drawer-log" role="log" aria-live="polite" aria-relevant="additions">
    <div class="terminal-session"><span>SESSION</span><code>{{ job.id }}</code><span class="terminal-session-kind">{{ job.kind }}</span></div>
    <p v-if="error" class="task-error"><span aria-hidden="true">!</span> {{ error }}</p>
    <div v-for="(event, index) in job.events" :key="index" class="task-event" :class="eventLevel(event.message)">
      <span class="task-line-number" aria-hidden="true">{{ String(index + 1).padStart(3, '0') }}</span>
      <time>{{ event.time }}</time><span class="task-level">{{ eventLevel(event.message) === 'error' ? 'ERR' : eventLevel(event.message) === 'success' ? 'OK' : 'INFO' }}</span>
      <div class="task-event-body"><strong>{{ stage(event.message) }}</strong><small v-if="Object.keys(event.data || {}).length">{{ details(event.data) }}</small></div></div>
    <p v-if="!job.events.length" class="task-waiting"><span aria-hidden="true">›</span> 任务已开始，等待第一条进度日志<span class="terminal-cursor" aria-hidden="true">▌</span></p>
    <div class="terminal-footer"><span>{{ job.status === 'running' ? '● 实时更新中' : '● 本次任务已结束' }}</span><button type="button" class="task-view" @click="emit('view'); expanded = false">查看完整运行记录 ↗</button></div>
  </div>
</aside></template>
