import type { Page, Request } from '@playwright/test';
import { expect, test } from './evidence';

// A1: a sign-in link, /setup#code=…, signs in with one click. The fixture is a
// desktop, which has no setup page, so each test serves the real page (its
// markup, script and headers, from the fixture) at /setup and answers the
// page's own requests. The code leaves the address bar and history at once,
// whatever comes of it, and reaches only the page's POST, as a typed one does.

const CODE = 'ABCD-EFGH-JKMN';
const WRONG = "That code doesn't match. Check it and try again.";
const USED =
  "This code has already been used or has expired. To make a new one, set a new ROW_BOT_SETUP_CODE in your service's Variables and deploy the change.";

type Answer = { status: number; body: Record<string, unknown> };
const SIGNED_IN: Answer = {
  status: 200,
  body: { ok: true, authenticated: true, next: '/app-v2/' },
};

async function serveSetupPage(page: Page, state: 'unclaimed' | 'owned') {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated browser runner');
  const response = await page.request.post(
    `/__r2_fixture/setup-page/${state}`,
    { headers: { 'X-Fixture-Token': token, Origin: new URL(base).origin } },
  );
  expect(response.ok(), await response.text()).toBe(true);
  const served = (await response.json()) as {
    html: string;
    headers: Record<string, string>;
  };
  await page.route('**/setup', (route) =>
    route.fulfill({
      status: 200,
      headers: {
        ...served.headers,
        'Content-Type': 'text/html; charset=utf-8',
      },
      body: served.html,
    }),
  );
  await page.route('**/readyz', (route) =>
    route.fulfill({ status: 200, json: { ready: true } }),
  );
  await page.route('**/app-v2/', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'text/html',
      body: '<!doctype html><title>Row-Bot</title><main>Signed in</main>',
    }),
  );
  const posts: Request[] = [];
  let answer = SIGNED_IN;
  let held: Promise<void> = Promise.resolve();
  let release = () => {};
  await page.route('**/api/access/setup', async (route) => {
    posts.push(route.request());
    await held;
    await route.fulfill({ status: answer.status, json: answer.body });
  });
  return {
    posts,
    answer: (next: Answer) => {
      answer = next;
    },
    /** Keep the next answers back until `release`. */
    hold: () => {
      held = new Promise((resolve) => (release = resolve));
    },
    release: () => release(),
  };
}

/** A refusal answered on purpose shows as a failed request in the console. */
function expectRefusal(status: string) {
  test.info().annotations.push({
    type: 'expected-console-error',
    description: JSON.stringify({
      owner: 'setup-link',
      fixture: 'the setup page’s refusal of a link’s code',
      signature: `Failed to load resource: the server responded with a status of ${status}`,
      count: 1,
    }),
  });
}

test('a sign-in link signs in with one click, and its code leaves the address at once', async ({
  page,
}) => {
  const setup = await serveSetupPage(page, 'unclaimed');
  setup.hold();
  await page.goto(`/setup#code=${CODE}`);

  // Sent as if Continue was pressed, with the code in the field.
  await expect(page.locator('#claim-code')).toHaveValue(CODE);
  await expect(page.getByRole('button', { name: 'Checking…' })).toBeDisabled();
  await expect.poll(() => setup.posts.length).toBe(1);
  expect(setup.posts[0].postDataJSON()).toEqual({ code: CODE });
  expect(setup.posts[0].url()).not.toContain(CODE);
  // Before any answer, the fragment is gone from the address and this
  // history entry.
  expect(page.url()).toMatch(/\/setup$/);
  expect(await page.evaluate(() => location.hash)).toBe('');

  setup.release();
  await page.waitForURL(/\/app-v2\/$/);
  expect(page.url()).not.toContain(CODE);
  expect(setup.posts).toHaveLength(1);
});

test('a used or wrong code from a link is refused in today’s words, and still leaves the address', async ({
  page,
}) => {
  expectRefusal('410 (Gone)');
  expectRefusal('400 (Bad Request)');
  const setup = await serveSetupPage(page, 'unclaimed');

  setup.answer({
    status: 410,
    body: { ok: false, error: 'code_used', detail: USED },
  });
  await page.goto(`/setup#code=${CODE}`);
  await expect(page.getByRole('alert')).toHaveText(USED);
  // A used code points to where a new one is made.
  await expect(
    page.getByRole('link', { name: 'Manage service' }),
  ).toBeVisible();
  await expect(page.locator('#claim-code')).toHaveValue(CODE);
  expect(page.url()).toMatch(/\/setup$/);

  // A link pasted into this tab, already on the page, goes the same way.
  setup.answer({
    status: 400,
    body: { ok: false, error: 'wrong_code', detail: WRONG, tries_left: null },
  });
  await page.goto(`/setup#code=${CODE}`);
  await expect(page.getByRole('alert')).toHaveText(WRONG);
  await expect(page.locator('#claim-code')).toHaveValue(CODE);
  expect(page.url()).toMatch(/\/setup$/);
  expect(setup.posts).toHaveLength(2);
});

test('a link that holds no code is ignored and cleared', async ({ page }) => {
  const setup = await serveSetupPage(page, 'unclaimed');
  // Opened, then pasted into the same tab.
  for (const fragment of ['#code=ABC', '#code=ABCD-EFGH-JKM!', '#other=1']) {
    await page.goto(`/setup${fragment}`);
    await expect(page.locator('#claim-code')).toBeFocused();
    await expect(page.locator('#claim-code')).toHaveValue('');
    await expect.poll(() => page.url()).toMatch(/\/setup$/);
  }
  expect(setup.posts).toHaveLength(0);
});

test('a Row-Bot someone owns sends no link’s code and shows its owner page', async ({
  page,
}) => {
  const setup = await serveSetupPage(page, 'owned');
  await page.goto(`/setup#code=${CODE}`);

  await expect(
    page.getByRole('heading', { name: 'This Row-Bot already has an owner' }),
  ).toBeVisible();
  expect(page.url()).toMatch(/\/setup$/);
  // Only a recovery code could sign in here, and it signs out every other
  // browser: that takes a deliberate Continue, with its own field empty.
  await page.getByText('Lost access?').click();
  await expect(page.locator('#lost-code')).toBeVisible();
  await expect(page.locator('#lost-code')).toHaveValue('');
  // Pasted into this tab: cleared, and still not sent.
  await page.goto(`/setup#code=${CODE}`);
  await expect.poll(() => page.url()).toMatch(/\/setup$/);
  await expect(page.locator('#lost-code')).toHaveValue('');
  expect(setup.posts).toHaveLength(0);
});
