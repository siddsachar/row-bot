import type { Locator, Page, TestInfo } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';
import { asPairedPhone, onPhone, PHONES, tapPath } from './phone-helpers';
import { fixtureHeaders } from './surface-helpers';

// The phone's Chats tab (A2, D10 A): Chats | Files with search on top,
// labelled chips with Pinned first, no checkboxes, a long-press for Pin,
// Rename and Delete, Select in ⋯, and Files listing what Row-Bot made.

/** Once per backend: a pinned chat, and chats with a page, a deck, a code folder and media. */
async function seed(page: Page): Promise<void> {
  const response = await page.request.post('/__a2_fixture/seed', {
    headers: fixtureHeaders(),
  });
  expect(response.ok(), await response.text()).toBe(true);
}

/** A chat of this test's own, so renaming or pinning it touches no other. */
async function namedChat(page: Page, title: string): Promise<void> {
  const response = await page.request.post(
    `/__p3_fixture/named-conversation/${encodeURIComponent(title)}`,
    { headers: fixtureHeaders() },
  );
  expect(response.ok()).toBe(true);
}

/** A finger resting on the element until its long-press fires. */
async function longPress(page: Page, target: Locator): Promise<void> {
  const box = (await target.boundingBox())!;
  const point = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
  const touch = await page.context().newCDPSession(page);
  await touch.send('Input.dispatchTouchEvent', {
    type: 'touchStart',
    touchPoints: [point],
  });
  await page.waitForTimeout(700);
  await touch.send('Input.dispatchTouchEvent', {
    type: 'touchEnd',
    touchPoints: [],
  });
}

/** Unique in the shared backend across sizes, projects and retries. */
const own = (width: number, info: TestInfo) =>
  `${width} ${info.project.name} ${info.retry}`;
const library = (page: Page) =>
  page.getByRole('region', { name: 'Conversation library', exact: true });
const row = (page: Page, title: string) =>
  library(page).getByRole('link', { name: new RegExp(`^${title}`) });

for (const phone of PHONES)
  test.describe(phone.name, () => {
    onPhone(phone);
    const width = phone.use.viewport.width;

    test.beforeEach(async ({ page }) => {
      await seed(page);
      await asPairedPhone(page);
      await page.goto('/app-v2/');
      await expect(
        page.getByRole('navigation', { name: 'Main' }),
      ).toBeVisible();
    });

    test('Chats finds a chat by search in three taps, with labelled chips, Pinned first and no checkboxes', async ({
      page,
    }, info) => {
      const title = `Find me ${own(width, info)}`;
      await namedChat(page, title);
      const main = page.getByRole('navigation', { name: 'Main' });
      const search = page.getByRole('searchbox', {
        name: 'Search titles and messages',
      });
      expect(
        await tapPath(page, [
          main.getByRole('link', { name: 'Chats' }),
          search,
        ]),
      ).toBe(2);
      const chips = page.getByRole('radiogroup', { name: 'Conversation type' });
      for (const name of ['All', 'Chats', 'Designs', 'Code'])
        await expect(chips.getByRole('radio', { name })).toHaveText(name);
      await expect(library(page).getByRole('heading').first()).toHaveText(
        'Pinned',
      );
      await expect(row(page, 'Book club questions')).toBeVisible();
      await expect(page.getByRole('checkbox')).toHaveCount(0);
      await assertNoOverflow(page);
      await screenshot(page, info, `phone-chats-${width}`);

      // Typing is not a tap.
      await search.fill(title);
      const hit = page
        .getByRole('list', { name: 'Conversation search results' })
        .getByRole('button', { name: new RegExp(`^${title}`) })
        .first();
      expect(await tapPath(page, [hit])).toBe(1);
      await expect(page).toHaveURL(/\/app-v2\/conversations\/[^/?]+$/);
    });

    test('a long-press pins a chat and renames another; the row still opens with a tap', async ({
      page,
    }, info) => {
      const pin = `Pin me ${own(width, info)}`;
      const rename = `Rename me ${own(width, info)}`;
      const renamed = `Renamed ${own(width, info)}`;
      await namedChat(page, pin);
      await namedChat(page, rename);
      await page
        .getByRole('navigation', { name: 'Main' })
        .getByRole('link', {
          name: 'Chats',
        })
        .tap();

      // Pin: a long-press and one tap.
      await longPress(page, row(page, pin));
      const menu = page.getByRole('menu');
      await expect(menu).toBeVisible();
      await expect(page).toHaveURL(/\/app-v2\/library$/);
      await screenshot(page, info, `phone-chats-long-press-${width}`);
      expect(
        await tapPath(page, [menu.getByRole('menuitem', { name: 'Pin' })]),
      ).toBe(1);
      const pinned = library(page).getByRole('list', { name: 'Pinned' });
      await expect(
        pinned.getByRole('link', { name: new RegExp(`^${pin}`) }),
      ).toBeVisible();

      // Rename: a long-press, Rename, type, Enter.
      await longPress(page, row(page, rename));
      await menu.getByRole('menuitem', { name: 'Rename' }).tap();
      const dialog = page.getByRole('dialog', { name: rename });
      const name = dialog.getByRole('textbox', { name: 'Name', exact: true });
      await name.fill(renamed);
      await name.press('Enter');
      await expect(page.getByRole('dialog', { name: renamed })).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(row(page, renamed)).toBeVisible();

      await row(page, renamed).tap();
      await expect(page).toHaveURL(/\/app-v2\/conversations\/[^/?]+$/);
    });

    test('⋯ › Select reaches Pin, Rename, Export and Delete without a long-press or checkboxes', async ({
      page,
    }, info) => {
      await page.goto('/app-v2/library');
      await page.getByRole('button', { name: 'More for Chats' }).tap();
      await page.getByRole('menuitem', { name: 'Select' }).tap();
      const bar = page.getByRole('toolbar', { name: 'Selected conversations' });
      await expect(bar).toBeVisible();
      const choice = library(page).getByRole('button', {
        name: /^Book club questions/,
      });
      await choice.tap();
      await expect(choice).toHaveAttribute('aria-pressed', 'true');
      await expect(bar.getByText('1 selected')).toBeVisible();
      for (const name of ['Unpin', 'Rename', 'Export', 'Delete…'])
        await expect(
          bar.getByRole('button', { name, exact: true }),
        ).toBeVisible();
      await expect(page.getByRole('checkbox')).toHaveCount(0);
      await assertNoOverflow(page);
      await screenshot(page, info, `phone-chats-select-${width}`);
      await bar.getByRole('button', { name: 'Done' }).tap();
      await expect(bar).toHaveCount(0);
      await expect(row(page, 'Book club questions')).toBeVisible();
    });

    test('Files opens a page in three taps, and lists designs, code folders and media with their chats', async ({
      page,
    }, info) => {
      const main = page.getByRole('navigation', { name: 'Main' });
      const files = page.getByRole('list', { name: 'Files' });
      const plan = files.getByRole('link', { name: /^Beta launch plan/ });
      await main.getByRole('link', { name: 'Chats' }).tap();
      await page.getByRole('radio', { name: 'Files' }).tap();
      await expect(plan).toBeVisible();
      for (const item of [
        /^Community Workshop Deck.*Design · in Community workshop deck/,
        /Code folder · in Release notes cleanup/,
        /^Launch card.*Picture · in Launch assets/,
      ])
        await expect(files.getByRole('link', { name: item })).toBeVisible();
      await expect(page.getByRole('checkbox')).toHaveCount(0);
      await assertNoOverflow(page);
      await screenshot(page, info, `phone-chats-files-${width}`);

      // Other journeys leave their own pages: Pages shows pages only.
      await page.getByRole('radio', { name: 'Pages' }).tap();
      await expect(plan).toBeVisible();
      await expect(
        files.getByRole('link', { name: /^Community Workshop Deck/ }),
      ).toHaveCount(0);
      await page.getByRole('radio', { name: 'All' }).tap();

      await page.goto('/app-v2/');
      expect(
        await tapPath(page, [
          main.getByRole('link', { name: 'Chats' }),
          page.getByRole('radio', { name: 'Files' }),
          plan,
        ]),
      ).toBe(3);
      await expect(page).toHaveURL(/\/app-v2\/conversations\/a2-page-chat$/);
      await expect(
        page.getByRole('region', { name: 'Beta launch plan page' }),
      ).toBeVisible();
    });

    test('a design in Files opens in its panel with its chat', async ({
      page,
    }) => {
      await page.goto('/app-v2/library?show=files');
      await page
        .getByRole('list', { name: 'Files' })
        .getByRole('link', { name: /^Community Workshop Deck/ })
        .tap();
      await expect(page).toHaveURL(/\/app-v2\/conversations\/a2-design-chat$/);
      await expect(
        page.getByRole('region', { name: 'Design preview', exact: true }),
      ).toBeVisible();
    });
  });

test.describe('desktop', () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test('keeps the Library: its title, three segments, icon filters and a checkbox on every row', async ({
    page,
  }, info) => {
    await seed(page);
    await page.goto('/app-v2/library');
    await expect(
      page.getByRole('heading', { name: 'Library', level: 1 }),
    ).toBeVisible();
    const segments = page.getByRole('radiogroup', {
      name: 'Show in the library',
    });
    for (const name of ['Conversations', 'Pages', 'Media'])
      await expect(segments.getByRole('radio', { name })).toBeVisible();
    await expect(page.getByRole('radio', { name: 'Files' })).toHaveCount(0);
    await expect(
      page.getByRole('button', { name: 'Refresh library' }),
    ).toBeVisible();
    await expect(
      library(page).getByRole('checkbox', {
        name: 'Select Book club questions',
      }),
    ).toBeAttached();
    await expect(
      page.getByRole('button', { name: 'More for Chats' }),
    ).toHaveCount(0);
    await screenshot(page, info, 'phone-chats-desktop-library');
  });
});
