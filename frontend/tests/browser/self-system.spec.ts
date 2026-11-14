import type { Page, TestInfo } from '@playwright/test';
import { expect, screenshot, test } from './evidence';
import { composer, newConversation } from './unified-helpers';

/*
 * Row-Bot Self: a how-to answered from Row-Bot's own guides, each setting a
 * chip that opens its row, the cited guide in its sheet, a setting change
 * asked in words, and Undo on a change Row-Bot made. The fixture scripts the
 * model; the settings, guides and Undo owners are real.
 */

async function send(page: Page, text: string) {
  await composer(page).fill(text);
  await page.getByRole('button', { name: 'Send', exact: true }).click();
}

/** The same screen in light and dark, for the SOTA UX review. */
async function bothThemes(page: Page, testInfo: TestInfo, name: string) {
  await screenshot(page, testInfo, `${name}-light`);
  await page.emulateMedia({ colorScheme: 'dark' });
  await screenshot(page, testInfo, `${name}-dark`);
  await page.emulateMedia({ colorScheme: 'light' });
}

test('a how-to links each setting to its row, cites its guide, and Row-Bot’s change can be undone', async ({
  page,
}, testInfo) => {
  await newConversation(page);
  await send(
    page,
    'How do I change when the dream cycle runs? self guide fixture',
  );
  const chip = page.getByRole('link', {
    name: 'Preferences › Dream Cycle',
    exact: true,
  });
  await expect(chip).toBeVisible();
  const card = page.getByRole('group', { name: 'Changed Dream Cycle' });
  await expect(card).toContainText('Changed Dream Cycle to 2:00–6:00');
  await bothThemes(page, testInfo, 'self-guide-answer');

  // The guide opens offline in its sheet, at the cited section.
  await page
    .getByRole('button', {
      name: 'Settings: Preferences › Overnight Tidy-Up',
      exact: true,
    })
    .click();
  const sheet = page.getByRole('dialog', {
    name: 'Settings: Preferences › Overnight Tidy-Up',
  });
  await expect(
    sheet.locator('[data-guide-section="overnight-tidy-up"]'),
  ).toBeVisible();
  await expect(
    sheet.getByRole('link', { name: 'Open on row-bot.ai' }),
  ).toHaveAttribute('href', 'https://row-bot.ai/docs/settings/preferences');
  await bothThemes(page, testInfo, 'self-guide-sheet');
  await page.keyboard.press('Escape');
  await expect(sheet).toHaveCount(0);

  // Undo puts the old hours back: the person's own action, nothing to approve.
  await card
    .getByRole('button', { name: 'Undo: back to 1:00–5:00', exact: true })
    .click();
  await expect(
    page.getByRole('group', { name: 'Undone: Dream Cycle' }),
  ).toContainText('Undone: Dream Cycle is back to 1:00–5:00');
  await bothThemes(page, testInfo, 'self-undone');

  // The chip opens the row in place, as Settings' own search does.
  await chip.click();
  await expect(page).toHaveURL(/\/app-v2\/settings\/preferences#dream-cycle$/);
  await expect(
    page.locator('[data-setting-anchor="dream-cycle"]').first(),
  ).toBeVisible();
  await bothThemes(page, testInfo, 'self-chip-opened');
});

test('a setting change is asked in words, with its row and Not now', async ({
  page,
}, testInfo) => {
  await newConversation(page);
  await send(page, 'Move the dream hours to 2-6 self change fixture');
  const card = page.getByRole('complementary', {
    name: 'Change Dream Cycle',
    exact: true,
  });
  await expect(card).toContainText('Change Dream Cycle?');
  await expect(card).toContainText('1:00–5:00 → 2:00–6:00');
  await expect(
    card.getByRole('link', { name: 'Show in Settings' }),
  ).toBeVisible();
  await bothThemes(page, testInfo, 'self-change-card');
  // Not now answers the card: it leaves the chat and the setting stays as it was.
  await card.getByRole('button', { name: 'Not now', exact: true }).click();
  await expect(card).toHaveCount(0);
});
