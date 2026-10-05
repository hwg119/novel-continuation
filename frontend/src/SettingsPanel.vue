<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api, writeOptions, type Job } from './api'
import { askConfirm } from './confirmService'
import MessageToast from './MessageToast.vue'

const props = defineProps<{ projectId: string; modelName: string; currentChapter: number | null; generating: boolean }>()
const emit = defineEmits<{ task: [id: string]; chapter: [number: number];
  generate: [request: { number: number; title: string; beatsLimit: number | null }] }>()
type Settings = Record<string, any>
const settings = ref<Settings>({})
const chapter = ref(1)
const beatsText = ref('')
const glossaryText = ref('')
const mode = ref<'global' | 'chapter'>('chapter')
const toastMessage = ref('')
const error = ref('')
const running = ref(false)
const activeTask = ref<'plan' | 'plan-revise' | 'book-rules' | ''>('')
const planningReview = ref<Record<string, any> | null>(null)
const reviewLabels: Record<string, string> = {
  initial_review_only: '已完成初审，未做后验收', passed: '重写后验收通过',
  patched_pending_review: '已局部修补，待确认', needs_review: '验收发现问题，待确认',
  incomplete: '验收未完成，待确认',
  editorial_pending_review: '已做文字规范，待确认',
}
const revisionIssues = ref('')
const startingContinuation = ref(false)
const illustrationSvg = ref('')
const illustrationPng = ref('')
const illustrationBusy = ref(false)
const illustrationLoading = ref(false)
const illustrationPreview = computed(() => illustrationPng.value || (illustrationSvg.value ?
  `data:image/svg+xml;charset=utf-8,${encodeURIComponent(illustrationSvg.value)}` : ''))

type BeatView = { num: string; name: string; desc: string; structured: boolean;
  pov: string; time: string; location: string; story: string; known: string;
  facts: string; element: string; ending: string }

function descriptionPart(text: string, marker: string, nextMarkers: string[]) {
  const start = text.indexOf(marker)
  if (start < 0) return ''
  const from = start + marker.length
  const ends = nextMarkers.map(item => text.indexOf(item, from)).filter(index => index >= 0)
  return text.slice(from, ends.length ? Math.min(...ends) : undefined).trim()
}

function beatView(beat: { num: string; name: string; desc: string }): BeatView {
  const desc = beat.desc || ''
  const header = desc.match(/^【视角：(.*?)｜时间：(.*?)｜地点：(.*?)】\s*/)
  if (!header) return { ...beat, structured: false, pov: '', time: '', location: '',
    story: desc, known: '', facts: '', element: '', ending: '' }
  const remainder = desc.slice(header[0].length)
  const markers = ['已知信息：', '新增信息：', '主要新增元素：', '结尾状态：']
  const firstMarker = markers.map(item => remainder.indexOf(item)).filter(index => index >= 0)
  return { ...beat, structured: true, pov: header[1].trim(), time: header[2].trim(),
    location: header[3].trim(), story: remainder.slice(0, firstMarker.length ? Math.min(...firstMarker) : undefined).trim(),
    known: descriptionPart(remainder, '已知信息：', markers.slice(1)),
    facts: descriptionPart(remainder, '新增信息：', markers.slice(2)),
    element: descriptionPart(remainder, '主要新增元素：', ['结尾状态：']),
    ending: descriptionPart(remainder, '结尾状态：', []) }
}

const beatViews = computed(() => parseBeats().map(beatView))

async function loadIllustration(number: number, project = props.projectId) {
  illustrationSvg.value = ''
  illustrationPng.value = ''
  if (!project || number < 1) return
  illustrationLoading.value = true
  try { const result = await api<{ exists: boolean; svg: string; png?: string }>(
    `/api/projects/${project}/chapters/${number}/illustration`)
    if (project === props.projectId && number === chapter.value) {
      illustrationSvg.value = result.exists ? result.svg : ''
      illustrationPng.value = result.exists ? result.png || '' : ''
    }
  } catch (cause) { if (project === props.projectId && number === chapter.value) error.value = String(cause) }
  finally { illustrationLoading.value = false }
}

function planFor(number: number) {
  revisionIssues.value = ''
  const plan = settings.value.chapter_plans?.[String(number)] ||
    (number === Number(settings.value.chapter_number) ? settings.value : {})
  settings.value.chapter_title = plan.chapter_title || ''
  settings.value.chapter_brief = plan.chapter_brief || ''
  settings.value.chapter_requirements = plan.chapter_requirements || ''
  planningReview.value = plan.planning_review || null
  beatsText.value = (plan.beats || []).map((beat: any) =>
    `${beat.num || ''} | ${beat.name || ''} | ${beat.desc || ''}`).join('\n')
  void loadIllustration(number)
}
async function load() {
  if (!props.projectId) return
  try { settings.value = await api(`/api/projects/${props.projectId}/settings`)
    settings.value.illustration_method = 'free'
    chapter.value = props.currentChapter || Number(settings.value.chapter_number) || 1
    glossaryText.value = Object.entries(settings.value.glossary || {}).map(([key, value]) =>
      `${(settings.value.glossary_hard_terms || []).includes(key) ? '!' : ''}${key}=${value}`).join('\n')
    planFor(chapter.value); error.value = ''
  } catch (cause) { error.value = String(cause) }
}
function parseBeats() { return beatsText.value.split('\n').map(line => {
  const [num, name, ...rest] = line.split('|').map(part => part.trim())
  return { num, name, desc: rest.join(' | ') }
}).filter(beat => beat.name || beat.desc) }
function parseGlossary() {
  const glossary: Record<string, string> = {}; const hard: string[] = []
  for (const line of glossaryText.value.split('\n')) {
    const [raw, ...rest] = line.split('='); const key = raw.trim().replace(/^!/, '')
    if (!key) continue
    glossary[key] = rest.join('=').trim(); if (raw.trim().startsWith('!')) hard.push(key)
  }
  settings.value.glossary = glossary; settings.value.glossary_hard_terms = hard
}
async function save(showToast = true): Promise<boolean> {
  if (!props.projectId) return false
    parseGlossary()
    settings.value.illustration_method = 'free'
  settings.value.chapter_number = chapter.value
  settings.value.beats = parseBeats()
  settings.value.chapter_plans ||= {}
  settings.value.chapter_plans[String(chapter.value)] = {
    chapter_title: settings.value.chapter_title || '', chapter_brief: settings.value.chapter_brief || '',
    chapter_requirements: settings.value.chapter_requirements || '', beats: settings.value.beats,
    planning_review: planningReview.value }
  try { settings.value = await api(`/api/projects/${props.projectId}/settings`, writeOptions('PUT', settings.value))
    if (showToast) toastMessage.value = '续写设定已保存'
    error.value = ''; return true }
  catch (cause) { error.value = String(cause); return false }
}
async function startContinuation() {
  if (startingContinuation.value || props.generating) return
  const beats = parseBeats()
  if (!beats.length) { error.value = '请先填写本章分幕大纲。'; return }
  if (!props.modelName) { error.value = '请先选择大模型配置。'; return }
  startingContinuation.value = true
  try {
    if (!await save(false)) return
    toastMessage.value = `第 ${chapter.value} 章已开始续写；进度可在底部控制台查看`
    emit('generate', { number: chapter.value, title: String(settings.value.chapter_title || ''), beatsLimit: null })
  } finally { startingContinuation.value = false }
}
async function start(kind: 'plan' | 'plan-revise' | 'book-rules') {
  if (!props.projectId) return
  running.value = true; activeTask.value = kind; error.value = ''
  try {
    const route = kind === 'plan' ? 'plan' : kind === 'plan-revise' ? 'plan/revise' : 'book-rules'
    const started = await api<{ id: string }>(`/api/projects/${props.projectId}/${route}`,
      writeOptions('POST', { number: chapter.value, model_name: props.modelName,
        requirements: kind === 'plan-revise' ? revisionIssues.value.trim() : '',
        preview: { background: settings.value.background, character_voices: settings.value.character_voices,
          extra_requirements: settings.value.extra_requirements, source_novel: settings.value.source_novel,
          chapter_brief: settings.value.chapter_brief, chapter_requirements: settings.value.chapter_requirements,
          chapter_title: settings.value.chapter_title, beats: parseBeats(),
          beats_per_chapter: settings.value.beats_per_chapter } }))
    emit('task', started.id)
    toastMessage.value = 'AI 正在生成建议；完成后会填入当前页面'
    const poll = async () => {
      const job = await api<Job>(`/api/projects/${props.projectId}/jobs/${started.id}`)
      if (job.status === 'running') { setTimeout(poll, 1500); return }
      running.value = false; activeTask.value = ''
      if (job.status !== 'completed' || !job.result) { error.value = job.message; return }
      if (kind === 'plan' || kind === 'plan-revise') {
        settings.value.chapter_title = String(job.result.chapter_title || '')
        planningReview.value = job.result.planning_review || null
        beatsText.value = ((job.result.beats || []) as any[]).map(beat =>
          `${beat.num} | ${beat.name} | ${beat.desc}`).join('\n')
        mode.value = 'chapter'
      } else {
        for (const key of ['background', 'character_voices', 'extra_requirements'])
          settings.value[key] = job.result[key] || ''
        mode.value = 'global'
      }
      if (kind === 'plan') {
        if (!await save(false)) return
        toastMessage.value = 'AI 回目与分幕已生成并保存，可直接生成插图或开始续写'
      } else {
        toastMessage.value = kind === 'plan-revise'
          ? planningReview.value?.status === 'passed'
            ? '分幕修订要求验收通过；候选已填入，请保存设定后生效'
            : '分幕候选已填入，但要求尚未通过验收；请查看提醒，原规划未被保存覆盖'
          : 'AI 建议已填入；检查后请保存设定'
      }
    }
    void poll()
  } catch (cause) { error.value = String(cause); running.value = false; activeTask.value = '' }
}
async function generatePlan() {
  if (running.value) return
  if (!props.modelName) { error.value = '请先选择大模型配置。'; return }
  const replacing = Boolean(settings.value.chapter_title?.trim() || parseBeats().length)
  if (!await askConfirm({
    title: replacing ? `重新生成第 ${chapter.value} 章回目与分幕？` : `生成第 ${chapter.value} 章回目与分幕？`,
    message: replacing
      ? '模型会根据当前章目标、上一章上下文和 Wiki 重新规划；生成完成后将自动覆盖并保存本章现有回目与分幕。'
      : '模型会根据当前章目标、上一章上下文和 Wiki 生成回目及完整分幕，并在完成后自动保存本章设定。',
    symbol: '纲',
    confirmLabel: replacing ? '使用此模型重新生成' : '使用此模型生成',
    modelName: props.modelName,
  })) return
  await start('plan')
}
async function revisePlan() {
  if (running.value) return
  if (!parseBeats().length) { error.value = '请先生成或填写一版分幕规划。'; return }
  if (!revisionIssues.value.trim()) { error.value = '请先填写希望 AI 解决的问题点。'; return }
  if (!props.modelName) { error.value = '请先选择大模型配置。'; return }
  if (!await askConfirm({
    title: `按问题修订第 ${chapter.value} 章规划？`,
    message: '模型会针对问题调整规划，再对照用户要求验收。未落实时最多补修一次。候选先填入页面，不会自动覆盖已保存规划；确认后请保存设定。',
    symbol: '修', confirmLabel: '使用此模型修订', modelName: props.modelName,
  })) return
  await start('plan-revise')
}
async function generateIllustration() {
  if (!props.projectId || !props.modelName || chapter.value < 1 || illustrationBusy.value) return
  const replacing = Boolean(illustrationSvg.value)
  if (!await askConfirm({
    title: replacing ? `替换第 ${chapter.value} 章插图？` : `生成第 ${chapter.value} 章插图？`,
    message: '模型根据本章设计原创构图，生成支持曲线、分组和渐变的自由 SVG，不限素材目录。经 resvg 渲染校验后替换旧图；失败保留原图，最多两次生成。',
    symbol: '图',
    confirmLabel: replacing ? '使用此模型重新生成' : '使用此模型生成',
    modelName: props.modelName,
  })) return
  const project = props.projectId
  const number = chapter.value
  illustrationBusy.value = true; error.value = ''
  try { const started = await api<{ id: string }>(
    `/api/projects/${project}/chapters/${number}/illustration`,
    writeOptions('POST', { model_name: props.modelName,
      preview: { chapter_title: settings.value.chapter_title,
        illustration_method: 'free',
        chapter_brief: settings.value.chapter_brief, beats: parseBeats(),
        illustration_style: settings.value.illustration_style,
        illustration_style_notes: settings.value.illustration_style_notes } }))
    emit('task', started.id)
    toastMessage.value = `正在生成第 ${number} 章插图；进度可在底部控制台查看`
    const poll = async () => {
      try { const job = await api<Job>(`/api/projects/${project}/jobs/${started.id}`)
        if (job.status === 'running') { setTimeout(poll, 2000); return }
        illustrationBusy.value = false
        if (job.status !== 'completed') { error.value = job.message; return }
        if (project === props.projectId && number === chapter.value) await loadIllustration(number, project)
        toastMessage.value = `第 ${number} 章插图已保存；导出 Word 时将自动插入`
      } catch (cause) { illustrationBusy.value = false; error.value = String(cause) }
    }
    void poll()
  } catch (cause) { illustrationBusy.value = false; error.value = String(cause) }
}
watch(() => props.projectId, () => { void load() })
watch(() => props.currentChapter, number => {
  if (number && number !== chapter.value) { chapter.value = number; planFor(number); mode.value = 'chapter' }
})
onMounted(() => { void load() })
</script>

<template><div class="feature-page"><MessageToast :message="toastMessage" :duration="4200" @close="toastMessage = ''" /><div class="feature-head"><span class="eyebrow">续写设定</span><h1>先定边界，再写下一章。</h1><p>全书规则与当前章节分开保存；AI 生成的内容先留在表单中供检查。</p></div>
  <p v-if="error" class="notice error" role="alert">{{ error }}</p>
  <div class="segmented"><button :class="{ selected: mode === 'chapter' }" @click="mode = 'chapter'">当前章节</button><button :class="{ selected: mode === 'global' }" @click="mode = 'global'">全书规则</button></div>
  <div v-if="mode === 'global'" class="settings-grid"><section class="panel"><h2>基础设定</h2>
    <label>工程名<input v-model="settings.name"></label><label>母本出处<input v-model="settings.source_novel"></label>
    <label>本书背景<textarea v-model="settings.background" rows="6"></textarea></label>
    <label>插图生成方式<select v-model="settings.illustration_method"><option value="free">自由 SVG 意象图</option></select></label>
    <label>插图画风<select v-model="settings.illustration_style"><option value="auto">依小说内容自动选择</option>
      <option value="wuxia">武侠绘本</option><option value="ink">水墨剪影</option>
      <option value="children">儿童绘本</option><option value="custom">自定义</option></select></label>
    <label>插图画风补充说明{{ settings.illustration_style === 'custom' ? '' : '（可选）' }}
      <textarea v-model="settings.illustration_style_notes" rows="3" placeholder="例如：人物服饰参考古代江湖，避免现代建筑。"></textarea></label>
    <label>人物口吻<textarea v-model="settings.character_voices" rows="5"></textarea></label>
    <label>附加要求<textarea v-model="settings.extra_requirements" rows="6"></textarea></label>
    <button :disabled="running || !modelName" @click="start('book-rules')">AI 提取全书规则</button></section>
    <section class="panel"><h2>生成与检索</h2>
      <label>系统提示词<textarea v-model="settings.system_prompt" rows="5"></textarea></label>
      <label>术语表（每行 中文=英文；前缀 ! 为硬约束）<textarea v-model="glossaryText" rows="7"></textarea></label>
      <label>禁用词（逗号分隔）<input :value="(settings.forbidden_words || []).join('，')" @input="settings.forbidden_words = ($event.target as HTMLInputElement).value.split(/[,，]/).map(s => s.trim()).filter(Boolean)"></label>
      <div class="inline-fields"><label>每幕字数<input v-model.number="settings.chars_per_beat" type="number"></label><label>默认温度<input v-model.number="settings.temperature" type="number" step="0.1"></label></div>
      <div class="inline-fields"><label>检索条数<input v-model.number="settings.retrieval_k_for_continuation" type="number"></label><label>摘要字数<input v-model.number="settings.summary_chars" type="number"></label></div>
      <label class="check"><input v-model="settings.retrieval_in_continuation" type="checkbox">续写时检索母本</label>
      <label class="check"><input v-model="settings.corpus_is_english" type="checkbox">母本是英文</label>
      <label class="check"><input v-model="settings.use_previous_summaries" type="checkbox">携带前章摘要</label>
      <label class="check"><input v-model="settings.traditional" type="checkbox">繁体输出</label>
    </section></div>
  <template v-else><section class="panel chapter-settings"><h2>第 {{ chapter }} 章规划</h2>
    <p v-if="planningReview" class="muted-note">规划检查：{{ reviewLabels[planningReview.status] || planningReview.status }}。编辑后请结合正文核对。</p>
    <div v-if="planningReview?.source === 'user_revision'" :class="['notice', { error: planningReview.status !== 'passed' }]" role="status">
      <strong>{{ planningReview.status === 'passed' ? '修订要求验收通过' : planningReview.status === 'incomplete' ? '修订要求尚未完成验收' : '修订要求尚未落实' }}</strong>
      <p>{{ planningReview.summary }}</p>
      <p v-if="planningReview.changes">分幕内容变化：{{ planningReview.changes.content_changed_beats.length ? '第 ' + planningReview.changes.content_changed_beats.join('、') + ' 幕' : planningReview.requirement_scope === 'labels' ? '无（本次仅调整名称或表达）' : '无；仅改名不能落实情节类要求' }}。这是候选稿，保存后才生效。</p>
      <p v-for="(note, index) in planningReview.unresolved || []" :key="index">{{ note.scope }}：{{ note.problem }} {{ note.suggestion }}</p>
    </div>
    <details v-if="planningReview?.editorial_notes?.length"><summary>编辑提醒与修补记录（{{ planningReview.editorial_notes.length }} 项，仍需核对）</summary>
      <p v-for="(note, index) in planningReview.editorial_notes" :key="index" class="muted-note">{{ note.scope }}：{{ note.problem }} {{ note.suggestion }}</p>
    </details>
    <div class="inline-fields"><label>章号<input v-model.number="chapter" type="number" min="1" @change="planFor(chapter); emit('chapter', chapter)"></label>
      <label>规划幕数<input v-model.number="settings.beats_per_chapter" type="number" min="1" max="12"></label></div>
    <label>本章回目<input v-model="settings.chapter_title"></label>
    <label>本章目标<textarea v-model="settings.chapter_brief" rows="4"></textarea></label>
    <label>本章专属要求<textarea v-model="settings.chapter_requirements" rows="4"></textarea></label>
    <div class="outline-heading"><div><span class="field-label">分幕大纲</span><p>按情节顺序阅读；展开“连续性依据”可核对人物在本幕知道什么。</p></div>
      <button :disabled="running || !modelName" @click="generatePlan"><span v-if="activeTask === 'plan'" class="button-spinner" aria-hidden="true"></span>{{ activeTask === 'plan' ? '正在生成回目与分幕…' : 'AI 提取回目与分幕' }}</button></div>
    <section v-if="beatViews.length" class="plan-revision-box">
      <div class="revision-copy"><span class="revision-mark">定向修订</span><div><h3>把发现的问题交回给模型</h3>
        <p>逐条写明冲突、重复、节奏或人物行为问题；未提及的内容会尽量保留。</p></div></div>
      <textarea v-model="revisionIssues" rows="5" placeholder="例如：&#10;1. 第一幕与第三幕功能重复，请合并；&#10;2. 第二幕地点与工程设定冲突，请修正；&#10;3. 调查线索出现得太顺利，增加一个无法立即确认的歧义。"></textarea>
      <div class="revision-actions"><small>修订结果不会自动保存，可先检查新旧分幕。</small>
        <button :disabled="running || !modelName || !revisionIssues.trim()" @click="revisePlan"><span v-if="activeTask === 'plan-revise'" class="button-spinner" aria-hidden="true"></span>{{ activeTask === 'plan-revise' ? '修订中…' : '按问题修改分幕' }}</button></div>
    </section>
    <div v-if="beatViews.length" class="beat-outline" aria-label="分幕大纲">
      <article v-for="(beat, index) in beatViews" :key="`${beat.num}-${index}`" class="outline-beat">
        <div class="beat-seal"><span>第</span><strong>{{ beat.num || index + 1 }}</strong><span>幕</span></div>
        <div class="beat-sheet"><header><div><span class="beat-kicker">BEAT {{ String(index + 1).padStart(2, '0') }}</span><h3>{{ beat.name || '未命名分幕' }}</h3></div>
          <div v-if="beat.structured" class="beat-meta"><span>{{ beat.pov }}</span><span>{{ beat.time }}</span><span>{{ beat.location }}</span></div></header>
          <p class="beat-story">{{ beat.story || '尚未填写本幕剧情。' }}</p>
          <aside v-if="beat.element" class="beat-new-element"><span>本章新元素</span><p>{{ beat.element }}</p></aside>
          <div v-if="beat.ending" class="beat-ending"><span>幕末</span><p>{{ beat.ending }}</p></div>
          <details v-if="beat.known || beat.facts" class="beat-evidence"><summary>连续性依据</summary><div>
            <section v-if="beat.known"><span>进入本幕前已知</span><p>{{ beat.known }}</p></section>
            <section v-if="beat.facts"><span>本幕新增信息</span><p>{{ beat.facts }}</p></section>
          </div></details>
        </div>
      </article>
    </div>
    <div v-else class="outline-empty">还没有分幕。填写本章目标后，让 AI 生成第一版规划。</div>
    <details class="outline-source"><summary>编辑原始分幕文本</summary>
      <label>每行格式：幕号 | 幕名 | 内容<textarea v-model="beatsText" rows="12"></textarea></label>
      <p>这里的修改会实时反映到上方路线图；保存时仍使用兼容现有工程的分幕格式。</p>
    </details></section>
    <section class="panel chapter-settings illustration-panel"><div class="illustration-head"><div><span class="eyebrow">章节图版 · {{ String(chapter).padStart(3, '0') }}</span><h2>本章插图</h2></div>
      <button :disabled="illustrationBusy || illustrationLoading || !modelName || chapter < 1" @click="generateIllustration">{{ illustrationBusy ? '生成中…' : illustrationSvg ? '重新生成插图' : '生成本章插图' }}</button></div>
      <label>生成方式<select v-model="settings.illustration_method" :disabled="illustrationBusy"><option value="free">自由 SVG 意象图</option></select></label>
      <div class="illustration-frame"><img v-if="illustrationSvg" :src="illustrationPreview" :alt="`第 ${chapter} 章插图预览`">
        <p v-else>{{ illustrationLoading ? '正在载入本章插图…' : '本章尚无插图。导出 Word 时将保留标题与正文，不插入图片。' }}</p></div>
      <p class="muted-note">不受素材目录限制：由模型自由设计构图，支持曲线、分组、渐变、透明度和裁剪。预览与 Word 共用 resvg 渲染。生成成功后替换旧图，失败保留原图。</p></section></template>
  <div class="sticky-actions"><button @click="save()">保存设定</button><button class="subtle" @click="load">重新载入</button>
    <button v-if="mode === 'chapter'" class="continue-button" :disabled="startingContinuation || generating || running || !modelName || !parseBeats().length" @click="startContinuation"><span v-if="startingContinuation || generating" class="button-spinner" aria-hidden="true"></span>{{ startingContinuation ? '正在保存…' : generating ? '续写中…' : '开始续写' }}</button></div>
</div></template>
