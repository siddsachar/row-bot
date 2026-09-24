const scripts = new Map<string, Promise<void>>();

/** Load one authenticated, same-origin renderer asset exactly once. */
export function loadLocalRuntimeScript(path: string, ready: () => boolean) {
  if (ready()) return Promise.resolve();
  const existing = scripts.get(path);
  if (existing) return existing;
  const promise = new Promise<void>((resolve, reject) => {
    const script = document.createElement('script');
    script.src = `/app-v2/runtime/${path}`;
    script.async = true;
    script.referrerPolicy = 'no-referrer';
    script.addEventListener('load', () =>
      ready() ? resolve() : reject(new Error('renderer_unavailable')),
    );
    script.addEventListener('error', () =>
      reject(new Error('renderer_failed')),
    );
    document.head.append(script);
  });
  scripts.set(path, promise);
  void promise.catch(() => scripts.delete(path));
  return promise;
}
