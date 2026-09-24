/** Only a visible, idle conversation may present a finished design unprompted. */
export function canAutoOpenDesign(
  conversationId: string,
  pathname: string,
  visibility: DocumentVisibilityState,
  active: Element | null,
  _draftText: string,
): boolean {
  if (
    visibility !== 'visible' ||
    pathname !== `/conversations/${conversationId}`
  )
    return false;
  if (!(active instanceof HTMLElement)) return true;
  if (active.closest('.panel-content, [role="dialog"]')) return false;
  if (active.matches('textarea, input, [contenteditable="true"]')) return false;
  return true;
}
