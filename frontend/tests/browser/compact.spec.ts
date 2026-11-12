import { expect, test, writeEvidence } from './evidence';
import {
  assertCompactSurface,
  captureSurface,
  installVisualViewportFixture,
  openHome,
  openSeedConversation,
  setVisualViewportHeight,
} from './surface-helpers';

test.beforeEach(async ({ page }) => {
  await installVisualViewportFixture(page);
  await page.addInitScript(() => {
    localStorage.setItem(
      'row-bot.appearance.v1',
      JSON.stringify({
        version: 1,
        appearance: 'system',
        accent: 'blue',
        density: 'comfortable',
        reduce_transparency: false,
      }),
    );
  });
});

test('compact navigation drawer opens the selected conversation without rolling history back', async ({
  page,
}) => {
  await openHome(page);
  await page
    .getByRole('button', { name: 'Toggle navigation', exact: true })
    .click();
  await page
    .getByRole('navigation', {
      name: 'Workspace navigation',
      exact: true,
    })
    .getByRole('button', {
      name: 'Phase 1 conversation A',
      exact: true,
    })
    .click();
  await expect(page).toHaveURL(/\/app-v2\/conversations\/p1-browser-a$/);
  await expect(
    page.getByRole('heading', {
      name: 'Phase 1 conversation A',
      exact: true,
    }),
  ).toBeVisible();
});

test('compact setup, panel sheets, Back and virtual keyboard preserve the conversation draft', async ({
  page,
}, testInfo) => {
  await openSeedConversation(page);
  const composer = page.getByRole('textbox', {
    name: 'Message',
    exact: true,
  });
  const draft = `Retained ${testInfo.project.name} draft`;
  await composer.fill(draft);

  // Add resource lives in the composer's + menu; closing the setup returns
  // focus to +.
  const addResource = page.getByRole('button', {
    name: 'Add files and more',
    exact: true,
  });
  const openSetup = async () => {
    await addResource.click();
    await page
      .getByRole('menuitem', { name: 'Add resource…', exact: true })
      .click();
  };
  await openSetup();
  const setup = page.getByRole('dialog', {
    name: 'Add resource',
    exact: true,
  });
  await expect(setup).toBeVisible();
  await setup
    .getByRole('textbox', { name: 'Name (optional)', exact: true })
    .fill('Retained compact resource draft');
  await page.goBack();
  await expect(setup).toHaveCount(0);
  await expect(addResource).toBeFocused();
  await expect(composer).toHaveValue(draft);

  await openSetup();
  await expect(
    setup.getByRole('textbox', { name: 'Name (optional)', exact: true }),
  ).toHaveValue('Retained compact resource draft');
  await page.keyboard.press('Escape');
  await expect(addResource).toBeFocused();

  // Phones fold the panels into the header's ⋯; tablets keep Open panel.
  const phone = page.viewportSize()!.width < 768;
  const panelOpener = page.getByRole('button', {
    name: phone ? 'Conversation menu' : 'Open panel',
    exact: true,
  });
  await panelOpener.click();
  await page
    .getByRole('menuitem', {
      name: phone ? 'Open Workspace notes' : 'Workspace notes',
      exact: true,
    })
    .click();
  const sheet = page.getByRole('dialog', {
    name: 'Workspace notes',
    exact: true,
  });
  await expect(sheet).toHaveClass(/\bsheet\b/);
  await page.goBack();
  await expect(sheet).toHaveCount(0);
  await expect(panelOpener).toBeFocused();
  await expect(composer).toHaveValue(draft);

  await composer.focus();
  const visualHeight = Math.max(360, page.viewportSize()!.height - 320);
  await setVisualViewportHeight(page, visualHeight);
  await expect(page.locator('html')).toHaveAttribute('data-virtual-keyboard');
  const viewportState = await page.evaluate(() => ({
    viewportFit: document
      .querySelector('meta[name="viewport"]')
      ?.getAttribute('content'),
    visualHeight: getComputedStyle(document.documentElement).getPropertyValue(
      '--visual-viewport-height',
    ),
    keyboardInset: getComputedStyle(document.documentElement).getPropertyValue(
      '--virtual-keyboard-inset',
    ),
    safeAreaRules: [...document.styleSheets].some((sheet) => {
      try {
        return [...sheet.cssRules].some((rule) =>
          rule.cssText.includes('safe-area-inset-bottom'),
        );
      } catch {
        return false;
      }
    }),
  }));
  expect(viewportState.viewportFit).toContain('viewport-fit=cover');
  expect(viewportState.visualHeight).toBe(`${visualHeight}px`);
  expect(
    Number.parseInt(viewportState.keyboardInset, 10),
  ).toBeGreaterThanOrEqual(300);
  expect(viewportState.safeAreaRules).toBe(true);
  await expect(composer).toHaveValue(draft);
  await writeEvidence(testInfo, 'compact-viewport-and-draft', {
    viewportState,
    retainedDraft: true,
    setupBackRestoredFocus: true,
    sheetBackRestoredFocus: true,
    limitation:
      'VisualViewport occlusion is deterministic browser emulation; physical mobile browser chrome and on-screen keyboards remain device checks.',
  });
  await assertCompactSurface(page, testInfo, 'compact-conversation');
  await captureSurface(page, testInfo, 'compact-virtual-keyboard');
});

test('compact themes, accents and accessibility preferences retain one mounted workspace', async ({
  page,
}, testInfo) => {
  await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' });
  await openSeedConversation(page);
  const workspace = page.getByTestId('conversation-workspace');
  await workspace.evaluate((element) =>
    element.setAttribute('data-phase5-retained', 'true'),
  );
  // Settings is a navigation link since Phase 2; compact layouts keep it in
  // the navigation drawer.
  const settingsLink = page.getByRole('link', {
    name: 'Settings',
    exact: true,
  });
  if (await settingsLink.first().isVisible())
    await settingsLink.first().click();
  else {
    await page
      .getByRole('button', { name: 'Toggle navigation', exact: true })
      .click();
    await page
      .getByRole('dialog', { name: 'Conversations' })
      .getByRole('link', { name: 'Settings', exact: true })
      .click();
  }
  await expect(page).toHaveURL(/\/app-v2\/settings\/models$/);
  await page
    .getByRole('combobox', { name: 'Settings section', exact: true })
    .selectOption('appearance');
  await expect(page).toHaveURL(/\/app-v2\/settings\/appearance$/);
  await expect(
    page.getByRole('heading', { name: 'Appearance', exact: true }),
  ).toBeVisible();
  const accent = page.getByRole('combobox', {
    name: 'Colour theme',
    exact: true,
  });
  for (const [theme, colour] of [
    ['light', 'teal'],
    ['dark', 'violet'],
    ['system', 'amber'],
  ] as const) {
    await page
      .getByRole('button', {
        name: {
          light: 'Light',
          dark: 'Dark',
          system: /^System, follows your device/,
        }[theme],
        exact: theme !== 'system',
      })
      .click();
    await accent.selectOption(colour);
    await expect(page.locator('html')).toHaveAttribute(
      'data-theme',
      theme === 'system' ? 'dark' : theme,
    );
    await expect(page.locator('html')).toHaveAttribute('data-accent', colour);
    await expect(workspace).toHaveAttribute('data-phase5-retained', 'true');
    await expect(page.getByTestId('conversation-workspace')).toHaveCount(1);
  }
  await page
    .getByRole('switch', { name: 'Reduce transparency', exact: true })
    .check();
  await expect(page.locator('html')).toHaveAttribute('data-opaque', 'true');

  if (page.viewportSize()!.width === 390) {
    await page.emulateMedia({
      colorScheme: 'dark',
      reducedMotion: 'reduce',
      forcedColors: 'active',
    });
    await page.evaluate(() => {
      document.documentElement.style.zoom = '2';
    });
    const preferenceProof = await page.evaluate(() => ({
      forcedColors: matchMedia('(forced-colors: active)').matches,
      reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
      opaque: document.documentElement.dataset.opaque,
      zoom: getComputedStyle(document.documentElement).zoom,
    }));
    expect(preferenceProof).toEqual({
      forcedColors: true,
      reducedMotion: true,
      opaque: 'true',
      zoom: '2',
    });
    await writeEvidence(testInfo, 'compact-preference-emulation', {
      ...preferenceProof,
      limitation:
        'Chromium media emulation and CSS zoom are deterministic engine evidence, not operating-system high-contrast or browser-chrome zoom certification.',
    });
    await captureSurface(page, testInfo, 'compact-forced-colours-zoom', {
      axe: false,
    });
    await page.evaluate(() => {
      document.documentElement.style.zoom = '1';
    });
  }

  await assertCompactSurface(page, testInfo, 'compact-preferences');
  await captureSurface(page, testInfo, 'compact-theme-preferences');
  await page.goBack();
  await expect(page).toHaveURL(/\/app-v2\/settings\/models$/);
  await page.goBack();
  await expect(page).toHaveURL(/\/app-v2\/conversations\/p1-browser-a$/);
  await expect(workspace).toHaveAttribute('data-phase5-retained', 'true');
});

test('touch screens say what Home’s quick starts make and show no keyboard hints', async ({
  page,
}) => {
  await openHome(page);
  const quickStarts = page.getByRole('group', {
    name: 'Quick starts',
    exact: true,
  });
  const design = quickStarts.getByRole('button', {
    name: 'New design',
    exact: true,
  });
  await expect(design).toBeVisible();
  // No tooltip on touch, so the button carries its own word.
  expect(
    await design.evaluate((button) =>
      getComputedStyle(button, '::after').content.replaceAll('"', ''),
    ),
  ).toBe('Design');

  await page
    .getByRole('button', { name: 'Workspace commands', exact: true })
    .first()
    .click();
  const search = page.getByPlaceholder(
    'Search conversations, commands, settings…',
  );
  await expect(search).toBeVisible();
  const visibleKeycaps = await page
    .locator('.kbd, .command-palette-footer')
    .evaluateAll(
      (nodes) =>
        nodes.filter((node) => (node as HTMLElement).offsetParent !== null)
          .length,
    );
  expect(visibleKeycaps).toBe(0);
  const viewport = page.viewportSize();
  if (viewport && viewport.width < 768) {
    // A phone's search sheet has room for every result: none hide behind a
    // short scroll box.
    const clipped = await page
      .locator('.command-palette-results')
      .evaluate((list) => list.scrollHeight - list.clientHeight);
    expect(clipped).toBeLessThanOrEqual(1);
  }
  await page.keyboard.press('Escape');
});

test('a phone opens menus as bottom sheets that close with a swipe down', async ({
  page,
}) => {
  const viewport = page.viewportSize();
  test.skip(
    !viewport || viewport.width >= 768,
    'Menus stay popovers from tablet width up.',
  );
  await openSeedConversation(page);
  await page
    .getByRole('button', { name: 'Add files and more', exact: true })
    .first()
    .click();
  const menu = page.getByRole('menu');
  await expect(menu).toBeVisible();
  const box = await menu.boundingBox();
  expect(box).not.toBeNull();
  // Full width, resting on the bottom edge, over a scrim.
  expect(box!.x).toBeLessThanOrEqual(1);
  expect(box!.width).toBeGreaterThanOrEqual(viewport!.width - 2);
  expect(box!.y + box!.height).toBeGreaterThanOrEqual(viewport!.height - 2);
  expect(
    await menu.evaluate(
      (node) => getComputedStyle(node.parentElement!).backgroundColor,
    ),
  ).not.toBe('rgba(0, 0, 0, 0)');

  const touch = await page.context().newCDPSession(page);
  const x = box!.x + box!.width / 2;
  const y = box!.y + 12;
  await touch.send('Input.dispatchTouchEvent', {
    type: 'touchStart',
    touchPoints: [{ x, y }],
  });
  for (let step = 1; step <= 6; step += 1) {
    await touch.send('Input.dispatchTouchEvent', {
      type: 'touchMove',
      touchPoints: [{ x, y: y + step * 40 }],
    });
  }
  await touch.send('Input.dispatchTouchEvent', {
    type: 'touchEnd',
    touchPoints: [],
  });
  await expect(menu).toBeHidden();
});

test('a phone composer gives the text its own full-width row and swaps voice for Send as you type', async ({
  page,
}) => {
  const viewport = page.viewportSize();
  test.skip(
    !viewport || viewport.width >= 768,
    'Tablets keep the wide composer.',
  );
  await openSeedConversation(page);
  const composer = page.locator('.composer').first();
  await expect(composer).toHaveAttribute('data-single-line', 'true');
  const message = page.getByRole('textbox', { name: 'Message', exact: true });
  const send = page.getByRole('button', { name: 'Send', exact: true });
  // Voice, where the host offers it, shares Send's place: never both.
  const voice = composer.locator('.composer-voice');

  await message.fill('Check the venue booking before Friday');
  await expect(send).toBeVisible();
  await expect(voice).toBeHidden();
  const [text, field] = await Promise.all([
    message.boundingBox(),
    composer.locator('.composer-field').boundingBox(),
  ]);
  expect(text!.width / field!.width).toBeGreaterThan(0.85);

  // An empty draft has nothing to send: no Send until there is text.
  await message.fill('');
  await expect(send).toBeHidden();
});

test('a phone reaches Home, Chats, Workflows and Settings from a tab bar that a conversation hides', async ({
  page,
}) => {
  const viewport = page.viewportSize();
  test.skip(
    !viewport || viewport.width >= 768,
    'Tablets keep the navigation drawer.',
  );
  await openHome(page);
  const tabs = page.getByRole('navigation', { name: 'Main' });
  await expect(tabs.getByRole('link', { name: 'Home' })).toHaveAttribute(
    'aria-current',
    'page',
  );
  const box = await tabs.boundingBox();
  expect(box!.y + box!.height).toBeGreaterThanOrEqual(viewport!.height - 1);

  await tabs.getByRole('link', { name: 'Chats' }).click();
  await expect(page).toHaveURL(/\/app-v2\/library/);
  await expect(tabs.getByRole('link', { name: 'Chats' })).toHaveAttribute(
    'aria-current',
    'page',
  );
  await tabs.getByRole('link', { name: 'Workflows' }).click();
  await expect(page).toHaveURL(/\/app-v2\/?\?tab=workflows/);
  await tabs.getByRole('link', { name: 'Settings' }).click();
  await expect(page).toHaveURL(/\/app-v2\/settings/);

  await openSeedConversation(page);
  await expect(tabs).toHaveCount(0);
});

test('a phone opens the conversation list with a swipe in from the left edge', async ({
  page,
}) => {
  const viewport = page.viewportSize();
  test.skip(
    !viewport || viewport.width >= 768,
    'Tablets keep the button only.',
  );
  await openSeedConversation(page);
  const touch = await page.context().newCDPSession(page);
  const y = Math.round(viewport!.height / 2);
  await touch.send('Input.dispatchTouchEvent', {
    type: 'touchStart',
    touchPoints: [{ x: 4, y }],
  });
  for (let step = 1; step <= 5; step += 1) {
    await touch.send('Input.dispatchTouchEvent', {
      type: 'touchMove',
      touchPoints: [{ x: 4 + step * 30, y }],
    });
  }
  await touch.send('Input.dispatchTouchEvent', {
    type: 'touchEnd',
    touchPoints: [],
  });
  await expect(
    page.getByRole('dialog', { name: 'Conversations' }),
  ).toBeVisible();
});
