<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api } from './api'
import { writeOptions, type Job } from './api'
import { askConfirm } from './confirmService'
import WikiRelationGraph from './WikiRelationGraph.vue'

type RecordItem = { source: string; target: string; predicate: string; value: string; chapter: number; story_time?: string; quote: string; source_type?: string; role_direction_verified?: boolean; direction_from?: string; direction_to?: string; from_role?: string; to_role?: string }
type GraphNode = { id: string; label: string; degree: number }
type GraphEdge = { id: string; source: string; target: string; count: number; chapters: number[]; predicates?: string[]; records: RecordItem[] }
type Candidate = { subject: string; target: string; predicate: string; reason?: string; quote?: string; confidence: string; chapter: number; status?: string; index?: number }
type GraphData = { nodes: GraphNode[]; edges: GraphEdge[]; records_count: number; candidates?: Candidate[]; candidates_count?: number; review_pending?: { subject: string; target: string; reason: string }[]; aliases?: Record<string, string[]>; view?: 'history' | 'merged'; through_chapter?: number | null }
type Identity = { left: string; right: string; reason: string; chapters: number[] }
type IdentityStatus = { candidate_pairs: number; unprocessed: number; confirmed: number; distinct: number; pending: Identity[] }

const props = defineProps<{ projectId: string; currentChapter: number | null; modelName: string }>()
const emit = defineEmits<{ back: []; openChapter: [number: number]; openSubject: [subject: string]; task: [id: string] }>()
const data = ref<GraphData>({ nodes: [], edges: [], records_count: 0 })
const loading = ref(false)
const error = ref('')
const mode = ref<'core' | 'all'>('core')
const query = ref('')
const focusedSearchNode = ref('')
const startChapter = ref<number | null>(null)
const endChapter = ref<number | null>(null)
const selectedNode = ref('')
const selectedEdge = ref('')
const graphView = ref<InstanceType<typeof WikiRelationGraph> | null>(null)
// 默认展示截至当前章节的合并关系；需要追溯证据时再切换到历史记录。
const dataView = ref<'history' | 'merged'>('merged')
const identityOpen = ref(false)
const identityBusy = ref(false)
const identityState = ref<IdentityStatus>({ candidate_pairs: 0, unprocessed: 0, confirmed: 0, distinct: 0, pending: [] })
const identityFeedback = ref('')
const personReviewBusy = ref(false)
const personReviewFeedback = ref('')

async function reviewPerson(subject: string) {
  if (!props.modelName || personReviewBusy.value) return
  if (!await askConfirm({ title: `核对「${subject}」的全部人物关系？`,
    message: '模型会逐对阅读已有 Wiki 与关系记录，调用次数随关联人物数量增加；证据冲突的关系会暂时退出当前图谱。',
    symbol: '系', confirmLabel: '开始关系核对', modelName: props.modelName })) return
  personReviewBusy.value = true; error.value = ''; personReviewFeedback.value = '正在核对关系，可在任务控制台查看进度。'
  try {
    const project = props.projectId
    const started = await api<{ id: string }>(`/api/projects/${project}/wiki/relationships/review-person`,
      writeOptions('POST', { subject, model_name: props.modelName, through_chapter: endChapter.value }))
    emit('task', started.id)
    const poll = async () => {
      try {
        const job = await api<Job>(`/api/projects/${project}/jobs/${started.id}`)
        if (job.status === 'running') { setTimeout(poll, 2500); return }
        personReviewBusy.value = false
        if (job.status !== 'completed') { error.value = job.message; return }
        await load()
        if (data.value.nodes.some(node => node.id === subject)) selectedNode.value = subject
        personReviewFeedback.value = `已核对 ${job.result?.reviewed || 0}/${job.result?.pairs || 0} 对关系；确认 ${job.result?.confirmed || 0} 对，待核查 ${job.result?.uncertain || 0} 对。${job.result?.status === 'partial' ? '模型输出中断，可重新执行。' : ''}`
      } catch (cause) { personReviewBusy.value = false; error.value = String(cause) }
    }
    void poll()
  } catch (cause) { personReviewBusy.value = false; error.value = String(cause) }
}
const rebuildStart = ref(Math.max(1, (props.currentChapter || 1) - 14))
const rebuildEnd = ref(props.currentChapter || 1)
const rebuildBusy = ref(false)

async function rebuildRelationships() {
  if (rebuildBusy.value || !props.modelName || rebuildStart.value < 1 || rebuildEnd.value < rebuildStart.value) return
  if (!await askConfirm({ title: `重新整理第 ${rebuildStart.value}—${rebuildEnd.value} 章人物关系？`,
    message: '模型会读取相关章节正文，重新整理关系；若后面还有已整理章节，也会继续处理以保持关系连贯。原有结果在任务成功前保留。',
    symbol: '关', confirmLabel: '开始重新整理', modelName: props.modelName })) return
  rebuildBusy.value = true; error.value = ''; identityFeedback.value = '正在重新整理人物关系，可在任务控制台查看进度。'
  try {
    const project = props.projectId
    const started = await api<{ id: string }>(`/api/projects/${project}/wiki/relationships/rebuild`,
      writeOptions('POST', { start: rebuildStart.value, end: rebuildEnd.value, model_name: props.modelName }))
    emit('task', started.id)
    const poll = async () => {
      try {
        const job = await api<Job>(`/api/projects/${project}/jobs/${started.id}`)
        if (job.status === 'running') { setTimeout(poll, 2500); return }
        rebuildBusy.value = false
        if (job.status !== 'completed' || job.result?.status === 'partial') {
          error.value = job.result?.status === 'partial' ? '模型输出未完成，原有关系结果已保留。' : job.message; return
        }
        await Promise.all([loadIdentities(), load()])
        identityFeedback.value = `关系整理完成；当前图谱已更新。`
      } catch (cause) { rebuildBusy.value = false; error.value = String(cause) }
    }
    void poll()
  } catch (cause) { rebuildBusy.value = false; error.value = String(cause) }
}

async function loadIdentities() {
  identityState.value = await api<IdentityStatus>(`/api/projects/${props.projectId}/wiki/identities`)
}

async function reviewIdentities() {
  if (identityBusy.value || !props.modelName) return
  identityBusy.value = true; error.value = ''; identityFeedback.value = '正在核对历史人物身份，可在任务控制台查看进度。'
  try {
    const project = props.projectId
    const started = await api<{ id: string }>(`/api/projects/${project}/wiki/identities/review`,
      writeOptions('POST', { model_name: props.modelName }))
    emit('task', started.id)
    const poll = async () => {
      try {
        const job = await api<Job>(`/api/projects/${project}/jobs/${started.id}`)
        if (job.status === 'running') { setTimeout(poll, 2500); return }
        identityBusy.value = false
        if (job.status !== 'completed') { error.value = job.message; return }
        await Promise.all([loadIdentities(), load()])
        identityFeedback.value = `已核对 ${job.result?.processed || 0} 组；待确认 ${identityState.value.pending.length} 组。${job.result?.status === 'partial' ? '模型输出未完成，剩余组合可再次运行。' : ''}`
      } catch (cause) { identityBusy.value = false; error.value = String(cause) }
    }
    void poll()
  } catch (cause) { identityBusy.value = false; error.value = String(cause) }
}

async function decideIdentity(item: Identity, status: 'confirmed' | 'distinct') {
  try {
    await api(`/api/projects/${props.projectId}/wiki/identities/decide`,
      writeOptions('POST', { left: item.left, right: item.right, status }))
    await Promise.all([loadIdentities(), load()])
  } catch (cause) { error.value = String(cause) }
}

function nearName(query: string, name: string): boolean {
  if (!/^[\u4e00-\u9fff]{3,}$/.test(query)) return false
  const chars = Array.from(name.toLocaleLowerCase())
  const needle = Array.from(query)
  for (let offset = 0; offset <= chars.length - needle.length; offset++) {
    let changes = 0
    for (let index = 0; index < needle.length; index++) {
      if (chars[offset + index] !== needle[index]) changes++
    }
    if (changes === 1) return true
  }
  return false
}

const searchMatches = computed(() => {
  const folded = query.value.trim().toLocaleLowerCase()
  if (!folded) return []
  const aliases = data.value.aliases || {}
  const exact: GraphNode[] = []
  const approximate: GraphNode[] = []
  for (const node of data.value.nodes) {
    const names = [node.id, node.label, ...(aliases[node.id] || [])]
    if (names.some(name => name.toLocaleLowerCase().includes(folded))) exact.push(node)
    else if (names.some(name => nearName(folded, name))) approximate.push(node)
  }
  return [...exact, ...approximate]
})
function approximateMatch(node: GraphNode): boolean {
  const folded = query.value.trim().toLocaleLowerCase()
  return ![node.id, node.label, ...(data.value.aliases?.[node.id] || [])]
    .some(name => name.toLocaleLowerCase().includes(folded))
}

const visibleIds = computed(() => {
  const folded = query.value.trim().toLocaleLowerCase()
  if (folded) {
    const matches = new Set(focusedSearchNode.value
      ? [focusedSearchNode.value] : searchMatches.value.map(node => node.id))
    const expanded = new Set(matches)
    for (const edge of data.value.edges) {
      if (matches.has(edge.source)) expanded.add(edge.target)
      if (matches.has(edge.target)) expanded.add(edge.source)
    }
    return expanded
  }
  if (mode.value === 'all') return new Set(data.value.nodes.map(node => node.id))
  return new Set(data.value.nodes.slice(0, 24).map(node => node.id))
})
const visibleEdges = computed(() => data.value.edges.filter(edge => visibleIds.value.has(edge.source) && visibleIds.value.has(edge.target)))
const visibleNodes = computed(() => data.value.nodes.filter(node => visibleIds.value.has(node.id)))
const chosenEdge = computed(() => data.value.edges.find(edge => edge.id === selectedEdge.value) || null)
function edgeDirection(edge: GraphEdge): string {
  const forward = edge.records.some(record => record.role_direction_verified && record.direction_from === edge.source && record.direction_to === edge.target)
  const backward = edge.records.some(record => record.role_direction_verified && record.direction_from === edge.target && record.direction_to === edge.source)
  return backward && !forward ? `${edge.target} → ${edge.source}`
    : forward && !backward ? `${edge.source} → ${edge.target}`
    : `${edge.source} ↔ ${edge.target}`
}
const chosenNode = computed(() => data.value.nodes.find(node => node.id === selectedNode.value) || null)
const nodeRelations = computed(() => selectedNode.value
  ? data.value.edges.filter(edge => edge.source === selectedNode.value || edge.target === selectedNode.value)
  : [])
function neighbourLabel(edge: GraphEdge, nodeId: string): string {
  const other = edge.source === nodeId ? edge.target : edge.source
  const directed = edge.records.find(record => record.role_direction_verified &&
    ((record.direction_from === nodeId && record.direction_to === other) ||
     (record.direction_to === nodeId && record.direction_from === other)))
  if (!directed) return other
  const role = directed.direction_from === other ? directed.from_role : directed.to_role
  return `${other}（${role}）`
}

async function load() {
  if (!props.projectId) return
  loading.value = true; error.value = ''
  try {
    const params = new URLSearchParams()
    if (startChapter.value) params.set('start_chapter', String(startChapter.value))
    if (endChapter.value) params.set('end_chapter', String(endChapter.value))
    params.set('view', dataView.value)
    const suffix = params.size ? `?${params}` : ''
    data.value = await api<GraphData>(`/api/projects/${props.projectId}/wiki/relationships${suffix}`)
    selectedEdge.value = ''; selectedNode.value = ''
  } catch (cause) { error.value = String(cause) }
  finally { loading.value = false }
}
function chooseNode(id: string) { selectedNode.value = selectedNode.value === id ? '' : id; selectedEdge.value = '' }
function focusSearchNode(id: string) { focusedSearchNode.value = id; selectedNode.value = id; selectedEdge.value = '' }
function chooseEdge(id: string) { selectedEdge.value = selectedEdge.value === id ? '' : id; selectedNode.value = '' }
async function confirmCandidate(index: number) {
  try { await api(`/api/projects/${props.projectId}/wiki/relationships/candidates/${index}/confirm`, writeOptions('POST')); await load() }
  catch (cause) { error.value = String(cause) }
}
watch(() => props.projectId, () => { void load(); void loadIdentities() })
watch(query, () => { focusedSearchNode.value = ''; selectedNode.value = '' })
onMounted(() => { endChapter.value = props.currentChapter; void load(); void loadIdentities() })
</script>

<template><div class="feature-page relation-page">
  <div class="relation-hero"><div><button class="text-button relation-back" @click="emit('back')">← 返回人物档案</button><span class="eyebrow">小说 Wiki · 人物关系谱</span><h1>让人物之间的线，自己开口。</h1><p>{{ dataView === 'merged' ? `当前合并关系：仅保留模型确认或作者设定的稳定关系${data.candidates_count ? `；另有 ${data.candidates_count} 条候选线索待确认。` : '。'}` : '历史关系：按章节保留全部原文提取记录，包含待整理线索。' }}</p></div><div class="relation-tally"><strong>{{ data.nodes.length }}</strong><span>人物</span><strong>{{ data.edges.length }}</strong><span>关系线</span><strong>{{ data.records_count }}</strong><span>原文记录</span></div></div>
  <p v-if="error" class="notice error">{{ error }}</p>
  <section class="panel relation-identity"><button class="relation-identity-toggle" @click="identityOpen = !identityOpen"><span><small>高级操作</small><strong>历史人物身份整理</strong></span><span>{{ identityOpen ? '收起 ↑' : '展开 ↓' }}</span></button>
    <div v-if="identityOpen" class="relation-identity-body"><p>用模型核对不同姓名是否指同一人物。按组保存进度；已处理的组再次运行时会跳过。</p><p>待核对 {{ identityState.unprocessed }} 组 · 已合并 {{ identityState.confirmed }} 组 · 待确认 {{ identityState.pending.length }} 组</p><button :disabled="identityBusy || !modelName || !identityState.unprocessed" @click="reviewIdentities">{{ identityBusy ? '整理中…' : '整理历史人物身份' }}</button><p v-if="identityFeedback">{{ identityFeedback }}</p>
      <div class="relation-rebuild"><h3>补全遗漏的人物关系</h3><p>重新读取章节正文并整理关系。若后面还有已整理章节，任务会继续处理，以保持关系连贯。</p><label>起始章 <input v-model.number="rebuildStart" type="number" min="1"></label><label>结束章 <input v-model.number="rebuildEnd" type="number" :min="rebuildStart"></label><button :disabled="rebuildBusy || !modelName || rebuildEnd < rebuildStart" @click="rebuildRelationships">{{ rebuildBusy ? '整理中…' : '重新整理该范围关系' }}</button></div>
      <div v-if="identityState.pending.length" class="relation-identity-pending"><h3>待确认身份</h3><article v-for="item in identityState.pending" :key="`${item.left}|${item.right}`"><strong>{{ item.left }} / {{ item.right }}</strong><p>{{ item.reason || '模型没有给出充分依据。' }}</p><small v-if="item.chapters?.length">依据章节：{{ item.chapters.join('、') }}</small><div><button @click="decideIdentity(item, 'confirmed')">确认为同一人</button><button class="secondary" @click="decideIdentity(item, 'distinct')">保留为不同人</button></div></article></div></div>
  </section>
  <section v-if="dataView === 'merged' && data.candidates?.length" class="panel"><h2>待核查的人物关系</h2><p>模型给出的候选与不合格关系名称，需要核查后才能进入当前关系图。</p><article v-for="item in data.candidates" :key="item.index"><strong>{{ item.subject }} → {{ item.target }}：{{ item.predicate }}</strong><p>{{ item.reason || item.quote }}</p><small>第 {{ item.chapter }} 章</small> <button v-if="item.confidence === 'tentative'" @click="confirmCandidate(item.index!)">确认关系</button></article></section>
  <section v-if="dataView === 'merged' && data.review_pending?.length" class="panel"><h2>证据冲突，暂不显示的关系</h2><article v-for="item in data.review_pending" :key="`${item.subject}|${item.target}`"><strong>{{ item.subject }} ↔ {{ item.target }}</strong><p>{{ item.reason }}</p></article></section>
  <section class="relation-workbench">
    <div class="relation-tools"><label>寻找人物<input v-model="query" placeholder="输入人物名称"></label><label>起始章<input v-model.number="startChapter" type="number" min="1" placeholder="不限"></label><label>结束章<input v-model.number="endChapter" type="number" min="1" placeholder="不限"></label><button :disabled="loading" @click="load">{{ loading ? '读取中…' : '应用章节范围' }}</button><div class="relation-mode"><button :class="{ active: dataView === 'merged' }" @click="dataView = 'merged'; startChapter = null; void load()">当前合并关系</button><button :class="{ active: dataView === 'history' }" @click="dataView = 'history'; void load()">历史关系记录</button><button :class="{ active: mode === 'core' }" @click="mode = 'core'">核心关系</button><button :class="{ active: mode === 'all' }" @click="mode = 'all'">全部关系</button></div></div>
    <div v-if="query.trim()" class="relation-neighbours"><span>匹配人物 {{ searchMatches.length }} 位：</span><button v-for="node in searchMatches" :key="node.id" :class="{ active: focusedSearchNode === node.id }" @click="focusSearchNode(node.id)">{{ node.label }}{{ approximateMatch(node) ? '（近似匹配）' : '' }} · {{ node.degree }} 条关系</button><span v-if="!searchMatches.length">当前关系中未找到该人物，可查看历史记录或核对别名。</span></div>
    <div class="relation-stage"><div class="relation-canvas"><div class="relation-canvas-head"><span>当前展示 {{ visibleNodes.length }}/{{ data.nodes.length }} 人 · {{ visibleEdges.length }}/{{ data.edges.length }} 条线</span><div><button class="text-button" @click="graphView?.zoomIn()">放大</button><button class="text-button" @click="graphView?.zoomOut()">缩小</button><button class="text-button" @click="graphView?.fit()">适应画布</button></div></div>
      <WikiRelationGraph v-if="visibleNodes.length" ref="graphView" :nodes="visibleNodes" :edges="visibleEdges" :selected-node="selectedNode" :selected-edge="selectedEdge" @select-node="chooseNode" @select-edge="chooseEdge" @clear="selectedNode = ''; selectedEdge = ''"/>
      <p v-if="!visibleNodes.length && !loading" class="relation-empty">当前条件下没有可显示的关系。调整人物名称或章节范围。</p></div>
      <aside class="relation-inspector"><template v-if="chosenEdge"><span class="eyebrow">关系依据 · {{ chosenEdge.count }} 条</span><h2>{{ edgeDirection(chosenEdge) }}</h2><p>只有已核实双方具体身份的非对称关系才显示箭头；其余线条只表示有关联，不表示身份方向。</p><div class="relation-evidence"><article v-for="(record, index) in chosenEdge.records" :key="index"><div><span>{{ record.chapter ? `第 ${record.chapter} 章` : '作者设定' }}</span><b>{{ record.role_direction_verified ? `${record.direction_from}（${record.from_role}）→ ${record.direction_to}（${record.to_role}）` : `${record.source} ↔ ${record.target}` }}：{{ record.predicate }}{{ record.value && record.value !== record.predicate ? ` · ${record.value}` : '' }}</b></div><blockquote>{{ record.quote }}</blockquote><button v-if="record.chapter" class="text-button" @click="emit('openChapter', record.chapter)">打开本章 ↗</button></article></div></template>
        <template v-else-if="chosenNode"><span class="eyebrow">人物节点</span><h2>{{ chosenNode.label }}</h2><p>{{ chosenNode.degree }} 条关系记录，连接 {{ nodeRelations.length }} 条关系线。</p><button @click="emit('openSubject', chosenNode.id)">查看人物档案</button> <button :disabled="personReviewBusy || !modelName" @click="reviewPerson(chosenNode.id)">{{ personReviewBusy ? '核对中…' : '核对该人物全部关系' }}</button><p v-if="personReviewFeedback">{{ personReviewFeedback }}</p><div class="relation-neighbours"><button v-for="edge in nodeRelations" :key="edge.id" @click="chooseEdge(edge.id)"><span>{{ neighbourLabel(edge, chosenNode.id) }}</span><small>{{ edge.count }} 条 ↗</small></button></div></template>
        <template v-else><span class="eyebrow">阅图方法</span><h2>先选一条线。</h2><p>箭头仅表示已核实的非对称身份方向；无箭头表示身份方向尚未核实或关系本身对等。线条粗细代表记录数量。核心关系按记录数选取前24人。</p><ul><li>滚轮缩放画布</li><li>拖动空白处平移</li><li>点击人物突出相邻关系</li><li>点击连线核对章节原文</li></ul></template></aside>
    </div>
  </section>
</div></template>
