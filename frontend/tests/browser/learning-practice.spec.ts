import type { Page, TestInfo } from '@playwright/test';
import { accessibility, assertNoOverflow, expect, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';

// Learning (preview), L4b: practice and the quick question. The fixture switches this build's practice and quick
// question on, writes a night that practised (12 made-up document tasks, 9 passed; coding practice waiting for a
// container) and a quick question; nothing reaches a model. The Overview's Overnight line, the question in Needs
// you with its two answers and ✕, answering it, and waving it away.

async function fixture(page: Page, path: string) {
  const response = await page.request.post(path, {
    headers: fixtureHeaders(),
  });
  expect(response.ok(), await response.text()).toBe(true);
}

const l1a = (page: Page, action: string) =>
  fixture(page, `/__l1a_fixture/learning/${action}`);
const l4b = (page: Page, action: string) =>
  fixture(page, `/__l4b_fixture/learning/${action}`);

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
  await l4b(page, 'restore');
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

async function turnOn(page: Page) {
  await l1a(page, 'preview');
  await l1a(page, 'gpu');
  await page.goto('/app-v2/settings/learning');
  await page.getByRole('button', { name: 'Turn on Learning' }).click();
  const sheet = page.getByRole('dialog', { name: 'Turn on Learning' });
  // The graphics card is read first: turning on before it is known skips the download ask.
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

async function open(page: Page) {
  await page.goto('/app-v2/?tab=learning&view=overview');
  await expect(
    page.getByRole('heading', { name: 'Learning', exact: true }),
  ).toBeVisible();
}

test('a night that practised, and a quick question answered in Needs you', async ({
  page,
}, info) => {
  await turnOn(page);
  await l4b(page, 'on');
  await l4b(page, 'practised');
  await l4b(page, 'question');
  await open(page);

  const night = page.getByRole('region', { name: 'Overnight' });
  await expect(
    night.getByText('Practised 12 document tasks it made up: 9 passed'),
  ).toBeVisible();
  await expect(
    night.getByText(
      'Coding practice needs Docker or Podman, so none was made.',
    ),
  ).toBeVisible();
  const needs = page.getByRole('region', { name: 'Needs you' });
  await expect(
    needs.getByText(
      'Quick question: When two files share a name, which should I open?',
    ),
  ).toBeVisible();
  await expect(
    needs.getByRole('button', { name: 'Don’t ask this again' }),
  ).toBeVisible();
  await shoot(page, info, 'overview-practice-question');

  await needs.getByRole('button', { name: 'The newest' }).click();
  await expect(
    notice(page, 'Thanks. Row-Bot will learn from this.'),
  ).toBeVisible();
  await expect(needs.getByText(/^Quick question:/)).toHaveCount(0);
  await shoot(page, info, 'overview-question-answered');
});

test('waving the quick question away never asks it again', async ({ page }) => {
  await turnOn(page);
  await l4b(page, 'on');
  await l4b(page, 'question');
  await open(page);

  const needs = page.getByRole('region', { name: 'Needs you' });
  await needs.getByRole('button', { name: 'Don’t ask this again' }).click();
  await expect(notice(page, 'Row-Bot won’t ask that again.')).toBeVisible();
  await expect(needs.getByText(/^Quick question:/)).toHaveCount(0);
  await expect(
    needs.getByText(
      'Nothing needs you. Changes that are ready to review show here.',
    ),
  ).toBeVisible();
});
