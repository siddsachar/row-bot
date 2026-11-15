/**
 * One animation loop for every living Buddy on the page. Each instance asks
 * for a frame rate (60 while moving, less when settled) and is skipped while
 * off screen; the loop stops entirely when nothing visible needs frames or
 * the page is hidden.
 */
export type Animated = {
  /** Draw a frame; now is a rAF timestamp in milliseconds. */
  tick(now: number): void;
  fps(): number;
  visible: boolean;
};

type Entry = Animated & { last: number };

const entries = new Set<Entry>();
let frame = 0;
/** Living instances drawn at once; the rest show their still. */
export const LIVE_LIMIT = 4;
let live = 0;

function loop(now: number) {
  frame = 0;
  let any = false;
  for (const entry of entries) {
    if (!entry.visible) continue;
    any = true;
    if (now - entry.last >= 1000 / entry.fps() - 4) {
      entry.last = now;
      entry.tick(now);
    }
  }
  if (any && !document.hidden) frame = requestAnimationFrame(loop);
}

export function wake() {
  if (!frame && !document.hidden && entries.size)
    frame = requestAnimationFrame(loop);
}

if (typeof document !== 'undefined')
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) wake();
  });

export function schedule(animated: Animated): () => void {
  const entry = Object.assign(animated, { last: -Infinity });
  entries.add(entry);
  wake();
  return () => {
    entries.delete(entry);
  };
}

/** Claim one of the page's live slots; returns a release, or null when full. */
export function claimLiveSlot(): (() => void) | null {
  if (live >= LIVE_LIMIT) return null;
  live++;
  let released = false;
  return () => {
    if (!released) live--;
    released = true;
  };
}
