import { expect, test, writeEvidence } from './evidence';
import {
  captureSurface,
  fixtureHeaders,
  openSeedConversation,
} from './phase5-helpers';

test('offline and reconnect keep the compact draft without replaying a command', async ({
  context,
  page,
}, testInfo) => {
  let eventRequests = 0;
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/v1/events')
      eventRequests += 1;
  });
  await openSeedConversation(page);
  const composer = page.getByRole('textbox', {
    name: 'Message',
    exact: true,
  });
  await composer.fill('Draft retained through deterministic network handoff');
  const initialEventRequests = eventRequests;

  await context.setOffline(true);
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
  const fixture = await (
    await page.request.get('/__p1_fixture/state', {
      headers: fixtureHeaders(),
    })
  ).json();
  expect(fixture.calls).toEqual([]);
  expect(fixture.external_calls).toBe(0);
  await writeEvidence(testInfo, 'offline-reconnect-proof', {
    eventRequests,
    producerCalls: fixture.calls.length,
    externalCalls: fixture.external_calls,
    draftRetained: true,
    scope:
      'Chromium loopback context offline emulation. Physical radio handoff/background suspension remains a device check.',
  });
  await captureSurface(page, testInfo, 'compact-reconnected');
});
