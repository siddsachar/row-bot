import { expect, test, writeEvidence } from './evidence';
import {
  captureSurface,
  fixtureHeaders,
  openSeedConversation,
  watchReads,
} from './surface-helpers';

test('offline and reconnect keep the compact draft without replaying a command', async ({
  context,
  page,
}, testInfo) => {
  let eventRequests = 0;
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/v1/events')
      eventRequests += 1;
  });
  const reads = watchReads(page);
  // A page on this computer keeps its open conversation stream through a
  // network drop (#568), and an emulated outage never breaks a stream already
  // open. So the outage starts as the stream opens: its first request waits
  // for it, and every run takes the reconnect path this spec is about.
  let startOutage = () => {};
  const outage = new Promise<void>((resolve) => (startOutage = resolve));
  await page.route(
    (url) => url.pathname === '/api/v1/events',
    async (route) => {
      await outage;
      await route.continue();
    },
  );
  // Other specs on this fixture server may have sent messages already; only
  // a call made after this test starts would be a replay.
  const fixtureState = async () =>
    (
      await page.request.get('/__p1_fixture/state', {
        headers: fixtureHeaders(),
      })
    ).json();
  const callsBefore = (await fixtureState()).calls.length;
  await openSeedConversation(page);
  const composer = page.getByRole('textbox', {
    name: 'Message',
    exact: true,
  });
  await composer.fill('Draft retained through deterministic network handoff');
  // The draft is saved and the reads the first screen doesn't show have
  // answered: only what the outage itself starts can fail in it.
  await reads.settled();
  const initialEventRequests = eventRequests;
  expect(initialEventRequests).toBeGreaterThan(0);

  // A read or save the page happens to start during the outage (the
  // conversation stream and its retries, the queue, Buddy's picture) fails as
  // any request would offline.
  testInfo.annotations.push({
    type: 'expected-console-error',
    description: JSON.stringify({
      signature: 'Failed to load resource: net::ERR_INTERNET_DISCONNECTED',
      count: 6,
      upTo: true,
      owner: 'Compact offline and reconnect',
      fixture: 'context.setOffline(true) for the outage window',
    }),
  });
  await context.setOffline(true);
  startOutage();
  try {
    await expect(page.getByTestId('pwa-status')).toHaveAttribute(
      'data-pwa-state',
      'offline',
    );
    await expect(
      page.getByText(
        'Row-Bot is offline. Your unsent draft stays on this device.',
        { exact: true },
      ),
    ).toBeVisible();
    await expect(page.locator('.connection-status')).toHaveText(
      /reconnecting|disconnected/,
      { timeout: 30_000 },
    );
    await expect(composer).toHaveValue(
      'Draft retained through deterministic network handoff',
    );
    await captureSurface(page, testInfo, 'compact-offline', { axe: false });
  } finally {
    await context.setOffline(false);
  }

  await expect(page.locator('.connection-status')).toHaveText('Connected', {
    timeout: 30_000,
  });
  await expect(composer).toHaveValue(
    'Draft retained through deterministic network handoff',
  );
  await expect.poll(() => eventRequests).toBeGreaterThan(initialEventRequests);
  const fixture = await fixtureState();
  const replayed = fixture.calls.slice(callsBefore);
  expect(replayed).toEqual([]);
  expect(fixture.external_calls).toBe(0);
  await writeEvidence(testInfo, 'offline-reconnect-proof', {
    eventRequests,
    producerCalls: replayed.length,
    externalCalls: fixture.external_calls,
    draftRetained: true,
    scope:
      'Chromium loopback context offline emulation. Physical radio handoff/background suspension remains a device check.',
  });
  await captureSurface(page, testInfo, 'compact-reconnected');
});
