import type { Page } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';
import {
  blockFixtureServiceWorkers,
  composer,
  newConversation,
} from './unified-helpers';

/*
 * App views in chat (MCP Apps, Phase 6): a tool step whose app declares a view
 * shows it in its own sandboxed frame, which can't reach Row-Bot's page,
 * cookies, storage or API; a call from the view asks first, on the standard
 * approval card, and the answer reaches the view. The app is a local fake
 * serving the fixture counter's own view; the model is scripted.
 */

function headers() {
  const origin = new URL(process.env.ROW_BOT_BROWSER_BASE_URL!).origin;
  return {
    'X-Fixture-Token': process.env.ROW_BOT_BROWSER_CONTROL_TOKEN!,
    'X-Fixture-Origin': origin,
    Origin: origin,
  };
}

/** The view's own probe of Row-Bot's API, refused by the view's policy: the browser says so, twice. */
function expectBlockedProbe() {
  const api = `${new URL(process.env.ROW_BOT_BROWSER_BASE_URL!).origin}/api/v1/handshake`;
  for (const signature of [
    `Connecting to '${api}' violates the following Content Security Policy directive: "connect-src 'none'". The action has been blocked.`,
    `Fetch API cannot load ${api}. Refused to connect because it violates the document's Content Security Policy.`,
  ])
    test.info().annotations.push({
      type: 'expected-console-error',
      description: JSON.stringify({
        owner: 'app-views',
        fixture: 'counter view isolation probe',
        signature,
        count: 1,
        upTo: true,
      }),
    });
}

async function seed(page: Page, path: string) {
  const response = await page.request.post(path, { headers: headers() });
  expect(response.ok(), await response.text()).toBe(true);
}

// The fixture app leaves with the journey: later ones (removing the last app, say) find Apps as they expect.
test.afterEach(async ({ page }) => {
  await seed(page, '/__p6_fixture/views/remove');
});

test('an app view shows in its own sandbox, never reaches Row-Bot, and asks before it changes anything', async ({
  page,
  context,
}, testInfo) => {
  test.slow();
  expectBlockedProbe();
  await seed(page, '/__p6_fixture/views');
  await blockFixtureServiceWorkers(context);
  await newConversation(page);
  await composer(page).fill('Show my counter app view fixture');
  await page.getByRole('button', { name: 'Send', exact: true }).click();

  const view = page.getByRole('region', { name: 'Counter · Counter view' });
  await expect(view).toBeVisible();
  // Scripts only: no same origin, forms, pop-ups, downloads or top navigation.
  await expect(view.locator('iframe')).toHaveAttribute(
    'sandbox',
    'allow-scripts',
  );
  const frame = page.frameLocator('.app-view iframe');
  await expect(frame.locator('#count')).toHaveText('3');
  const isolation = frame.locator('#isolation');
  for (const line of [
    'cookies: blocked',
    'storage: blocked',
    'page: blocked',
    'row-bot: blocked',
  ])
    await expect(isolation).toContainText(line);
  await assertNoOverflow(page);
  await screenshot(page, testInfo, 'app-view');

  // A change from the view waits for the person, on the standard card.
  await frame.getByRole('button', { name: 'Add one' }).click();
  const approval = view.getByRole('complementary', {
    name: /Approval required for .* in Counter$/,
  });
  await expect(approval).toContainText('Allow Counter to increment?');
  await expect(frame.locator('#count')).toHaveText('3');
  await screenshot(page, testInfo, 'app-view-approval');
  await approval.getByRole('button', { name: 'Approve' }).click();
  await expect(frame.locator('#count')).toHaveText('4');
  await expect(approval).toBeHidden();
});
