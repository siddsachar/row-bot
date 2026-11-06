import type { Page, TestInfo } from '@playwright/test';
import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';

// N1: Claude (your Claude plan) runs the person's own Claude Code. The fixture
// answers for Claude Code (/__s9_fixture/claude-plan/{state}); no spec starts
// or asks a real Claude Code, and nothing reaches Anthropic.

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
  await fixture(page, '/__s9_fixture/claude-plan/restore');
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

/** Claude's sheet: from its tile once ready, else from Add a provider. */
async function claudeSheet(page: Page, state: string, setUpAgain = false) {
  await fixture(
    page,
    `/__s9_fixture/claude-plan/${state}${setUpAgain ? '?set_up_again=true' : ''}`,
  );
  await page.goto('/app-v2/settings/models');
  const connected = page.getByRole('region', { name: 'Connected' });
  const add = connected.getByRole('button', { name: 'Add a provider' });
  await expect(add).toBeVisible();
  const tile = connected.locator('.settings-provider-tile', {
    hasText: 'Claude (your Claude plan)',
  });
  if (await tile.count()) await tile.click();
  else {
    await add.click();
    const gallery = page.getByRole('dialog', { name: 'Add a provider' });
    await gallery.getByRole('tab', { name: 'Subscription' }).click();
    await gallery
      .getByRole('button', { name: /Claude \(your Claude plan\)/ })
      .click();
  }
  const sheet = page.getByRole('dialog', { name: 'Claude (your Claude plan)' });
  await expect(sheet).toBeVisible();
  return sheet;
}

const states = [
  {
    state: 'not_installed',
    pill: 'Not set up',
    words: 'Install Claude Code on this computer, then come back.',
  },
  {
    state: 'signed_out',
    pill: 'Not signed in',
    words: 'Open a terminal and run this, then sign in with your Claude plan:',
  },
  {
    state: 'not_plan',
    pill: 'Not signed in',
    words: 'Claude Code is signed in without a Claude plan',
  },
  {
    state: 'too_old',
    pill: 'Update needed',
    words:
      'Claude Code 2.1.100 is too old for Row-Bot; it needs 2.1.263 or newer.',
  },
  {
    state: 'error',
    pill: 'Couldn’t check',
    words: "Claude Code didn't say whether it is signed in.",
  },
];

for (const { state, pill, words } of states)
  test(`Claude plan: ${state} shows what Claude Code says and one next step`, async ({
    page,
  }, info) => {
    const dialog = await claudeSheet(page, state);
    await expect(dialog).toContainText(pill);
    await expect(dialog).toContainText(words);
    await expect(dialog).toContainText(
      'Row-Bot never sees your Claude sign-in.',
    );
    // No token field or device code anywhere.
    await expect(dialog.locator('input')).toHaveCount(0);
    await shoot(page, info, `claude-plan-${state.replace('_', '-')}`);
  });

test('Claude plan: ready shows the plan and Claude Code’s version', async ({
  page,
}, info) => {
  await fixture(page, '/__s9_fixture/claude-plan/ready');
  await page.goto('/app-v2/settings/models');
  const tile = page
    .getByRole('region', { name: 'Connected' })
    .locator('.settings-provider-tile', {
      hasText: 'Claude (your Claude plan)',
    });
  await expect(tile).toContainText('Subscription');
  await shoot(page, info, 'claude-plan-ready-row');
  const dialog = await claudeSheet(page, 'ready');
  await expect(dialog).toContainText('Ready');
  await expect(dialog).toContainText('Runs Claude Code on this computer');
  await expect(dialog).toContainText('Claude Max · Claude Code 2.1.282');
  await shoot(page, info, 'claude-plan-ready');
  await dialog.getByRole('button', { name: 'Done' }).click();
  await expect(dialog).toBeHidden();
});

test('Claude plan: a profile the old sign-in set up is told to set it up again', async ({
  page,
}, info) => {
  const dialog = await claudeSheet(page, 'signed_out', true);
  await expect(dialog).toContainText('Set up again');
  await expect(dialog).toContainText(
    'Claude now works through Claude Code. Set it up again.',
  );
  await shoot(page, info, 'claude-plan-set-up-again');
});

test('Claude plan: Check again reads Claude Code afresh', async ({ page }) => {
  const dialog = await claudeSheet(page, 'signed_out');
  await expect(dialog).toContainText('claude auth login');
  await fixture(page, '/__s9_fixture/claude-plan/ready');
  await dialog.getByRole('button', { name: 'Check again' }).click();
  await expect(dialog).toContainText('Claude Max · Claude Code 2.1.282');
});

test('Claude plan in the first run: Claude Code’s steps, never a Row-Bot sign-in', async ({
  page,
}, info) => {
  await fixture(page, '/__s9_fixture/claude-plan/signed_out');
  await fixture(page, '/__p10_fixture/first-run/fresh?runtime=not_installed');
  try {
    await page.goto('/app-v2/');
    await expect(page).toHaveURL(/\/app-v2\/setup$/);
    await page
      .getByRole('group', { name: 'How should Row-Bot think?' })
      .getByRole('button', { name: /With my subscription/ })
      .click();
    await page.getByRole('radio', { name: 'Claude' }).click();
    const panel = page.getByRole('region', { name: 'With my subscription' });
    await expect(panel).toContainText(
      'Runs Claude Code on this computer; uses your Claude plan’s limits.',
    );
    await expect(panel).toContainText('claude auth login');
    await shoot(page, info, 'claude-plan-first-run');
  } finally {
    await fixture(page, '/__p10_fixture/first-run/restore');
  }
});
