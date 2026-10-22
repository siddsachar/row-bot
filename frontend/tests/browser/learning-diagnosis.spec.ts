import type { Page, TestInfo } from '@playwright/test';
import { accessibility, assertNoOverflow, expect, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';

// Learning (preview), L3a: the night's look for things to improve. The fixture writes last
// night's report and two drafted changes directly (no model, GPU or download): the Overview's
// next look and Overnight lines, "What Row-Bot is learning", the Claude-plan and stopped
// nights, and Changes listing the drafts as Waiting to be tested. Each test puts the profile back.

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

/** A notice by its words (not Radix's announcement copy of them). */
function notice(page: Page, text: string | RegExp) {
  return page.locator('.toast-message').filter({ hasText: text });
}

async function turnOn(page: Page) {
  await l1a(page, 'preview');
  await l1a(page, 'gpu');
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

async function open(page: Page, view = 'overview') {
  await page.goto(`/app-v2/?tab=learning&view=${view}`);
  await expect(
    page.getByRole('heading', { name: 'Learning', exact: true }),
  ).toBeVisible();
}

test('the night’s look: next look, Overnight, what Row-Bot is learning, and the drafts under Changes', async ({
  page,
}, info) => {
  await turnOn(page);
  await open(page);
  await expect(
    page.getByText(/^Next look tonight at .+ tokens this month$/),
  ).toBeVisible();
  await expect(page.getByText('Nothing learned yet.')).toBeVisible();
  await shoot(page, info, 'overview-next-look');

  await l3a(page, 'night');
  await open(page);
  const night = page.getByRole('region', { name: 'Overnight' });
  await expect(
    night.getByText('Found 3 things to improve · drafted 2'),
  ).toBeVisible();
  await expect(
    night.getByText('1 kind of work needs more examples'),
  ).toBeVisible();
  await expect(
    night.getByText('1 of them looks like a bug in Row-Bot itself'),
  ).toBeVisible();
  await expect(night.getByText(/^Left out 1 idea:/)).toBeVisible();
  const kinds = page.getByRole('table', {
    name: 'Kinds of work and where they stand',
  });
  await expect(
    kinds.getByRole('rowheader', { name: 'Document lookups' }),
  ).toBeVisible();
  await expect(
    kinds.getByText('A change waits to be tested').first(),
  ).toBeVisible();
  await expect(kinds.getByText('Needs more examples')).toBeVisible();
  await shoot(page, info, 'overview-overnight');

  await open(page, 'changes');
  const filters = page.getByRole('radiogroup', { name: 'Show changes' });
  await expect(filters.getByRole('radio', { name: 'Testing' })).toBeChecked();
  const list = page.getByRole('list', { name: 'Changes waiting to be tested' });
  await expect(list.getByText('Waiting to be tested')).toHaveCount(2);
  await expect(list.getByText('Use the current policy')).toBeVisible();
  await shoot(page, info, 'changes-drafts');
  await page
    .getByRole('button', { name: 'Show details for Use the current policy' })
    .click();
  await expect(
    list.getByText(
      /In 3 chats about document lookups, you corrected Row-Bot twice and you stopped it once/,
    ),
  ).toBeVisible();
  await page
    .getByRole('button', {
      name: 'Show details for Check the calendar before booking',
    })
    .click();
  await expect(list.getByText(/never used by itself/)).toBeVisible();
  await shoot(page, info, 'changes-draft-why');
  await filters.getByRole('radio', { name: 'Ready' }).click();
  await expect(
    page.getByText(/No changes are ready to review\./),
  ).toBeVisible();
});

test('a night on the Claude plan makes no call and says what to do; a night cut short carries on', async ({
  page,
}, info) => {
  await turnOn(page);
  await l3a(page, 'claude_plan');
  await open(page);
  await expect(
    page.getByText(
      /^Learning’s improvement work needs a model other than your Claude plan\./,
    ),
  ).toBeVisible();
  await shoot(page, info, 'overview-claude-plan');

  await l3a(page, 'stopped');
  await open(page);
  await expect(
    page.getByText('Found 2 things to improve · drafted 1'),
  ).toBeVisible();
  await expect(
    page.getByText(/^Stopped when you came back to Row-Bot/),
  ).toBeVisible();
  await shoot(page, info, 'overview-stopped');

  await l3a(page, 'nothing');
  await open(page);
  await expect(
    page.getByText('Looked for things to improve: nothing new.'),
  ).toBeVisible();
  await shoot(page, info, 'overview-nothing-new');
});
