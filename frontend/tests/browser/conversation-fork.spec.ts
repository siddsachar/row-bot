import type { Locator, Page, TestInfo } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';
import { composer, dismissNotices, newConversation } from './unified-helpers';

/*
 * B5 · Edit and resend, Fork from here: both continue in a new conversation
 * that holds the history before the edited message (or up to the end of the
 * forked reply's turn) and says where it came from; the original is
 * unchanged. The chats, the fork and the sends run through the real server;
 * the model's replies are scripted ("Noted: …").
 */

const PHONE = { width: 375, height: 812 };
const DESKTOP = { width: 1280, height: 900 };
const FORKED_NOTE = /^Forked from “.+” · Open original$/;

async function views(page: Page, info: TestInfo, name: string) {
  await dismissNotices(page);
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
) {
  for (const phone of [false, true]) {
    await page.setViewportSize(phone ? PHONE : DESKTOP);
    await reach(phone);
    await views(page, info, phone ? `${name}-phone` : `${name}-desktop`);
    // A phone's actions sheet closes before the next size.
    if (await page.getByRole('menu').isVisible())
      await page.keyboard.press('Escape');
  }
  await page.setViewportSize(DESKTOP);
}

/**
 * An older message's action: its icon on a desktop; on a phone, where only
 * the newest messages keep icons (D14), the same item in the sheet a
 * right-click (or a long-press) opens.
 */
async function action(
  page: Page,
  message: Locator,
  name: string,
  phone: boolean,
): Promise<void> {
  if (phone) {
    await message.locator('.message-text').click({ button: 'right' });
    await expect(
      page.getByRole('menu').getByRole('menuitem', { name }),
    ).toBeVisible();
    return;
  }
  await message.hover();
  await message.getByRole('button', { name }).hover();
  await expect(page.getByRole('tooltip')).toContainText(name);
}

function log(page: Page): Locator {
  return page.getByRole('log', { name: 'Conversation' });
}

function mine(page: Page, text: string): Locator {
  return log(page)
    .getByRole('article', { name: 'You message' })
    .filter({ hasText: text });
}

function reply(page: Page, text: string): Locator {
  return log(page)
    .getByRole('article', { name: 'Row-Bot message' })
    .filter({ hasText: text });
}

async function send(page: Page, text: string, answer: string) {
  await composer(page).fill(text);
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(log(page).getByText(answer, { exact: true })).toBeVisible();
}

/** A three-turn chat: First, Second and Third, each answered. */
async function threeTurns(page: Page): Promise<string> {
  const original = await newConversation(page);
  await send(page, 'First fork fixture', 'Noted: First.');
  await send(page, 'Second fork fixture', 'Noted: Second.');
  await send(page, 'Third fork fixture', 'Noted: Third.');
  return original;
}

async function userTexts(page: Page): Promise<string[]> {
  return (
    await log(page)
      .getByRole('article', { name: 'You message' })
      .locator('.message-text')
      .allInnerTexts()
  ).map((text) => text.trim());
}

test('Edit and resend continues in a new conversation from before the message; the original is unchanged', async ({
  page,
}, info) => {
  // Three turns and twenty evidence screenshots.
  test.setTimeout(180_000);
  await page.setViewportSize(DESKTOP);
  const original = await threeTurns(page);
  const second = mine(page, 'Second fork fixture');

  await everywhere(page, info, 'message-actions', (phone) =>
    action(page, second, 'Edit and resend', phone),
  );
  await everywhere(page, info, 'reply-actions', (phone) =>
    action(page, reply(page, 'Noted: First.'), 'Fork from here', phone),
  );

  // The field, wherever the edited text has taken it (the bubble's own text is hidden meanwhile).
  const field = log(page).getByRole('textbox', { name: 'Edit your message' });
  await everywhere(page, info, 'inline-editor', async () => {
    if (!(await field.isVisible())) {
      await second.hover();
      await second.getByRole('button', { name: 'Edit and resend' }).click();
    }
    await expect(field).toBeFocused();
    await expect(field).toHaveValue('Second fork fixture');
  });

  // Escape leaves the message as it was; the edit opens again.
  await field.press('Escape');
  await expect(field).toHaveCount(0);
  await second.hover();
  await second.getByRole('button', { name: 'Edit and resend' }).click();
  await field.fill('Second edited fork fixture');
  await field.press('Enter');

  await expect(page).not.toHaveURL(new RegExp(`/conversations/${original}$`));
  await expect(log(page).getByText('Noted: Second edited.')).toBeVisible();
  const note = page.getByRole('note').filter({ hasText: 'Forked from' });
  await expect(note).toHaveText(FORKED_NOTE);
  expect(await userTexts(page)).toEqual([
    'First fork fixture',
    'Second edited fork fixture',
  ]);
  await expect(log(page).getByText('Noted: First.')).toBeVisible();
  await expect(log(page).getByText('Noted: Third.')).toHaveCount(0);

  await everywhere(page, info, 'forked-chat', async () => {
    await expect(note).toBeVisible();
  });

  await note.getByRole('link', { name: 'Open original' }).click();
  await expect(page).toHaveURL(new RegExp(`/conversations/${original}$`));
  await expect(log(page).getByText('Noted: Third.')).toBeVisible();
  expect(await userTexts(page)).toEqual([
    'First fork fixture',
    'Second fork fixture',
    'Third fork fixture',
  ]);
  await expect(
    page.getByRole('note').filter({ hasText: 'Forked from' }),
  ).toHaveCount(0);
});

test('Fork from here continues in a new conversation from the end of that reply', async ({
  page,
}) => {
  await page.setViewportSize(DESKTOP);
  const original = await newConversation(page);
  await send(page, 'First fork fixture', 'Noted: First.');
  await send(page, 'Second fork fixture', 'Noted: Second.');

  const first = reply(page, 'Noted: First.');
  await first.hover();
  await first.getByRole('button', { name: 'Fork from here' }).click();

  await expect(page).not.toHaveURL(new RegExp(`/conversations/${original}$`));
  await expect(
    page.getByRole('note').filter({ hasText: 'Forked from' }),
  ).toHaveText(FORKED_NOTE);
  await expect(log(page).getByText('Noted: First.')).toBeVisible();
  expect(await userTexts(page)).toEqual(['First fork fixture']);

  // The fork goes on by itself.
  await send(page, 'Another fork fixture', 'Noted: Another.');
  expect(await userTexts(page)).toEqual([
    'First fork fixture',
    'Another fork fixture',
  ]);
});
