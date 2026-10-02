import { reactive } from 'vue'

export type ConfirmOptions = {
  title: string
  message: string
  symbol?: string
  confirmLabel?: string
  cancelLabel?: string
  modelName?: string
}

export const confirmState = reactive({
  open: false, title: '', message: '', symbol: '确', confirmLabel: '确认',
  cancelLabel: '取消', modelName: '',
})

let resolveCurrent: ((confirmed: boolean) => void) | null = null

export function askConfirm(options: ConfirmOptions): Promise<boolean> {
  if (resolveCurrent) resolveCurrent(false)
  Object.assign(confirmState, {
    open: true, title: options.title, message: options.message,
    symbol: options.symbol || '确', confirmLabel: options.confirmLabel || '确认',
    cancelLabel: options.cancelLabel || '取消', modelName: options.modelName || '',
  })
  return new Promise(resolve => { resolveCurrent = resolve })
}

function finish(confirmed: boolean) {
  confirmState.open = false
  const resolve = resolveCurrent
  resolveCurrent = null
  resolve?.(confirmed)
}

export function acceptConfirm() { finish(true) }
export function cancelConfirm() { finish(false) }
