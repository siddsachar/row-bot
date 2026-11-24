import { existsSync, readFileSync } from 'node:fs';
import { homeOpened } from './phone-helpers';
import { fileURLToPath } from 'node:url';
import type { Locator, Page } from '@playwright/test';
import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';

// E1, On this computer: Setup lists the model servers already running (found
// on loopback when the page opens), then every way to run models locally as
// equal rows. Row-Bot's own download is one of them: it offers the model that
// fits this computer (a synthetic 12 GB NVIDIA card), names the size on its
// button, shows the download's progress and then the ready model, which
// becomes the default like any other pick. The fixture fakes the runtime and
// the found servers: nothing is downloaded, no port is asked, no llama.cpp runs.

async function fixture(page: Page, path: string) {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated Phase 4 runner');
  const response = await page.request.post(path, {
    headers: { 'X-Fixture-Token': token, Origin: new URL(base).origin },
  });
  expect(response.ok(), await response.text()).toBe(true);
}

/**
 * The owner's review shots (desktop 1280x900 and phone 375x812, light and
 * dark), only on a machine where `.tmp/e1-screens.flag` names their folder.
 */
async function reviewShots(page: Page, name: string, focus?: Locator) {
  const flag = fileURLToPath(
    new URL('../../../.tmp/e1-screens.flag', import.meta.url),
  );
  const folder = existsSync(flag) ? readFileSync(flag, 'utf8').trim() : '';
  if (!folder) return;
  const before = page.viewportSize();
  for (const [size, width, height] of [
    ['desktop', 1280, 900],
    ['phone', 375, 812],
  ] as const) {
    await page.setViewportSize({ width, height });
    for (const scheme of ['light', 'dark'] as const) {
      await page.emulateMedia({ colorScheme: scheme });
      await focus?.scrollIntoViewIfNeeded();
      await page.waitForTimeout(150);
      await page.screenshot({
        path: `${folder}/${name}-${size}-${scheme}.png`,
        fullPage: true,
        animations: 'disabled',
      });
    }
  }
  await page.emulateMedia({ colorScheme: null });
  if (before) await page.setViewportSize(before);
}

test.afterEach(async ({ page }) => {
  await fixture(page, '/__e1_fixture/local-runtime/restore');
  await fixture(page, '/__p10_fixture/first-run/restore');
});

test('On this computer: what is running first, every way to run models as an equal row, and Row-Bot’s download in one of them', async ({
  page,
}, testInfo) => {
  await fixture(page, '/__e1_fixture/local-runtime/fresh?found=lmstudio');
  await fixture(page, '/__p10_fixture/first-run/fresh?runtime=not_installed');
  await page.goto('/app-v2/');
  await expect(page).toHaveURL(/\/app-v2\/setup$/);
  const choices = page.getByRole('group', {
    name: 'How should Row-Bot think?',
  });
  await reviewShots(page, '1-setup-choice');

  await choices.getByRole('button', { name: /On this computer/ }).click();
  const found = page.getByRole('region', { name: 'Found on this computer' });
  await expect(found).toContainText('LM Studio');
  await expect(
    found.getByRole('list', { name: 'LM Studio models' }),
  ).toContainText('qwen3.8-27b');
  const ways = page.getByRole('list', {
    name: 'Ways to run models on this computer',
  });
  await expect(ways.getByRole('listitem')).toHaveCount(5);
  await expect(ways.locator('.primary')).toHaveCount(0);
  await screenshot(page, testInfo, 'local-page');
  await accessibility(page, testInfo, 'local-page-axe');
  await assertNoOverflow(page);
  const panel = page.locator('.first-run-panel').first();
  await reviewShots(page, '2-on-this-computer', panel);

  await ways
    .getByRole('button', { name: 'Set up Download with Row-Bot (llama.cpp)' })
    .click();
  await expect(
    page.getByText(
      'Qwen3.8 27B · 2-bit · fits your NVIDIA GeForce RTX 4070 (12 GB) · reads 32K · 10.6 GB download',
    ),
  ).toBeVisible();
  const row = page.locator('[data-way="row_bot"]');
  await reviewShots(page, '3-download-consent-with-size', row);

  // Every pinned model, with how it would run here.
  await page.getByRole('button', { name: 'Choose another model' }).click();
  const list = page.getByRole('list', { name: 'Models for this computer' });
  await expect(list).toContainText('smaller, older generation');
  await reviewShots(page, '3b-choose-another-model', list);
  await list.getByRole('button', { name: /Qwen3.8 27B · 2-bit/ }).click();

  await page.getByRole('button', { name: 'Download 10.6 GB' }).click();
  await expect(
    page
      .getByRole('status')
      .filter({ hasText: 'Downloading Qwen3.8 27B · 2-bit' }),
  ).toContainText('4.2 GB of 10.6 GB');
  await expect(
    page.getByRole('button', { name: 'Stop download; it resumes later' }),
  ).toBeVisible();
  await fixture(page, '/__e1_fixture/local-runtime/downloading?done=6.8');
  await expect(page.getByText(/6\.8 GB of 10\.6 GB/)).toBeVisible();
  await screenshot(page, testInfo, 'downloading');
  await assertNoOverflow(page);
  await reviewShots(page, '4-download-progress', row);

  await fixture(page, '/__e1_fixture/local-runtime/installed');
  await expect(
    page.getByText('Qwen3.8 27B · 2-bit is on this computer and ready.'),
  ).toBeVisible({ timeout: 10_000 });
  await reviewShots(page, '5-ready', row);
  await page.getByRole('button', { name: 'Use Qwen3.8 27B · 2-bit' }).click();
  await expect(homeOpened(page)).toBeVisible({ timeout: 15_000 });
});

test('on a phone, a long model name truncates in its row and the page never scrolls sideways (W2)', async ({
  page,
}, testInfo) => {
  await fixture(page, '/__e1_fixture/local-runtime/fresh?found=lmstudio');
  await fixture(page, '/__p10_fixture/first-run/fresh?runtime=not_installed');
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/app-v2/');
  await page
    .getByRole('group', { name: 'How should Row-Bot think?' })
    .getByRole('button', { name: /On this computer/ })
    .click();
  const models = page.getByRole('list', { name: 'LM Studio models' });
  const long = models.getByRole('button', { name: /mistral-small-3\.3-24b/ });
  await expect(long).toBeVisible();
  const sizes = await page.evaluate(() => {
    const view = document.querySelector('.first-run')!;
    const name = [
      ...document.querySelectorAll<HTMLElement>(
        '.first-run-model > span:first-child',
      ),
    ].find((item) => item.textContent?.startsWith('mistral-small'))!;
    return {
      page: [view.scrollWidth, view.clientWidth],
      name: [name.scrollWidth, name.clientWidth],
      parents: [...document.querySelectorAll<HTMLElement>('main, .first-run')]
        .map((item) => item.scrollWidth - item.clientWidth)
        .filter((extra) => extra > 1),
    };
  });
  expect(sizes.page[0]).toBeLessThanOrEqual(sizes.page[1] + 1);
  expect(sizes.parents).toEqual([]);
  // The name is cut with an ellipsis inside its row.
  expect(sizes.name[0]).toBeGreaterThan(sizes.name[1]);
  await assertNoOverflow(page);
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await long.scrollIntoViewIfNeeded();
    await screenshot(page, testInfo, `long-model-name-phone-${scheme}`);
  }
  await page.emulateMedia({ colorScheme: null });
});

test('Use on a found server’s model connects it and makes the model the default', async ({
  page,
}) => {
  await fixture(page, '/__e1_fixture/local-runtime/fresh?found=lmstudio');
  await fixture(page, '/__p10_fixture/first-run/fresh?runtime=not_installed');
  await page.goto('/app-v2/');
  await page
    .getByRole('group', { name: 'How should Row-Bot think?' })
    .getByRole('button', { name: /On this computer/ })
    .click();
  await page
    .getByRole('list', { name: 'LM Studio models' })
    .getByRole('button', { name: /qwen3\.8-27b/ })
    .click();
  await expect(homeOpened(page)).toBeVisible({ timeout: 15_000 });
});

test('Ollama running with models shows under Found, beside the other ways', async ({
  page,
}) => {
  await fixture(page, '/__e1_fixture/local-runtime/fresh');
  await fixture(
    page,
    '/__p10_fixture/first-run/fresh?runtime=running&models=qwen3.8:27b',
  );
  await page.goto('/app-v2/');
  await page
    .getByRole('group', { name: 'How should Row-Bot think?' })
    .getByRole('button', { name: /On this computer/ })
    .click();
  const found = page.getByRole('region', { name: 'Found on this computer' });
  await expect(found).toContainText('Ollama');
  await expect(
    found.getByRole('button', { name: /qwen3\.8:27b/ }),
  ).toBeVisible();
  await expect(
    page.getByRole('list', { name: 'Ways to run models on this computer' }),
  ).toContainText('Ollama is running');
});

test('Settings › Models › Local lists Row-Bot’s download among the local runtimes; Remove asks first and says what it freed', async ({
  page,
}, testInfo) => {
  await fixture(page, '/__e1_fixture/local-runtime/installed');
  await page.goto('/app-v2/settings/models');
  const group = page.locator('[data-setting-anchor="local-models"]');
  await expect(group).toContainText('Download with Row-Bot (llama.cpp)');
  await expect(group).toContainText('Ready · 1 model');
  await expect(
    group.getByRole('list', { name: 'Models Row-Bot downloaded' }),
  ).toContainText('Qwen3.8 27B · 2-bit');
  await group.scrollIntoViewIfNeeded();
  await screenshot(page, testInfo, 'settings');
  await assertNoOverflow(page);
  await reviewShots(page, '6-settings-local', group);

  await group
    .getByRole('button', { name: 'Add a way to run models locally' })
    .click();
  await expect(
    group.getByRole('list', { name: 'Ways to run models on this computer' }),
  ).toBeVisible();
  await reviewShots(page, '6b-settings-local-add', group);
  await group
    .getByRole('button', { name: 'Add a way to run models locally' })
    .click();

  await group
    .getByRole('button', { name: 'Remove Row-Bot’s download and its models' })
    .click();
  const dialog = page.getByRole('alertdialog', {
    name: 'Remove Row-Bot’s download and its models?',
  });
  await expect(dialog).toContainText('Other local runtimes aren’t touched');
  await reviewShots(page, '6c-settings-remove-confirm');
  await dialog.getByRole('button', { name: 'Remove' }).click();
  await expect(group).toContainText(/Removed\. .* freed\./);
});
