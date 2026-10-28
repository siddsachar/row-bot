import type { Page, TestInfo } from '@playwright/test';
import { accessibility, assertNoOverflow, expect, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';

// Learning (preview), L4c: the personal model helps learning. The fixture switches this build's jobs (and practice)
// on and writes who does each job: drafting by personal model v3 since its test, making practice tasks back with
// the main model after a test that wasn't close enough. Nothing reaches a model. How Row-Bot learns › Jobs.

async function fixture(page: Page, path: string) {
  const response = await page.request.post(path, {
    headers: fixtureHeaders(),
  });
  expect(response.ok(), await response.text()).toBe(true);
}

const l1a = (page: Page, action: string) =>
  fixture(page, `/__l1a_fixture/learning/${action}`);
const l4a = (page: Page, action: string) =>
  fixture(page, `/__l4a_fixture/learning/${action}`);
const l4c = (page: Page, action: string) =>
  fixture(page, `/__l4c_fixture/learning/${action}`);

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
  await l4c(page, 'restore');
  await l4a(page, 'restore');
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

test('how Row-Bot learns: who does each of Learning’s jobs, with its latest test', async ({
  page,
}, info) => {
  await turnOn(page);
  await l4a(page, 'methods');
  await l4c(page, 'jobs');
  await open(page);

  const section = page.getByRole('region', { name: 'How Row-Bot learns' });
  await section
    .getByRole('button', { name: 'How Row-Bot learns: details' })
    .click();
  const sheet = page.getByRole('dialog', { name: 'How Row-Bot learns' });
  const jobs = sheet.getByRole('region', {
    name: 'Jobs your personal model does',
  });
  await expect(jobs.getByText('Drafting changes')).toBeVisible();
  await expect(jobs.getByText(/^Personal model v3 · since /)).toBeVisible();
  await expect(
    jobs.getByText(/: 22 of 24 usable drafts for v3, 23 for /),
  ).toBeVisible();
  await expect(jobs.getByText('Making practice tasks')).toBeVisible();
  await expect(
    jobs.getByText(/its last test wasn’t close enough/),
  ).toBeVisible();
  await jobs.scrollIntoViewIfNeeded();
  await shoot(page, info, 'how-row-bot-learns-jobs');
});
