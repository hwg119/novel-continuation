<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { api, writeOptions } from './api'
import { routePath, type View } from './routeState'
import { toast } from './toastService'
import XSender from 'vue-element-plus-x/es/XSender/index.js'
import 'element-plus/es/components/button/style/css'
import { ElMessageBox } from 'element-plus'
import 'element-plus/es/components/message-box/style/css'
import { isCompositionEnter } from './chatInput'
import { chapterActions, followsLatest, type ChatChapterState } from './chatChapter'
import { chatTimeline, chatReplyText, type ChatEvent, type ProcessEntry } from './chatTimeline'
import { resultCard, resultLink } from './chatResults'
import { beatPreview } from './beatPreview'
import { importStoryRequirements } from './storyReview'

const props = defineProps<{ projectId: string; chapterNumber: number | null; modelName: string }>()
const emit = defineEmits<{ task: [id: string] }>()
type Event = ChatEvent
type Session = { id: string; title: string; chapter: number | null }
const sessions = ref<Session[]>([])
const selected = ref('')
const sessionMenu = ref('')
function closeSessionMenu(event: FocusEvent) {
  if (!(event.currentTarget as HTMLElement).contains(event.relatedTarget as Node | null)) sessionMenu.value = ''
}
const managingSession = ref(false)
const events = ref<Event[]>([])
const input = ref('')
const sender = ref<InstanceType<typeof XSender>>()
onMounted(() => { void nextTick(() => sender.value?.focus('last')) })
function focusChatInput() { void nextTick(() => sender.value?.focus('last')) }
function focusComposerBlank(event: MouseEvent) {
  const target = event.target
  if (busy.value || !(target instanceof Element)) return
  // Keep native caret placement, text selection and button clicks intact.
  if (target.closest('button, a, input, textarea, select, [contenteditable="true"]')) return
  if (window.getSelection()?.isCollapsed === false) return
  focusChatInput()
}
const composing = ref(false)
function inputChanged() { input.value = sender.value?.getModelValue().text || '' }
function guardComposition(event: KeyboardEvent) {
  if (isCompositionEnter(event, composing.value)) event.stopPropagation()
}
const chapter = ref<number | null>(props.chapterNumber)
const busy = ref(false)
const confirming = ref('')
const saving = ref('')
const connected = ref(false)
const expandedProcesses = ref<Record<string, boolean>>({})
const processStates: Record<string, string> = { running: '进行中', completed: '已完成', failed: '失败', cancelled: '已停止', interrupted: '已中断' }
function toggleProcess(key: string, event: globalThis.Event) {
  expandedProcesses.value[key] = (event.target as HTMLDetailsElement).open
}
function logDetails(entry: ProcessEntry) {
  return Object.entries(entry.details).map(([key, value]) => `${key}：${typeof value === 'object' ? JSON.stringify(value) : String(value ?? '')}`).join(' · ')
}
const transcript = ref<HTMLDivElement>()
const chapterState = ref<ChatChapterState | null>(null)
const checkingChapter = ref(false)
const quickActions = computed(() => chapterState.value ? chapterActions(chapterState.value) : [])
let chapterCheck = 0
let chapterTimer: ReturnType<typeof setTimeout> | undefined
async function loadChapterState() {
  const version = ++chapterCheck
  const number = targetChapter()
  chapterState.value = null
  if (!props.projectId || !number) { checkingChapter.value = false; return }
  checkingChapter.value = true
  try {
    const state = await api<ChatChapterState>(`/api/projects/${props.projectId}/chapters/${number}/plan-status`)
    if (version === chapterCheck) chapterState.value = state
  } finally { if (version === chapterCheck) checkingChapter.value = false }
}
function scrollToLatest() {
  void nextTick(() => { if (transcript.value) transcript.value.scrollTop = transcript.value.scrollHeight })
}
let stream: EventSource | null = null
let loadVersion = 0
const labels: Record<string, string> = { plan: '生成分幕', plan_revision: '修改分幕', generate: '续写章节', consistency: '故事审校', revise: '执行修订' }
const running = computed(() => {
  const user = [...events.value].reverse().find(e => e.kind === 'user')
  if (!user) return ''
  return events.value.some(e => ['done','failed','cancelled','interrupted'].includes(e.kind) && e.data.turn_id === user.data.turn_id)
    ? '' : user.data.turn_id as string
})
const messages = computed(() => chatTimeline(events.value))
const enhancementChoices = ref<Record<string, string[]>>({})
const revisingAudit = ref('')
function auditCanRevise(data: Record<string, any>) {
  return data.status === 'completed' && !!importStoryRequirements(data.result?.revision_requirements || '',
    data.result?.story_enhancements || [], enhancementChoices.value[data.job_id] || [])
}
async function reviseAudit(event: Event) {
  if (revisingAudit.value || !auditCanRevise(event.data)) return
  if (!props.modelName) { toast.error('请先选择大模型'); return }
  revisingAudit.value = event.data.job_id
  try {
    const job = await api<{ id: string }>(`/api/projects/${props.projectId}/chat/${selected.value}/actions/${event.data.action_id}/revise-audit`,
      writeOptions('POST', { model_name: props.modelName, enhancements: enhancementChoices.value[event.data.job_id] || [] }))
    emit('task', job.id)
  } catch (error) { toast.error(error) }
  finally { revisingAudit.value = '' }
}
watch(messages, rows => {
  for (const row of rows) if (row.kind === 'task_result' && row.data.job_id && !(row.data.job_id in enhancementChoices.value))
    enhancementChoices.value[row.data.job_id] = []
}, { immediate: true })
function actionHandled(id: string) { return events.value.some(e => e.kind === 'action_status' && e.data.action_id === id) }
function planSaved(id: string) { return events.value.some(e => e.kind === 'plan_saved' && e.data.action_id === id) }
function remember() {
  localStorage.setItem(`novel-chat:${props.projectId}`, selected.value)
  const url = new URL(window.location.href)
  url.searchParams.set('session', selected.value)
  window.history.replaceState({}, '', url)
}
function connect() {
  stream?.close()
  const cursor = events.value.at(-1)?.id || 0
  stream = new EventSource(`/api/projects/${props.projectId}/chat/${selected.value}/events?after=${cursor}`)
  stream.onopen = () => { connected.value = true }
  stream.onerror = () => { connected.value = false }
  stream.onmessage = message => {
    const event: Event = JSON.parse(message.data)
    const panel = transcript.value
    const nearBottom = followsLatest(panel)
    if (!events.value.some(e => e.id === event.id)) events.value.push(event)
    if (nearBottom) scrollToLatest()
    if (event.kind === 'plan_saved' || (event.kind === 'task_result' && event.data.status === 'completed'))
      void loadChapterState().catch(toast.error)
  }
}
function targetChapter() { return Number.isSafeInteger(Number(chapter.value)) && Number(chapter.value) > 0 ? Number(chapter.value) : null }
async function choose(id: string) {
  sessionMenu.value = ''
  const version = ++loadVersion
  stream?.close(); connected.value = false
  const data = await api<Session & { events: Event[] }>(`/api/projects/${props.projectId}/chat/${id}`)
  if (version !== loadVersion) return
  selected.value = id; events.value = data.events; chapter.value = data.chapter; expandedProcesses.value = {}
  remember(); connect(); scrollToLatest()
  await nextTick()
  if (version === loadVersion) sender.value?.focus('last')
}
async function create() {
  const result = await api<{ id: string }>(`/api/projects/${props.projectId}/chat`, writeOptions('POST', { chapter: targetChapter() }))
  await refreshList(); await choose(result.id)
}
async function refreshList() { sessions.value = await api<Session[]>(`/api/projects/${props.projectId}/chat`) }
async function renameSession(session: Session) {
  if (managingSession.value) return
  sessionMenu.value = ''
  const project = props.projectId
  managingSession.value = true
  try {
    const { value } = await ElMessageBox.prompt('请输入新的对话名称', '重命名对话', {
      customClass: 'chat-project-dialog', modalClass: 'chat-project-dialog-backdrop',
      inputValue: session.title, confirmButtonText: '保存', cancelButtonText: '取消',
      inputValidator: (value: string) => (value.trim().length >= 1 && value.trim().length <= 80) || '名称需为 1～80 个字符',
      closeOnClickModal: false,
    })
    if (project !== props.projectId) return
    await api(`/api/projects/${project}/chat/${session.id}`, writeOptions('PATCH', { title: value.trim() }))
    await refreshList(); toast.success('对话已重命名')
  } catch (error) { if (error !== 'cancel' && error !== 'close') toast.error(error) }
  finally { managingSession.value = false }
}
async function deleteSession(session: Session) {
  if (managingSession.value) return
  sessionMenu.value = ''
  const project = props.projectId
  const id = session.id
  managingSession.value = true
  try {
    await ElMessageBox.confirm(`确定删除对话“${session.title}”？小说正文与运行记录不受影响，已启动的后台任务仍会继续运行。`, '删除对话', {
      customClass: 'chat-project-dialog', modalClass: 'chat-project-dialog-backdrop',
      confirmButtonText: '确认删除', cancelButtonText: '取消', type: 'warning', closeOnClickModal: false,
    })
    if (project !== props.projectId) return
    await api(`/api/projects/${project}/chat/${id}`, writeOptions('DELETE'))
    if (selected.value === id) {
      ++loadVersion; stream?.close(); stream = null; connected.value = false
      selected.value = ''; events.value = []; expandedProcesses.value = {}
      localStorage.removeItem(`novel-chat:${props.projectId}`)
      const url = new URL(window.location.href); url.searchParams.delete('session')
      window.history.replaceState({}, '', url)
      await refreshList()
      if (sessions.value[0]) await choose(sessions.value[0].id)
      else { await nextTick(); sender.value?.focus('last') }
    } else await refreshList()
    toast.success('对话已移除，正文与运行记录不受影响')
  } catch (error) { if (error !== 'cancel' && error !== 'close') toast.error(error) }
  finally { managingSession.value = false }
}
async function initialize() {
  if (!props.projectId) return
  await refreshList()
  const id = new URLSearchParams(window.location.search).get('session') || localStorage.getItem(`novel-chat:${props.projectId}`)
  const available = sessions.value.find(s => s.id === id) || sessions.value[0]
  if (available) await choose(available.id)
}
async function send() {
  await sendText(input.value)
}
async function sendText(content: string) {
  if (!content.trim() || busy.value || running.value) return
  if (!props.projectId) { toast.error('请先选择工程'); return }
  if (!props.modelName) { toast.error('请先选择大模型'); return }
  busy.value = true
  try {
    if (!selected.value) await create()
    await api(`/api/projects/${props.projectId}/chat/${selected.value}/messages`, writeOptions('POST', { text: content, chapter: targetChapter(), model_name: props.modelName }))
    if (input.value === content) { sender.value?.clear(); input.value = '' }
    scrollToLatest(); await refreshList()
  } finally { busy.value = false }
}
async function confirm(event: Event) {
  const data = event.data
  if (actionHandled(data.action_id) || confirming.value) return
  confirming.value = data.action_id
  try {
    const job = await api<{ id: string }>(`/api/projects/${props.projectId}/chat/${selected.value}/actions/${data.action_id}/confirm`, writeOptions('POST'))
    emit('task', job.id)
  } finally { confirming.value = '' }
}
async function stop() { await api(`/api/projects/${props.projectId}/chat/${selected.value}/turns/${running.value}/stop`, writeOptions('POST')) }
async function savePlan(event: Event) {
  if (planSaved(event.data.action_id) || saving.value) return
  saving.value = event.data.action_id
  try {
    await api(`/api/projects/${props.projectId}/chat/${selected.value}/actions/${event.data.action_id}/save-plan`, writeOptions('POST'))
    toast.success('分幕已保存')
  } finally { saving.value = '' }
}
function link(data: Record<string, any>) { return routePath(data.view as View, props.projectId, data.chapter) }
watch(() => props.projectId, () => { void initialize().catch(toast.error) }, { immediate: true })
watch([() => props.projectId, chapter], () => {
  ++chapterCheck; chapterState.value = null; checkingChapter.value = !!targetChapter()
  clearTimeout(chapterTimer)
  chapterTimer = setTimeout(() => { void loadChapterState().catch(toast.error) }, 180)
}, { immediate: true })
onUnmounted(() => { ++loadVersion; ++chapterCheck; clearTimeout(chapterTimer); stream?.close() })
</script>

<template>
  <section class="chat-workbench">
    <aside class="chat-sessions"><div class="chat-session-head"><strong>创作对话</strong><button type="button" class="chat-button chat-button-secondary" @click="create">新对话</button></div>
      <div v-for="session in sessions" :key="session.id" class="chat-session-row" @keydown.esc="sessionMenu = ''" @focusout="closeSessionMenu">
        <button class="chat-session" :class="{ selected: selected === session.id }" @click="choose(session.id)">{{ session.title }}<small>{{ session.chapter ? `第 ${session.chapter} 章` : '未指定章节' }}</small></button>
        <div class="chat-session-more" :class="{ open: sessionMenu === session.id }">
          <button type="button" class="chat-more-trigger" :aria-label="`管理对话：${session.title}`" :aria-expanded="sessionMenu === session.id" :aria-controls="`session-menu-${session.id}`" @click="sessionMenu = sessionMenu === session.id ? '' : session.id">⋯</button>
          <div v-if="sessionMenu === session.id" :id="`session-menu-${session.id}`" class="chat-session-menu"><button type="button" :disabled="managingSession" @click="renameSession(session)">重命名</button><button type="button" :disabled="managingSession" @click="deleteSession(session)">删除</button></div>
        </div>
      </div>
    </aside>
    <div class="chat-desk">
      <header><p class="eyebrow">对话工作台</p><h1>说出想写的下一步。</h1><p>讨论、分幕、续写和修订，从一个目标开始。正文仍在阅读页展示。</p></header>
      <div class="chat-context"><label>当前章 <input v-model.number="chapter" type="number" min="1" placeholder="章号" @change="focusChatInput"></label><span>{{ modelName || '尚未选择模型' }}</span><small>{{ connected ? '实时连接' : selected ? '连接中／正在重连' : '会话会自动保存' }}</small></div>
      <div ref="transcript" class="chat-transcript" aria-live="polite" tabindex="0" aria-label="聊天记录">
        <div v-if="!messages.length" class="chat-empty"><strong>先聊目标，再执行。</strong><p>例如：先看看下一章该如何推进，给我一份分幕。</p><p>也可以要求修改当前分幕，或审校已经写好的章节。</p></div>
        <article v-for="event in messages" :key="event.id" class="chat-entry" :class="event.kind">
          <details v-if="event.kind === 'process'" class="chat-process" :class="event.data.status" :open="!!expandedProcesses[event.data.key]" @toggle="toggleProcess(event.data.key, $event)">
            <summary><span class="chat-process-title">&gt;_ 处理过程 · {{ event.data.label }}</span><small>{{ processStates[event.data.status] || event.data.status }} · {{ event.data.entries.length }} 条</small><span class="chat-process-latest">{{ event.data.entries.at(-1)?.text || '等待第一条日志' }}</span></summary>
            <div class="chat-process-log" role="log" aria-label="处理日志"><div v-for="entry in event.data.entries" :key="entry.id" class="chat-process-line" :class="entry.level"><time>{{ entry.time || '—' }}</time><span>{{ entry.level === 'error' ? 'ERR' : entry.level === 'success' ? 'OK' : 'INFO' }}</span><div>{{ entry.text }}<small v-if="Object.keys(entry.details).length">{{ logDetails(entry) }}</small></div></div><p v-if="!event.data.entries.length">任务已开始，等待进度日志。</p></div>
          </details>
          <template v-else-if="event.kind === 'action'"><div class="chat-result-head"><div><small>第 {{ event.data.chapter }} 章 · {{ event.data.model }}</small><h3>{{ labels[event.data.tool] }}</h3></div><span class="chat-result-state">{{ actionHandled(event.data.action_id) ? '操作已处理，结果见下方' : '待执行' }}</span></div><p class="chat-action-description">{{ event.data.message }}</p><details v-if="event.data.requirements" class="chat-content-preview"><summary>查看本次处理要求</summary><div class="chat-result-prose">{{ event.data.requirements }}</div></details><div class="chat-result-actions"><button type="button" class="chat-button" :disabled="actionHandled(event.data.action_id) || !!confirming" @click="confirm(event)">{{ actionHandled(event.data.action_id) ? '已处理' : confirming === event.data.action_id ? '启动中…' : '执行' }}</button></div></template>
          <template v-else-if="event.kind === 'task_result'"><div class="chat-result-head"><div><small>第 {{ event.data.chapter }} 章<span v-if="event.data.model"> · {{ event.data.model }}</span></small><h3>{{ resultCard(event.data).title }}</h3></div><span class="chat-result-state">{{ resultCard(event.data, planSaved(event.data.action_id)).state }}</span></div><p>{{ event.data.text }}</p>
            <p v-if="event.data.result?.story_summary" class="chat-result-summary">{{ event.data.result.story_summary }}</p>
            <details v-if="event.data.result?.issues?.length" class="chat-result-warning"><summary>修订验收提醒 · {{ event.data.result.issues.length }} 项</summary><ul><li v-for="(issue, index) in event.data.result.issues" :key="index">{{ issue }}</li></ul></details>
            <details v-if="event.data.result?.beats?.length" class="chat-plan-preview"><summary>查看分幕 · {{ event.data.result.chapter_title }}<span>{{ event.data.result.beats.length }} 幕</span></summary><div class="chat-beat-list"><section v-for="beat in event.data.result.beats.map(beatPreview)" :key="beat.key" class="chat-beat-card"><header><span class="chat-beat-number">{{ String(beat.number).padStart(2, '0') }}</span><h4>{{ beat.name }}</h4></header><div v-if="beat.meta.length" class="chat-beat-meta"><span v-for="meta in beat.meta" :key="meta.label"><small>{{ meta.label }}</small>{{ meta.value }}</span></div><div class="chat-beat-body"><p v-for="(paragraph, index) in beat.paragraphs" :key="index">{{ paragraph }}</p></div></section></div><p v-if="event.data.result.planning_review?.summary" class="chat-plan-review">{{ event.data.result.planning_review.summary }}</p></details>
            <section v-if="event.data.result?.revision_requirements" class="chat-review-requirements"><h4>主线修订要求</h4><div class="chat-result-prose">{{ event.data.result.revision_requirements }}</div></section>
            <details v-if="event.data.result?.story_enhancements?.length" class="chat-content-preview"><summary>可选情节增强 · {{ event.data.result.story_enhancements.length }} 项 · 已选 {{ enhancementChoices[event.data.job_id]?.length || 0 }} 项</summary><div class="chat-enhancement-list"><section v-for="(item, index) in event.data.result.story_enhancements" :key="item.id || index" :class="{ selected: enhancementChoices[event.data.job_id]?.includes(item.id) }"><label class="chat-enhancement-choice"><input v-model="enhancementChoices[event.data.job_id]" type="checkbox" :value="item.id" :disabled="!item.id || event.data.status !== 'completed'"><strong>{{ item.scope || `建议 ${Number(index) + 1}` }}</strong></label><p>{{ item.suggestion }}</p><small v-if="item.preserve">保留：{{ item.preserve }}</small></section></div><p class="chat-preview-note">使用当前模型直接修订主线要求与所选增强，不跳转页面，不自动覆盖正文。</p></details>
            <details v-if="event.data.result?.report" class="chat-content-preview"><summary>查看完整审校报告</summary><div class="chat-result-prose">{{ event.data.result.report }}</div></details>
            <details v-if="event.data.result?.candidate" class="chat-content-preview"><summary>预览修订正文 · {{ event.data.result.candidate.length }} 字符</summary><div class="chat-result-prose chat-draft-prose">{{ event.data.result.candidate }}</div><p class="chat-preview-note">这是候选稿，不代表已应用；请比较差异后决定。</p></details>
            <div class="chat-result-actions"><button v-if="event.data.status === 'completed' && event.data.result?.beats?.length" type="button" class="chat-button" :disabled="planSaved(event.data.action_id) || saving === event.data.action_id" @click="savePlan(event)">{{ planSaved(event.data.action_id) ? '已保存分幕' : saving === event.data.action_id ? '保存中…' : '保存分幕' }}</button><button v-if="resultCard(event.data).tool === 'consistency'" type="button" class="chat-button" :disabled="!auditCanRevise(event.data) || !!revisingAudit" :title="`使用当前模型：${modelName}`" @click="reviseAudit(event)">{{ revisingAudit === event.data.job_id ? '正在启动修订…' : '导入要求并修订' }}</button><a v-for="action in resultCard(event.data, planSaved(event.data.action_id), enhancementChoices[event.data.job_id] || []).actions.filter(action => action.label !== '导入要求并修订')" :key="action.label" class="chat-feature-link" :href="resultLink(projectId, event.data.chapter, action)">{{ action.label }} →</a></div></template>
          <template v-else-if="event.kind === 'link'"><a class="chat-feature-link" :href="link(event.data)">{{ event.data.label }} →</a></template>
          <template v-else><p>{{ event.kind === 'user' ? (event.data.text || event.data.message) : chatReplyText(event.data.text || event.data.message || '') }}</p></template>
        </article>
      </div>
      <div class="chat-shortcuts"><span v-if="checkingChapter">正在检查章节…</span>
        <template v-else-if="chapterState"><span class="chat-chapter-state">{{ chapterState.chapter_exists ? '已有正文' : '尚未写作' }} · {{ chapterState.has_beats ? `已保存 ${chapterState.beats_count} 幕大纲` : '暂无分幕大纲' }}</span>
          <template v-for="action in quickActions" :key="action.label"><a v-if="action.view" :href="routePath(action.view as View, projectId, chapterState.number)">{{ action.label }}</a><button v-else type="button" class="subtle" :disabled="busy || !!running" @click="sendText(action.prompt)">{{ action.label }}</button></template>
        </template><span v-else>{{ targetChapter() ? '章节状态暂不可用' : '选择章号后显示快捷操作' }}</span>
      </div>
      <div class="chat-composer" @click.capture="focusComposerBlank" @keydown.capture="guardComposition" @compositionstart="composing = true" @compositionend="composing = false">
        <XSender ref="sender" variant="updown" submit-type="enter" :max-length="5000" :tip-config="false"
          :disabled="busy" :loading="busy || !!running" :custom-style="{ minHeight: '76px', maxHeight: '220px' }"
          placeholder="说说你想做什么；指定章号可以避免指代不清。" @change="inputChanged" @submit="send">
          <template #prefix><small class="chat-key-hint">Enter 发送 · Shift+Enter 换行</small></template>
          <template #action-list><button v-if="running" type="button" class="chat-button chat-button-secondary" @click="stop">停止回复</button><button type="button" class="chat-button chat-send-button" :aria-busy="busy || !!running" :disabled="busy || !!running || !input.trim()" @click="send">{{ busy ? '发送中…' : running ? '正在回复…' : '发送' }}</button></template>
          <template #footer><div class="chat-composer-note">聊天操作点击即执行；候选稿不会自动应用，续写不覆盖已有正文。</div></template>
        </XSender>
      </div>
    </div>
  </section>
</template>

<style scoped>
.chat-workbench{flex:1;min-height:0;width:100%;display:grid;grid-template-columns:220px minmax(0,1fr);grid-template-rows:minmax(0,1fr);gap:24px;max-width:1500px;margin:0 auto;padding:20px 24px;overflow:hidden}
.chat-sessions{min-height:0;overflow-y:auto;border-right:1px solid #d4e1dd;padding-right:18px;scrollbar-width:thin}
.chat-session-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px}
.chat-button{font-family:inherit;font-size:14px;font-weight:600;line-height:1.2}
.chat-button{appearance:none;display:inline-flex;align-items:center;justify-content:center;gap:6px;min-height:36px;padding:8px 16px;border:1px solid #397b69;border-radius:8px;background:#397b69;color:#fff;font:600 14px/1.2 inherit;cursor:pointer;white-space:nowrap;transition:background .15s,border-color .15s,opacity .15s}.chat-button:hover:not(:disabled){background:#2c6455;border-color:#2c6455}.chat-button:focus-visible{outline:2px solid #79a998;outline-offset:3px}.chat-button:disabled{opacity:.45;cursor:not-allowed}.chat-button-secondary{background:#f8fbf9;border-color:#bed4ca;color:#356b59}.chat-button-secondary:hover:not(:disabled){background:#e1eeea;border-color:#92b6a6}.chat-send-button{min-width:76px}.chat-send-button[aria-busy="true"]::before{content:'';width:12px;height:12px;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:chat-button-spin .8s linear infinite}@keyframes chat-button-spin{to{transform:rotate(360deg)}}@media(prefers-reduced-motion:reduce){.chat-send-button[aria-busy="true"]::before{animation:none}}
.chat-session{display:block;width:100%;text-align:left;color:#244f50;background:transparent;margin:6px 0;padding:14px;border:1px solid transparent}
.chat-session.selected{background:#e1eeea;border-color:#9cbeb0}.chat-session small{display:block;margin-top:8px;color:#698585}
.chat-desk{min-width:0;min-height:0;display:flex;flex-direction:column}.chat-desk>header{flex-shrink:0}
.chat-desk h1{font-family:var(--font-display,serif);font-size:26px;margin:6px 0 8px}.chat-desk header p{color:#688486;margin:5px 0 10px}
.chat-context{display:flex;gap:20px;align-items:center;flex-wrap:wrap;border-block:1px solid #d4e1dd;padding:10px 0;flex-shrink:0}
.chat-context label{display:flex;gap:10px;align-items:center}.chat-context input{width:95px;min-height:38px;padding:7px 10px;border:1px solid #cbdad2;border-radius:6px;background:#fbfdfb;color:#263a40;font:inherit;accent-color:#326b59;transition:border-color .15s,box-shadow .15s}.chat-context input:hover{border-color:#92b6a6}.chat-context input:focus{outline:none;border-color:#79a998;box-shadow:0 0 0 2px #79a99833}.chat-context small{margin-left:auto;color:#698585}
.chat-shortcuts{display:flex;align-items:center;flex-wrap:wrap;gap:8px;padding:10px 0;flex-shrink:0;font-size:12px;color:#698585}
.chat-chapter-state{margin-right:auto}.chat-shortcuts button,.chat-shortcuts a{border:1px solid #bed4ca;border-radius:18px;padding:5px 12px;color:#356b59;background:#f8fbf9;font-size:12px;text-decoration:none}.chat-shortcuts button:hover:not(:disabled),.chat-shortcuts a:hover{background:#e1eeea}
.chat-transcript{flex:1;min-height:0;overflow-y:auto;overflow-x:hidden;overscroll-behavior-y:contain;scrollbar-width:thin;padding:10px 12px 16px 0;scroll-padding-bottom:16px}
.chat-transcript:focus-visible{outline:2px solid #79a998;outline-offset:-2px}
.chat-empty{padding:32px 20px;border-left:3px solid #79a998;color:#688486}
.chat-entry{white-space:pre-wrap;overflow-wrap:anywhere;padding:10px 0;margin:8px 0;line-height:1.8}.chat-entry p{margin:0}
.chat-entry.user{width:fit-content;max-width:80%;background:#e5efec;padding:10px 16px;margin-left:auto;margin-right:0;border-radius:12px 12px 0 12px;text-align:left}
.chat-enhancement-choice{display:flex;align-items:flex-start;gap:9px;cursor:pointer;color:#183f3a;font-size:14px}.chat-enhancement-choice input{width:16px;height:16px;flex:none;margin:4px 0 0;accent-color:#326b59}.chat-enhancement-list>section.selected{border-color:#92b6a6;background:#edf4ef}
.chat-entry.action,.chat-entry.task_result{white-space:normal;background:#fbfdfb}.chat-action-description{font-size:14px;line-height:1.8;color:#587069}.chat-content-preview{padding:10px 12px;border:1px solid #d4e1dd;border-radius:6px;background:#f7faf8}.chat-content-preview>summary{font-size:14px;font-weight:600}.chat-result-prose{max-width:85ch;margin-top:12px;white-space:pre-wrap;font-size:14px;line-height:1.85;color:#40565b;overflow-wrap:anywhere}.chat-draft-prose{font-family:"Noto Serif CJK SC","Songti SC",serif;line-height:1.95}.chat-review-requirements{margin-top:14px;padding:12px 14px;border-left:3px solid #92b6a6;background:#edf4ef}.chat-review-requirements h4,.chat-enhancement-list h4{margin:0;color:#183f3a;font-size:14px}.chat-review-requirements .chat-result-prose{margin-top:8px}.chat-enhancement-list{display:grid;gap:10px;margin-top:12px}.chat-enhancement-list>section{padding:12px;border:1px solid #d4e1dd;border-radius:6px;background:#fbfdfb}.chat-entry .chat-enhancement-list p{margin-top:6px;font-size:14px;line-height:1.85;white-space:pre-wrap}.chat-enhancement-list small{display:block;margin-top:8px;color:#698585;font-size:12px}.chat-entry .chat-preview-note{margin-top:12px;color:#698585;font-size:12px}.chat-result-warning ul{padding-left:20px;margin:10px 0 0}.chat-result-warning li{margin-bottom:8px;white-space:pre-wrap;line-height:1.75}.chat-result-actions>.chat-button{margin-top:0!important}
.chat-plan-preview{white-space:normal}.chat-plan-preview>summary{padding:10px 0;font-size:14px}.chat-plan-preview>summary>span{margin-left:12px;color:#698585;font-size:12px}.chat-beat-list{display:grid;gap:12px;margin-top:8px}.chat-beat-card{padding:18px 20px;border:1px solid #d4e1dd;border-radius:8px;background:#fbfdfb}.chat-beat-card>header{display:flex;align-items:center;gap:12px;margin-bottom:12px}.chat-beat-number{display:grid;place-items:center;width:32px;height:32px;flex:none;border-radius:6px;background:#e5efec;color:#356b59;font:600 13px/1 Consolas,monospace}.chat-beat-card h4{margin:0;color:#183f3a;font-size:17px;line-height:1.4}.chat-beat-meta{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:12px}.chat-beat-meta>span{display:inline-flex;gap:7px;align-items:baseline;padding:4px 9px;border-radius:5px;background:#edf4ef;color:#42685c;font-size:12px}.chat-beat-meta small{color:#789084;font-size:11px}.chat-beat-body{max-width:85ch;color:#40565b;font-size:14px;line-height:1.85;overflow-wrap:anywhere}.chat-entry .chat-beat-body p{margin:0 0 8px;white-space:pre-wrap}.chat-entry .chat-beat-body p:last-child{margin-bottom:0}.chat-entry .chat-plan-review{margin-top:12px;padding:10px 12px;border-left:3px solid #92b6a6;background:#edf4ef;color:#587069;font-size:13px;line-height:1.75}@media(max-width:800px){.chat-beat-card{padding:14px}.chat-beat-body{font-size:14px}}
.chat-result-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap}.chat-result-head small{color:#698585;font-size:12px}.chat-result-head h3{margin:4px 0 12px;font:600 22px/1.3 "KaiTi","STKaiti",serif;color:#183f3a}.chat-result-state{border-radius:16px;padding:4px 10px;background:#edf4ef;color:#527064;font-size:12px}.chat-result-actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px;padding-top:14px;border-top:1px solid #e0e9e4}.chat-entry .chat-result-summary{margin-top:12px;color:#587069}.chat-entry .chat-result-warning{margin-top:12px;padding:10px;background:#fff4e9;color:#8b603d;font-size:13px}.chat-entry.task_result details{margin-top:12px}.chat-entry.task_result summary{cursor:pointer;color:#356b59}
.chat-session-row{position:relative;margin-bottom:12px;min-width:0}.chat-session-row>.chat-session{padding-right:36px}
.chat-session{border-radius:8px;transition:background .15s,border-color .15s}.chat-session:hover{background:#edf4ef;border-color:#cbdad2}.chat-session:focus-visible,.chat-session-menu button:focus-visible,.chat-more-trigger:focus-visible,.chat-shortcuts button:focus-visible,.chat-shortcuts a:focus-visible{outline:2px solid #79a998;outline-offset:2px}.chat-session-menu button:disabled,.chat-shortcuts button:disabled{opacity:.45;cursor:not-allowed}.chat-entry.task_result>.chat-button{margin-top:14px}
.chat-session-more{position:absolute;right:6px;top:8px;z-index:2}.chat-more-trigger{opacity:0;padding:0 6px;background:transparent;color:#52796b;border:0;font-size:24px;line-height:28px;border-radius:5px}.chat-session-row:hover .chat-more-trigger,.chat-session-row:focus-within .chat-more-trigger,.chat-session-more.open .chat-more-trigger{opacity:1}.chat-more-trigger:hover{background:#d5e8df}.chat-session-menu{position:absolute;right:0;top:32px;min-width:100px;padding:5px;background:#fff;border:1px solid #cadbd4;border-radius:7px;box-shadow:0 5px 16px #193a3320}.chat-session-menu button{display:block;width:100%;padding:6px 12px;text-align:left;border:0;border-radius:4px;background:transparent;color:#52796b;font-size:13px}.chat-session-menu button:hover{background:#e5efec}@media(hover:none){.chat-more-trigger{opacity:1}}
.chat-feature-link{display:inline-flex;align-items:center;padding:8px 16px;border:1px solid #bed4ca;border-radius:8px;background:#e5efec;color:#356b59;text-decoration:none;font-weight:600}.chat-feature-link:hover{background:#d5e8df}.chat-feature-link:focus-visible{outline:2px solid #79a998;outline-offset:2px}
.chat-entry.progress,.chat-entry.task_progress{font-size:14px;color:#698585;padding:2px 0}
.chat-entry.action,.chat-entry.task_result{border:1px solid #bed4ca;border-left:4px solid #397b69;padding:20px;border-radius:6px}.chat-entry.action button{margin-top:14px}
.chat-entry.failed,.chat-entry.interrupted{color:#a74026;background:#fff1e9;padding:14px}
.chat-entry.process{padding:0;white-space:normal}.chat-process{border:1px solid #bacdc8;border-radius:7px;background:#edf3f1;color:#4f7068}.chat-process summary{display:flex;align-items:center;flex-wrap:wrap;gap:8px 14px;padding:11px 14px;cursor:pointer;list-style:none;font-size:12px}.chat-process summary::before{content:'▸'}.chat-process[open] summary::before{content:'▾'}.chat-process-title{font-weight:700}.chat-process summary small{margin-left:auto}.chat-process-latest{width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding-left:18px;opacity:.85}.chat-process.failed,.chat-process.interrupted{border-color:#d4a68c}.chat-process.running .chat-process-title{color:#2c7760}.chat-process-log{max-height:260px;overflow-y:auto;overscroll-behavior:contain;scrollbar-width:thin;background:#19343a;color:#c5d8d2;padding:12px 14px;border-radius:0 0 6px 6px;font:12px/1.8 Consolas,'Microsoft YaHei',monospace}.chat-process-line{display:grid;grid-template-columns:64px 34px minmax(0,1fr);gap:8px;margin-bottom:9px}.chat-process-line time{color:#8ca9a5}.chat-process-line>div{white-space:pre-wrap;overflow-wrap:anywhere}.chat-process-line small{display:block;color:#91afa5}.chat-process-line.error{color:#efb39c}.chat-process-line.success{color:#97d5ae}
.chat-composer{position:static;flex-shrink:0;min-width:0;margin-top:12px;border-radius:10px;box-shadow:0 4px 15px #193a3310;--el-color-primary:#397b69;--el-border-color:#b8d2c7;--el-text-color-regular:#263a40;--el-text-color-primary:#263a40;--el-text-color-placeholder:#698585;--el-bg-color:#fff;--el-fill-color-blank:#fff;--chat-primary:#397b69;--chat-text:#263a40;--chat-input-shadow-color:#397b6933}
.chat-key-hint{font-size:12px;color:#698585}.chat-composer-note{font-size:12px;color:#698585;padding:8px 16px}
.chat-composer :deep(.elx-x-sender__action-list){gap:10px}.chat-composer :deep(.chat-rich-text){font:inherit;line-height:1.8}
.chat-composer :deep(.elx-x-sender){display:block;border-radius:10px;background:#fff}.chat-composer :deep(.elx-x-sender):focus-within{outline:2px solid #79a998;outline-offset:2px}
@media(max-height:750px){.chat-desk>header{display:none}.chat-workbench{padding-block:12px}.chat-composer-note{display:none}}
@media(max-width:800px){.chat-workbench{grid-template-columns:1fr;grid-template-rows:auto minmax(0,1fr);padding:10px 14px;gap:10px}.chat-sessions{border-right:0;display:flex;gap:8px;overflow:auto;max-height:76px;padding:0}.chat-session{min-width:150px;margin:0;padding:9px}.chat-session-head{min-width:120px;margin:0}.chat-desk>header{display:none}.chat-context{gap:12px}.chat-chapter-state{width:100%}.chat-composer .chat-key-hint{display:none}.chat-shortcuts{padding:6px 0}.chat-transcript{padding-top:4px}}
</style>

<!-- MessageBox is teleported to body; scope these styles with its custom class. -->
<style>
.chat-project-dialog-backdrop{background:#102a31ad;backdrop-filter:blur(2px)}
.el-message-box.chat-project-dialog{width:min(500px,calc(100vw - 40px));max-width:500px;padding:24px;border:1px solid #cbdad2;border-radius:8px;background:#fbfdfb;box-shadow:0 24px 80px #071e2652;font-family:"Microsoft YaHei","Noto Sans CJK SC",sans-serif;--el-color-primary:#326b59;--el-color-primary-light-3:#5b8f77;--el-color-primary-light-9:#edf4ef;--el-border-color:#cbdad2;--el-text-color-primary:#183f3a;--el-text-color-regular:#587069}
.chat-project-dialog .el-message-box__header{padding:0 26px 14px 0}
.chat-project-dialog .el-message-box__title{color:#183f3a;font:600 24px/1.25 "KaiTi","STKaiti",serif}
.chat-project-dialog .el-message-box__headerbtn{top:18px;right:18px}
.chat-project-dialog .el-message-box__content{padding:0;color:#587069;font-size:14px;line-height:1.75}
.chat-project-dialog .el-message-box__message p{margin:0;line-height:1.75}
.chat-project-dialog .el-message-box__status{color:#92703e}
.chat-project-dialog .el-message-box__input{padding-top:16px}
.chat-project-dialog .el-input__wrapper{min-height:40px;background:#fff;box-shadow:0 0 0 1px #cbdad2 inset;border-radius:6px}
.chat-project-dialog .el-input__wrapper.is-focus{box-shadow:0 0 0 2px #79a998 inset}
.chat-project-dialog .el-input__inner{color:#263a40;font-size:14px}
.chat-project-dialog .el-message-box__btns{gap:9px;margin-top:22px;padding:14px 0 0;border-top:1px solid #e0e9e4}
.chat-project-dialog .el-message-box__btns .el-button{min-width:106px;height:38px;margin:0;padding:9px 16px;border:1px solid #cbdad2;border-radius:6px;background:#edf4ef;color:#356b59;font-family:inherit;font-size:14px}
.chat-project-dialog .el-message-box__btns .el-button:hover{background:#e1eeea;border-color:#92b6a6}
.chat-project-dialog .el-message-box__btns .el-button--primary{background:#326b59;border-color:#326b59;color:#fff}
.chat-project-dialog .el-message-box__btns .el-button--primary:hover{background:#275847;border-color:#275847}
.chat-project-dialog .el-message-box__btns .el-button:focus-visible{outline:2px solid #79a998;outline-offset:3px}
</style>
