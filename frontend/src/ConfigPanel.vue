<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, writeOptions, type Job } from './api'
import { askConfirm } from './confirmService'

type Model = Record<string, any>
type Config = { llm_configs: Record<string, Model>; embedding_configs: Record<string, Model>;
  default_llm_config_name: string; last_llm_config_name: string; last_embedding_config_name: string; workspace_root: string;
  proxy_setting: { enabled: boolean; proxy_url: string; proxy_port: string } }
const emit = defineEmits<{ changed: [] }>()
const config = ref<Config | null>(null)
const group = ref<'llm_configs' | 'embedding_configs'>('llm_configs')
const selected = ref('')
const mode = ref<'edit' | 'new'>('edit')
const draft = ref<Model>({})
const feedback = ref('')
const error = ref('')
const newName = ref('')
const editName = ref('')
const workspace = ref('')
const defaultModel = ref('')
const currentEmbedding = ref('')
const proxy = ref({ enabled: false, proxy_url: '127.0.0.1', proxy_port: '' })
const testing = ref(false)
const names = computed(() => Object.keys(config.value?.[group.value] || {}))
const groupLabel = computed(() => group.value === 'llm_configs' ? '大模型' : '向量模型')
const interfaceOptions = computed(() => group.value === 'llm_configs'
  ? ['OpenAI', 'DeepSeek', 'Ollama', 'Azure OpenAI', 'Azure AI', 'ML Studio', 'Gemini',
      '阿里云百炼', '火山引擎', '硅基流动', 'Grok']
  : ['OpenAI', 'Azure OpenAI', 'Ollama', 'ML Studio', 'Gemini', 'SiliconFlow',
      '本地bge', '离线哈希'])
const targetName = computed(() => mode.value === 'new' ? newName.value.trim() : editName.value.trim())

function select(name: string) { mode.value = 'edit'; selected.value = name; editName.value = name; draft.value = { ...(config.value?.[group.value]?.[name] || {}) }; draft.value.api_key = '' }
async function load() {
  try { config.value = await api<Config>('/api/config');
    const preferred = group.value === 'llm_configs' ? config.value.last_llm_config_name : config.value.last_embedding_config_name
    select(config.value[group.value][preferred] ? preferred : Object.keys(config.value[group.value])[0] || '')
    workspace.value = config.value.workspace_root || ''; defaultModel.value = config.value.default_llm_config_name || config.value.last_llm_config_name || Object.keys(config.value.llm_configs)[0] || ''; currentEmbedding.value = config.value.last_embedding_config_name || Object.keys(config.value.embedding_configs)[0] || ''; proxy.value = { ...proxy.value, ...(config.value.proxy_setting || {}) } }
  catch (cause) { error.value = String(cause) }
}
function chooseGroup(value: 'llm_configs' | 'embedding_configs') {
  group.value = value; newName.value = ''; if (!config.value) return
  const preferred = value === 'llm_configs' ? config.value.last_llm_config_name : config.value.last_embedding_config_name
  select(config.value[value][preferred] ? preferred : Object.keys(config.value[value])[0] || '')
}
async function save() {
  const name = targetName.value
  if (!name) { error.value = '请填写配置名称。'; return }
  if ((mode.value === 'new' || name !== selected.value) && config.value?.[group.value]?.[name]) { error.value = '配置名称已存在；请换一个名称。'; return }
  try {
    await api(`/api/config/${group.value}`, writeOptions('PUT', {
      name, values: draft.value, original_name: mode.value === 'edit' ? selected.value : '' }))
    feedback.value = mode.value === 'new' ? `已新增${groupLabel.value}配置“${name}”。` : name !== selected.value ? `配置已重命名为“${name}”并保存。` : `已保存配置“${name}”。`
    error.value = ''; await load(); emit('changed') }
  catch (cause) { error.value = String(cause) }
}
function add() { mode.value = 'new'; selected.value = ''; newName.value = ''; draft.value = {
  interface_format: group.value === 'llm_configs' ? 'OpenAI' : 'Ollama',
  base_url: '', model_name: '', api_key: '', temperature: 0.8, max_tokens: 8192, timeout: 600,
}; feedback.value = ''; error.value = '' }
async function remove() {
  if (mode.value !== 'edit' || !selected.value || !await askConfirm({
    title: `删除${groupLabel.value}配置？`, message: `配置“${selected.value}”将从全局配置中移除。`,
    symbol: '删', confirmLabel: '删除此配置' })) return
  try { await api(`/api/config/${group.value}/${encodeURIComponent(selected.value)}`, writeOptions('DELETE'))
    feedback.value = `配置“${selected.value}”已删除。`; error.value = ''; await load(); emit('changed') }
  catch (cause) { error.value = String(cause) }
}
async function move(name: string, offset: number) {
  if (!config.value || group.value !== 'llm_configs') return
  const ordered = [...names.value]
  const index = ordered.indexOf(name); const target = index + offset
  if (index < 0 || target < 0 || target >= ordered.length) return
  ;[ordered[index], ordered[target]] = [ordered[target], ordered[index]]
  try {
    await api(`/api/config/${group.value}/order`, writeOptions('PUT', { names: ordered }))
    const existing = config.value[group.value]
    config.value[group.value] = Object.fromEntries(ordered.map(item => [item, existing[item]]))
    feedback.value = '大模型配置顺序已保存。'; error.value = ''; emit('changed')
  } catch (cause) { error.value = String(cause) }
}
async function test() {
  const name = targetName.value
  if (!name) { error.value = '请先填写配置名称。'; return }
  if ((mode.value === 'new' || name !== selected.value) && config.value?.[group.value]?.[name]) { error.value = '配置名称已存在；请换一个名称。'; return }
  testing.value = true; error.value = ''; feedback.value = '正在保存并测试模型连接……'
  try {
    await api(`/api/config/${group.value}`, writeOptions('PUT', {
      name, values: draft.value, original_name: mode.value === 'edit' ? selected.value : '' }))
    const started = await api<{ id: string }>(`/api/config/${group.value}/${encodeURIComponent(name)}/test`, writeOptions('POST'))
    const poll = async () => {
      const job = await api<Job>(`/api/config/jobs/${started.id}`)
      if (job.status === 'running') { setTimeout(poll, 1200); return }
      testing.value = false
      if (job.status !== 'completed') { error.value = `配置已保存，但连接测试失败：${job.message}`; await load(); emit('changed'); return }
      feedback.value = `配置已保存，连接成功：${JSON.stringify(job.result || {})}`
      await load(); emit('changed')
    }
    void poll()
  } catch (cause) { testing.value = false; error.value = String(cause) }
}
async function saveMisc() {
  try {
    await api('/api/config/misc', writeOptions('PUT', { workspace_root: workspace.value.trim(), proxy_setting: proxy.value, default_llm_config_name: defaultModel.value, last_embedding_config_name: currentEmbedding.value }))
    if (config.value) {
      config.value.default_llm_config_name = defaultModel.value
      config.value.last_embedding_config_name = currentEmbedding.value
    }
    feedback.value = '全局配置已保存；切换工作目录需重启 Web 服务。'; error.value = ''
    emit('changed')
  } catch (cause) { error.value = String(cause) }
}
onMounted(() => { void load() })
</script>

<template><div class="feature-page"><div class="feature-head"><span class="eyebrow">全局配置</span><h1>把模型接到写作台。</h1><p>选择已有配置进行修改，或单独新增一份。密钥只保存在本机配置文件中，页面不会回显。</p></div>
  <p v-if="error" class="notice error" role="alert">{{ error }}</p><p v-if="feedback" class="notice success">{{ feedback }}</p>
  <div class="segmented"><button :class="{ selected: group === 'llm_configs' }" @click="chooseGroup('llm_configs')">大模型</button>
    <button :class="{ selected: group === 'embedding_configs' }" @click="chooseGroup('embedding_configs')">向量模型</button></div>
  <section class="config-workspace"><aside class="panel config-roster"><div class="config-roster-head"><div><span class="eyebrow">已保存</span><h2>{{ groupLabel }}配置 <small>{{ names.length }}</small></h2></div></div>
    <div v-for="(name, index) in names" :key="name" class="config-roster-row" :class="{ active: mode === 'edit' && selected === name }"><button class="config-roster-item" @click="select(name)"><strong>{{ name }} <em v-if="group === 'llm_configs' && name === config?.default_llm_config_name">默认</em><em v-if="group === 'embedding_configs' && name === config?.last_embedding_config_name">当前使用</em></strong><small>{{ config?.[group]?.[name]?.model_name || '未填写模型名' }}</small></button>
      <span v-if="group === 'llm_configs'" class="config-order-actions"><button type="button" title="上移" :disabled="index === 0" @click.stop="move(name, -1)">↑</button><button type="button" title="下移" :disabled="index === names.length - 1" @click.stop="move(name, 1)">↓</button></span></div>
    <p v-if="!names.length" class="muted-note">尚无配置。请点击下方新增。</p><button class="config-create" :class="{ active: mode === 'new' }" @click="add">＋ 新增{{ groupLabel }}配置</button></aside>
  <div class="panel config-editor"><div class="config-editor-head"><div><span class="eyebrow">{{ mode === 'new' ? '新增配置' : '编辑已有配置' }}</span><h2>{{ mode === 'new' ? `新建${groupLabel}连接` : selected || '选择左侧配置' }}</h2><p>{{ mode === 'new' ? '填写一个不重复的名称，保存后会出现在左侧清单。' : '修改只作用于当前选中的配置，不会创建新配置。' }}</p></div><button v-if="mode === 'new' && names.length" class="subtle" @click="select(names[0])">取消新增</button></div>
    <label>配置名称<input v-if="mode === 'new'" v-model="newName" placeholder="例如：本地 Ollama / DeepSeek Flash"><input v-else v-model="editName" placeholder="配置名称"></label>
    <div v-if="mode === 'new' || selected">
    <div class="feature-grid"><label>接口格式<select v-model="draft.interface_format"><option v-for="format in interfaceOptions" :key="format" :value="format">{{ format }}</option></select></label>
      <label>模型名<input v-model="draft.model_name"></label></div>
    <label>Base URL<input v-model="draft.base_url"></label>
    <label>API Key（{{ mode === 'new' ? '本地模型可留空' : '留空保留已保存的密钥' }}）<input v-model="draft.api_key" type="password" autocomplete="new-password" :placeholder="draft.has_api_key ? '已保存密钥' : '未设置'"></label>
    <div class="inline-fields"><label v-if="group === 'llm_configs'">温度<input v-model.number="draft.temperature" type="number" step="0.1"></label>
      <label v-if="group === 'llm_configs'">最大 tokens<input v-model.number="draft.max_tokens" type="number"></label>
      <label v-if="group === 'embedding_configs'">检索条数<input v-model.number="draft.retrieval_k" type="number"></label>
      <label>超时秒数<input v-model.number="draft.timeout" type="number"></label></div>
    <div class="config-editor-actions"><button :disabled="testing" @click="save">{{ mode === 'new' ? '新增并保存' : '保存修改' }}</button><button class="subtle" :disabled="testing" @click="test">{{ testing ? '测试中…' : '保存并测试连接' }}</button><button v-if="mode === 'edit'" class="danger subtle" :disabled="testing || names.length <= 1" @click="remove">删除此配置</button></div></div><p v-else class="muted-note">请从左侧选择配置，或新增一份。</p>
  </div></section>
  <section class="panel"><h2>默认模型与运行环境</h2>
    <label>默认续写模型<select v-model="defaultModel"><option v-for="name in Object.keys(config?.llm_configs || {})" :key="name" :value="name">{{ name }} · {{ config?.llm_configs?.[name]?.model_name || '未填写模型名' }}</option></select></label>
    <p class="muted-note">打开工作台或刷新页面时默认选择该模型；正式续写前仍会再次显示模型名称供确认。</p>
    <label>当前向量模型<select v-model="currentEmbedding"><option v-for="name in Object.keys(config?.embedding_configs || {})" :key="name" :value="name">{{ name }} · {{ config?.embedding_configs?.[name]?.model_name || config?.embedding_configs?.[name]?.interface_format || '未填写模型名' }}</option></select></label>
    <p class="muted-note">向量库构建、全文搜索、续写检索和保存章节后的增量更新均使用此配置。切换不同维度的模型后，需要重建已有向量库。</p>
    <label>工作目录<input v-model="workspace" placeholder="留空使用默认 workspace"></label>
    <label class="check"><input v-model="proxy.enabled" type="checkbox">启用代理</label>
    <div class="inline-fields"><label>代理地址<input v-model="proxy.proxy_url"></label><label>端口<input v-model="proxy.proxy_port"></label></div>
    <button @click="saveMisc">保存全局设置</button><p class="muted-note">工作目录变更在重启 Web 服务后生效。</p>
  </section>
</div></template>
