import type { Page, TestInfo } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';
import { composer, newConversation } from './unified-helpers';

/*
 * N12-B · Computer use, rebuilt. The real computer tool, service, routes,
 * the chat's step and the approval and question cards run against a fake
 * desktop behind a fake Cua Driver: Edge on HomeCover's site. Nothing touches
 * this computer's screen. Every state at desktop and phone, light and dark.
 */

const PHONE = { width: 390, height: 844 };
const DESKTOP = { width: 1280, height: 900 };

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

async function everywhere(
  page: Page,
  info: TestInfo,
  name: string,
  settle: () => Promise<void> = async () => {},
) {
  await expect(settle).toPass();
  await views(page, info, name);
  await page.setViewportSize(PHONE);
  await expect(settle).toPass();
  await views(page, info, `${name}-phone`);
  await page.setViewportSize(DESKTOP);
}

async function post(page: Page, path: string, data: object = {}) {
  const response = await page.request.post(path, {
    headers: fixtureHeaders(),
    data,
  });
  expect(response.ok()).toBe(true);
}

async function send(page: Page, text: string) {
  await composer(page).fill(text);
  await page.getByRole('button', { name: 'Send', exact: true }).click();
}

function transcript(page: Page) {
  return page.getByRole('log', { name: 'Conversation' });
}

/**
 * The turn's newest activity line, which says what the computer step is
 * doing; the steps it finished sit above it, under the words before them.
 */
function activity(page: Page) {
  return transcript(page)
    .getByRole('group', { name: /^(Tool )?[Aa]ctivity$/ })
    .locator('.activity-summary-text')
    .last();
}

test.beforeEach(async ({ page }) => post(page, '/__n12b_fixture/reset'));
// Other specs see computer use as it was: not set up (S3's screen recording reply needs that).
test.afterEach(async ({ page }) => post(page, '/__n12b_fixture/restore'));

test('a turn using Edge asks once, works live in the chat, then asks before paying', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  const conversation = await newConversation(page);
  await send(page, 'n12b claim fixture');

  const firstUse = page.getByRole('complementary', {
    name: 'Approval required: Let Row-Bot use Microsoft Edge in this chat?',
  });
  await expect(firstUse).toContainText(
    'Row-Bot will use Microsoft Edge with your mouse and keyboard while you watch.',
  );
  await expect(activity(page)).toHaveText('Waiting for your OK');
  await everywhere(page, info, 'computer-first-use', async () => {
    await firstUse.scrollIntoViewIfNeeded();
    await expect(
      firstUse.getByRole('button', { name: 'Approve', exact: true }),
    ).toBeEnabled();
  });
  await firstUse.getByRole('button', { name: 'Approve', exact: true }).click();

  const step = transcript(page).getByRole('region', {
    name: 'Using Microsoft Edge',
  });
  await expect(step).toContainText('Using Microsoft Edge');
  await expect(step).toContainText('HomeCover – Make a claim');
  await expect(step.getByRole('status')).toContainText('Clicked "Leak"');
  await expect(activity(page)).toHaveText('Using Microsoft Edge');
  await expect(
    step.getByRole('img', {
      name: 'The Microsoft Edge window Row-Bot is using',
    }),
  ).toBeVisible();
  await expect(step).toContainText(
    'Press Esc on the computer to stop it at once.',
  );
  await everywhere(page, info, 'computer-step-working', () =>
    step.scrollIntoViewIfNeeded(),
  );

  await post(page, '/__n12b_fixture/release', {
    conversation_id: conversation,
  });
  const pay = page.getByRole('complementary', {
    name: 'Approval required: Pay in Microsoft Edge?',
  });
  await expect(pay).toContainText(
    'Row-Bot will click "Confirm and pay £60" in Microsoft Edge',
  );
  await expect(
    pay.getByRole('img', {
      name: 'The Microsoft Edge window, with where Row-Bot will act',
    }),
  ).toBeVisible();
  await expect(step.getByRole('status')).toContainText('Waiting for your OK');
  await expect(activity(page)).toHaveText('Waiting for your OK');
  await everywhere(page, info, 'computer-pay-approval', () =>
    pay.scrollIntoViewIfNeeded(),
  );
  await pay.getByRole('button', { name: 'Approve', exact: true }).click();
  await expect(
    transcript(page).getByText(/Booked: a plumber comes on Tuesday/),
  ).toBeVisible();
});

test('using the computer yourself pauses Row-Bot, which asks to carry on', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  await newConversation(page);
  await send(page, 'n12b carry on fixture');

  const card = page.getByRole('complementary', {
    name: 'Row-Bot has a question',
  });
  await expect(card).toContainText(
    'You used the computer while Row-Bot was working in Microsoft Edge. Carry on?',
  );
  const step = transcript(page).getByRole('region', {
    name: 'Using Microsoft Edge',
  });
  await expect(step.getByRole('status')).toContainText(
    'Paused while you use it',
  );
  await expect(activity(page)).toHaveText('Paused while you use the computer');
  await everywhere(page, info, 'computer-carry-on', () =>
    card.scrollIntoViewIfNeeded(),
  );
  await card.getByRole('radio', { name: /Carry on/ }).check();
  await card.getByRole('button', { name: 'Answer' }).click();
  await expect(
    transcript(page).getByText(/Your claim form is open/),
  ).toBeVisible();
});

test('when computer use isn’t set up, the chat says so and how to fix it', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  await newConversation(page);
  await send(page, 'n12b not set up fixture');

  const problem = transcript(page).getByRole('alert').filter({
    hasText: "Computer use isn't set up yet: Cua Driver isn't installed.",
  });
  await expect(problem).toContainText('Set up… › Install.');
  await expect(
    problem.getByRole('link', { name: 'Open computer use settings' }),
  ).toBeVisible();
  await everywhere(page, info, 'computer-not-set-up', () =>
    problem.scrollIntoViewIfNeeded(),
  );
});

test('Settings › Browser and computer use, with computer use on', async ({
  page,
}, info) => {
  await post(page, '/__n12b_fixture/settings');
  await page.setViewportSize(DESKTOP);
  await page.goto('/app-v2/settings/system');
  const item = page.locator('[data-setting-anchor="browser-computer-use"]');
  await item.getByRole('button', { name: 'Set up…' }).click();
  const dialog = page.getByRole('dialog', { name: 'Browser and computer use' });
  await expect(dialog).toContainText(
    'Computer use works in apps on this computer, with your mouse and keyboard, while you watch.',
  );
  await expect(
    dialog.getByRole('switch', { name: 'Computer use' }),
  ).toBeChecked();
  await expect(
    dialog.getByRole('button', { name: 'Check setup' }),
  ).toBeVisible();
  await everywhere(page, info, 'settings-computer-use', () =>
    dialog
      .getByRole('button', { name: 'Install Cua Driver' })
      .scrollIntoViewIfNeeded(),
  );
});
