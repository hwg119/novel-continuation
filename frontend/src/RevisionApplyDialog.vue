<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { ElDialog } from 'element-plus'
import 'element-plus/es/components/dialog/style/css'
import { api, writeOptions } from './api'
import { compareChapters, visibleDiffRows } from './revisionDiff'

const props = defineProps<{ projectId: string; sessionId: string; actionId: string; jobId: string }>()
const emit = defineEmits<{ close: []; saved: [result: { vector_job_id?: string | null }] }>()
type Preview = { chapter: number; title: string; current: string; candidate: string; revision: string; outdated: boolean; applied: boolean; issues: string[] }
const preview = ref<Preview>()
const error = ref('')
const saving = ref(false)
const showAll = ref(false)
const rows = computed(() => preview.value ? compareChapters(preview.value.current,preview.value.candidate) : [])
const visible = computed(() => visibleDiffRows(rows.value,showAll.value))
const changes = computed(() => rows.value.filter(row => row.kind !== 'same').length)
const allowed = computed(() => preview.value && !preview.value.outdated && !preview.value.applied && !preview.value.issues.length && !error.value)
async function save() {
  if (!allowed.value || saving.value || !preview.value) return
  saving.value = true
  try {
    const result = await api<{ vector_job_id?: string | null }>(`/api/projects/${props.projectId}/chat/${props.sessionId}/actions/${props.actionId}/apply-revision`,writeOptions('POST',{revision:preview.value.revision}))
    emit('saved',result)
  } catch (cause) { error.value = String(cause) }
  finally { saving.value = false }
}
onMounted(async () => {
  try { preview.value = await api<Preview>(`/api/projects/${props.projectId}/revisions/${props.jobId}/preview`) }
  catch (cause) { error.value = String(cause) }
  await nextTick()
})
</script>

<template><ElDialog :model-value="true" title="比较并应用修订稿" class="chat-revision-dialog" width="min(1100px, 94vw)" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" @close="emit('close')">
  <p v-if="!preview && !error">正在读取当前正文与修订稿…</p><p v-if="error" class="notice error" role="alert">{{ error }}</p>
  <template v-if="preview"><p class="revision-dialog-intro">第 {{ preview.chapter }} 章 · {{ preview.title }} · {{ changes }} 处变动</p>
    <p v-if="preview.applied" class="notice success">此修订稿已保存为当前正文，无需重复应用。</p><p v-else-if="preview.outdated" class="notice error">正文在生成修订稿后已变化，不能直接覆盖。请到完整修订页重新核对。</p>
    <ul v-if="preview.issues.length" class="issue-list"><li v-for="(issue,index) in preview.issues" :key="index">{{ issue }}</li></ul>
    <label class="check"><input v-model="showAll" type="checkbox">显示未改动段落</label>
    <div class="revision-dialog-scroll"><div class="revision-comparison"><div class="revision-column-head">当前正文 · 删除为红色</div><div class="revision-column-head">修订稿 · 新增为绿色</div>
      <template v-for="(row,index) in visible" :key="index"><div v-if="row.kind === 'collapsed'" class="revision-collapsed">已收起 {{ row.count }} 段未改动内容 <button @click="showAll = true">展开</button></div><template v-else><div class="revision-cell" :class="row.kind"><span v-for="(part,i) in row.beforeParts" :key="i" :class="{'diff-removed':part.changed}">{{ part.text }}</span></div><div class="revision-cell" :class="row.kind"><span v-for="(part,i) in row.afterParts" :key="i" :class="{'diff-added':part.changed}">{{ part.text }}</span></div></template></template>
    </div></div>
  </template>
  <template #footer><span class="revision-dialog-note">确认后自动备份并保存正文，无需再点保存。</span><button class="dialog-secondary" :disabled="saving" @click="emit('close')">取消</button><button class="dialog-primary" :disabled="!allowed || saving" @click="save">{{ saving ? '正在保存…' : '应用并保存正文' }}</button></template>
</ElDialog></template>

<style>
.chat-revision-dialog{border:1px solid #cbdad2;border-radius:8px;background:#fbfdfb;--el-color-primary:#326b59;--el-text-color-primary:#183f3a;--el-text-color-regular:#587069}.chat-revision-dialog .el-dialog__title{font:600 24px/1.3 "KaiTi","STKaiti",serif;color:#183f3a}.chat-revision-dialog .el-dialog__footer{display:flex;flex-wrap:wrap;gap:10px;align-items:center;border-top:1px solid #e0e9e4;padding-top:14px}.revision-dialog-intro{margin-top:0;color:#587069}.revision-dialog-scroll{max-height:55vh;overflow:auto;overscroll-behavior:contain;margin-top:14px}.revision-dialog-note{margin-right:auto;font-size:12px;color:#698585}.chat-revision-dialog .dialog-primary,.chat-revision-dialog .dialog-secondary{border:1px solid #cbdad2;border-radius:6px;padding:10px 16px;font:inherit;background:#edf4ef;color:#356b59}.chat-revision-dialog .dialog-primary{background:#326b59;border-color:#326b59;color:#fff}.chat-revision-dialog button:disabled{opacity:.45;cursor:not-allowed}.chat-revision-dialog button:focus-visible{outline:2px solid #79a998;outline-offset:2px}
</style>
