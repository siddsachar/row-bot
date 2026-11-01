import type { Page, TestInfo } from '@playwright/test';
import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';

// N1, U11, U12: Claude (your Claude plan) runs Claude Code. The fixture answers
// for Claude Code (/__s9_fixture/claude-plan/{state}), stands in for its install
// (/__a5_fixture/claude-install/{outcome}) and plays `claude auth login` in the
// sign-in panel; no spec starts or asks a real Claude Code, and nothing
// reaches Anthropic.

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

async function claudeRow(page: Page, state: string, setUpAgain = false) {
  await fixture(
    page,
    `/__s9_fixture/claude-plan/${state}${setUpAgain ? '?set_up_again=true' : ''}`,
  );
  await page.goto('/app-v2/settings/providers');
  const row = page
    .getByRole('region', { name: 'Subscriptions' })
    .getByRole('listitem')
    .filter({ hasText: 'Claude (your Claude plan)' });
  await expect(row).toBeVisible();
  return row;
}

const SUMMARY =
  'Row-Bot runs Claude Code, Anthropic’s program, with your own Claude plan. Its limits apply.';

const states = [
  { state: 'not_installed', pill: 'Not set up', button: 'Use my Claude plan' },
  { state: 'signed_out', pill: 'Not signed in', button: 'Use my Claude plan' },
  {
    state: 'not_plan',
    pill: 'Not signed in',
    button: 'Sign in again',
    words: 'Claude Code is signed in without a Claude plan',
  },
  {
    state: 'too_old',
    pill: 'Update needed',
    button: 'Update Claude Code',
    words: 'Row-Bot needs Claude Code 2.1.263 or newer.',
  },
  {
    state: 'error',
    pill: 'Couldn’t check',
    button: 'Check again',
    words: "Claude Code didn't say whether it is signed in.",
  },
];

async function claudeDialog(page: Page, state: string) {
  const row = await claudeRow(page, state);
  await row.getByRole('button', { name: 'Set up Claude' }).click();
  return page.getByRole('dialog', { name: 'Claude (your Claude plan)' });
}

for (const { state, pill, button, words } of states)
  test(`Claude plan: ${state} shows one next step`, async ({ page }, info) => {
    const row = await claudeRow(page, state);
    await expect(row).toContainText(pill);
    await row.getByRole('button', { name: 'Set up Claude' }).click();
    const dialog = page.getByRole('dialog', {
      name: 'Claude (your Claude plan)',
    });
    await expect(dialog).toContainText(SUMMARY);
    if (words) await expect(dialog).toContainText(words);
    await expect(dialog.getByRole('button', { name: button })).toBeVisible();
    await expect(dialog).toContainText(
      'Row-Bot never sees your Claude sign-in.',
    );
    // No token field, no device code and no command to run in a terminal.
    await expect(dialog.locator('input')).toHaveCount(0);
    await expect(dialog).not.toContainText(/terminal|claude auth login/i);
    await shoot(page, info, `claude-plan-${state.replace('_', '-')}`);
  });

test('Claude plan: Use my Claude plan installs Claude Code, then Claude’s own sign-in connects it', async ({
  page,
}, info) => {
  const dialog = await claudeDialog(page, 'not_installed');
  await fixture(page, '/__a5_fixture/claude-install/hold');
  await dialog.getByRole('button', { name: 'Use my Claude plan' }).click();
  await expect(dialog).toContainText('Installing Claude Code from Anthropic…');
  await shoot(page, info, 'claude-plan-installing');

  await fixture(page, '/__a5_fixture/claude-install/ok');
  await expect(dialog).toContainText('Installed (version 2.1.300)');
  const panel = dialog.getByRole('region', { name: 'Claude’s sign-in' });
  // The fixture's browser is on this computer: Claude opens the browser itself.
  await expect(panel).toContainText(
    'Finish signing in in the browser window Claude opened.',
  );
  await expect(panel).toContainText('Paste code here if prompted');
  await shoot(page, info, 'claude-plan-sign-in');

  // Claude's link, wrapped across rows, opens whole in a new tab. (The tab
  // itself is only recorded: nothing here reaches claude.ai.)
  await page.evaluate(() => {
    const opened: { href: string; target: string }[] = [];
    Object.assign(window, { openedLinks: opened });
    HTMLAnchorElement.prototype.click = function (this: HTMLAnchorElement) {
      opened.push({ href: this.href, target: this.target });
    };
  });
  const row = panel
    .locator('.xterm-rows > div')
    .filter({ hasText: 'https://claude.ai' });
  const box = (await row.boundingBox())!;
  await page.mouse.move(box.x + 24, box.y + box.height / 2);
  await page.waitForTimeout(300);
  await page.mouse.click(box.x + 24, box.y + box.height / 2);
  await expect
    .poll(() =>
      page.evaluate(
        () => (window as unknown as { openedLinks: unknown[] }).openedLinks,
      ),
    )
    .toEqual([
      {
        href: 'https://claude.ai/oauth/authorize?code=true&client_id=fixture&response_type=code&state=fixture',
        target: '_blank',
      },
    ]);

  const terminal = panel.getByRole('textbox', { name: 'Terminal input' });
  await terminal.focus();
  await page.keyboard.type('not-the-code');
  await page.keyboard.press('Enter');
  // Claude's own refusal, and its prompt stays open.
  await expect(panel).toContainText('OAuth error: Invalid code');
  await shoot(page, info, 'claude-plan-wrong-code');

  await terminal.focus();
  await page.keyboard.type('FIXTURE-CODE');
  await page.keyboard.press('Enter');
  await expect(panel).toBeHidden();
  await expect(dialog).toContainText('Claude plan connected');
  await expect(dialog).toContainText('Claude Max · Claude Code 2.1.282');
  await shoot(page, info, 'claude-plan-connected');
});

test('Claude plan: an install that failed says why, and Try again', async ({
  page,
}, info) => {
  const dialog = await claudeDialog(page, 'not_installed');
  await fixture(page, '/__a5_fixture/claude-install/checksum_mismatch');
  await dialog.getByRole('button', { name: 'Use my Claude plan' }).click();
  await expect(dialog.getByRole('alert')).toContainText(
    'Claude Code couldn’t be installed: the download didn’t match Anthropic’s checksum, so it was deleted.',
  );
  await shoot(page, info, 'claude-plan-install-failed');
  await fixture(page, '/__a5_fixture/claude-install/ok');
  await dialog.getByRole('button', { name: 'Try again' }).click();
  await expect(
    dialog.getByRole('region', { name: 'Claude’s sign-in' }),
  ).toBeVisible();
});

test('Claude plan: closing Claude’s sign-in early says so, and Sign in again', async ({
  page,
}, info) => {
  const dialog = await claudeDialog(page, 'signed_out');
  await dialog.getByRole('button', { name: 'Use my Claude plan' }).click();
  const panel = dialog.getByRole('region', { name: 'Claude’s sign-in' });
  await expect(panel).toContainText('Paste code here if prompted');
  await panel.getByRole('button', { name: 'Close Claude’s sign-in' }).click();
  await expect(dialog.getByRole('alert')).toContainText(
    'Claude’s sign-in was closed before it finished.',
  );
  await shoot(page, info, 'claude-plan-closed-early');
  await dialog.getByRole('button', { name: 'Sign in again' }).click();
  await expect(panel).toContainText('Paste code here if prompted');
});

test('Claude plan: ready shows the plan and Claude Code’s version', async ({
  page,
}, info) => {
  const row = await claudeRow(page, 'ready');
  await expect(row).toContainText('Ready');
  await expect(row).toContainText('Runs Claude Code with your Claude plan');
  await expect(row).toContainText('Claude Max');
  await shoot(page, info, 'claude-plan-ready-row');
  await row
    .getByRole('button', { name: 'More actions for Claude (your Claude plan)' })
    .click();
  await page.getByRole('menuitem', { name: 'Check Claude Code' }).click();
  const dialog = page.getByRole('dialog', {
    name: 'Claude (your Claude plan)',
  });
  await expect(dialog).toContainText('Claude plan connected');
  await expect(dialog).toContainText('Claude Max · Claude Code 2.1.282');
  await expect(
    dialog.getByRole('button', { name: 'Update Claude Code' }),
  ).toBeVisible();
  await shoot(page, info, 'claude-plan-ready');
  await dialog.getByRole('button', { name: 'Done' }).click();
  await expect(dialog).toBeHidden();
});

test('Claude plan: a profile the old sign-in set up is told to set it up again', async ({
  page,
}, info) => {
  const row = await claudeRow(page, 'signed_out', true);
  await expect(row).toContainText('Set up again');
  await expect(row).toContainText(
    'Claude now works through Claude Code. Set it up again',
  );
  await shoot(page, info, 'claude-plan-set-up-again-row');
  await row.getByRole('button', { name: 'Set up Claude' }).click();
  await expect(
    page.getByRole('dialog', { name: 'Claude (your Claude plan)' }),
  ).toContainText('Claude now works through Claude Code. Set it up again.');
  await shoot(page, info, 'claude-plan-set-up-again');
});

test('Claude plan: Check again reads Claude Code afresh', async ({ page }) => {
  const dialog = await claudeDialog(page, 'error');
  await fixture(page, '/__s9_fixture/claude-plan/ready');
  await dialog.getByRole('button', { name: 'Check again' }).click();
  await expect(dialog).toContainText('Claude Max · Claude Code 2.1.282');
});

test('Claude plan in the first run: Use my Claude plan, never a Row-Bot sign-in', async ({
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
    await expect(panel).toContainText(SUMMARY);
    await expect(
      panel.getByRole('button', { name: 'Use my Claude plan' }),
    ).toBeVisible();
    await expect(panel).not.toContainText(/claude auth login/);
    await shoot(page, info, 'claude-plan-first-run');
  } finally {
    await fixture(page, '/__p10_fixture/first-run/restore');
  }
});
