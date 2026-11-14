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

// Notifications on this device (D8): each push was encrypted for this browser,
// which decrypts it before this runs. It carries a short title, one line and
// the place in Row-Bot it is about; a push without them shows Row-Bot's own
// generic words. One notice per place: a newer one replaces it.
const HOME = '/app-v2/';
const PLACE = /^\/app-v2\/(?:conversations\/[A-Za-z0-9%._~-]{1,256})?$/;

const noticeOf = (data) => {
  let value = null;
  try {
    value = data ? data.json() : null;
  } catch {
    value = null;
  }
  const text = (field, limit) =>
    typeof value?.[field] === 'string'
      ? value[field].trim().slice(0, limit)
      : '';
  const title = text('title', 60);
  const path = text('path', 300);
  return {
    title: title || 'Row-Bot',
    body: title ? text('body', 120) : 'Row-Bot is waiting for you.',
    path: PLACE.test(path) ? path : HOME,
  };
};

self.addEventListener('push', (event) => {
  const notice = noticeOf(event.data);
  event.waitUntil(
    self.registration.showNotification(notice.title, {
      body: notice.body,
      icon: '/app-v2/icon-192.png',
      badge: '/app-v2/icon-192.png',
      tag: `row-bot:${notice.path}`,
      renotify: true,
      data: { path: notice.path },
    }),
  );
});

// Tapping a notice opens the place it is about: in an open Row-Bot window
// when there is one, otherwise in a new one.
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const path = event.notification.data?.path;
  const target = new URL(
    PLACE.test(path ?? '') ? path : HOME,
    self.location.origin,
  ).href;
  event.waitUntil(
    self.clients
      .matchAll({ type: 'window', includeUncontrolled: true })
      .then((windows) => {
        const open = windows.find(
          (client) => new URL(client.url).origin === self.location.origin,
        );
        if (!open) return self.clients.openWindow(target);
        return open
          .focus()
          .then((client) =>
            client.url === target || typeof client.navigate !== 'function'
              ? client
              : client.navigate(target).catch(() => client),
          );
      }),
  );
});
