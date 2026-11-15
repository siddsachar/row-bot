import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';
import { blockFixtureServiceWorkers } from './unified-helpers';
import type { Page, TestInfo } from '@playwright/test';

// F27: signing in to an app from another device. The fixture treats this
// browser as a phone or remote browser and signs in against the synthetic
// service on this machine. Without Row-Bot's public address the service sends
// the browser to http://127.0.0.1/…, which doesn't load on another device: the
// test answers that address itself with a dead-end page (it is never fetched),
// and the person pastes it back. Each test removes the app it connected.

const SINK = 'http://127.0.0.1';
const CALLBACK =
  /^http:\/\/127\.0\.0\.1\/api\/v1\/settings\/mcp\/auth\/callback\?/;

test.use({ serviceWorkers: 'allow', localSinks: [SINK] });
test.beforeEach(async ({ context, page }) => {
  await blockFixtureServiceWorkers(context);
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
  // What another device shows at Row-Bot's loopback address: nothing there.
  await context.route(CALLBACK, (route) =>
    route.fulfill({
      contentType: 'text/html',
      body: "<!doctype html><title>This site can't be reached</title><h1>This site can't be reached</h1><p>127.0.0.1 refused to connect.</p>",
    }),
  );
});

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
  expect(response.ok(), await response.text()).toBe(true);
}

/**
 * A refusal the test causes on purpose shows as a failed request in the
 * console. `upTo`: the server's reason phrase depends on its Python (422 is
 * "Unprocessable Content" from 3.13, "Unprocessable Entity" before), so each
 * spelling may appear at most `count` times.
 */
function expectConsoleError(
  fixture: string,
  status: string,
  count: number,
  upTo = false,
) {
  test.info().annotations.push({
    type: 'expected-console-error',
    description: JSON.stringify({
      owner: 'apps-sign-in-elsewhere',
      fixture,
      signature: `Failed to load resource: the server responded with a status of ${status}`,
      count,
      ...(upTo ? { upTo } : {}),
    }),
  });
}

const phone = (info: TestInfo) =>
  (info.project.use.viewport?.width ?? 1440) < 700;

async function shoot(page: Page, info: TestInfo, name: string) {
  const suffix = phone(info) ? '-phone' : '';
  // On a phone the setup's steps start the screen, so the whole sheet shows.
  const steps = page.getByRole('list', { name: 'Setup steps' });
  if (phone(info) && (await steps.count()))
    await steps.evaluate((list) => list.scrollIntoView({ block: 'start' }));
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `${name}${suffix}-${appearance}`);
  }
  await accessibility(page, info, `${name}${suffix}`);
  await page.emulateMedia({ colorScheme: 'light' });
}

/** The app's page, Connect, and the consent: its sign-in starts. */
async function connect(page: Page, app: string) {
  await page.goto('/app-v2/settings/apps');
  await page
    .getByRole('searchbox', { name: 'Search apps' })
    .fill(app.toLowerCase());
  await page.getByRole('link', { name: new RegExp(`^${app}`) }).click();
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const consent = page.getByRole('dialog');
  await expect(consent).toHaveAccessibleName(`Connect ${app}`);
  await consent.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(consent).toHaveCount(0);
}

async function allowAndFinish(page: Page, app: string) {
  const access = page.getByRole('dialog');
  await expect(access).toHaveAccessibleName(`Here's what ${app} can do`);
  await access.getByRole('button', { name: 'Allow', exact: true }).click();
  await expect(
    page.getByTestId('conversation-pane').getByText('Ready', { exact: true }),
  ).toBeVisible();
}

async function remove(page: Page, app: string) {
  await page.getByRole('button', { name: `More for ${app}` }).click();
  await page.getByRole('menuitem', { name: 'Remove…' }).click();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: 'Remove', exact: true })
    .click();
  await expect(page).toHaveURL(/settings\/apps$/);
}

test('another device pastes back the address its browser ends on', async ({
  page,
  context,
}, info) => {
  for (const phrase of ['Unprocessable Content', 'Unprocessable Entity'])
    expectConsoleError(
      'a pasted address that is not one',
      `422 (${phrase})`,
      1,
      true,
    );
  expectConsoleError('three refused pastes', '409 (Conflict)', 3);
  await seed(page, '/__s7_fixture/sign-in-elsewhere?mode=paste');
  await connect(page, 'Linear');
  const field = page.getByLabel('Paste that address here');
  await expect(field).toBeVisible();
  const open = page.getByRole('button', { name: 'Open sign-in page' });
  await expect(open).toBeEnabled();
  await expect(page.getByRole('timer')).toContainText(
    /The sign-in link works for another [45]:\d\d\./,
  );
  await shoot(page, info, 'sign-in-elsewhere-paste');

  const opened = context.waitForEvent('page');
  await open.click();
  const service = await opened;
  await service.getByRole('link', { name: 'Allow' }).click();
  await service.waitForURL(CALLBACK);
  await expect(
    service.getByRole('heading', { name: "This site can't be reached" }),
  ).toBeVisible();
  const address = service.url();
  await service.close();

  const finish = page.getByRole('button', { name: 'Finish' });
  const alert = page.getByRole('alert');
  // Not the address: plain words beside the field, and it stays to be fixed.
  await field.fill('https://linear.app/settings');
  await finish.click();
  await expect(alert).toHaveText(/That doesn't look like the address\./);
  await expect(field).toHaveAttribute('aria-invalid', 'true');
  await shoot(page, info, 'sign-in-elsewhere-error-invalid');

  // Another sign-in's address: the same address with another state.
  const other = new URL(address);
  other.searchParams.set('state', 'x'.repeat(43));
  await field.fill(other.href);
  await finish.click();
  await expect(alert).toHaveText(/from a different sign-in/);
  await shoot(page, info, 'sign-in-elsewhere-error-other');

  // "Already used" and "ended" come from the server too; shown here by
  // answering one paste each with that refusal.
  const route = '**/api/v1/integrations/plans/*/sign-in';
  for (const [code, words, name] of [
    ['mcp_auth_address_used', /already been used/, 'used'],
    ['mcp_auth_address_expired', /This sign-in has ended/, 'ended'],
  ] as const) {
    await page.route(route, async (request) => {
      const real = await request.fetch({ postData: '{"address":"nope"}' });
      await request.fulfill({
        status: 409,
        contentType: 'application/problem+json',
        json: {
          ...(await real.json()),
          type: `urn:row-bot:error:${code}`,
          title: code.replace(/_/g, ' '),
          status: 409,
          code,
        },
      });
    });
    await field.fill(address);
    await finish.click();
    await expect(alert).toHaveText(words);
    await shoot(page, info, `sign-in-elsewhere-error-${name}`);
    await page.unroute(route);
  }

  await field.fill(address);
  await finish.click();
  await allowAndFinish(page, 'Linear');
  await shoot(page, info, 'sign-in-elsewhere-success');
  await expect(page.getByText(/synthetic-code|state=/)).toHaveCount(0);
  await remove(page, 'Linear');
});

test('a sign-in left past its five minutes says so and offers to start again', async ({
  page,
}, info) => {
  await seed(page, '/__s7_fixture/sign-in-elsewhere?mode=paste');
  await connect(page, 'Calendly');
  await expect(page.getByLabel('Paste that address here')).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Open sign-in page' }),
  ).toBeEnabled();
  await seed(page, '/__s7_fixture/sign-in-elsewhere/expire');
  await expect(
    page.getByText(
      "The sign-in wasn't finished in time. Anything already done is kept.",
    ),
  ).toBeVisible();
  await expect(page.getByLabel('Paste that address here')).toHaveCount(0);
  await expect(
    page.getByRole('button', { name: 'Try again', exact: true }),
  ).toBeVisible();
  await shoot(page, info, 'sign-in-elsewhere-expired');
  await remove(page, 'Calendly');
});

test('through the public address the person just signs in', async ({
  page,
  context,
}, info) => {
  await seed(page, '/__s7_fixture/sign-in-elsewhere?mode=public');
  await connect(page, 'Mixpanel');
  await expect(
    page.getByText(
      'Sign in to Mixpanel on the page that opens, then come back here. This page updates by itself.',
    ),
  ).toBeVisible();
  const open = page.getByRole('button', { name: 'Open sign-in page' });
  await expect(open).toBeVisible();
  await expect(page.getByLabel('Paste that address here')).toHaveCount(0);
  await shoot(page, info, 'sign-in-elsewhere-public');
  const opened = context.waitForEvent('page');
  await open.click();
  const service = await opened;
  await service.getByRole('link', { name: 'Allow' }).click();
  await expect(
    service.getByText('Signed in to the synthetic service.'),
  ).toBeVisible();
  await service.close();
  await allowAndFinish(page, 'Mixpanel');
  await remove(page, 'Mixpanel');
});

test('where the service offers a code, the person enters it on its own page', async ({
  page,
}, info) => {
  await seed(page, '/__s7_fixture/sign-in-elsewhere?mode=device');
  await connect(page, 'Granola');
  await expect(page.getByText('WDJB-MJHT')).toBeVisible();
  await expect(
    page.getByText('Go to auth.example.test/device and sign in.'),
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Open auth.example.test/device' }),
  ).toBeVisible();
  await expect(page.getByRole('timer')).toContainText(
    /The sign-in code works for another [45]:\d\d\./,
  );
  await shoot(page, info, 'sign-in-elsewhere-code');
  await seed(page, '/__s7_fixture/sign-in-elsewhere/allow-code');
  await allowAndFinish(page, 'Granola');
  await remove(page, 'Granola');
});
