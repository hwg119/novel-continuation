<script setup lang="ts">
import { ref } from 'vue'
import { api } from './api'
const props = defineProps<{projectId:string;chapter:number}>()
const png = ref('')
const loading = ref(false)
const visible = ref(false)
const error = ref('')
async function show() {
  if (loading.value) return
  if (png.value) { visible.value = !visible.value; return }
  loading.value = true
  try {
    const result = await api<{exists:boolean;png?:string}>(`/api/projects/${props.projectId}/chapters/${props.chapter}/illustration`)
    if (!result.exists || !result.png) throw new Error('当前章节没有已保存的插图')
    png.value = result.png; visible.value = true
  } catch (cause) { error.value = String(cause) }
  finally { loading.value = false }
}
</script>
<template><div class="chat-illustration-preview"><button type="button" :disabled="loading" @click="show">{{loading ? '加载插图…' : visible ? '收起插图' : '查看插图'}}</button><p v-if="error" role="alert">{{error}}</p><figure v-if="visible"><img :src="png" :alt="`第 ${chapter} 章插图`"><figcaption>当前已保存的章节插图；重新生成会更新此图，Word 导出使用已保存版本。</figcaption></figure></div></template>
<style scoped>.chat-illustration-preview{margin-top:14px}.chat-illustration-preview button{border:1px solid #bed4ca;border-radius:6px;background:#edf4ef;color:#356b59;padding:8px 14px;font:inherit;font-size:14px}.chat-illustration-preview figure{margin:12px 0 0}.chat-illustration-preview img{display:block;width:100%;max-width:800px;height:auto;border:1px solid #d4e1dd;border-radius:6px}.chat-illustration-preview figcaption{margin-top:8px;font-size:12px;color:#698585}</style>
