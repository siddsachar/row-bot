import type { Page, TestInfo } from '@playwright/test';
import { accessibility, assertNoOverflow, expect, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';

// Learning (preview), O124: moving an existing setup to a larger certified starting model. The fixture
// fakes an RTX 5090 (the 32 GB tier: Qwen3.5 9B) on a setup made on a stand-in smaller certified model
// (the 9B is the only one since T1; Part 4 brings other families), and stands in for the download; nothing reaches the network, a GPU or a provider. The Personal model view offers the
// stronger model once (Switch… or Dismiss); Settings › Learning › Starting model › Change… lists the
// card's certified models, asks with the download and the free space, then switches and downloads.

async function fixture(page: Page, path: string) {
  const response = await page.request.post(path, {
    headers: fixtureHeaders(),
  });
  expect(response.ok(), await response.text()).toBe(true);
}

const l1a = (page: Page, action: string) =>
  fixture(page, `/__l1a_fixture/learning/${action}`);
const l2x = (page: Page, action: string, name = '') =>
  fixture(
    page,
    `/__l2x_fixture/learning/${action}${name ? `?name=${name}` : ''}`,
  );

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    if (window !== window.top) return;
    localStorage.setItem(
      'row-bot.appearance.v1',
      JSON.stringify({
        version: 1,
        appearance: 'system',
        accent: 'blue',
        density: 'compact',
        reduce_transparency: false,
      }),
    );
  });
});

test.afterEach(async ({ page }) => {
  // Close the app first: restoring turns Learning's start-up flag off, and a
  // poll still due (download, training) would get a 404 a real app never sees.
  await page.close();
  await l2x(page, 'restore');
  await l1a(page, 'restore');
});

const phone = (info: TestInfo) =>
  (info.project.use.viewport?.width ?? 1440) < 700;

/** `<surface>-<state>-{desktop,phone}-{light,dark}.png`, each with an axe scan. */
async function shoot(page: Page, info: TestInfo, name: string) {
  const size = phone(info) ? 'phone' : 'desktop';
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    const file = info.outputPath(`${name}-${size}-${appearance}.png`);
    await page.screenshot({
      path: file,
      fullPage: true,
      animations: 'disabled',
    });
    await info.attach(`${name}-${size}-${appearance}`, {
      path: file,
      contentType: 'image/png',
    });
    await accessibility(page, info, `${name}-${size}-${appearance}`);
  }
  await page.emulateMedia({ colorScheme: 'light' });
}

function notice(page: Page, text: string | RegExp) {
  return page.locator('.toast-message').filter({ hasText: text });
}

/** Learning on, on an RTX 5090, with the starting model set to the stand-in smaller model (a setup made before). */
async function onTheSmallModel(page: Page) {
  await l1a(page, 'preview');
  await l2x(page, 'two-models');
  await l2x(page, 'card', '5090');
  await page.goto('/app-v2/settings/learning');
  await page.getByRole('button', { name: 'Turn on Learning' }).click();
  const sheet = page.getByRole('dialog', { name: 'Turn on Learning' });
  // The card's check has answered (setup then offers its download).
  await expect(
    sheet.getByText('NVIDIA GeForce RTX 5090 (32 GB)'),
  ).toBeVisible();
  await sheet.getByRole('button', { name: 'Turn on Learning' }).click();
  await page
    .getByRole('dialog', { name: /^Download the training engine and / })
    .getByRole('button', { name: 'Not now', exact: true })
    .click();
  await expect(notice(page, 'Learning is on.')).toBeVisible();
  await l2x(page, 'starting-model', 'test-small');
}

function offer(page: Page) {
  return page.getByRole('status', { name: 'A stronger starting model' });
}

async function openModel(page: Page) {
  await page.goto('/app-v2/?tab=learning&view=model');
  await expect(
    page.getByRole('heading', { name: 'Learning', exact: true }),
  ).toBeVisible();
}

test('Settings › Starting model › Change… moves a smaller model’s setup to the 9B the card is certified for (O124)', async ({
  page,
}, info) => {
  await onTheSmallModel(page);
  await page.goto('/app-v2/settings/learning');
  await page.getByRole('button', { name: 'Change the starting model' }).click();
  const sheet = page.getByRole('dialog', { name: 'Starting model' });
  const nine = sheet.getByRole('radio', { name: /^Qwen3\.5 9B · recommended/ });
  await expect(nine).toBeVisible();
  await expect(
    sheet.getByRole('radio', { name: /^A small model · in use/ }),
  ).toBeChecked();
  await shoot(page, info, 'settings-starting-model-change');

  await nine.check();
  await sheet.getByRole('button', { name: 'Use Qwen3.5 9B…' }).click();
  const ask = page.getByRole('dialog', { name: 'Switch to Qwen3.5 9B?' });
  await expect(ask).toContainText(
    /Row-Bot downloads Qwen3\.5 9B \(.+\) from Hugging Face and the training engine \(.+\), then trains/,
  );
  await expect(ask).toContainText('free on this computer.');
  await shoot(page, info, 'settings-starting-model-ask');

  await ask.getByRole('button', { name: 'Download and switch' }).click();
  await expect(notice(page, /^Switched to Qwen3\.5 9B\./)).toBeVisible();
  await expect(
    page.getByText(/^Qwen3\.5 9B · Apache 2\.0 · from Hugging Face/),
  ).toBeVisible();
  await shoot(page, info, 'settings-starting-model-switched');

  // The Personal model view's offer is answered by the switch.
  await openModel(page);
  await expect(page.getByText('Qwen3.5 9B · Apache 2.0').first()).toBeVisible();
  await expect(offer(page)).toHaveCount(0);
});

test('the Personal model view offers the stronger model once: Switch… asks, Dismiss hides it for good (O124)', async ({
  page,
}, info) => {
  await onTheSmallModel(page);
  await openModel(page);
  await expect(offer(page)).toContainText(
    'Your graphics card can now train Qwen3.5 9B, a stronger starting model.',
  );
  await shoot(page, info, 'model-stronger-offer');

  await offer(page)
    .getByRole('button', { name: /^Switch… \(downloads .+\)$/ })
    .click();
  const ask = page.getByRole('dialog', { name: 'Switch to Qwen3.5 9B?' });
  await expect(
    ask.getByRole('button', { name: 'Download and switch' }),
  ).toBeEnabled();
  await shoot(page, info, 'model-stronger-ask');
  await page.keyboard.press('Escape');
  await expect(ask).toHaveCount(0);

  await offer(page)
    .getByRole('button', { name: 'Don’t suggest Qwen3.5 9B again' })
    .click();
  await expect(offer(page)).toHaveCount(0);
  await openModel(page);
  await expect(offer(page)).toHaveCount(0);
});
