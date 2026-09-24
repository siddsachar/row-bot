import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';
import { blockFixtureServiceWorkers } from './unified-helpers';
import type { Page } from '@playwright/test';

async function seed(page: Page, state: 'populated' | 'empty') {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated Phase 4 runner');
  const response = await page.request.post(`/__p4_fixture/knowledge/${state}`, {
    headers: { 'X-Fixture-Token': token, Origin: new URL(base).origin },
  });
  expect(response.ok(), await response.text()).toBe(true);
}

test.use({ nativeNetwork: true, serviceWorkers: 'allow' });
test.beforeEach(async ({ context, page }) => {
  await blockFixtureServiceWorkers(context);
  await page.addInitScript(() => {
    if (window !== window.top) return;
    localStorage.setItem(
      'row-bot.appearance.v1',
      JSON.stringify({ version: 1, appearance: 'system', accent: 'blue' }),
    );
  });
});

test('Knowledge Settings matches the reviewed NiceGUI hierarchy and workflows', async ({
  page,
}, info) => {
  await seed(page, 'populated');
  await page.goto('/app-v2/settings/knowledge');

  await expect(
    page.getByRole('region', { name: 'Memory graph summary' }),
  ).toBeVisible();
  const wiki = page.getByRole('region', { name: 'Wiki vault', exact: true });
  await expect(wiki).toBeVisible();
  await expect(
    wiki.getByRole('button', { name: 'Browse', exact: true }),
  ).toBeVisible();
  await expect(
    wiki.getByRole('button', { name: 'Check vault sync', exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole('heading', { name: 'Needs Review' }),
  ).toBeVisible();
  await expect(
    page.locator('.settings-knowledge-filters').getByRole('combobox'),
  ).toHaveCount(4);
  await expect(page.locator('.settings-knowledge-result')).toHaveCount(25);
  await expect(
    page.getByText('Showing 25 of 105 matching entries.'),
  ).toBeVisible();

  const memory = page.getByRole('switch', { name: 'Enable Memory' });
  const wasEnabled = await memory.isChecked();
  await memory.click();
  const reviewedMemory = page.getByRole('region', {
    name: 'Reviewed memory setting',
  });
  await expect(reviewedMemory).toContainText(
    wasEnabled ? 'disabled' : 'enabled',
  );
  await reviewedMemory
    .getByRole('button', { name: 'Apply memory setting' })
    .click();
  await expect(memory).toBeChecked({ checked: !wasEnabled });

  const search = page.getByRole('searchbox', { name: 'Search knowledge' });
  await search.fill('tail needle');
  await expect(page.locator('.settings-knowledge-result')).toHaveCount(1);
  await page.getByRole('combobox', { name: 'Category' }).selectOption('fact');
  await page.getByRole('combobox', { name: 'Status' }).selectOption('active');
  await page
    .getByRole('combobox', { name: 'Source' })
    .selectOption('extraction');
  await page.getByRole('combobox', { name: 'Tier' }).selectOption('semantic');
  await expect(page.locator('.settings-knowledge-result')).toHaveCount(1);

  const row = page.locator('.settings-knowledge-result').first();
  await row.locator('summary').click();
  await expect(row.getByText('p4-entity-104', { exact: true })).toBeVisible();
  await expect(row.getByText('semantic', { exact: true })).toBeVisible();
  await row.getByText('Provenance', { exact: true }).click();
  await expect(
    row.getByText('Source: synthetic', { exact: true }),
  ).toBeVisible();
  await row.getByRole('button', { name: /Edit/ }).click();
  const editor = page.getByRole('dialog', { name: 'Edit knowledge' });
  await expect(editor).toBeVisible();
  await expect(editor.getByRole('textbox', { name: 'Subject' })).toHaveValue(
    'Phase 4 knowledge 104',
  );
  await editor.getByRole('button', { name: 'Close knowledge editor' }).click();
  await expect(editor).toHaveCount(0);
  await expect(row.getByRole('button', { name: /Edit/ })).toBeFocused();

  await page.getByText('Recent recall decisions', { exact: true }).click();
  await expect(page.getByText('Memory used', { exact: true })).toBeVisible();
  await expect(page.getByText(/Phase 4 knowledge 000 \(0\.93\)/)).toBeVisible();
  await page.getByText('Memory change log', { exact: true }).click();
  await expect(
    page.getByText('mark needs review', { exact: true }),
  ).toBeVisible();
  await page
    .getByRole('heading', { name: 'Knowledge', exact: true })
    .scrollIntoViewIfNeeded();

  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await expect(page.locator('html')).toHaveAttribute(
      'data-theme',
      appearance,
    );
    await assertNoOverflow(page);
    await screenshot(page, info, `knowledge-parity-${appearance}`);
    await accessibility(page, info, `knowledge-parity-${appearance}`);
  }

  await page.getByRole('button', { name: 'Select', exact: true }).click();
  await row
    .getByRole('checkbox', { name: 'Select Phase 4 knowledge 104' })
    .click();
  await page.getByRole('button', { name: 'Review delete selected' }).click();
  await page
    .getByRole('button', { name: 'Confirm permanent deletion' })
    .click();
  await expect(page.getByText('No matching knowledge')).toBeVisible();

  await search.fill('');
  await page.getByRole('combobox', { name: 'Category' }).selectOption('');
  await page.getByRole('combobox', { name: 'Status' }).selectOption('');
  await page.getByRole('combobox', { name: 'Source' }).selectOption('');
  await page.getByRole('combobox', { name: 'Tier' }).selectOption('');
  const deleteAll = page.getByRole('button', { name: /Delete all knowledge/ });
  await expect(deleteAll).toBeEnabled();
  await deleteAll.click();
  await expect(
    page.getByRole('region', { name: 'Reviewed knowledge deletion' }),
  ).toContainText('104 entries');
  await page
    .getByRole('button', { name: 'Confirm permanent deletion' })
    .click();
  await expect(page.getByText('No matching knowledge')).toBeVisible();
});
