<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api, writeOptions, type Job } from './api'
import { toast } from './toastService'

const props = defineProps<{ projectId: string; modelName: string; chapters: number[] }>()
const emit = defineEmits<{ task: [id: string] }>()
const start = ref(1)
const end = ref(1)
const extracting = ref(false)
const loading = ref(false)
const analysis = ref<Record<string, any> | null>(null)
const selectedChapter = ref<number | null>(null)
const selectedCharacter = ref('')
const palette = ['#2f6f61', '#a96f4c', '#526f9b', '#976b8d', '#917a3c', '#5b8184', '#9a5d59']
const rows = computed<any[]>(() => analysis.value?.chapters || [])
const storylines = computed<any[]>(() => analysis.value?.storylines || [])
const selectedRow = computed(() => rows.value.find(row => row.chapter === selectedChapter.value) || rows.value[0] || null)
const relationNodes = computed(() => {
  const names = [...new Set<string>((analysis.value?.relationships || []).flatMap((edge: any) => [edge.source, edge.target]).filter(Boolean))]
  return names.map((name, index) => {
    const angle = names.length ? index * Math.PI * 2 / names.length - Math.PI / 2 : 0
    return { name, x: 180 + Math.cos(angle) * 125, y: 145 + Math.sin(angle) * 100 }
  })
})
const nodeMap = computed(() => new Map(relationNodes.value.map(node => [node.name, node])))
const visibleRelations = computed<any[]>(() => {
  const edges = analysis.value?.relationships || []
  return selectedCharacter.value ? edges.filter((edge: any) => edge.source === selectedCharacter.value || edge.target === selectedCharacter.value) : edges
})
function initRange() {
  if (!props.chapters.length) return
  const sorted = [...props.chapters].sort((a, b) => a - b)
  end.value = sorted.at(-1)!
  start.value = sorted[Math.max(0, sorted.length - 10)]!
}
async function loadCached() {
  if (!props.projectId || start.value < 1 || end.value < start.value) return
  loading.value = true
  try {
    const result = await api<{ exists: boolean; analysis?: Record<string, any> | null }>(`/api/projects/${props.projectId}/narrative-analysis?start=${start.value}&end=${end.value}`)
    analysis.value = result.analysis || null
    selectedChapter.value = analysis.value?.chapters?.[0]?.chapter || null
  } catch (error) { toast.error(error) }
  finally { loading.value = false }
}
async function extract() {
  if (extracting.value || !props.modelName) return
  if (start.value < 1 || end.value < start.value) { toast.error('请输入有效的起止章节'); return }
  if (end.value - start.value + 1 > 20) { toast.error('一次最多分析20章，请缩小范围'); return }
  extracting.value = true
  try {
    const started = await api<{ id: string }>(`/api/projects/${props.projectId}/narrative-analysis`, writeOptions('POST', { start: start.value, end: end.value, model_name: props.modelName }))
    emit('task', started.id)
    const poll = async () => {
      const job = await api<Job>(`/api/projects/${props.projectId}/jobs/${started.id}`)
      if (job.status === 'running') { setTimeout(poll, 1500); return }
      extracting.value = false
      const value = job.result?.analysis as Record<string, any> | undefined
      if (job.status !== 'completed' || !value) { toast.error(job.message || '章节分析失败'); return }
      analysis.value = value
      selectedChapter.value = value.chapters?.[0]?.chapter || null
      toast.success(`第 ${start.value}—${end.value} 章叙事分析已生成`)
    }
    void poll()
  } catch (error) { extracting.value = false; toast.error(error) }
}
function activeOn(line: any, chapter: number) { return (line.chapters || []).some((item: any) => item.chapter === chapter) }
function lineChange(line: any, chapter: number) { return (line.chapters || []).find((item: any) => item.chapter === chapter)?.change || '' }
function metricPoints(key: string) {
  if (!rows.value.length) return ''
  return rows.value.map((row, index) => `${rows.value.length === 1 ? 50 : index * 100 / (rows.value.length - 1)},${56 - Number(row[key] || 1) * 10}`).join(' ')
}
function toggleCharacter(name: string) { selectedCharacter.value = selectedCharacter.value === name ? '' : name }
watch(() => props.projectId, () => { analysis.value = null; initRange(); void loadCached() })
onMounted(() => { initRange(); void loadCached() })
</script>

<template><div class="analysis-page">
  <header class="analysis-hero"><div><span>CHAPTER EVIDENCE MAP</span><h1>先看已经写出了什么。</h1><p>选择一段已完成章节，提取实际推进的叙事线、人物关系与节奏变化。结果用于判断后续续写，不会修改正文或蓝图。</p></div>
    <form class="analysis-range" @submit.prevent="extract"><label>从第 <input v-model.number="start" type="number" min="1"> 章</label><i>至</i><label>第 <input v-model.number="end" type="number" min="1"> 章</label><button :disabled="extracting || loading || !modelName"><span v-if="extracting" class="button-spinner"></span>{{ extracting ? '正在提取' : '提取分析' }}</button><small>一次最多20章</small></form></header>
  <p v-if="loading" class="analysis-empty">正在读取已保存的分析…</p>
  <section v-else-if="!analysis" class="analysis-empty"><b>尚未分析这个章节范围</b><span>点击“提取分析”，系统会读取正文与摘要，生成独立的结构观察。</span></section>
  <template v-else>
    <section class="analysis-overview"><span>第 {{ analysis.range.start }}—{{ analysis.range.end }} 章</span><p>{{ analysis.overview }}</p></section>
    <section class="analysis-panel"><header><div><span>NARRATIVE THREADS</span><h2>叙事线图</h2></div><small>亮点表示该章让这条线发生了实际变化</small></header>
      <div class="thread-scroll"><div class="thread-map" :style="{ '--count': rows.length, gridTemplateColumns: `260px repeat(${rows.length}, minmax(68px, 1fr))`, minWidth: `max(100%, calc(260px + ${rows.length} * 68px))` }"><div class="thread-corner" style="position:sticky;left:0;z-index:4">叙事线</div><button v-for="row in rows" :key="`head-${row.chapter}`" :class="{ selected: selectedRow?.chapter === row.chapter }" @click="selectedChapter = row.chapter"><b>{{ row.chapter }}</b><small>{{ row.event_mode }}</small></button><template v-for="(line, lineIndex) in storylines" :key="line.id"><div class="thread-name" style="position:sticky;left:0;z-index:3;background:#f8fbf9"><i :style="{ background: palette[lineIndex % palette.length] }"></i><span><b :title="line.name" style="overflow:visible;white-space:normal;text-overflow:clip;line-height:1.35">{{ line.name }}</b><small>{{ line.kind }}</small></span></div><button v-for="row in rows" :key="`${line.id}-${row.chapter}`" class="thread-cell" :class="{ active: activeOn(line,row.chapter), selected: selectedRow?.chapter === row.chapter }" :style="{ '--line': palette[lineIndex % palette.length] }" :title="lineChange(line,row.chapter) || '本章没有明确推进'" @click="selectedChapter = row.chapter"><i></i></button></template></div></div>
      <article v-if="selectedRow" class="chapter-lens"><div><span>第 {{ selectedRow.chapter }} 章 · {{ selectedRow.event_mode }}</span><h3>{{ selectedRow.role }}</h3><p>{{ selectedRow.summary }}</p></div><dl><dt>转折</dt><dd>{{ selectedRow.turning_point || '本章没有明确转折' }}</dd><dt>压力</dt><dd>{{ selectedRow.tension }}/5</dd><dt>情感</dt><dd>{{ selectedRow.emotion }}/5</dd><dt>神秘</dt><dd>{{ selectedRow.mystery }}/5</dd></dl></article>
    </section>
    <div class="analysis-columns">
      <section class="analysis-panel relation-panel"><header><div><span>RELATIONSHIPS</span><h2>人物关系图</h2></div><small>点击人物聚焦其关系</small></header><svg viewBox="0 0 360 290" role="img" aria-label="人物关系变化图"><g v-for="(edge,index) in visibleRelations" :key="`${edge.source}-${edge.target}-${index}`"><line :x1="nodeMap.get(edge.source)?.x" :y1="nodeMap.get(edge.source)?.y" :x2="nodeMap.get(edge.target)?.x" :y2="nodeMap.get(edge.target)?.y"/><title>{{ edge.change }}</title></g><g v-for="node in relationNodes" :key="node.name" class="relation-node" :class="{ selected: selectedCharacter === node.name, muted: selectedCharacter && selectedCharacter !== node.name && !visibleRelations.some((edge:any) => edge.source === node.name || edge.target === node.name) }" @click="toggleCharacter(node.name)"><circle :cx="node.x" :cy="node.y" r="27"/><text :x="node.x" :y="node.y" text-anchor="middle" dominant-baseline="central">{{ node.name.length > 4 ? node.name.slice(0,4) : node.name }}</text></g></svg><ul><li v-for="(edge,index) in visibleRelations" :key="index"><b>{{ edge.source }} ↔ {{ edge.target }}</b><span>{{ edge.change }}</span><small>第 {{ edge.evidence_chapters?.join('、') }} 章</small></li></ul></section>
      <section class="analysis-panel pulse-panel"><header><div><span>RHYTHM</span><h2>节奏走势</h2></div></header><div class="pulse-legend"><i class="tension"></i>外部压力<i class="emotion"></i>情感强度<i class="mystery"></i>神秘感</div><svg viewBox="0 0 100 62" preserveAspectRatio="none"><line v-for="y in [6,16,26,36,46,56]" :key="y" x1="0" :y1="y" x2="100" :y2="y"/><polyline class="tension" :points="metricPoints('tension')"/><polyline class="emotion" :points="metricPoints('emotion')"/><polyline class="mystery" :points="metricPoints('mystery')"/></svg><div class="theme-card"><b>主题问题</b><p>{{ analysis.theme?.question || '尚未形成清晰主题问题' }}</p><small>{{ analysis.theme?.current_landing }}</small></div></section>
    </div>
    <section class="analysis-panel diagnosis-panel"><header><div><span>NEXT WRITING MOVE</span><h2>对后续续写的启示</h2></div><small>分析建议，不自动写入蓝图</small></header><div><article><b>已经有效</b><ul><li v-for="item in analysis.diagnosis?.strengths || []" :key="item">{{ item }}</li></ul></article><article class="warning"><b>目前失衡</b><ul><li v-for="item in analysis.diagnosis?.imbalances || []" :key="item">{{ item }}</li><li v-for="item in analysis.diagnosis?.stalled_patterns || []" :key="item">{{ item }}</li></ul></article><article class="priority"><b>续写优先处理</b><ol><li v-for="item in analysis.diagnosis?.continuation_priorities || []" :key="item">{{ item }}</li></ol></article></div></section>
  </template>
</div></template>

<style scoped>
.analysis-page{width:min(1180px,calc(100% - 44px));margin:0 auto;padding:44px 0 90px;color:#28443e}.analysis-hero{display:grid;grid-template-columns:1fr auto;gap:36px;align-items:end;margin-bottom:18px}.analysis-hero>div{max-width:720px}.analysis-hero span,.analysis-panel header span{color:#748d86;font:700 10px/1.4 Consolas,monospace;letter-spacing:.16em}.analysis-hero h1{margin:8px 0;color:#183f38;font:400 clamp(30px,4vw,48px)/1.15 "Noto Serif CJK SC","Songti SC",serif}.analysis-hero p{margin:0;color:#687f79;line-height:1.75}.analysis-range{display:grid;grid-template-columns:auto auto auto auto;gap:8px;align-items:center;padding:14px;border:1px solid #cadbd4;border-radius:8px;background:#fff}.analysis-range label{color:#58716a;font-size:12px}.analysis-range input{width:58px;border:0;border-bottom:1px solid #9fbab0;padding:6px;color:#214a41;font:700 15px Consolas,monospace}.analysis-range i{color:#9aaba5;font-style:normal}.analysis-range button{border:0;border-radius:5px;background:#2f705f;color:#fff;padding:10px 15px;font-weight:700}.analysis-range small{grid-column:1/-1;color:#8a9b95;text-align:right}.analysis-empty{display:grid;gap:8px;padding:70px;border:1px dashed #bdcec7;border-radius:9px;background:#f8fbf9;color:#789089;text-align:center}.analysis-overview{display:flex;gap:16px;margin-bottom:12px;padding:13px 18px;border-left:4px solid #b78c49;background:#fff9ed}.analysis-overview span{flex:none;color:#8b6b38;font-weight:700}.analysis-overview p{margin:0;line-height:1.65}.analysis-panel{margin-bottom:14px;padding:22px;border:1px solid #cfddd7;border-radius:9px;background:#fff}.analysis-panel>header{display:flex;align-items:end;justify-content:space-between;gap:15px;margin-bottom:17px}.analysis-panel h2{margin:3px 0 0;color:#214c43;font:500 24px/1.2 "Noto Serif CJK SC","Songti SC",serif}.analysis-panel header small{color:#82958f}.thread-scroll{overflow-x:auto;padding-bottom:8px}.thread-map{--count:8;display:grid;grid-template-columns:170px repeat(var(--count),minmax(68px,1fr));min-width:max(100%,calc(170px + var(--count) * 68px));overflow:hidden;border:1px solid #dbe5e1;border-radius:7px;background:#f8fbf9}.thread-map>button,.thread-corner{min-height:55px;border:0;border-bottom:1px solid #dfe7e3;background:#eef4f1;color:#48675f}.thread-corner{display:grid;align-content:center;padding:0 13px;font-weight:700}.thread-map>button{display:grid;place-content:center;gap:2px;border-left:1px solid #dfe7e3}.thread-map>button.selected{background:#dcebe4}.thread-map>button small{max-width:62px;overflow:hidden;color:#83958f;font-size:9px;text-overflow:ellipsis}.thread-name{display:flex;align-items:center;gap:9px;min-height:52px;padding:7px 12px;border-bottom:1px solid #e2e9e6}.thread-name i{width:6px;height:29px;border-radius:4px}.thread-name span{display:grid;gap:2px;min-width:0}.thread-name b{overflow:hidden;font-size:12px;text-overflow:ellipsis;white-space:nowrap}.thread-name small{color:#8b9c96;font-size:9px}.thread-map button.thread-cell{position:relative;min-height:52px;border-radius:0;background:linear-gradient(transparent calc(50% - 1px),#dce5e1 calc(50% - 1px),#dce5e1 calc(50% + 1px),transparent calc(50% + 1px))}.thread-cell i{position:absolute;left:50%;top:50%;width:7px;height:7px;border:2px solid #ccd8d3;border-radius:50%;background:#fff;transform:translate(-50%,-50%)}.thread-cell.active i{width:12px;height:12px;border:3px solid #fff;background:var(--line);box-shadow:0 0 0 2px var(--line)}.chapter-lens{display:grid;grid-template-columns:1.4fr .6fr;gap:20px;margin-top:12px;padding:16px 18px;border-radius:7px;background:#203f3a;color:#edf5f2}.chapter-lens span{color:#9ebbb1;font:700 10px Consolas,monospace}.chapter-lens h3{margin:5px 0;color:#fff}.chapter-lens p{margin:0;color:#cbdad5;line-height:1.6}.chapter-lens dl{display:grid;grid-template-columns:42px 1fr;gap:5px 9px;margin:0;font-size:11px}.chapter-lens dt{color:#91aea4}.chapter-lens dd{margin:0}.analysis-columns{display:grid;grid-template-columns:1.15fr .85fr;gap:14px}.relation-panel svg{width:100%;height:290px;border-radius:7px;background:#f6faf8}.relation-panel line{stroke:#9db4ab;stroke-width:1.5}.relation-node{cursor:pointer}.relation-node circle{fill:#fff;stroke:#397262;stroke-width:2}.relation-node text{fill:#345c52;font-size:10px}.relation-node.selected circle{fill:#2f705f}.relation-node.selected text{fill:#fff}.relation-node.muted{opacity:.25}.relation-panel ul{display:grid;gap:7px;margin:10px 0 0;padding:0;list-style:none}.relation-panel li{display:grid;grid-template-columns:auto 1fr auto;gap:10px;padding:8px 10px;background:#f6faf8;font-size:10px}.relation-panel li span{color:#60766f}.relation-panel li small{color:#899a94}.pulse-legend{display:flex;align-items:center;gap:5px;color:#778c85;font-size:10px}.pulse-legend i{width:17px;height:2px;margin-left:7px}.tension{stroke:#a95443}.emotion{stroke:#ad657a}.mystery{stroke:#557a9d}.pulse-legend .tension{background:#a95443}.pulse-legend .emotion{background:#ad657a}.pulse-legend .mystery{background:#557a9d}.pulse-panel>svg{width:100%;height:150px;margin:12px 0}.pulse-panel svg line{stroke:#e4ebe8;stroke-width:.45}.pulse-panel polyline{fill:none;stroke-width:2;vector-effect:non-scaling-stroke;stroke-linecap:round;stroke-linejoin:round}.theme-card{padding:15px;border-left:3px solid #a08645;background:#fff9ed}.theme-card p{margin:6px 0;color:#574f3a}.theme-card small{color:#897e63;line-height:1.5}.diagnosis-panel>div{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.diagnosis-panel article{padding:15px;border-top:3px solid #6d9b8b;background:#f7faf8}.diagnosis-panel article.warning{border-color:#b57656;background:#fff8f3}.diagnosis-panel article.priority{border-color:#9a8141;background:#fffaf0}.diagnosis-panel ul,.diagnosis-panel ol{margin:9px 0 0;padding-left:18px;color:#5e736d;font-size:11px;line-height:1.65}.diagnosis-panel li+li{margin-top:5px}@media(max-width:900px){.analysis-hero,.analysis-columns{grid-template-columns:1fr}.analysis-range{justify-self:start}.diagnosis-panel>div{grid-template-columns:1fr}}@media(max-width:600px){.analysis-page{width:calc(100% - 24px);padding-top:24px}.analysis-range{grid-template-columns:1fr 20px 1fr}.analysis-range button{grid-column:1/-1}.chapter-lens{grid-template-columns:1fr}.analysis-panel{padding:16px}.relation-panel li{grid-template-columns:1fr}}
</style>
