import type { Locator, Page, TestInfo } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';
import {
  blockFixtureServiceWorkers,
  composer,
  dismissNotices,
  newConversation,
} from './unified-helpers';

/*
 * N7 · Several X accounts, named on every post. Two accounts (@rowbot_ai and
 * @sidd) are signed in through a fake X API; nothing reaches X. The model's
 * x_post calls are scripted; the card is the real one for the call, and once
 * approved the real sender posts to the fake API, or loses X's answer and
 * finds the post among the account's latest instead of sending it again.
 */

const PHONE = { width: 390, height: 844 };
const DESKTOP = { width: 1280, height: 900 };

async function views(page: Page, info: TestInfo, name: string, focus: Locator) {
  await dismissNotices(page);
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await expect(page.locator('html')).toHaveAttribute(
      'data-theme',
      appearance,
    );
    await focus.scrollIntoViewIfNeeded();
    await assertNoOverflow(page);
    await screenshot(page, info, `${name}-${appearance}`);
  }
  await page.emulateMedia({ colorScheme: 'light' });
}

/** Evidence at desktop and phone size, light and dark. */
async function everywhere(
  page: Page,
  info: TestInfo,
  name: string,
  focus: Locator,
) {
  await views(page, info, name, focus);
  await page.setViewportSize(PHONE);
  await views(page, info, `${name}-phone`, focus);
  await page.setViewportSize(DESKTOP);
}

async function seed(page: Page, path: string) {
  const response = await page.request.post(path, {
    headers: fixtureHeaders(),
  });
  expect(response.ok(), await response.text()).toBe(true);
  return response.json();
}

async function send(page: Page, text: string) {
  await composer(page).fill(text);
  await page.getByRole('button', { name: 'Send', exact: true }).click();
}

function transcript(page: Page) {
  return page.getByRole('log', { name: 'Conversation', exact: true });
}

/** Open the turn's steps and the x_post step itself, to show the tool's own result. */
async function openResult(page: Page, text: RegExp) {
  const log = transcript(page);
  const result = log.getByText(text);
  // The steps render again once the turn settles, so a summary can be
  // replaced mid-click (B420): open whichever is closed until the result
  // shows, never toggling an open one shut.
  const open = async (summary: ReturnType<Page['locator']>) => {
    const details = summary.locator('xpath=..');
    if ((await details.getAttribute('open', { timeout: 2_000 })) === null)
      await summary.click({ timeout: 2_000 });
  };
  await expect(async () => {
    await open(log.locator('summary.activity-summary').last());
    await open(
      log.locator('details summary').filter({ hasText: /on X/ }).last(),
    );
    await expect(result).toBeVisible({ timeout: 2_000 });
  }).toPass({ timeout: 30_000 });
  return result;
}

test.beforeEach(async ({ context, page }) => {
  await blockFixtureServiceWorkers(context);
  await page.setViewportSize(DESKTOP);
  expect(await seed(page, '/__n7_fixture/accounts')).toEqual({
    accounts: ['@rowbot_ai', '@sidd'],
  });
});
test.afterEach(async ({ page }) => {
  await seed(page, '/__n7_fixture/reset');
});

test('Settings › Apps › X lists each account with its handle, name and ID, and adds another', async ({
  page,
}, info) => {
  await page.goto('/app-v2/settings/apps/x');
  const article = page.getByRole('article', { name: 'X account' });
  await article.getByRole('link', { name: 'Open', exact: true }).click();
  const list = page.getByRole('list', { name: 'X accounts' });
  await expect(list.getByRole('listitem')).toHaveCount(2);
  await expect(list.getByRole('listitem').first()).toContainText(
    '@rowbot_ai · Row-Bot',
  );
  await expect(list.getByRole('listitem').first()).toContainText('ID 111');
  await expect(list.getByRole('listitem').last()).toContainText('@sidd · Sidd');
  await expect(
    list.getByRole('button', { name: 'Remove @sidd' }),
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Add another account' }),
  ).toBeVisible();
  await everywhere(page, info, 'x-accounts-settings', list);
});

test('the post card names the account and shows the whole text and the picture; the result links the post', async ({
  page,
}, info) => {
  await newConversation(page);
  await send(page, 'n7 post fixture');
  const card = page.getByRole('complementary', {
    name: 'Approval required: Post to X as @rowbot_ai?',
  });
  await expect(card.getByText('Post to X as @rowbot_ai?')).toBeVisible();
  await expect(card.getByText('ID 111')).toBeVisible();
  await expect(card.getByText(/Row-Bot 5\.3 is out\./)).toBeVisible();
  await expect(card.getByText(/row-bot\.ai\/5\.3/)).toBeVisible();
  await expect(
    card.getByRole('img', { name: 'Preview of launch-card.png' }),
  ).toBeVisible();
  await expect(card.getByText('launch-card.png')).toBeVisible();
  await expect(card.getByText(/^[0-9a-f]{12}$/)).toBeVisible();
  await everywhere(page, info, 'x-post-card', card);

  await card.getByRole('button', { name: 'Approve', exact: true }).click();
  const reply = transcript(page).getByText(
    /^Done\. It's live on @rowbot_ai: https:\/\/x\.com\/rowbot_ai\/status\/\d+$/,
  );
  await expect(reply).toBeVisible();
  await expect(card).toHaveCount(0);
  const result = await openResult(
    page,
    /Post ID: \d+ · Account: @rowbot_ai \(ID 111\) · Time: \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ/,
  );
  await everywhere(page, info, 'x-post-result', result);
});

test('the post is edited on its card: the text against the account’s limit, a picture taken off, then the edited post asks again (G2)', async ({
  page,
}, info) => {
  await newConversation(page);
  await send(page, 'n7 post fixture');
  const card = page.getByRole('complementary', {
    name: 'Approval required: Post to X as @rowbot_ai?',
  });
  await expect(card.getByText(/Row-Bot 5\.3 is out\./)).toBeVisible();
  await card.getByRole('button', { name: 'Edit the post' }).click();
  const text = card.getByRole('textbox', {
    name: 'Edit the post as @rowbot_ai',
  });
  await expect(text).toBeFocused();
  await expect(card.getByText(/^\d+ of 280$/)).toBeVisible();
  await expect(
    card.getByRole('button', { name: 'Approve', exact: true }),
  ).toBeHidden();
  await everywhere(page, info, 'x-post-edit', card);

  await text.fill(
    'Row-Bot 5.3.1 is out: edit a post right on its card before it goes to X.',
  );
  await card.getByRole('button', { name: 'Remove launch-card.png' }).click();
  await expect(card.getByText('72 of 280')).toBeVisible();
  await card.getByRole('button', { name: 'Save edits' }).click();
  const edited = page.getByRole('complementary', {
    name: 'Approval required: Post to X as @rowbot_ai?',
  });
  // The new card, loaded: the edited text as the post's own, no picture.
  await expect(
    edited.getByRole('button', { name: 'Approve', exact: true }),
  ).toBeEnabled();
  await expect(edited.locator('.x-post-text')).toHaveText(
    'Row-Bot 5.3.1 is out: edit a post right on its card before it goes to X.',
  );
  await expect(edited.getByText('launch-card.png')).toHaveCount(0);
  await everywhere(page, info, 'x-post-edited', edited);

  await edited.getByRole('button', { name: 'Approve', exact: true }).click();
  await expect(
    transcript(page).getByText(
      /^Done\. It's live on @rowbot_ai: https:\/\/x\.com\/rowbot_ai\/status\/\d+$/,
    ),
  ).toBeVisible();
  await expect(
    await openResult(
      page,
      /Row-Bot 5\.3\.1 is out: edit a post right on its card/,
    ),
  ).toBeVisible();
});

test('a scheduled post that didn’t go out stays in Review with why, and goes without its picture in one click', async ({
  page,
}, info) => {
  const { sent } = await seed(page, '/__n7_fixture/failed-post');
  expect(sent).toHaveLength(1);
  expect(sent[0].status).toBe('failed');
  await page.goto('/app-v2/?tab=overview');
  await dismissNotices(page);
  const notSent = page.getByRole('list', { name: "Posts that didn't go out" });
  const row = notSent.getByRole('listitem');
  await expect(row).toContainText('· @rowbot_ai');
  await expect(row).toContainText(
    'was connected before Row-Bot could post pictures and videos. Sign in to it once more in Settings › Apps › X',
  );
  await expect(
    row.getByRole('button', { name: 'Try again now' }),
  ).toBeVisible();
  await everywhere(page, info, 'review-not-sent', notSent);

  await row
    .getByRole('button', { name: 'Post it now without the picture' })
    .click();
  await expect(notSent).toHaveCount(0);
  await expect(page.getByText(/Posted on X as @rowbot_ai/).first()).toBeVisible(
    { timeout: 15_000 },
  );
});

test('Home › Review shows the waiting post in full with its time; approving schedules it and Cancel stops it (G3)', async ({
  page,
}, info) => {
  await newConversation(page);
  await send(page, 'n7 later post fixture');
  const inChat = page.getByRole('complementary', {
    name: 'Approval required: Post to X as @rowbot_ai?',
  });
  await expect(inChat.getByText(/^Goes out at/)).toBeVisible();
  await page.goto('/app-v2/?tab=overview');
  await dismissNotices(page);
  await expect(
    page.getByRole('button', { name: 'Go to Review' }),
  ).toContainText('1 post to review');
  const review = page.getByRole('list', { name: 'Waiting to go out' });
  const card = review.getByRole('complementary', {
    name: 'Approval required: Post to X as @rowbot_ai?',
  });
  await expect(card.getByText(/Row-Bot 5\.3 is out\./)).toBeVisible();
  await expect(card.getByText('launch-card.png')).toBeVisible();
  await expect(card.getByText(/^Goes out at/)).toBeVisible();
  await expect(
    card.getByRole('button', { name: 'Approve for then', exact: true }),
  ).toBeEnabled();
  await everywhere(page, info, 'review-queue', card);

  info.annotations.push({
    type: 'expected-console-error',
    description: JSON.stringify({
      signature:
        'Failed to load resource: the server responded with a status of 409 (Conflict)',
      count: 1,
      upTo: true,
      owner: 'x-accounts later-post fixture',
      fixture:
        'The chat that asked stays open behind Home with its own card: approving here moves its turn on, and a card it shows again reads the answered approval, which answers 409 and says it was answered',
    }),
  });
  await card
    .getByRole('button', { name: 'Approve for then', exact: true })
    .click();
  const scheduled = page.getByRole('list', { name: 'Scheduled posts' });
  await expect(
    scheduled.getByText(/^Today, .* · @rowbot_ai$|, .* · @rowbot_ai$/),
  ).toBeVisible();
  await expect(review).toHaveCount(0);
  await everywhere(page, info, 'review-scheduled', scheduled);

  // Edit before it goes out: the text and which files go; it keeps its time.
  await scheduled.getByRole('button', { name: 'Edit this post' }).click();
  const box = scheduled.getByRole('textbox', {
    name: 'Edit the post as @rowbot_ai',
  });
  await box.fill('Row-Bot 5.3 is out. Edited before it goes out.');
  await everywhere(page, info, 'review-scheduled-edit', scheduled);
  await scheduled.getByRole('button', { name: 'Save edits' }).click();
  await expect(box).toHaveCount(0);
  await expect(
    scheduled.getByText('Row-Bot 5.3 is out. Edited before it goes out.'),
  ).toBeVisible();
  await page.reload();
  await expect(
    page
      .getByRole('list', { name: 'Scheduled posts' })
      .getByText('Row-Bot 5.3 is out. Edited before it goes out.'),
  ).toBeVisible();

  await scheduled.getByRole('button', { name: 'Cancel this post' }).click();
  await expect(scheduled).toHaveCount(0);
});

test('a reply card links the post it answers', async ({ page }, info) => {
  await newConversation(page);
  await send(page, 'n7 reply fixture');
  const card = page.getByRole('complementary', {
    name: 'Approval required: Reply on X as @rowbot_ai?',
  });
  await expect(
    card.getByRole('link', { name: /^x\.com\/i\/status\/\d+$/ }),
  ).toBeVisible();
  await expect(card.getByText(/^Thanks! It keeps everything/)).toBeVisible();
  await everywhere(page, info, 'x-reply-card', card);
  await card.getByRole('button', { name: 'Deny', exact: true }).click();
  await expect(card).toHaveCount(0);
  await expect(
    transcript(page).getByText('You said no, so Row-Bot stopped there.', {
      exact: false,
    }),
  ).toBeVisible();
});

test('when X does not confirm a post, Row-Bot finds it among the account’s latest instead of sending it again', async ({
  page,
}, info) => {
  await newConversation(page);
  await send(page, 'n7 unsure fixture');
  const card = page.getByRole('complementary', {
    name: 'Approval required: Post to X as @rowbot_ai?',
  });
  await card.getByRole('button', { name: 'Approve', exact: true }).click();
  const reply = transcript(page).getByText(
    /^X didn't confirm it at first, so I checked @rowbot_ai's latest posts: it went out once\./,
  );
  await expect(reply).toBeVisible();
  const result = await openResult(
    page,
    /Row-Bot read @rowbot_ai's latest posts and found it\./,
  );
  await everywhere(page, info, 'x-post-unsure-found', result);
});

test('with two accounts, a post that names none is refused with both handles', async ({
  page,
}, info) => {
  await newConversation(page);
  await send(page, 'n7 which fixture');
  const reply = transcript(page).getByText(
    /^Which X account should I post this from: @rowbot_ai \(Row-Bot\) or @sidd \(Sidd\)\?/,
  );
  await expect(reply).toBeVisible();
  const refusal = await openResult(
    page,
    /^Which X account\? @rowbot_ai or @sidd\./,
  );
  await expect(refusal).toContainText('Nothing was sent');
  await everywhere(page, info, 'x-which-account', reply);
});
