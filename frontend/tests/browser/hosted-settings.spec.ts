import type { Page } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';

// A hosted Row-Bot's own Settings: System names its host with the version,
// memory and the host's pages (U5); Data downloads a fresh backup first and
// says what that backup leaves out (U9, F3); Devices offers the pod's own address,
// with no way to choose how it is reached. The fixture switches make the
// handshake say "hosted", give the server a 2 GB memory limit, and save a
// pod's https address (the fixture's own is loopback). On Railway (R1) the same
// pages use Railway's words: a service, which its owner updates there.

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
  await fixture(page, '/__r2_fixture/railway-ids/off');
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

  // Data: Download backup comes first; it makes a fresh backup and saves it on
  // this device. Back up now keeps its copy on the server, and says so (F3).
  await page.goto('/app-v2/settings/data');
  await expect(
    page.getByText(
      'Makes a fresh backup and saves it on this device. To move Row-Bot to your computer, install Row-Bot there, open Settings › Data and choose Restore from backup.',
    ),
  ).toBeVisible();
  await expect(
    page.getByText('PikaPods can also keep its own backups of your server.'),
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

test('on Railway, System and Updates speak Railway’s words: a service its owner updates (R1)', async ({
  page,
}, testInfo) => {
  await fixture(page, '/__r1_fixture/host/railway');
  await fixture(page, '/__a7_fixture/place/hosted');
  // Railway sets limits in decimal GB: 8,000,000,000 bytes reads 8 GB, not 7.5 (F1).
  await fixture(page, '/__a8_fixture/memory/8?unit=gb');

  await page.goto('/app-v2/settings/system');
  const card = page.getByRole('region', { name: 'Hosted by Railway' });
  await expect(
    card.getByText(/^Row-Bot .+ · updates come from Railway$/),
  ).toBeVisible();
  await expect(card.getByText('Memory 2.0 GB of 8 GB')).toBeVisible();
  for (const [name, url] of [
    ['Manage your service in Railway', 'https://railway.com/dashboard'],
    ['Railway support', 'https://station.railway.com/'],
    ['Railway status', 'https://status.railway.com/'],
  ])
    await expect(card.getByRole('link', { name })).toHaveAttribute('href', url);
  await expect(card).toContainText('Railway runs the server and billing.');
  await assertNoOverflow(page);
  await screenshot(page, testInfo, 'railway-system');

  await page.goto('/app-v2/settings/updates');
  await expect(page.getByText('Updates come from Railway')).toBeVisible();
  await expect(
    page.getByText(
      'New versions install by themselves each night when automatic updates are on in Railway.',
    ),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: /Check|Update/ })).toHaveCount(
    0,
  );
  await assertNoOverflow(page);
  await screenshot(page, testInfo, 'railway-updates');

  // With the IDs Railway gives every service, links land on its own pages (A2).
  await fixture(page, '/__r2_fixture/railway-ids/on');
  await page.goto('/app-v2/settings/system');
  const service =
    /^https:\/\/railway\.com\/project\/[0-9a-f-]{36}\/service\/[0-9a-f-]{36}/;
  for (const [name, url] of [
    ['Manage your service in Railway', service],
    ['Usage in Railway', /^https:\/\/railway\.com\/workspace\/usage$/],
  ] as const)
    await expect(card.getByRole('link', { name })).toHaveAttribute('href', url);
  // Railway backs up volumes only on its Pro plan (F3): no Backups button, and
  // Data says so.
  await expect(card.getByRole('link', { name: /Backups/ })).toHaveCount(0);
  await page.goto('/app-v2/settings/data');
  await expect(
    page.getByText(
      'Railway backs up your server only on its Pro plan. Download a backup from time to time to keep a copy somewhere else.',
    ),
  ).toBeVisible();
  await expect(page.getByRole('link', { name: /Backups in/ })).toHaveCount(0);
  await assertNoOverflow(page);
  // Updates: where Railway's automatic updates are switched on (A5).
  await page.goto('/app-v2/settings/updates');
  await expect(
    page.getByRole('link', { name: 'Check in Railway' }),
  ).toHaveAttribute('href', /\/settings\?environmentId=[0-9a-f-]{36}$/);
  await assertNoOverflow(page);
});

test('the desktop shows no host card or Download a backup row, and its restore takes a hosted backup', async ({
  page,
}) => {
  await page.goto('/app-v2/settings/system');
  await expect(page.getByRole('region', { name: 'Workspace' })).toBeVisible();
  await expect(page.getByText(/Hosted by/)).toHaveCount(0);
  await page.goto('/app-v2/settings/data');
  await expect(
    page.getByText(/A backup from a hosted Row-Bot works too\./),
  ).toBeVisible();
  await expect(page.getByText('Download a backup')).toHaveCount(0);
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
