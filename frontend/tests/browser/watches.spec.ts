import type { Locator, Page, TestInfo } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';
import {
  composer,
  dismissNotices,
  fixtureState,
  newConversation,
} from './unified-helpers';

/*
 * B4 · a watch: a page, a command or a question checked on a schedule, which
 * speaks up only when it changes. The fixture seeds watches as records whose
 * next check is a day away, so nothing runs while the spec looks at their
 * cards; a page watch set with /watch reads a synthetic page.
 */

const PHONE = { width: 375, height: 812 };
const DESKTOP = { width: 1280, height: 900 };

async function views(page: Page, info: TestInfo, name: string, focus: Locator) {
  await dismissNotices(page);
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await expect(page.locator('html')).toHaveAttribute(
      'data-theme',
      appearance,
    );
    await focus.scrollIntoViewIfNeeded();
    await assertNoOverflow(page);
    await screenshot(page, info, `${name}-${appearance}`);
  }
  await page.emulateMedia({ colorScheme: 'light' });
}

/**
 * Evidence at desktop and phone size, light and dark. `phone` is what a
 * phone shows in its place, when that differs (C2: a workflow's sheet, or
 * "Open this on your computer" for its editor and settings).
 */
async function everywhere(
  page: Page,
  info: TestInfo,
  name: string,
  focus: Locator,
  phone: Locator = focus,
) {
  await views(page, info, name, focus);
  await page.setViewportSize(PHONE);
  await views(page, info, `${name}-phone`, phone);
  await page.setViewportSize(DESKTOP);
}

async function seed(
  page: Page,
  conversation: string,
  state: string,
  mode = 'judged',
) {
  const seeded = await page.request.post('/__b4_fixture/watch', {
    headers: fixtureHeaders(),
    data: { conversation_id: conversation, state, mode },
  });
  expect(seeded.ok(), await seeded.text()).toBe(true);
}

async function reset(page: Page) {
  const done = await page.request.post('/__b4_fixture/reset', {
    headers: fixtureHeaders(),
  });
  expect(done.ok(), await done.text()).toBe(true);
}

function card(page: Page) {
  return page
    .getByRole('region', { name: 'Waiting', exact: true })
    .locator('.watch-card');
}

test.beforeEach(async ({ page }) => {
  await page.setViewportSize(DESKTOP);
  await reset(page);
});
test.afterEach(async ({ page }) => {
  await reset(page);
});

test('a watch card shows its last check and what it used, with Check now, Pause and Stop', async ({
  page,
}, info) => {
  const conversation = await newConversation(page);
  await seed(page, conversation, 'watching');
  const watch = card(page);
  await expect(watch).toContainText('Watching PR 590 is ready to merge');
  await expect(watch).toContainText(
    'every 30 min · 1 of 20 changes · 12k tokens · $0.04',
  );
  await expect(watch).toContainText(': Checks still running (3 of 5).');
  for (const name of ['Check now', 'Pause watching', 'Stop watching'])
    await expect(
      watch.getByRole('button', { name, exact: true }),
    ).toBeVisible();
  await everywhere(page, info, 'watch-watching', watch);

  await watch.getByRole('button', { name: 'Pause watching' }).click();
  await expect(watch).toContainText('Paused: PR 590 is ready to merge');
  await expect(watch.getByRole('button', { name: 'Check now' })).toHaveCount(0);
  await everywhere(page, info, 'watch-paused', watch);
  await watch.getByRole('button', { name: 'Resume watching' }).click();
  await expect(watch).toContainText('Watching PR 590 is ready to merge');

  await watch.getByRole('button', { name: 'Stop watching' }).click();
  await expect(watch).toHaveCount(0);

  await seed(page, conversation, 'trouble', 'command');
  await expect(card(page)).toHaveClass(/is-trouble/);
  await expect(card(page)).toContainText(
    "Couldn't check: it didn't finish in 60 seconds",
  );
  await everywhere(page, info, 'watch-trouble', card(page));
});

test('/watch with a page sets a watch at once, with no model call, and the palette says how', async ({
  page,
}, info) => {
  await newConversation(page);
  const before = (await fixtureState(page)).calls.length;
  // The palette shows while the composer holds "/wat": dismissing a notice or
  // resizing can close it, so it is opened again before each picture.
  const option = page
    .getByRole('listbox', { name: 'Slash commands' })
    .getByRole('option', { name: '/watch Watch' });
  for (const [size, suffix] of [
    [DESKTOP, ''],
    [PHONE, '-phone'],
  ] as const) {
    await page.setViewportSize(size);
    await dismissNotices(page);
    for (const appearance of ['light', 'dark'] as const) {
      await page.emulateMedia({ colorScheme: appearance });
      await composer(page).fill('');
      await composer(page).fill('/wat');
      await expect(option).toContainText(
        '/watch [interval] page, `command` or what to watch',
      );
      await assertNoOverflow(page);
      await screenshot(page, info, `watch-hint${suffix}-${appearance}`);
    }
  }
  await page.setViewportSize(DESKTOP);
  await page.emulateMedia({ colorScheme: 'light' });

  await composer(page).fill('/watch 10m https://status.example.com');
  await composer(page).press('Escape');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(
    page.getByText('Watching. Row-Bot speaks up here only when it changes.', {
      exact: true,
    }),
  ).toBeVisible();
  await expect(card(page)).toContainText('Watching https://status.example.com');
  await expect(card(page)).toContainText('every 10 min');
  await expect(composer(page)).toHaveValue('');
  expect((await fixtureState(page)).calls).toHaveLength(before);
});

test('an ended watch offers to become a workflow; saving the workflow puts the watch away', async ({
  page,
}, info) => {
  const conversation = await newConversation(page);
  await seed(page, conversation, 'capped', 'command');
  const watch = card(page);
  await expect(watch).toContainText('Stopped watching CI on PR 590');
  await expect(watch).toContainText('Stopped after 20 changes.');
  const make = watch.getByRole('button', { name: 'Make this a workflow' });
  await expect(make).toBeVisible();
  await everywhere(page, info, 'watch-ended', watch);

  await make.click();
  await expect(page).toHaveURL(/tab=workflows/);
  const editor = page.getByRole('region', { name: 'Workflow editor' });
  await expect(page.getByLabel('Name', { exact: true })).toHaveValue(
    'Watch: CI on PR 590',
  );
  await expect(page.getByRole('textbox', { name: /^Message 1/ })).toHaveValue(
    /^Run `gh pr checks 590` and compare its output with the last run/,
  );
  await everywhere(
    page,
    info,
    'watch-workflow-editor',
    page.getByLabel('Name', { exact: true }),
    // A phone edits workflows on the computer (C2, D11 A).
    page.getByRole('region', { name: 'The workflow editor' }),
  );
  await page
    .getByRole('button', { name: 'Save workflow', exact: true })
    .click();
  await expect(editor).toHaveCount(0);

  await page.goto(`/app-v2/conversations/${conversation}`);
  await expect(composer(page)).toBeVisible();
  await expect(card(page)).toHaveCount(0);
});
