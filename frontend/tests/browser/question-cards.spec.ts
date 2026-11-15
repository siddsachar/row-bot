import type { Locator, Page, TestInfo } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';
import { composer, dismissNotices, newConversation } from './unified-helpers';

/*
 * N4 · Row-Bot asks questions with options, in cards. The model's call is
 * scripted; the card, the answer, the resumed turn and the collapsed answer
 * run through the real server: ask, answer (an option, Other, several as
 * steps), collapsed. A helper's question shows in the chat that started it,
 * and an unattended workflow's in Needs you. The seeded helper and workflow
 * are records: nothing runs.
 */

const PHONE = { width: 390, height: 844 };
const DESKTOP = { width: 1280, height: 900 };

async function views(page: Page, info: TestInfo, name: string) {
  await dismissNotices(page);
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

/** Evidence at desktop and phone size, light and dark. */
async function everywhere(page: Page, info: TestInfo, name: string) {
  await views(page, info, name);
  await page.setViewportSize(PHONE);
  await views(page, info, `${name}-phone`);
  await page.setViewportSize(DESKTOP);
}

async function ask(page: Page, text: string): Promise<Locator> {
  await composer(page).fill(text);
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  const card = page.getByRole('complementary', {
    name: 'Row-Bot has a question',
  });
  await expect(card.getByRole('button', { name: 'Skip' })).toBeEnabled();
  return card;
}

async function reset(page: Page) {
  const done = await page.request.post('/__n4_fixture/reset', {
    headers: fixtureHeaders(),
  });
  expect(done.ok()).toBe(true);
}

test.afterEach(async ({ page }) => reset(page));

test('Row-Bot asks with options, the answer goes on, and the card collapses to the choice', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  await newConversation(page);
  const card = await ask(page, 'n4 question fixture');
  const group = card.getByRole('group', {
    name: 'When should the beta go out?',
  });
  await expect(card.getByText('Beta timing', { exact: true })).toBeVisible();
  await expect(group.getByRole('radio')).toHaveCount(4);
  await expect(
    group.getByRole('radio', { name: /Beta\.1 now/ }),
  ).toHaveAccessibleDescription('Ship the current build to testers today.');
  await expect(group.getByText('Recommended', { exact: true })).toBeVisible();
  await expect(card.getByRole('button', { name: 'Answer' })).toBeDisabled();
  await everywhere(page, info, 'question-card');

  // Other… opens a text field for the person's own words.
  await group.getByRole('radio', { name: 'Other…' }).check();
  const own = card.getByRole('textbox', { name: 'Your own answer' });
  await expect(own).toBeFocused();
  await own.fill('Only the plans pages for now');
  await everywhere(page, info, 'question-card-other');

  // 1–4 picks from the keyboard (here, the recommended one); Enter answers.
  await own.blur();
  await page.keyboard.press('1');
  await expect(group.getByRole('radio', { name: /Beta\.1 now/ })).toBeChecked();
  await page.keyboard.press('Enter');

  const transcript = page.getByRole('log', { name: 'Conversation' });
  const reply = transcript.getByText("Thanks. I'll plan the beta that way.", {
    exact: true,
  });
  await expect(reply).toBeVisible();
  await expect(card).toHaveCount(0);
  const answered = transcript.getByRole('group', {
    name: 'You chose: Beta.1 now',
  });
  await expect(answered).toBeVisible();
  // In the past tense, and in order: the question, your answer, then the reply.
  await expect(transcript.getByText('Waiting for your answer')).toHaveCount(0);
  await expect(
    transcript.getByText('Asked you a question').first(),
  ).toBeVisible();
  // The live row settles into the saved one meanwhile: read both once they stand still.
  await expect
    .poll(async () => {
      const [chosenAt, replyAt] = await Promise.all([
        answered.boundingBox(),
        reply.boundingBox(),
      ]);
      return chosenAt && replyAt ? chosenAt.y < replyAt.y : null;
    })
    .toBe(true);
  await everywhere(page, info, 'question-answered');
});

test('a message typed while a question waits answers it in those words, and the turn goes on', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  await newConversation(page);
  const card = await ask(page, 'n4 question fixture');
  await composer(page).fill('Next week, after the nightly');
  await everywhere(page, info, 'question-typed-answer');
  await composer(page).press('Enter');
  const transcript = page.getByRole('log', { name: 'Conversation' });
  await expect(
    transcript.getByText("Thanks. I'll plan the beta that way.", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(card).toHaveCount(0);
  await expect(
    transcript.getByRole('group', { name: /Next week, after the nightly/ }),
  ).toBeVisible();
  await expect(composer(page)).toHaveValue('');
});

test('several questions are steps in one card, with checkboxes where several may be picked', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  await newConversation(page);
  const card = await ask(page, 'n4 steps fixture');
  await expect(
    card.getByRole('list', { name: 'Question 1 of 2' }),
  ).toBeVisible();
  await card.getByRole('radio', { name: /After the fixes/ }).check();
  await card.getByRole('button', { name: 'Next' }).click();
  const pages = card.getByRole('group', {
    name: 'Which pages go in the beta?',
  });
  await pages.getByRole('checkbox', { name: /Plans/ }).check();
  await pages.getByRole('checkbox', { name: /Docs/ }).check();
  await expect(card.getByRole('button', { name: 'Answer' })).toBeEnabled();
  await everywhere(page, info, 'question-card-multi-select');

  await card.getByRole('button', { name: 'Answer' }).click();
  await expect(
    page.getByRole('log', { name: 'Conversation' }).getByRole('group', {
      name: 'You chose: Beta timing: After the fixes · Pages: Plans, Docs',
    }),
  ).toBeVisible();
});

test('skipping lets Row-Bot go on with its recommendation', async ({
  page,
}) => {
  await page.setViewportSize(DESKTOP);
  await newConversation(page);
  const card = await ask(page, 'n4 question fixture');
  await card.getByRole('button', { name: 'Skip' }).click();
  await expect(
    page
      .getByRole('log', { name: 'Conversation' })
      .getByRole('group', { name: 'You skipped the question' }),
  ).toBeVisible();
});

test('a helper’s question shows in the chat that started it', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  const conversation = await newConversation(page);
  const seeded = await page.request.post('/__n4_fixture/helper', {
    headers: fixtureHeaders(),
    data: { conversation_id: conversation },
  });
  expect(seeded.ok()).toBe(true);
  await page.reload();
  const notice = page.getByRole('group', {
    name: 'Launch planner has a question',
  });
  await expect(notice).toContainText('Launch planner has a question.');
  await notice.scrollIntoViewIfNeeded();
  await expect(
    notice
      .getByRole('complementary', { name: 'Row-Bot has a question' })
      .getByRole('button', { name: 'Skip' }),
  ).toBeEnabled();
  await everywhere(page, info, 'question-helper');
});

test('a workflow’s question waits in Needs you and is answered there', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  const seeded = await page.request.post('/__n4_fixture/workflow', {
    headers: fixtureHeaders(),
  });
  expect(seeded.ok()).toBe(true);
  await page.goto('/app-v2/');
  const needs = page.getByRole('list', { name: 'Needs you' });
  // The run asks in the workflow's conversation, named with its ⚡ (F31).
  const answer = needs.getByRole('button', {
    name: /^Answer: \W*Weekly launch check$/,
  });
  await expect(answer).toBeVisible();
  await everywhere(page, info, 'question-needs-you');

  await answer.click();
  const dialog = page.getByRole('dialog', { name: 'Row-Bot has a question' });
  await dialog.getByRole('radio', { name: /Next week/ }).check();
  await views(page, info, 'question-needs-you-dialog');
  await dialog.getByRole('button', { name: 'Answer' }).click();
  await expect(dialog).toHaveCount(0);
  await expect(answer).toHaveCount(0);
});
