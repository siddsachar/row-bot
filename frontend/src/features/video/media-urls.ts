/**
 * Played files, shared and kept a little after their last use: a panel that
 * moves between the side and full screen mounts again, and a detached video
 * still loading a revoked address would fail.
 */
const cache = new Map<
  string,
  { url: Promise<string>; users: number; timer: number }
>();

export function acquire(
  ref: string,
  load: (signal: AbortSignal) => Promise<Blob | undefined>,
): Promise<string> {
  let entry = cache.get(ref);
  if (!entry) {
    const abort = new AbortController();
    entry = {
      url: load(abort.signal).then(
        (blob) => (blob ? URL.createObjectURL(blob) : ''),
        () => '',
      ),
      users: 0,
      timer: 0,
    };
    cache.set(ref, entry);
  }
  window.clearTimeout(entry.timer);
  entry.users += 1;
  return entry.url;
}

export function release(ref: string) {
  const entry = cache.get(ref);
  if (!entry) return;
  entry.users -= 1;
  if (entry.users > 0) return;
  entry.timer = window.setTimeout(() => {
    cache.delete(ref);
    void entry.url.then((url) => url && URL.revokeObjectURL(url));
  }, 5000);
}
