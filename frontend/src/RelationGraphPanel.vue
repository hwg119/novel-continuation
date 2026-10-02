<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api } from './api'
import WikiRelationGraph from './WikiRelationGraph.vue'

type RecordItem = { source: string; target: string; predicate: string; value: string; chapter: number; story_time?: string; quote: string }
type GraphNode = { id: string; label: string; degree: number }
type GraphEdge = { id: string; source: string; target: string; count: number; chapters: number[]; records: RecordItem[] }
type GraphData = { nodes: GraphNode[]; edges: GraphEdge[]; records_count: number }

const props = defineProps<{ projectId: string; currentChapter: number | null }>()
const emit = defineEmits<{ back: []; openChapter: [number: number]; openSubject: [subject: string] }>()
const data = ref<GraphData>({ nodes: [], edges: [], records_count: 0 })
const loading = ref(false)
const error = ref('')
const mode = ref<'core' | 'all'>('core')
const query = ref('')
const startChapter = ref<number | null>(null)
const endChapter = ref<number | null>(null)
const selectedNode = ref('')
const selectedEdge = ref('')
const graphView = ref<InstanceType<typeof WikiRelationGraph> | null>(null)

const visibleIds = computed(() => {
  const folded = query.value.trim().toLocaleLowerCase()
  if (folded) {
    const matches = new Set(data.value.nodes.filter(node => node.label.toLocaleLowerCase().includes(folded)).map(node => node.id))
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
const chosenNode = computed(() => data.value.nodes.find(node => node.id === selectedNode.value) || null)
const nodeRelations = computed(() => selectedNode.value
  ? data.value.edges.filter(edge => edge.source === selectedNode.value || edge.target === selectedNode.value)
  : [])

async function load() {
  if (!props.projectId) return
  loading.value = true; error.value = ''
  try {
    const params = new URLSearchParams()
    if (startChapter.value) params.set('start_chapter', String(startChapter.value))
    if (endChapter.value) params.set('end_chapter', String(endChapter.value))
    const suffix = params.size ? `?${params}` : ''
    data.value = await api<GraphData>(`/api/projects/${props.projectId}/wiki/relationships${suffix}`)
    selectedEdge.value = ''; selectedNode.value = ''
  } catch (cause) { error.value = String(cause) }
  finally { loading.value = false }
}
function chooseNode(id: string) { selectedNode.value = selectedNode.value === id ? '' : id; selectedEdge.value = '' }
function chooseEdge(id: string) { selectedEdge.value = selectedEdge.value === id ? '' : id; selectedNode.value = '' }
watch(() => props.projectId, () => void load())
onMounted(() => { endChapter.value = props.currentChapter; void load() })
</script>

<template><div class="feature-page relation-page">
  <div class="relation-hero"><div><button class="text-button relation-back" @click="emit('back')">← 返回人物档案</button><span class="eyebrow">小说 Wiki · 完整关系谱</span><h1>让人物之间的线，自己开口。</h1><p>每条边都来自可核对的章节原文；点击人物看其关系，点击连线看证据。</p></div><div class="relation-tally"><strong>{{ data.nodes.length }}</strong><span>人物</span><strong>{{ data.edges.length }}</strong><span>关系线</span><strong>{{ data.records_count }}</strong><span>原文记录</span></div></div>
  <p v-if="error" class="notice error">{{ error }}</p>
  <section class="relation-workbench">
    <div class="relation-tools"><label>寻找人物<input v-model="query" placeholder="输入人物名称"></label><label>起始章<input v-model.number="startChapter" type="number" min="1" placeholder="不限"></label><label>结束章<input v-model.number="endChapter" type="number" min="1" placeholder="不限"></label><button :disabled="loading" @click="load">{{ loading ? '读取中…' : '应用章节范围' }}</button><div class="relation-mode"><button :class="{ active: mode === 'core' }" @click="mode = 'core'">核心关系</button><button :class="{ active: mode === 'all' }" @click="mode = 'all'">全部关系</button></div></div>
    <div class="relation-stage"><div class="relation-canvas"><div class="relation-canvas-head"><span>当前展示 {{ visibleNodes.length }}/{{ data.nodes.length }} 人 · {{ visibleEdges.length }}/{{ data.edges.length }} 条线</span><div><button class="text-button" @click="graphView?.zoomIn()">放大</button><button class="text-button" @click="graphView?.zoomOut()">缩小</button><button class="text-button" @click="graphView?.fit()">适应画布</button></div></div>
      <WikiRelationGraph v-if="visibleNodes.length" ref="graphView" :nodes="visibleNodes" :edges="visibleEdges" :selected-node="selectedNode" :selected-edge="selectedEdge" @select-node="chooseNode" @select-edge="chooseEdge" @clear="selectedNode = ''; selectedEdge = ''"/>
      <p v-if="!visibleNodes.length && !loading" class="relation-empty">当前条件下没有可显示的关系。调整人物名称或章节范围。</p></div>
      <aside class="relation-inspector"><template v-if="chosenEdge"><span class="eyebrow">关系原文 · {{ chosenEdge.count }} 条</span><h2>{{ chosenEdge.source }} ↔ {{ chosenEdge.target }}</h2><div class="relation-evidence"><article v-for="(record, index) in chosenEdge.records" :key="index"><div><span>第 {{ record.chapter }} 章</span><b>{{ record.predicate }}{{ record.value ? ` · ${record.value}` : '' }}</b></div><blockquote>{{ record.quote }}</blockquote><button class="text-button" @click="emit('openChapter', record.chapter)">打开本章 ↗</button></article></div></template>
        <template v-else-if="chosenNode"><span class="eyebrow">人物节点</span><h2>{{ chosenNode.label }}</h2><p>{{ chosenNode.degree }} 条关系记录，连接 {{ nodeRelations.length }} 条关系线。</p><button @click="emit('openSubject', chosenNode.id)">查看人物档案</button><div class="relation-neighbours"><button v-for="edge in nodeRelations" :key="edge.id" @click="chooseEdge(edge.id)"><span>{{ edge.source === chosenNode.id ? edge.target : edge.source }}</span><small>{{ edge.count }} 条 ↗</small></button></div></template>
        <template v-else><span class="eyebrow">阅图方法</span><h2>先选一条线。</h2><p>线条粗细代表原文记录数量。核心关系按记录数选取前24人；“全部关系”不会省略节点。</p><ul><li>滚轮缩放画布</li><li>拖动空白处平移</li><li>点击人物突出相邻关系</li><li>点击连线核对章节原文</li></ul></template></aside>
    </div>
  </section>
</div></template>
