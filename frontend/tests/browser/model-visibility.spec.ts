import type { Locator, Page, TestInfo } from '@playwright/test';
import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';
import { clickNewChat } from './unified-helpers';

// F10: favourite models first, hidden ones under "More models". The fixture
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

/** The rows in order, by model name ("More models (3)" for the closed row). */
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

/** A row's menu, by pointer: the ⋯ button beside its option. */
async function rowMenu(page: Page, list: Locator, name: string) {
  const option = list.getByRole('option', { name: exactly(name) }).first();
  await option.scrollIntoViewIfNeeded();
  await option.hover();
  await list
    .locator('.model-picker-row')
    .filter({ has: page.getByRole('option', { name: exactly(name) }) })
    .first()
    .getByRole('button', { name: `More for ${name}`, includeHidden: true })
    .click();
  const menu = page.getByRole('menu', { name: `More for ${name}` });
  await expect(menu).toBeVisible();
  return menu;
}

test('favourites and hidden models from the picker keep their order', async ({
  page,
}, info) => {
  await fixture(page, '/__s8_fixture/models/seed');
  await page.goto('/app-v2/');
  await clickNewChat(page);
  let list = await openPicker(page);
  await expect(list.getByRole('option')).toHaveCount(20);
  expect((await rows(list)).slice(0, 2)).toEqual([
    'deepseek-r1:14b',
    'gemma3:12b',
  ]);

  // Two favourites, in the order they're added.
  await (
    await rowMenu(page, list, 'qwen3.8:27b')
  )
    .getByRole('menuitem', { name: 'Add to favourites' })
    .click();
  await (
    await rowMenu(page, list, 'llama3.1:8b')
  )
    .getByRole('menuitem', { name: 'Add to favourites' })
    .click();
  await expect(
    list.getByRole('group', { name: 'Favourites' }).getByRole('option'),
  ).toHaveCount(2);

  // Hide three: two by pointer, one from the keyboard.
  for (const name of ['smollm2:1.7b', 'llava:13b']) {
    await (
      await rowMenu(page, list, name)
    )
      .getByRole('menuitem', { name: 'Hide' })
      .click();
    await expect(list.getByRole('option', { name: exactly(name) })).toHaveCount(
      0,
    );
  }
  const search = page.getByRole('combobox', { name: 'Search models' });
  await search.fill('deepseek');
  await search.press('Shift+F10');
  await page.getByRole('menuitem', { name: 'Hide' }).click();
  await expect(search).toBeFocused();
  await search.fill('');

  let order = await rows(list);
  expect(order.slice(0, 2)).toEqual(['qwen3.8:27b', 'llama3.1:8b']);
  expect(order.at(-1)).toBe('More models (3)');
  expect(order).toHaveLength(2 + 15 + 1);
  // The last one hidden is said once in the list, with Undo.
  await expect(
    page.getByText('deepseek-r1:14b is under More models.'),
  ).toBeVisible();
  await list.evaluate((element) => element.scrollTo(0, 0));
  await shoot(page, info, 'models-picker');

  await rowMenu(page, list, 'llama3.1:8b');
  await expect(
    page.getByRole('menuitem', { name: 'Remove from favourites' }),
  ).toBeVisible();
  await shoot(page, info, 'models-row-menu');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('menu')).toHaveCount(0);
  await expect(list).toBeVisible();

  // Saved: a reload lists them the same way.
  await page.reload();
  list = await openPicker(page);
  order = await rows(list);
  expect(order.slice(0, 2)).toEqual(['qwen3.8:27b', 'llama3.1:8b']);
  expect(order.at(-1)).toBe('More models (3)');

  // A hidden model is still usable, and stays in view while it's chosen.
  await list.getByRole('option', { name: 'More models (3)' }).click();
  await expect(
    list.getByRole('group', { name: 'More models' }).getByRole('option'),
  ).toHaveCount(3);
  await list.evaluate((element) => element.scrollTo(0, element.scrollHeight));
  await shoot(page, info, 'models-picker-more');
  await list.getByRole('option', { name: exactly('llava:13b') }).click();
  await expect(list).toHaveCount(0);
  list = await openPicker(page);
  // Listed in its provider's group (and under Recent, as any recent choice).
  const chosen = list
    .getByRole('option', { name: exactly('llava:13b') })
    .last();
  await expect(chosen).toContainText('Hidden');
  await expect(chosen.getByRole('img', { name: 'Current' })).toBeVisible();
  await expect(
    list.getByRole('option', { name: 'More models (2)' }),
  ).toBeVisible();
  await shoot(page, info, 'models-hidden-selected');
});

test('Settings › Models lists the local models with their state', async ({
  page,
}, info) => {
  await fixture(
    page,
    '/__s8_fixture/models/seed?favourites=qwen3.8:27b,llama3.1:8b&hidden=smollm2:1.7b,llava:13b,deepseek-r1:14b',
  );
  await page.goto('/app-v2/settings/models');
  const group = page.getByRole('region', { name: 'On this computer' });
  await expect(group).toContainText('2 favourites · 3 hidden');
  const listed = group.getByRole('list', { name: 'Models on this computer' });
  await expect(listed.getByRole('listitem')).toHaveCount(18);
  await expect(listed.getByRole('listitem').first()).toContainText(
    'qwen3.8:27bListed first',
  );
  await group.evaluate((element) => element.scrollIntoView({ block: 'start' }));
  await shoot(page, info, 'models-settings');

  await group.getByText('Hidden (3)').click();
  const hidden = group.getByRole('list', { name: 'Hidden models' });
  await expect(hidden.getByRole('listitem')).toHaveCount(3);
  await expect(
    hidden.getByRole('button', { name: 'Show llava:13b' }),
  ).toBeVisible();
  await hidden.evaluate((element) => element.scrollIntoView({ block: 'end' }));
  await shoot(page, info, 'models-settings-hidden');
  await group.getByRole('button', { name: 'Show all hidden' }).click();
  await expect(group).not.toContainText('3 hidden');
  await expect(
    group.getByRole('button', { name: 'Show all hidden' }),
  ).toHaveCount(0);
  await expect(listed.getByRole('listitem')).toHaveCount(21);
});

test('Setup lists favourites, then Row-Bot’s picks, then every model', async ({
  page,
}, info) => {
  await fixture(
    page,
    '/__s8_fixture/models/seed?favourites=qwen3.8:27b,qwen3:14b&hidden=smollm2:1.7b',
  );
  await fixture(page, '/__p10_fixture/first-run/fresh');
  await page.goto('/app-v2/');
  await expect(page).toHaveURL(/\/app-v2\/setup$/);
  await page.getByRole('button', { name: /On this computer/ }).click();
  const favourites = page.getByRole('list', { name: 'Your favourites' });
  await expect(favourites.getByRole('button')).toHaveText([
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
  await page.getByText('Show all models (16)').click();
  const all = page.getByRole('list', { name: 'All models on this computer' });
  await expect(all.getByRole('button').last()).toContainText('Hidden');
});
