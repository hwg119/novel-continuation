<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api, writeOptions, type Job } from './api'
import { askConfirm } from './confirmService'
import { toast } from './toastService'

type Annotation = { word: string; meaning: string }
type Segment = { id: string; source: string; text: string; translation?: string;
  translated: boolean; annotations: Annotation[] }
type LearningEdition = { chapter: number; level: string; level_label: string;
  exists: boolean; valid: boolean; blocks: { index: number; segments: Segment[] }[];
  stats: { segments?: number; translated_segments?: number; coverage?: number; annotations?: number } }

const props = defineProps<{ projectId: string; chapterNumber: number; modelName: string;
  dirty: boolean; sourceRevision: string }>()
const emit = defineEmits<{ task: [id: string] }>()
const levels = [
  { id: 'primary', label: '小学高年级', note: '短句与高频词' },
  { id: 'junior', label: '初中', note: '常见叙事表达' },
  { id: 'senior', label: '高中', note: '较完整的英文阅读' },
]
const level = ref('primary')
const edition = ref<LearningEdition | null>(null)
const loading = ref(false)
const generating = ref(false)
const exporting = ref(false)
const exportOpen = ref(false)
const exportMode = ref<'inline' | 'endnotes' | 'exercise'>('endnotes')
const includeVocabulary = ref(true)
const includeIllustration = ref(true)
const revealed = ref(new Set<string>())
const EXPORT_PREFS_KEY = 'novel-continuation:learning-export-options'

const activeLevel = computed(() => levels.find(item => item.id === level.value)!)
const coverage = computed(() => Math.round(Number(edition.value?.stats?.coverage || 0) * 100))

async function load() {
  if (!props.projectId || !props.chapterNumber) return
  loading.value = true; revealed.value = new Set()
  try {
    edition.value = await api<LearningEdition>(
      `/api/projects/${props.projectId}/chapters/${props.chapterNumber}/learning/${level.value}`)
  } catch { edition.value = null }
  finally { loading.value = false }
}
function toggleSource(id: string) {
  const next = new Set(revealed.value)
  if (next.has(id)) next.delete(id); else next.add(id)
  revealed.value = next
}
async function generate() {
  if (generating.value || !props.modelName) return
  if (props.dirty) { toast.info('请先保存当前正文，再生成英语学习版。'); return }
  if (!await askConfirm({
    title: `${edition.value?.exists ? '重新生成' : '生成'}第 ${props.chapterNumber} 章${activeLevel.value.label}学习版？`,
    message: edition.value?.exists
      ? '现有同级学习版将被覆盖；小说正文、向量库和 Wiki 不会发生变化。'
      : `模型只翻译适合${activeLevel.value.label}水平的部分完整句子，并为少量难词添加中文释义。`,
    symbol: '学', confirmLabel: edition.value?.exists ? '重新生成学习版' : '生成学习版',
    modelName: props.modelName,
  })) return
  generating.value = true
  const project = props.projectId; const chapter = props.chapterNumber; const selectedLevel = level.value
  try {
    const started = await api<{ id: string }>(
      `/api/projects/${project}/chapters/${chapter}/learning/${selectedLevel}`,
      writeOptions('POST', { model_name: props.modelName }))
    emit('task', started.id)
    toast.info(`正在使用 ${props.modelName} 生成英语学习版；进度可在底部控制台查看。`)
    const poll = async () => {
      try {
        const job = await api<Job>(`/api/projects/${project}/jobs/${started.id}`)
        if (job.status === 'running') { setTimeout(poll, 1400); return }
        generating.value = false
        if (job.status !== 'completed') { toast.error(job.message); return }
        if (project === props.projectId && chapter === props.chapterNumber && selectedLevel === level.value) await load()
        toast.success(`第 ${chapter} 章${activeLevel.value.label}英语学习版已生成。`)
      } catch (cause) { generating.value = false; toast.error(cause) }
    }
    void poll()
  } catch (cause) { generating.value = false; toast.error(cause) }
}
async function exportWord() {
  if (!edition.value?.valid || exporting.value) return
  exporting.value = true
  try {
    await api(`/api/projects/${props.projectId}/chapters/${props.chapterNumber}/learning/${level.value}/export`,
      writeOptions('POST', { mode: exportMode.value, include_vocabulary: includeVocabulary.value,
        include_illustration: includeIllustration.value }))
    window.location.href = `/api/projects/${props.projectId}/chapters/${props.chapterNumber}/learning/${level.value}/export/${exportMode.value}`
    toast.success('英语学习版 Word 已生成并开始下载。')
    exportOpen.value = false
  } catch (cause) { toast.error(cause) }
  finally { exporting.value = false }
}
function loadExportPreferences() {
  try {
    const saved = JSON.parse(localStorage.getItem(EXPORT_PREFS_KEY) || '{}')
    if (['inline', 'endnotes', 'exercise'].includes(saved.mode)) exportMode.value = saved.mode
    if (typeof saved.vocabulary === 'boolean') includeVocabulary.value = saved.vocabulary
    if (typeof saved.illustration === 'boolean') includeIllustration.value = saved.illustration
  } catch { /* 损坏的本地偏好直接使用默认值。 */ }
}
function saveExportPreferences() {
  try {
    localStorage.setItem(EXPORT_PREFS_KEY, JSON.stringify({ mode: exportMode.value,
      vocabulary: includeVocabulary.value, illustration: includeIllustration.value }))
  } catch { /* 浏览器禁用本地存储时不影响导出。 */ }
}

watch(level, load)
watch(() => [props.projectId, props.chapterNumber, props.sourceRevision], load)
watch([exportMode, includeVocabulary, includeIllustration], saveExportPreferences)
onMounted(() => { loadExportPreferences(); void load() })
</script>

<template><section class="learning-reader" aria-labelledby="learning-reader-title">
  <header class="learning-reader-head"><div><span class="eyebrow">分级双语阅读 · 第 {{ chapterNumber }} 章</span><h2 id="learning-reader-title">在故事里，慢慢增加英文。</h2><p>只替换适合当前水平的完整句子；点击英文可核对中文原句。</p></div>
    <div class="learning-head-actions"><button v-if="edition?.valid" class="learning-export-button" :disabled="generating || loading" @click="exportOpen = !exportOpen">导出学习版 Word</button><button :disabled="generating || loading || dirty || !modelName" @click="generate"><span v-if="generating" class="button-spinner" aria-hidden="true"></span>{{ generating ? '生成中…' : edition?.exists ? '重新生成' : '生成学习版' }}</button></div></header>
  <nav class="learning-levels" aria-label="英语学习级别"><button v-for="item in levels" :key="item.id" :class="{ active: level === item.id }" @click="level = item.id"><strong>{{ item.label }}</strong><small>{{ item.note }}</small></button></nav>
  <section v-if="exportOpen && edition?.valid" class="learning-export-panel"><div><span class="eyebrow">打印版式 · 自动记住选择</span><h3>选择中文提示出现的位置</h3></div><div class="learning-export-modes"><label :class="{ active: exportMode === 'endnotes' }"><input v-model="exportMode" type="radio" value="endnotes"><span><strong>章末索引 <em>推荐</em></strong><small>正文只保留上标编号，中文原句集中放在章末，阅读最连贯。</small></span></label><label :class="{ active: exportMode === 'exercise' }"><input v-model="exportMode" type="radio" value="exercise"><span><strong>练习模式</strong><small>正文隐藏释义和原句，答案统一放在章末。</small></span></label><label :class="{ active: exportMode === 'inline' }"><input v-model="exportMode" type="radio" value="inline"><span><strong>段后对照</strong><small>每个自然段后列出中文原句，适合逐句精读，但会打断阅读。</small></span></label></div><div class="learning-export-options"><label><input v-model="includeVocabulary" type="checkbox">附本章词汇表</label><label><input v-model="includeIllustration" type="checkbox">包含本章插图（如有）</label><button :disabled="exporting" @click="exportWord"><span v-if="exporting" class="button-spinner" aria-hidden="true"></span>{{ exporting ? '正在排版…' : '生成并下载 Word' }}</button></div></section>
  <p v-if="dirty" class="learning-note warning">正文有未保存修改。保存后才能生成与当前正文一致的学习版。</p>
  <p v-else-if="loading" class="learning-empty">正在读取学习版…</p>
  <div v-else-if="edition?.valid" class="learning-sheet">
    <div class="learning-stats"><span>英文覆盖约 <strong>{{ coverage }}%</strong></span><span>翻译 <strong>{{ edition.stats.translated_segments || 0 }}</strong> 句</span><span>注释 <strong>{{ edition.stats.annotations || 0 }}</strong> 词</span></div>
    <div class="learning-prose"><p v-for="block in edition.blocks" :key="block.index"><template v-for="segment in block.segments" :key="segment.id"><button v-if="segment.translated" class="learning-sentence" :class="{ revealed: revealed.has(segment.id) }" :aria-expanded="revealed.has(segment.id)" @click="toggleSource(segment.id)"><span>{{ segment.text }}</span><small v-if="revealed.has(segment.id)">{{ segment.source }}</small></button><span v-else>{{ segment.text }}</span></template></p></div>
  </div>
  <div v-else class="learning-empty"><span>{{ edition?.exists ? '旧学习版与当前正文不一致' : `${activeLevel.label}学习版尚未生成` }}</span><p>{{ edition?.exists ? '正文已经修改，请重新生成后再阅读。' : '生成后会保存在工程的 learning_editions 目录，不影响小说正文。' }}</p><button :disabled="generating || dirty || !modelName" @click="generate">{{ edition?.exists ? '重新生成此级别' : '生成此级别' }}</button></div>
</section></template>
