import type { Locator, Page, TestInfo } from '@playwright/test';
import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';
import { clickNewChat } from './unified-helpers';

// Your models: the starred ones, which every picker lists first. The fixture
// puts twenty synthetic local models in the composer's picker and on the
// synthetic local runtime (Setup and Settings › Models); nothing reaches
// Ollama. Each test puts the profile back.

async function fixture(page: Page, path: string) {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated browser runner');
  const response = await page.request.post(path, {
    headers: { 'X-Fixture-Token': token, Origin: new URL(base).origin },
  });
  expect(response.ok(), await response.text()).toBe(true);
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    if (window !== window.top) return;
    localStorage.setItem(
      'row-bot.appearance.v1',
      JSON.stringify({
        version: 1,
        appearance: 'system',
        accent: 'blue',
        density: 'compact',
        reduce_transparency: false,
      }),
    );
  });
});

test.afterEach(async ({ page }) => {
  await fixture(page, '/__p10_fixture/first-run/restore');
  await fixture(page, '/__s8_fixture/models/restore');
});

const phone = (info: TestInfo) =>
  (info.project.use.viewport?.width ?? 1440) < 700;

/** Light and dark, then an accessibility scan of what shows. */
async function shoot(page: Page, info: TestInfo, name: string) {
  const suffix = phone(info) ? '-phone' : '';
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `${name}${suffix}-${appearance}`);
  }
  await accessibility(page, info, `${name}${suffix}`);
  await page.emulateMedia({ colorScheme: 'light' });
}

/** The composer's picker: the pill, or the + menu on a one-line composer. */
async function openPicker(page: Page): Promise<Locator> {
  const pill = page.getByRole('button', { name: 'Model', exact: true });
  const plus = page.getByRole('button', { name: 'Add files and more' });
  await expect(pill.or(plus).first()).toBeVisible();
  if (await pill.isVisible()) await pill.click();
  else {
    await plus.click();
    await page.getByRole('menuitem', { name: /^Model/ }).click();
  }
  const list = page.getByRole('listbox', { name: 'Models' });
  await expect(list).toBeVisible();
  return list;
}

const exactly = (name: string) =>
  new RegExp(`^${name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`);

/** The rows in order, by model name. */
function rows(list: Locator) {
  return list
    .getByRole('option')
    .evaluateAll((options) =>
      options.map(
        (option) =>
          option.querySelector('.model-picker-option-name')?.textContent ?? '',
      ),
    );
}

/** A row's star, by pointer: the button beside its option. */
async function rowStar(page: Page, list: Locator, name: string) {
  const option = list.getByRole('option', { name: exactly(name) }).first();
  await option.scrollIntoViewIfNeeded();
  await option.hover();
  await list
    .locator('.model-picker-row')
    .filter({ has: page.getByRole('option', { name: exactly(name) }) })
    .first()
    .getByRole('button', { name: /your models$/, includeHidden: true })
    .click();
}

test('stars from the picker make your models, in the order they were added', async ({
  page,
}, info) => {
  await fixture(page, '/__s8_fixture/models/seed');
  await page.goto('/app-v2/');
  await clickNewChat(page);
  let list = await openPicker(page);
  // Nothing starred yet: every ready local model is listed.
  await expect(list.getByRole('option')).toHaveCount(20);
  expect((await rows(list)).slice(0, 2)).toEqual([
    'deepseek-r1:14b',
    'gemma3:12b',
  ]);
  await shoot(page, info, 'models-picker-unstarred');

  // Two stars in a row, in the order they're added; the open list keeps its rows.
  const yours = list.getByRole('group', { name: 'Your models' });
  await rowStar(page, list, 'qwen3.8:27b');
  await expect(yours.getByRole('option')).toHaveCount(1);
  await rowStar(page, list, 'llama3.1:8b');
  await expect(yours.getByRole('option')).toHaveText([
    /^qwen3\.8:27b/,
    /^llama3\.1:8b/,
  ]);
  await expect(list.getByRole('option')).toHaveCount(20);
  await list.evaluate((element) => element.scrollTo(0, 0));
  await shoot(page, info, 'models-picker');

  // Saved: a reload lists your models, and Ollama's list is now just them
  // (with the model this chat uses, which is always in view).
  await page.reload();
  list = await openPicker(page);
  expect(await rows(list)).toEqual(['qwen3.8:27b', 'llama3.1:8b', 'qwen3:14b']);

  // The keyboard's way: Shift+F10 unstars the highlighted model.
  const search = page.getByRole('combobox', { name: 'Search models' });
  await search.fill('llama3.1');
  await search.press('Shift+F10');
  await expect(search).toBeFocused();
  await search.fill('');
  await expect(
    list.getByRole('group', { name: 'Your models' }).getByRole('option'),
  ).toHaveText([/^qwen3\.8:27b/]);
});

test('Settings › Models lists your starred models, and takes one out', async ({
  page,
}, info) => {
  await fixture(
    page,
    '/__s8_fixture/models/seed?favourites=qwen3.8:27b,llama3.1:8b',
  );
  await page.goto('/app-v2/settings/models');
  const yours = page.getByRole('list', { name: 'Your models' });
  await expect(yours.getByRole('listitem')).toHaveCount(2);
  await expect(yours.getByRole('listitem').first()).toContainText(
    'qwen3.8:27b',
  );
  await yours.evaluate((element) => element.scrollIntoView({ block: 'start' }));
  await shoot(page, info, 'models-settings');
  await yours
    .getByRole('button', { name: 'Remove llama3.1:8b from your models' })
    .click();
  await expect(yours.getByRole('listitem')).toHaveCount(1);
});

test('Setup lists your models, then Row-Bot’s picks, then every model', async ({
  page,
}, info) => {
  await fixture(
    page,
    '/__s8_fixture/models/seed?favourites=qwen3.8:27b,qwen3:14b',
  );
  await fixture(page, '/__p10_fixture/first-run/fresh');
  await page.goto('/app-v2/');
  await expect(page).toHaveURL(/\/app-v2\/setup$/);
  await page.getByRole('button', { name: /On this computer/ }).click();
  const starred = page.getByRole('list', { name: 'Your models' });
  await expect(starred.getByRole('button')).toHaveText([
    /^qwen3\.8:27b/,
    /^qwen3:14b/,
  ]);
  await expect(
    page.getByRole('list', { name: 'Recommended' }).getByRole('button'),
  ).toHaveCount(3);
  await expect(page.getByText('Show all models (16)')).toBeVisible();
  if (phone(info))
    await page
      .getByRole('heading', { name: 'Pick a model' })
      .evaluate((element) => element.scrollIntoView({ block: 'start' }));
  await shoot(page, info, 'models-first-run');
});
