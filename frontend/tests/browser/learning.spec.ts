import type { Page, TestInfo } from '@playwright/test';
import { onPhoneWidth } from './phone-helpers';
import { accessibility, assertNoOverflow, expect, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';
import { composer, newConversation } from './unified-helpers';

// Learning, L1a and O195: on from the start (Home says so once), Teach on a reply
// and Undo, a chat switched off, training asking to download, off and on again,
// and the Overview's states. The fixture stands in for the download, fakes the
// hardware check and scripts the reply; nothing reaches a GPU, a download or a
// provider. Each test puts the profile back.

/**
 * Home shows Learning: its sub-tab on a computer (open when ``selected``). A phone has no
 * sub-tabs (C1); there Home's view is Learning's when it is open.
 */
async function expectLearningTab(page: Page, selected = false) {
  if (onPhoneWidth(page)) {
    if (selected)
      await expect(page.locator('.home-view')).toHaveAttribute(
        'data-home-tab',
        'learning',
      );
    return;
  }
  const tab = page.getByRole('tab', { name: 'Learning' });
  await expect(tab).toBeVisible();
  if (selected) await expect(tab).toHaveAttribute('aria-selected', 'true');
}

async function fixture(page: Page, action: string) {
  const response = await page.request.post(
    `/__l1a_fixture/learning/${action}`,
    {
      headers: fixtureHeaders(),
    },
  );
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
  // Close the app first: restoring deletes Learning's data and its stand-ins, and
  // a poll still due (download, training) would read them half gone.
  await page.close();
  await fixture(page, 'restore');
});

const phone = (info: TestInfo) =>
  (info.project.use.viewport?.width ?? 1440) < 700;

/**
 * `<surface>-<state>-{desktop,phone}-{light,dark}.png` in the test's output
 * folder, each with an axe scan.
 */
async function shoot(
  page: Page,
  info: TestInfo,
  name: string,
  { axe = true }: { axe?: boolean } = {},
) {
  const size = phone(info) ? 'phone' : 'desktop';
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    const file = info.outputPath(`${name}-${size}-${appearance}.png`);
    await page.screenshot({
      path: file,
      fullPage: true,
      animations: 'disabled',
    });
    await info.attach(`${name}-${size}-${appearance}`, {
      path: file,
      contentType: 'image/png',
    });
    // An open menu hides the page behind it (aria-hidden) while it holds
    // focus; its screens are scanned once it closes, on the dialog it opens.
    if (axe) await accessibility(page, info, `${name}-${size}-${appearance}`);
  }
  await page.emulateMedia({ colorScheme: 'light' });
}

/** A notice by its words (not Radix's announcement copy of them). */
function notice(page: Page, text: string | RegExp) {
  return page.locator('.toast-message').filter({ hasText: text });
}

async function openSettings(page: Page, leaf: string) {
  await page.goto(`/app-v2/settings/${leaf}`);
  // A phone's header row names the page, not Settings (C3).
  await expect(
    page.getByRole('region', { name: 'Settings', exact: true }),
  ).toBeVisible();
}

/**
 * Train during quiet hours in Settings › Learning: Learning is on by default (O195),
 * training a personal model is the person's own step, and it asks to download.
 */
async function trainingOn(
  page: Page,
  info: TestInfo,
  screens: boolean,
  answer: 'Download' | 'Not now' = 'Download',
) {
  await openSettings(page, 'learning');
  await page.getByRole('switch', { name: 'Train during quiet hours' }).click();
  const ask = page.getByRole('dialog', {
    name: 'Download the training engine and Qwen3.5 9B?',
  });
  await expect(ask).toBeVisible();
  if (screens) await shoot(page, info, 'download-ask');
  await ask.getByRole('button', { name: answer, exact: true }).click();
  await expect(
    notice(
      page,
      answer === 'Download'
        ? /^Downloading the training engine/
        : 'Not downloading for now.',
    ),
  ).toBeVisible();
}

/** The chat's menu: the sidebar row's on a desktop, the header's ⋯ on a phone. */
async function openChatMenu(page: Page, info: TestInfo, title: string) {
  if (phone(info)) {
    await page.getByRole('button', { name: 'Conversation menu' }).click();
    return;
  }
  await page
    .getByRole('button', { name: `Actions for ${title}`, exact: true })
    .click();
}

test('Learning is on from the start: Home says so once, Teach a reply and undo it, then switch a chat off', async ({
  page,
}, info) => {
  // 17 screens in light and dark, each with an axe scan, and nine page loads: more than 60 s on a busy runner.
  test.slow();
  // "Learn new skills" lives in Settings › Learning, not Preferences (D29).
  await fixture(page, 'stand-ins');
  await fixture(page, 'gpu');
  await openSettings(page, 'preferences');
  await expect(
    page.getByRole('switch', { name: 'Learn new skills' }),
  ).toHaveCount(0);
  await shoot(page, info, 'preferences');

  // Home says once that Learning is on, what it does and what it costs (O195); Got it ends it everywhere.
  await page.goto('/app-v2/');
  const intro = page.getByRole('region', {
    name: 'Row-Bot learns from your corrections',
  });
  await expect(intro).toBeVisible();
  await expect(intro).toContainText('What it records stays on this computer.');
  await expectLearningTab(page);
  await shoot(page, info, 'home-intro');
  await intro.getByRole('button', { name: 'Got it' }).click();
  await expect(intro).toHaveCount(0);
  await page.reload();
  await expectLearningTab(page);
  await expect(
    page.getByRole('region', { name: 'Row-Bot learns from your corrections' }),
  ).toHaveCount(0);

  // Teach on a reply from the start: the sheet, the thanks and Undo.
  await newConversation(page);
  await composer(page).fill('l1a teach fixture');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  const reply = page.getByText(
    'The 2026 budget for travel is 1,200 (from the draft).',
  );
  await expect(reply).toBeVisible();
  const teach = page.getByRole('button', { name: 'Teach Row-Bot' });
  await expect(teach).toBeVisible();
  await shoot(page, info, 'reply-toolbar');
  await teach.click();
  const sheet = page.getByRole('dialog', { name: 'Teach Row-Bot' });
  await sheet.getByRole('radio', { name: 'Wrong answer' }).click();
  await sheet
    .getByRole('textbox', { name: 'What should it do next time?' })
    .fill('Use the approved budget, not the draft.');
  await shoot(page, info, 'teach-sheet');
  await sheet.getByRole('button', { name: 'Send' }).click();
  await expect(
    notice(page, 'Thanks. Row-Bot will learn from this.'),
  ).toBeVisible();
  await shoot(page, info, 'teach-toast');
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(
    notice(page, 'Removed. Row-Bot won’t learn from it.'),
  ).toBeVisible();

  // The failed step offers Teach too.
  await page.getByText(/^Used 1 tool/).click();
  await page.locator('.activity-step summary').first().click();
  await expect(
    page.getByRole('button', { name: 'Teach Row-Bot' }).nth(1),
  ).toBeVisible();

  // Don't learn from this chat: a check item, and its confirm.
  const title =
    (await page.locator('.conversation-heading h1').textContent()) ?? '';
  await openChatMenu(page, info, title.trim());
  const item = page.getByRole('menuitemcheckbox', {
    name: 'Don’t learn from this chat',
  });
  await expect(item).toHaveAttribute('aria-checked', 'false');
  await shoot(page, info, 'chat-menu', { axe: false });
  await item.click();
  const confirm = page.getByRole('alertdialog', {
    name: 'Don’t learn from this chat?',
  });
  await expect(confirm).toContainText(
    'This also deletes what Learning recorded from this chat.',
  );
  await shoot(page, info, 'chat-menu-confirm');
  await confirm
    .getByRole('button', { name: 'Don’t learn from this chat' })
    .click();
  await expect(
    notice(
      page,
      'Row-Bot won’t learn from this chat. What it recorded from it is deleted.',
    ),
  ).toBeVisible();
  // Switched off: no Teach here any more.
  await expect(page.getByRole('button', { name: 'Teach Row-Bot' })).toHaveCount(
    0,
  );
});

test('Settings › Learning: on, its experimental parts and the personal model off, training asks to download, and off and on again asks nothing', async ({
  page,
}, info) => {
  await fixture(page, 'stand-ins');
  await fixture(page, 'gpu');
  await openSettings(page, 'learning');
  await expect(page.getByText('Learning on', { exact: true })).toBeVisible();
  await expect(
    page.getByRole('switch', { name: 'Create skills while chatting' }),
  ).toBeChecked();
  await expect(
    page.getByRole('switch', { name: 'Try experimental learning' }),
  ).not.toBeChecked();
  await expect(
    page.getByRole('switch', { name: 'Train during quiet hours' }),
  ).not.toBeChecked();
  await shoot(page, info, 'settings-on');

  // Download in the ask starts it (L2b; the fixture's download stands still).
  await trainingOn(page, info, true);
  await expect(
    page.getByText('Downloading the training engine').first(),
  ).toBeVisible();
  // The monthly limit: a custom amount in millions, then No limit, which asks first and still shows spending.
  const limit = page.getByRole('combobox', { name: 'Monthly limit' });
  await limit.scrollIntoViewIfNeeded();
  await limit.selectOption({ label: 'Custom amount…' });
  const amount = page.getByRole('textbox', {
    name: 'Custom monthly limit, in millions of tokens',
  });
  await expect(amount).toBeFocused();
  await amount.fill('1200');
  await amount.press('Enter');
  await expect(amount).toHaveAccessibleDescription(
    'Enter 0.1 to 1,000 million tokens, like 75 or 2.5.',
  );
  await shoot(page, info, 'settings-limit-custom-error');
  await amount.fill('75');
  await amount.press('Enter');
  await expect(notice(page, 'Monthly limit set to 75M tokens')).toBeVisible();
  await shoot(page, info, 'settings-limit-custom');
  await limit.selectOption({ label: 'No limit' });
  const ask = page.getByRole('group', { name: 'Confirm Monthly limit' });
  await expect(ask).toBeVisible();
  await shoot(page, info, 'settings-limit-no-limit-ask');
  await ask.getByRole('button', { name: 'Change it' }).click();
  await expect(notice(page, 'Learning has no monthly limit now')).toBeVisible();
  await expect(
    page.getByText(/tokens used this month \(no limit\)$/),
  ).toBeVisible();
  await shoot(page, info, 'settings-limit-none');
  await limit.selectOption({ label: '2M tokens' });
  await expect(notice(page, 'Monthly limit set to 2M tokens')).toBeVisible();
  // The rest of the page (it scrolls inside the settings pane).
  await page
    .locator('#settings-page')
    .evaluate((element) => (element.scrollTop = element.scrollHeight));
  await shoot(page, info, 'settings-on-lower');

  // Off, then on again: the sheet starts from the saved choices and never asks to download.
  await page.getByRole('switch', { name: 'Learning', exact: true }).click();
  await page
    .getByRole('dialog', { name: 'Turn off Learning?' })
    .getByRole('button', { name: 'Turn off' })
    .click();
  await expect(page.getByText('Learning off', { exact: true })).toBeVisible();
  await shoot(page, info, 'settings-off');
  await page.getByRole('button', { name: 'Turn on Learning' }).click();
  const setup = page.getByRole('dialog', { name: 'Turn on Learning' });
  await expect(
    setup.getByText(/Nothing downloads when you turn Learning on\.$/),
  ).toBeVisible();
  await shoot(page, info, 'setup');
  await setup.getByRole('button', { name: 'Turn on Learning' }).click();
  await expect(notice(page, 'Learning is on.')).toBeVisible();
  await expect(page.getByRole('dialog', { name: /^Download/ })).toHaveCount(0);
  await page.getByRole('button', { name: 'Open Learning' }).click();
  await expectLearningTab(page, true);
  await expect(page.getByText('Nothing learned yet.')).toBeVisible();
  await shoot(page, info, 'overview-empty');
});

test('the Learning tab: paused, limit reached, reading past chats, its other views and deleting learning data', async ({
  page,
}, info) => {
  await fixture(page, 'stand-ins');
  await fixture(page, 'gpu');
  await trainingOn(page, info, false, 'Not now');
  const open = async (view = '') => {
    await page.goto(`/app-v2/?tab=learning${view ? `&view=${view}` : ''}`);
    await expect(
      page.getByRole('heading', { name: 'Learning', exact: true }),
    ).toBeVisible();
  };

  await fixture(page, 'reading');
  await open();
  await expect(
    page.getByText('Reading 214 past chats · 61 done'),
  ).toBeVisible();
  await shoot(page, info, 'overview-reading');

  await fixture(page, 'limit');
  await open();
  await expect(page.getByText(/^Monthly limit reached/)).toBeVisible();
  await shoot(page, info, 'overview-limit');

  await page.getByRole('button', { name: 'More for Learning' }).click();
  await page.getByRole('menuitem', { name: 'Pause' }).click();
  await expect(
    page.getByText(
      'Learning is paused. Nothing new is recorded, tested or trained.',
    ),
  ).toBeVisible();
  await shoot(page, info, 'overview-paused');

  await open('changes');
  await expect(
    page.getByRole('radiogroup', { name: 'Show changes' }),
  ).toBeVisible();
  await shoot(page, info, 'changes-empty');
  await open('lessons');
  await expect(page.getByText(/^No lessons yet/)).toBeVisible();
  await shoot(page, info, 'lessons-empty');
  await open('model');
  await expect(
    page.getByRole('heading', { name: 'No personal model yet' }),
  ).toBeVisible();
  await expect(
    page.getByText('NVIDIA GeForce RTX 5080 Laptop (16 GB)'),
  ).toBeVisible();
  await shoot(page, info, 'model-not-set-up');
  await page.getByRole('button', { name: 'Download…' }).click();
  await page
    .getByRole('dialog', {
      name: 'Download the training engine and Qwen3.5 9B?',
    })
    .getByRole('button', { name: 'Download', exact: true })
    .click();
  // Learning is paused here: the answer is kept and it reads as Ready to download (O36).
  await expect(
    notice(page, 'Ready to download once you resume Learning.'),
  ).toBeVisible();
  await expect(
    page
      .getByRole('region', { name: 'Download' })
      .getByText('Ready to download', { exact: true }),
  ).toBeVisible();
  await shoot(page, info, 'model-ready');

  await page.getByRole('button', { name: 'More for Learning' }).click();
  await page.getByRole('menuitem', { name: 'Delete learning data…' }).click();
  const dialog = page.getByRole('alertdialog', {
    name: 'Delete learning data?',
  });
  await expect(dialog).toContainText(
    'chats you switched off stay switched off.',
  );
  await shoot(page, info, 'delete-dialog');
  await dialog.getByRole('button', { name: 'Delete learning data' }).click();
  await expect(notice(page, /^Learning data deleted/)).toBeVisible();
});

test('Learning data says how much of it is browser and computer pictures', async ({
  page,
}, info) => {
  await fixture(page, 'stand-ins');
  await fixture(page, 'gpu');
  await fixture(page, 'pictures');
  await openSettings(page, 'learning');

  const group = page.getByRole('heading', { name: 'Learning data' });
  await group.scrollIntoViewIfNeeded();
  await expect(
    page.getByText(
      /, including 960\sKB of browser and computer pictures \(up to 10\sGB; the oldest go first\)\. Turning Learning off keeps it\.$/,
    ),
  ).toBeVisible();
  await shoot(page, info, 'settings-learning-data-pictures');
});

test('without an NVIDIA graphics card the Personal model view says Learning still improves Row-Bot (O131)', async ({
  page,
}, info) => {
  // A model trained on Hugging Face runs here after, so with no card to run one it isn't offered.
  await fixture(page, 'stand-ins');
  await fixture(page, 'no-gpu');
  await page.goto('/app-v2/?tab=learning&view=model');
  await expect(
    page.getByText(
      /A personal model needs an NVIDIA graphics card\. Learning still improves Row-Bot with lessons and ways of working\./,
    ),
  ).toBeVisible();
  await expect(page.getByText(/Hugging Face instead/)).toHaveCount(0);
  await shoot(page, info, 'model-no-gpu');
});

test('a graphics card too small to train offers Hugging Face, and training there asks nothing to download (O124)', async ({
  page,
}, info) => {
  await fixture(page, 'stand-ins');
  await fixture(page, 'small-gpu');
  await page.goto('/app-v2/?tab=learning&view=model');
  await expect(
    page.getByRole('heading', { name: 'This computer can’t train a model' }),
  ).toBeVisible();
  await expect(
    page.getByText(/You can train one on Hugging Face instead/),
  ).toBeVisible();
  await shoot(page, info, 'model-small-gpu');
  await page.getByRole('button', { name: 'Learning settings' }).click();
  await page.getByRole('radio', { name: 'Hugging Face' }).click();
  await expect(notice(page, /^Where to train/)).toBeVisible();
  await page.getByRole('switch', { name: 'Train during quiet hours' }).click();
  await expect(
    notice(page, 'Train during quiet hours turned on'),
  ).toBeVisible();
  await expect(page.getByRole('dialog', { name: /^Download/ })).toHaveCount(0);
  await page.goto('/app-v2/?tab=learning&view=model');
  await expect(
    page.getByRole('heading', { name: 'Training on Hugging Face' }),
  ).toBeVisible();
  await shoot(page, info, 'model-hugging-face');
});
