import type { Locator, Page } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';
import { asPairedPhone, onPhone, PHONES, tapPath } from './phone-helpers';
import { fixtureHeaders } from './surface-helpers';

// The phone's conversation header (B1, D8 A and D14 A): ‹, the title with the
// model under it (a tap switches it), New chat and ⋯ (Find, Conversation
// details, Export, Rename); icons only under the newest answer and the
// person's last message, a long-press for the others.

async function fixture(page: Page, path: string): Promise<void> {
  const response = await page.request.post(path, { headers: fixtureHeaders() });
  expect(response.ok(), await response.text()).toBe(true);
}

/** Once per backend: "Workshop planning", three exchanges. */
const CHAT = '/app-v2/conversations/b1-workshop-chat';

/** A finger resting on the element, or moving `drift` px as it would to scroll. */
async function press(page: Page, target: Locator, drift = 0): Promise<void> {
  const box = (await target.boundingBox())!;
  const point = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
  const touch = await page.context().newCDPSession(page);
  await touch.send('Input.dispatchTouchEvent', {
    type: 'touchStart',
    touchPoints: [point],
  });
  if (drift)
    await touch.send('Input.dispatchTouchEvent', {
      type: 'touchMove',
      touchPoints: [{ x: point.x, y: point.y - drift }],
    });
  await page.waitForTimeout(700);
  await touch.send('Input.dispatchTouchEvent', {
    type: 'touchEnd',
    touchPoints: [],
  });
}

const header = (page: Page) => page.locator('header.conversation-heading');
const replies = (page: Page) =>
  page.getByRole('article', { name: 'Row-Bot message' });

async function openChat(page: Page): Promise<void> {
  await page.goto(CHAT);
  await expect(replies(page)).toHaveCount(3);
}

test.beforeEach(async ({ page }) => {
  await fixture(page, '/__b1_fixture/seed');
});

for (const phone of PHONES)
  test.describe(phone.name, () => {
    onPhone(phone);
    const width = phone.use.viewport.width;

    test.beforeEach(async ({ page }) => {
      await asPairedPhone(page);
    });
    test.afterEach(async ({ page }) => {
      await fixture(page, '/__s8_fixture/models/restore');
    });

    test('the model under the title switches it in two taps, and New chat starts one in one', async ({
      page,
    }, info) => {
      await fixture(page, '/__s8_fixture/models/seed');
      await openChat(page);
      const row = header(page);
      await expect(
        row.getByRole('heading', { name: 'Workshop planning' }),
      ).toBeVisible();
      await expect(row.getByRole('button', { name: 'Back' })).toBeVisible();
      await assertNoOverflow(page);
      await screenshot(page, info, `phone-conversation-${width}`);

      const picker = page.getByRole('dialog', { name: 'Choose a model' });
      let taps = await tapPath(page, [
        row.getByRole('button', { name: /^(Model: |Choose a model$)/ }),
      ]);
      // The first model on screen that this chat isn't using (the list opens
      // scrolled to the current one).
      const options = picker.getByRole('option');
      await expect(options.first()).toBeAttached();
      const index = await options.evaluateAll((rows) => {
        const list = rows[0]
          .closest('[role="listbox"]')!
          .getBoundingClientRect();
        return rows.findIndex((row) => {
          const box = row.getBoundingClientRect();
          return (
            row.getAttribute('aria-selected') !== 'true' &&
            box.top >= list.top &&
            box.bottom <= list.bottom
          );
        });
      });
      expect(index).toBeGreaterThanOrEqual(0);
      const option = options.nth(index);
      const choice = (
        await option.locator('.model-picker-option-name').textContent()
      )?.trim();
      expect(choice).toBeTruthy();
      await screenshot(page, info, `phone-conversation-model-${width}`);
      taps += await tapPath(page, [option]);
      expect(taps).toBe(2);
      await expect(picker).toHaveCount(0);
      await expect(
        row.getByRole('button', { name: `Model: ${choice}` }),
      ).toBeVisible();

      // A tap on the title itself opens the model sheet too.
      const title = (await row
        .getByRole('heading', { name: 'Workshop planning' })
        .boundingBox())!;
      await page.touchscreen.tap(title.x + 20, title.y + title.height / 2);
      await expect(picker).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(picker).toHaveCount(0);

      const before = page.url();
      expect(
        await tapPath(page, [row.getByRole('button', { name: 'New chat' })]),
      ).toBe(1);
      await expect(page).not.toHaveURL(before);
      await expect(page).toHaveURL(/\/app-v2\/conversations\/[^/?]+$/);
      await expect(
        page.getByRole('textbox', { name: 'Message', exact: true }),
      ).toBeFocused();
    });

    test('⋯ starts with Find, Details, Export and Rename, and Find opens in two taps', async ({
      page,
    }, info) => {
      await openChat(page);
      const more = header(page).getByRole('button', {
        name: 'Conversation menu',
      });
      await more.tap();
      const menu = page.getByRole('menu');
      await expect(menu).toBeVisible();
      await expect(menu.getByRole('menuitem').nth(0)).toHaveAccessibleName(
        'Find in conversation',
      );
      for (const [index, name] of [
        'Conversation details',
        'Export conversation',
        'Rename conversation',
      ].entries())
        await expect(menu.getByRole('menuitem').nth(index + 1)).toHaveText(
          name,
        );
      // Incognito is in + › Chat settings (B2).
      await expect(
        menu.getByRole('menuitem', { name: /^Incognito/ }),
      ).toHaveCount(0);
      await screenshot(page, info, `phone-conversation-menu-${width}`);
      await page.keyboard.press('Escape');
      await expect(menu).toHaveCount(0);

      expect(
        await tapPath(page, [
          more,
          page.getByRole('menuitem', { name: 'Find in conversation' }),
        ]),
      ).toBe(2);
      await expect(
        page.getByRole('dialog', { name: 'Find in conversation' }),
      ).toBeVisible();
    });

    test('only the newest answer and your last message keep icons; an older one opens its actions with a long-press', async ({
      page,
    }, info) => {
      await page
        .context()
        .grantPermissions(['clipboard-read', 'clipboard-write']);
      await openChat(page);
      const bars = page.getByRole('toolbar');
      await expect(bars).toHaveCount(2);
      await expect(
        replies(page).last().getByRole('toolbar', {
          name: 'Row-Bot message actions',
        }),
      ).toBeVisible();
      await expect(
        page
          .getByRole('article', { name: 'You message' })
          .last()
          .getByRole('toolbar', { name: 'You message actions' }),
      ).toBeVisible();
      // Retry the newest answer: one tap, on screen.
      await expect(
        replies(page).last().getByRole('button', { name: 'Retry' }),
      ).toBeInViewport();

      // A finger that moves is scrolling: nothing opens.
      const older = replies(page).first();
      await press(page, older, 30);
      await expect(page.getByRole('menu')).toHaveCount(0);

      // Copy an older answer: a long-press and one tap.
      await press(page, older);
      const sheet = page.getByRole('menu');
      await expect(sheet).toBeVisible();
      // Teach is there too: Learning is on from the start (O195), and the
      // press sheet is where an older reply keeps its extras.
      await expect(sheet.getByRole('menuitem')).toHaveText([
        'Copy message',
        'Select text',
        'Read aloud',
        'Fork from here',
        'Teach Row-Bot',
      ]);
      await screenshot(page, info, `phone-conversation-long-press-${width}`);
      expect(
        await tapPath(page, [
          sheet.getByRole('menuitem', { name: 'Copy message' }),
        ]),
      ).toBe(1);
      await expect(sheet).toHaveCount(0);
      expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(
        'The deck is ready: a title slide and an agenda.',
      );
    });
  });

test.describe('desktop', () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test('keeps the header icons and the model chip, and icons under every message', async ({
    page,
  }, info) => {
    await openChat(page);
    const row = header(page);
    for (const name of ['Find', 'Export', 'Conversation details'])
      await expect(
        row.getByRole('button', { name, exact: true }),
      ).toBeVisible();
    await expect(row.getByRole('button', { name: 'New chat' })).toHaveCount(0);
    await expect(
      row.getByRole('button', { name: 'Conversation menu' }),
    ).toHaveCount(0);
    await expect(row.getByRole('button', { name: /^Model: / })).toHaveCount(0);
    await expect(page.getByRole('toolbar')).toHaveCount(6);
    await screenshot(page, info, 'phone-conversation-desktop');
  });
});
