import type { Page, TestInfo } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';
import { composer, newConversation } from './unified-helpers';

/*
 * A0 · approval levels: Ask, Auto (a reviewer approves what matches the
 * person's request), Hands-off (on purpose, one dialog the first time)
 * and Block. The fixture scripts the model; the approval control, the
 * one-time confirmation and the approval card run through the real server.
 * (Named to run after bootstrap.spec.ts, which needs a backend with no turns.)
 */

const PHONE = { width: 390, height: 844 };

async function send(page: Page, text: string) {
  await composer(page).fill(text);
  await page.getByRole('button', { name: 'Send', exact: true }).click();
}

/** Evidence in light and dark at the current size. */
async function views(page: Page, info: TestInfo, name: string) {
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await expect(page.locator('html')).toHaveAttribute(
      'data-theme',
      appearance,
    );
    await assertNoOverflow(page);
    await screenshot(page, info, `${name}-${appearance}`);
  }
  await page.emulateMedia({ colorScheme: 'light' });
}

async function forgetFullAutonomy(page: Page) {
  const reset = await page.request.post('/__a0_fixture/full-autonomy/reset', {
    headers: fixtureHeaders(),
  });
  expect(reset.ok()).toBe(true);
}

test('Hands-off is turned on once, on purpose, and reads in words while on', async ({
  page,
}, info) => {
  await forgetFullAutonomy(page);
  await newConversation(page);
  const approvals = page.getByRole('button', {
    name: /^Approvals: /,
  });
  await approvals.click();
  const menu = page.getByRole('menu');
  await expect(menu.getByRole('menuitem')).toHaveText([
    /^Ask/,
    /^Auto/,
    /^Hands-off/,
    /^Block/,
  ]);
  await expect(
    menu.getByText('A reviewer approves what you asked for; the rest asks'),
  ).toBeVisible();
  await views(page, info, 'approval-levels-menu');

  await menu.getByRole('menuitem', { name: /^Hands-off/ }).click();
  const dialog = page.getByRole('alertdialog', {
    name: 'Turn on Hands-off?',
  });
  await expect(dialog).toContainText('Screen and camera captures');
  await expect(dialog).toContainText(
    'Conversations shared with other people stay on Auto',
  );
  await views(page, info, 'hands-off-dialog');
  await dialog.getByRole('button', { name: 'Turn on', exact: true }).click();
  // Focus returns to the control; its hint is not part of the evidence.
  await page.evaluate(() =>
    (document.activeElement as HTMLElement | null)?.blur(),
  );

  const on = page.getByRole('button', { name: 'Approvals: Hands-off' });
  await expect(on).toHaveText(/Hands-off/);
  await views(page, info, 'hands-off-on');

  // Saved with the conversation, and the dialog never shows again.
  await page.reload();
  await expect(on).toBeVisible();
  await on.click();
  await page.getByRole('menuitem', { name: /^Auto/ }).click();
  await expect(approvals).toBeVisible();
  await approvals.click();
  await page.getByRole('menuitem', { name: /^Hands-off/ }).click();
  await expect(on).toBeVisible();
  await expect(page.getByRole('alertdialog')).toHaveCount(0);
});

test('on a phone the levels sit under + and Hands-off still shows', async ({
  page,
}, info) => {
  await forgetFullAutonomy(page);
  await page.setViewportSize(PHONE);
  await newConversation(page);
  await page.getByRole('button', { name: 'Add files and more' }).click();
  await page.getByRole('menuitem', { name: /^Approvals/ }).press('ArrowRight');
  const levels = page.getByRole('menu').last();
  await expect(levels.getByRole('menuitemradio')).toHaveText([
    /^Ask/,
    /^Auto/,
    /^Hands-off/,
    /^Block/,
  ]);
  await views(page, info, 'approval-levels-menu-phone');
  await levels.getByRole('menuitemradio', { name: /^Hands-off/ }).click();
  const dialog = page.getByRole('alertdialog', {
    name: 'Turn on Hands-off?',
  });
  await views(page, info, 'hands-off-dialog-phone');
  await dialog.getByRole('button', { name: 'Turn on', exact: true }).click();
  // Focus returns to the control; its hint is not part of the evidence.
  await page.evaluate(() =>
    (document.activeElement as HTMLElement | null)?.blur(),
  );
  await expect(
    page.getByRole('status').filter({ hasText: 'Hands-off' }),
  ).toBeVisible();
  await views(page, info, 'hands-off-on-phone');
});

test('an ordinary action may be allowed for the rest of the chat', async ({
  page,
}, info) => {
  await newConversation(page);
  await send(page, 'Save the cut list outside files fixture');
  const card = page.getByRole('complementary', {
    name: /^Approval required/,
  });
  await expect(card).toContainText(
    'Outside the workspace: D:/Videos/trip/cut-list.txt',
  );
  const allow = card.getByRole('button', { name: /Allow in this chat/ });
  await expect(allow).toBeVisible();
  await views(page, info, 'allow-in-this-chat-card');
  await page.setViewportSize(PHONE);
  // A phone keeps Deny and Approve side by side, one thumb each, and the
  // allowance among the choices under More choices (D4).
  await expect(allow).toBeHidden();
  const [deny, approve] = await Promise.all([
    card.getByRole('button', { name: 'Deny', exact: true }).boundingBox(),
    card.getByRole('button', { name: 'Approve', exact: true }).boundingBox(),
  ]);
  expect(Math.abs(deny!.width - approve!.width)).toBeLessThanOrEqual(1);
  expect(Math.abs(deny!.y - approve!.y)).toBeLessThanOrEqual(1);
  expect(deny!.height).toBeGreaterThanOrEqual(44);
  await views(page, info, 'allow-in-this-chat-card-phone');
  await card.getByRole('button', { name: 'More choices', exact: true }).click();
  await expect(
    page.getByRole('menuitem', { name: 'Allow in this chat' }),
  ).toBeVisible();
  await page.keyboard.press('Escape');
  await page.setViewportSize({ width: 1280, height: 900 });
  // Answer it: an approval left waiting shows in every later spec's sidebar and Buddy.
  await card.getByRole('button', { name: 'Deny', exact: true }).click();
  await expect(
    page
      .getByRole('log', { name: 'Conversation' })
      .getByText('You said no, so Row-Bot stopped there.', { exact: true }),
  ).toBeVisible();
});

test('a proposed conversation starts with one tap and reads as from this chat (5.3.1 C)', async ({
  page,
}, info) => {
  await newConversation(page);
  await send(page, 'Plan the launch linked conversation fixture');
  const card = page.getByRole('group', {
    name: 'Make the 5.3 demo video · proposed',
  });
  await expect(card.getByRole('button', { name: 'Start' })).toBeVisible();
  await expect(card.getByRole('button', { name: 'Not now' })).toBeVisible();
  await views(page, info, 'linked-conversation-proposed');
  await page.setViewportSize(PHONE);
  await views(page, info, 'linked-conversation-proposed-phone');
  await page.setViewportSize({ width: 1280, height: 900 });

  await card.getByRole('button', { name: 'Start' }).click();
  const started = page.getByRole('group', {
    name: /^Make the 5\.3 demo video · (working|done)$/,
  });
  await expect(started.getByRole('button', { name: 'Open' })).toBeVisible();
  await started.getByRole('button', { name: 'Open' }).click();
  const from = page.getByRole('note').filter({ hasText: 'From ' });
  await expect(from).toContainText(
    'Script, record and cut a 60 second demo of the browser pane.',
  );
  await expect(
    page.getByRole('button', {
      name: 'Make the 5.3 demo video (started by another conversation)',
    }),
  ).toBeVisible();
  await views(page, info, 'linked-conversation-from');
  await page.setViewportSize(PHONE);
  await views(page, info, 'linked-conversation-from-phone');
});

test('the reviewer’s decisions are visible but quiet', async ({
  page,
}, info) => {
  await newConversation(page);
  // The chat runs on Auto: the reviewer decides first.
  await page.getByRole('button', { name: /^Approvals: / }).click();
  await page.getByRole('menuitem', { name: /^Auto/ }).click();
  await send(page, 'Tidy my notes reviewer asked fixture');
  const card = page.getByRole('complementary', {
    name: /^Approval required/,
  });
  await expect(card).toContainText(
    'The reviewer asked you: You asked to tidy the notes, not to delete any.',
  );
  // Already on Auto: nothing to switch to.
  await expect(
    card.getByRole('button', { name: 'Use Auto in this chat' }),
  ).toHaveCount(0);
  await views(page, info, 'reviewer-asked-card');
  await page.setViewportSize(PHONE);
  await views(page, info, 'reviewer-asked-card-phone');
  await page.setViewportSize({ width: 1280, height: 900 });
  await card.getByRole('button', { name: 'Deny', exact: true }).click();
  // The conversation itself: Buddy's companion can show the latest reply too.
  const transcript = page.getByRole('log', { name: 'Conversation' });
  await expect(
    transcript.getByText('You said no, so Row-Bot stopped there.', {
      exact: true,
    }),
  ).toBeVisible();

  await send(page, 'Save and commit decided without asking fixture');
  await expect(
    transcript.getByText('Saved the summary and committed the tidy-up.', {
      exact: true,
    }),
  ).toBeVisible();
  const steps = transcript.getByText('Used 2 tools', { exact: true }).last();
  const reviewed = transcript.getByText('Reviewer approved', { exact: true });
  const full = transcript.getByText('Hands-off', { exact: true }).last();
  // The live steps settle into the saved ones as the turn ends (collapsed
  // again, seen on CI): open them, and again if they were replaced.
  await expect(async () => {
    if (!(await reviewed.isVisible())) await steps.click();
    await expect(reviewed).toBeVisible({ timeout: 1_000 });
  }).toPass({ timeout: 15_000 });
  await expect(full).toBeVisible();
  await reviewed.click();
  await expect(
    transcript.getByText(
      'Approved by the reviewer: You asked to save the summary to your notes',
    ),
  ).toBeVisible();
  await full.click();
  await expect(
    transcript.getByText('Allowed by Hands-off', { exact: true }),
  ).toBeVisible();
  await views(page, info, 'activity-decided-without-asking');
  await page.setViewportSize(PHONE);
  await views(page, info, 'activity-decided-without-asking-phone');
});
