import type { Page } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';

// A hosted Row-Bot's own Settings: System names its host with the version,
// memory and the host's pages (U5); Data moves Row-Bot with a fresh backup and
// says what that backup leaves out (U9); Devices offers the pod's own address,
// with no way to choose how it is reached. The fixture switches make the
// handshake say "hosted", give the server a 2 GB memory limit, and save a
// pod's https address (the fixture's own is loopback).

async function fixture(page: Page, path: string) {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated browser runner');
  const response = await page.request.post(path, {
    headers: { 'X-Fixture-Token': token, Origin: new URL(base).origin },
  });
  expect(response.ok(), await response.text()).toBe(true);
}

test.afterEach(async ({ page }) => {
  await fixture(page, '/__a9_fixture/pod-address/off');
  await fixture(page, '/__a8_fixture/memory/off');
  await fixture(page, '/__a7_fixture/place/this_computer');
});

test('a hosted Row-Bot names its host, moves with a backup and connects devices at its own address', async ({
  page,
}, testInfo) => {
  await fixture(page, '/__a7_fixture/place/hosted');
  await fixture(page, '/__a8_fixture/memory/2');

  await page.goto('/app-v2/settings/system');
  const card = page.getByRole('region', { name: 'Hosted by PikaPods' });
  await expect(
    card.getByText(/^Row-Bot .+ · updated by PikaPods$/),
  ).toBeVisible();
  await expect(card.getByText('Memory 0.5 GB of 2 GB')).toBeVisible();
  for (const [name, url] of [
    ['Manage your pod in PikaPods', 'https://www.pikapods.com/pods'],
    ['PikaPods support', 'https://docs.pikapods.com/'],
    [
      'PikaPods status',
      'https://docs.pikapods.com/faq/account#business-use-and-service-level',
    ],
  ])
    await expect(card.getByRole('link', { name })).toHaveAttribute('href', url);
  await assertNoOverflow(page);
  await screenshot(page, testInfo, 'hosted-system');

  // Data: Move Row-Bot makes a fresh backup and saves it on this device.
  await page.goto('/app-v2/settings/data');
  await expect(
    page.getByText(
      'Download a backup, then install Row-Bot on your computer, open Settings › Data and choose Restore from backup.',
    ),
  ).toBeVisible();
  const saved = page.waitForEvent('download');
  const move = page.getByRole('button', { name: 'Download backup' });
  await move.click();
  const ready = page.getByText(
    /is ready\. Choose Download backup to save it\./,
  );
  const done = page.getByText(/^Downloaded “Row-Bot backup .+\.zip”\.$/);
  await expect(ready.or(done)).toBeVisible({ timeout: 60_000 });
  // A long backup outlives the click: the browser saves it on the next one.
  if (await ready.isVisible()) await move.click();
  expect((await saved).suggestedFilename()).toMatch(/^Row-Bot backup .+\.zip$/);
  await expect(done).toBeVisible();
  await expect(
    page.getByText(
      /^Not in the backup: API keys, sign-in tokens and passwords/,
    ),
  ).toBeVisible();
  await assertNoOverflow(page);
  await screenshot(page, testInfo, 'hosted-data-move');

  // Devices: the code opens the pod's own address; nothing sets how it's reached.
  await fixture(page, '/__a9_fixture/pod-address/on');
  await page.goto('/app-v2/settings/access');
  await page
    .getByRole('button', { name: 'Connect a phone or computer' })
    .click();
  await expect(
    page.getByRole('img', { name: 'QR code to connect a phone or computer' }),
  ).toBeVisible();
  await expect(page.getByText('row-bot-demo.pikapod.net')).toBeVisible();
  await expect(
    page.getByRole('radiogroup', { name: /How it reaches/ }),
  ).toHaveCount(0);
  await expect(
    page.getByText('Network, allowed addresses and the public link'),
  ).toHaveCount(0);
  await assertNoOverflow(page);
  await screenshot(page, testInfo, 'hosted-devices');
  // Closing cancels the open code.
  await page
    .getByRole('button', { name: 'Close connect a phone or computer' })
    .click();
});

test('the desktop shows no host card or Move row, and its restore takes a hosted backup', async ({
  page,
}) => {
  await page.goto('/app-v2/settings/system');
  await expect(page.getByRole('region', { name: 'Workspace' })).toBeVisible();
  await expect(page.getByText(/Hosted by/)).toHaveCount(0);
  await page.goto('/app-v2/settings/data');
  await expect(
    page.getByText(/A backup from a hosted Row-Bot works too\./),
  ).toBeVisible();
  await expect(page.getByText('Move Row-Bot')).toHaveCount(0);
  await page.goto('/app-v2/settings/access');
  await page
    .getByRole('button', { name: 'Connect a phone or computer' })
    .click();
  await expect(
    page.getByRole('radiogroup', { name: /How it reaches/ }),
  ).toBeVisible();
  await page
    .getByRole('button', { name: 'Close connect a phone or computer' })
    .click();
});
