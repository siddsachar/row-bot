import type { BrowserContext, Page } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';
import {
  blockFixtureServiceWorkers,
  composer,
  fixtureState,
  newConversation,
} from './unified-helpers';

/*
 * Apps in chat (Phase 5): the agent suggests an app from the local catalog and
 * the person connects it in the chat, then Continue goes on; a tool step and
 * its approval name the app; @ focuses a message on it, + → Apps switches it
 * off for one chat, and an app that needs a sign-in waits in Home's Needs you.
 * Every service is a local fake; the model is scripted.
 */

function headers() {
  const origin = new URL(process.env.ROW_BOT_BROWSER_BASE_URL!).origin;
  return {
    'X-Fixture-Token': process.env.ROW_BOT_BROWSER_CONTROL_TOKEN!,
    'X-Fixture-Origin': origin,
    Origin: origin,
  };
}

async function seed(page: Page, path: string) {
  const response = await page.request.post(path, { headers: headers() });
  expect(response.ok()).toBe(true);
}

async function send(page: Page, text: string) {
  await composer(page).fill(text);
  await page.getByRole('button', { name: 'Send', exact: true }).click();
}

/** Sign in on the synthetic provider's page, which opens in its own tab. */
async function approveSignIn(
  context: BrowserContext,
  start: () => Promise<void>,
) {
  const signIn = context.waitForEvent('page');
  await start();
  const page = await signIn;
  await page.getByRole('link', { name: 'Allow' }).click();
  await expect(
    page.getByText('Signed in to the synthetic service.'),
  ).toBeVisible();
  await page.close();
}

async function lastCall(page: Page) {
  const state = await fixtureState(page);
  return state.calls.at(-1) as unknown as {
    case: string;
    app_scope?: { focus: string[]; exclude_servers: string[] } | null;
  };
}

test('an app found in the chat connects there, names itself on its steps, and follows the chat', async ({
  page,
  context,
}, testInfo) => {
  test.slow();
  await seed(page, '/__p5_fixture/apps');
  await blockFixtureServiceWorkers(context);
  await newConversation(page);

  // The agent suggests an app from the local catalog: the card shows it as the catalog does.
  await send(page, 'Find my meeting notes connect fixture Granola');
  const card = page.getByRole('group', { name: 'Apps to connect' });
  const granola = card.getByRole('listitem', { name: 'Granola', exact: true });
  await expect(granola).toContainText('by Granola');
  await assertNoOverflow(page);
  await screenshot(page, testInfo, 'chat-connect-card');

  // Connecting is the app's own consent sheet and plan, right in the chat.
  await granola.getByRole('button', { name: 'Connect', exact: true }).click();
  const consent = page.getByRole('dialog', { name: 'Connect Granola' });
  await expect(consent).toContainText('What you ask goes to mcp.granola.ai.');
  await approveSignIn(context, () =>
    consent.getByRole('button', { name: 'Connect', exact: true }).click(),
  );
  const access = page.getByRole('dialog', {
    name: "Here's what Granola can do",
  });
  await access.getByRole('button', { name: 'Allow', exact: true }).click();
  const ready = card.getByRole('listitem', { name: 'Granola, Ready' });
  await expect(ready).toBeVisible();
  await screenshot(page, testInfo, 'chat-connect-ready');

  // Continue is the person's own message, in fixed words (an app's name can come from a third-party listing).
  await ready.getByRole('button', { name: 'Continue', exact: true }).click();
  await expect(
    page
      .getByRole('article', { name: 'Row-Bot message' })
      .getByText('Continuing with what you asked.', { exact: true })
      .first(),
  ).toBeVisible();

  // A step through the app shows its logo and name, and its change asks first, named too.
  await send(page, 'app tool fixture Granola');
  const approval = page.getByRole('complementary', {
    name: /Approval required for .* in Granola$/,
  });
  await expect(approval).toBeVisible();
  await expect(approval.locator('.app-icon')).toBeVisible();
  await page
    .getByText(/Used \d+ tools?/)
    .first()
    .click();
  await expect(page.locator('.activity-step-app').first()).toContainText(
    'Granola',
  );
  await screenshot(page, testInfo, 'chat-app-steps');
  await approval.getByRole('button', { name: 'Deny', exact: true }).click();
  await expect(
    page
      .getByText('The requested action was denied. No action was taken.')
      .first(),
  ).toBeVisible();

  // @ lists the chat's apps; choosing one keeps the mention in the message.
  await composer(page).fill('Look this up in @Gra');
  const mentions = page.getByRole('listbox', { name: 'Mentions' });
  const mention = mentions
    .getByRole('group', { name: 'Apps' })
    .getByRole('option', { name: /^Granola/ });
  await expect(mention).toBeVisible();
  await screenshot(page, testInfo, 'chat-mention');
  await mention.click();
  await expect(composer(page)).toHaveValue('Look this up in @Granola ');

  // + → Apps switches it off for this chat only; a turn then leaves it out.
  await page.getByRole('button', { name: 'Add files and more' }).click();
  await page.getByRole('menuitem', { name: /^Apps/ }).click();
  const apps = page.getByRole('menu', { name: /^Apps/ });
  const toggle = apps.getByRole('menuitemcheckbox', { name: 'Granola' });
  await expect(toggle).toHaveAttribute('aria-checked', 'true');
  await expect(
    apps.getByRole('menuitem', { name: 'Find more apps' }),
  ).toBeVisible();
  await screenshot(page, testInfo, 'chat-apps-menu');
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-checked', 'false');
  await page.keyboard.press('Escape');
  await page.keyboard.press('Escape');
  await send(page, 'Granola is connected now');
  await expect
    .poll(async () => (await lastCall(page)).app_scope?.exclude_servers ?? [])
    .toHaveLength(1);

  // Signed out by the service: Home's Needs you offers its fix, one click away.
  await seed(page, '/__p5_fixture/apps/sign-out?name=granola');
  await page.goto('/app-v2/');
  const needs = page.getByRole('list', { name: 'Needs you' });
  await expect(needs.getByText('Sign in to Granola')).toBeVisible();
  await screenshot(page, testInfo, 'home-needs-you');
  await needs.getByRole('button', { name: 'Sign in again' }).click();
  await expect(page).toHaveURL(/\/settings\/apps\/item\?id=/);
  await expect(
    page.getByRole('dialog', { name: /Sign in again|Fix Granola/ }),
  ).toBeVisible();
});

test('accounts, channels and key tools are apps, set up in their own scoped settings', async ({
  page,
  context,
}, testInfo) => {
  await blockFixtureServiceWorkers(context);
  for (const [app, heading, editor] of [
    ['google', 'Google', 'How to set up Google'],
    ['telegram', 'Telegram', 'Telegram'],
    ['tavily', 'Tavily', 'Web Search'],
  ] as const) {
    await page.goto(`/app-v2/settings/apps/${app}`);
    await expect(
      page.getByRole('heading', { name: heading, exact: true, level: 3 }),
    ).toBeVisible();
    const ways = page.getByRole('region', { name: 'Ways to connect' });
    if (app === 'tavily') {
      // Tavily's hosted server is a way too; the page leads with the recommended one, its built-in
      // web search tool, and links to the others.
      await expect(ways.getByRole('listitem').first()).toContainText(
        /Web search.*Built in.*Recommended.*This one/,
      );
      await expect(
        ways.getByRole('link', { name: /Tavily MCP Hosted/ }),
      ).toBeVisible();
      await screenshot(page, testInfo, `app-${app}`);
    }
    await expect(page.getByText(/Built in/).first()).toBeVisible();
    if (app !== 'tavily') await screenshot(page, testInfo, `app-${app}`);
    await page.getByRole('button', { name: 'Set up', exact: true }).click();
    await expect(page).toHaveURL(/edit=1/);
    // Its owner's own page, scoped to it alone.
    await expect(
      app === 'telegram'
        ? page.locator('details[open]').filter({ hasText: 'Telegram' })
        : page.getByText(editor).first(),
    ).toBeVisible();
    await screenshot(page, testInfo, `app-${app}-settings`);
  }
  // The old pages open the apps.
  await page.goto('/app-v2/settings/channels#telegram');
  await expect(page).toHaveURL(/\/settings\/apps\/telegram$/);
  await page.goto('/app-v2/settings/accounts#google');
  await expect(page).toHaveURL(/\/settings\/apps\/google$/);
});
