<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { api, writeOptions, type Job } from './api'
import { askConfirm } from './confirmService'

const props = defineProps<{ projectId: string; projectName: string }>()
const emit = defineEmits<{ changed: []; task: [id: string] }>()
const name = ref('')
const parent = ref('')
const existing = ref('')
const files = ref<File[]>([])
const pattern = ref('')
const rangeStart = ref(1)
const rangeEnd = ref(1)
const chapterCount = ref(0)
const chapterNumbers = ref<number[]>([])
const indexedChapters = ref<number[]>([])
const segments = ref<number | null>(null)
const feedback = ref('')
const error = ref('')
const busy = ref(false)
const dialog = ref<'create' | 'import' | null>(null)

async function pick(mode: 'directory' | 'project_file', target: 'parent' | 'existing') {
  try {
    const result = await api<{ path: string }>('/api/local-picker', writeOptions('POST', { mode }))
    if (result.path) { if (target === 'parent') parent.value = result.path; else existing.value = result.path }
  } catch (cause) { error.value = String(cause) }
}

async function action(run: () => Promise<void>) {
  busy.value = true; error.value = ''; feedback.value = ''
  try { await run() } catch (cause) { error.value = String(cause) }
  finally { busy.value = false }
}
async function create() { await action(async () => {
  await api('/api/projects', writeOptions('POST', { name: name.value, parent: parent.value }))
  feedback.value = '工程已创建。'; dialog.value = null; emit('changed')
}) }
async function importProject() { await action(async () => {
  await api('/api/projects/import', writeOptions('POST', { path: existing.value }))
  feedback.value = '已有工程已验证并登记。'; dialog.value = null; emit('changed')
}) }
async function remove() {
  if (!props.projectId || !await askConfirm({ title: `移除“${props.projectName}”？`,
    message: '只会从工作台列表移除登记，磁盘中的工程文件不会删除。', symbol: '移', confirmLabel: '移除登记' })) return
  await action(async () => { await api(`/api/projects/${props.projectId}`, writeOptions('DELETE'))
    feedback.value = '已移除登记，磁盘文件保留。'; emit('changed') })
}
async function importCorpus() {
  if (!props.projectId || !files.value.length) return
  if (!await askConfirm({ title: '导入并重新切分母本？',
    message: '若工程已有章节，将先完整备份，再使用所选母本文件重新切分并覆盖章节。', symbol: '导', confirmLabel: '备份并导入' })) return
  const replace = true
  await action(async () => {
    const form = new FormData()
    for (const file of files.value) form.append('files', file)
    form.append('heading_pattern', pattern.value)
    form.append('replace', String(replace))
    const result = await api<{ id: string }>(`/api/projects/${props.projectId}/corpus`,
      { method: 'POST', headers: { 'X-Novel-Workbench': '1' }, body: form })
    emit('task', result.id); feedback.value = '母本切分已开始，可在“运行记录”查看进度。'
    const project = props.projectId
    const poll = async () => {
      try { const job = await api<Job>(`/api/projects/${project}/jobs/${result.id}`)
        if (job.status === 'running') { setTimeout(poll, 2500); return }
        if (job.status === 'completed' && project === props.projectId) await loadChapterRange()
      } catch { /* 运行记录仍可查看任务结果；手动刷新可重读章节范围。 */ }
    }
    void poll()
  })
}
async function buildVectors() {
  if (!props.projectId) return
  await action(async () => { const result = await api<{ id: string }>(`/api/projects/${props.projectId}/vectors`,
    writeOptions('POST', { start: rangeStart.value, end: rangeEnd.value }))
    emit('task', result.id); feedback.value = '向量更新已开始：已有章节将替换，未入库章节将追加。' })
}
async function clearVectors() {
  if (!props.projectId || !await askConfirm({ title: '清空当前向量库？',
    message: '全部检索分段将被删除，全文语义搜索和续写检索会暂停，直至重新构建。', symbol: '清', confirmLabel: '清空向量库' })) return
  await action(async () => { await api(`/api/projects/${props.projectId}/vectors`, writeOptions('DELETE'))
    segments.value = 0; feedback.value = '向量库已清空。' })
}
async function loadVectorInfo() {
  if (!props.projectId) return
  try { const info = await api<{ segments: number; indexed_chapters: number[] }>(`/api/projects/${props.projectId}/vectors`)
    segments.value = info.segments; indexedChapters.value = info.indexed_chapters || [] }
  catch { segments.value = null; indexedChapters.value = [] }
}
async function loadChapterRange() {
  const project = props.projectId
  if (!project) { chapterCount.value = 0; chapterNumbers.value = []; rangeEnd.value = 1; return }
  try { const chapters = await api<{ number: number }[]>(`/api/projects/${project}/chapters`)
    if (project !== props.projectId) return
    chapterNumbers.value = chapters.map(item => item.number).sort((a, b) => a - b)
    chapterCount.value = chapters.length
    rangeEnd.value = chapters.reduce((maximum, item) => Math.max(maximum, item.number), 1)
  } catch (cause) { if (project === props.projectId) {
    chapterCount.value = 0; chapterNumbers.value = []; rangeEnd.value = 1; error.value = String(cause) } }
}
async function refreshVectorSection() {
  await Promise.all([loadVectorInfo(), loadChapterRange()])
  const covered = new Set(indexedChapters.value)
  rangeStart.value = chapterNumbers.value.find(number => !covered.has(number))
    ?? chapterNumbers.value.at(-1) ?? 1
}
watch(() => props.projectId, refreshVectorSection)
onMounted(refreshVectorSection)
</script>

<template>
  <div class="feature-page"><div class="feature-head"><span class="eyebrow">工程与母本</span><h1>让故事有自己的书架。</h1><p>工程仍保存在原来的目录；Web 版直接使用同一份章节与向量库。</p></div>
    <p v-if="error" class="notice error" role="alert">{{ error }}</p><p v-if="feedback" class="notice success">{{ feedback }}</p>
    <section class="panel project-actions"><h2>工程管理</h2><p>新建工程，或将已有工程登记到当前工作台。导入不会复制或修改原文件。</p>
      <button @click="dialog = 'create'">新建工程</button><button class="subtle" @click="dialog = 'import'">导入已有工程</button>
      <button class="danger subtle" :disabled="busy || !projectId" @click="remove">移除“{{ projectName }}”的登记</button></section>
    <section class="panel"><h2>导入母本</h2><p>可多选文本文件，按所选顺序切章。已有章节会要求确认，并在覆盖前备份。</p>
      <label>母本文件<input type="file" accept=".txt,text/plain" multiple @change="files = Array.from(($event.target as HTMLInputElement).files || [])"></label>
      <label>标题正则（可选）<input v-model="pattern" placeholder="留空使用默认规则"></label>
      <button :disabled="busy || !projectId || !files.length" @click="importCorpus">切分导入</button></section>
    <section class="panel"><h2>向量库</h2><p>当前分段：{{ segments === null ? '未读取' : segments }} · 已有章节：{{ chapterCount }} · 已入库章节：{{ indexedChapters.length }}。</p>
      <p class="muted-note">更新规则：向量库中已存在的章节会整章替换，不存在的章节才会追加。默认从第一篇未入库章节开始；若已全部覆盖，则默认选择最新章。</p>
      <div class="inline-fields"><label>起始章<input v-model.number="rangeStart" type="number" min="1"></label><label>结束章<input v-model.number="rangeEnd" type="number" min="1"></label></div>
      <button :disabled="busy || !projectId || !chapterCount || rangeStart > rangeEnd" @click="buildVectors">替换／追加所选章节</button>
      <button class="subtle" :disabled="busy || !projectId" @click="refreshVectorSection">刷新章节范围与分段数</button>
      <button class="danger subtle" :disabled="busy || !projectId" @click="clearVectors">清空向量库</button></section>
    <div v-if="dialog" class="modal-backdrop" @click.self="dialog = null" @keydown.esc="dialog = null">
      <section class="panel project-dialog" role="dialog" aria-modal="true" :aria-label="dialog === 'create' ? '新建工程' : '导入已有工程'">
        <div class="dialog-head"><h2>{{ dialog === 'create' ? '新建工程' : '导入已有工程' }}</h2><button class="text-button" aria-label="关闭" @click="dialog = null">关闭</button></div>
        <template v-if="dialog === 'create'"><label>工程名<input v-model="name" autofocus placeholder="小说名称"></label>
          <label>父目录<input v-model="parent" placeholder="留空使用当前工作目录"></label>
          <button class="subtle" @click="pick('directory', 'parent')">选择文件夹</button>
          <div class="dialog-actions"><button :disabled="busy || !name.trim()" @click="create">创建工程</button></div></template>
        <template v-else><p>请选择含有 project.json 和 chapters 文件夹的工程目录；也可选择 project.json 文件定位工程。</p>
          <label>工程目录<input v-model="existing" placeholder="可手动填写本机路径"></label>
          <button class="subtle" @click="pick('directory', 'existing')">选择文件夹</button>
          <button class="subtle" @click="pick('project_file', 'existing')">选择 project.json</button>
          <div class="dialog-actions"><button :disabled="busy || !existing.trim()" @click="importProject">验证并导入</button></div></template>
        <p v-if="error" class="notice error" role="alert">{{ error }}</p>
      </section></div>
  </div>
</template>
