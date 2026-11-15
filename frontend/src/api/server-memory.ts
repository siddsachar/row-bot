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

const GB = 1e9;
const GIB = 2 ** 30;
// The kernel keeps a limit in whole memory pages, so one set as 1 GB reads a little under it.
const PAGE_SLACK = 2 ** 20;

/** How many whole `unit`s `bytes` is, give or take the page rounding; 0 when it isn't whole. */
function whole(bytes: number, unit: number) {
  const count = Math.round(bytes / unit);
  return count >= 1 && Math.abs(bytes - count * unit) <= PAGE_SLACK ? count : 0;
}

/**
 * Memory in the unit the host set the limit in (H12, U8). A limit of a whole
 * number of GB (Railway's 8,000,000,000 bytes) or GiB (Docker's
 * `--memory 4g`) is that number: "8 GB", "4 GB"; any other limit is decimal
 * GB to one place, "2.7 GB". With `limitBytes`, `bytes` is memory in use,
 * said to one place in the limit's unit: "1.4 GB" of "4 GB".
 */
export function sizeWords(bytes: number, limitBytes?: number) {
  const unit = whole(limitBytes ?? bytes, GIB) ? GIB : GB;
  const count = limitBytes === undefined ? whole(bytes, unit) : 0;
  return count ? `${count} GB` : `${Math.max(0.1, bytes / unit).toFixed(1)} GB`;
}

/**
 * The size of a server too small for local speech-to-text ("2 GB"), or ''
 * when there is no limit or it is big enough; a page re-renders when it changes.
 */
export function useSmallServer(): string {
  const bytes = useSyncExternalStore(subscribe, () => limit);
  return bytes !== null && bytes < SMALL_SERVER_BYTES ? sizeWords(bytes) : '';
}
