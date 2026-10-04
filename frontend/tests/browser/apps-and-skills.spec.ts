import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';
import {
  blockFixtureServiceWorkers,
  composer,
  fixtureState,
} from './unified-helpers';
import type { BrowserContext, Page, TestInfo } from '@playwright/test';

/** Phase 3 journeys: every network effect is a local fake; clicks and dialogs are counted. */
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

/** Counts in-app clicks and dialogs for the complexity budget. */
function budget(page: Page) {
  let clicks = 0;
  let dialogs = 0;
  return {
    click: async (target: ReturnType<Page['locator']>) => {
      clicks += 1;
      await target.click();
    },
    dialog: () => {
      dialogs += 1;
      return page.getByRole('dialog');
    },
    report: (info: TestInfo, name: string) => {
      void info.attach(`${name}-budget`, {
        body: JSON.stringify({ clicks, dialogs }),
        contentType: 'application/json',
      });
      return { clicks, dialogs };
    },
  };
}

async function openApps(
  page: Page,
  context: BrowserContext,
  path = '/app-v2/settings/apps',
) {
  await blockFixtureServiceWorkers(context);
  await page.goto(path);
  await expect(
    page.getByRole('region', { name: /^(Apps|Skills)$/ }),
  ).toBeVisible();
}

/** A console line a test causes on purpose (a missing item, an injected outage). */
function expectConsoleError(fixture: string, signature: string) {
  test.info().annotations.push({
    type: 'expected-console-error',
    description: JSON.stringify({
      owner: 'apps-and-skills',
      fixture,
      signature,
      count: 1,
      upTo: true,
    }),
  });
}

async function find(page: Page, query: string) {
  await page
    .getByRole('searchbox', { name: /^Search (apps|skills)$/ })
    .fill(query);
}

async function checkLayout(page: Page, info: TestInfo, name: string) {
  await assertNoOverflow(page);
  await accessibility(page, info, name);
  await screenshot(page, info, name);
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

test('connect a hosted app with sign-in, then turn it off and remove it', async ({
  page,
  context,
}, info) => {
  await seed(page, '/__p5_fixture/apps');
  await openApps(page, context);
  const steps = budget(page);
  await find(page, 'linear');
  await steps.click(page.getByRole('link', { name: /^Linear/ }));
  await expect(page.getByRole('heading', { name: 'Linear' })).toBeVisible();
  await expect(
    page.getByText(/What you ask goes to mcp\.linear\.app/),
  ).toBeVisible();
  await steps.click(page.getByRole('button', { name: 'Connect', exact: true }));
  const consent = steps.dialog();
  await expect(consent).toHaveAccessibleName('Connect Linear');
  await expect(
    consent.getByText('You sign in to Linear in your browser.'),
  ).toBeVisible();
  await checkLayout(page, info, 'apps-consent');
  await approveSignIn(context, () =>
    steps.click(consent.getByRole('button', { name: 'Connect', exact: true })),
  );
  const access = steps.dialog();
  await expect(access).toHaveAccessibleName("Here's what Linear can do");
  await expect(
    access.locator('summary', { hasText: 'Looks things up' }),
  ).toBeVisible();
  await expect(
    access.locator('summary', { hasText: 'Always asks first' }),
  ).toBeVisible();
  await expect(
    access.getByRole('radio', { name: /Ask before changes/ }),
  ).toBeChecked();
  await checkLayout(page, info, 'apps-access');
  await steps.click(access.getByRole('button', { name: 'Allow', exact: true }));
  await expect(page.getByText('Ready', { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/settings\/apps\/item\?id=mcp%3A/);
  const counted = steps.report(info, 'hosted-connect');
  expect(counted.clicks).toBeLessThanOrEqual(4);
  expect(counted.dialogs).toBeLessThanOrEqual(2);
  await expect(
    page.getByRole('button', { name: 'Try it', exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(/3 of 3 actions on · Ask before changes/),
  ).toBeVisible();
  await checkLayout(page, info, 'apps-detail-ready');

  await page.getByRole('button', { name: 'More for Linear' }).click();
  await page.getByRole('menuitem', { name: 'Turn off' }).click();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: 'Turn off', exact: true })
    .click();
  await expect(page.getByText('Off', { exact: true })).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Turn on', exact: true }),
  ).toBeVisible();

  await page.getByRole('button', { name: 'More for Linear' }).click();
  await page.getByRole('menuitem', { name: 'Remove…' }).click();
  const remove = page.getByRole('dialog');
  await expect(remove).toHaveAccessibleName('Remove Linear');
  await expect(remove.getByRole('button', { name: 'Cancel' })).toBeFocused();
  await expect(
    remove.getByRole('button', { name: 'Remove', exact: true }),
  ).toHaveClass(/danger/);
  await checkLayout(page, info, 'apps-remove');
  // Cleanup is part of what is agreed: the switch follows a fresh review.
  const cleanup = remove.getByRole('switch', {
    name: 'Also delete saved keys and data',
  });
  await cleanup.click();
  await expect(cleanup).toBeChecked();
  await remove.getByRole('button', { name: 'Remove', exact: true }).click();
  await expect(page).toHaveURL(/settings\/apps$/);
  await expect(page.getByRole('heading', { name: 'Your apps' })).toHaveCount(0);
});

test('connect an API-key app with one pasted key', async ({
  page,
  context,
}, info) => {
  await seed(page, '/__p5_fixture/apps');
  await openApps(page, context);
  const steps = budget(page);
  await find(page, 'coda');
  await steps.click(page.getByRole('link', { name: /^Coda/ }));
  await steps.click(page.getByRole('button', { name: 'Connect', exact: true }));
  const consent = steps.dialog();
  await expect(consent.getByText(/You paste a key/)).toBeVisible();
  await steps.click(
    consent.getByRole('button', { name: 'Connect', exact: true }),
  );
  const key = page.getByLabel('API key');
  await expect(key).toHaveAttribute('type', 'password');
  await key.fill('synthetic-coda-key');
  await steps.click(
    page.getByRole('button', { name: 'Continue', exact: true }),
  );
  await steps.click(
    steps.dialog().getByRole('button', { name: 'Allow', exact: true }),
  );
  await expect(page.getByText('Ready', { exact: true })).toBeVisible();
  const counted = steps.report(info, 'api-key-connect');
  expect(counted.clicks).toBeLessThanOrEqual(5);
  await expect(page.getByText('Saved in your system keychain')).toBeVisible();
  await expect(page.getByText('synthetic-coda-key')).toHaveCount(0);
});

test('fix sign-in after stopping half way', async ({ page, context }, info) => {
  await seed(page, '/__p5_fixture/apps');
  await openApps(page, context, '/app-v2/settings/apps/notion');
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const signIn = context.waitForEvent('page');
  await page
    .getByRole('dialog')
    .getByRole('button', { name: 'Connect', exact: true })
    .click();
  await (await signIn).close(); // Closed without signing in.
  await expect(
    page.getByText('Finish signing in to Notion in your browser.', {
      exact: false,
    }),
  ).toBeVisible();
  await checkLayout(page, info, 'apps-stepper-sign-in');
  await page.getByRole('button', { name: 'Stop', exact: true }).click();
  await expect(page).toHaveURL(/settings\/apps\/item\?id=mcp%3A/);
  await expect(page.getByText('Sign in needed', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await approveSignIn(context, () =>
    page
      .getByRole('dialog')
      .getByRole('button', { name: 'Sign in', exact: true })
      .click(),
  );
  await page
    .getByRole('dialog')
    .getByRole('button', { name: 'Allow', exact: true })
    .click();
  await expect(page.getByText('Ready', { exact: true })).toBeVisible();
});

test('add a skill turns it on and Try it only drafts', async ({
  page,
  context,
}, info) => {
  await seed(page, '/__p4_fixture/integration-skills');
  await openApps(page, context, '/app-v2/settings/skills');
  const steps = budget(page);
  await find(page, 'writing');
  await steps.click(page.getByRole('link', { name: /^Browser writing/ }));
  await steps.click(page.getByRole('button', { name: 'Add', exact: true }));
  const consent = steps.dialog();
  await expect(
    consent.getByText(/Adds the skill and turns it on/),
  ).toBeVisible();
  await steps.click(consent.getByRole('button', { name: 'Add', exact: true }));
  await expect(page.getByText('Ready', { exact: true })).toBeVisible();
  const counted = steps.report(info, 'add-skill');
  expect(counted.clicks).toBeLessThanOrEqual(3);
  await expect(
    page.getByText('/browser-writing', { exact: true }),
  ).toBeVisible();
  const inside = page.locator('summary', { hasText: "What's inside" });
  await expect(inside).toContainText('2 files');
  await inside.click();
  await expect(page.getByText('references/checklist.txt')).toBeVisible();
  await checkLayout(page, info, 'skills-detail');
  const before = (await fixtureState(page)).calls.length;
  await page.getByRole('button', { name: 'Try it', exact: true }).click();
  await expect(composer(page)).toHaveValue('/browser-writing ');
  expect((await fixtureState(page)).calls.length).toBe(before);
});

test('a package brings its own app and finishes setup in one plan', async ({
  page,
  context,
}, info) => {
  await openApps(
    page,
    context,
    '/app-v2/settings/apps/item?id=plugin%3Abundled%3Alocal-text-tools',
  );
  await page.getByRole('button', { name: 'Add', exact: true }).click();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: 'Add', exact: true })
    .click();
  await expect(page).toHaveURL(
    /settings\/apps\/item\?id=plugin%3A[a-z-]*local-text-tools/,
  );
  const included = page.getByRole('region', { name: 'Included' });
  await expect(included.getByRole('link').nth(1)).toBeVisible();
  await page
    .getByRole('button', { name: 'Continue setup', exact: true })
    .click();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: 'Continue setup', exact: true })
    .click();
  await expect(
    page.getByRole('button', { name: 'Continue setup', exact: true }),
  ).toHaveCount(0);
  await checkLayout(page, info, 'apps-package');
  await included
    .getByRole('link')
    .filter({ hasText: /connection|text-stats|local/i })
    .first()
    .click();
  await expect(page.getByText(/Installed as part of/)).toBeVisible();
});

test('legacy links land on Apps, Skills, one item or Advanced', async ({
  page,
  context,
}) => {
  expectConsoleError(
    'a legacy link to a skill that is not added',
    'Failed to load resource: the server responded with a status of 404 (Not Found)',
  );
  await blockFixtureServiceWorkers(context);
  for (const [from, to] of [
    [
      '/app-v2/settings/integrations?type=skill&tab=discover',
      /\/settings\/skills$/,
    ],
    ['/app-v2/settings/integrations?type=mcp&tab=my', /\/settings\/apps$/],
    [
      '/app-v2/settings/integrations?type=mcp&view=catalogs',
      /\/settings\/apps\?view=advanced$/,
    ],
    [
      '/app-v2/settings/integrations?tab=my&type=skill&selected=skill%3Abrowser-writing',
      /\/settings\/skills\/browser-writing$/,
    ],
    ['/app-v2/settings/mcp#mcp-servers', /\/settings\/apps$/],
    ['/app-v2/settings/mcp#mcp-runtimes', /\/settings\/apps\?view=advanced$/],
    ['/app-v2/settings/plugins#plugin-marketplace', /\/settings\/apps$/],
    [
      '/app-v2/settings/skills#public-skills',
      /\/settings\/skills#public-skills$/,
    ],
  ] as const) {
    await page.goto(from);
    await expect(page).toHaveURL(to);
  }
  await expect(
    page.getByRole('searchbox', { name: 'Search skills' }),
  ).toBeVisible();
});

test('pending and unavailable catalogs explain themselves and offer a way on', async ({
  page,
  context,
}, info) => {
  expectConsoleError(
    'an injected catalog outage',
    'Failed to load resource: the server responded with a status of 503 (Service Unavailable)',
  );
  await blockFixtureServiceWorkers(context);
  let calls = 0;
  await page.route(
    '**/api/v1/integrations/items?*scope=catalog*',
    async (route) => {
      calls += 1;
      if (calls === 1)
        return route.fulfill({
          status: 503,
          contentType: 'application/json',
          body: JSON.stringify({ code: 'dependency_unavailable', status: 503 }),
        });
      const response = await route.fetch();
      const body = await response.json();
      body.sources = [
        {
          source: 'official',
          status: 'pending',
          message: '',
          fetched_at: null,
        },
      ];
      return route.fulfill({ response, json: body });
    },
  );
  await page.goto('/app-v2/settings/apps');
  await expect(page.getByRole('alert')).toContainText("Couldn't load apps");
  await page.getByRole('button', { name: 'Retry', exact: true }).click();
  await expect(
    page.getByText('Preparing the app catalog…', { exact: false }),
  ).toBeVisible();
  await page.unroute('**/api/v1/integrations/items?*scope=catalog*');
  await find(page, 'zzzz-no-such-app');
  await expect(
    page.getByRole('heading', { name: /No apps match/ }),
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Search online catalogs' }).first(),
  ).toBeVisible();
  await checkLayout(page, info, 'apps-empty');
});

test('Apps works with the keyboard and names everything for screen readers', async ({
  page,
  context,
}, info) => {
  await openApps(page, context);
  await page.getByRole('searchbox', { name: 'Search apps' }).focus();
  await page.keyboard.type('context7');
  const card = page.getByRole('link', { name: /^Context7/ }).first();
  await expect(card).toBeVisible();
  await card.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { name: 'Context7' })).toBeFocused();
  await page.getByRole('button', { name: 'Connect', exact: true }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(
    page.getByRole('button', { name: 'Connect', exact: true }),
  ).toBeFocused();
  await checkLayout(page, info, 'apps-context7-detail');
  await page.goto('/app-v2/settings/apps');
  await expect(page.getByRole('heading', { name: 'Featured' })).toBeVisible();
  await checkLayout(page, info, 'apps-home');
  const viewport = page.viewportSize()!;
  await page.setViewportSize({ width: 320, height: 800 });
  await assertNoOverflow(page);
  await screenshot(page, info, 'apps-home-320');
  await page.setViewportSize(viewport);
});
