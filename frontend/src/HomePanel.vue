<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api, type Job } from './api'

type Chapter = { number: number; title: string; generated_at: string | null; modified_at: string }
type PlanStatus = { number: number; has_beats: boolean; beats_count: number; title: string }
const props = defineProps<{ projectId: string; projectName: string; chapters: Chapter[];
  modelName: string; wikiPendingCount: number; generating: boolean }>()
const emit = defineEmits<{ generate: [request: { number: number; title: string; beatsLimit: number | null }];
  settings: [number: number]; chapter: [number: number]; navigate: [page: 'projects' | 'wiki' | 'runs' | 'config'] }>()
const number = ref(1)
const title = ref('')
const beatsLimit = ref<number | null>(null)
const plan = ref<PlanStatus | null>(null)
const checking = ref(false)
const error = ref('')
const vectorSegments = ref<number | null>(null)
const jobs = ref<Job[]>([])
const recent = computed(() => [...props.chapters].sort((a, b) => b.number - a.number).slice(0, 6))
const latest = computed(() => recent.value[0])
const recentJobs = computed(() => jobs.value.slice(0, 3))
const todoCount = computed(() => Number(!props.chapters.length) + Number(!props.modelName) +
  Number(!!props.chapters.length && !plan.value?.has_beats && !checking.value) +
  Number(!!props.chapters.length && vectorSegments.value === 0) + Number(props.wikiPendingCount > 0))
const jobNames: Record<string, string> = { generate: '章节续写', revise: '整章修订',
  consistency: '故事审校', vectors: '向量库构建', chapter_vector: '章节向量更新', corpus: '导入母本',
  wiki: 'Wiki 更新', illustration: '章节插图', plan: '分幕规划', book_rules: '全书规则' }
function jobStatus(status: string) {
  return status === 'running' ? '运行中' : status === 'completed' ? '已完成' :
    status === 'failed' ? '失败' : '已中断'
}
async function loadOverview(project: string) {
  if (!project) { vectorSegments.value = null; jobs.value = []; return }
  const [vectorInfo, latestJobs] = await Promise.allSettled([
    api<{ segments: number }>(`/api/projects/${project}/vectors`),
    api<Job[]>(`/api/projects/${project}/jobs`),
  ])
  if (props.projectId === project) {
    vectorSegments.value = vectorInfo.status === 'fulfilled' ? vectorInfo.value.segments : null
    jobs.value = latestJobs.status === 'fulfilled' ? latestJobs.value : []
  }
}
watch(() => props.projectId, project => { void loadOverview(project) }, { immediate: true })
watch(() => props.chapters.length, () => { if (props.projectId) void loadOverview(props.projectId) })
watch(() => [props.projectId, props.chapters] as const, () => {
  number.value = Math.max(1, ...props.chapters.map(item => item.number + 1))
}, { immediate: true })
watch(() => [props.projectId, number.value] as const, async ([project, selected]) => {
  plan.value = null; title.value = ''; error.value = ''
  if (!project || !Number.isInteger(selected) || selected < 1) return
  checking.value = true
  try {
    const result = await api<PlanStatus>(`/api/projects/${project}/chapters/${selected}/plan-status`)
    if (props.projectId === project && number.value === selected) plan.value = result
  } catch (cause) { error.value = String(cause) }
  finally { if (props.projectId === project && number.value === selected) checking.value = false }
}, { immediate: true })
</script>

<template><div class="feature-page home-page"><section class="home-hero" aria-label="当前书稿概览">
  <div class="home-hero-copy"><span class="home-hero-eyebrow">当前书稿 / WORKSPACE</span>
    <h1>{{ projectName || '从这里接着写。' }}</h1>
    <p>{{ latest ? `最新收录第 ${latest.number} 章 · ${latest.title || '未命名章节'}` : '还没有章节。导入母本后，故事会从这里继续。' }}</p>
    <button v-if="latest" class="home-hero-link" @click="emit('chapter', latest.number)">打开最新章节 <span aria-hidden="true">↗</span></button>
    <button v-else class="home-hero-link" @click="emit('navigate', 'projects')">导入小说母本 <span aria-hidden="true">↗</span></button></div>
  <div class="home-hero-volume" aria-hidden="true"><span>CHAPTER</span><strong>{{ String(latest?.number || 0).padStart(3, '0') }}</strong>
    <small>{{ chapters.length }} 章已收录</small></div></section>
  <div class="home-metrics" aria-label="工程状态">
    <div><span>章节总数</span><strong>{{ chapters.length }}</strong><small>正文已收录</small></div>
    <div><span>向量索引</span><strong>{{ vectorSegments === null ? '—' : vectorSegments.toLocaleString('zh-CN') }}</strong><small>可检索分段</small></div>
    <div><span>Wiki 待更新</span><strong>{{ wikiPendingCount }}</strong><small>新增或改动章节</small></div></div>
  <p v-if="error" class="notice error" role="alert">{{ error }}</p>
  <div class="home-work-grid"><section class="panel home-compose"><div><span class="eyebrow">继续创作</span><h2>第 {{ number }} 章</h2>
    <p v-if="plan?.has_beats">已准备 {{ plan.beats_count }} 幕{{ plan.title ? ` · ${plan.title}` : '' }}</p>
    <p v-else-if="checking">正在检查分幕计划…</p><p v-else>尚无该章分幕大纲，先到续写设定中规划。</p></div>
    <div class="home-compose-controls"><label>章号<input v-model.number="number" type="number" min="1"></label>
      <label>回目（可选）<input v-model="title" :placeholder="plan?.title || '使用计划中的回目'"></label>
      <label>生成幕数（留空为全部）<input v-model.number="beatsLimit" type="number" min="1"></label></div>
    <div class="home-actions"><button class="subtle" @click="emit('settings', number)">设置本章分幕</button>
      <button :disabled="generating || !projectId || !modelName || checking || !plan?.has_beats"
        @click="emit('generate', { number, title, beatsLimit })"><span v-if="generating" class="button-spinner" aria-hidden="true"></span>{{ generating ? '续写中…' : '开始续写' }}</button></div></section>
    <section class="panel home-todo"><div class="home-section-head"><div><span class="eyebrow">接下来</span><h2>待办与入口</h2></div><span>{{ todoCount }} 项待处理</span></div>
      <button v-if="!chapters.length" class="home-todo-item" @click="emit('navigate', 'projects')"><span>导入小说母本</span><small>建立章节目录与正文</small><b aria-hidden="true">↗</b></button>
      <button v-if="!modelName" class="home-todo-item" @click="emit('navigate', 'config')"><span>配置大模型</span><small>续写与插图需要模型</small><b aria-hidden="true">↗</b></button>
      <button v-if="chapters.length && !plan?.has_beats && !checking" class="home-todo-item" @click="emit('settings', number)"><span>规划第 {{ number }} 章</span><small>填写分幕后即可续写</small><b aria-hidden="true">↗</b></button>
      <button v-if="chapters.length && vectorSegments === 0" class="home-todo-item" @click="emit('navigate', 'projects')"><span>构建向量库</span><small>让续写能检索母本</small><b aria-hidden="true">↗</b></button>
      <button v-if="wikiPendingCount" class="home-todo-item" @click="emit('navigate', 'wiki')"><span>更新小说 Wiki</span><small>{{ wikiPendingCount }} 章等待编纂</small><b aria-hidden="true">↗</b></button>
      <p v-if="!todoCount" class="home-todo-clear">当前没有待处理事项。可以继续续写，或打开最近章节检查正文。</p>
    </section></div>
  <section class="panel home-recent"><div class="home-recent-head"><h2>最近章节</h2><span>按章号倒序</span></div>
    <button v-for="chapter in recent" :key="chapter.number" class="recent-chapter" @click="emit('chapter', chapter.number)">
      <span class="recent-number">{{ String(chapter.number).padStart(3, '0') }}</span>
      <strong>{{ chapter.title || `第 ${chapter.number} 章` }}</strong>
      <time>{{ chapter.generated_at ? `生成于 ${chapter.generated_at}` : `更新于 ${chapter.modified_at}` }}</time></button>
    <p v-if="!recent.length">还没有章节。可在“工程与母本”导入已有小说。</p></section>
  <section class="panel home-runs"><div class="home-recent-head"><h2>最近运行</h2><button @click="emit('navigate', 'runs')">查看全部记录 ↗</button></div>
    <div v-for="job in recentJobs" :key="job.id" class="home-run-row"><span class="home-run-mark" :class="job.status"></span>
      <strong>{{ jobNames[job.kind] || job.kind }}</strong><span>{{ jobStatus(job.status) }}</span><time>{{ job.created_at || '' }}</time></div>
    <p v-if="!recentJobs.length" class="home-empty">尚无运行记录。开始续写或构建向量库后会在这里显示。</p></section>
</div></template>
