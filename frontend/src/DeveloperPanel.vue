<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { Core } from 'cytoscape'
import { api } from './api'

type Source = { id: string; path: string; symbol: string | null }
type InputDetail = { 0: string; 1: string; 2: string }
type Node = { id: string; label: string; layer: string; inputs: string; outputs: string; boundary: string; code: Source[]; prompts: Source[]; input_details: InputDetail[] }
type Edge = { id: string; source: string; target: string; label: string }
type MapData = { nodes: Node[]; edges: Edge[]; scope: string; version: string }
type SourceData = Source & { text: string; start_line: number; end_line: number; total_end_line: number; truncated: boolean; hash: string }
type Run = { id: string; bytes: number; modified_at: number }
type Event = { index: number; event: string; timestamp: string | null; stage: string; fields: string[]; elapsed_ms: number | null }
const props = defineProps<{ projectId: string }>()
const map = ref<MapData | null>(null)
const selected = ref('audit')
const mode = ref<'map' | 'runtime'>('map')
const error = ref('')
const graphElement = ref<HTMLElement | null>(null)
const sourceData = ref<SourceData | null>(null)
const sourceLoading = ref(false)
const runs = ref<Run[]>([])
const runId = ref('')
const events = ref<Event[]>([])
const nextOffset = ref<number | null>(null)
const eventData = ref<{ text: string; truncated: boolean; total_chars: number; warning: string } | null>(null)
const eventIndex = ref<number | null>(null)
const inspector = ref<'source' | 'event' | null>(null)
const loading = ref(false)
let graph: Core | null = null
let observer: ResizeObserver | null = null
let sourceRequest = 0
let logRequest = 0
let mounted = false
const current = computed(() => map.value?.nodes.find(n => n.id === selected.value))
const incoming = computed(() => map.value?.edges.filter(e => e.target === selected.value) || [])
const outgoing = computed(() => map.value?.edges.filter(e => e.source === selected.value) || [])
function label(id: string) { return map.value?.nodes.find(n => n.id === id)?.label || id }
function humanTime(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === '') return '未记录时间'
  const numeric = typeof value === 'number' ? value : Number(value)
  const date = Number.isFinite(numeric) && String(value).trim() !== ''
    ? new Date(numeric < 10_000_000_000 ? numeric * 1000 : numeric)
    : new Date(String(value))
  if (Number.isNaN(date.getTime())) return '时间格式未知'
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}
function choose(id: string) { selected.value = id; sourceData.value = null; inspector.value = null; sourceRequest++; sourceLoading.value = false; highlight() }
function closeInspector() { inspector.value = null }
function highlight() {
  if (!graph) return
  graph.elements().removeClass('chosen connected faded')
  const node = graph.$id(selected.value)
  node.addClass('chosen')
  node.closedNeighborhood().addClass('connected')
  graph.elements().not(node.closedNeighborhood()).addClass('faded')
}
async function render() {
  await nextTick()
  if (!graphElement.value || !map.value || mode.value !== 'map') return
  const module = await import('cytoscape')
  if (!mounted || !graphElement.value || mode.value !== 'map') return
  graph?.destroy(); observer?.disconnect()
  const positions: Record<string, [number, number]> = {
    context:[90,55], agent:[290,55], proposal:[490,55], api:[690,55], jobs:[690,190],
    plan:[90,320], write:[290,320], audit:[490,320], revise:[690,320], result:[490,480], model:[90,480],
  }
  graph = module.default({ container:graphElement.value,
    elements:[...map.value.nodes.map(n => ({ data:{ id:n.id,label:n.label },position:{x:positions[n.id]![0],y:positions[n.id]![1]} })),
      ...map.value.edges.map(e => ({data:e}))], layout:{name:'preset',padding:30},
    minZoom:.45, maxZoom:2, userZoomingEnabled:false, userPanningEnabled:false, autoungrabify:true,
    style:[
      {selector:'node',style:{shape:'round-rectangle',width:132,height:46,'background-color':'#e5efeb','border-width':1,'border-color':'#bacfc5',label:'data(label)','font-size':14,'font-family':'Microsoft YaHei','text-valign':'center',color:'#263a40'}},
      {selector:'edge',style:{width:1.3,'line-color':'#a9bdb6','target-arrow-color':'#a9bdb6','target-arrow-shape':'triangle','curve-style':'bezier',label:'data(label)','font-size':11,color:'#5a7770','text-background-color':'#fbfdfb','text-background-opacity':.95,'text-background-padding':'3px','text-rotation':'autorotate'}},
      {selector:'.faded',style:{opacity:.22}},
      {selector:'node.chosen',style:{'background-color':'#397b69',color:'#fff','border-color':'#397b69'}},
      {selector:'edge.connected',style:{'line-color':'#397b69','target-arrow-color':'#397b69',width:2}},
    ],
  })
  graph.on('tap','node',event => choose(event.target.id()))
  observer = new ResizeObserver(() => { graph?.resize(); graph?.fit(undefined,30) })
  observer.observe(graphElement.value)
  highlight()
}
async function openSource(source: Source) {
  const request = ++sourceRequest
  sourceLoading.value = true; error.value = ''; sourceData.value = null; inspector.value = 'source'
  try {
    const result = await api<SourceData>(`/api/developer/sources/${source.id}`)
    if (request === sourceRequest) sourceData.value = result
  } catch (cause) { if (request === sourceRequest) error.value = String(cause) }
  finally { if (request === sourceRequest) sourceLoading.value = false }
}
async function refreshRuns() {
  const request = ++logRequest
  runs.value = []; events.value = []; runId.value = ''; eventData.value = null; eventIndex.value = null; inspector.value = null; nextOffset.value = null
  if (!props.projectId) return
  loading.value = true
  try {
    const result = await api<Run[]>(`/api/projects/${props.projectId}/developer/runs`)
    if (request === logRequest) runs.value = result
  } catch (cause) { if (request === logRequest) error.value = String(cause) }
  finally { if (request === logRequest) loading.value = false }
}
async function openRun(id: string, append = false) {
  const offset = append ? nextOffset.value : 0
  if (offset === null) return
  const request = ++logRequest
  runId.value = id; loading.value = true; error.value = ''
  if (!append) { events.value = []; eventData.value = null; eventIndex.value = null; inspector.value = null; nextOffset.value = null }
  try {
    const result = await api<{ events:Event[]; next_offset:number | null }>(`/api/projects/${props.projectId}/developer/runs/${id}?offset=${offset}`)
    if (request !== logRequest) return
    events.value = append ? [...events.value,...result.events] : result.events
    nextOffset.value = result.next_offset
  } catch (cause) { if (request === logRequest) error.value = String(cause) }
  finally { if (request === logRequest) loading.value = false }
}
async function openEvent(event: Event) {
  const request = ++logRequest
  eventIndex.value = event.index; eventData.value = null; inspector.value = 'event'; loading.value = true; error.value = ''
  try {
    const result = await api<typeof eventData.value>(`/api/projects/${props.projectId}/developer/runs/${runId.value}/events/${event.index}`,{headers:{'X-Novel-Workbench':'1'}})
    if (request === logRequest) eventData.value = result
  } catch (cause) { if (request === logRequest) error.value = String(cause) }
  finally { if (request === logRequest) loading.value = false }
}
watch(mode, () => { if (mode.value === 'map') render(); else { graph?.destroy(); graph=null; observer?.disconnect(); refreshRuns() } })
watch(() => props.projectId, () => { error.value=''; refreshRuns() })
onMounted(async () => {
  mounted = true
  try { map.value = await api<MapData>('/api/developer/map'); if (mounted) await render() }
  catch (cause) { error.value=String(cause) }
})
onBeforeUnmount(() => { mounted=false; sourceRequest++; logRequest++; graph?.destroy(); observer?.disconnect() })
</script>

<template>
  <section class="developer-panel">
    <header class="developer-heading"><div><span class="developer-eyebrow">开发者 · 只读</span><h1>从入口，看到每一次模型调用。</h1><p>逻辑地图看设计；运行回放看实际发生。这里不会生成或修改小说。</p></div></header>
    <div class="developer-tabs" role="tablist" aria-label="开发者视图"><button role="tab" :aria-selected="mode === 'map'" @click="mode = 'map'">逻辑地图</button><button role="tab" :aria-selected="mode === 'runtime'" @click="mode = 'runtime'">运行回放</button></div>
    <p v-if="error" class="developer-error" role="alert">{{ error }}</p>
    <template v-if="mode === 'map'">
      <p class="developer-note">{{ map?.scope || '正在读取结构…' }} <span v-if="map">· 地图版本 {{ map.version }}</span></p>
      <div ref="graphElement" class="developer-graph" role="img" aria-label="核心数据流：上下文到智能体、操作提案、功能API和后台任务，分支进入规划、续写、审校与修订，再输出结果并记录模型调用。"></div>
      <nav class="developer-node-list" aria-label="选择逻辑节点"><button v-for="node in map?.nodes" :key="node.id" :aria-pressed="selected === node.id" @click="choose(node.id)">{{ node.label }}</button></nav>
      <section v-if="current" class="developer-detail">
        <header><span>{{ current.layer }}</span><h2>{{ current.label }}</h2></header>
        <div class="developer-data-flow"><div><small>输入概览</small><p>{{ current.inputs }}</p></div><span aria-hidden="true">→</span><div><small>输出</small><p>{{ current.outputs }}</p></div></div>
        <section v-if="current.input_details.length" class="developer-input-inventory">
          <header><h3>输入材料清单</h3><p>每项说明它从哪里来、包含什么；来源是事实依据、操作状态还是方法规则。</p></header>
          <div class="developer-input-cards"><article v-for="item in current.input_details" :key="item[0]"><strong>{{ item[0] }}</strong><code>{{ item[1] }}</code><p>{{ item[2] }}</p></article></div>
        </section>
        <p class="developer-boundary">{{ current.boundary }}</p>
        <div class="developer-neighbours"><span>上游</span><button v-for="edge in incoming" :key="edge.id" @click="choose(edge.source)">{{ label(edge.source) }} · {{ edge.label }}</button><span v-if="!incoming.length">入口</span></div>
        <div class="developer-neighbours"><span>下游</span><button v-for="edge in outgoing" :key="edge.id" @click="choose(edge.target)">{{ label(edge.target) }} · {{ edge.label }}</button><span v-if="!outgoing.length">持久化 / 展示</span></div>
        <div class="developer-source-columns"><section><h3>代码入口</h3><button v-for="item in current.code" :key="item.id" @click="openSource(item)"><code>{{ item.path }}</code><small v-if="item.symbol">{{ item.symbol }}</small></button></section><section><h3>提示词来源与组装</h3><p v-if="!current.prompts.length">此节点不直接组装提示词，沿下游查找。</p><button v-for="item in current.prompts" :key="item.id" @click="openSource(item)"><code>{{ item.path }}</code><small v-if="item.symbol">{{ item.symbol }}</small></button></section></div>
        <p v-if="sourceLoading" class="developer-inline-status">正在打开源码检查器…</p>
      </section>
    </template>
    <template v-else>
      <div class="developer-run-toolbar"><p>来自当前工程的 runs/plan_logs，按事件顺序展示，不推测未记录的调用。</p><button :disabled="loading" @click="refreshRuns">刷新日志</button></div>
      <p class="developer-note">展开详情可能包含小说全文及提示词。已隐藏已配置密钥和常见凭证，但分享前仍需人工检查。</p>
      <p v-if="!props.projectId">请先选择工程。</p><p v-else-if="!runs.length && !loading">尚无完整调用日志。先在创作流程运行一次任务，再刷新。</p>
      <div class="developer-replay"><aside aria-label="选择运行日志"><button v-for="run in runs" :key="run.id" :aria-pressed="runId === run.id" @click="openRun(run.id)"><code>{{ run.id }}</code><small>{{ humanTime(run.modified_at) }} · {{ Math.round(run.bytes / 1024) }} KB</small></button></aside><section><p v-if="!runId">选择一份日志，查看工具查阅、提示词、模型返回与验收事件。</p><p v-if="loading">正在读取…</p><ol class="developer-events"><li v-for="event in events" :key="event.index"><button :aria-pressed="eventIndex === event.index" @click="openEvent(event)"><span>{{ event.index + 1 }}</span><div><strong>{{ event.event }}</strong><small>{{ humanTime(event.timestamp) }} {{ event.stage ? '· ' + event.stage : '' }} {{ event.elapsed_ms != null ? '· ' + event.elapsed_ms + 'ms' : '' }}</small><small>字段：{{ event.fields.join(' · ') }}</small></div></button></li></ol><button v-if="nextOffset !== null" :disabled="loading" @click="openRun(runId, true)">继续读取事件</button></section></div>
    </template>
    <div v-if="inspector" class="developer-inspector-backdrop" role="presentation" @click.self="closeInspector">
      <section class="developer-inspector" role="dialog" aria-modal="true" :aria-label="inspector === 'source' ? '源码检查器' : '运行日志检查器'">
        <header><div><span>{{ inspector === 'source' ? '源码与 Skill 检查器' : '实际运行记录' }}</span><strong v-if="inspector === 'source' && sourceData">{{ sourceData.path }}:{{ sourceData.start_line }}</strong><strong v-else-if="inspector === 'event'">事件 {{ (eventIndex ?? 0) + 1 }}</strong><strong v-else>正在读取…</strong></div><button class="developer-inspector-close" aria-label="关闭检查器" @click="closeInspector">关闭 ×</button></header>
        <p v-if="inspector === 'source' && sourceLoading" class="developer-inline-status">正在读取已登记的源码片段…</p>
        <template v-else-if="inspector === 'source' && sourceData"><p class="developer-inspector-meta">SHA {{ sourceData.hash.slice(0,12) }} · 第 {{ sourceData.start_line }}–{{ sourceData.end_line }} 行</p><pre><code>{{ sourceData.text }}</code></pre><p v-if="sourceData.truncated">仅展示前250行，该符号截至第{{ sourceData.total_end_line }}行；完整内容请在本地源码查看。</p></template>
        <template v-else-if="inspector === 'event' && eventData"><p class="developer-inspector-meta">记录字符数 {{ eventData.total_chars.toLocaleString() }}</p><pre><code>{{ eventData.text }}</code></pre><p v-if="eventData.truncated">该事件过长，仅显示前160000字符。完整日志请在本地查看。</p><p>{{ eventData.warning }}</p></template>
        <p v-else class="developer-inline-status">正在读取详情…</p>
      </section>
    </div>
  </section>
</template>

<style scoped>
.developer-panel{max-width:1300px;margin:0 auto;padding:26px;color:#263a40}.developer-heading h1{font-family:"KaiTi","STKaiti",serif;font-size:30px;font-weight:400;margin:10px 0}.developer-heading p,.developer-note{color:#698585;font-size:13px;line-height:1.8}.developer-eyebrow{font-size:12px;letter-spacing:.12em;color:#397b69}.developer-tabs{display:flex;gap:8px;margin:22px 0 14px;border-bottom:1px solid #d4e1dd;padding-bottom:12px}.developer-panel button{border:1px solid #bdd2c8;background:#fbfdfb;color:#356b59;border-radius:6px;padding:9px 13px;text-align:left}.developer-panel button[aria-selected="true"],.developer-panel button[aria-pressed="true"]{background:#397b69;color:#fff;border-color:#397b69}.developer-panel button:focus-visible{outline:2px solid #397b69;outline-offset:3px}.developer-panel button:disabled{opacity:.5;cursor:default}.developer-graph{height:480px;background:#fbfdfb;border:1px solid #d4e1dd;border-radius:9px}.developer-node-list{display:flex;flex-wrap:wrap;gap:7px;margin:14px 0 24px}.developer-node-list button{font-size:12px}.developer-detail{padding:24px;background:#fff;border:1px solid #d4e1dd;border-radius:9px}.developer-detail>header{display:flex;align-items:baseline;gap:14px}.developer-detail>header>span{color:#698585;font-size:12px}.developer-detail h2{margin:0;font-size:21px}.developer-data-flow{display:grid;grid-template-columns:1fr auto 1fr;gap:20px;align-items:center;margin:22px 0}.developer-data-flow>div{padding:14px 18px;background:#edf4ef;border-radius:6px}.developer-data-flow small{color:#698585}.developer-data-flow p{margin:6px 0;line-height:1.8;font-size:14px}.developer-boundary{border-left:3px solid #bb9b52;padding-left:12px;font-size:13px;line-height:1.8}.developer-neighbours{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:10px 0;font-size:12px}.developer-neighbours>span:first-child{width:40px;color:#698585}.developer-source-columns{display:grid;grid-template-columns:1fr 1fr;gap:28px;margin-top:24px}.developer-source-columns h3{font-size:15px}.developer-source-columns button{display:block;width:100%;margin:7px 0;overflow-wrap:anywhere}.developer-panel code{font-family:Consolas,monospace;font-size:12px}.developer-panel small{display:block;font-size:12px;margin-top:5px}.developer-code{margin-top:22px}.developer-code header{display:flex;flex-wrap:wrap;gap:12px;justify-content:space-between;font-size:12px;overflow-wrap:anywhere}.developer-code pre{max-height:520px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;background:#edf3f1;padding:18px;border-radius:6px;line-height:1.7}.developer-code p{font-size:12px;color:#698585}.developer-error{color:#a33e2a}.developer-run-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;font-size:13px}.developer-replay{display:grid;grid-template-columns:285px minmax(0,1fr);gap:24px;margin-top:22px}.developer-replay aside button{display:block;width:100%;margin-bottom:9px;overflow-wrap:anywhere}.developer-events{list-style:none;padding:0;margin:0}.developer-events button{display:flex;gap:14px;width:100%;margin-bottom:8px;overflow-wrap:anywhere}.developer-events button>div{min-width:0}.developer-events strong{font-size:13px}.developer-events button>span{font:12px Consolas,monospace}.developer-events small{color:inherit;opacity:.8}@media(max-width:800px){.developer-panel{padding:16px}.developer-replay,.developer-source-columns{grid-template-columns:1fr}.developer-graph{height:350px}.developer-data-flow{grid-template-columns:1fr;gap:8px}.developer-data-flow>span{display:none}.developer-detail{padding:16px}.developer-run-toolbar{align-items:flex-start}}
.developer-input-inventory{margin:20px 0 22px;padding:17px 18px;border:1px solid #d8e5e0;background:#f7faf8;border-radius:7px}.developer-input-inventory header{display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}.developer-input-inventory h3{margin:0;font-size:15px}.developer-input-inventory header p{margin:0;color:#698585;font-size:12px;line-height:1.7}.developer-input-cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin-top:13px}.developer-input-cards article{min-width:0;padding:12px 13px;background:#fff;border-left:3px solid #a6c5b8}.developer-input-cards strong{display:block;font-size:13px}.developer-input-cards code{display:inline-block;margin-top:5px;padding:2px 5px;background:#edf4ef;color:#356b59;font-size:11px;overflow-wrap:anywhere}.developer-input-cards p{margin:7px 0 0;color:#526d68;font-size:12px;line-height:1.65}.developer-inline-status{font-size:13px;color:#698585}.developer-inspector-backdrop{position:fixed;z-index:80;inset:0;display:flex;justify-content:flex-end;background:rgba(24,47,49,.32);padding:18px}.developer-inspector{display:flex;flex-direction:column;width:min(880px,100%);height:calc(100vh - 36px);padding:21px 24px;background:#fbfdfb;border-left:4px solid #397b69;box-shadow:-18px 0 42px rgba(22,48,47,.2);border-radius:10px 0 0 10px}.developer-inspector>header{display:flex;justify-content:space-between;align-items:flex-start;gap:18px;padding-bottom:15px;border-bottom:1px solid #d4e1dd}.developer-inspector>header span{display:block;margin-bottom:5px;color:#698585;font-size:12px}.developer-inspector>header strong{display:block;overflow-wrap:anywhere;font:600 13px Consolas,monospace}.developer-inspector-close{flex:0 0 auto}.developer-inspector-meta{margin:14px 0 8px;color:#698585;font-size:12px}.developer-inspector pre{flex:1;min-height:0;margin:0;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;background:#edf3f1;padding:18px;border-radius:6px;line-height:1.7}.developer-inspector>p{font-size:12px;color:#698585}@media(max-width:800px){.developer-input-cards{grid-template-columns:1fr}.developer-inspector-backdrop{padding:0}.developer-inspector{height:100vh;border-radius:0;padding:17px}}
</style>
