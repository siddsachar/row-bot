import { expect, test, type Page } from '@playwright/test';
import { screenshot } from './evidence';
import { openFixture, type FixtureWindow } from './fixture';

type Block = { id: string; type: string; [key: string]: unknown };

/** Shows one assistant answer made of these blocks in the open conversation. */
async function showAnswer(page: Page, blocks: Block[]) {
  await page.evaluate((answer) => {
    const { controller } = (window as FixtureWindow).__ROW_BOT_FIXTURE__;
    const projection = controller.getSnapshot().projection!;
    const base = projection.rows[0];
    const row = {
      ...base,
      id: 'answer-components',
      message_id: 'answer-components',
      role: 'assistant' as const,
      blocks: answer,
      traces: [],
    };
    (controller as unknown as { update(patch: unknown): void }).update({
      history: null,
      projection: { ...projection, rows: [row] },
    });
  }, blocks);
}

/** Desktop and phone, light and dark, of what is on screen. */
async function screens(
  page: Page,
  info: Parameters<typeof screenshot>[1],
  name: string,
  target: () => ReturnType<Page['locator']>,
) {
  for (const [width, height, size] of [
    [1440, 900, 'desktop'],
    [375, 812, 'phone'],
  ] as const) {
    await page.setViewportSize({ width, height });
    for (const scheme of ['light', 'dark'] as const) {
      await page.emulateMedia({ colorScheme: scheme });
      await target().scrollIntoViewIfNeeded();
      const layout = await page.evaluate(() => ({
        document: document.documentElement.scrollWidth,
        viewport: document.documentElement.clientWidth,
      }));
      expect(layout.document).toBeLessThanOrEqual(layout.viewport + 1);
      await screenshot(page, info, `${name}-${size}-${scheme}`);
    }
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.emulateMedia({ colorScheme: 'light' });
}

const TABLE = [
  'Here is the budget:',
  '',
  '| Item | Cost | Share | Due |',
  '| --- | ---: | ---: | --- |',
  '| **Rent** | £1,200 | 62% | 1 Dec 2026 |',
  '| Food | £320 | 17% | 12 Nov 2026 |',
  '| Phone | £45 | 2% | 9 Nov 2026 |',
  '| Travel | £380 | 19% | 20 Nov 2026 |',
].join('\n');

test('a Markdown table sorts by pointer and keyboard, and a task list ticks', async ({
  page,
}, info) => {
  test.skip(
    info.project.name !== 'chromium-desktop',
    'One focused Chromium pass',
  );
  await openFixture(page);
  await showAnswer(page, [
    { id: 'table', type: 'markdown', text: TABLE },
    {
      id: 'tasks',
      type: 'markdown',
      text: 'Before you go:\n\n- [x] Book the train\n- [ ] Pack the charger\n- [ ] Tell **Sam**',
    },
  ]);
  const table = page.getByRole('table');
  const costs = () =>
    table.locator('tbody tr td:nth-child(2)').allTextContents();
  await expect(table).toBeVisible();
  expect(await costs()).toEqual(['£1,200', '£320', '£45', '£380']);

  const cost = table.getByRole('button', { name: 'Cost' });
  await cost.click();
  await expect(cost.locator('xpath=..')).toHaveAttribute(
    'aria-sort',
    'ascending',
  );
  expect(await costs()).toEqual(['£45', '£320', '£380', '£1,200']);
  await cost.click();
  await expect(cost.locator('xpath=..')).toHaveAttribute(
    'aria-sort',
    'descending',
  );
  expect(await costs()).toEqual(['£1,200', '£380', '£320', '£45']);
  await screenshot(page, info, 'table-sorted-pointer');

  const due = table.getByRole('button', { name: 'Due' });
  await due.focus();
  await page.keyboard.press('Enter');
  await expect(due.locator('xpath=..')).toHaveAttribute(
    'aria-sort',
    'ascending',
  );
  await expect(cost.locator('xpath=..')).toHaveAttribute('aria-sort', 'none');
  expect(
    await table.locator('tbody tr td:nth-child(4)').allTextContents(),
  ).toEqual(['9 Nov 2026', '12 Nov 2026', '20 Nov 2026', '1 Dec 2026']);
  const align = await table
    .locator('tbody tr td:nth-child(2)')
    .first()
    .evaluate((cell) => getComputedStyle(cell).textAlign);
  expect(align).toBe('end');
  await screens(page, info, 'table-sorted', () => table);

  const pack = page.getByRole('checkbox', { name: 'Pack the charger' });
  await expect(pack).not.toBeChecked();
  await expect(
    page.getByRole('checkbox', { name: 'Book the train' }),
  ).toBeChecked();
  await pack.click();
  await expect(pack).toBeChecked();
  await page.getByRole('checkbox', { name: 'Tell Sam' }).press('Space');
  await expect(page.getByRole('checkbox', { name: 'Tell Sam' })).toBeChecked();
  await screens(page, info, 'task-list', () => page.locator('.task-list'));
});
