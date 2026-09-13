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

test('Phase 4 knowledge and documents expose saved summaries and incomplete status through Settings', async ({
  page,
}, info) => {
  await seed(page, 'populated');
  await page.goto('/app-v2/settings');
  await page.getByRole('link', { name: 'Knowledge', exact: true }).click();
  await page
    .getByRole('searchbox', { name: 'Search knowledge' })
    .fill('Phase 4 knowledge');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.getByText('105 matching saved entries')).toBeVisible();
  await expect(page.locator('.settings-results > li')).toHaveCount(50);
  await page
    .getByRole('button', { name: 'Load more knowledge', exact: true })
    .click();
  await expect(page.locator('.settings-results > li')).toHaveCount(100);
  await page
    .getByRole('button', { name: 'Load more knowledge', exact: true })
    .click();
  await expect(page.locator('.settings-results > li')).toHaveCount(105);
  await page
    .getByRole('searchbox', { name: 'Search knowledge' })
    .fill('tail needle');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.locator('.settings-results > li')).toHaveCount(1);
  await page.locator('.settings-results summary').click();
  await expect(
    page.getByText('This saved summary is shortened.'),
  ).toBeVisible();
  await expect(page.getByText('p4-entity-104', { exact: true })).toBeVisible();
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await expect(page.locator('html')).toHaveAttribute(
      'data-theme',
      appearance,
    );
    await assertNoOverflow(page);
    await screenshot(page, info, `saved-knowledge-${appearance}`);
    await accessibility(page, info, `saved-knowledge-${appearance}`);
  }
  await page.goto('/app-v2/settings');
  await page.getByRole('link', { name: 'Documents', exact: true }).click();
  await page
    .getByRole('searchbox', { name: 'Search documents' })
    .fill('Phase 4 document');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.getByText('105 matching saved entries')).toBeVisible();
  await page
    .getByRole('combobox', { name: 'Saved document status' })
    .selectOption('completed');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.locator('.settings-results > li')).toHaveCount(1);
  await page.locator('.settings-results summary').click();
  await expect(
    page.getByText('Partial — completion records disagree or are missing'),
  ).toBeVisible();
  await expect(
    page.getByText('Current searchability', { exact: true }),
  ).toBeVisible();
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await expect(page.locator('html')).toHaveAttribute(
      'data-theme',
      appearance,
    );
    await assertNoOverflow(page);
    await screenshot(page, info, `saved-document-${appearance}`);
    await accessibility(page, info, `saved-document-${appearance}`);
  }
  await seed(page, 'empty');
  await page
    .getByRole('button', { name: 'Reload documents', exact: true })
    .click();
  await expect(page.getByText('No matching documents')).toBeVisible();
});
