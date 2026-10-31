import type { Page } from '@playwright/test';
import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';
import { fixtureState, releaseProducer } from './unified-helpers';

// U3, U4, U10: a Row-Bot that a known host runs (ROW_BOT_HOSTED_BY). Its
// first run is two big buttons; one click on ChatGPT starts the device-code
// sign-in (the fixture's synthetic one, approved by itself), copies the code
// and opens OpenAI's page; then a new chat suggests one first message, and
// after the answer a card offers to add Row-Bot to a phone. The Setup Center
// adds the phone and a backup. Nothing reaches OpenAI, a runtime or the
// network: OpenAI's page is answered here.

async function fixture(page: Page, path: string) {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated browser runner');
  const response = await page.request.post(path, {
    headers: { 'X-Fixture-Token': token, Origin: new URL(base).origin },
  });
  expect(response.ok(), await response.text()).toBe(true);
}

test.afterEach(async ({ page }) => {
  await fixture(page, '/__a6_fixture/hosted/off');
  await fixture(page, '/__p10_fixture/first-run/restore');
});

test('hosted: ChatGPT in one click, straight into a chat with one suggestion, then the phone card', async ({
  page,
  context,
  isMobile,
  browserName,
}, testInfo) => {
  test.skip(
    browserName !== 'chromium',
    'Clipboard permissions are granted in Chromium only',
  );
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await context.route('https://example.invalid/**', (route) =>
    route.fulfill({
      contentType: 'text/html',
      body: '<title>OpenAI sign-in</title>',
    }),
  );
  await fixture(page, '/__p4_fixture/subscriptions');
  await fixture(page, '/__a6_fixture/hosted/on');
  await fixture(page, '/__p10_fixture/first-run/fresh');
  await page.goto('/app-v2/');
  await expect(page).toHaveURL(/\/app-v2\/setup$/);
  const choices = page.getByRole('group', {
    name: 'How should Row-Bot think?',
  });
  await expect(choices.getByRole('button')).toHaveText([
    'Sign in with ChatGPT',
    'Use my Claude plan',
  ]);
  await expect(
    page.getByText(/Running models on this server needs a GPU/),
  ).toBeVisible();
  await expect(page.getByText('On this computer')).toHaveCount(0);
  await screenshot(page, testInfo, 'hosted-first-run');
  await accessibility(page, testInfo, 'hosted-first-run-axe');
  await assertNoOverflow(page);
  await page.getByText('Other ways').click();
  await expect(
    page.getByRole('link', { name: /Use my own model server/ }),
  ).toHaveAttribute(
    'href',
    '/app-v2/settings/providers?add=custom-endpoint&type=ollama&from=setup',
  );
  await screenshot(page, testInfo, 'hosted-other-ways');

  // One click: the sign-in starts, the code is copied, OpenAI's page opens.
  const opened = page.waitForEvent('popup');
  await choices.getByRole('button', { name: 'Sign in with ChatGPT' }).click();
  const signIn = await opened;
  await expect(signIn).toHaveURL('https://example.invalid/device');
  await signIn.close();
  await expect(
    page.getByText(/Row-Bot notices when you’re done/),
  ).toBeVisible();
  await expect(page.getByText('SYNTHETIC', { exact: true })).toBeVisible();
  await screenshot(page, testInfo, 'hosted-waiting-for-sign-in');
  await assertNoOverflow(page);

  // The synthetic code is approved by itself: Row-Bot notices, starts on
  // ChatGPT's own pick, checks it and opens a new chat.
  // The chat's own suggestion, not a past chat of that name in the sidebar.
  const first = page.locator('.chat-empty-first-message');
  await expect(first).toBeVisible({ timeout: 30_000 });
  await expect(page).toHaveURL(/\/app-v2\/conversations\/[^/?]+/);
  await screenshot(page, testInfo, 'hosted-first-chat');
  await assertNoOverflow(page);
  const conversation = new URL(page.url()).pathname.split('/').at(-1);
  await first.click();
  // This chat's turn, not one an earlier project left in the shared backend.
  const turn = async () =>
    (await fixtureState(page)).calls.find(
      (call) => call.conversation_id === conversation && !call.quiesced,
    );
  await expect.poll(async () => Boolean(await turn())).toBe(true);
  await releaseProducer(page, (await turn())!);
  await expect(page.getByText('Synthetic stream settled.')).toBeVisible();

  // After the first answer: Add Row-Bot to your phone.
  const card = page.getByRole('region', { name: 'Add Row-Bot to your phone' });
  await expect(card).toBeVisible();
  if (isMobile) await expect(card.getByRole('listitem').first()).toBeVisible();
  else {
    await card.getByRole('button', { name: 'Show the code' }).click();
    // The fixture serves itself on a loopback address, which no phone reaches.
    await expect(card).toContainText('Settings › Devices & remote access');
  }
  await screenshot(page, testInfo, 'hosted-phone-card');
  await assertNoOverflow(page);
  const saved = page.waitForResponse(
    (response) =>
      response.url().includes('/setup/onboarding/commands') &&
      response.request().method() === 'POST',
  );
  await card
    .getByRole('button', {
      name: 'Hide this. You can add your phone later in Settings › Devices & remote access.',
    })
    .click();
  await expect(card).toHaveCount(0);
  expect((await saved).ok()).toBe(true);
  await page.reload();
  await expect(page.getByText('Synthetic stream settled.')).toBeVisible();
  await expect(
    page.getByRole('region', { name: 'Add Row-Bot to your phone' }),
  ).toHaveCount(0);
});

test('hosted: the Setup Center adds the phone and a backup, and says where local AI comes from', async ({
  page,
}, testInfo) => {
  await fixture(page, '/__a6_fixture/hosted/on');
  await page.goto('/app-v2/setup');
  const checklist = page.getByRole('region', { name: 'Setup checklist' });
  await expect(
    checklist.getByRole('link', { name: 'Connect a phone' }),
  ).toHaveAttribute('href', '/app-v2/settings/access#connect');
  await expect(
    checklist.getByRole('link', { name: 'Download a backup' }),
  ).toHaveAttribute('href', '/app-v2/settings/data#backup');
  await expect(
    page.getByText(
      'Local/private AI: Use your own model server, or Row-Bot on your own computer.',
    ),
  ).toBeVisible();
  await screenshot(page, testInfo, 'hosted-setup-center');
  await assertNoOverflow(page);
});
