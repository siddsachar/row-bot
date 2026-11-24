import type { Page } from '@playwright/test';
import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';
import { asPairedPhone, onPhone, PHONES, tapPath } from './phone-helpers';
import { fixtureHeaders } from './surface-helpers';

// Memory on a phone (B4, D12 A): Settings › Memory › Your memories opens a
// searchable list with labelled type chips; a tap opens a memory in the
// editor, the bin deletes it. The graph, import, clean-up and Dream Cycle stay
// on the computer, and a link to the graph's numbers says so. Earlier specs in
// the same backend save memories too, so nothing here counts them all.

/** Six memories of five kinds, saved afresh ("seeded") or taken away. */
async function memories(page: Page, state: 'seeded' | 'removed') {
  const response = await page.request.post(
    `/__phone_b4_fixture/memories/${state}`,
    { headers: fixtureHeaders() },
  );
  expect(response.ok(), await response.text()).toBe(true);
}

const list = (page: Page) => page.getByRole('list', { name: 'Memories' });
const row = (page: Page, subject: string) =>
  list(page).getByRole('button', { name: new RegExp(`^${subject}`) });

/** Home › Settings › Memory › Your memories, counted. */
async function openFromSettings(page: Page): Promise<number> {
  await page.goto('/app-v2/');
  return tapPath(page, [
    page.getByRole('navigation', { name: 'Main' }).getByRole('link', {
      name: 'Settings',
    }),
    page.getByRole('link', { name: /^Memory/ }),
    page.getByRole('link', { name: /^Your memories \(\d+\)$/ }),
  ]);
}

test.afterEach(async ({ page }) => {
  await memories(page, 'removed');
});

for (const phone of PHONES)
  test.describe(phone.name, () => {
    onPhone(phone);
    const width = phone.use.viewport.width;

    test.beforeEach(async ({ page }) => {
      await memories(page, 'seeded');
      await asPairedPhone(page);
    });

    test('Settings › Memory › Your memories opens a list to search and filter by a type chip', async ({
      page,
    }, info) => {
      expect(await openFromSettings(page)).toBe(3);
      await expect(page).toHaveURL(/\/app-v2\/?\?tab=knowledge$/);
      for (const subject of ['Community workshop', 'Sam Rivera', 'Book Club'])
        await expect(row(page, subject)).toBeVisible();
      await expect(row(page, 'Book Club')).toContainText(
        'Project · Meets on Mondays.',
      );
      await assertNoOverflow(page);
      await screenshot(page, info, `phone-memory-list-${width}`);
      await accessibility(page, info, `phone-memory-list-${width}`);

      // Search: a tap on the field, the words, a tap on the memory.
      const search = page.getByRole('searchbox', { name: 'Search memories' });
      expect(await tapPath(page, [search])).toBe(1);
      await search.fill('book');
      await expect(row(page, 'Book Club')).toBeVisible();
      await expect(row(page, 'Sam Rivera')).toContainText('Runs the Book Club');
      await expect(row(page, 'Community workshop')).toHaveCount(0);
      await screenshot(page, info, `phone-memory-search-${width}`);
      await search.fill('');

      // A labelled chip per kind; Projects shows the two projects only.
      const chips = page.getByRole('radiogroup', { name: 'Memory type' });
      await expect(chips.getByRole('radio', { name: 'All' })).toBeChecked();
      expect(
        await tapPath(page, [chips.getByRole('radio', { name: 'Projects' })]),
      ).toBe(1);
      await expect(row(page, 'Book Club')).toBeVisible();
      await expect(row(page, 'Community workshop')).toBeVisible();
      await expect(row(page, 'Sam Rivera')).toHaveCount(0);
      await expect(row(page, 'Launch checklist')).toHaveCount(0);
      await assertNoOverflow(page);
      await screenshot(page, info, `phone-memory-chip-${width}`);
    });

    test('a memory opens in the editor to change it, and its bin deletes it after the usual confirmation', async ({
      page,
    }, info) => {
      await openFromSettings(page);
      expect(await tapPath(page, [row(page, 'Book Club')])).toBe(1);
      const editor = page.getByRole('dialog', { name: 'Edit knowledge' });
      const description = editor.getByRole('textbox', { name: 'Description' });
      await expect(description).toHaveValue('Meets on Mondays.');
      await screenshot(page, info, `phone-memory-editor-${width}`);
      await description.fill('Meets on Tuesdays at the library.');
      expect(await tapPath(page, ['Save knowledge'])).toBe(1);
      await expect(editor.getByText(/^Knowledge saved\./)).toBeVisible();
      await editor.getByRole('button', { name: 'Close', exact: true }).click();
      await expect(editor).toBeHidden();
      await expect(row(page, 'Book Club')).toContainText(
        'Meets on Tuesdays at the library.',
      );

      expect(
        await tapPath(page, [
          list(page).getByRole('button', { name: 'Delete Book Club' }),
        ]),
      ).toBe(1);
      const confirm = page.getByRole('alertdialog', {
        name: "Delete 'Book Club'?",
      });
      await expect(confirm).toContainText('It cannot be undone.');
      await screenshot(page, info, `phone-memory-delete-${width}`);
      expect(await tapPath(page, ['Delete memory'])).toBe(1);
      await expect(
        page.getByText('Book Club deleted.', { exact: true }),
      ).toBeVisible();
      await expect(row(page, 'Book Club')).toHaveCount(0);
      await expect(row(page, 'Community workshop')).toBeVisible();
    });

    test('the graph, import, clean-up and Dream Cycle are not on the phone, and a link to the graph numbers says so', async ({
      page,
    }, info) => {
      await page.goto('/app-v2/?tab=knowledge');
      await expect(row(page, 'Book Club')).toBeVisible();
      await expect(page.locator('.knowledge-network-shell')).toHaveCount(0);
      await expect(
        page.getByRole('radiogroup', { name: 'Knowledge view' }),
      ).toHaveCount(0);
      for (const name of [
        'Filters',
        'Import memories',
        'Review memory',
        /Dream/,
        'Refresh knowledge',
      ])
        await expect(page.getByRole('button', { name })).toHaveCount(0);
      await expect(
        page.getByText('The graph, import and clean-up are on your computer.'),
      ).toBeVisible();

      await page.goto('/app-v2/settings/knowledge');
      await expect(
        page.getByRole('link', { name: /^Your memories \(\d+\)$/ }),
      ).toBeVisible();
      for (const name of ['Import memories', 'Review memory'])
        await expect(page.getByRole('button', { name })).toHaveCount(0);
      await expect(page.getByText('Groups of linked memories')).toHaveCount(0);
      await screenshot(page, info, `phone-memory-settings-${width}`);

      // Settings search's "Graph health" leads here.
      await page.goto('/app-v2/settings/knowledge#memory-graph');
      const notice = page.getByRole('region', {
        name: 'How your memories link',
      });
      await expect(notice).toContainText('Open this on your computer');
      await expect(notice).toBeInViewport();
      await assertNoOverflow(page);
      await screenshot(page, info, `phone-memory-notice-${width}`);
      await tapPath(page, [notice.getByRole('link', { name: 'Go to Home' })]);
      await expect(page).toHaveURL(/\/app-v2\/?$/);
    });
  });

test.describe('desktop', () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test('keeps the graph, its controls, import and clean-up, and Settings › Memory as they were', async ({
    page,
  }, info) => {
    await memories(page, 'seeded');
    await page.goto('/app-v2/?tab=knowledge');
    await expect(
      page.getByRole('combobox', { name: 'Search memories' }),
    ).toBeVisible();
    await expect(
      page.getByRole('radiogroup', { name: 'Knowledge view' }),
    ).toBeVisible();
    for (const name of ['Filters', 'Import memories', 'Review memory'])
      await expect(page.getByRole('button', { name })).toBeVisible();
    await expect(
      page.getByRole('group', { name: 'Memory types' }),
    ).toBeVisible();
    await expect(
      page.getByRole('searchbox', { name: 'Search memories' }),
    ).toHaveCount(0);
    await expect(list(page)).toHaveCount(0);
    await screenshot(page, info, 'phone-memory-desktop-knowledge');

    await page.goto('/app-v2/settings/knowledge');
    await expect(
      page.getByRole('link', { name: 'Open Knowledge' }),
    ).toBeVisible();
    for (const name of ['Import memories', 'Review memory'])
      await expect(page.getByRole('button', { name })).toBeVisible();
    await expect(page.getByText('Groups of linked memories')).toBeVisible();
    await expect(
      page.getByRole('link', { name: /^Your memories/ }),
    ).toHaveCount(0);
    await expect(
      page.getByText('The graph, import and clean-up are on your computer.'),
    ).toHaveCount(0);
    await screenshot(page, info, 'phone-memory-desktop-settings');
  });
});
