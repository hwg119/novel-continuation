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
          <template v-else-if="event.kind === 'action'"><small>待执行操作 · 第 {{ event.data.chapter }} 章 · {{ event.data.model }}</small><h3>{{ labels[event.data.tool] }}</h3><p>{{ event.data.message }}</p><p v-if="event.data.requirements">{{ event.data.requirements }}</p><button :disabled="actionHandled(event.data.action_id) || !!confirming" @click="confirm(event)">{{ actionHandled(event.data.action_id) ? '已处理' : confirming === event.data.action_id ? '启动中…' : '执行' }}</button></template>
          <template v-else-if="event.kind === 'task_result'"><strong>{{ event.data.status === 'completed' ? '任务完成' : '任务未完成' }}</strong><p>{{ event.data.text }}</p>
            <details v-if="event.data.result?.beats?.length"><summary>查看候选分幕 · {{ event.data.result.chapter_title }}</summary><section v-for="beat in event.data.result.beats" :key="beat.num"><h4>{{ beat.name }}</h4><p>{{ beat.desc }}</p></section><p>{{ event.data.result.planning_review?.summary }}</p></details>
            <button v-if="event.data.status === 'completed' && event.data.result?.beats?.length" :disabled="planSaved(event.data.action_id) || saving === event.data.action_id" @click="savePlan(event)">{{ planSaved(event.data.action_id) ? '已保存分幕' : saving === event.data.action_id ? '保存中…' : '保存分幕' }}</button>
            <details v-if="event.data.result?.report || event.data.result?.candidate"><summary>查看结果内容</summary><p>{{ event.data.result.report || event.data.result.candidate }}</p></details>
            <a v-if="event.data.chapter" :href="link(event.data)">{{ event.data.result?.beats?.length ? '打开续写与分幕' : '查看结果' }} →</a></template>
          <template v-else-if="event.kind === 'link'"><a class="chat-feature-link" :href="link(event.data)">{{ event.data.label }} →</a></template>
          <template v-else><p>{{ event.kind === 'user' ? (event.data.text || event.data.message) : chatReplyText(event.data.text || event.data.message || '') }}</p></template>
        </article>
      </div>
      <div class="chat-shortcuts"><span v-if="checkingChapter">正在检查章节…</span>
        <template v-else-if="chapterState"><span class="chat-chapter-state">{{ chapterState.chapter_exists ? '已有正文' : '尚未写作' }} · {{ chapterState.has_beats ? `已保存 ${chapterState.beats_count} 幕大纲` : '暂无分幕大纲' }}</span>
          <template v-for="action in quickActions" :key="action.label"><a v-if="action.view" :href="routePath(action.view as View, projectId, chapterState.number)">{{ action.label }}</a><button v-else type="button" class="subtle" :disabled="busy || !!running" @click="sendText(action.prompt)">{{ action.label }}</button></template>
        </template><span v-else>{{ targetChapter() ? '章节状态暂不可用' : '选择章号后显示快捷操作' }}</span>
      </div>
      <div class="chat-composer" @keydown.capture="guardComposition" @compositionstart="composing = true" @compositionend="composing = false">
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
.chat-context label{display:flex;gap:10px;align-items:center}.chat-context input{width:95px;padding:6px}.chat-context small{margin-left:auto;color:#698585}
.chat-shortcuts{display:flex;align-items:center;flex-wrap:wrap;gap:8px;padding:10px 0;flex-shrink:0;font-size:12px;color:#698585}
.chat-chapter-state{margin-right:auto}.chat-shortcuts button,.chat-shortcuts a{border:1px solid #bed4ca;border-radius:18px;padding:5px 12px;color:#356b59;background:#f8fbf9;font-size:12px;text-decoration:none}.chat-shortcuts button:hover:not(:disabled),.chat-shortcuts a:hover{background:#e1eeea}
.chat-transcript{flex:1;min-height:0;overflow-y:auto;overflow-x:hidden;overscroll-behavior-y:contain;scrollbar-width:thin;padding:10px 12px 16px 0;scroll-padding-bottom:16px}
.chat-transcript:focus-visible{outline:2px solid #79a998;outline-offset:-2px}
.chat-empty{padding:32px 20px;border-left:3px solid #79a998;color:#688486}
.chat-entry{white-space:pre-wrap;overflow-wrap:anywhere;padding:10px 0;margin:8px 0;line-height:1.8}.chat-entry p{margin:0}
.chat-entry.user{width:fit-content;max-width:80%;background:#e5efec;padding:10px 16px;margin-left:auto;margin-right:0;border-radius:12px 12px 0 12px;text-align:left}
.chat-session-row{position:relative;margin-bottom:12px;min-width:0}.chat-session-row>.chat-session{padding-right:36px}
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
