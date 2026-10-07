<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api } from './api'
import { compareChapters, visibleDiffRows } from './revisionDiff'

type Draft = { id: string; kind: 'ai_draft' | 'previous_version'; created_at: string; candidate: string; source: string; issues: string[] }
const props = defineProps<{ projectId: string; chapterNumber: number; currentText: string }>()
const emit = defineEmits<{ back: [] }>()
const drafts = ref<Draft[]>([])
const selected = ref('')
const mode = ref<'compare' | 'draft'>('compare')
const showAll = ref(false)
const error = ref('')
const draft = computed(() => drafts.value.find(item => item.id === selected.value))
const candidateBody = computed(() => {
  const candidate = draft.value?.candidate || ''
  const currentTitle = props.currentText.split(/\r?\n/, 1)[0]?.trim()
  const first = candidate.split(/\r?\n/, 1)[0]?.trim()
  return currentTitle && first === currentTitle ? candidate.replace(/^[^\n]*\n\s*/, '') : candidate
})
const comparison = computed(() => draft.value
  ? compareChapters(props.currentText.replace(/^[^\n]*\n\s*/, ''), candidateBody.value) : [])
const visibleRows = computed(() => visibleDiffRows(comparison.value, showAll.value))
const changes = computed(() => comparison.value.filter(row => row.kind !== 'same').length)
async function load() {
  if (!props.projectId || !props.chapterNumber) return
  try {
    drafts.value = await api<Draft[]>(`/api/projects/${props.projectId}/chapters/${props.chapterNumber}/revisions`)
    const params = new URLSearchParams(window.location.search)
    const requested = params.get('revision') || ''
    selected.value = requested ? drafts.value.find(item => item.id === requested)?.id || '' : drafts.value[0]?.id || ''
    mode.value = params.get('mode') === 'draft' ? 'draft' : 'compare'
    error.value = requested && !selected.value ? '指定修订稿不存在或尚未完成，没有自动切换到其他版本。' : ''
  } catch (cause) { error.value = String(cause) }
}
watch(() => [props.projectId, props.chapterNumber], () => { void load() })
onMounted(() => { void load() })
</script>

<template><div class="feature-page revision-page"><button class="back-link" @click="emit('back')">← 返回小说全文</button>
  <div class="feature-head"><span class="eyebrow">第 {{ chapterNumber }} 章 · 修订稿</span><h1>逐段对照，再决定取舍。</h1>
    <p>相近段落并排对齐；红色标出当前章将被删改的文字，绿色标出所选版本的新增文字。</p></div>
  <p v-if="error" class="notice error" role="alert">{{ error }}</p>
  <section v-if="drafts.length" class="revision-toolbar"><label>选择修订稿<select v-model="selected">
      <option v-for="item in drafts" :key="item.id" :value="item.id">{{ item.created_at }} · {{ item.kind === 'ai_draft' ? 'AI 修订稿' : '修改前版本' }}{{ item.issues.length ? ' · 有规则提醒' : '' }}</option>
    </select></label><div class="segmented"><button :class="{ selected: mode === 'compare' }" @click="mode = 'compare'">高亮对照</button>
      <button :class="{ selected: mode === 'draft' }" @click="mode = 'draft'">只看修订稿</button></div></section>
  <p v-if="draft?.issues.length" class="issue-list">{{ draft.issues.join('；') }}</p>
  <template v-if="draft && mode === 'compare'"><div class="revision-diff-controls"><span>{{ changes }} 处变动 · 比较方向：当前章节 → {{ draft.kind === 'ai_draft' ? 'AI 修订稿' : '修改前版本' }}</span>
    <label class="check"><input v-model="showAll" type="checkbox">显示未改动段落</label></div>
    <p v-if="!changes" class="notice success">两个版本的正文没有差异。</p>
    <div v-else class="revision-comparison"><div class="revision-column-head">当前章节</div><div class="revision-column-head">{{ draft.kind === 'ai_draft' ? 'AI 修订稿' : '修改前版本' }} · {{ draft.created_at }}</div>
      <template v-for="(row, index) in visibleRows" :key="index">
        <div v-if="row.kind === 'collapsed'" class="revision-collapsed">已收起 {{ row.count }} 段未改动内容 <button @click="showAll = true">展开全文</button></div>
        <template v-else><div class="revision-cell" :class="row.kind"><span v-for="(part, partIndex) in row.beforeParts" :key="partIndex" :class="{ 'diff-removed': part.changed }">{{ part.text }}</span></div>
          <div class="revision-cell" :class="row.kind"><span v-for="(part, partIndex) in row.afterParts" :key="partIndex" :class="{ 'diff-added': part.changed }">{{ part.text }}</span></div></template>
      </template></div></template>
  <article v-else-if="draft" class="panel revision-full-text">{{ draft.candidate }}</article>
  <section v-else class="panel"><p>本章暂无保存的 AI 整章修订稿。可在“审校与修订”生成建议后回来查看。</p></section>
</div></template>
