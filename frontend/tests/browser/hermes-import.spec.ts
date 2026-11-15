import type { Page, TestInfo } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';

// Settings › Data › Import from another assistant with a synthetic Hermes
// home on the fixture app: the preview says where each item goes, and the
// memories brought over (and past chats, when ticked) wait in Import memories
// for the person to check. Only those are chosen, so the fixture's own settings
// stay as they are.

async function seed(page: Page): Promise<string> {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated browser runner');
  const response = await page.request.post('/__hm_fixture/seed', {
    headers: { 'X-Fixture-Token': token, Origin: new URL(base).origin },
  });
  expect(response.ok(), await response.text()).toBe(true);
  return ((await response.json()) as { source: string }).source;
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
  test(`Hermes memories come over to be reviewed, and the preview says where each item goes (${appearance})`, async ({
    page,
  }, info) => {
    const source = await seed(page);
    await page.emulateMedia({ colorScheme: appearance });
    await page.goto('/app-v2/settings/data');
    await page.locator('summary', { hasText: 'Import…' }).click();
    await page.getByLabel('Source folder').fill(source);
    await page.getByRole('button', { name: 'Scan folders' }).click();

    const preview = page.getByLabel('Migration preview');
    await expect(preview.getByText('Personality from SOUL.md')).toBeVisible();
    await expect(
      preview.getByText(/Becomes Row-Bot's personality: “Calm, direct/),
    ).toBeVisible();
    await expect(preview.getByLabel(/MCP server: linear/)).not.toBeChecked();
    await expect(preview.getByText(/Fill in LINEAR_API_KEY/)).toBeVisible();
    await expect(
      preview.getByText(/Not brought over: Row-Bot doesn't connect to nous/),
    ).toBeVisible();
    await expect(
      preview.getByLabel(/Default model: nous › Hermes-4-405B/),
    ).toBeDisabled();
    // Past chats are offered, unticked; cron jobs are named as not converted.
    const chats = preview.getByLabel(
      /Past chats from state\.db: 3 conversations/,
    );
    await expect(chats).toBeEnabled();
    await expect(chats).not.toBeChecked();
    await expect(
      preview.getByText(
        "Cron jobs aren't converted: make a workflow for each one in Workflows.",
        { exact: true },
      ),
    ).toBeVisible();
    // Every row shows all its words, none drawn over the next (touch screens too).
    expect(
      await preview
        .locator('.migration-item')
        .evaluateAll(
          (rows) =>
            rows.filter((row) => row.scrollHeight > row.clientHeight + 1)
              .length,
        ),
    ).toBe(0);
    await shoot(page, info, 'a-preview', appearance);

    await preview.getByRole('button', { name: 'Clear all' }).click();
    await preview.getByLabel(/Memories from MEMORY\.md: 4 entries/).check();
    await preview.getByLabel(/About you, from USER\.md: 2 entries/).check();
    await chats.check();
    await expect(preview.getByText(/3 selected/)).toBeVisible();
    await preview.getByRole('button', { name: 'Apply selected items' }).click();
    await page
      .getByRole('alertdialog', { name: 'Confirm migration' })
      .getByRole('button', { name: 'Confirm import' })
      .click();

    await expect(
      page.getByText(
        '6 memories and 3 past chats are ready to review. Nothing is saved to memory until you check them.',
      ),
    ).toBeVisible();
    const review = page.getByRole('button', { name: 'Review memories' });
    await review.scrollIntoViewIfNeeded();
    await shoot(page, info, 'b-receipt', appearance);

    await review.click();
    const dialog = page.getByRole('dialog', { name: 'Import memories' });
    const found = dialog.getByRole('list', { name: 'Memories found' });
    await expect(found).toBeVisible();
    await expect(
      found.getByText(
        'User prefers local-first tools unless cloud is explicitly faster.',
      ),
    ).toBeVisible();
    await expect(dialog.getByText(/6 memories found/)).toBeVisible();
    // Looking in the past chats stays the person's choice, made here.
    await expect(
      dialog.getByText('Also look for memories in 3 past conversations'),
    ).toBeVisible();
    await expect(
      dialog.getByLabel('Also look in past conversations'),
    ).not.toBeChecked();
    await shoot(page, info, 'c-review', appearance);
  });
}
