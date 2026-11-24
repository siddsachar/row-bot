import { request, type Page } from '@playwright/test';
import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';
import { asPairedPhone, onPhone, PHONES, tapPath } from './phone-helpers';
import { fixtureHeaders } from './surface-helpers';
import { composer } from './unified-helpers';

// Workflows on a phone (C2, D11 A): its own page of rows, each with a status
// line and a switch, under All, Running, Failed and Off chips; a row opens
// its sheet with Run now, Open chat and its recent runs' answers; + starts a
// chat that sets one up; the editor and settings stay on the computer.
// Earlier specs in the same backend may leave workflows behind, so each test
// finds the seeded ones by name and never counts them all.

/** The seeded workflows' ids by key (brief, launch, digest, tidy, expenses). */
let ids: Record<string, string> = {};

async function seed(page: Page) {
  const response = await page.request.post('/__phone_c2_fixture/workflows', {
    headers: fixtureHeaders(),
  });
  expect(response.ok(), await response.text()).toBe(true);
  ids = ((await response.json()) as { task_ids: Record<string, string> })
    .task_ids;
}

const tabBar = (page: Page) => page.getByRole('navigation', { name: 'Main' });
const workflowsTab = (page: Page) =>
  tabBar(page).getByRole('link', { name: 'Workflows' });
const list = (page: Page) => page.getByRole('list', { name: 'Workflows' });
const row = (page: Page, name: string) =>
  list(page).getByRole('listitem').filter({ hasText: name });
const rowButton = (page: Page, name: string) =>
  row(page, name).getByRole('button', { name: new RegExp(`^${name}`) });
const sheet = (page: Page, name: string) => page.getByRole('dialog', { name });

async function openHome(page: Page) {
  await page.goto('/app-v2/');
  await expect(
    page.getByRole('heading', { level: 2, name: /^Good / }),
  ).toBeVisible();
}

// Each test seeds afresh (which clears the last seed); once done, nothing of
// ours is left running or failed for the specs after this one. Not after each
// test: a page still open on a workflow's chat would see it deleted.
test.afterAll(async () => {
  const fixture = await request.newContext({
    baseURL: process.env.ROW_BOT_BROWSER_BASE_URL,
  });
  await fixture.post('/__phone_c2_fixture/reset', {
    headers: fixtureHeaders(),
  });
  await fixture.dispose();
});

for (const phone of PHONES)
  test.describe(phone.name, () => {
    onPhone(phone);
    const width = phone.use.viewport.width;

    test.beforeEach(async ({ page }) => {
      await asPairedPhone(page);
      await seed(page);
    });

    test('lists each workflow with its status line and switch, under chips that filter', async ({
      page,
    }, info) => {
      await openHome(page);
      expect(await tapPath(page, [workflowsTab(page)])).toBe(1);
      await expect(page).toHaveURL(/\/app-v2\/?\?tab=workflows$/);
      await expect(
        page.getByRole('group', { name: 'Workspace controls' }),
      ).toContainText('Workflows');
      await expect(
        page.getByRole('heading', { level: 1, name: 'Workflows' }),
      ).toBeAttached();
      await expect(row(page, 'Morning Brief')).toContainText(/ · next in /);
      await expect(row(page, 'Launch Summary')).toContainText(
        'Running · message 2 of 3',
      );
      await expect(row(page, 'Research Digest')).toContainText(
        /Failed 2 hours ago · Every /,
      );
      await expect(row(page, 'Weekly Tidy-up')).toContainText('Off · Every ');
      await expect(row(page, 'Expense Sorter')).toContainText('Run when asked');
      // The desktop's search, segments, delivery defaults and editor buttons are gone.
      await expect(
        page.getByRole('searchbox', { name: 'Search workflows' }),
      ).toHaveCount(0);
      await expect(
        page.getByRole('button', { name: /^Edit workflow/ }),
      ).toHaveCount(0);
      await expect(
        page.getByRole('button', { name: 'Delivery defaults' }),
      ).toHaveCount(0);
      // Finger-sized: rows and switches 44px or more.
      for (const target of [
        rowButton(page, 'Morning Brief'),
        row(page, 'Morning Brief').getByRole('switch'),
      ]) {
        const box = (await target.boundingBox())!;
        expect(Math.round(box.height)).toBeGreaterThanOrEqual(44);
        expect(Math.round(box.width)).toBeGreaterThanOrEqual(44);
      }
      await assertNoOverflow(page);
      await screenshot(page, info, `phone-workflows-${width}`);
      await accessibility(page, info, `phone-workflows-${width}`);

      const chips = page.getByRole('radiogroup', { name: 'Show workflows' });
      await chips.getByRole('radio', { name: /^Running \d+$/ }).tap();
      await expect(row(page, 'Launch Summary')).toBeVisible();
      await expect(row(page, 'Morning Brief')).toHaveCount(0);
      await chips.getByRole('radio', { name: /^Failed \d+$/ }).tap();
      await expect(row(page, 'Research Digest')).toBeVisible();
      await expect(row(page, 'Launch Summary')).toHaveCount(0);
      await chips.getByRole('radio', { name: /^Off \d+$/ }).tap();
      await expect(row(page, 'Weekly Tidy-up')).toBeVisible();
      await expect(row(page, 'Research Digest')).toHaveCount(0);
      await chips.getByRole('radio', { name: /^All \d+$/ }).tap();
      await expect(row(page, 'Morning Brief')).toBeVisible();
    });

    test('opens a workflow’s runs and their answers in two taps', async ({
      page,
    }, info) => {
      await openHome(page);
      expect(
        await tapPath(page, [
          workflowsTab(page),
          rowButton(page, 'Morning Brief'),
        ]),
      ).toBe(2);
      const opened = sheet(page, 'Morning Brief');
      await expect(opened).toContainText('Asks before actions');
      const runs = opened.getByRole('list', { name: 'Recent runs' });
      await expect(runs).toContainText(
        '3 items for today: the dentist at 3, a reply from Sam, rain later.',
      );
      await expect(runs).toContainText('Quiet day, nothing new.');
      await expect(
        opened.getByRole('button', { name: 'Open chat', exact: true }),
      ).toBeVisible();
      await expect(opened).toContainText(
        'Change its messages or schedule on your computer, or ask in its chat.',
      );
      // A run opens to what it did.
      await runs.getByRole('button', { name: /^Yesterday/ }).tap();
      await expect(
        runs.getByRole('button', { name: /^Yesterday/ }),
      ).toHaveAttribute('aria-expanded', 'true');
      await assertNoOverflow(page);
      await screenshot(page, info, `phone-workflow-sheet-${width}`);
      await accessibility(page, info, `phone-workflow-sheet-${width}`);
      // Open chat goes to the workflow's one conversation.
      await opened
        .getByRole('button', { name: 'Open chat', exact: true })
        .tap();
      await expect(page).toHaveURL(/\/app-v2\/conversations\/[^/]+$/);
      await expect(composer(page)).toBeVisible();
    });

    test('runs a workflow now in three taps, through its review', async ({
      page,
    }) => {
      await openHome(page);
      expect(
        await tapPath(page, [
          workflowsTab(page),
          rowButton(page, 'Expense Sorter'),
          sheet(page, 'Expense Sorter').getByRole('button', {
            name: 'Run now',
            exact: true,
          }),
        ]),
      ).toBe(3);
      const opened = sheet(page, 'Expense Sorter');
      await expect(opened.getByRole('status')).toContainText(
        /Run started\.|Run finished/,
      );
      await expect(
        opened.getByRole('list', { name: 'Recent runs' }).getByRole('listitem'),
      ).toHaveCount(1);
      // The run ends and its review is fresh: Run now is ready again.
      await expect(
        opened.getByRole('button', { name: 'Run now', exact: true }),
      ).toBeEnabled({ timeout: 30_000 });
    });

    test('turns a workflow on and off with its switch, saved on the server', async ({
      page,
    }) => {
      await page.goto('/app-v2/?tab=workflows');
      const off = row(page, 'Weekly Tidy-up').getByRole('switch');
      await expect(off).not.toBeChecked();
      expect(await tapPath(page, [off])).toBe(1);
      await expect(off).toBeChecked();
      await expect(sheet(page, 'Weekly Tidy-up')).toHaveCount(0);
      await page.reload();
      await expect(
        row(page, 'Weekly Tidy-up').getByRole('switch'),
      ).toBeChecked();
      await expect(row(page, 'Weekly Tidy-up')).toContainText(/ · next in /);
      // The sheet's switch is the same one.
      await rowButton(page, 'Weekly Tidy-up').tap();
      const inSheet = sheet(page, 'Weekly Tidy-up').getByRole('switch');
      await expect(inSheet).toBeChecked();
      await inSheet.tap();
      await expect(inSheet).not.toBeChecked();
    });

    test('+ starts the chat that sets a workflow up', async ({
      page,
    }, info) => {
      await page.goto('/app-v2/?tab=workflows');
      await expect(row(page, 'Morning Brief')).toBeVisible();
      const plus = page
        .getByRole('group', { name: 'Workspace controls' })
        .getByRole('button', { name: 'New workflow', exact: true });
      expect(await tapPath(page, [plus])).toBe(1);
      await expect(page).toHaveURL(/\/app-v2\/conversations\/[^/]+$/);
      await expect(composer(page)).toHaveValue('Set up a workflow that ');
      await screenshot(page, info, `phone-workflows-new-${width}`);
    });

    test('a link to one workflow opens its sheet; one to its settings says to use the computer', async ({
      page,
    }, info) => {
      await page.goto(`/app-v2/?tab=workflows&workflow=${ids.digest}`);
      const opened = sheet(page, 'Research Digest');
      await expect(opened).toBeVisible();
      await expect(opened).toContainText('Failed');
      await expect(page).toHaveURL(/\/app-v2\/?\?tab=workflows$/);
      await page.keyboard.press('Escape');
      await expect(opened).toHaveCount(0);

      await page.goto(
        `/app-v2/?tab=workflows&workflow=${ids.digest}&fix=folder`,
      );
      const away = page.getByRole('region', { name: 'Workflow settings' });
      await expect(away).toContainText('Open this on your computer');
      await expect(away).toContainText(
        'Change a workflow’s messages, schedule and delivery on your computer',
      );
      await assertNoOverflow(page);
      await screenshot(page, info, `phone-workflow-settings-${width}`);
      expect(await tapPath(page, ['Back to workflows'])).toBe(1);
      await expect(row(page, 'Research Digest')).toBeVisible();
    });

    test('Home’s Needs you offers Run again on a failed workflow', async ({
      page,
    }, info) => {
      await openHome(page);
      const failed = page
        .getByRole('list', { name: 'Needs you' })
        .getByRole('listitem')
        .filter({ hasText: 'Research Digest failed' });
      const again = failed.getByRole('button', {
        name: 'Run Research Digest again',
      });
      await expect(again).toBeVisible();
      const box = (await again.boundingBox())!;
      expect(Math.round(box.height)).toBeGreaterThanOrEqual(44);
      await screenshot(page, info, `phone-workflows-home-${width}`);
      expect(await tapPath(page, [again])).toBe(1);
      // It runs again through its review, so it leaves Needs you.
      await expect(failed).toHaveCount(0);
    });
  });

test.describe('desktop', () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test.beforeEach(async ({ page }) => {
    await seed(page);
  });

  test('keeps its workflow library, editor and run drawer as they were', async ({
    page,
  }, info) => {
    await page.goto('/app-v2/?tab=workflows');
    await expect(
      page.getByRole('tablist', { name: 'Home capabilities' }),
    ).toBeVisible();
    await expect(
      page.getByRole('searchbox', { name: 'Search workflows' }),
    ).toBeVisible();
    await expect(
      page
        .getByRole('radiogroup', { name: 'Show workflows' })
        .getByRole('radio'),
    ).toHaveText(['All', 'Enabled', 'Scheduled', 'Failed']);
    await expect(page.locator('.phone-workflows')).toHaveCount(0);
    await expect(
      page.getByRole('button', { name: 'Run workflow: Morning Brief' }),
    ).toBeVisible();
    await screenshot(page, info, 'phone-workflows-desktop');
    await page
      .getByRole('button', { name: 'More actions for Morning Brief' })
      .click();
    await page.getByRole('menuitem', { name: 'Run history' }).click();
    const runs = page.getByRole('region', { name: 'Task runs and approvals' });
    await expect(
      runs.getByRole('region', { name: 'Selected run' }),
    ).toContainText('Completed');
    await expect(
      runs.getByRole('button', { name: 'Refresh runs' }),
    ).toBeVisible();
    await page.getByRole('button', { name: 'Close workflow runs' }).click();
    await page
      .getByRole('button', { name: 'Edit workflow: Morning Brief' })
      .click();
    await expect(
      page.getByRole('region', { name: 'Workflow editor' }),
    ).toBeVisible();
    // The same editor on a phone-sized window says to use the computer, and
    // comes back when the window widens: nothing was reset.
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(
      page.getByRole('region', { name: 'The workflow editor' }),
    ).toContainText('Open this on your computer');
    await screenshot(page, info, 'phone-workflow-editor-390');
    await page.setViewportSize({ width: 1440, height: 900 });
    await expect(
      page.getByRole('form', { name: 'Edit task', exact: true }),
    ).toBeVisible();
  });
});
