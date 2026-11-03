import type { Locator, Page, TestInfo } from '@playwright/test';
import {
  assertNoOverflow,
  expect,
  screenshot,
  test,
  writeEvidence,
} from './evidence';
import { fixtureHeaders } from './surface-helpers';
import { blockFixtureServiceWorkers, dismissNotices } from './unified-helpers';

/*
 * B407 · One push channel for background state. A workflow run the scheduler
 * starts in the background (here "Morning digest", whose step waits for an
 * approval) reaches the sidebar, Home and Monitor at once, without a reload
 * and without waiting for any timer.
 * B406 · A desktop window binds its native bridge again silently: a reload
 * or a refocus never shows "Desktop features are reconnecting…".
 */

const PHONE = { width: 390, height: 844 };
const DESKTOP = { width: 1440, height: 900 };
// The run's conversation, titled with its icon and when it ran.
const RUN = /^\W*Morning digest/;
const ASK = "Send this morning's digest to the team?";
// The run's turn reaches its approval in the background, which a busy runner
// can take several seconds to do. Pushed, the approval shows as soon as it is
// raised; read by timer it would wait for the minute's fallback read.
const RAISED_MS = 10_000;

test.use({ serviceWorkers: 'allow', nativeNetwork: true });
test.beforeEach(async ({ context, page }) => {
  await blockFixtureServiceWorkers(context);
  await page.setViewportSize(DESKTOP);
  for (const step of ['reset', 'titles']) {
    const done = await page.request.post(`/__b407_fixture/${step}`, {
      headers: fixtureHeaders(),
    });
    expect(done.ok(), await done.text()).toBe(true);
  }
});
test.afterEach(async ({ page }) => {
  const reset = await page.request.post('/__b407_fixture/reset', {
    headers: fixtureHeaders(),
  });
  expect(reset.ok()).toBe(true);
});

const sidebar = (page: Page) =>
  page.getByRole('navigation', { name: 'Workspace navigation', exact: true });

/** Open the app and wait until its live stream is open. */
async function openLive(page: Page, path: string) {
  const live = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === '/api/v1/live' &&
      response.status() === 200,
  );
  await page.goto(path);
  await live;
  await expect(sidebar(page)).toBeVisible();
}

/**
 * Start the run on the server as its schedule would; how long until `shown`
 * is visible, from asking for the run and from the run having started.
 */
async function startRun(page: Page, shown: Locator) {
  await expect(shown).toHaveCount(0);
  const asked = Date.now();
  const response = await page.request.post('/__b407_fixture/background-run', {
    headers: fixtureHeaders(),
  });
  expect(response.ok(), await response.text()).toBe(true);
  const started = Date.now();
  await expect(shown).toBeVisible({ timeout: 3000 });
  const now = Date.now();
  return { fromStartMs: now - started, fromRequestMs: now - asked };
}

async function views(page: Page, info: TestInfo, name: string) {
  await dismissNotices(page);
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await expect(page.locator('html')).toHaveAttribute(
      'data-theme',
      appearance,
    );
    await assertNoOverflow(page);
    await screenshot(page, info, `${name}-${appearance}`);
  }
  await page.emulateMedia({ colorScheme: 'light' });
}

test('a background workflow run reaches the sidebar and Home at once (B407)', async ({
  page,
}, info) => {
  await openLive(page, '/app-v2/');
  const row = sidebar(page).getByRole('button', { name: RUN });
  const inSidebar = await startRun(page, row);
  // Its approval waits in Home's Needs you, read the same way.
  const needs = page.getByRole('list', { name: 'Needs you' });
  await expect(needs.getByText(ASK)).toBeVisible({ timeout: RAISED_MS });
  // Live updates, not a timer: within about a second.
  expect(inSidebar.fromStartMs).toBeLessThan(1500);
  await writeEvidence(info, 'background-run-timing', inSidebar);
  await views(page, info, 'background-run');
  await page.setViewportSize(PHONE);
  await page
    .getByRole('button', { name: 'Toggle navigation', exact: true })
    .click();
  await expect(
    page.getByRole('dialog').getByRole('button', { name: RUN }).first(),
  ).toBeVisible();
  await views(page, info, 'background-run-phone');
});

test('Monitor counts a background run and the sidebar says its approval waits (B407)', async ({
  page,
}, info) => {
  await openLive(page, '/app-v2/?tab=monitor');
  // With every check passing, the activity chart waits behind Show details (U38).
  const details = page.getByRole('button', { name: 'Show details' });
  if (await details.isVisible()) await details.click();
  const lane = page.getByRole('img', {
    name: 'Workflow runs: 1 in the last 24 hours',
  });
  const inMonitor = await startRun(page, lane);
  await expect(
    sidebar(page).getByText('1 approval is waiting', { exact: true }),
  ).toBeVisible({ timeout: RAISED_MS });
  expect(inMonitor.fromStartMs).toBeLessThan(1500);
  await writeEvidence(info, 'background-run-monitor-timing', inMonitor);
  await views(page, info, 'background-run-monitor');
  await page.setViewportSize(PHONE);
  await lane.scrollIntoViewIfNeeded();
  await views(page, info, 'background-run-monitor-phone');
});

test('a desktop window reloaded on a Home tab binds again without the reconnecting pill (B406)', async ({
  page,
}, info) => {
  // A desktop window whose bridge keeps answering that its proof lapsed: it
  // binds again in the background the whole time.
  await page.addInitScript(() => {
    if (window !== window.top) return;
    Object.assign(window, {
      __ROW_BOT_NATIVE_CLIENT__: {
        dispatch: async () => ({
          status: 'unavailable',
          reason: 'native_proof_required',
        }),
      },
    });
  });
  await openLive(page, '/app-v2/');
  // Home's tabs live at "/app-v2?tab=…": reloading one comes back to it.
  await page.getByRole('tab', { name: 'Monitor' }).click();
  await expect(page).toHaveURL(/\/app-v2\?tab=monitor$/);
  await page.reload();
  await expect(sidebar(page)).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Monitor' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await page.evaluate(() => {
    window.dispatchEvent(new Event('focus'));
    document.dispatchEvent(new Event('visibilitychange'));
  });
  const status = page.locator('.desktop-reconnecting');
  // Longer than the pill's own delay: nothing the person asked for waits.
  await page.waitForTimeout(3000);
  await expect(status).toBeEmpty();
  await expect(page).toHaveURL(/\?tab=monitor$/);
  await views(page, info, 'reload-no-pill');
  await page.setViewportSize(PHONE);
  await expect(status).toBeEmpty();
  await views(page, info, 'reload-no-pill-phone');
});
