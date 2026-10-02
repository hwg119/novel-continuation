<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api, writeOptions, type Job } from './api'
import { askConfirm } from './confirmService'

type Fact = { subject: string; predicate: string; value: string; quote: string; chapter: number; kind: string; story_time?: string }
type Subject = { subject: string; facts: Fact[] }
type Relation = { source: string; target: string; predicate: string; value?: string; story_time?: string; chapter: number; quote: string }
type Entity = { subject: string; facts_count: number; stable: Fact[]; timeline: Fact[]; relationships: Relation[] }
type ClaimStatus = 'supported' | 'unsupported' | 'uncertain' | 'unverified'
type RecordTab = 'stable' | 'timeline' | 'relationships'
type ProfileClaim = { section: string; text: string; status?: ClaimStatus; review_note?: string; sources: { id: string; chapter: number; quote: string }[] }
type Profile = { subject: string; evidence_count: number; claims: ProfileClaim[]; note: string; verification_version?: number; evidence_expansion_version?: number; verification_counts?: Record<string, number> }
type Pending = { chapter: number; reason: 'new' | 'changed' | 'upgrade' | 'sensitive' }
type ReviewRecord = { id: string; status: 'kept' | 'corrected' | 'rejected' | 'uncertain'; chapter: number; subject: string; predicate: string; value: string; quote: string; reason: string; corrected?: Partial<Fact>; reviewed_at: string }
type ReviewState = { counts: Record<'pending' | 'kept' | 'corrected' | 'rejected' | 'uncertain', number>; pending: { id: string; chapter: number; subject: string; predicate: string; value: string; risk_score: number; risk_reasons: string[] }[]; recent: ReviewRecord[]; total_high_risk: number }
const props = defineProps<{ projectId: string; modelName: string; models: string[]; currentChapter: number | null; focusSubject?: string }>()
const emit = defineEmits<{ task: [id: string]; openChapter: [number: number]; updated: [count: number]; openGraph: [] }>()
const subjects = ref<Subject[]>([])
const pending = ref<Pending[]>([])
const query = ref('')
const selected = ref('')
const entity = ref<Entity | null>(null)
const profile = ref<Profile | null>(null)
const profileBusy = ref(false)
const profileFilter = ref<ClaimStatus>('supported')
const recordTab = ref<RecordTab>('stable')
const relationPeer = ref('')
const asOf = ref<number | null>(null)
const start = ref(1)
const end = ref(100)
const busy = ref(false)
const feedback = ref('')
const error = ref('')
const retryModel = ref('')
const reviewModel = ref('')
const reviewBusy = ref(false)
const reviewOpen = ref(false)
const reviewState = ref<ReviewState>({ counts: { pending: 0, kept: 0, corrected: 0, rejected: 0, uncertain: 0 }, pending: [], recent: [], total_high_risk: 0 })
let entityRequest = 0

const graphNodes = computed(() => {
  if (!entity.value) return []
  const names = [...new Set(entity.value.relationships.map(r => r.source === selected.value ? r.target : r.source))].slice(0, 8)
  return names.map((name, index) => ({ name, x: 310 + 210 * Math.cos(index * 2 * Math.PI / names.length - Math.PI / 2), y: 168 + 110 * Math.sin(index * 2 * Math.PI / names.length - Math.PI / 2) }))
})
const profileCounts = computed(() => {
  const counts: Record<ClaimStatus, number> = { supported: 0, unsupported: 0, uncertain: 0, unverified: 0 }
  for (const claim of profile.value?.claims || []) counts[claim.status || 'unverified']++
  return counts
})
const visibleClaims = computed(() => (profile.value?.claims || []).filter(claim => (claim.status || 'unverified') === profileFilter.value))
const visibleRelations = computed(() => {
  if (!relationPeer.value) return entity.value?.relationships || []
  return (entity.value?.relationships || []).filter(relation =>
    (relation.source === selected.value && relation.target === relationPeer.value) ||
    (relation.target === selected.value && relation.source === relationPeer.value))
})
function toggleRelationPeer(name: string) {
  relationPeer.value = relationPeer.value === name ? '' : name
}
function relationLabel(relation: Relation) {
  if (relation.predicate === '称呼') return `称呼：“${relation.value || '未记录'}”`
  if (relation.value && relation.value !== relation.target) return `${relation.predicate} · ${relation.value}`
  return relation.predicate
}
async function load() {
  if (!props.projectId) return
  try {
    subjects.value = await api<Subject[]>(`/api/projects/${props.projectId}/wiki?query=${encodeURIComponent(query.value)}`)
    if (props.focusSubject && subjects.value.some(item => item.subject === props.focusSubject)) selected.value = props.focusSubject
    if (!subjects.value.some(item => item.subject === selected.value)) selected.value = subjects.value[0]?.subject || ''
  } catch (cause) { error.value = String(cause) }
}
async function loadEntity() {
  const request = ++entityRequest
  entity.value = null
  if (!props.projectId || !selected.value) return
  try {
    const before = asOf.value && asOf.value > 0 ? `?before_chapter=${asOf.value + 1}` : ''
    const result = await api<Entity>(`/api/projects/${props.projectId}/wiki/subjects/${encodeURIComponent(selected.value)}${before}`)
    if (request === entityRequest) entity.value = result
  } catch (cause) { if (request === entityRequest) error.value = String(cause) }
}
async function loadProfile() {
  const request = entityRequest
  profile.value = null
  if (!props.projectId || !selected.value) return
  try {
    const before = asOf.value && asOf.value > 0 ? `?before_chapter=${asOf.value + 1}` : ''
    const result = await api<{ profile: Profile | null }>(`/api/projects/${props.projectId}/wiki/subjects/${encodeURIComponent(selected.value)}/profile${before}`)
    if (request === entityRequest) {
      profile.value = result.profile
      profileFilter.value = result.profile?.verification_version ? 'supported' : 'unverified'
    }
  } catch (cause) { if (request === entityRequest) error.value = String(cause) }
}
async function generateProfile(mode: 'generate' | 'verify' | 'expand' = 'generate') {
  if (!props.projectId || !selected.value || !props.modelName || profileBusy.value) return
  profileBusy.value = true; error.value = ''
  const project = props.projectId
  const subject = selected.value
  const before = asOf.value && asOf.value > 0 ? asOf.value + 1 : null
  try {
    const started = await api<{ id: string }>(`/api/projects/${project}/wiki/subjects/${encodeURIComponent(subject)}/profile`,
      writeOptions('POST', { model_name: props.modelName, before_chapter: before,
        force: mode === 'generate' && !!profile.value, verify_only: mode === 'verify', expand_only: mode === 'expand' }))
    emit('task', started.id)
    const poll = async () => {
      try {
        const job = await api<Job>(`/api/projects/${project}/jobs/${started.id}`)
        if (job.status === 'running') { setTimeout(poll, 2500); return }
        profileBusy.value = false
        if (job.status !== 'completed') { error.value = job.message; return }
        feedback.value = `${subject}的 AI 档案${mode === 'expand' ? '补证' : mode === 'verify' ? '复核' : '生成并复核'}完成。`
        if (project === props.projectId && subject === selected.value) await loadProfile()
      } catch (cause) { profileBusy.value = false; error.value = String(cause) }
    }
    void poll()
  } catch (cause) { profileBusy.value = false; error.value = String(cause) }
}
async function loadPending() {
  if (!props.projectId) { pending.value = []; return }
  try {
    const result = await api<{ pending: Pending[]; count: number }>(`/api/projects/${props.projectId}/wiki/pending`)
    pending.value = result.pending; emit('updated', result.count)
  } catch (cause) { error.value = String(cause) }
}
async function loadReview() {
  if (!props.projectId) return
  try { reviewState.value = await api<ReviewState>(`/api/projects/${props.projectId}/wiki/review`) }
  catch (cause) { error.value = String(cause) }
}
async function startReview() {
  const model = reviewModel.value || props.modelName
  const count = reviewState.value.counts.pending
  if (!props.projectId || !model || !count || reviewBusy.value) return
  if (!await askConfirm({ title: `复核 ${count} 条高风险事实？`,
    message: '模型只读取每条事实附近的局部原文，不会重新读取整章。可靠修正会自动应用，不确定项将退出续写上下文。',
    symbol: '核', confirmLabel: '开始智能复核', modelName: model })) return
  reviewBusy.value = true; error.value = ''
  const project = props.projectId
  try {
    const started = await api<{ id: string }>(`/api/projects/${project}/wiki/review`, writeOptions('POST', { model_name: model }))
    emit('task', started.id); feedback.value = `正在用「${model}」复核局部证据；可在底部控制台查看进度。`
    const poll = async () => {
      try {
        const job = await api<Job>(`/api/projects/${project}/jobs/${started.id}`)
        if (job.status === 'running') { setTimeout(poll, 2500); return }
        reviewBusy.value = false
        if (job.status !== 'completed') { error.value = job.message; return }
        const failed = Number(job.result?.failed_items || 0)
        feedback.value = `智能复核完成：保留 ${job.result?.kept || 0}，修正 ${job.result?.corrected || 0}，移除 ${job.result?.rejected || 0}，不确定 ${job.result?.uncertain || 0}。${failed ? `另有 ${failed} 条因模型输出无效保留为待复核，可再次运行。` : ''}`
        if (project === props.projectId) { await Promise.all([loadReview(), load()]); await loadEntity() }
      } catch (cause) { reviewBusy.value = false; error.value = String(cause) }
    }
    void poll()
  } catch (cause) { reviewBusy.value = false; error.value = String(cause) }
}
async function build(onlyPending = false) {
  if (!props.projectId || !props.modelName || (onlyPending && !pending.value.length)) return
  const count = onlyPending ? pending.value.length : end.value - start.value + 1
  if (count > 10 && !await askConfirm({ title: `编纂 ${count} 章 Wiki？`,
    message: '该任务将逐章调用模型，耗时和调用成本会随章节数量增加。', symbol: '辑',
    confirmLabel: '开始批量编纂', modelName: props.modelName })) return
  busy.value = true; error.value = ''
  const project = props.projectId
  try {
    const started = await api<{ id: string }>(`/api/projects/${project}/wiki${onlyPending ? '/pending' : ''}`,
      writeOptions('POST', onlyPending ? { model_name: props.modelName } : { start: start.value, end: end.value, model_name: props.modelName }))
    emit('task', started.id)
    feedback.value = '模型正在逐章编纂；可在底部控制台查看进度。'
    const poll = async () => {
      try {
        const job = await api<Job>(`/api/projects/${project}/jobs/${started.id}`)
        if (job.status === 'running') { setTimeout(poll, 2500); return }
        busy.value = false
        if (job.status !== 'completed') { error.value = job.message; return }
        const blocked = Array.isArray(job.result?.skipped_sensitive) ? job.result.skipped_sensitive.length : 0
        feedback.value = `Wiki 已更新：${job.result?.chapters || 0} 章、${job.result?.facts || 0} 条可溯源事实。${blocked ? `另有 ${blocked} 章因模型内容策略受限，未重复请求。` : ''}`
        if (project === props.projectId) { await Promise.all([load(), loadPending()]); await loadEntity(); await loadProfile() }
      } catch (cause) { busy.value = false; error.value = String(cause) }
    }
    void poll()
  } catch (cause) { busy.value = false; error.value = String(cause) }
}
async function retrySensitive(chapter: number) {
  const model = retryModel.value || props.modelName
  if (!props.projectId || !model || busy.value) return
  busy.value = true; error.value = ''
  const project = props.projectId
  try {
    const started = await api<{ id: string }>(`/api/projects/${project}/wiki/chapters/${chapter}/retry`,
      writeOptions('POST', { model_name: model }))
    emit('task', started.id)
    const poll = async () => {
      try {
        const job = await api<Job>(`/api/projects/${project}/jobs/${started.id}`)
        if (job.status === 'running') { setTimeout(poll, 2500); return }
        busy.value = false
        if (job.status !== 'completed') { error.value = job.message; return }
        feedback.value = job.result?.status === 'blocked_sensitive'
          ? `第 ${chapter} 章仍被该模型的内容策略拦截；请换用其他模型。`
          : `第 ${chapter} 章已用「${model}」完成编纂。`
        if (project === props.projectId) { await Promise.all([load(), loadPending()]); await loadEntity(); await loadProfile() }
      } catch (cause) { busy.value = false; error.value = String(cause) }
    }
    void poll()
  } catch (cause) { busy.value = false; error.value = String(cause) }
}
watch(() => props.projectId, () => { selected.value = ''; entity.value = null; retryModel.value = props.modelName; reviewModel.value = props.modelName; void Promise.all([load(), loadPending(), loadReview()]) })
watch([selected, asOf], () => { relationPeer.value = ''; void loadEntity(); void loadProfile() })
onMounted(() => { if (props.currentChapter) end.value = props.currentChapter; reviewModel.value = props.modelName; void Promise.all([load(), loadPending(), loadReview()]) })
</script>

<template><div class="feature-page wiki-page"><div class="feature-head"><span class="eyebrow">小说 Wiki · 原文可追溯</span><h1>一个人物，不止一条事实。</h1><p>按章查看经历与状态；关系图只展示有原文引句的关系。</p></div>
  <p v-if="error" class="notice error" role="alert">{{ error }}</p><p v-if="feedback" class="notice success">{{ feedback }}</p>
  <section class="panel wiki-pending"><div class="wiki-pending-head"><div><span class="eyebrow">增量更新</span><h2>{{ pending.length ? `${pending.length} 章待更新` : 'Wiki 已是最新' }}</h2><p>{{ pending.length ? '新增、正文变更或旧版缓存需升级；内容受限章节将跳过，避免整批中断。' : '当前各章正文与 Wiki 缓存一致。' }}</p></div><button :disabled="busy || !pending.length || !projectId || !modelName" @click="build(true)">{{ busy ? '更新中…' : '一键更新待处理章节' }}</button></div><div v-if="pending.length" class="wiki-pending-list"><span v-for="item in pending.filter(item => item.reason !== 'sensitive').slice(0, 18)" :key="item.chapter">第 {{ item.chapter }} 章 · {{ item.reason === 'new' ? '新增' : item.reason === 'upgrade' ? '需升级' : '正文已改' }}</span><em v-if="pending.filter(item => item.reason !== 'sensitive').length > 18">另有 {{ pending.filter(item => item.reason !== 'sensitive').length - 18 }} 章</em></div><div v-if="pending.some(item => item.reason === 'sensitive')" class="wiki-sensitive"><div><span class="eyebrow">内容受限</span><strong>这些章节的原文被当前模型服务拦截。</strong><p>已保留此前可核对的片段事实；请选替代模型单章重试。</p></div><label>替代模型<select v-model="retryModel"><option value="">当前模型（{{ modelName }}）</option><option v-for="name in models.filter(name => name !== modelName)" :key="name" :value="name">{{ name }}</option></select></label><div class="wiki-sensitive-actions"><button v-for="item in pending.filter(item => item.reason === 'sensitive')" :key="item.chapter" class="secondary" :disabled="busy || !retryModel && !modelName" @click="retrySensitive(item.chapter)">重试第 {{ item.chapter }} 章</button></div></div></section>
  <section class="panel wiki-review"><div class="wiki-review-head"><div><span class="eyebrow">局部证据 · 第二道校验</span><h2>智能复核</h2><p>只把高风险事实及前后约 350 字交给复核模型，不重读整章。</p></div><div class="wiki-review-score"><strong>{{ reviewState.counts.pending }}</strong><span>条待复核</span></div></div>
    <div class="wiki-review-ledger"><div><span>已保留</span><strong>{{ reviewState.counts.kept }}</strong></div><div><span>已修正</span><strong>{{ reviewState.counts.corrected }}</strong></div><div><span>已移除</span><strong>{{ reviewState.counts.rejected }}</strong></div><div><span>不确定</span><strong>{{ reviewState.counts.uncertain }}</strong></div></div>
    <div class="wiki-review-actions"><label>复核模型<select v-model="reviewModel"><option v-for="name in models" :key="name" :value="name">{{ name }}</option></select></label><button :disabled="reviewBusy || !reviewState.counts.pending || !reviewModel" @click="startReview">{{ reviewBusy ? '复核中…' : '开始二次复核' }}</button><button class="secondary" :disabled="!reviewState.recent.length" @click="reviewOpen = !reviewOpen">{{ reviewOpen ? '收起记录' : '查看复核记录' }}</button></div>
    <p v-if="reviewState.counts.pending" class="muted-note">待处理主要来自人物关系、稳定设定、重要状态变化和证据主体不明确的记录。</p><p v-else class="muted-note">当前没有尚未复核的高风险事实。</p>
    <div v-if="reviewOpen" class="wiki-review-history"><article v-for="item in reviewState.recent" :key="item.id" :class="`review-${item.status}`"><div><span>第 {{ item.chapter }} 章</span><b>{{ item.status === 'kept' ? '保留' : item.status === 'corrected' ? '已修正' : item.status === 'rejected' ? '已移除' : '不确定' }}</b></div><strong>{{ item.subject }} · {{ item.predicate }} · {{ item.value }}</strong><p>{{ item.reason }}</p><blockquote>{{ item.quote }}</blockquote></article></div>
  </section>
  <section class="panel"><h2>自动编纂</h2><div class="inline-fields"><label>起始章<input v-model.number="start" type="number" min="1"></label><label>结束章<input v-model.number="end" type="number" min="1"></label><button :disabled="busy || !projectId || !modelName" @click="build(false)">{{ busy ? '编纂中…' : '按范围编纂 Wiki' }}</button></div><p class="muted-note">仅正文未变且已是新版的章节使用缓存。可先选少量章节升级，核对效果后再扩展范围。</p></section>
  <section class="panel wiki-library"><div class="wiki-library-tools"><label>查找人物、地点或物品<input v-model="query" placeholder="输入实体名称" @input="load"></label><label>只看截至第几章<input v-model.number="asOf" type="number" min="1" placeholder="全部章节"></label></div><div class="wiki-grid"><nav class="wiki-index" aria-label="Wiki 条目"><button v-for="item in subjects" :key="item.subject" :class="{ chosen: selected === item.subject }" @click="selected = item.subject">{{ item.subject }} <small>{{ item.facts.length }}</small></button><p v-if="!subjects.length">尚无 Wiki 内容。先选择章节范围开始编纂。</p></nav>
    <div v-if="entity" class="wiki-entity"><div class="wiki-entity-head"><span class="eyebrow">人物与世界档案</span><h2>{{ entity.subject }}</h2><p>{{ entity.facts_count }} 条可核对记录{{ asOf ? ` · 截至第 ${asOf} 章` : '' }}</p></div><aside class="wiki-evidence-note"><strong>这里记录原文曾经写过什么</strong><span>状态和关系可能被后文改变；判断续写时的当前情况，应以最近章节为准。</span></aside>
    <section class="wiki-profile">
      <div class="wiki-profile-head">
        <div><span class="eyebrow">AI 归纳 · 原文复核</span><h3>人物档案</h3></div>
        <div class="wiki-profile-actions">
          <button v-if="profile?.verification_version === 2 && profileCounts.uncertain" :disabled="profileBusy || !modelName" class="secondary" @click="generateProfile('expand')">补证待核实</button>
          <button v-if="profile" :disabled="profileBusy || !modelName" class="secondary" @click="generateProfile('verify')">{{ profile.verification_version ? '重新复核' : '复核现有档案' }}</button>
          <button :disabled="profileBusy || !modelName || !entity.facts_count" @click="generateProfile('generate')">{{ profileBusy ? '处理中…' : profile ? '重新生成档案' : '生成档案并复核' }}</button>
        </div>
      </div>
      <p class="muted-note">{{ profile ? `${profile.evidence_count} 条原文事实参与编纂。${profile.note}` : '尚未生成档案；模型将按原文证据分批整理并独立复核，不修改下方事实。' }}</p>
      <p v-if="profile?.verification_version === 2 && profileCounts.uncertain" class="muted-note">补证只复查待核实要点及其引用章节的较完整原文，不重建 Wiki 事实。</p>
      <template v-if="profile">
        <div class="wiki-profile-filters" aria-label="档案复核状态"><button v-for="option in ([['supported', '原文支持'], ['uncertain', '待核实'], ['unsupported', '未通过'], ['unverified', '尚未复核']] as const)" :key="option[0]" :class="{ active: profileFilter === option[0] }" @click="profileFilter = option[0]">{{ option[1] }} <span>{{ profileCounts[option[0]] }}</span></button></div>
        <div class="wiki-profile-list"><article v-for="(claim, index) in visibleClaims" :key="index" :class="`wiki-claim-${claim.status || 'unverified'}`"><span class="eyebrow">{{ claim.section }} · {{ claim.review_note || '尚未复核' }}</span><p>{{ claim.text }}</p><div class="wiki-profile-sources"><button v-for="source in claim.sources" :key="source.id" class="text-button" :title="source.quote" @click="emit('openChapter', source.chapter)">第 {{ source.chapter }} 章 · {{ source.quote }} ↗</button></div></article><p v-if="!visibleClaims.length" class="wiki-empty">此类陈述暂无记录。</p></div>
      </template>
    </section>
    <nav class="wiki-record-tabs" role="tablist" aria-label="原文记录分类">
      <button role="tab" :aria-selected="recordTab === 'stable'" :class="{ active: recordTab === 'stable' }" @click="recordTab = 'stable'">较稳定原文记录 <span>{{ entity.stable.length }}</span></button>
      <button role="tab" :aria-selected="recordTab === 'timeline'" :class="{ active: recordTab === 'timeline' }" @click="recordTab = 'timeline'">经历与状态时间线 <span>{{ entity.timeline.length }}</span></button>
      <button role="tab" :aria-selected="recordTab === 'relationships'" :class="{ active: recordTab === 'relationships' }" @click="recordTab = 'relationships'">人物关系原文记录 <span>{{ entity.relationships.length }}</span></button>
    </nav>
    <section v-if="recordTab === 'stable'" class="wiki-record-panel" role="tabpanel">
      <h3>较稳定原文记录</h3><p class="wiki-section-note">若与较晚章节冲突，以较晚记录为准。</p><p v-if="!entity.stable.length" class="wiki-empty">暂无提取记录。</p>
      <div class="wiki-record-list"><article v-for="(fact, index) in entity.stable" :key="`s${index}`" class="wiki-fact"><div class="wiki-fact-meta"><span>第 {{ fact.chapter }} 章</span><span>较稳定记录 · 较晚正文优先</span><span v-if="fact.story_time">{{ fact.story_time }}</span></div><strong>{{ fact.predicate }} · {{ fact.value }}</strong><blockquote>{{ fact.quote }}</blockquote><button class="text-button" @click="emit('openChapter', fact.chapter)">核对第 {{ fact.chapter }} 章原文 ↗</button></article></div>
    </section>
    <section v-else-if="recordTab === 'timeline'" class="wiki-record-panel" role="tabpanel">
      <h3>经历与状态时间线</h3><p class="wiki-section-note">按原文出现章节记录，不自动推断当前状态。</p><p v-if="!entity.timeline.length" class="wiki-empty">暂无提取记录。</p>
      <article v-for="(fact, index) in entity.timeline" :key="`t${index}`" class="wiki-fact wiki-timeline"><div class="wiki-fact-meta"><span>第 {{ fact.chapter }} 章</span><span>{{ fact.kind === 'state' ? '当时状态 · 当前是否仍成立未知' : '已发生事件 · 不代表当前状态' }}</span><span v-if="fact.story_time">{{ fact.story_time }}</span></div><strong>{{ fact.predicate }} · {{ fact.value }}</strong><blockquote>{{ fact.quote }}</blockquote><button class="text-button" @click="emit('openChapter', fact.chapter)">核对第 {{ fact.chapter }} 章原文 ↗</button></article>
    </section>
    <section v-else class="wiki-record-panel wiki-relationships" role="tabpanel">
      <div class="wiki-relationship-title"><div><h3>人物关系原文记录</h3><p class="wiki-section-note">此处为当前人物概览；完整关系谱不会省略节点。</p></div><button class="secondary" @click="emit('openGraph')">打开完整关系图 ↗</button></div><p v-if="!entity.relationships.length" class="wiki-empty">暂无可验证的关系。旧 Wiki 缓存需升级后才会提取关系对象。</p>
      <template v-else>
        <div class="wiki-graph-wrap"><svg class="wiki-graph" viewBox="0 0 620 336" role="img" :aria-label="`${entity.subject}的人物关系图`">
          <g v-for="node in graphNodes" :key="`line-${node.name}`" class="wiki-graph-edge" :class="{ active: relationPeer === node.name, muted: relationPeer && relationPeer !== node.name }" tabindex="0" role="button" :aria-pressed="relationPeer === node.name" :aria-label="`只查看${entity.subject}与${node.name}之间的关系`" @click.stop="toggleRelationPeer(node.name)" @keydown.enter.stop="toggleRelationPeer(node.name)" @keydown.space.prevent.stop="toggleRelationPeer(node.name)"><line x1="310" y1="168" :x2="node.x" :y2="node.y" class="wiki-graph-hit"/><line x1="310" y1="168" :x2="node.x" :y2="node.y" class="wiki-graph-line"/></g>
          <g v-for="node in graphNodes" :key="node.name" class="wiki-graph-node" tabindex="0" role="button" :aria-label="`查看${node.name}的人物档案`" @click="selected = node.name" @keydown.enter="selected = node.name"><circle :cx="node.x" :cy="node.y" r="38"/><text :x="node.x" :y="node.y" text-anchor="middle" dominant-baseline="central">{{ node.name.length > 5 ? node.name.slice(0, 5) + '…' : node.name }}</text></g>
          <circle cx="310" cy="168" r="53" class="wiki-graph-center"/><text x="310" y="168" text-anchor="middle" dominant-baseline="central" class="wiki-graph-center-label">{{ entity.subject.length > 6 ? entity.subject.slice(0, 6) + '…' : entity.subject }}</text>
        </svg></div>
        <div v-if="relationPeer" class="wiki-relation-filter"><span>仅显示 {{ entity.subject }} ↔ {{ relationPeer }}</span><button class="text-button" @click="relationPeer = ''">查看全部关系</button></div>
        <div class="wiki-relation-list"><article v-for="(relation, index) in visibleRelations" :key="index"><div class="wiki-fact-meta"><span>第 {{ relation.chapter }} 章</span><span>关系记录 · 后文可能变化</span><span v-if="relation.story_time">{{ relation.story_time }}</span></div><strong>{{ relation.source }} → {{ relation.target }}</strong><span class="wiki-relation-label">{{ relationLabel(relation) }}</span><blockquote>{{ relation.quote }}</blockquote><button class="text-button" @click="emit('openChapter', relation.chapter)">核对第 {{ relation.chapter }} 章原文 ↗</button></article></div>
        <p v-if="relationPeer && !visibleRelations.length" class="wiki-empty">这两个人物之间暂无可显示的关系记录。</p>
      </template>
    </section>
    </div><div v-else class="wiki-entity wiki-empty">选择左侧条目，查看档案和关系。</div></div></section>
</div></template>
