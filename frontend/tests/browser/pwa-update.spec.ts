import type { Page } from '@playwright/test';
import { expect, test, writeEvidence } from './evidence';
import { captureSurface, openHome } from './surface-helpers';
import { composer, newConversation } from './unified-helpers';

/** An older worker controls the page and a newer one is waiting. */
async function fakeWaitingUpdate(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const container = navigator.serviceWorker;
    const waiting = new EventTarget() as EventTarget & {
      state: ServiceWorkerState;
      postMessage(value: unknown): void;
    };
    waiting.state = 'installed';
    waiting.postMessage = (value: unknown) => {
      sessionStorage.setItem('phase5-update-message', JSON.stringify(value));
      queueMicrotask(() =>
        container.dispatchEvent(new Event('controllerchange')),
      );
    };
    const registration = new EventTarget() as EventTarget & {
      waiting: typeof waiting;
      installing: null;
    };
    registration.waiting = waiting;
    registration.installing = null;
    Object.defineProperty(container, 'controller', {
      configurable: true,
      value: {},
    });
    Object.defineProperty(container, 'register', {
      configurable: true,
      value: async () => registration,
    });
  });
}

test('manifest and installed service worker cache only immutable public assets', async ({
  context,
  page,
}, testInfo) => {
  await openHome(page);
  expect(
    await page.locator('meta[name="theme-color"]').evaluateAll((elements) =>
      elements.map((element) => ({
        media: element.getAttribute('media'),
        content: element.getAttribute('content'),
      })),
    ),
  ).toEqual([
    { media: '(prefers-color-scheme: light)', content: '#f4f7fb' },
    { media: '(prefers-color-scheme: dark)', content: '#101722' },
  ]);
  const manifestHref = await page
    .locator('link[rel="manifest"]')
    .getAttribute('href');
  expect(manifestHref).toBe('/app-v2/app.webmanifest');
  const manifestResponse = await page.request.get(manifestHref!);
  expect(manifestResponse.ok()).toBe(true);
  expect(manifestResponse.headers()['content-type']).toContain(
    'application/manifest+json',
  );
  const manifest = await manifestResponse.json();
  expect(manifest).toMatchObject({
    id: '/app-v2/',
    start_url: '/app-v2/',
    scope: '/app-v2/',
    display: 'standalone',
  });
  expect(manifest.icons).toEqual([
    expect.objectContaining({
      src: '/app-v2/icon-192.png',
      sizes: '192x192',
      type: 'image/png',
    }),
    expect.objectContaining({
      src: '/app-v2/icon-512.png',
      sizes: '512x512',
      type: 'image/png',
    }),
  ]);

  await page.evaluate(async () => {
    await navigator.serviceWorker.ready;
  });
  await page.reload();
  await expect(
    page.getByRole('heading', { name: 'Home', exact: true }),
  ).toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(() => Boolean(navigator.serviceWorker.controller)),
    )
    .toBe(true);
  const cacheProof = await page.evaluate(async () => {
    const script = document.querySelector<HTMLScriptElement>(
      'script[type="module"][src*="/assets/"]',
    );
    if (!script) throw new Error('Hashed application entry is unavailable');
    await fetch(script.src, { credentials: 'same-origin' });
    await fetch('/api/access/session', { credentials: 'same-origin' });
    const names = await caches.keys();
    const requests = (
      await Promise.all(
        names.map(async (name) => {
          const cache = await caches.open(name);
          return Promise.all(
            (await cache.keys()).map((request) => request.url),
          );
        }),
      )
    ).flat();
    const apiCached = await Promise.all(
      names.map(async (name) =>
        (await caches.open(name)).match('/api/access/session'),
      ),
    );
    const shellCached = await Promise.all(
      names.map(async (name) => (await caches.open(name)).match('/app-v2/')),
    );
    return {
      names,
      requests: requests.map((value) => new URL(value).pathname),
      apiCached: apiCached.some(Boolean),
      shellCached: shellCached.some(Boolean),
    };
  });
  expect(cacheProof.names).toEqual(['row-bot-public-shell-v1']);
  expect(cacheProof.requests.length).toBeGreaterThan(0);
  expect(
    cacheProof.requests.every((path) =>
      /^\/app-v2\/assets\/(?:[A-Za-z0-9_-]+\/)*[A-Za-z0-9_.-]+-[A-Za-z0-9_-]{8,}\.(?:js|css|svg|png|jpg|jpeg|webp|ico|woff2?)$/.test(
        path,
      ),
    ),
  ).toBe(true);
  expect(cacheProof.apiCached).toBe(false);
  expect(cacheProof.shellCached).toBe(false);
  testInfo.annotations.push({
    type: 'expected-console-error',
    description: JSON.stringify({
      signature:
        'Failed to load resource: the server responded with a status of 503 ()',
      count: 1,
      owner: 'Phase 5 cold-offline service-worker fixture',
      fixture: 'intentional-public-offline-navigation-503',
    }),
  });
  await context.setOffline(true);
  try {
    const offline = await page.goto('/app-v2/offline-proof');
    expect(offline?.status()).toBe(503);
    await expect(
      page.getByRole('heading', { name: 'Row-Bot is offline', exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText(
        'Your private workspace was not cached. Reconnect, then reload this page.',
        { exact: true },
      ),
    ).toBeVisible();
    await expect(page.getByText('Phase 1 conversation A')).toHaveCount(0);
    await captureSurface(page, testInfo, 'pwa-cold-offline-public-shell');
  } finally {
    await context.setOffline(false);
  }
  await writeEvidence(testInfo, 'pwa-cache-contract', {
    manifest: {
      id: manifest.id,
      start_url: manifest.start_url,
      scope: manifest.scope,
      display: manifest.display,
      icons: manifest.icons,
    },
    cacheProof,
    coldOfflineShellContainsPrivateWorkspace: false,
  });
});

test('an old controlled PWA presents an explicit update and activates only from the user action', async ({
  page,
}, testInfo) => {
  await fakeWaitingUpdate(page);
  await openHome(page);
  const update = page.getByRole('button', {
    name: 'Update and reload',
    exact: true,
  });
  await expect(update).toBeVisible();
  await expect(page.getByTestId('pwa-status')).toHaveAttribute(
    'data-pwa-state',
    'update_available',
  );
  await captureSurface(page, testInfo, 'pwa-update-available');
  await Promise.all([page.waitForEvent('framenavigated'), update.click()]);
  await expect(
    page.getByRole('heading', { name: 'Home', exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => sessionStorage.getItem('phase5-update-message')),
  ).toBe('{"type":"SKIP_WAITING"}');
  await writeEvidence(testInfo, 'old-pwa-update-action', {
    waitingWorker: true,
    explicitAction: 'Update and reload',
    message: { type: 'SKIP_WAITING' },
    reloaded: true,
    scope:
      'Deterministic ServiceWorkerContainer waiting-worker emulation verifies the React update contract. The preceding test exercises Chromium registration and CacheStorage against the shipped worker.',
  });
});

test('a waiting update never covers the composer or its Send button', async ({
  page,
}, testInfo) => {
  await fakeWaitingUpdate(page);
  await newConversation(page);
  const notice = page.getByTestId('pwa-status');
  await expect(
    notice.getByRole('button', { name: 'Update and reload', exact: true }),
  ).toBeVisible();
  await composer(page).fill('Still sendable');
  const send = page.getByRole('button', { name: 'Send', exact: true });
  const geometry: Record<string, unknown> = {};
  for (const size of [
    { name: 'phone', width: 390, height: 844 },
    { name: 'desktop', width: 1440, height: 900 },
  ]) {
    await page.setViewportSize(size);
    await expect(send).toBeVisible();
    const box = (await notice.boundingBox())!;
    for (const part of [composer(page), send]) {
      const other = (await part.boundingBox())!;
      const overlaps =
        box.x < other.x + other.width &&
        other.x < box.x + box.width &&
        box.y < other.y + other.height &&
        other.y < box.y + box.height;
      expect(overlaps, `${size.name}: the notice covers the composer`).toBe(
        false,
      );
    }
    // Nothing intercepts a click on Send (a trial click checks, sends nothing).
    await send.click({ trial: true });
    await captureSurface(
      page,
      testInfo,
      `pwa-update-over-${size.name}-composer`,
    );
    geometry[size.name] = { notice: box, send: await send.boundingBox() };
  }
  await writeEvidence(testInfo, 'pwa-update-clear-of-composer', geometry);
});

test('a notice stays above the composer when the install offer appears later', async ({
  page,
}, testInfo) => {
  await newConversation(page);
  // The offer arrives after the composer measured itself; it rises above it.
  await page.evaluate(() =>
    window.dispatchEvent(
      Object.assign(new Event('beforeinstallprompt', { cancelable: true }), {
        prompt: async () => {},
        userChoice: Promise.resolve({ outcome: 'dismissed', platform: 'web' }),
      }),
    ),
  );
  await expect(page.getByTestId('pwa-install')).toBeVisible();
  await composer(page).fill('/stop');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  const notice = page
    .locator('.toast')
    .filter({ hasText: 'No response is currently running.' });
  await expect(notice).toBeVisible();
  await notice.evaluate((element) =>
    Promise.all(element.getAnimations().map((motion) => motion.finished)),
  );
  // Found live: it stayed where it was and covered the composer's top.
  const box = (await notice.boundingBox())!;
  const field = (await page.locator('.composer').boundingBox())!;
  expect(
    box.y + box.height,
    'the notice covers the composer',
  ).toBeLessThanOrEqual(field.y);
  await captureSurface(page, testInfo, 'notice-above-composer-with-install');
});
