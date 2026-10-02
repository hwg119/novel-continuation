import { reactive } from 'vue'

export type ToastKind = 'success' | 'error' | 'info'

export const toastState = reactive({
  id: 0,
  message: '',
  kind: 'info' as ToastKind,
  duration: 4200,
})

export function readableError(cause: unknown): string {
  const text = cause instanceof Error ? cause.message : String(cause || '发生未知错误')
  return text.replace(/^Error:\s*/i, '').trim() || '发生未知错误'
}

export function showToast(message: string, kind: ToastKind = 'info', duration?: number) {
  toastState.id += 1
  toastState.message = message
  toastState.kind = kind
  toastState.duration = duration ?? (kind === 'error' ? 7000 : 4200)
}

export function closeToast() { toastState.message = '' }
export const toast = {
  success: (message: string) => showToast(message, 'success'),
  error: (cause: unknown) => showToast(readableError(cause), 'error'),
  info: (message: string) => showToast(message, 'info'),
}
