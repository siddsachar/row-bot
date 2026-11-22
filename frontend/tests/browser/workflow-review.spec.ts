import type { Locator, Page, TestInfo } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';
import { blockFixtureServiceWorkers, dismissNotices } from './unified-helpers';

/*
 * N9 · A workflow's own code folder, its schedule's timezone, what happens to a
 * run missed while Row-Bot was closed, and "Always ask me". F31 · a run is a
 * turn of the workflow's conversation and shows how it ended. The fixture seeds
 * a switched-off workflow and four run records (one ran late in its folder and
 * posted, one waits on its conversation's person-only X card, one was replaced
 * by a newer run, one was stopped by a removed folder); nothing is scheduled or
 * run.
 */

const PHONE = { width: 390, height: 844 };
const DESKTOP = { width: 1280, height: 900 };
const NAME = 'Landing checks';

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

async function openWorkflows(page: Page) {
  await page.goto('/app-v2/?tab=workflows');
  await page.getByRole('searchbox', { name: 'Search workflows' }).fill(NAME);
  await page
    .getByRole('button', { name: 'Search workflows', exact: true })
    .click();
  const card = page.locator('.workflow-grid > li').filter({ hasText: NAME });
  await expect(card).toHaveCount(1);
  return card;
}

async function choose(page: Page, action: string) {
  const card = await openWorkflows(page);
  await card
    .getByRole('button', { name: `More actions for ${NAME}`, exact: true })
    .click();
  await page.getByRole('menuitem', { name: action, exact: true }).click();
}

test.use({ serviceWorkers: 'allow', nativeNetwork: true });
test.beforeEach(async ({ context, page }) => {
  await blockFixtureServiceWorkers(context);
  await page.setViewportSize(DESKTOP);
  const seeded = await page.request.post('/__n9_fixture/workflows', {
    headers: fixtureHeaders(),
  });
  expect(seeded.ok(), await seeded.text()).toBe(true);
});
test.afterEach(async ({ page }) => {
  const done = await page.request.post('/__n9_fixture/reset', {
    headers: fixtureHeaders(),
  });
  expect(done.ok()).toBe(true);
});

test('workflow settings choose the code folder, missed runs and the apps that always ask', async ({
  page,
}, info) => {
  await choose(page, 'Workflow settings');
  const editor = page.getByRole('region', {
    name: 'Workflow settings editor',
    exact: true,
  });
  const folder = editor.getByRole('combobox', { name: 'Code folder' });
  await expect(folder).toHaveValue('dev_n9_landing');
  await expect(folder.locator('option:checked')).toHaveText('row-bot-landing');
  await expect(
    editor.getByRole('combobox', {
      name: 'If Row-Bot is closed at a scheduled time',
    }),
  ).toHaveValue('run_once');
  await expect(
    editor.getByText(
      'Only the latest missed time runs, once, and says it ran late.',
    ),
  ).toBeVisible();
  // A phone changes a workflow's settings on the computer (C2, D11 A).
  const away = page.getByRole('region', { name: 'Workflow settings' });
  await everywhere(page, info, 'workflow-folder', folder, away);
  const always = editor.getByRole('group', { name: 'Always ask me' });
  await expect(
    always.getByRole('switch', {
      name: 'Always ask me before Tavily acts',
    }),
  ).toBeChecked();
  await everywhere(page, info, 'workflow-always-ask-apps', always, away);
});

test('the schedule builder shows the zone with the time and names it in the next run', async ({
  page,
}, info) => {
  const card = await openWorkflows(page);
  await card
    .getByRole('button', { name: `Edit workflow: ${NAME}`, exact: true })
    .click();
  const preview = page.locator('.schedule-preview');
  await expect(preview).toContainText('Every day at');
  await expect(preview).toContainText('· Europe/London');
  await expect(preview.locator('time')).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Timezone', exact: true }),
  ).toContainText('Europe/London');
  await everywhere(
    page,
    info,
    'schedule-zone',
    preview,
    page.getByRole('region', { name: 'The workflow editor' }),
  );
});

test('run history shows the waiting run’s own X card, the folder, a late run and how runs ended', async ({
  page,
}, info) => {
  await choose(page, 'Run history');
  const runs = page.getByRole('region', {
    name: 'Task runs and approvals',
    exact: true,
  });
  const selected = runs.getByRole('region', { name: 'Selected run' });
  // The newest run waits for you on its conversation's card: the whole post, its account and picture.
  await expect(selected.getByText('Waiting for you')).toBeVisible();
  const card = selected.getByRole('complementary', {
    name: /^Approval required/,
  });
  await expect(
    card.getByText(/Landing checks passed for row-bot\.ai/),
  ).toBeVisible();
  await expect(card.getByText('@rowbot').first()).toBeVisible();
  await expect(card.getByRole('button', { name: 'Approve' })).toBeEnabled();
  // A phone shows the runs in the workflow's sheet, the waiting run open (C2).
  const sheet = page.getByRole('dialog', { name: NAME });
  await everywhere(
    page,
    info,
    'waiting-run-x-card',
    card,
    sheet.getByRole('complementary', { name: /^Approval required/ }),
  );
  const sheetRuns = sheet.getByRole('list', { name: 'Recent runs' });

  await runs.getByRole('button', { name: 'Show run n9-late' }).click();
  await expect(selected.getByText('In row-bot-landing')).toBeVisible();
  await expect(
    selected.getByText('Ran late: Row-Bot was closed at 09:00.'),
  ).toBeVisible();
  // How it ended, from what its tools did: the post and its link.
  const did = selected.getByRole('list', { name: 'What it did' });
  await expect(did.getByText('Posted on X as @rowbot')).toBeVisible();
  await expect(
    did.getByRole('link', { name: 'Open: Posted on X as @rowbot' }),
  ).toHaveAttribute('href', 'https://x.com/rowbot/status/1849000000000000001');
  await everywhere(
    page,
    info,
    'run-posted-folder-ran-late',
    selected,
    sheetRuns,
  );

  await runs.getByRole('button', { name: 'Show run n9-replaced' }).click();
  await expect(selected.getByText('Expired', { exact: true })).toBeVisible();
  await expect(
    selected.getByText('A newer run replaced it before it was approved.'),
  ).toBeVisible();
  await everywhere(page, info, 'run-replaced', selected, sheetRuns);

  await runs.getByRole('button', { name: 'Show run n9-folder-gone' }).click();
  await expect(
    selected.getByText(
      "row-bot-landing isn't a code folder any more. Choose another in this workflow's settings.",
    ),
  ).toBeVisible();
  // It needs you (not a safety block), with its one fix.
  await expect(selected.getByText('Needs you', { exact: true })).toBeVisible();
  await expect(selected.getByText('Blocked', { exact: true })).toHaveCount(0);
  const chooseFolder = selected.getByRole('button', {
    name: 'Choose a folder',
  });
  await expect(chooseFolder).toBeVisible();
  await everywhere(page, info, 'workflow-folder-removed', selected, sheetRuns);

  // The drawer was read again on the wider window: the run is picked again.
  await runs.getByRole('button', { name: 'Show run n9-folder-gone' }).click();
  await chooseFolder.click();
  const editor = page.getByRole('region', {
    name: 'Workflow settings editor',
    exact: true,
  });
  await expect(
    editor.getByRole('combobox', { name: 'Code folder' }),
  ).toBeFocused();
});
