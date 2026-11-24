import type { Page, TestInfo } from '@playwright/test';
import { accessibility, assertNoOverflow, expect, test } from './evidence';
import { onPhone, PHONES } from './phone-helpers';

// U1 and U2 of the local-models uplift: each local model has one settings
// sheet. Row-Bot's own llama.cpp (Qwen3.5 9B on a synthetic 12 GB card) offers
// the windows that fit, with their memory; LM Studio's window is its own,
// shown with the way to load it again; the old "Advanced context" is now the
// Cloud reading limit. Under More, Sampling shows the model card's values and
// takes the person's own. The fixture runs nothing: a choice rewrites the
// endpoint, as it does while no server runs. Screens at desktop and phone
// width, light and dark, are saved for the UX review.

const BUILT_IN = 'model:custom_openai_llamacpp:qwen3.5-9b';
const STUDIO = 'model:custom_openai_lmstudio-1234:gemma-4';

async function fixture(page: Page, state: 'on' | 'off') {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated Phase 4 runner');
  const response = await page.request.post(
    `/__u1_fixture/local-model-sheet/${state}`,
    { headers: { 'X-Fixture-Token': token, Origin: new URL(base).origin } },
  );
  expect(response.ok(), await response.text()).toBe(true);
}

/**
 * The same screen in light and dark, as the window shows it (a sheet is fixed
 * to the window, which a full-page capture would stretch).
 */
async function bothSchemes(page: Page, testInfo: TestInfo, name: string) {
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await page.waitForTimeout(150);
    const file = testInfo.outputPath(`${name}-${scheme}.png`);
    await page.screenshot({ path: file, animations: 'disabled' });
    await testInfo.attach(`${name}-${scheme}`, {
      path: file,
      contentType: 'image/png',
    });
  }
  await page.emulateMedia({ colorScheme: null });
}

/** An open menu over the sheet, light only (the colours are the menu's own). */
async function screenshotMenu(page: Page, testInfo: TestInfo, name: string) {
  const file = testInfo.outputPath(`${name}.png`);
  await page.screenshot({ path: file, animations: 'disabled' });
  await testInfo.attach(name, { path: file, contentType: 'image/png' });
}

const sheet = (page: Page) => page.getByRole('dialog');

test.beforeEach(async ({ page }) => fixture(page, 'on'));
test.afterEach(async ({ page }) => fixture(page, 'off'));

test('a model Row-Bot runs offers the windows that fit, with their memory, and saves a choice', async ({
  page,
}, testInfo) => {
  await page.goto('/app-v2/settings/models');
  const local = page.locator('[data-setting-anchor="local-models"]');
  await local.getByRole('button', { name: 'Settings for Qwen3.5 9B' }).click();

  const dialog = sheet(page);
  await expect(
    dialog.getByRole('heading', { name: 'Qwen3.5 9B' }),
  ).toBeVisible();
  await expect(dialog).toContainText(
    'Runs in Row-Bot (llama.cpp) on this computer.',
  );
  const context = dialog.getByRole('button', { name: /^Context:/ });
  await expect(context).toHaveAccessibleName('Context: Automatic · 128K');
  // Opened with a click: focus is on the sheet, so no ring on its controls.
  await expect(
    dialog.getByRole('button', { name: 'Close dialog' }),
  ).not.toBeFocused();
  await expect(dialog).toContainText(
    'Reads 128K · model and context about 8.4 GB of your card’s 12 GB',
  );
  await expect(
    dialog.getByRole('radiogroup', { name: 'Thinking' }),
  ).toBeVisible();
  // The 9B answers without thinking unless a chat turns it on (D3).
  await expect(dialog).toContainText(
    'Automatic: off for this model. A chat’s own choice still wins.',
  );
  await accessibility(page, testInfo, 'built-in-sheet-axe');
  await bothSchemes(page, testInfo, 'desktop-built-in-sheet');

  await context.click();
  const menu = page.getByRole('menu');
  await expect(menu.getByRole('menuitem')).toHaveText([
    /^Automatic · 128K/,
    /^32K/,
    /^64K/,
    /^128K/,
  ]);
  await screenshotMenu(page, testInfo, 'desktop-built-in-context-menu');
  await menu.getByRole('menuitem', { name: /^64K/ }).click();
  await expect(dialog.getByRole('status')).toHaveText(
    'Saved. It applies from the next message.',
  );
  await expect(dialog).toContainText(
    'Reads 64K · model and context about 7 GB of your card’s 12 GB',
  );
  await dialog.locator('summary', { hasText: 'More' }).click();
  await expect(
    dialog.getByRole('button', { name: /^Reply length:/ }),
  ).toBeVisible();
  // The 9B answers without thinking unless a chat turns it on (D3); the card
  // has values for both.
  await expect(dialog).toContainText(
    'Not thinking: temperature 0.7 · top-p 0.8 · top-k 20 · min-p 0 · presence penalty 1.5',
  );
  await bothSchemes(page, testInfo, 'desktop-built-in-sheet-saved');

  // Custom starts from the card's values for how it answers now.
  await dialog.getByRole('button', { name: 'Sampling: Model card' }).click();
  await page.getByRole('menuitem', { name: 'Custom' }).click();
  const temperature = dialog.getByLabel('Temperature');
  await expect(temperature).toHaveValue('0.7');
  await temperature.fill('0.5');
  await temperature.press('Enter');
  await expect(dialog.getByRole('status')).toHaveText(
    'Saved. It applies from the next message.',
  );
  await expect(dialog).toContainText('Your values, whether it thinks or not.');
  await accessibility(page, testInfo, 'built-in-sheet-sampling-axe');
  await bothSchemes(page, testInfo, 'desktop-built-in-sheet-sampling');
  await dialog.getByRole('button', { name: 'Sampling: Custom' }).click();
  await screenshotMenu(page, testInfo, 'desktop-built-in-sampling-menu');
  await page.getByRole('menuitem', { name: 'Model card' }).click();
  await expect(dialog.getByLabel('Temperature')).toHaveCount(0);

  // The choice is what the model reads: the Local row says so after closing.
  await dialog.getByRole('button', { name: 'Close dialog' }).click();
  await expect(local).toContainText('reads 64K');
});

test('a server the person runs shows what it loaded and how to change it', async ({
  page,
}, testInfo) => {
  await page.goto(
    `/app-v2/settings/models?model-settings=${encodeURIComponent(STUDIO)}`,
  );
  const dialog = sheet(page);
  await expect(dialog.getByRole('heading', { name: 'gemma-4' })).toBeVisible();
  await expect(dialog).toContainText('LM Studio loaded it at 32K.');
  await expect(dialog).toContainText(
    'Load it at another size here, or change Context Length in LM Studio.',
  );
  await expect(dialog.getByRole('button', { name: /^Context:/ })).toHaveCount(
    0,
  );
  await expect(
    dialog.getByRole('button', { name: 'Reload in LM Studio at 64K' }),
  ).toBeVisible();
  await bothSchemes(page, testInfo, 'desktop-server-sheet');

  // Closing leaves Settings › Models as it was.
  await page.keyboard.press('Escape');
  await expect(page).toHaveURL(/\/app-v2\/settings\/models$/);
});

test('the Cloud reading limit is for cloud models only', async ({
  page,
}, testInfo) => {
  await page.goto('/app-v2/settings/models');
  await page.locator('summary', { hasText: 'Cloud models' }).click();
  const row = page.locator('[data-setting-anchor="reading-limit"]');
  await expect(row).toContainText('Cloud reading limit');
  await expect(row).not.toContainText('what your computer can hold');
  await row.scrollIntoViewIfNeeded();
  await bothSchemes(page, testInfo, 'desktop-cloud-reading-limit');
});

for (const phone of PHONES.slice(0, 1)) {
  test.describe(phone.name, () => {
    onPhone(phone);

    test('the sheet fits a phone, light and dark', async ({
      page,
    }, testInfo) => {
      await page.goto(
        `/app-v2/settings/models?model-settings=${encodeURIComponent(BUILT_IN)}`,
      );
      const dialog = sheet(page);
      await expect(
        dialog.getByRole('button', { name: /^Context:/ }),
      ).toBeVisible();
      await dialog.locator('summary', { hasText: 'More' }).click();
      await assertNoOverflow(page);
      await bothSchemes(page, testInfo, 'phone-built-in-sheet');
      await dialog
        .getByRole('button', { name: 'Sampling: Model card' })
        .click();
      await page.getByRole('menuitem', { name: 'Custom' }).click();
      await expect(dialog.getByLabel('Temperature')).toHaveValue('0.7');
      await dialog.getByLabel('Presence penalty').scrollIntoViewIfNeeded();
      await assertNoOverflow(page);
      await bothSchemes(page, testInfo, 'phone-built-in-sheet-sampling');
      // On a phone the size menu rises as a sheet of its own.
      await dialog.getByRole('button', { name: /^Context:/ }).click();
      await expect(page.getByRole('menuitem', { name: /^64K/ })).toBeVisible();
      await screenshotMenu(page, testInfo, 'phone-built-in-context-menu');
      await page.keyboard.press('Escape');

      await page.goto(
        `/app-v2/settings/models?model-settings=${encodeURIComponent(STUDIO)}`,
      );
      await expect(sheet(page)).toContainText('LM Studio loaded it at 32K.');
      await assertNoOverflow(page);
      await bothSchemes(page, testInfo, 'phone-server-sheet');
    });
  });
}
