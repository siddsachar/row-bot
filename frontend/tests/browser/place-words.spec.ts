import type { Page } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';

// U7: Row-Bot says "this computer" only when the browser is on the computer it
// runs on. A phone reaching a desktop Row-Bot, or any browser of a hosted one,
// reads "your Row-Bot server", named after its host on first use. The fixture
// switch makes the handshake say what such a browser is told.

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
  await fixture(page, '/__a7_fixture/place/this_computer');
});

test('says where Row-Bot runs from the browser’s side: this computer, a hosted server, a server', async ({
  page,
}, testInfo) => {
  const searchRuns = page.getByRole('radiogroup', { name: 'Search runs' });
  await page.goto('/app-v2/settings/documents');
  await expect(
    searchRuns.getByRole('radio', { name: 'This computer' }),
  ).toBeVisible();
  await expect(
    page.getByText('On this computer, your documents never leave it.'),
  ).toBeVisible();

  await fixture(page, '/__a7_fixture/place/hosted');
  await page.reload();
  await expect(
    searchRuns.getByRole('radio', { name: 'Your Row-Bot server' }),
  ).toBeVisible();
  await expect(
    page.getByText(
      'On your Row-Bot server at PikaPods, your documents never leave it.',
    ),
  ).toBeVisible();
  await page.goto('/app-v2/settings/voice');
  await expect(
    page.getByText(
      'On your Row-Bot server at PikaPods keeps your voice private.',
    ),
  ).toBeVisible();
  await expect(page.getByText(/this computer/)).toHaveCount(0);
  await assertNoOverflow(page);
  await screenshot(page, testInfo, 'place-hosted-voice');

  await fixture(page, '/__a7_fixture/place/server');
  await page.goto('/app-v2/settings/documents');
  await expect(
    page.getByText(
      'On the computer Row-Bot runs on, your documents never leave it.',
    ),
  ).toBeVisible();
  await expect(page.getByText(/this computer/)).toHaveCount(0);
  await screenshot(page, testInfo, 'place-server-documents');
});
