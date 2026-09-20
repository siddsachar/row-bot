/* global self, caches, fetch, Response, URL */
/* Row-Bot PWA v1: immutable public build assets only. */
const CACHE_PREFIX = 'row-bot-public-shell-';
const CACHE_NAME = `${CACHE_PREFIX}v1`;
const IMMUTABLE_ASSET =
  /^\/app-v2\/assets\/(?:[A-Za-z0-9_-]+\/)*[A-Za-z0-9_.-]+-[A-Za-z0-9_-]{8,}\.(?:js|css|svg|png|jpg|jpeg|webp|ico|woff2?)$/;

const offline = () =>
  new Response(
    '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#101722"><title>Row-Bot offline</title><main><h1>Row-Bot is offline</h1><p>Your private workspace was not cached. Reconnect, then reload this page.</p><button onclick="location.reload()">Try again</button></main></html>',
    {
      status: 503,
      headers: {
        'Cache-Control': 'no-store',
        'Content-Type': 'text/html; charset=utf-8',
        'Referrer-Policy': 'no-referrer',
        'X-Content-Type-Options': 'nosniff',
      },
    },
  );

self.addEventListener('message', (event) => {
  if (event.data?.type === 'SKIP_WAITING') void self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) =>
        Promise.all(
          names
            .filter(
              (name) => name.startsWith(CACHE_PREFIX) && name !== CACHE_NAME,
            )
            .map((name) => caches.delete(name)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).catch(offline));
    return;
  }
  if (!IMMUTABLE_ASSET.test(url.pathname)) return;
  event.respondWith(
    caches.open(CACHE_NAME).then(async (cache) => {
      const cached = await cache.match(request);
      if (cached) return cached;
      const response = await fetch(request);
      const policy = response.headers.get('Cache-Control') ?? '';
      if (
        response.ok &&
        response.type === 'basic' &&
        /(?:^|,)\s*public\b/i.test(policy) &&
        /(?:^|,)\s*immutable\b/i.test(policy)
      ) {
        await cache.put(request, response.clone());
      }
      return response;
    }),
  );
});
