import type { Page, TestInfo } from '@playwright/test';
import { accessibility, assertNoOverflow, expect, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';

// Learning (preview), L4a: how Row-Bot learns. The fixture writes learning methods, their trials and a
// night's switch directly (no model): the Overview's method row and Overnight line with Undo, the How
// Row-Bot learns sheet (current method, track record with its denominators, history, what it can never
// change), Undo of the switch, a night that kept the method, and the first method before any comparison.

async function fixture(page: Page, path: string) {
  const response = await page.request.post(path, {
    headers: fixtureHeaders(),
  });
  expect(response.ok(), await response.text()).toBe(true);
}

const l1a = (page: Page, action: string) =>
  fixture(page, `/__l1a_fixture/learning/${action}`);
const l3a = (page: Page, action: string) =>
  fixture(page, `/__l3a_fixture/learning/${action}`);
const l4a = (page: Page, action: string) =>
  fixture(page, `/__l4a_fixture/learning/${action}`);

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
  await l4a(page, 'restore');
  await l3a(page, 'restore');
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
    sheet.getByText('NVIDIA GeForce RTX 5080 Laptop (16 GB)'),
  ).toBeVisible();
  await sheet.getByRole('button', { name: 'Turn on Learning' }).click();
  const ask = page.getByRole('dialog', {
    name: 'Download the training engine and Qwen3.5 9B?',
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

test('how Row-Bot learns: a new method switched in by itself, its track record, and Undo', async ({
  page,
}, info) => {
  await turnOn(page);
  await l4a(page, 'methods');
  await l4a(page, 'switched');
  await open(page);

  const night = page.getByRole('region', { name: 'Overnight' });
  await expect(
    night.getByText(
      'Changed how it learns (method v3): tries a way of working first',
    ),
  ).toBeVisible();
  const section = page.getByRole('region', { name: 'How Row-Bot learns' });
  await expect(section.getByText(/^Method v3 · since /)).toBeVisible();
  await shoot(page, info, 'overview-method-switched');

  await section
    .getByRole('button', { name: 'How Row-Bot learns: details' })
    .click();
  const sheet = page.getByRole('dialog', { name: 'How Row-Bot learns' });
  await expect(sheet.getByText('Learning method v3')).toBeVisible();
  await expect(
    sheet.getByText(
      /^Looks first at mistakes you corrected, tries a way of working before a lesson/,
    ),
  ).toBeVisible();
  const table = sheet.getByRole('table', {
    name: 'Ideas kept by each method on the same new problems',
  });
  await expect(table.getByRole('row')).toHaveCount(3);
  await expect(
    sheet.getByText(/^Compared on 31 new problems at the same budget/),
  ).toBeVisible();
  await expect(
    sheet.getByText('v2 tried and dropped: no better than v1'),
  ).toBeVisible();
  await expect(sheet.getByText('The hidden tests that judge it')).toBeVisible();
  await shoot(page, info, 'how-row-bot-learns');

  await sheet.getByRole('button', { name: 'Undo learning method v3' }).click();
  await expect(notice(page, 'Back to learning method v1.')).toBeVisible();
  await expect(sheet.getByText('Learning method v1')).toBeVisible();
  await expect(sheet.getByText('Undone: back to v1')).toBeVisible();
  await expect(
    sheet.getByRole('button', { name: /^Undo learning method/ }),
  ).toHaveCount(0);
  await shoot(page, info, 'how-row-bot-learns-undone');
});

test('a night that kept the method, and the first method before any comparison', async ({
  page,
}, info) => {
  await turnOn(page);
  await open(page);
  const section = page.getByRole('region', { name: 'How Row-Bot learns' });
  await expect(section.getByText('Method v1')).toBeVisible();
  await section
    .getByRole('button', { name: 'How Row-Bot learns: details' })
    .click();
  const sheet = page.getByRole('dialog', { name: 'How Row-Bot learns' });
  await expect(
    sheet.getByText(
      /^Not compared with another method yet\. A comparison runs once 30 new problems have built up/,
    ),
  ).toBeVisible();
  await shoot(page, info, 'how-row-bot-learns-first');
  await page.keyboard.press('Escape');

  await l4a(page, 'methods');
  await l4a(page, 'kept');
  await open(page);
  await expect(
    page
      .getByRole('region', { name: 'Overnight' })
      .getByText(
        'Tried a new way of learning; it wasn’t better. Kept method v3.',
      ),
  ).toBeVisible();
  await shoot(page, info, 'overview-method-kept');
});
