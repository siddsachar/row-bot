import type { Page, TestInfo } from '@playwright/test';
import { accessibility, assertNoOverflow, expect, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';

// Learning (preview), L2a: the personal model's training. The fixture flips the start-up
// flag, fakes the hardware check and makes the fake trainer the training engine, driven
// step by step; nothing reaches a GPU, a download or a provider. Train now → progress →
// pause on use → it continues → Stop, then Row-Bot closing mid-run and running out of GPU
// memory, with Monitor's row and Buddy's line. Each test puts the profile back.

async function fixture(page: Page, path: string) {
  const response = await page.request.post(path, {
    headers: fixtureHeaders(),
  });
  expect(response.ok(), await response.text()).toBe(true);
}

const l1a = (page: Page, action: string) =>
  fixture(page, `/__l1a_fixture/learning/${action}`);
const l2a = (page: Page, action: string, steps = 0) =>
  fixture(
    page,
    `/__l2a_fixture/learning/${action}${steps ? `?steps=${steps}` : ''}`,
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
  await l2a(page, 'restore');
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

/** A notice by its words (not Radix's announcement copy of them). */
function notice(page: Page, text: string | RegExp) {
  return page.locator('.toast-message').filter({ hasText: text });
}

async function turnOn(page: Page) {
  await page.goto('/app-v2/settings/learning');
  await page.getByRole('button', { name: 'Turn on Learning' }).click();
  const sheet = page.getByRole('dialog', { name: 'Turn on Learning' });
  await expect(
    sheet.getByText('NVIDIA GeForce RTX 5070 Laptop (8 GB)'),
  ).toBeVisible();
  await sheet.getByRole('button', { name: 'Turn on Learning' }).click();
  const ask = page.getByRole('dialog', {
    name: 'Download the training engine and Qwen3.5 2B?',
  });
  await ask.getByRole('button', { name: 'Not now', exact: true }).click();
  await expect(notice(page, 'Learning is on.')).toBeVisible();
}

async function openModel(page: Page) {
  await page.goto('/app-v2/?tab=learning&view=model');
  await expect(
    page.getByRole('heading', { name: 'Learning', exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText('NVIDIA GeForce RTX 5070 Laptop (8 GB)'),
  ).toBeVisible();
}

/** The card's own words (the status region repeats some for screen readers). */
function card(page: Page) {
  return page.getByRole('region', { name: 'Training' });
}

const LINE = 'Learning: training your personal model';

/**
 * Buddy (the sidebar's, on a desktop) has caught up: its quiet line while training runs,
 * and something else once it stops. Buddy notices what runs every few seconds and the
 * sidebar reads Buddy when a page opens (then every 30 s), so the page is opened again.
 */
async function buddySays(page: Page, info: TestInfo, training: boolean) {
  if (phone(info)) return;
  const status = page
    .getByRole('complementary', { name: 'Buddy companion' })
    .getByRole('status');
  await expect(async () => {
    await page.reload();
    if (training) await expect(status).toHaveText(LINE, { timeout: 2_000 });
    else await expect(status).not.toHaveText(LINE, { timeout: 2_000 });
  }).toPass({ timeout: 30_000 });
  await expect(card(page)).toBeVisible();
  await expect(
    page.getByText('NVIDIA GeForce RTX 5070 Laptop (8 GB)'),
  ).toBeVisible();
}

test('Train now, progress, pause on use, it continues, Stop; Row-Bot closing mid-run and running out of GPU memory', async ({
  page,
}, info) => {
  test.setTimeout(180_000);
  await l1a(page, 'preview');
  await l1a(page, 'gpu');
  await turnOn(page);
  await l2a(page, 'engine');

  // A planned run: Train now.
  await openModel(page);
  await expect(
    card(page).getByText(/^Training v1 runs tonight in quiet hours/),
  ).toBeVisible();
  await expect(page.getByText('Installed', { exact: true })).toBeVisible();
  await card(page).getByRole('button', { name: 'Train now' }).click();
  await expect(notice(page, 'Training started.')).toBeVisible();

  // Progress you can follow, here, in Monitor and in Buddy.
  await l2a(page, 'advance', 340);
  const running =
    'Training v1 · step 340 of 1,000 · about 40 min left · pauses when you use Row-Bot';
  await expect(card(page).getByText(running)).toBeVisible();
  await expect(
    card(page).getByRole('progressbar', { name: 'Training progress' }),
  ).toHaveAttribute('value', '340');
  await buddySays(page, info, true);
  await shoot(page, info, 'model-training');

  await page.goto('/app-v2/?tab=monitor');
  const row = page
    .getByRole('listitem')
    .filter({ hasText: 'Personal model v1' });
  await expect(row.getByText('Training · step 340 of 1,000')).toBeVisible();
  await expect(
    row.getByRole('button', { name: 'Stop training personal model v1' }),
  ).toBeVisible();
  await row.evaluate((element) => element.scrollIntoView({ block: 'center' }));
  await shoot(page, info, 'monitor-training');

  if (phone(info))
    await page.getByRole('button', { name: 'Toggle navigation' }).click();
  const buddy = page.getByRole('complementary', { name: 'Buddy companion' });
  await expect(buddy.getByRole('status')).toHaveText(LINE, {
    timeout: 15_000,
  });
  await shoot(page, info, 'buddy-training');
  if (phone(info)) await page.keyboard.press('Escape');

  // Pause on use: "Pausing after this step…" until it really has paused.
  await openModel(page);
  await l2a(page, 'use');
  await expect(card(page).getByText('Pausing after this step…')).toBeVisible();
  await shoot(page, info, 'model-pausing');
  await l2a(page, 'step');
  await expect(
    card(page).getByText(
      'Training v1 paused at step 341 of 1,000 while you use Row-Bot.',
    ),
  ).toBeVisible();
  await expect(
    card(page).getByText('It continues when you’re done.'),
  ).toBeVisible();
  await buddySays(page, info, false);
  await shoot(page, info, 'model-paused');

  // The chat ended: Train now's run continues by itself.
  await l2a(page, 'free');
  await openModel(page);
  await expect(
    card(page).getByText(/^Training v1 · step 341 of 1,000/),
  ).toBeVisible();

  // Stop: it keeps its newest saved step and asks.
  await card(page).getByRole('button', { name: 'Stop' }).click();
  await expect(
    notice(page, 'Training stopped. It can continue from step 341.'),
  ).toBeVisible();
  await expect(
    card(page).getByText(
      'You stopped training v1. It can continue from step 341.',
    ),
  ).toBeVisible();
  await card(page).getByRole('button', { name: 'Continue' }).click();
  await expect(notice(page, 'Training continues from step 341.')).toBeVisible();

  // Row-Bot closed mid-run: on the next start it offers Continue or Discard.
  await l2a(page, 'closed');
  await openModel(page);
  await expect(
    card(page).getByText(
      'Training stopped when Row-Bot closed. It can continue from step 341.',
    ),
  ).toBeVisible();
  await expect(
    card(page).getByRole('button', { name: 'Discard' }),
  ).toBeVisible();
  await buddySays(page, info, false);
  await shoot(page, info, 'model-recovery');

  // Out of GPU memory: the reason in plain words and a way out.
  await l2a(page, 'oom');
  await openModel(page);
  await expect(
    card(page).getByText(
      'Training stopped: not enough GPU memory. A smaller starting model or shorter tasks fit. Earlier versions are kept.',
    ),
  ).toBeVisible();
  await buddySays(page, info, false);
  await shoot(page, info, 'model-out-of-memory');
  await card(page)
    .getByRole('button', { name: 'Use recommended settings' })
    .click();
  await expect(
    notice(page, 'Training will use the recommended settings next time.'),
  ).toBeVisible();
  await expect(
    card(page).getByText(/^Training v1 runs tonight in quiet hours/),
  ).toBeVisible();
});
