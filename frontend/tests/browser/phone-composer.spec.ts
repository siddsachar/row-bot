import type { Page } from '@playwright/test';
import { expect, screenshot, test } from './evidence';
import {
  installVisualViewportFixture,
  setVisualViewportHeight,
} from './surface-helpers';
import {
  blockFixtureServiceWorkers,
  composer,
  newConversation,
  openConversation,
} from './unified-helpers';

// A phone with its on-screen keyboard open: the app follows the visual
// viewport, so the composer sits just above the keyboard.
const PHONE = { width: 375, height: 812 };
const KEYBOARD = 336;

test.use({ viewport: PHONE, hasTouch: true, isMobile: true });
test.skip(
  ({ browserName }) => browserName === 'firefox',
  'Firefox has no mobile emulation',
);
test.use({ serviceWorkers: 'allow' });
test.beforeEach(async ({ context, page }) => {
  await blockFixtureServiceWorkers(context);
  await installVisualViewportFixture(page);
});

/**
 * Types into the composer with the keyboard open, as on a phone. The
 * keyboard closes the moment the field loses focus, before a tap's click
 * lands, and the composer drops to the bottom of the screen with it.
 */
async function typeWithKeyboard(page: Page, text: string): Promise<void> {
  await composer(page).focus();
  await composer(page).fill(text);
  await setVisualViewportHeight(page, PHONE.height - KEYBOARD);
  await expect(page.locator(':root')).toHaveAttribute('data-virtual-keyboard');
  await page.evaluate(() => {
    document.getElementById('message-composer')!.addEventListener(
      'blur',
      () =>
        (
          window as unknown as {
            __P5_VISUAL_VIEWPORT__: { setHeight(value: number): void };
          }
        ).__P5_VISUAL_VIEWPORT__.setHeight(window.innerHeight),
      { once: true },
    );
  });
}

for (const theme of ['light', 'dark'] as const) {
  test(`one tap on Send in a new chat sends the message, never a quick-start prompt (${theme})`, async ({
    page,
  }, info) => {
    await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' });
    await newConversation(page);
    const prompts = page
      .getByRole('group', { name: 'Example prompts' })
      .getByRole('button');
    await expect(prompts).toHaveCount(4);
    const promptTexts = await prompts.evaluateAll((buttons) =>
      buttons.map(
        (button) =>
          button.querySelector('.chat-empty-prompt-text')!.textContent!,
      ),
    );
    await typeWithKeyboard(page, 'Answer this instant reply fixture');
    await screenshot(page, info, `phone-new-chat-typed-${theme}`);

    await page.getByRole('button', { name: 'Send', exact: true }).tap();

    const sent = page.getByRole('article', { name: 'You message' });
    await expect(sent).toHaveCount(1);
    await expect(sent).toContainText('Answer this instant reply fixture');
    for (const prompt of promptTexts)
      await expect(sent).not.toContainText(prompt);
    await expect(composer(page)).toHaveValue('');
    // The keyboard stays up for the next message, as after Enter.
    await expect(composer(page)).toBeFocused();
    await expect(page.locator(':root')).toHaveAttribute(
      'data-virtual-keyboard',
    );
    await screenshot(page, info, `phone-new-chat-sent-${theme}`);
  });
}

test('one tap on Send sends in a chat with history while the keyboard is open', async ({
  page,
}) => {
  await openConversation(page);
  const sent = page
    .getByRole('article', { name: 'You message' })
    .filter({ hasText: 'One tap with the keyboard open' });
  await typeWithKeyboard(page, 'One tap with the keyboard open');

  await page.getByRole('button', { name: 'Send', exact: true }).tap();

  await expect(sent).toHaveCount(1);
  await expect(composer(page)).toHaveValue('');
});

test('one tap on + opens its menu while the keyboard is open', async ({
  page,
}) => {
  await openConversation(page);
  await typeWithKeyboard(page, 'A draft to attach a file to');

  await page
    .getByRole('button', { name: 'Add files and more', exact: true })
    .tap();

  await expect(
    page.getByRole('menuitem', { name: 'Attach file', exact: true }),
  ).toBeVisible();
  await expect(composer(page)).toHaveValue('A draft to attach a file to');
});
