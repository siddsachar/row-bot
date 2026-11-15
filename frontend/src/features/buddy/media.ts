import { useEffect, useState, useSyncExternalStore } from 'react';

// A replaced image may still start its fetch in a later task, so a retired
// media URL outlives the commit that dropped it by this long.
const REVOKE_DELAY_MS = 2000;

export function revokeLater(url: string) {
  window.setTimeout(() => URL.revokeObjectURL(url), REVOKE_DELAY_MS);
}

// The browser counts an image that starts loading while the page unloads
// (beforeunload, pagehide) as a ping, which connect-src governs, so a blob:
// still would break the page's policy then. A render can still land in that
// moment: React commits pending updates (a still that just arrived) with any
// synchronous one. Buddy shows Row-Bot's glyph in such a render instead.
let leaving = false;
let hidden = false;
const unloadListeners = new Set<() => void>();

function unloadChanged() {
  if (!leaving && !hidden) unloadListeners.forEach((listener) => listener());
}

if (typeof window !== 'undefined') {
  // Capture: these run before the page's own handlers, which may render.
  window.addEventListener(
    'beforeunload',
    () => {
      leaving = true;
      // The person stayed (an unsaved draft asked them to): carry on.
      window.setTimeout(() => {
        leaving = false;
        unloadChanged();
      }, 0);
    },
    true,
  );
  window.addEventListener('pagehide', () => (hidden = true), true);
  window.addEventListener(
    'pageshow',
    () => {
      hidden = false;
      unloadChanged();
    },
    true,
  );
}

/** True while the page unloads: no Buddy image may start loading then. */
export function usePageUnloading(): boolean {
  return useSyncExternalStore(
    (listener) => {
      unloadListeners.add(listener);
      return () => unloadListeners.delete(listener);
    },
    () => leaving || hidden,
  );
}

export type BuddyMediaLoader = (
  conversation: string | null,
  packId: string,
  assetId: string,
  revision: string,
  signal: AbortSignal,
) => Promise<Blob>;

const MEDIA_LIMIT = 24;
const rememberedMedia = new WeakMap<object, Map<string, Blob>>();

/**
 * Pack media are the same bytes in every conversation for one pack revision,
 * so switching conversations reuses them instead of downloading them again
 * (each download spends the session's view budget). Bounded per owner.
 */
export function rememberBuddyMedia(
  owner: object,
  load: BuddyMediaLoader,
): BuddyMediaLoader {
  return async (conversation, pack, asset, revision, signal) => {
    let media = rememberedMedia.get(owner);
    if (!media) rememberedMedia.set(owner, (media = new Map()));
    const key = `${pack}\u0000${asset}\u0000${revision}`;
    const known = media.get(key);
    if (known) {
      media.delete(key);
      media.set(key, known);
      return known;
    }
    const blob = await load(conversation, pack, asset, revision, signal);
    media.set(key, blob);
    for (const oldest of media.keys()) {
      if (media.size <= MEDIA_LIMIT) break;
      media.delete(oldest);
    }
    return blob;
  };
}

export function useReducedMotion() {
  const query = '(prefers-reduced-motion: reduce)';
  const read = () =>
    typeof matchMedia === 'function' ? matchMedia(query).matches : false;
  const [reduced, setReduced] = useState(read);
  useEffect(() => {
    if (typeof matchMedia !== 'function') return;
    const media = matchMedia(query);
    const changed = () => setReduced(media.matches);
    media.addEventListener('change', changed);
    changed();
    return () => media.removeEventListener('change', changed);
  }, []);
  return reduced;
}
