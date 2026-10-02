<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'

const props = withDefaults(defineProps<{
  open: boolean
  title: string
  message: string
  dialogId?: string
  symbol?: string
  confirmLabel?: string
  cancelLabel?: string
  modelName?: string
}>(), { dialogId: 'confirm', symbol: '确', confirmLabel: '确认', cancelLabel: '取消' })

const emit = defineEmits<{ confirm: []; cancel: [] }>()
const confirmButton = ref<HTMLButtonElement | null>(null)

watch(() => props.open, async open => {
  if (!open) return
  await nextTick()
  confirmButton.value?.focus()
})
</script>

<template>
  <Teleport to="body">
    <Transition name="confirm-dialog">
      <div v-if="open" class="confirm-backdrop" @click.self="emit('cancel')" @keydown.esc="emit('cancel')">
        <section class="confirm-card" role="alertdialog" aria-modal="true" :aria-labelledby="`${dialogId}-title`" :aria-describedby="`${dialogId}-message`">
          <div class="confirm-symbol" aria-hidden="true">{{ symbol }}</div>
          <div class="confirm-copy">
            <span class="eyebrow">需要确认</span>
            <h2 :id="`${dialogId}-title`">{{ title }}</h2>
            <p :id="`${dialogId}-message`">{{ message }}</p>
            <div v-if="modelName" class="confirm-model"><span>本次调用模型</span><strong>{{ modelName }}</strong></div>
          </div>
          <div class="confirm-actions">
            <button type="button" class="subtle" @click="emit('cancel')">{{ cancelLabel }}</button>
            <button ref="confirmButton" type="button" class="confirm-primary" @click="emit('confirm')">{{ confirmLabel }}</button>
          </div>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>
