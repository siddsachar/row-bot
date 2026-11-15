import type { Page, TestInfo } from '@playwright/test';
import { accessibility, assertNoOverflow, expect, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';

// Learning (preview), L3b: testing and using changes. The fixture writes changes in each state
// (tested and ready, edited, in use by itself, not better, waiting), last night's tests, a test
// running now, a lesson Row-Bot saved for itself and a chat whose reply used a lesson. Nothing
// reaches a model, a GPU or a download. Each test puts the profile back.

async function fixture(page: Page, path: string) {
  const response = await page.request.post(path, {
    headers: fixtureHeaders(),
  });
  expect(response.ok(), await response.text()).toBe(true);
}

const l1a = (page: Page, action: string) =>
  fixture(page, `/__l1a_fixture/learning/${action}`);
const l3b = (page: Page, action: string) =>
  fixture(page, `/__l3b_fixture/learning/${action}`);

test.beforeEach(async ({ page }) => {
  // A retry after a failed first launch starts from a clean profile too.
  await l3b(page, 'restore');
  await l1a(page, 'restore');
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
  await l3b(page, 'restore');
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

async function open(page: Page, view = 'overview') {
  await page.goto(`/app-v2/?tab=learning&view=${view}`);
  await expect(
    page.getByRole('heading', { name: 'Learning', exact: true }),
  ).toBeVisible();
}

test('a tested change waits in Needs you; its review shows the test; Use this change says Now in use, with Undo', async ({
  page,
}, info) => {
  await turnOn(page);
  await l3b(page, 'changes');
  await open(page);
  const needs = page.getByRole('list', { name: 'Changes ready to review' });
  await expect(
    needs.getByText('Pick documents by ID, not title'),
  ).toBeVisible();
  await expect(
    needs.getByText('Check the calendar before booking'),
  ).toBeVisible();
  const night = page.getByRole('region', { name: 'Overnight' });
  await expect(
    night.getByText('Tested 3 ideas: 2 ready, 1 not better'),
  ).toBeVisible();
  await expect(
    night.getByText(/^Put “Say which document you used” into use by itself/),
  ).toBeVisible();
  await shoot(page, info, 'overview-ready');

  await needs
    .getByRole('button', { name: 'Review Pick documents by ID, not title' })
    .click();
  const sheet = page.getByRole('dialog', {
    name: 'Pick documents by ID, not title',
  });
  await expect(sheet.getByText('Lesson for document lookups')).toBeVisible();
  const table = sheet.getByRole('table', {
    name: 'Tasks passed without and with this change',
  });
  await expect(
    table.getByRole('rowheader', {
      name: 'Document lookups it hadn’t seen (12)',
    }),
  ).toBeVisible();
  await expect(
    sheet.getByText(/Small test, so expect some variation\./),
  ).toBeVisible();
  await shoot(page, info, 'review-lesson');

  await sheet.getByRole('button', { name: 'Use this change' }).click();
  await expect(notice(page, 'Now in use.')).toBeVisible();
  await shoot(page, info, 'use-toast');
  await page.getByRole('button', { name: 'Undo', exact: true }).click();
  await expect(notice(page, /^Undone\./)).toBeVisible();

  await open(page, 'changes');
  await page.getByRole('radio', { name: 'In use' }).click();
  const inUse = page.getByRole('list', { name: 'Changes in use' });
  await expect(inUse.getByText('In use · by itself')).toBeVisible();
  await expect(
    inUse.getByRole('button', { name: 'Undo Say which document you used' }),
  ).toBeVisible();
  await shoot(page, info, 'changes-in-use');
});

test('the review adapts to a way of working, an edited change and one that wasn’t better', async ({
  page,
}, info) => {
  await turnOn(page);
  await l3b(page, 'changes');
  await open(page, 'changes');
  const ready = page.getByRole('list', { name: 'Changes ready to review' });
  await ready
    .getByRole('button', { name: 'Review Check the calendar before booking' })
    .click();
  const way = page.getByRole('dialog', {
    name: 'Check the calendar before booking',
  });
  await expect(
    way.getByText(
      'Way of working for tool workflows · How Row-Bot uses its tools',
    ),
  ).toBeVisible();
  await shoot(page, info, 'review-way-of-working');
  await way.getByRole('button', { name: 'Not now' }).click();

  await l3b(page, 'testing');
  await page.getByRole('radio', { name: 'Testing' }).click();
  const testing = page.getByRole('list', { name: 'Changes being tested' });
  await expect(testing.getByText('Edited: test again')).toBeVisible();
  await expect(
    page.getByText(
      'Testing “Hand long reports to a helper” on 24 tasks · 9 done',
    ),
  ).toBeVisible();
  await shoot(page, info, 'changes-testing');
  await testing
    .getByRole('button', { name: 'Review Use the current policy' })
    .click();
  const edited = page.getByRole('dialog', { name: 'Use the current policy' });
  await expect(
    edited.getByText('Edited: test again before using.'),
  ).toBeVisible();
  await expect(
    edited.getByRole('button', { name: 'Test again' }),
  ).toBeVisible();
  await shoot(page, info, 'review-edited');
  await edited.getByRole('button', { name: 'Close review' }).click();

  await page.getByRole('radio', { name: 'Not used' }).click();
  const notUsed = page.getByRole('list', { name: 'Changes not used' });
  await expect(notUsed.getByText('Not better')).toBeVisible();
  await notUsed
    .getByRole('button', { name: 'Review Pick the newest file' })
    .click();
  const worse = page.getByRole('dialog', { name: 'Pick the newest file' });
  await expect(
    worse.getByText(/^No clear difference\. Not used\./),
  ).toBeVisible();
  await shoot(page, info, 'review-not-better');
});

test('Lessons: in use with how often it was used, a saved one to test; a reply says it used a lesson', async ({
  page,
}, info) => {
  await turnOn(page);
  await l3b(page, 'changes');
  await l3b(page, 'saved_lesson');
  await open(page, 'lessons');
  const lessons = page.getByRole('list', { name: 'Lessons' });
  await expect(lessons.getByText('Say which document you used')).toBeVisible();
  await expect(
    lessons.getByText('Document lookups · not used yet this month'),
  ).toBeVisible();
  await expect(
    lessons.getByRole('button', {
      name: 'Test Shell: pip needs --user on macOS',
    }),
  ).toBeVisible();
  await shoot(page, info, 'lessons');

  // Leaving the page cuts its live-updates stream, which Chromium may log once or twice; the
  // fixture's chat (made straight in the thread store) may have no Buddy view yet.
  for (const [signature, fixture] of [
    [
      'Failed to load resource: net::ERR_INCOMPLETE_CHUNKED_ENCODING',
      'opening the chat by its address while the live-updates stream is open',
    ],
    [
      'Failed to load resource: the server responded with a status of 404 (Not Found)',
      'the fixture chat has no Buddy view (GET /conversations/l3b-policies/buddy)',
    ],
  ] as const)
    info.annotations.push({
      type: 'expected-console-error',
      description: JSON.stringify({
        owner: 'learning-changes',
        fixture,
        signature,
        count: 2,
        upTo: true,
      }),
    });
  await page.goto('/app-v2/conversations/l3b-policies');
  await expect(page.getByText(/^85 EUR a person, from POL-114/)).toBeVisible();
  await expect(page.getByText('Used a lesson')).toBeVisible();
  await shoot(page, info, 'chat-used-a-lesson');
});
