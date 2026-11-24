import type { Page } from '@playwright/test';
import { expect, screenshot, test } from './evidence';
import { asPairedPhone, onPhone, PHONES, tapPath } from './phone-helpers';
import {
  fixtureHeaders,
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
    page.getByRole('menuitem', { name: 'Files', exact: true }),
  ).toBeVisible();
  await expect(composer(page)).toHaveValue('A draft to attach a file to');
});

// ------------------------------------------- the + sheet and Chat settings (B2)

// A 2×2 PNG, the photo the tiles attach.
const PHOTO = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFklEQVR4nGP8z8Dwn4GBgYGJAQoAAAD//wMAEv8B/1mWv8UAAAAASUVORK5CYII=',
  'base64',
);
const plusButton = (page: Page) =>
  page.getByRole('button', { name: 'Add files and more', exact: true });
const tile = (page: Page, name: string) =>
  page
    .getByRole('group', { name: 'Attach', exact: true })
    .getByRole('menuitem', { name, exact: true });

async function seedA2(page: Page): Promise<void> {
  const seeded = await page.request.post('/__a2_fixture/seed', {
    headers: fixtureHeaders(),
  });
  expect(seeded.ok()).toBe(true);
}

for (const phone of PHONES)
  test.describe(`${phone.name}: the + sheet`, () => {
    onPhone(phone);

    test('attaches a photo, a camera shot and a file from the tiles, each picker asking for its own', async ({
      page,
    }, info) => {
      await asPairedPhone(page);
      await newConversation(page);
      const tray = page.getByRole('list', { name: 'Attachments' });
      for (const [name, accept, capture, file] of [
        ['Photos', 'image/*', null, 'photo.png'],
        ['Camera', 'image/*', 'environment', 'camera.png'],
        ['Files', null, null, 'notes.txt'],
      ] as const) {
        const chooser = page.waitForEvent('filechooser');
        // + → tile, then the pick in the phone's own picker.
        expect(
          await tapPath(page, ['Add files and more', tile(page, name)]),
        ).toBe(2);
        const picker = await chooser;
        expect(picker.isMultiple()).toBe(true);
        expect(await picker.element().getAttribute('accept')).toBe(accept);
        expect(await picker.element().getAttribute('capture')).toBe(capture);
        await picker.setFiles(
          file.endsWith('.png')
            ? { name: file, mimeType: 'image/png', buffer: PHOTO }
            : {
                name: file,
                mimeType: 'text/plain',
                buffer: Buffer.from('Agenda'),
              },
        );
        await expect(
          tray.getByRole('button', { name: `Preview ${file}` }),
        ).toBeVisible();
      }
      await expect(tray.locator('[data-kind="picture"]')).toHaveCount(2);
      await screenshot(
        page,
        info,
        `phone-plus-tray-${phone.use.viewport.width}`,
      );
    });

    test('Chat settings: one level down, Incognito on and off, Approvals changed, and back to +', async ({
      page,
    }, info) => {
      // Hands-off asks once per install: forget an earlier spec's yes.
      const reset = await page.request.post(
        '/__a0_fixture/full-autonomy/reset',
        { headers: fixtureHeaders() },
      );
      expect(reset.ok()).toBe(true);
      await asPairedPhone(page);
      await newConversation(page);
      const sheet = page.getByRole('menu');
      // The model is in the header, the context line in the details.
      await plusButton(page).tap();
      await expect(tile(page, 'Camera')).toBeInViewport();
      await expect(sheet.getByRole('menuitem', { name: /^Model/ })).toHaveCount(
        0,
      );
      await expect(
        sheet.getByText(/of context|Context will appear/),
      ).toHaveCount(0);
      await page.keyboard.press('Escape');

      // Incognito: + → Chat settings → the switch.
      const incognito = page.getByRole('menuitemcheckbox', {
        name: /^Incognito/,
      });
      expect(
        await tapPath(page, [
          'Add files and more',
          page.getByRole('menuitem', { name: /^Chat settings/ }),
          incognito,
        ]),
      ).toBe(3);
      await expect(incognito).toHaveAttribute('aria-checked', 'true');
      // The header's mark shows at once (the open sheet hides the page from
      // the accessibility tree, so it is found by its look).
      await expect(page.locator('.conversation-incognito')).toBeVisible();
      await expect(composer(page)).toHaveAttribute('placeholder', /incognito/i);
      await screenshot(
        page,
        info,
        `phone-chat-settings-incognito-${phone.use.viewport.width}`,
      );
      // ‹ goes back to +, in the same sheet.
      await page.getByRole('menuitem', { name: 'Back', exact: true }).tap();
      await expect(tile(page, 'Photos')).toBeInViewport();
      // Escape in Chat settings goes back to + too.
      await page.getByRole('menuitem', { name: /^Chat settings/ }).tap();
      await expect(incognito).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(tile(page, 'Files')).toBeInViewport();
      await page.getByRole('menuitem', { name: /^Chat settings/ }).tap();
      await incognito.tap();
      await expect(incognito).toHaveAttribute('aria-checked', 'false');
      await page.keyboard.press('Escape');
      await page.keyboard.press('Escape');
      await expect(page.getByRole('menu')).toHaveCount(0);

      // Approvals: + → Chat settings → Approvals → Auto.
      expect(
        await tapPath(page, [
          'Add files and more',
          page.getByRole('menuitem', { name: /^Chat settings/ }),
          page.getByRole('menuitem', { name: /^Approvals/ }),
          page.getByRole('menuitemradio', { name: /^Auto/ }),
        ]),
      ).toBe(4);
      await expect(page.getByRole('menu')).toHaveCount(0);
      await plusButton(page).tap();
      await expect(
        page.getByRole('menuitem', { name: /^Chat settings/ }),
      ).toContainText('Approvals: Auto');
      await page.getByRole('menuitem', { name: /^Chat settings/ }).tap();
      await expect(
        page.getByRole('menuitem', { name: /^Approvals/ }),
      ).toContainText('Auto');
      // Hands-off still asks once before it turns on.
      await page.getByRole('menuitem', { name: /^Approvals/ }).tap();
      await page.getByRole('menuitemradio', { name: /^Hands-off/ }).tap();
      const dialog = page.getByRole('alertdialog', {
        name: 'Turn on Hands-off?',
      });
      await expect(dialog).toBeVisible();
      await dialog.getByRole('button', { name: 'Cancel' }).tap();
      await expect(dialog).toHaveCount(0);
      await plusButton(page).tap();
      await expect(
        page.getByRole('menuitem', { name: /^Chat settings/ }),
      ).toContainText('Approvals: Auto');
    });

    test('the chat ⋯ leaves Incognito to Chat settings, and the details show the context line', async ({
      page,
    }) => {
      await asPairedPhone(page);
      await newConversation(page);
      await page
        .getByRole('button', { name: 'Conversation menu', exact: true })
        .tap();
      const menu = page.getByRole('menu');
      await expect(
        menu.getByRole('menuitem', { name: 'Conversation details' }),
      ).toBeVisible();
      await expect(
        menu.getByRole('menuitem', { name: /Incognito/ }),
      ).toHaveCount(0);
      await expect(
        menu.getByRole('menuitemcheckbox', { name: /Incognito/ }),
      ).toHaveCount(0);
      await menu.getByRole('menuitem', { name: 'Conversation details' }).tap();
      const details = page.getByRole('dialog', {
        name: 'Conversation details',
      });
      await expect(
        details.getByRole('region', { name: 'Context' }),
      ).toContainText('Context will appear after the next response');
    });

    test('a design chat names its work under the header, not in a cut-off chip by the composer', async ({
      page,
    }) => {
      await seedA2(page);
      await asPairedPhone(page);
      await openConversation(page, 'a2-design-chat');
      await expect(
        page.getByRole('button', { name: /^Working on / }),
      ).toBeVisible();
      await expect(
        page.getByRole('group', { name: 'Resource write targets' }),
      ).toHaveCount(0);
      // The target is chosen in Chat settings instead.
      await plusButton(page).tap();
      await page.getByRole('menuitem', { name: /^Chat settings/ }).tap();
      await page
        .getByRole('menuitem', { name: /^Work in a folder or design/ })
        .tap();
      await expect(
        page.getByRole('menuitemradio', { name: /Community workshop/i }),
      ).toHaveAttribute('aria-checked', 'true');
    });
  });

test.describe('desktop: the + menu is unchanged', () => {
  test.use({
    viewport: { width: 1440, height: 900 },
    isMobile: false,
    hasTouch: false,
  });

  test('keeps Attach file, the approvals shield, Agent and Mode, and the design chip', async ({
    page,
  }, info) => {
    await seedA2(page);
    await openConversation(page, 'a2-design-chat');
    await expect(
      page.getByRole('button', { name: /^Approvals: / }),
    ).toBeVisible();
    await expect(
      page.getByRole('group', { name: 'Resource write targets' }),
    ).toBeVisible();
    await plusButton(page).click();
    const menu = page.getByRole('menu');
    for (const name of [
      'Attach file',
      'Add resource…',
      /^Skills…/,
      /^Apps/,
      /^Agent/,
      /^Mode/,
    ])
      await expect(menu.getByRole('menuitem', { name })).toBeVisible();
    await expect(page.getByRole('group', { name: 'Attach' })).toHaveCount(0);
    await expect(
      menu.getByRole('menuitem', { name: /^Chat settings/ }),
    ).toHaveCount(0);
    await screenshot(page, info, 'desktop-plus-menu');
  });
});
