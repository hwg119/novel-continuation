<script setup lang="ts">
import { onUnmounted, watch } from 'vue'

const props = withDefaults(defineProps<{
  message: string
  duration?: number
  kind?: 'success' | 'error' | 'info'
  messageId?: number
}>(), { duration: 3000, kind: 'success', messageId: 0 })
const emit = defineEmits<{ close: [] }>()
let timer: ReturnType<typeof setTimeout> | null = null

watch(() => [props.message, props.messageId] as const, ([message]) => {
  if (timer) clearTimeout(timer)
  if (message) timer = setTimeout(() => emit('close'), props.duration)
}, { immediate: true })

onUnmounted(() => { if (timer) clearTimeout(timer) })
</script>

<template><Teleport to="body"><Transition name="message-toast">
  <div v-if="message" class="message-toast" :class="`message-toast-${kind}`" :role="kind === 'error' ? 'alert' : 'status'" aria-live="polite">
    <span class="message-toast-mark" aria-hidden="true">{{ kind === 'error' ? '!' : kind === 'info' ? 'i' : '✓' }}</span>
    <span>{{ message }}</span>
    <button type="button" aria-label="关闭提示" @click="emit('close')">×</button>
  </div>
</Transition></Teleport></template>
