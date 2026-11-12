import type { Page, TestInfo } from '@playwright/test';
import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';

// Knowledge › Review memory, end to end on the fixture app: synthetic
// memories shaped like a long-used profile's are reviewed, the suggestions
// are approved in a list, memory is backed up, and the review is undone. The
// default chat model is the Python tests' fake; no provider is called.

async function fixture(page: Page, path: string) {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated browser runner');
  const response = await page.request.post(path, {
    headers: { 'X-Fixture-Token': token, Origin: new URL(base).origin },
  });
  expect(response.ok(), await response.text()).toBe(true);
}

const phone = (info: TestInfo) =>
  (info.project.use.viewport?.width ?? 1440) < 700;

async function shoot(
  page: Page,
  info: TestInfo,
  name: string,
  appearance: 'light' | 'dark',
) {
  await assertNoOverflow(page);
  await screenshot(
    page,
    info,
    `${name}-${phone(info) ? 'phone' : 'desktop'}-${appearance}`,
  );
}

for (const appearance of ['light', 'dark'] as const) {
  test(`memory is reviewed, approved in a list, backed up and undone (${appearance})`, async ({
    page,
  }, info) => {
    await fixture(page, '/__mr_fixture/seed');
    await page.emulateMedia({ colorScheme: appearance });
    if (phone(info)) {
      await page.goto('/app-v2/settings/knowledge');
    } else {
      // Beside the Dream Cycle's overnight tidy-up, too.
      await page.goto('/app-v2/settings/preferences');
      const entry = page.getByRole('button', {
        name: 'Review memory',
        exact: true,
      });
      await entry.scrollIntoViewIfNeeded();
      await expect(entry).toBeVisible();
      await shoot(page, info, 'p-dream-cycle-entry', appearance);
      await page.goto('/app-v2/?tab=knowledge');
    }
    await page
      .getByRole('button', { name: 'Review memory', exact: true })
      .click();
    const dialog = page.getByRole('dialog', { name: 'Review memory' });
    await expect(dialog).toBeVisible();

    // (a) What a review does, with which model; nothing has run yet.
    await expect(
      dialog.getByRole('heading', { name: 'Tidy up your memory' }),
    ).toBeVisible();
    await expect(
      dialog.getByText('Nothing changes until you approve it in a list.'),
    ).toBeVisible();
    await expect(
      dialog.getByText(/Uses qwen3:8b on this computer/),
    ).toBeVisible();
    await shoot(page, info, 'a-intro', appearance);

    // (c) The suggestions, grouped, each with its reason.
    await dialog.getByRole('button', { name: 'Start review' }).click();
    const removals = dialog.getByRole('list', { name: 'Remove suggestions' });
    await expect(removals).toBeVisible();
    await expect(
      removals.getByText('Test data from development'),
    ).toBeVisible();
    await expect(
      removals.getByText('Replaced by a newer memory: “Recording theme now”'),
    ).toBeVisible();
    const rewrites = dialog.getByRole('list', { name: 'Rewrite suggestions' });
    await expect(
      rewrites.getByText(/Says “next week” without a date/),
    ).toBeVisible();
    await expect(
      dialog.getByRole('list', { name: 'Merge suggestions' }),
    ).toContainText('Same as “Coffee order”');
    await expect(
      dialog.getByRole('list', { name: 'Split your own record suggestions' }),
    ).toContainText('Contact details stay on it');
    await removals
      .getByRole('checkbox', { name: 'Weather in Leeds' })
      .uncheck();
    await shoot(page, info, 'c-suggestions', appearance);
    await accessibility(page, info, `memory-review-list-${appearance}`);

    // Long records and lessons are kept and marked; none is chosen.
    await dialog
      .getByRole('combobox', { name: 'Show' })
      .selectOption({ label: 'Long records and lessons' });
    const kept = dialog.getByRole('list', { name: 'Keep suggestions' });
    await expect(
      kept.getByText(/Kept — long record, check it yourself/),
    ).toBeVisible();
    await expect(kept.getByText('Lesson', { exact: true })).toBeVisible();
    await expect(kept.getByRole('checkbox')).toHaveCount(0);
    await shoot(page, info, 'c-kept', appearance);
    await dialog
      .getByRole('combobox', { name: 'Show' })
      .selectOption({ label: 'Everything' });

    // (d) Confirm, then (e) done with Undo.
    await dialog.getByRole('button', { name: /^Review \d+ changes$/ }).click();
    await expect(
      dialog.getByText(/^\d+ changes; memory is backed up first$/),
    ).toBeVisible();
    await dialog.getByRole('button', { name: /^Make \d+ changes$/ }).click();
    await expect(
      dialog.getByRole('heading', { name: 'Memory tidied up' }),
    ).toBeVisible();
    await expect(
      dialog.getByText(/You can undo this review until/),
    ).toBeVisible();
    await shoot(page, info, 'e-done', appearance);
    await dialog.getByRole('button', { name: 'Undo review' }).click();
    await expect(
      dialog.getByRole('heading', { name: 'Review undone' }),
    ).toBeVisible();
    await dialog.getByRole('button', { name: 'Done' }).click();
    await expect(dialog).toBeHidden();
  });
}
