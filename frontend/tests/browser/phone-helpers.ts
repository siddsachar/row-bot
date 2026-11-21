import type { Locator, Page } from '@playwright/test';
import { expect, test } from './evidence';
import { blockFixtureServiceWorkers } from './unified-helpers';

const ANDROID =
  'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36';

/** The two phone sizes every phone spec covers, with touch and a mobile user agent. */
export const PHONES = [
  { name: 'phone 375', viewport: { width: 375, height: 812 } },
  { name: 'phone 390', viewport: { width: 390, height: 844 } },
].map(({ name, viewport }) => ({
  name,
  use: { viewport, hasTouch: true, isMobile: true, userAgent: ANDROID },
}));

/**
 * Runs the enclosing describe block's tests on this phone. Call it inside
 * `test.describe`. Firefox has no mobile emulation, so they skip there.
 */
export function onPhone(phone: (typeof PHONES)[number]): void {
  test.use(phone.use);
  test.skip(
    ({ browserName }) => browserName === 'firefox',
    'Firefox has no mobile emulation',
  );
}

/**
 * Makes the page see what a paired phone sees. The browser runs on the
 * computer Row-Bot runs on, so the handshake would give it the desktop's own
 * features: this drops the `native:*` and `computer:interactive` capabilities
 * and says Row-Bot runs on a server. Call it before the page opens the app.
 * Service workers are blocked so every handshake reaches the rewrite.
 */
export async function asPairedPhone(page: Page): Promise<void> {
  await blockFixtureServiceWorkers(page.context());
  await page.route('**/api/v1/handshake', async (route) => {
    const response = await route.fetch();
    const view = await response.json();
    view.application_capabilities = (
      (view.application_capabilities ?? []) as string[]
    ).filter(
      (capability) =>
        !capability.startsWith('native:') &&
        capability !== 'computer:interactive',
    );
    view.place = { ...view.place, kind: 'server' };
    await route.fulfill({ response, json: view });
  });
}

/**
 * Taps each step in turn and returns the number of taps. A step is a locator,
 * or the exact name of the one link or button it taps. A step must be on
 * screen when its turn comes: one below the fold fails rather than being
 * scrolled to, so a path that needs a scroll never counts as taps alone.
 */
export async function tapPath(
  page: Page,
  steps: (Locator | string)[],
): Promise<number> {
  for (const step of steps) {
    const target =
      typeof step === 'string'
        ? page
            .getByRole('link', { name: step, exact: true })
            .or(page.getByRole('button', { name: step, exact: true }))
        : step;
    await expect(target).toBeInViewport();
    await target.tap();
  }
  return steps.length;
}

/** Under 768px: the phone layout. */
export function onPhoneWidth(page: Page): boolean {
  return page.viewportSize()!.width < 768;
}

/**
 * Home has opened: its sub-tabs on a computer or tablet; a phone has none
 * (C1), so there it is the page the address chose.
 */
export function homeOpened(page: Page): Locator {
  return onPhoneWidth(page)
    ? page.locator('.home-view').getByRole('tabpanel')
    : page.getByRole('tablist', { name: 'Home capabilities' });
}

/**
 * Opens one of Home's pages ("Knowledge", "Monitor"…): its tab on a computer
 * or tablet; on a phone, which has no sub-tabs (C1), its address.
 */
export async function openHomeTab(page: Page, name: string): Promise<void> {
  if (onPhoneWidth(page)) await page.goto(`/app-v2/?tab=${name.toLowerCase()}`);
  else await page.getByRole('tab', { name, exact: true }).click();
}

/** The pages in a phone's Settings › Row-Bot group (C3); the rest are in More settings. */
const PHONE_ROW_BOT_PAGES = new Set([
  'Models',
  'Voice',
  'Memory',
  'Apps',
  'Activity',
  'Buddy & Proactive',
]);

/**
 * A Settings page's link in a phone's lists (C3): a Row-Bot row, or one in
 * More settings, which this opens first. "Buddy" is Buddy & Proactive's row.
 * Call it on the Settings list.
 */
export async function phoneSettingLink(
  page: Page,
  name: string,
): Promise<Locator> {
  const row = name === 'Buddy' ? 'Buddy & Proactive' : name;
  await expect(page.getByRole('list', { name: 'This phone' })).toBeVisible();
  if (!PHONE_ROW_BOT_PAGES.has(row))
    await page
      .getByRole('link', { name: 'More settings', exact: true })
      .click();
  return page
    .locator('.phone-settings')
    .getByRole('link', { name: row, exact: true });
}

/**
 * Shows Devices' "Connect a phone or computer": at the top on a computer or
 * tablet; under Advanced on a phone (C3), which this opens first.
 */
export async function revealConnect(page: Page): Promise<void> {
  if (onPhoneWidth(page))
    await page
      .locator('.settings-advanced summary', { hasText: 'Advanced' })
      .click();
}
