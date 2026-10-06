/** Do not treat the Enter used to commit Chinese IME text as a send action. */
export function isCompositionEnter(event: Pick<KeyboardEvent, 'key' | 'isComposing' | 'keyCode'>, composing = false) {
  return event.key === 'Enter' && (composing || event.isComposing || event.keyCode === 229)
}
