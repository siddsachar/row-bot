/**
 * The chat a hosted first run opens after a sign-in (U3) suggests one first
 * message. The first run marks it for this window; sending it, or any message,
 * ends that.
 */
const KEY = 'row-bot:first-chat:v1';

export function markFirstChat(): void {
  try {
    sessionStorage.setItem(KEY, '1');
  } catch {
    /* Without storage the chat shows its usual suggestions. */
  }
}

export function firstChatPending(): boolean {
  try {
    return sessionStorage.getItem(KEY) === '1';
  } catch {
    return false;
  }
}

export function clearFirstChat(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    /* Nothing to clear. */
  }
}
