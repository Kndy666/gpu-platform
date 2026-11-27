/**
 * IME-safe check for custom Enter key handlers.
 * Handles Chrome/Firefox (isComposing) and Safari's out-of-order confirming keydown (keyCode === 229).
 */
export function isImeComposing(event: KeyboardEvent): boolean {
  return event.isComposing || event.keyCode === 229;
}
