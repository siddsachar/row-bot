import type { Page } from '@playwright/test';
import { expect, screenshot, test } from './evidence';
import { asPairedPhone, onPhone, PHONES, tapPath } from './phone-helpers';

// The phone's navigation (A1, D1 and D15): four tabs and no drawer, ‹ in a
// chat goes back where it came from, Workflows is a place of its own, and one
// Search finds chats, files and settings.

/** A swipe in from the screen's left edge, as a thumb does. */
async function swipeFromLeftEdge(page: Page): Promise<void> {
  const touch = await page.context().newCDPSession(page);
  const y = Math.round(page.viewportSize()!.height / 2);
  await touch.send('Input.dispatchTouchEvent', {
    type: 'touchStart',
    touchPoints: [{ x: 4, y }],
  });
  for (let step = 1; step <= 5; step += 1)
    await touch.send('Input.dispatchTouchEvent', {
      type: 'touchMove',
      touchPoints: [{ x: 4 + step * 30, y }],
    });
  await touch.send('Input.dispatchTouchEvent', {
    type: 'touchEnd',
    touchPoints: [],
  });
}

for (const phone of PHONES)
  test.describe(phone.name, () => {
    onPhone(phone);
    const width = phone.use.viewport.width;

    test.beforeEach(async ({ page }) => {
      await asPairedPhone(page);
      await page.goto('/app-v2/');
      await expect(
        page.getByRole('navigation', { name: 'Main' }),
      ).toBeVisible();
    });

    test('Chats, a chat and ‹ back to Chats is three taps; a swipe from the left edge goes back too; no drawer', async ({
      page,
    }, info) => {
      const main = page.getByRole('navigation', { name: 'Main' });
      const back = page.getByRole('button', { name: 'Back', exact: true });
      const firstChat = page
        .getByRole('region', { name: 'Conversation library' })
        .locator('a[href*="/conversations/"]')
        .first();
      await expect(
        page.getByRole('button', { name: 'Toggle navigation' }),
      ).toHaveCount(0);

      expect(
        await tapPath(page, [
          main.getByRole('link', { name: 'Chats' }),
          firstChat,
          back,
        ]),
      ).toBe(3);
      await expect(page).toHaveURL(/\/app-v2\/library$/);

      await firstChat.tap();
      await expect(back).toBeVisible();
      await screenshot(page, info, `phone-shell-chat-${width}`);
      await swipeFromLeftEdge(page);
      await expect(page).toHaveURL(/\/app-v2\/library$/);
      await expect(page.getByRole('dialog')).toHaveCount(0);
    });

    test('‹ from the installed app’s New chat lands on Home, and a reload there opens no other chat', async ({
      page,
    }) => {
      let creates = 0;
      page.on('request', (request) => {
        if (
          request.method() === 'POST' &&
          new URL(request.url()).pathname.endsWith('/commands') &&
          request.postDataJSON()?.type === 'conversation.create'
        )
          creates += 1;
      });
      await page.goto('/app-v2/?new=chat');
      await expect(page).toHaveURL(/\/app-v2\/conversations\/[^/?]+$/);
      expect(creates).toBe(1);
      await page.getByRole('button', { name: 'Back', exact: true }).tap();
      await expect(page).toHaveURL(/\/app-v2\/?$/);
      await page.reload();
      // Home's ask box shows once connected, when the shortcut would act.
      await expect(
        page.getByRole('textbox', { name: 'Ask Row-Bot' }),
      ).toBeVisible();
      await expect(page).toHaveURL(/\/app-v2\/?$/);
      expect(creates).toBe(1);
    });

    test('Workflows is one tap and has its own header, without Home’s sub-tabs', async ({
      page,
    }, info) => {
      const main = page.getByRole('navigation', { name: 'Main' });
      expect(
        await tapPath(page, [main.getByRole('link', { name: 'Workflows' })]),
      ).toBe(1);
      await expect(page).toHaveURL(/\/app-v2\/?\?tab=workflows$/);
      await expect(
        page.getByRole('group', { name: 'Workspace controls' }),
      ).toHaveText('Workflows');
      await expect(
        page.getByRole('tablist', { name: 'Home capabilities' }),
      ).toBeHidden();
      await screenshot(page, info, `phone-shell-workflows-${width}`);
    });

    test('Search finds a chat and a settings page in two taps each, with no commands or keyboard hints', async ({
      page,
    }, info) => {
      const search = page.getByRole('button', { name: 'Search', exact: true });
      const dialog = page.getByRole('dialog', { name: 'Search' });
      const field = dialog.getByRole('searchbox', {
        name: 'Search chats, files and settings',
      });

      await search.tap();
      const recent = dialog.getByRole('group', { name: 'Recent' });
      await expect(recent.getByRole('option').first()).toBeVisible();
      await expect(dialog.getByRole('group', { name: 'Commands' })).toHaveCount(
        0,
      );
      await expect(dialog.locator('kbd')).toHaveCount(0);
      const title = 'Phase 1 conversation A';
      await field.fill(title);
      const chat = dialog
        .getByRole('group', { name: 'Chats' })
        .getByRole('option', { name: title, exact: true });
      await expect(chat).toBeVisible();
      await screenshot(page, info, `phone-shell-search-${width}`);
      await page.keyboard.press('Escape');
      await expect(dialog).toHaveCount(0);

      // Typing is not a tap: open, type, then tap the result.
      expect(await tapPath(page, [search])).toBe(1);
      await field.fill(title);
      expect(await tapPath(page, [chat])).toBe(1);
      await expect(page).toHaveURL(/\/app-v2\/conversations\/[^/?]+$/);
      // ‹ returns to where Search was opened.
      await page.getByRole('button', { name: 'Back', exact: true }).tap();
      await expect(page).toHaveURL(/\/app-v2\/?$/);

      expect(await tapPath(page, [search])).toBe(1);
      await field.fill('models');
      expect(
        await tapPath(page, [
          dialog
            .getByRole('group', { name: 'Settings' })
            .getByRole('option', { name: /^Models settings/ }),
        ]),
      ).toBe(1);
      await expect(page).toHaveURL(/\/app-v2\/settings\/models$/);
    });
  });

test.describe('desktop', () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test('keeps its sidebar, Workspace commands and the conversation header as they were', async ({
    page,
  }, info) => {
    await page.goto('/app-v2/');
    await expect(
      page.getByRole('navigation', { name: 'Workspace navigation' }),
    ).toBeVisible();
    await expect(page.getByRole('navigation', { name: 'Main' })).toHaveCount(0);
    await expect(
      page.getByRole('button', { name: 'Toggle navigation', exact: true }),
    ).toHaveAttribute('aria-expanded', 'true');
    await page.keyboard.press('Control+k');
    const palette = page.getByRole('dialog', { name: 'Workspace commands' });
    await expect(
      palette.getByRole('group', { name: 'Commands' }),
    ).toBeVisible();
    await page.keyboard.press('Escape');

    await page.goto('/app-v2/conversations/p1-browser-a');
    await expect(
      page.getByRole('button', { name: 'Workspace commands', exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'Back', exact: true }),
    ).toHaveCount(0);
    await screenshot(page, info, 'phone-shell-desktop-chat');
  });
});
