import { expect, test, writeEvidence } from './evidence';
import {
  assertCompactSurface,
  captureSurface,
  installVisualViewportFixture,
  openHome,
  openSeedConversation,
  setVisualViewportHeight,
} from './phase5-helpers';

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

  const addResource = page.getByRole('button', {
    name: 'Add resource',
    exact: true,
  });
  await addResource.click();
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

  await addResource.click();
  await expect(
    setup.getByRole('textbox', { name: 'Name (optional)', exact: true }),
  ).toHaveValue('Retained compact resource draft');
  await page.keyboard.press('Escape');
  await expect(addResource).toBeFocused();

  const panelOpener = page.getByRole('button', {
    name: 'Open panel',
    exact: true,
  });
  await panelOpener.click();
  await page
    .getByRole('menuitem', { name: 'Workspace notes', exact: true })
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
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await expect(page).toHaveURL(/\/app-v2\/settings\/providers$/);
  await page
    .getByRole('combobox', { name: 'Settings section', exact: true })
    .selectOption('preferences');
  await expect(page).toHaveURL(/\/app-v2\/settings\/preferences$/);
  await expect(
    page.getByRole('heading', { name: 'Preferences', exact: true }),
  ).toBeVisible();
  await page
    .locator('summary')
    .filter({ hasText: 'Local client controls' })
    .click();
  const appearance = page.getByRole('combobox', {
    name: 'Appearance',
    exact: true,
  });
  const accent = page.getByRole('combobox', {
    name: 'Colour theme',
    exact: true,
  });
  for (const [theme, colour] of [
    ['light', 'teal'],
    ['dark', 'violet'],
    ['system', 'amber'],
  ] as const) {
    await appearance.selectOption(theme);
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
    .getByRole('checkbox', { name: 'Reduce transparency', exact: true })
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
  await expect(page).toHaveURL(/\/app-v2\/settings\/providers$/);
  await page.goBack();
  await expect(page).toHaveURL(/\/app-v2\/conversations\/p1-browser-a$/);
  await expect(workspace).toHaveAttribute('data-phase5-retained', 'true');
});
