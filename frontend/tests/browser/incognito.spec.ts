import type { Locator, Page, TestInfo } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';
import { composer, dismissNotices, newConversation } from './unified-helpers';

/*
 * N11 · Incognito chats: New chat ▾ › New incognito chat, the header's mark
 * and the composer's words, the sidebar row, the ⋯ switch, the toast when an
 * existing chat turns incognito, and the "Save to memory?" card. The chats,
 * the switch and the card run through the real server; the model's turns are
 * scripted, and the card is the real save_memory tool's own.
 */

const PHONE = { width: 390, height: 844 };
const DESKTOP = { width: 1280, height: 900 };
const HINT = 'Incognito: nothing here is saved to memory';
const SAVE_CARD = 'Approval required: Save to memory? This chat is incognito.';

async function views(
  page: Page,
  info: TestInfo,
  name: string,
  keepNotices = false,
) {
  if (!keepNotices) {
    await dismissNotices(page);
  }
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await expect(page.locator('html')).toHaveAttribute(
      'data-theme',
      appearance,
    );
    await assertNoOverflow(page);
    await screenshot(page, info, `${name}-${appearance}`);
  }
  await page.emulateMedia({ colorScheme: 'light' });
}

/** Evidence at desktop and phone size, light and dark; `reach` brings each size to the state. */
async function everywhere(
  page: Page,
  info: TestInfo,
  name: string,
  reach: (phone: boolean) => Promise<void>,
  keepNotices = false,
) {
  for (const phone of [false, true]) {
    await resize(page, phone ? PHONE : DESKTOP);
    await reach(phone);
    await views(page, info, phone ? `${name}-phone` : name, keepNotices);
    await closeOverlays(page);
  }
  await resize(page, DESKTOP);
}

/** A new size, once its layout has rendered: just after a resize the desktop
 *  sidebar can still be showing before the phone's drawer replaces it. */
async function resize(page: Page, size: { width: number; height: number }) {
  await page.setViewportSize(size);
  await page.evaluate(
    () =>
      new Promise<void>((done) =>
        requestAnimationFrame(() => requestAnimationFrame(() => done())),
      ),
  );
}

/** Closes open menus and the phone's navigation drawer, one Escape each. */
async function closeOverlays(page: Page) {
  const open = page.locator('[role="menu"], [role="dialog"]');
  for (let tries = 0; tries < 4 && (await open.count()); tries += 1)
    await page.keyboard.press('Escape');
  await expect(open).toHaveCount(0);
}

function navigation(page: Page): Locator {
  return page.getByRole('navigation', {
    name: 'Workspace navigation',
    exact: true,
  });
}

async function showNavigation(page: Page): Promise<Locator> {
  const nav = navigation(page);
  // Open when its own New chat control shows, not just its landmark (a closing
  // drawer's or the folded rail's).
  const open = nav.getByRole('button', {
    name: 'New chat options',
    exact: true,
  });
  // A drawer on phones; on desktop the sidebar may have folded to its rail,
  // and a resize can fold it again just after it opened: try until it stays.
  await expect(async () => {
    if (!(await open.isVisible())) {
      const toggles = page.getByRole('button', {
        name: /^(Toggle|Expand) navigation$/,
      });
      for (const toggle of await toggles.all())
        if (await toggle.isVisible()) {
          await toggle.click();
          break;
        }
    }
    await expect(open).toBeVisible({ timeout: 1000 });
  }).toPass({ timeout: 15_000 });
  return nav;
}

/** The phone's navigation is a drawer over the chat: closed to see the chat. */
async function hideNavigation(page: Page) {
  await closeOverlays(page);
  await expect(composer(page)).toBeVisible();
}

async function rename(page: Page, title: string) {
  await page.locator('.conversation-heading h1').click();
  const field = page.getByRole('textbox', { name: 'Conversation title' });
  await field.fill(title);
  await field.press('Enter');
  await expect(
    page.getByRole('heading', { level: 1, name: title, exact: true }),
  ).toBeVisible();
}

async function newIncognitoChat(page: Page, title: string) {
  await page.goto('/app-v2/');
  await expect(page.locator('.connection-status.connected')).toHaveText(
    'Connected',
  );
  const nav = await showNavigation(page);
  await nav
    .getByRole('button', { name: 'New chat options', exact: true })
    .click();
  await page.getByRole('menuitem', { name: /^New incognito chat/ }).click();
  await expect(page).toHaveURL(/\/app-v2\/conversations\/[^/?]+/);
  await expect(composer(page)).toHaveAttribute(
    'placeholder',
    'Message Row-Bot (incognito)',
  );
  await expect(page.getByRole('img', { name: HINT })).toBeVisible();
  await rename(page, title);
}

/** The composer's hint stays on one line: as tall empty as with one typed letter. */
async function placeholderOnOneLine(page: Page) {
  const field = composer(page);
  const height = () =>
    field.evaluate((element) => element.getBoundingClientRect().height);
  await field.fill('x');
  const oneLine = await height();
  await field.fill('');
  await expect.poll(height).toBe(oneLine);
}

async function send(page: Page, text: string, reply: string) {
  await composer(page).fill(text);
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(
    page
      .getByRole('log', { name: 'Conversation' })
      .getByText(reply, { exact: true }),
  ).toBeVisible();
}

test('New chat ▾ starts an incognito chat that says so in its header, composer, row and ⋯ menu', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  await newIncognitoChat(page, 'Weekend away');
  await send(
    page,
    'Find me somewhere quiet for a weekend away',
    'Here are three quiet places for a weekend away: Hay-on-Wye, Whitby and the Lake District.',
  );

  await everywhere(page, info, 'new-chat-menu', async () => {
    const nav = await showNavigation(page);
    await nav
      .getByRole('button', { name: 'New chat options', exact: true })
      .click();
    await expect(
      page.getByRole('menuitem', { name: /^New incognito chat/ }),
    ).toContainText('Nothing in it is saved to memory');
  });

  await everywhere(page, info, 'incognito-chat', async (phone) => {
    await hideNavigation(page);
    await expect(page.getByRole('img', { name: HINT })).toBeVisible();
    await expect(composer(page)).toHaveAttribute(
      'placeholder',
      phone ? /\(incognito\)$/ : 'Message Row-Bot (incognito)',
    );
    await placeholderOnOneLine(page);
  });

  await everywhere(page, info, 'sidebar-row', async () => {
    const nav = await showNavigation(page);
    await expect(
      nav.getByRole('button', {
        name: 'Weekend away (incognito)',
        exact: true,
      }),
    ).toBeVisible();
  });

  await everywhere(page, info, 'incognito-menu', async (phone) => {
    if (phone) {
      await hideNavigation(page);
      await page
        .getByRole('button', { name: 'Conversation menu', exact: true })
        .click();
    } else {
      const nav = await showNavigation(page);
      await nav
        .getByRole('button', { name: 'Actions for Weekend away', exact: true })
        .click();
    }
    const item = page.getByRole('menuitem', { name: /^Incognito/ });
    await expect(item).toHaveAttribute('aria-current', 'true');
    await expect(item).toContainText('On: nothing here is saved to memory');
  });
});

test('turning incognito on for an existing chat says what stays and links to Knowledge', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  await newConversation(page);
  await send(
    page,
    'Plan a weekend in Leeds',
    "Here's a weekend in Leeds: Saturday at the Royal Armouries and Kirkgate Market, Sunday in Roundhay Park.",
  );
  await rename(page, 'Leeds trip');
  await expect(page.getByRole('img', { name: HINT })).toHaveCount(0);

  const toggle = async (phone: boolean) => {
    if (phone) {
      await hideNavigation(page);
      await page
        .getByRole('button', { name: 'Conversation menu', exact: true })
        .click();
    } else {
      const nav = await showNavigation(page);
      await nav
        .getByRole('button', { name: 'Actions for Leeds trip', exact: true })
        .click();
    }
    await page.getByRole('menuitem', { name: /^Incognito/ }).click();
  };
  await everywhere(
    page,
    info,
    'turned-on-toast',
    async (phone) => {
      if (await page.getByRole('img', { name: HINT }).isVisible()) {
        // Off again first (the size before turned it on), so this size turns it on.
        await toggle(phone);
        await expect(page.getByRole('img', { name: HINT })).toHaveCount(0);
        await expect(
          page.locator('.toast').filter({ hasText: 'Incognito is off' }),
        ).toBeVisible();
        await dismissNotices(page);
      }
      await toggle(phone);
      await expect(page.getByRole('img', { name: HINT })).toBeVisible();
      const toast = page.locator('.toast').filter({
        hasText: 'Memories already saved from this chat stay.',
      });
      await expect(toast).toBeVisible();
      // Its action stays on one line, its full name a hover away.
      const review = toast.getByRole('button', { name: 'Review in Knowledge' });
      await expect(review).toHaveText('Review');
      expect(
        await review.evaluate((button) => {
          // One line of text: the words' boxes all sit on one line.
          const range = document.createRange();
          range.selectNodeContents(button);
          const tops = new Set(
            [...range.getClientRects()].map((box) => Math.round(box.top)),
          );
          return tops.size;
        }),
      ).toBe(1);
      // Held open while the pointer is on it.
      await toast.hover();
    },
    true,
  );

  // Off, then on again here: the toast's link opens Knowledge to review what was saved before.
  await toggle(false);
  await expect(page.getByRole('img', { name: HINT })).toHaveCount(0);
  await toggle(false);
  await page
    .locator('.toast')
    .filter({ hasText: 'Memories already saved from this chat stay.' })
    .getByRole('button', { name: 'Review in Knowledge' })
    .click();
  await expect(page).toHaveURL(/\/app-v2\/settings\/knowledge/);
});

test('in an incognito chat Row-Bot asks before it saves to memory, with the exact text', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  await newIncognitoChat(page, 'Coffee order');
  await composer(page).fill('Remember that I take my coffee black, no sugar');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  const card = page.getByRole('complementary', { name: SAVE_CARD });
  await expect(card.getByRole('button', { name: /^Save/ })).toBeEnabled();
  await expect(card).toContainText('Coffee: I take it black, no sugar.');
  await expect(
    card.getByRole('button', { name: 'Use Auto in this chat' }),
  ).toHaveCount(0);

  await everywhere(page, info, 'save-card', async () => {
    await hideNavigation(page);
    await card.scrollIntoViewIfNeeded();
    await expect(
      card.getByRole('button', { name: 'Don’t save' }),
    ).toBeVisible();
    await placeholderOnOneLine(page);
  });
  // The narrowest phones too: the hint is cut short, never wrapped.
  await page.setViewportSize({ width: 360, height: 780 });
  await hideNavigation(page);
  await placeholderOnOneLine(page);
  await assertNoOverflow(page);
  await screenshot(page, info, 'narrow-save-card-360');
  await page.setViewportSize(DESKTOP);

  await card.getByRole('button', { name: /^Save/ }).click();
  await expect(
    page
      .getByRole('log', { name: 'Conversation' })
      .getByText('Saved: you take your coffee black, no sugar.', {
        exact: true,
      }),
  ).toBeVisible();
});
