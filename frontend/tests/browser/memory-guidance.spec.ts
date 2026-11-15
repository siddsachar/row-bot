import type { Page } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';

// U8: on a server with a memory limit, Row-Bot says what fits. After a
// restart for lack of memory, Home's Needs you and Monitor say why and how to
// resize; on a server under 3.5 GB, Voice marks local speech-to-text and asks
// before it is chosen, and Documents suggests freeing the search model's
// memory. The fixture switches give the server a fake cgroup's limit and make
// the real start-up check find a run that ran out of memory.

async function fixture(page: Page, path: string) {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated browser runner');
  const response = await page.request.post(path, {
    headers: { 'X-Fixture-Token': token, Origin: new URL(base).origin },
  });
  expect(response.ok(), await response.text()).toBe(true);
}

const TITLE = 'Row-Bot restarted because the server ran out of memory';

/** Listen with, and the moment its choice is saved: a saved cloud choice can be reset to local. */
function listenWith(page: Page) {
  const select = page.getByLabel('Listen with', { exact: true });
  const reset = page.getByRole('button', {
    name: 'Reset Listen with to default',
  });
  return {
    select,
    cloudSaved: () => expect(reset).toBeVisible(),
    localSaved: () => expect(reset).toHaveCount(0),
  };
}

test.afterEach(async ({ page }) => {
  await fixture(page, '/__a8_fixture/memory/off');
  await fixture(page, '/__a7_fixture/place/this_computer');
});

test('a 2 GB hosted server says why Row-Bot restarted, what fits and how to resize', async ({
  page,
}, testInfo) => {
  await fixture(page, '/__a7_fixture/place/hosted');
  await fixture(page, '/__a8_fixture/memory/2');
  await fixture(page, '/__a8_fixture/oom');
  const listen = listenWith(page);

  await page.goto('/app-v2/');
  const needs = page.getByRole('list', { name: 'Needs you' });
  await expect(needs.getByText(TITLE)).toBeVisible();
  await expect(
    needs.getByRole('link', { name: 'Manage your pod in PikaPods' }),
  ).toHaveAttribute('href', 'https://www.pikapods.com/pods');
  await assertNoOverflow(page);
  await screenshot(page, testInfo, 'memory-home');

  await page.goto('/app-v2/?tab=monitor');
  const attention = page.getByRole('region', { name: 'Needs attention' });
  await expect(attention).toContainText(
    'Your server has 2 GB. Local speech-to-text together with the search model and browser needs about 3.2 GB. Make the pod 4 GB in PikaPods, or use cloud speech-to-text.',
  );
  await assertNoOverflow(page);
  await screenshot(page, testInfo, 'memory-monitor');

  // Voice: local speech-to-text is marked, and choosing it asks first.
  await attention
    .getByRole('link', { name: 'Use cloud speech-to-text' })
    .click();
  await expect(page).toHaveURL(/\/settings\/voice/);
  await expect(page.getByText('Needs a 4 GB server').first()).toBeVisible();
  await listen.select.selectOption('openai_realtime');
  await listen.cloudSaved();
  await listen.select.selectOption('local');
  const sheet = page.getByRole('dialog', { name: 'Use local speech-to-text?' });
  await expect(sheet).toContainText(
    'This server has 2 GB. Local speech-to-text may make Row-Bot restart.',
  );
  await screenshot(page, testInfo, 'memory-voice-sheet');
  await sheet.getByRole('button', { name: 'Use cloud speech-to-text' }).click();
  await expect(sheet).toBeHidden();
  await expect(listen.select).toHaveValue('openai_realtime');
  // The person's choice stands: local, as it was before this test.
  await listen.select.selectOption('local');
  await sheet.getByRole('button', { name: 'Use local anyway' }).click();
  await listen.localSaved();
  await expect(listen.select).toHaveValue('local');
  await expect(page.getByText('Needs a 4 GB server')).toHaveCount(2);

  // Documents: the search model's memory is given back when idle.
  await page.goto('/app-v2/settings/documents');
  await expect(page.getByText('Suggested on a 2 GB server')).toBeVisible();
  await expect(
    page.getByLabel('Free memory when idle', { exact: true }),
  ).not.toBeChecked();

  // Dismissed on Home, it stays away.
  await page.goto('/app-v2/');
  await needs.getByRole('button', { name: 'Dismiss' }).click();
  await expect(page.getByText(TITLE)).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole('heading', { level: 2 })).toBeVisible();
  await expect(page.getByText(TITLE)).toHaveCount(0);
});

test('the desktop shows no memory notice, pill or sheet', async ({ page }) => {
  const listen = listenWith(page);
  await page.goto('/app-v2/settings/voice');
  await expect(listen.select).toBeVisible();
  await expect(page.getByText('Needs a 4 GB server')).toHaveCount(0);
  await listen.select.selectOption('openai_realtime');
  await listen.cloudSaved();
  await listen.select.selectOption('local');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await listen.localSaved();
  await page.goto('/app-v2/');
  await expect(page.getByText(TITLE)).toHaveCount(0);
});
