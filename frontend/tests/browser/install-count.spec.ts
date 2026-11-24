import type { Page, TestInfo } from '@playwright/test';
import { homeOpened } from './phone-helpers';
import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';

// N6: install stats, on by default and shown only in Settings › System ›
// Privacy. The fixture keeps them dormant unless a spec turns them on
// (/__n6_fixture/install-count/{state}); its stats service only records what
// would have been sent, so nothing leaves this computer.

function control() {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated browser runner');
  return { 'X-Fixture-Token': token, Origin: new URL(base).origin };
}

async function fixture(page: Page, path: string) {
  const response = await page.request.post(path, { headers: control() });
  expect(response.ok(), await response.text()).toBe(true);
}

async function sent(page: Page): Promise<{ path: string; body: object }[]> {
  const response = await page.request.get('/__n6_fixture/install-count/sent', {
    headers: control(),
  });
  expect(response.ok()).toBe(true);
  return (await response.json()).sent;
}

test.afterEach(async ({ page }) => {
  await fixture(page, '/__n6_fixture/install-count/restore');
});

const phone = (info: TestInfo) =>
  (info.project.use.viewport?.width ?? 1440) < 700;

/** Light and dark, then an accessibility scan of what shows. */
async function shoot(page: Page, info: TestInfo, name: string) {
  const suffix = phone(info) ? '-phone' : '';
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `${name}${suffix}-${appearance}`);
  }
  await accessibility(page, info, `${name}${suffix}`);
  await page.emulateMedia({ colorScheme: 'light' });
}

test('nothing about install stats shows outside Settings', async ({ page }) => {
  await fixture(page, '/__n6_fixture/install-count/fresh');
  await page.goto('/app-v2/');
  await expect(homeOpened(page)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByText(/install stats/i)).toHaveCount(0);
});

test('Settings › System › Privacy: one row, details on request, and deleting what was sent', async ({
  page,
}, info) => {
  await fixture(page, '/__n6_fixture/install-count/on');
  await page.goto('/app-v2/settings/system#privacy');
  const privacy = page.getByRole('region', { name: 'Privacy' });
  const toggle = privacy.getByRole('switch', { name: 'Share install stats' });
  await expect(toggle).toBeChecked();
  await expect(privacy).toContainText('Install stats');
  await expect(privacy).toContainText('last sent today');
  const disclosure = (name: string) =>
    privacy.locator('details', {
      has: page.locator('summary', { hasText: name }),
    });
  // Closed: a few lines, the details and the contacts behind their disclosures.
  for (const name of ['Details', 'What else Row-Bot contacts'])
    await expect(disclosure(name)).not.toHaveAttribute('open');
  await expect(privacy.getByText(/Cloudflare sees it/)).toBeHidden();
  await privacy.scrollIntoViewIfNeeded();
  await shoot(page, info, 'privacy-on');

  await privacy.locator('summary', { hasText: 'Details' }).click();
  await privacy
    .locator('summary', { hasText: 'What else Row-Bot contacts' })
    .click();
  for (const words of [
    'A random ID made just for this, the Row-Bot version',
    'Once a day while Row-Bot is running.',
    'row-bot-count.row-bot.workers.dev',
    'deleted after 35 days',
    'Cloudflare sees it, as it does for any connection. Row-Bot doesn’t store it; the stats service only adds one to its country’s count for the day, never next to the ID.',
    'ROW_BOT_INSTALL_COUNT=0',
    'Update checks',
    'Connection checks',
    'Catalogs',
  ])
    await expect(privacy).toContainText(words);
  await privacy.scrollIntoViewIfNeeded();
  await shoot(page, info, 'privacy-details');

  await toggle.click();
  await expect(toggle).not.toBeChecked();
  await expect(
    page.getByText('Install stats turned off').first(),
  ).toBeVisible();
  await privacy.locator('summary', { hasText: 'Details' }).click();
  await privacy
    .locator('summary', { hasText: 'What else Row-Bot contacts' })
    .click();
  await privacy.scrollIntoViewIfNeeded();
  await shoot(page, info, 'privacy-off');

  await privacy.locator('summary', { hasText: 'Details' }).click();
  await privacy.getByRole('button', { name: 'Delete what was sent…' }).click();
  const confirm = page.getByRole('alertdialog', {
    name: 'Delete what was sent?',
  });
  await expect(confirm).toContainText('turns install stats off');
  await shoot(page, info, 'delete-what-was-sent');
  await confirm.getByRole('button', { name: 'Delete' }).click();
  await expect(
    page.getByText('Deleted what was sent. Install stats are off.').first(),
  ).toBeVisible();
  await expect(
    privacy.getByRole('button', { name: 'Delete what was sent…' }),
  ).toHaveCount(0);
  const requests = await sent(page);
  expect(requests.map((request) => request.path)).toEqual([
    '/v1/ping',
    '/v1/delete',
  ]);
  expect(Object.keys(requests[0].body)).toEqual([
    'schema',
    'install',
    'version',
    'os',
  ]);
});
