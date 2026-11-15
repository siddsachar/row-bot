import type { Locator, Page, TestInfo } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';
import { dismissNotices } from './unified-helpers';

/*
 * 5.3.1 B4 · Sandboxes in Settings › Data: a second Row-Bot on made-up data.
 * Making one seeds its folder through the real server; starting one launches
 * a second app, which the deterministic suite leaves to its slow test.
 */

const PHONE = { width: 390, height: 844 };
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

test('Settings › Data makes a sandbox from a made-up pack, and deletes it after asking', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  await page.goto('/app-v2/settings/data');
  const section = page.getByRole('region', { name: 'Sandboxes' });
  await expect(section).toContainText('A second Row-Bot on made-up data');
  const form = section.getByRole('group', { name: 'New sandbox' });
  await form.getByLabel('Name').fill('Demo shop');
  await form
    .getByLabel('Starts with')
    .selectOption({ label: 'A small shop, with its own web app and inbox' });
  await form.getByRole('button', { name: 'New sandbox' }).click();
  const row = section
    .getByRole('list', { name: 'Sandboxes' })
    .getByRole('listitem')
    .filter({ hasText: 'Demo shop' });
  await expect(row).toContainText('Stopped');
  await expect(
    row.getByRole('button', { name: 'Start Demo shop' }),
  ).toBeEnabled();
  await views(page, info, 'sandboxes', section);
  await page.setViewportSize(PHONE);
  await views(page, info, 'sandboxes-phone', section);
  await page.setViewportSize(DESKTOP);

  await row.getByRole('button', { name: 'Delete Demo shop' }).click();
  await expect(row.getByRole('group', { name: 'Confirm' })).toContainText(
    'Delete this sandbox and everything in it?',
  );
  await row.getByRole('button', { name: 'Delete', exact: true }).click();
  await expect(row).toHaveCount(0);
});
