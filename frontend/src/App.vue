<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { api, writeOptions, type Job } from './api'
import ProjectsPanel from './ProjectsPanel.vue'
import SettingsPanel from './SettingsPanel.vue'
import QualityPanel from './QualityPanel.vue'
import RunsPanel from './RunsPanel.vue'
import ConfigPanel from './ConfigPanel.vue'
import WikiPanel from './WikiPanel.vue'
import RelationGraphPanel from './RelationGraphPanel.vue'
import HomePanel from './HomePanel.vue'
const ChatPanel = defineAsyncComponent(() => import('./ChatPanel.vue'))
import RevisionPanel from './RevisionPanel.vue'
import TaskDrawer from './TaskDrawer.vue'
import ConfirmDialog from './ConfirmDialog.vue'
import MessageToast from './MessageToast.vue'
import LearningReader from './LearningReader.vue'
import { acceptConfirm, askConfirm, cancelConfirm, confirmState } from './confirmService'
import { parseRoute, routePath } from './routeState'
import { closeToast, toast, toastState } from './toastService'

type Project = { id: string; name: string; path: string }
type Chapter = { number: number; filename: string; title: string; generated_at: string | null; modified_at: string }
type ChapterFile = { number: number; text: string; revision: string }
type ChapterSummary = { number: number; text: string; exists: boolean; valid: boolean; issues: string[] }
type ChapterIllustration = { exists: boolean; svg: string; png?: string }
type ChapterSearchResult = { chapter: number; segment: number; text: string; relevance: number;
  semantic_score: number | null; matched_terms: string[]; constraint_mode: string }
type Tab = 'chat' | 'home' | 'write' | 'revision' | 'settings' | 'projects' | 'quality' | 'wiki' | 'relationships' | 'runs' | 'config'
type GenerationRequest = { number: number; title: string; beatsLimit: number | null }
type PendingGeneration = { request: GenerationRequest; overwrite: boolean; modelName: string }
type NavItem = { id: Tab; label: string; icon: string }
type BookBookmark = { paragraph: number; savedAt: string }

const navGroups: { label: string; items: NavItem[] }[] = [
  { label: '概览', items: [
    { id: 'home', label: '首页', icon: 'M3 11.5 12 4l9 7.5M5.5 10v9h13v-9M9.5 19v-5h5v5' },
  ] },
  { label: '创作', items: [
    { id: 'write', label: '小说正文', icon: 'M4 5.5c2.7-.8 5.3-.4 8 1.2v13c-2.7-1.6-5.3-2-8-1.2zM20 5.5c-2.7-.8-5.3-.4-8 1.2v13c2.7-1.6 5.3-2 8-1.2z' },
    { id: 'settings', label: '续写与分幕', icon: 'm5 19 3.7-1 9.9-9.9-2.7-2.7L6 15.3 5 19ZM14.5 6.8l2.7 2.7' },
    { id: 'quality', label: '审校与修订', icon: 'M5 4h10l4 4v12H5zM15 4v4h4M8 12h8M8 16h5' },
  ] },
  { label: '知识', items: [
    { id: 'wiki', label: '小说 Wiki', icon: 'M5 4h14v16H5zM8 8h8M8 12h8M8 16h5' },
    { id: 'relationships', label: '人物关系图', icon: 'M6 7a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM18 21a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM18 7a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM8 6l8 0M7.5 7.5l9 9' },
  ] },
  { label: '管理', items: [
    { id: 'projects', label: '工程与母本', icon: 'M3.5 6.5h6l2 2h9v10h-17z' },
    { id: 'runs', label: '运行记录', icon: 'M12 4a8 8 0 1 0 8 8M12 7v5l3 2M17 4h3v3' },
  ] },
]
const configNav: NavItem = { id: 'config', label: '全局配置', icon: 'M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6ZM19 12l2-1-2-3-2 .5-1-2.5h-4L10 6.5 8 6l-2 3-3 .5v4L6 15l1 3 3-.5 2 2.5h3l1-2.5 3 .5 2-3z' }
const activeTab = ref<Tab>('home')
const projects = ref<Project[]>([])
const chapters = ref<Chapter[]>([])
const selectedExportChapters = ref<number[]>([])
const exportingChapters = ref(false)
const projectId = ref('')
const chapterNumber = ref<number | null>(null)
const text = ref('')
const baseline = ref('')
const revision = ref('')
const chapterSummary = ref<ChapterSummary | null>(null)
const chapterIllustration = ref('')
const readingMode = ref<'original' | 'learning'>('original')
const bookMode = ref(false)
const bookIndexOpen = ref(false)
const BOOK_FONT_SIZE_KEY = 'novel-continuation:book-font-size'
const bookFontSizes = [16, 17, 18, 19, 20, 22, 24]
const savedBookFontSize = Number(window.localStorage.getItem(BOOK_FONT_SIZE_KEY))
const bookFontSize = ref(bookFontSizes.includes(savedBookFontSize) ? savedBookFontSize : 18)
const BOOKMARKS_KEY = 'novel-continuation:book-bookmarks'
const bookReader = ref<HTMLElement | null>(null)
function loadBookBookmarks(): Record<string, BookBookmark> {
  try {
    const value = JSON.parse(window.localStorage.getItem(BOOKMARKS_KEY) || '{}')
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {}
  } catch { return {} }
}
const bookBookmarks = ref<Record<string, BookBookmark>>(loadBookBookmarks())
const illustrationLoading = ref(false)
const summaryBusy = ref(false)
const settingsChapter = ref<number | null>(null)
const models = ref<string[]>([])
const modelName = ref('')
const generationNumber = ref(1)
const generationTitle = ref('')
const beatsLimit = ref<number | null>(null)
const focusJob = ref('')
const generating = ref(false)
const consoleLayout = ref({ visible: false, expanded: false })
const wikiPendingCount = ref(0)
const wikiFocusSubject = ref('')
const busy = ref(false)
const message = ref('')
const error = ref('')
const pendingGeneration = ref<PendingGeneration | null>(null)
const searchQuery = ref('')
const searchResults = ref<ChapterSearchResult[]>([])
const searchBusy = ref(false)
const searchDone = ref(false)
const searchError = ref('')
const expandedSearchHit = ref('')
const manuscriptEditor = ref<HTMLTextAreaElement | null>(null)
const routeReady = ref(false)
const PROJECT_KEY = 'novel-continuation:selected-project'
let switchingProject = false
let restoringRoute = false

watch(error, value => {
  if (!value) return
  toast.error(value)
  error.value = ''
})
watch(message, value => {
  if (!value) return
  toast.success(value)
  message.value = ''
})
watch(bookFontSize, value => window.localStorage.setItem(BOOK_FONT_SIZE_KEY, String(value)))
watch(bookBookmarks, value => window.localStorage.setItem(BOOKMARKS_KEY, JSON.stringify(value)), { deep: true })
watch([activeTab, chapterNumber, text], async () => {
  await nextTick()
  resizeManuscript()
})

const project = computed(() => projects.value.find(item => item.id === projectId.value))
const dirty = computed(() => text.value !== baseline.value)
const wordCount = computed(() => text.value.replace(/\s/g, '').length)
const title = computed(() => text.value.split(/\r?\n/, 1)[0]?.trim() || '未命名章节')
const chapterTitles = computed(() => new Map(chapters.value.map(item => [item.number, item.title])))
const chapterIllustrationUrl = computed(() => chapterIllustration.value
  ? chapterIllustration.value.startsWith('data:image/png;') ? chapterIllustration.value
    : `data:image/svg+xml;charset=utf-8,${encodeURIComponent(chapterIllustration.value)}` : '')
const bookParagraphs = computed(() => {
  const lines = text.value.replace(/\r\n/g, '\n').split('\n')
  const firstContent = lines.findIndex(line => line.trim())
  if (firstContent >= 0 && lines[firstContent].trim() === title.value) lines.splice(firstContent, 1)
  return lines.join('\n').trim().split(/\n+/).map(item => item.trim()).filter(Boolean)
})
const orderedChapterNumbers = computed(() => chapters.value.map(item => item.number).sort((a, b) => a - b))
const previousChapter = computed(() => {
  const index = orderedChapterNumbers.value.indexOf(chapterNumber.value || -1)
  return index > 0 ? orderedChapterNumbers.value[index - 1] : null
})
const nextChapter = computed(() => {
  const index = orderedChapterNumbers.value.indexOf(chapterNumber.value || -1)
  return index >= 0 && index < orderedChapterNumbers.value.length - 1 ? orderedChapterNumbers.value[index + 1] : null
})
const bookmarkKey = computed(() => `${projectId.value}:${chapterNumber.value || 0}`)
const currentBookmark = computed(() => bookBookmarks.value[bookmarkKey.value] || null)

function enterBookMode() { readingMode.value = 'original'; bookIndexOpen.value = false; bookMode.value = true }
function leaveBookMode() { bookMode.value = false; bookIndexOpen.value = false }
function saveBookBookmark() {
  const paragraphs = Array.from(bookReader.value?.querySelectorAll<HTMLElement>('[data-book-paragraph]') || [])
  const readingLine = window.innerHeight * 0.5
  const elementAtReadingLine = document.elementFromPoint(window.innerWidth * 0.5, readingLine)
    ?.closest<HTMLElement>('[data-book-paragraph]')
  let paragraph = Number(elementAtReadingLine?.dataset.bookParagraph ?? -1)
  if (!Number.isInteger(paragraph) || paragraph < 0) {
    paragraph = 0
    for (let index = 0; index < paragraphs.length; index += 1) {
      const bounds = paragraphs[index].getBoundingClientRect()
      if (bounds.top <= readingLine) paragraph = index
      else break
    }
  }
  bookBookmarks.value[bookmarkKey.value] = { paragraph, savedAt: new Date().toISOString() }
  toast.success(`已记录第 ${paragraph + 1} 段的阅读位置`)
}
async function goToBookBookmark() {
  if (!currentBookmark.value) return
  await nextTick()
  bookReader.value?.querySelector<HTMLElement>(`[data-book-paragraph="${currentBookmark.value.paragraph}"]`)
    ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
function removeBookBookmark() {
  const next = { ...bookBookmarks.value }
  delete next[bookmarkKey.value]
  bookBookmarks.value = next
  toast.success('本章书签已移除')
}

function clearChapterSearch() {
  searchQuery.value = ''; searchResults.value = []; searchDone.value = false; searchError.value = ''
  expandedSearchHit.value = ''
}
function searchHitKey(result: ChapterSearchResult) { return `${result.chapter}-${result.segment}` }
function toggleSearchHit(result: ChapterSearchResult) {
  const key = searchHitKey(result)
  expandedSearchHit.value = expandedSearchHit.value === key ? '' : key
}
function sourceRangeIgnoringWhitespace(source: string, excerpt: string): [number, number] | null {
  const sourceIndexes: number[] = []
  let normalizedSource = ''
  for (let index = 0; index < source.length; index += 1) {
    if (!/\s/.test(source[index])) { normalizedSource += source[index]; sourceIndexes.push(index) }
  }
  const normalizedExcerpt = excerpt.replace(/\s/g, '')
  if (!normalizedExcerpt) return null
  const start = normalizedSource.indexOf(normalizedExcerpt)
  if (start < 0) return null
  const endIndex = start + normalizedExcerpt.length - 1
  return [sourceIndexes[start], sourceIndexes[endIndex] + 1]
}
function resizeManuscript() {
  const editor = manuscriptEditor.value
  if (!editor || activeTab.value !== 'write') return
  editor.style.height = 'auto'
  editor.style.height = `${Math.max(editor.scrollHeight, window.innerHeight * 0.7)}px`
}
async function openSearchResult(result: ChapterSearchResult) {
  if (!await chooseChapter(result.chapter)) return
  await nextTick()
  const editor = manuscriptEditor.value
  if (!editor) return
  const range = sourceRangeIgnoringWhitespace(text.value, result.text)
  if (!range) {
    editor.scrollIntoView({ behavior: 'smooth', block: 'start' })
    message.value = `已打开第 ${result.chapter} 章；向量片段与当前正文存在格式差异，未能精确选中。`
    return
  }
  editor.focus({ preventScroll: true })
  editor.setSelectionRange(range[0], range[1])
  const ratio = range[0] / Math.max(text.value.length, 1)
  const editorTop = window.scrollY + editor.getBoundingClientRect().top
  window.scrollTo({ top: Math.max(0, editorTop + ratio * editor.scrollHeight - 120), behavior: 'smooth' })
  message.value = `已定位第 ${result.chapter} 章的命中片段。`
}
async function searchChapters() {
  const query = searchQuery.value.trim()
  if (!projectId.value || !query || searchBusy.value) return
  searchBusy.value = true; searchDone.value = false; searchError.value = ''
  try {
    const result = await api<{ results: ChapterSearchResult[] }>(
      `/api/projects/${projectId.value}/search/chapters`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, limit: 12 }) })
    searchResults.value = result.results; searchDone.value = true; expandedSearchHit.value = ''
  } catch (cause) { searchResults.value = []; searchDone.value = true; searchError.value = String(cause) }
  finally { searchBusy.value = false }
}

async function loadProjects(preferred?: string) {
  try { const previous = preferred || projectId.value || sessionStorage.getItem(PROJECT_KEY) || ''
    projects.value = await api<Project[]>('/api/projects')
    const next = projects.value.find(item => item.id === previous)?.id || projects.value[0]?.id
    if (next) { sessionStorage.setItem(PROJECT_KEY, next); await chooseProject(next) }
    else { projectId.value = ''; chapters.value = []; chapterNumber.value = null }
  } catch (cause) { error.value = String(cause) }
}
async function switchProject(event: Event) {
  const select = event.target as HTMLSelectElement
  const next = select.value
  if (!next || next === projectId.value) return
  if (dirty.value && !await askConfirm({ title: '放弃未保存的正文？',
    message: '切换工程会刷新页面，当前章节尚未保存的修改将丢失。', symbol: '换', confirmLabel: '放弃修改并切换' })) {
    select.value = projectId.value
    return
  }
  switchingProject = true
  sessionStorage.setItem(PROJECT_KEY, next)
  window.location.assign(routePath('home', next, null))
}
async function loadConfig() {
  try { const config = await api<{ llm_configs: Record<string, unknown>; default_llm_config_name: string; last_llm_config_name: string }>('/api/config')
    models.value = Object.keys(config.llm_configs || {})
    modelName.value = config.default_llm_config_name || config.last_llm_config_name || models.value[0] || '' }
  catch (cause) { error.value = String(cause) }
}
async function loadChapters(selectLast = false) {
  if (!projectId.value) return
  chapters.value = await api<Chapter[]>(`/api/projects/${projectId.value}/chapters`)
  selectedExportChapters.value = selectedExportChapters.value.filter(number =>
    chapters.value.some(item => item.number === number))
  generationNumber.value = (chapters.value.at(-1)?.number || 0) + 1
  if (selectLast && chapters.value.length) await chooseChapter(chapters.value.at(-1)!.number)
}
async function loadWikiPending(project = projectId.value) {
  if (!project) { wikiPendingCount.value = 0; return }
  try { const result = await api<{ count: number }>(`/api/projects/${project}/wiki/pending`)
    if (project === projectId.value) wikiPendingCount.value = result.count }
  catch { if (project === projectId.value) wikiPendingCount.value = 0 }
}
async function chooseProject(id: string) {
  if (dirty.value && !restoringRoute && !await askConfirm({ title: '切换到其他工程？',
    message: '当前章节尚未保存，继续后这些修改将丢失。', symbol: '换', confirmLabel: '放弃修改并切换' })) return
  error.value = ''; message.value = ''; projectId.value = id; chapterNumber.value = null
  selectedExportChapters.value = []
  text.value = baseline.value = ''; chapterIllustration.value = ''
  clearChapterSearch()
  try { await loadChapters(true); await loadWikiPending(id) } catch (cause) { error.value = String(cause) }
}
async function chooseChapter(number: number): Promise<boolean> {
  if (dirty.value && !restoringRoute && !await askConfirm({ title: `打开第 ${number} 章？`,
    message: '当前章节尚未保存，继续后这些修改将丢失。', symbol: '章', confirmLabel: '放弃修改并打开' })) return false
  error.value = ''; message.value = ''
  try { const chapter = await api<ChapterFile>(`/api/projects/${projectId.value}/chapters/${number}`)
    chapterNumber.value = number; text.value = baseline.value = chapter.text
    revision.value = chapter.revision
    await Promise.all([loadChapterSummary(number), loadChapterIllustration(number)])
    return true
  } catch (cause) { error.value = String(cause); return false }
}
async function loadChapterIllustration(number = chapterNumber.value, project = projectId.value) {
  if (!project || number === null) { chapterIllustration.value = ''; return }
  illustrationLoading.value = true
  try {
    const result = await api<ChapterIllustration>(`/api/projects/${project}/chapters/${number}/illustration`)
    if (projectId.value === project && chapterNumber.value === number) {
      chapterIllustration.value = result.exists ? result.png || result.svg : ''
    }
  } catch {
    if (projectId.value === project && chapterNumber.value === number) chapterIllustration.value = ''
  } finally {
    if (projectId.value === project && chapterNumber.value === number) illustrationLoading.value = false
  }
}
async function loadChapterSummary(number = chapterNumber.value) {
  if (!projectId.value || number === null) { chapterSummary.value = null; return }
  try { chapterSummary.value = await api<ChapterSummary>(`/api/projects/${projectId.value}/chapters/${number}/summary`) }
  catch { chapterSummary.value = null }
}
async function regenerateSummary() {
  if (!projectId.value || chapterNumber.value === null || !modelName.value || summaryBusy.value) return
  if (dirty.value) { toast.info('请先保存当前正文，再重新生成摘要。'); return }
  const project = projectId.value; const number = chapterNumber.value
  if (!await askConfirm({
    title: chapterSummary.value?.exists ? `重新生成第 ${number} 章摘要？` : `生成第 ${number} 章摘要？`,
    message: chapterSummary.value?.exists
      ? '现有摘要将被新结果替换；正文不会发生修改。'
      : '模型将读取本章正文并生成供后续章节规划使用的剧情摘要。',
    symbol: '摘',
    confirmLabel: chapterSummary.value?.exists ? '使用此模型重新生成' : '使用此模型生成',
    modelName: modelName.value,
  })) return
  summaryBusy.value = true; error.value = ''
  try {
    const started = await api<{ id: string }>(`/api/projects/${project}/chapters/${number}/summary`,
      writeOptions('POST', { number, model_name: modelName.value }))
    focusJob.value = started.id
    toast.info(`正在使用 ${modelName.value} 生成第 ${number} 章摘要；进度可在底部控制台查看。`)
    const poll = async () => { try {
      const job = await api<Job>(`/api/projects/${project}/jobs/${started.id}`)
      if (job.status === 'running') { setTimeout(poll, 1200); return }
      summaryBusy.value = false
      if (job.status !== 'completed') { toast.error(job.message); return }
      if (projectId.value === project && chapterNumber.value === number) await loadChapterSummary(number)
      toast.success(`第 ${number} 章摘要已生成并保存。`)
    } catch { summaryBusy.value = false } }
    void poll()
  } catch { summaryBusy.value = false }
}
async function save() {
  if (chapterNumber.value === null || !dirty.value || busy.value) return
  busy.value = true; error.value = ''; message.value = ''
  try { const result = await api<{ revision: string; vector_status?: string; vector_job_id?: string | null }>(
      `/api/projects/${projectId.value}/chapters/${chapterNumber.value}`,
      writeOptions('PUT', { text: text.value, revision: revision.value }))
    revision.value = result.revision; baseline.value = text.value
    if (result.vector_job_id) taskStarted(result.vector_job_id)
    message.value = result.vector_status === 'scheduled'
      ? '已保存；正在后台更新本章向量。'
      : result.vector_status === 'deferred'
        ? '已保存；当前有其他任务运行，本章向量待手动更新。'
        : '已保存；旧版本已留存。向量库尚未初始化。'
    void loadWikiPending() }
  catch (cause) { error.value = String(cause) }
  finally { busy.value = false }
}
function taskStarted(id: string) { focusJob.value = id; message.value = `任务 ${id} 已开始，可在运行记录查看。` }
async function generate(request?: GenerationRequest) {
  if (generating.value) return
  if (request) { generationNumber.value = request.number; generationTitle.value = request.title; beatsLimit.value = request.beatsLimit }
  if (!projectId.value || !modelName.value || generationNumber.value < 1) return
  const generationRequest = request || {
    number: generationNumber.value, title: generationTitle.value, beatsLimit: beatsLimit.value,
  }
  const exists = chapters.value.some(item => item.number === generationRequest.number)
  pendingGeneration.value = { request: generationRequest, overwrite: exists, modelName: modelName.value }
}
async function startGeneration(request: GenerationRequest, overwrite: boolean, selectedModel: string) {
  if (generating.value) return
  generationNumber.value = request.number
  generationTitle.value = request.title
  beatsLimit.value = request.beatsLimit
  generating.value = true
  try { const result = await api<{ id: string }>(`/api/projects/${projectId.value}/generate`,
    writeOptions('POST', { number: request.number, model_name: selectedModel,
      title: request.title, beats_limit: request.beatsLimit, overwrite }))
    taskStarted(result.id)
    const number = request.number; const project = projectId.value
    const poll = async () => { try { const job = await api<Job>(`/api/projects/${project}/jobs/${result.id}`)
      if (job.status === 'running') { setTimeout(poll, 2500); return }
      generating.value = false
      if (job.status === 'completed' && projectId.value === project) { await loadChapters(); await chooseChapter(number); void loadWikiPending(project) }
      else if (job.status !== 'completed') error.value = job.message }
      catch (cause) { generating.value = false; error.value = String(cause) }
    }; void poll()
  } catch (cause) { generating.value = false; error.value = String(cause) }
}
function confirmGeneration() {
  const pending = pendingGeneration.value
  pendingGeneration.value = null
  if (pending) void startGeneration(pending.request, pending.overwrite, pending.modelName)
}
async function exportWord() {
  if (!projectId.value || chapterNumber.value === null) return
  if (dirty.value) { error.value = '请先保存正文，再导出 Word。'; return }
  try { await api(`/api/projects/${projectId.value}/chapters/${chapterNumber.value}/export`, writeOptions('POST'))
    window.location.href = `/api/projects/${projectId.value}/chapters/${chapterNumber.value}/export`
    message.value = 'Word 已生成并开始下载。' }
  catch (cause) { error.value = String(cause) }
}
function toggleAllExportChapters() {
  selectedExportChapters.value = selectedExportChapters.value.length === chapters.value.length
    ? [] : chapters.value.map(item => item.number)
}
async function exportSelectedChapters() {
  if (!projectId.value || !selectedExportChapters.value.length || exportingChapters.value) return
  if (dirty.value) { error.value = '请先保存当前正文，再导出多章 Word。'; return }
  exportingChapters.value = true
  try {
    await api(`/api/projects/${projectId.value}/exports/chapters`,
      writeOptions('POST', { numbers: selectedExportChapters.value }))
    window.location.href = `/api/projects/${projectId.value}/exports/chapters`
    message.value = `已合并 ${selectedExportChapters.value.length} 章并开始下载。`
  } catch (cause) { error.value = String(cause) }
  finally { exportingChapters.value = false }
}
function openSettings(number: number) { settingsChapter.value = number; activeTab.value = 'settings' }
function switchTab(tab: Tab) {
  leaveBookMode()
  if (tab === 'settings') settingsChapter.value = null
  activeTab.value = tab
}
async function openChapter(number: number) { if (await chooseChapter(number)) activeTab.value = 'write' }
function applyRevision(candidate: string) { text.value = candidate; activeTab.value = 'write' }
async function openWikiChapter(number: number) { if (await chooseChapter(number)) activeTab.value = 'write' }
function currentRoutePath() {
  const number = activeTab.value === 'settings' ? (settingsChapter.value ?? chapterNumber.value) : chapterNumber.value
  return routePath(activeTab.value, projectId.value, number)
}
watch([activeTab, projectId, chapterNumber, settingsChapter], () => {
  if (!routeReady.value) return
  const path = currentRoutePath()
  if (window.location.pathname !== path) window.history.pushState({}, '', path)
}, { flush: 'post' })
watch(activeTab, tab => {
  if (tab === 'write' && chapterNumber.value !== null) void loadChapterIllustration()
})
async function restoreRoute() {
  const requested = parseRoute(window.location.pathname)
  if (!requested) { window.history.replaceState({}, '', currentRoutePath()); return }
  if (dirty.value && !await askConfirm({ title: '离开当前页面？',
    message: '当前章节尚未保存，继续后这些修改将丢失。', symbol: '离', confirmLabel: '放弃修改并离开' })) {
    window.history.pushState({}, '', currentRoutePath())
    return
  }
  routeReady.value = false
  restoringRoute = true
  try {
    const validProject = requested.projectId && projects.value.some(item => item.id === requested.projectId)
    if (validProject && requested.projectId !== projectId.value) {
      await chooseProject(requested.projectId)
      sessionStorage.setItem(PROJECT_KEY, requested.projectId)
    }
    if (requested.chapterNumber && projectId.value &&
        chapters.value.some(item => item.number === requested.chapterNumber))
      await chooseChapter(requested.chapterNumber)
    settingsChapter.value = requested.view === 'settings' ? requested.chapterNumber : null
    activeTab.value = requested.view === 'config' || !requested.projectId || validProject
      ? requested.view : 'home'
  } finally {
    restoringRoute = false
    await nextTick()
    routeReady.value = true
    const canonical = currentRoutePath()
    if (window.location.pathname !== canonical) window.history.replaceState({}, '', canonical)
  }
}
async function initialize() {
  const requested = parseRoute(window.location.pathname)
  await loadProjects(requested?.projectId || undefined)
  if (requested?.chapterNumber && requested.projectId === projectId.value &&
      chapters.value.some(item => item.number === requested.chapterNumber))
    await chooseChapter(requested.chapterNumber)
  settingsChapter.value = requested?.view === 'settings' ? requested.chapterNumber : null
  activeTab.value = requested && (requested.view === 'config' ||
    requested.projectId === projectId.value) ? requested.view : 'home'
  await nextTick()
  routeReady.value = true
  const canonical = currentRoutePath()
  if (window.location.pathname !== canonical) window.history.replaceState({}, '', canonical)
  void loadConfig()
}
function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && bookMode.value) { leaveBookMode(); return }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
    event.preventDefault(); void save()
  }
}
function onBeforeUnload(event: BeforeUnloadEvent) { if (dirty.value && !switchingProject) event.preventDefault() }
onMounted(() => { void initialize()
  window.addEventListener('keydown', onKeydown); window.addEventListener('beforeunload', onBeforeUnload)
  window.addEventListener('popstate', restoreRoute); window.addEventListener('resize', resizeManuscript) })
onUnmounted(() => { window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('beforeunload', onBeforeUnload); window.removeEventListener('popstate', restoreRoute)
  window.removeEventListener('resize', resizeManuscript) })
</script>

<template><div class="shell" :class="{ 'book-mode': bookMode, 'chat-mode': activeTab === 'chat' }"><aside class="sidebar">
  <div class="brand"><span class="brand-mark">续</span><div><strong>续写工作台</strong><small>长篇小说 · 本机工程</small></div></div>
  <div class="project-switcher"><label for="project">当前工程</label><span>{{ chapters.length }} 章<span v-if="wikiPendingCount"> · Wiki 待更新 {{ wikiPendingCount }}</span></span>
    <select id="project" :value="projectId" @change="switchProject">
      <option v-if="!projects.length" value="">暂无工程</option><option v-for="item in projects" :key="item.id" :value="item.id">{{ item.name }}</option></select></div>
  <div class="workspace-entry"><span class="workspace-entry-label">创作入口</span>
    <button class="conversation-entry" :class="{ active: activeTab === 'chat' }" :aria-current="activeTab === 'chat' ? 'page' : undefined" @click="switchTab('chat')">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 4h16v12H9l-5 4V4ZM8 8h8M8 12h5" /></svg>
      <span><strong>对话创作</strong><small>说出目标，一起推进</small></span><span aria-hidden="true">↗</span>
    </button>
  </div>
  <div class="workbench-divider"><span>功能工作台</span><small>查看 · 管理 · 编辑</small></div>
  <nav class="primary-nav" aria-label="功能工作台">
    <section v-for="group in navGroups" :key="group.label" class="nav-group"><span class="nav-group-label">{{ group.label }}</span>
      <button v-for="item in group.items" :key="item.id" :class="{ active: activeTab === item.id }" @click="switchTab(item.id)">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path :d="item.icon" /></svg><span>{{ item.label }}</span>
        <b v-if="item.id === 'wiki' && wikiPendingCount" class="nav-count" :aria-label="`${wikiPendingCount} 章 Wiki 待更新`">{{ wikiPendingCount }}</b>
        <i v-if="item.id === 'runs' && generating" class="nav-running" aria-label="任务运行中"></i>
      </button></section>
  </nav>
  <div class="sidebar-bottom"><button class="config-nav" :class="{ active: activeTab === configNav.id }" @click="switchTab(configNav.id)">
    <svg viewBox="0 0 24 24" aria-hidden="true"><path :d="configNav.icon" /></svg><span>{{ configNav.label }}</span></button>
    <div class="side-foot">本机运行 · 数据保留在原工程目录</div></div>
</aside><main class="main" :class="{ 'console-visible': consoleLayout.visible, 'console-expanded': consoleLayout.expanded }">
  <header v-if="!bookMode" class="toolbar"><div class="crumb">{{ project?.name || '请选择工程' }} <span v-if="chapterNumber !== null">/ 第 {{ chapterNumber }} 章</span></div>
    <div class="toolbar-actions"><label class="model-picker">模型 <select v-model="modelName"><option v-for="name in models" :key="name">{{ name }}</option></select></label>
      <span class="save-state" :class="{ unsaved: dirty }">{{ dirty ? '未保存修改' : '已同步' }}</span>
      <button type="button" class="save-button" :disabled="!dirty || busy" @click="save">{{ busy ? '保存中…' : '保存章节' }}</button></div></header>
  <HomePanel v-if="activeTab === 'home'" :project-id="projectId" :project-name="project?.name || ''"
    :chapters="chapters" :model-name="modelName" :wiki-pending-count="wikiPendingCount" :generating="generating"
    @generate="generate" @settings="openSettings" @chapter="openChapter" @navigate="switchTab" />
  <ChatPanel v-else-if="activeTab === 'chat'" :project-id="projectId" :chapter-number="chapterNumber" :model-name="modelName"
    @task="taskStarted" />
  <template v-else-if="activeTab === 'write'"><div class="reading-layout">
    <aside class="reading-index" :class="{ open: bookIndexOpen }" aria-label="本书章节目录"><div class="reading-index-head"><strong>章节目录</strong><span>{{ chapters.length }} 章</span><button v-if="bookMode" type="button" aria-label="关闭章节目录" @click="bookIndexOpen = false">×</button></div>
      <div v-if="!bookMode && chapters.length" class="chapter-export-tools"><button type="button" @click="toggleAllExportChapters">{{ selectedExportChapters.length === chapters.length ? '清空' : '全选' }}</button><span>已选 {{ selectedExportChapters.length }} 章</span><button type="button" class="chapter-export-submit" :disabled="!selectedExportChapters.length || exportingChapters" @click="exportSelectedChapters">{{ exportingChapters ? '生成中…' : '合并导出' }}</button></div>
      <nav class="reading-chapters" aria-label="小说章节"><div v-for="item in [...chapters].reverse()" :key="item.number" class="reading-chapter-row"><label v-if="!bookMode" class="chapter-export-check" :aria-label="`选择第 ${item.number} 章导出`"><input v-model="selectedExportChapters" type="checkbox" :value="item.number"></label><button type="button"
        class="reading-chapter" :class="{ active: item.number === chapterNumber }" :aria-current="item.number === chapterNumber ? 'page' : undefined"
        @click="chooseChapter(item.number)"><span>第 {{ item.number }} 章</span></button></div>
        <p v-if="!chapters.length" class="reading-empty">暂无章节。请先导入母本，或生成新章。</p></nav></aside>
    <div class="editor-wrap">
    <nav v-if="bookMode" class="book-toolbar" aria-label="看书模式工具栏">
      <button type="button" @click="bookIndexOpen = !bookIndexOpen">目录</button>
      <span>第 {{ chapterNumber }} 章</span>
      <div><button type="button" class="book-chapter-nav" :disabled="previousChapter === null" @click="previousChapter !== null && chooseChapter(previousChapter)">上一章</button><button type="button" class="book-chapter-nav" :disabled="nextChapter === null" @click="nextChapter !== null && chooseChapter(nextChapter)">下一章</button><label class="book-font-picker">字号<select v-model.number="bookFontSize" aria-label="看书模式正文字号"><option v-for="size in bookFontSizes" :key="size" :value="size">{{ size }}</option></select></label><button type="button" class="book-exit" @click="leaveBookMode">退出看书模式</button></div>
    </nav>
    <aside v-if="bookMode" class="book-bookmark-dock" aria-label="阅读书签">
      <button type="button" class="book-bookmark-save" @click="saveBookBookmark"><b>签</b><span>{{ currentBookmark ? '更新书签' : '加书签' }}</span></button>
      <button v-if="currentBookmark" type="button" class="book-bookmark-return" @click="goToBookBookmark"><b>返</b><span>回到书签</span></button>
      <button v-if="currentBookmark" type="button" class="book-bookmark-remove" @click="removeBookBookmark"><b>×</b><span>移除</span></button>
    </aside>
    <section v-if="!bookMode" class="story-search" :class="{ expanded: searchDone || searchBusy }" aria-labelledby="story-search-title">
      <div class="story-search-intro"><span>全书语义检索 · {{ chapters.length }} 章</span><h2 id="story-search-title">沿着一句话，找回故事里的线索。</h2>
        <p>不必记得原句。输入人物、事件、场景或一段模糊记忆，向量检索会从整部小说中找到语义相近的正文。</p></div>
      <form class="story-search-form" role="search" @submit.prevent="searchChapters">
        <label class="sr-only" for="chapter-semantic-search">搜索小说全文</label><input id="chapter-semantic-search" v-model="searchQuery" maxlength="500" placeholder="例如：主角离开故乡后去了哪里？" autocomplete="off">
        <button type="submit" :disabled="searchBusy || !searchQuery.trim()"><span v-if="searchBusy" class="button-spinner" aria-hidden="true"></span>{{ searchBusy ? '正在检索' : '搜索全书' }}</button>
        <button v-if="searchDone || searchQuery" class="story-search-clear" type="button" @click="clearChapterSearch">清除</button></form>
      <div v-if="searchDone || searchBusy" class="story-search-results" aria-live="polite">
        <p v-if="searchBusy" class="story-search-state">正在穿过章节与段落，寻找最接近的叙事线索……</p>
        <p v-else-if="searchError" class="story-search-state error">{{ searchError }}</p>
        <template v-else><div class="story-search-summary"><strong>找到 {{ searchResults.length }} 处相关片段</strong><span>相关度为本次结果内的相对排序</span></div>
          <div class="story-search-grid"><article v-for="result in searchResults" :key="searchHitKey(result)" class="story-search-hit" :class="{ expanded: expandedSearchHit === searchHitKey(result) }">
            <button type="button" class="story-search-preview" :aria-expanded="expandedSearchHit === searchHitKey(result)" @click="toggleSearchHit(result)">
              <span class="story-search-hit-head"><b>第 {{ result.chapter }} 章 · 片段 {{ result.segment }}</b><em>{{ result.relevance }}%</em></span>
              <strong>{{ chapterTitles.get(result.chapter) || `第 ${result.chapter} 章` }}</strong><small>{{ result.text }}</small><span class="story-search-expand">{{ expandedSearchHit === searchHitKey(result) ? '收起完整片段' : '展开完整片段' }} <i>{{ expandedSearchHit === searchHitKey(result) ? '⌃' : '⌄' }}</i></span>
            </button><footer><span>命中正文块</span><button type="button" @click="openSearchResult(result)">打开本章并定位 <i>↗</i></button></footer>
          </article></div><p v-if="!searchResults.length" class="story-search-state">没有找到相关片段。换一种叙述方式，或检查向量库是否覆盖了目标章节。</p></template>
      </div>
    </section>
    <template v-if="chapterNumber !== null"><div v-if="!bookMode" class="editor-meta"><span>第 {{ chapterNumber }} 章</span><span>{{ readingMode === 'original' ? `${wordCount.toLocaleString('zh-CN')} 字 · Ctrl+S 保存` : '分级双语阅读' }}</span></div>
      <div v-if="!bookMode" class="reading-view-row"><div class="reading-mode-switch" role="tablist" aria-label="正文阅读模式"><button role="tab" :aria-selected="readingMode === 'original'" :class="{ active: readingMode === 'original' }" @click="readingMode = 'original'">原文与编辑</button><button role="tab" :aria-selected="readingMode === 'learning'" :class="{ active: readingMode === 'learning' }" @click="readingMode = 'learning'">英语学习版</button></div><button type="button" class="enter-book-mode" @click="enterBookMode">看书模式</button></div>
      <h1>{{ title }}</h1>
      <figure v-if="chapterIllustrationUrl" class="chapter-front-illustration"><img :src="chapterIllustrationUrl" :alt="`第 ${chapterNumber} 章插图`"><figcaption>本章插图</figcaption></figure>
      <div v-else-if="illustrationLoading" class="chapter-front-illustration loading" aria-live="polite">正在载入本章插图…</div>
      <template v-if="readingMode === 'original'"><article v-if="bookMode" ref="bookReader" class="book-reader" :style="{ fontSize: `${bookFontSize}px` }" aria-label="章节正文"><p v-for="(paragraph, index) in bookParagraphs" :key="index" :data-book-paragraph="index" :class="{ bookmarked: currentBookmark?.paragraph === index }">{{ paragraph }}</p></article><div v-else class="manuscript"><div class="manuscript-rule" aria-hidden="true"></div><textarea ref="manuscriptEditor" v-model="text" spellcheck="false" aria-label="章节全文"></textarea></div>
      <div v-if="!bookMode" class="editor-actions"><button class="subtle" :disabled="dirty" @click="exportWord">导出 Word</button>
        <button class="subtle" @click="activeTab = 'revision'">查看修订稿／高亮对照</button>
        <button class="subtle" @click="activeTab = 'quality'">整章修订／审校</button></div>
      <section v-if="!bookMode" class="chapter-summary-card" :class="{ invalid: chapterSummary && !chapterSummary.valid }">
        <div class="chapter-summary-head"><div><span class="eyebrow">章节摘要 · 第 {{ chapterNumber }} 章</span><h2>下一章规划的承接依据</h2></div>
          <span class="summary-state" :class="{ valid: chapterSummary?.valid, invalid: chapterSummary && !chapterSummary.valid }">{{ chapterSummary?.valid ? '可用' : chapterSummary?.exists ? '需重新生成' : '尚未生成' }}</span></div>
        <p v-if="chapterSummary?.text" class="chapter-summary-text">{{ chapterSummary.text }}</p>
        <p v-else class="chapter-summary-empty">当前章节还没有摘要。生成下一章分幕前建议先生成。</p>
        <ul v-if="chapterSummary?.issues?.length" class="summary-issues"><li v-for="issue in chapterSummary.issues" :key="issue">{{ issue }}</li></ul>
        <div class="chapter-summary-actions"><small>生成第 {{ (chapterNumber || 0) + 1 }} 章分幕时会优先使用这里的内容。</small><button type="button" :disabled="summaryBusy || dirty || !modelName" @click="regenerateSummary"><span v-if="summaryBusy" class="button-spinner" aria-hidden="true"></span>{{ summaryBusy ? '生成中…' : chapterSummary?.exists ? '重新生成摘要' : '生成摘要' }}</button></div>
      </section></template>
      <LearningReader v-else :project-id="projectId" :chapter-number="chapterNumber" :model-name="modelName" :dirty="dirty" :source-revision="revision" @task="taskStarted" />
    </template><div v-else class="welcome"><span>写作从这里继续</span><h1>选一章，接着写。</h1><p>可以导入母本，也可以先在续写设定中规划新章。</p></div>
    </div></div></template>
  <RevisionPanel v-else-if="activeTab === 'revision' && chapterNumber !== null" :project-id="projectId" :chapter-number="chapterNumber" :current-text="text" @back="activeTab = 'write'" />
  <SettingsPanel v-else-if="activeTab === 'settings'" :project-id="projectId" :model-name="modelName" :current-chapter="settingsChapter || chapterNumber" :generating="generating" @task="taskStarted" @chapter="settingsChapter = $event" @generate="generate" />
  <ProjectsPanel v-else-if="activeTab === 'projects'" :project-id="projectId" :project-name="project?.name || ''" @changed="loadProjects()" @task="taskStarted" />
  <QualityPanel v-else-if="activeTab === 'quality'" :project-id="projectId" :chapter-number="chapterNumber" :model-name="modelName" :text="text" :revision="revision" :dirty="dirty" @task="taskStarted" @apply="applyRevision" />
  <WikiPanel v-else-if="activeTab === 'wiki'" :project-id="projectId" :model-name="modelName" :models="models" :current-chapter="chapterNumber" :focus-subject="wikiFocusSubject" @task="taskStarted" @open-chapter="openWikiChapter" @open-graph="activeTab = 'relationships'" @updated="wikiPendingCount = $event" />
  <RelationGraphPanel v-else-if="activeTab === 'relationships'" :project-id="projectId" :current-chapter="chapterNumber" @back="activeTab = 'wiki'" @open-chapter="openWikiChapter" @open-subject="wikiFocusSubject = $event; activeTab = 'wiki'" />
  <RunsPanel v-else-if="activeTab === 'runs'" :project-id="projectId" :focus-job="focusJob" />
  <ConfigPanel v-else :key="activeTab" @changed="loadConfig" />
  <TaskDrawer v-if="!bookMode && activeTab !== 'chat'" :project-id="projectId" :focus-job="focusJob" @view="activeTab = 'runs'" @layout="consoleLayout = $event" />
  <ConfirmDialog dialog-id="generation-confirm" :open="Boolean(pendingGeneration)"
    :symbol="pendingGeneration?.overwrite ? '覆' : '写'"
    :title="pendingGeneration?.overwrite ? '确认覆盖并续写？' : '确认开始续写？'"
    :message="pendingGeneration?.overwrite
      ? `将使用模型“${pendingGeneration.modelName}”重新生成第 ${pendingGeneration.request.number} 章。当前正文会先保留版本快照，再被新正文覆盖。`
      : `将使用模型“${pendingGeneration?.modelName || ''}”生成第 ${pendingGeneration?.request.number || ''} 章。请确认模型选择正确。`"
    :confirm-label="pendingGeneration?.overwrite ? '保留快照并续写' : '使用此模型续写'"
    :model-name="pendingGeneration?.modelName || ''"
    @confirm="confirmGeneration" @cancel="pendingGeneration = null" />
  <ConfirmDialog dialog-id="global-confirm" :open="confirmState.open" :symbol="confirmState.symbol"
    :title="confirmState.title" :message="confirmState.message" :confirm-label="confirmState.confirmLabel"
    :cancel-label="confirmState.cancelLabel" :model-name="confirmState.modelName"
    @confirm="acceptConfirm" @cancel="cancelConfirm" />
  <MessageToast :message="toastState.message" :message-id="toastState.id" :kind="toastState.kind"
    :duration="toastState.duration" @close="closeToast" />
</main></div></template>
