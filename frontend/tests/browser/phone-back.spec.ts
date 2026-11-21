import { request, type Locator, type Page } from '@playwright/test';
import { expect, screenshot, test } from './evidence';
import { asPairedPhone, onPhone, PHONES } from './phone-helpers';
import { fixtureHeaders } from './surface-helpers';
import { composer, newConversation } from './unified-helpers';

// Back and sheets for the native shells (A3, D16): every sheet and menu holds
// a history entry, so Android's back gesture and the browser's Back close the
// open one first, one level per Back, and only then leave the page. The status
// bar follows the theme, and the desktop is unchanged. Each test opens the
// page it starts from with its own navigation, so a second Back has somewhere
// to go.

async function fixture(page: Page, path: string): Promise<void> {
  const response = await page.request.post(path, { headers: fixtureHeaders() });
  expect(response.ok(), await response.text()).toBe(true);
}

/** "Workshop planning", three exchanges (B1's chat). */
const CHAT = '/app-v2/conversations/b1-workshop-chat';
const replies = (page: Page) =>
  page.getByRole('article', { name: 'Row-Bot message' });

/** The page a test starts from, entered after Home so Back can leave it. */
async function startAt(page: Page, path: string, ready: Locator) {
  await page.goto('/app-v2/');
  await expect(page.locator('.connection-status.connected')).toHaveText(
    'Connected',
  );
  await page.goto(path);
  await expect(ready).toBeVisible();
}

/** Back closes what is open and stays on this page. */
async function backCloses(page: Page, open: Locator) {
  const url = page.url();
  await page.goBack();
  await expect(open).toHaveCount(0);
  expect(page.url()).toBe(url);
}

/** Then a second Back leaves the page, for the one before it. */
async function backLeaves(page: Page) {
  const url = page.url();
  await page.goBack();
  await expect.poll(() => page.url()).not.toBe(url);
  // The page it went back to loads afresh: let it connect.
  await expect(page.locator('.connection-status.connected')).toHaveText(
    'Connected',
  );
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

/** Both of index.html's theme-color tags. */
const themeColors = (page: Page) =>
  page
    .locator('meta[name="theme-color"]')
    .evaluateAll((tags) => tags.map((tag) => tag.getAttribute('content')));
const surface = (page: Page) =>
  page.evaluate(() =>
    getComputedStyle(document.documentElement)
      .getPropertyValue('--surface')
      .trim(),
  );

test.afterAll(async () => {
  const backend = await request.newContext({
    baseURL: process.env.ROW_BOT_BROWSER_BASE_URL,
  });
  for (const path of ['/__phone_c2_fixture/reset', '/__d1_fixture/rules/clear'])
    await backend.post(path, { headers: fixtureHeaders() });
  await backend.dispose();
});

for (const phone of PHONES)
  test.describe(phone.name, () => {
    onPhone(phone);
    const width = phone.use.viewport.width;

    test.beforeEach(async ({ page }) => {
      await asPairedPhone(page);
    });

    test('the + sheet closes one level per Back, then Back leaves the chat', async ({
      page,
    }, info) => {
      await fixture(page, '/__b1_fixture/seed');
      await startAt(page, CHAT, replies(page).first());
      const plus = page.getByRole('button', {
        name: 'Add files and more',
        exact: true,
      });
      const sheet = page.getByRole('menu');
      await plus.tap();
      await expect(sheet).toHaveAttribute('data-level', 'plus');
      await screenshot(page, info, `phone-back-plus-${width}`);
      await backCloses(page, sheet);

      // Chat settings is a level of the same sheet: Back goes up to +.
      await plus.tap();
      await sheet.getByRole('menuitem', { name: /^Chat settings/ }).tap();
      await expect(sheet).toHaveAttribute('data-level', 'settings');
      const url = page.url();
      await page.goBack();
      await expect(sheet).toHaveAttribute('data-level', 'plus');
      expect(page.url()).toBe(url);
      await backCloses(page, sheet);
      await expect(composer(page)).toBeVisible();
      await backLeaves(page);
      await expect(page).toHaveURL(/\/app-v2\/$/);
    });

    test('a message’s long-press sheet, the model popover and ⋯ › Details close on Back', async ({
      page,
    }) => {
      await fixture(page, '/__b1_fixture/seed');
      await startAt(page, CHAT, replies(page).first());
      await expect(replies(page)).toHaveCount(3);
      await longPress(page, replies(page).first());
      const sheet = page.getByRole('menu');
      await expect(sheet.getByRole('menuitem').first()).toBeVisible();
      await backCloses(page, sheet);

      await page
        .locator('header.conversation-heading')
        .getByRole('button', { name: /^(Model: |Choose a model$)/ })
        .tap();
      const picker = page.getByRole('dialog', { name: 'Choose a model' });
      await expect(picker).toBeVisible();
      await backCloses(page, picker);
      // Closed by its own way out, it leaves no step behind.
      const more = page
        .locator('header.conversation-heading')
        .getByRole('button', { name: 'Conversation menu' });
      await more.tap();
      await expect(page.getByRole('menu')).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(page.getByRole('menu')).toHaveCount(0);
      // ⋯ › Details: the sheet takes the menu's step, one Back closes it.
      await more.tap();
      await page.getByRole('menuitem', { name: 'Conversation details' }).tap();
      const details = page.getByRole('dialog');
      await expect(details).toBeVisible();
      await backCloses(page, details);
      await backLeaves(page);
      await expect(page).toHaveURL(/\/app-v2\/$/);
    });

    test('Always allow over an approval’s sheet closes one level per Back, and Back stays on Home', async ({
      page,
    }, info) => {
      await fixture(page, '/__d1_fixture/rules/seed');
      await newConversation(page);
      await composer(page).fill('Run the tests always allow fixture');
      await page.getByRole('button', { name: 'Send', exact: true }).click();
      await expect(
        page.getByRole('complementary', { name: /^Approval required/ }),
      ).toContainText('npm test', { timeout: 15_000 });
      await page.goto('/app-v2/');
      const card = page
        .getByRole('list', { name: 'Needs you' })
        .getByRole('listitem')
        .filter({ hasText: 'npm test' })
        .first();
      await card.getByRole('button', { name: /^Open / }).tap();
      const review = page.getByRole('dialog', { name: 'Review and approve' });
      await review
        .getByRole('button', { name: 'More choices', exact: true })
        .tap();
      await page.getByRole('menuitem', { name: /Always allow…/ }).tap();
      const always = page.getByRole('dialog', { name: 'Always allow this?' });
      await expect(always).toContainText('Always allow npm test');
      await screenshot(page, info, `phone-back-always-allow-${width}`);

      const home = page.url();
      await page.goBack();
      await expect(always).toHaveCount(0);
      await expect(review).toBeVisible();
      expect(page.url()).toBe(home);
      await screenshot(page, info, `phone-back-approval-sheet-${width}`);
      await backCloses(page, review);
      // Nothing was approved: the card still waits. Deny it to leave none behind.
      await card.getByRole('button', { name: /^Deny/ }).tap();
      await expect(card).toHaveCount(0);
    });

    test('a workflow’s sheet closes on Back, and Open chat takes its step', async ({
      page,
    }, info) => {
      await fixture(page, '/__phone_c2_fixture/workflows');
      const list = page.getByRole('list', { name: 'Workflows' });
      await startAt(page, '/app-v2/?tab=workflows', list);
      const open = () =>
        list
          .getByRole('listitem')
          .filter({ hasText: 'Morning Brief' })
          .getByRole('button', { name: /^Morning Brief/ })
          .tap();
      const sheet = page.getByRole('dialog', { name: 'Morning Brief' });
      await open();
      await expect(
        sheet.getByRole('list', { name: 'Recent runs' }),
      ).toBeVisible();
      await screenshot(page, info, `phone-back-workflow-${width}`);
      await backCloses(page, sheet);

      // Open chat: Back from the chat returns to Workflows, in one step.
      await open();
      await sheet.getByRole('button', { name: 'Open chat', exact: true }).tap();
      await expect(page).toHaveURL(/\/app-v2\/conversations\/[^/]+$/);
      await page.goBack();
      await expect(page).toHaveURL(/\/app-v2\/?\?tab=workflows$/);
      await expect(sheet).toHaveCount(0);
      await backLeaves(page);
      await expect(page).toHaveURL(/\/app-v2\/$/);
    });

    test('a Chats long-press menu and Search close on Back', async ({
      page,
    }) => {
      const title = `Back press ${width} ${test.info().project.name} ${test.info().retry}`;
      await fixture(
        page,
        `/__p3_fixture/named-conversation/${encodeURIComponent(title)}`,
      );
      const library = page.getByRole('region', {
        name: 'Conversation library',
        exact: true,
      });
      await startAt(page, '/app-v2/library', library);
      const row = library.getByRole('link', { name: new RegExp(`^${title}`) });
      await longPress(page, row);
      const menu = page.getByRole('menu');
      await expect(menu.getByRole('menuitem', { name: /Pin/ })).toBeVisible();
      await backCloses(page, menu);

      await page.getByRole('button', { name: 'Search', exact: true }).tap();
      const search = page.getByRole('dialog', { name: 'Search' });
      await expect(search).toBeVisible();
      await backCloses(page, search);
      await backLeaves(page);
      await expect(page).toHaveURL(/\/app-v2\/$/);
    });

    test('Back and ‹ from a More settings page both return to More settings', async ({
      page,
    }) => {
      const title = page.getByRole('heading', { level: 1 });
      await startAt(page, '/app-v2/settings', title);
      await page.getByRole('link', { name: 'More settings' }).tap();
      await expect(title).toHaveText('More settings');
      await page.getByRole('link', { name: 'Updates' }).tap();
      await expect(title).toHaveText('Updates');
      await page.goBack();
      await expect(title).toHaveText('More settings');
      await page.getByRole('link', { name: 'Updates' }).tap();
      await page.getByRole('link', { name: 'Back to More settings' }).tap();
      await expect(title).toHaveText('More settings');
    });

    test('the status bar follows the theme the app shows, light and dark', async ({
      page,
    }) => {
      await page.emulateMedia({ colorScheme: 'light' });
      await startAt(
        page,
        '/app-v2/settings',
        page.getByRole('heading', { level: 1 }),
      );
      const light = await surface(page);
      expect(light).toMatch(/^#/);
      await expect.poll(() => themeColors(page)).toEqual([light, light]);
      await page.emulateMedia({ colorScheme: 'dark' });
      await expect.poll(() => surface(page)).not.toBe(light);
      const dark = await surface(page);
      await expect.poll(() => themeColors(page)).toEqual([dark, dark]);
      // This phone's Theme set apart from the system's: the bar follows the app.
      await page.emulateMedia({ colorScheme: 'light' });
      await page
        .getByRole('radiogroup', { name: /Theme/ })
        .getByRole('radio', { name: 'Dark', exact: true })
        .tap();
      await expect.poll(() => themeColors(page)).toEqual([dark, dark]);
      await page
        .getByRole('radiogroup', { name: /Theme/ })
        .getByRole('radio', { name: 'System', exact: true })
        .tap();
      await expect.poll(() => themeColors(page)).toEqual([light, light]);
    });

    test('the app shell never pulls to refresh, and lists still scroll', async ({
      page,
    }) => {
      await startAt(
        page,
        '/app-v2/settings',
        page.getByRole('heading', { level: 1 }),
      );
      expect(
        await page.evaluate(() => [
          getComputedStyle(document.documentElement).overscrollBehaviorY,
          getComputedStyle(document.body).overscrollBehaviorY,
        ]),
      ).toEqual(['none', 'none']);
    });
  });

test.describe('desktop', () => {
  test.use({
    viewport: { width: 1440, height: 900 },
    hasTouch: false,
    isMobile: false,
  });

  test('keeps menus as popovers with no history step, and dialogs as before', async ({
    page,
  }) => {
    await fixture(page, '/__b1_fixture/seed');
    await page.goto(CHAT);
    await expect(replies(page).first()).toBeVisible();
    const before = await page.evaluate(() => window.history.length);
    await page
      .getByRole('button', { name: 'Add files and more', exact: true })
      .first()
      .click();
    const menu = page.getByRole('menu');
    await expect(menu).toBeVisible();
    expect(await page.evaluate(() => window.history.length)).toBe(before);
    expect(
      await page.evaluate(
        () => getComputedStyle(document.documentElement).overscrollBehaviorY,
      ),
    ).toBe('auto');
    await page.keyboard.press('Escape');
    await expect(menu).toHaveCount(0);
  });
});
