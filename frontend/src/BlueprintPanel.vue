<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api, writeOptions, type Job } from './api'
import { toast } from './toastService'

const props = defineProps<{ projectId: string; modelName: string }>()
const emit = defineEmits<{ task: [id: string] }>()
type Blueprint = Record<string, any>
type ArchitectureDirection = Record<string, any>
const blueprint = ref<Blueprint | null>(null)
const candidate = ref<Blueprint | null>(null)
const candidateError = ref('')
const requirements = ref('')
const loading = ref(false)
const generating = ref(false)
const directionLoading = ref(false)
const directions = ref<ArchitectureDirection[]>([])
const saving = ref(false)
const candidateContent = ref<HTMLElement | null>(null)
const active = computed(() => candidate.value || blueprint.value)
const plan = computed(() => active.value?.plan || {})
const isCandidate = computed(() => Boolean(candidate.value))
const hasBlueprintPlan = computed(() => Boolean(active.value?.plan?.phases?.length))
const fastClosureRequirement = `[节奏模式：快速收束]
快速结束当前证据搜集阶段：未来两章内完成最后的必要确认，不再增加新的日期、文书、名单或同类旁证；第三章前必须进入针对明确对手或明确目标的主动行动。当前案件在六章内形成可执行的责任结论或完成正面对抗，之后的章节转入后果、人物选择和下一条故事线。不要用“继续核验、整理记录、缩小范围”延迟结案。`

async function load() {
  if (!props.projectId) return
  loading.value = true
  try {
    const result = await api<{ exists: boolean; blueprint: Blueprint; candidate?: Blueprint | null; directions?: ArchitectureDirection[]; candidate_error?: string }>(`/api/projects/${props.projectId}/story-blueprint`)
    blueprint.value = result.blueprint
    candidate.value = result.candidate ? structuredClone(result.candidate) : null
    directions.value = Array.isArray(result.directions) ? structuredClone(result.directions) : []
    candidateError.value = result.candidate_error || ''
  } catch (error) { toast.error(error) }
  finally { loading.value = false }
}

async function generateDirections() {
  if (directionLoading.value || generating.value || !props.modelName) return
  directionLoading.value = true
  directions.value = []
  try {
    const started = await api<{ id: string }>(`/api/projects/${props.projectId}/story-blueprint/directions`,
      writeOptions('POST', { model_name: props.modelName, requirements: requirements.value.trim(), mode: 'regenerate' }))
    emit('task', started.id)
    const poll = async () => {
      const job = await api<Job>(`/api/projects/${props.projectId}/jobs/${started.id}`)
      if (job.status === 'running') { setTimeout(poll, 1500); return }
      directionLoading.value = false
      const rows = job.result?.directions
      if (job.status !== 'completed' || !Array.isArray(rows) || rows.length !== 3) {
        toast.error(job.message || '架构方向生成失败'); return
      }
      directions.value = rows as ArchitectureDirection[]
      toast.success('请选择一种架构方向，再展开完整蓝图')
    }
    void poll()
  } catch (error) { directionLoading.value = false; toast.error(error) }
}

function chooseDirection(direction: ArchitectureDirection) {
  const selected = `[已选择的长期架构方向：${direction.title}]
必须以此方向作为新蓝图的故事发动机和阶段划分依据，不得退回旧蓝图的同构结构。
故事发动机：${direction.story_engine}
当前案件处理：${direction.current_case_resolution}
主要冲突：${direction.dominant_conflict}
阶段逻辑：${direction.phase_logic}
后续主线：${direction.future_arc}
读者期待：${direction.reader_promise}
${requirements.value.trim() ? `用户补充：${requirements.value.trim()}` : ''}`
  void generate('regenerate', selected)
}

async function discardCandidate() {
  try {
    await api(`/api/projects/${props.projectId}/story-blueprint/candidate`, writeOptions('DELETE'))
    candidate.value = null
    candidateError.value = ''
    toast.success('已放弃蓝图候选稿，正式蓝图没有改变')
  } catch (error) { toast.error(error) }
}

async function generate(mode: 'create' | 'adjust' | 'regenerate', requirementOverride = '') {
  if (generating.value || !props.modelName) return
  generating.value = true
  try {
    const started = await api<{ id: string }>(`/api/projects/${props.projectId}/story-blueprint/generate`,
      writeOptions('POST', { model_name: props.modelName, requirements: requirementOverride || requirements.value.trim(), mode }))
    emit('task', started.id)
    const poll = async () => {
      const job = await api<Job>(`/api/projects/${props.projectId}/jobs/${started.id}`)
      if (job.status === 'running') { setTimeout(poll, 1500); return }
      generating.value = false
      if (job.status !== 'completed' || !job.result?.candidate) {
        toast.error(job.message || '蓝图生成失败'); return
      }
      candidate.value = structuredClone(job.result.candidate as Blueprint)
      directions.value = []
      candidateError.value = ''
      toast.success('蓝图候选稿已生成；尚未覆盖当前蓝图')
    }
    void poll()
  } catch (error) { generating.value = false; toast.error(error) }
}

async function save(value = active.value): Promise<boolean> {
  if (!value || saving.value) return false
  saving.value = true
  try {
    const result = await api<{ blueprint: Blueprint }>(`/api/projects/${props.projectId}/story-blueprint`,
      writeOptions('PUT', { blueprint: value }))
    blueprint.value = result.blueprint
    candidate.value = null
    toast.success('故事蓝图已保存，后续章节策略会读取它')
    return true
  } catch (error) { toast.error(error); return false }
  finally { saving.value = false }
}

async function toggleLock(item: Record<string, any>, collection: string) {
  const previous = Boolean(item.locked)
  item.locked = !previous
  try {
    const key = String(collection === 'chapter_roadmap' ? item.chapter : item.id)
    const result = await api<{ blueprint: Blueprint; candidate?: Blueprint | null }>(`/api/projects/${props.projectId}/story-blueprint/lock`,
      writeOptions('PUT', { collection, key, locked: item.locked }))
    blueprint.value = structuredClone(result.blueprint)
    if (result.candidate) candidate.value = structuredClone(result.candidate)
    toast.success(previous ? '节点已解锁' : '节点已锁定')
  } catch (error) { item.locked = previous; toast.error(error) }
}
function accelerateCurrentPlot() {
  requirements.value = fastClosureRequirement
  void generate('adjust', fastClosureRequirement)
}
function showCandidateContent() { candidateContent.value?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }
function reminderItems(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String).filter(Boolean)
  if (typeof value === 'string') return value.split(/[；;\n]+/).map(item => item.trim()).filter(Boolean)
  return []
}

async function exportGraph() {
  try {
    const graph = await api<Record<string, any>>(`/api/projects/${props.projectId}/story-blueprint/graph`)
    const url = URL.createObjectURL(new Blob([JSON.stringify(graph, null, 2)], { type: 'application/json' }))
    const link = document.createElement('a'); link.href = url; link.download = 'story-blueprint-graph.json'; link.click()
    URL.revokeObjectURL(url)
  } catch (error) { toast.error(error) }
}

watch(() => props.projectId, () => { directions.value = []; void load() })
onMounted(() => void load())
</script>

<template><div class="blueprint-page">
  <header class="blueprint-hero"><div><span>STORY ARCHITECTURE</span><h1>先画整栋楼，再设计每一层。</h1>
    <p>正文事实、已确认规划和模型建议彼此分离。模型可以提出蓝图，但只有你确认后才会影响后续分幕。</p></div>
    <div class="blueprint-actions"><button class="subtle" :disabled="loading" @click="load">重新载入</button><button class="subtle" :disabled="!blueprint" @click="exportGraph">导出故事线图数据</button></div></header>

  <section class="blueprint-ai panel"><div><h2>{{ hasBlueprintPlan ? '让模型调整或重新设计长期规划' : '让模型建立第一版蓝图' }}</h2>
    <p>说明预期结局、篇幅、希望加强或避免的方向。模型会结合已保存章节摘要，不会直接修改正文。</p></div>
    <textarea v-model="requirements" rows="4" placeholder="例如：后续约三十章完成本卷；调查线必须转入主动设局，同时让家庭线形成独立冲突，不要再围绕日期和档案反复核对。"></textarea>
    <div class="blueprint-mode-actions"><template v-if="hasBlueprintPlan"><button :disabled="generating || !modelName" @click="generate('adjust')">{{ generating ? '正在整理…' : '生成调整候选' }}</button><button class="subtle" :disabled="generating || directionLoading || !modelName" @click="generateDirections">{{ directionLoading ? '正在构思三种方向…' : '重新设计一版' }}</button><button class="subtle" :disabled="generating || !modelName" @click="accelerateCurrentPlot">加速收束当前线</button></template><button v-else :disabled="generating || !modelName" @click="generate('create')">{{ generating ? '正在建立…' : 'AI 建立故事蓝图' }}</button></div></section>

  <section v-if="directions.length" class="panel blueprint-directions"><div class="blueprint-section-head"><div><span>ARCHITECTURE OPTIONS</span><h2>先选择故事发动机</h2></div><small>三种方向不会立即覆盖蓝图；选择后才展开阶段与章节路线。</small></div><div class="blueprint-direction-grid"><article v-for="direction in directions" :key="direction.id"><span>{{ direction.title }}</span><h3>{{ direction.story_engine }}</h3><p><b>当前案件：</b>{{ direction.current_case_resolution }}</p><p><b>主要冲突：</b>{{ direction.dominant_conflict }}</p><p><b>阶段逻辑：</b>{{ direction.phase_logic }}</p><p><b>后续主线：</b>{{ direction.future_arc }}</p><small>{{ direction.reader_promise }}</small><button :disabled="generating" @click="chooseDirection(direction)">选择并展开</button></article></div></section>

  <div v-if="loading" class="outline-empty">正在读取故事蓝图…</div>
  <aside v-if="!loading && candidateError" class="blueprint-candidate"><div><strong>上次候选稿不完整，未予展示</strong><span>{{ candidateError }}。请重新生成，正式蓝图没有改变。</span></div></aside>
  <template v-if="!loading && active">
    <aside v-if="isCandidate" class="blueprint-candidate"><div class="blueprint-candidate-copy"><strong>模型候选稿，尚未应用</strong><span>{{ plan.premise || '候选蓝图已生成' }}</span><small>{{ plan.phases?.length || 0 }} 个阶段 · {{ plan.arcs?.length || 0 }} 条故事线 · {{ plan.milestones?.length || 0 }} 个关键转折 · {{ plan.chapter_roadmap?.length || 0 }} 章近期路线</small><small v-if="plan.phases?.length">阶段：{{ plan.phases.map((item: any) => item.name).join(' → ') }}</small></div>
      <button class="subtle" @click="showCandidateContent">查看候选内容</button><button class="subtle" @click="discardCandidate">放弃候选</button><button :disabled="saving" @click="save(candidate)">{{ saving ? '保存中…' : '确认并应用候选' }}</button></aside>

    <section ref="candidateContent" class="panel blueprint-foundation"><div class="blueprint-section-head"><div><span>01 · FOUNDATION</span><h2>全书设计目标</h2></div><small>这三项决定故事最终建成什么样。</small></div>
      <div class="blueprint-foundation-grid"><label>核心命题<textarea v-model="plan.premise" rows="3"></textarea></label><label>最终抵达的变化<textarea v-model="plan.destination" rows="3"></textarea></label><label>目标结局状态<textarea v-model="plan.ending_state" rows="3"></textarea></label></div>
      <button v-if="!isCandidate" :disabled="saving" @click="save()">{{ saving ? '保存中…' : '保存蓝图文字' }}</button></section>

    <section class="panel"><div class="blueprint-section-head"><div><span>02 · PHASES</span><h2>故事阶段</h2></div><small>每个阶段都有明确的进入状态和退出条件。</small></div>
      <div class="blueprint-phase-track"><article v-for="(phase, index) in plan.phases || []" :key="phase.id" class="blueprint-phase"><b>{{ String(Number(index) + 1).padStart(2, '0') }}</b><div><span>{{ phase.status }}</span><h3>{{ phase.name }}</h3><p>{{ phase.purpose }}</p><dl><dt>进入</dt><dd>{{ phase.entry_state }}</dd><dt>退出</dt><dd>{{ phase.exit_state }}</dd></dl></div><button class="subtle" @click="toggleLock(phase, 'phases')">{{ phase.locked ? '解锁' : '锁定' }}</button></article></div></section>

    <section class="panel"><div class="blueprint-section-head"><div><span>03 · STORY LINES</span><h2>主线与支线</h2></div><small>故事线不是道具清单，而是从当前状态走向目标状态的变化。</small></div>
      <div class="blueprint-arc-grid"><article v-for="arc in plan.arcs || []" :key="arc.id" :class="['blueprint-arc', arc.kind]"><header><span>{{ arc.kind === 'primary' ? '主线' : '支线' }}</span><strong>{{ arc.name }}</strong><button class="subtle" @click="toggleLock(arc, 'arcs')">{{ arc.locked ? '解锁' : '锁定' }}</button></header><p><b>现在：</b>{{ arc.current_state }}</p><p><b>目标：</b>{{ arc.target_state }}</p><p><b>下一压力：</b>{{ arc.next_pressure }}</p><details><summary>交汇与收束</summary><ul><li v-for="item in arc.intersections || []" :key="item">{{ item }}</li></ul><p>{{ arc.closure_condition }}</p></details></article></div></section>

    <section class="panel"><div class="blueprint-section-head"><div><span>04 · MILESTONES</span><h2>关键转折</h2></div><small>它们连接故事线和阶段，防止章节只做局部运动。</small></div>
      <div class="blueprint-milestones"><article v-for="item in plan.milestones || []" :key="item.id"><span>{{ item.target_range }}</span><h3>{{ item.name }}</h3><p>{{ item.purpose }}</p><small>兑现：{{ item.payoff }}</small><button class="subtle" @click="toggleLock(item, 'milestones')">{{ item.locked ? '解锁' : '锁定' }}</button></article></div></section>

    <section class="panel"><div class="blueprint-section-head"><div><span>05 · CHAPTER ROADMAP</span><h2>临近章节路线</h2></div><small>只规定章节作用和结果，不提前写死分幕场景。</small></div>
      <div class="blueprint-roadmap"><article v-for="item in plan.chapter_roadmap || []" :key="item.chapter"><b>第 {{ item.chapter }} 章</b><div><h3>{{ item.role }}</h3><p>{{ item.required_change }}</p><small>章末：{{ item.ending_state }}</small><ul v-if="reminderItems(item.must_not_repeat).length"><li v-for="text in reminderItems(item.must_not_repeat)" :key="text">避免：{{ text }}</li></ul></div><button class="subtle" @click="toggleLock(item, 'chapter_roadmap')">{{ item.locked ? '解锁' : '锁定' }}</button></article></div></section>

    <section class="panel"><div class="blueprint-section-head"><div><span>FACTS / PROPOSALS</span><h2>事实边界与调整记录</h2></div><small>事实来自已保存章节；未来规划不进入事实区。</small></div>
      <details><summary>已写事实 · 截至第 {{ active.facts_through_chapter || 0 }} 章（{{ active.facts?.length || 0 }} 项）</summary><div class="blueprint-facts"><article v-for="fact in active.facts || []" :key="fact.id"><span :class="fact.status">{{ fact.status === 'observed' ? '有依据' : '待核对' }}</span><p>{{ fact.statement }}</p><small>依据章节：{{ fact.evidence_chapters?.join('、') || '未标明' }}</small><button class="subtle" @click="toggleLock(fact, 'facts')">{{ fact.locked ? '解锁事实' : '锁定事实' }}</button></article></div></details>
      <details><summary>模型调整建议（{{ active.proposals?.length || 0 }} 条）</summary><article v-for="proposal in active.proposals || []" :key="proposal.id" class="blueprint-proposal"><strong>{{ proposal.reason }}</strong><ul><li v-for="change in proposal.changes || []" :key="change">{{ change }}</li></ul></article></details></section>
  </template>
  <div v-if="!loading && !active" class="outline-empty">还没有故事蓝图。先让模型根据已保存章节建立第一版。</div>
</div></template>
