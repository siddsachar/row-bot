import { useSyncExternalStore } from 'react';

/**
 * The server's memory limit (H12), as the latest handshake said: a
 * container's, in bytes; null on the desktop, which has none. Voice and
 * Documents say what fits a small server with it (U8).
 */
let limit: number | null = null;
const listeners = new Set<() => void>();

/** Under this, local speech-to-text doesn't fit beside the rest (4 GB is recommended). */
const SMALL_SERVER_BYTES = 3.5 * 2 ** 30;

export function setMemoryLimit(bytes: number | null | undefined) {
  const next = bytes ?? null;
  if (next === limit) return;
  limit = next;
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** A memory size as people say it: "2 GB", "1.5 GB" (to the nearest half). */
export function sizeWords(bytes: number) {
  return `${Math.max(1, Math.round((bytes / 2 ** 30) * 2)) / 2} GB`;
}

/**
 * The size of a server too small for local speech-to-text ("2 GB"), or ''
 * when there is no limit or it is big enough; a page re-renders when it changes.
 */
export function useSmallServer(): string {
  const bytes = useSyncExternalStore(subscribe, () => limit);
  return bytes !== null && bytes < SMALL_SERVER_BYTES ? sizeWords(bytes) : '';
}
