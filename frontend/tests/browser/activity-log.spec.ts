import type { Page, TestInfo } from '@playwright/test';
import { captureBrowserDownload } from './download-helpers';
import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';

// D1 the activity log: Settings › System › Activity and Monitor's line. The
// fixture seeds a day of actions in one chat through the log's own writer
// (/__d1_fixture/activity/seed) and clears them, with the chat, afterwards.

function control() {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated browser runner');
  return { 'X-Fixture-Token': token, Origin: new URL(base).origin };
}

async function activity(page: Page, state: 'seed' | 'clear') {
  const response = await page.request.post(`/__d1_fixture/activity/${state}`, {
    headers: control(),
  });
  expect(response.ok(), await response.text()).toBe(true);
}

test.afterEach(async ({ page }) => {
  await activity(page, 'clear');
});

const PHONE = { width: 375, height: 812 };

/** Light and dark, then an accessibility scan of what shows. */
async function shoot(page: Page, info: TestInfo, name: string) {
  const suffix = (page.viewportSize()?.width ?? 1440) < 700 ? '-phone' : '';
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `${name}${suffix}-${appearance}`);
  }
  await accessibility(page, info, `${name}${suffix}`);
  await page.emulateMedia({ colorScheme: 'light' });
}

const log = (page: Page) =>
  page.getByRole('region', { name: 'Every action', exact: true });

for (const size of ['desktop', 'phone'] as const) {
  test.describe(size, () => {
    test.use(size === 'phone' ? { viewport: PHONE } : {});

    test(`lists what needed approval with plain outcomes, filters it and exports it (${size})`, async ({
      page,
    }, info) => {
      await activity(page, 'seed');
      await page.goto('/app-v2/settings/activity');
      const region = log(page);
      await expect(region).toBeVisible({ timeout: 15_000 });
      // Needed approval first: reads that needed none wait under All.
      await expect(
        region.getByRole('radio', { name: 'Needed approval' }),
      ).toBeChecked();
      for (const outcome of [
        'Waiting for you',
        'You approved',
        'Reviewer approved',
        'You said no',
        'Hands-off',
      ])
        await expect(
          region.locator('.alog-outcome', { hasText: outcome }).first(),
        ).toBeVisible();
      await expect(region.getByText('Today', { exact: true })).toBeVisible();
      await expect(
        region.locator('.alog-outcome', { hasText: /^Ran$/ }),
      ).toHaveCount(0);
      await shoot(page, info, 'activity-list');

      // A row opens to who decided and why.
      await region
        .getByRole('button', { name: /Run npm test in row-bot/ })
        .click();
      await expect(region.getByText('The reviewer (Auto)')).toBeVisible();
      await expect(
        region.getByText('You asked me to run the tests before pushing.'),
      ).toBeVisible();
      await expect(
        region.getByRole('link', { name: 'Open in chat' }),
      ).toHaveAttribute('href', /\/conversations\/d1-activity-chat$/);
      await shoot(page, info, 'activity-expanded');

      // Filters: what you said no to, and everything from Proactive.
      await region.getByRole('radio', { name: 'You said no' }).click();
      await expect(region.locator('.alog-outcome')).toHaveText(['You said no']);
      await region.getByRole('radio', { name: 'All' }).click();
      await region
        .getByRole('combobox', { name: 'Where' })
        .selectOption('proactive');
      await expect(region.locator('.alog-outcome')).toHaveText(['Ran']);
      await region.getByRole('combobox', { name: 'Where' }).selectOption('');
      await region
        .getByRole('searchbox', { name: 'Search activity' })
        .fill('invoice');
      await expect(region.locator('.alog-outcome')).toHaveText(['Hands-off']);
      await shoot(page, info, 'activity-filters');

      // Export as CSV saves what the filters show.
      const download = await captureBrowserDownload(page, () =>
        region.getByRole('button', { name: 'Export as CSV' }).click(),
      );
      expect(download.name).toMatch(
        /^row-bot-activity-\d{4}-\d{2}-\d{2}\.csv$/,
      );
      const csv = download.bytes.toString('utf-8');
      expect(csv).toContain(
        'Time,Chat,Where,Tool,App,What,Level,Outcome,Decided by,Reason',
      );
      expect(csv).toContain('Move invoice-2026-11.pdf to Documents/Taxes');
      expect(csv).not.toContain('Run npm test');
    });

    test(`says what will show while there is nothing, and clears only after you confirm (${size})`, async ({
      page,
    }, info) => {
      await activity(page, 'clear');
      await page.goto('/app-v2/settings/activity');
      const region = log(page);
      await expect(region.getByText('Nothing yet')).toBeVisible({
        timeout: 15_000,
      });
      await expect(
        region.getByRole('button', { name: 'Clear activity' }),
      ).toBeDisabled();
      await shoot(page, info, 'activity-empty');

      await activity(page, 'seed');
      await page.reload();
      await region.getByRole('button', { name: 'Clear activity' }).click();
      const dialog = page.getByRole('alertdialog', {
        name: 'Clear all activity?',
      });
      await expect(dialog).toBeVisible();
      await shoot(page, info, 'activity-clear-confirm');
      await dialog.getByRole('button', { name: 'Clear activity' }).click();
      await expect(region.getByText('Nothing yet')).toBeVisible();
    });

    test(`Monitor says what today held and opens the whole list (${size})`, async ({
      page,
    }, info) => {
      await activity(page, 'seed');
      await page.goto('/app-v2/?tab=monitor');
      if (size === 'phone') {
        // Monitor is on the computer (D5); the whole list is in Settings › Activity.
        await expect(
          page.getByRole('region', { name: 'System Monitor' }),
        ).toContainText('Open this on your computer');
        await page.goto('/app-v2/settings/activity');
        await expect(log(page)).toBeVisible();
        return;
      }
      const line = page.locator('.monitor-activity-line');
      await expect(line).toContainText(
        /Today: \d+ actions · \d+ asked you · 1 you said no to/,
        { timeout: 15_000 },
      );
      await line.scrollIntoViewIfNeeded();
      await shoot(page, info, 'activity-monitor-line');
      await line.getByRole('button', { name: 'See all activity' }).click();
      await expect(page).toHaveURL(/\/app-v2\/settings\/activity$/);
      await expect(log(page)).toBeVisible();
    });
  });
}
