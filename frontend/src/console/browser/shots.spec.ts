import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { openConsole } from './serve';
import type { FakeState } from './fake-api';
import { COMPANY_SPACE } from './fake-brain';

// The UX review's screenshots, taken only when ROW_BOT_CONSOLE_SHOTS names a
// folder: every page at a desktop width in light and dark, filled in, empty
// and failing, each page once at 390 px, and the states and dialogs between.
const folder = process.env.ROW_BOT_CONSOLE_SHOTS;
test.skip(!folder, 'Set ROW_BOT_CONSOLE_SHOTS to a folder to take them');

const PAGES: [string, string][] = [
  ['people', 'people'],
  ['devices', 'devices'],
  ['models-endpoints', 'models?tab=endpoints'],
  ['models-catalog', 'models?tab=catalog'],
  ['models-budgets', 'models?tab=budgets'],
  ['models-usage', 'models?tab=usage'],
  ['policy-company', 'policy?tab=company'],
  ['policy-groups', 'policy?tab=groups'],
  ['policy-history', 'policy?tab=history'],
  ['audit', 'audit'],
  ['knowledge', 'knowledge'],
  ['knowledge-documents', `knowledge?space=${COMPANY_SPACE}&tab=documents`],
  ['knowledge-facts', `knowledge?space=${COMPANY_SPACE}&tab=facts`],
  ['knowledge-graph', `knowledge?space=${COMPANY_SPACE}&tab=graph`],
];
const THEMES = ['light', 'dark'] as const;

/** The whole page in one picture: the window grows to the content's height. */
async function shoot(page: Page, name: string, width = 1440) {
  await page.setViewportSize({ width, height: width < 768 ? 844 : 900 });
  await page.waitForTimeout(250);
  const height = await page.evaluate(() => {
    const main = document.querySelector('.console-main');
    const nav = document.querySelector('.console-nav');
    return Math.max(
      main
        ? main.scrollHeight +
            (window.innerWidth < 768 ? (nav?.clientHeight ?? 0) : 0)
        : 0,
      document.documentElement.scrollHeight,
    );
  });
  await page.setViewportSize({
    width,
    height: Math.max(width < 768 ? 844 : 900, height),
  });
  await page.waitForTimeout(100);
  mkdirSync(folder as string, { recursive: true });
  await page.screenshot({ path: resolve(folder as string, `${name}.png`) });
}

async function settle(page: Page) {
  await expect(
    page
      .locator('.console-page h1, .console-state h2, .console-state strong')
      .first(),
  ).toBeVisible();
  await expect(page.getByRole('status', { name: /^Loading/ })).toHaveCount(0);
}

for (const theme of THEMES) {
  for (const state of ['populated', 'empty', 'error'] as FakeState[]) {
    test(`pages ${state} ${theme}`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: theme });
      await openConsole(page, '/console/people', state);
      for (const [name, path] of PAGES) {
        if (name === 'people' && state === 'error') continue;
        await page.setViewportSize({ width: 1440, height: 900 });
        await page.goto(`https://org.example.test/console/${path}`);
        await settle(page);
        await shoot(page, `${name}-${state}-${theme}`);
      }
    });
  }

  test(`states and dialogs ${theme}`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: theme });
    // People can't fail on their own: the console needs them to open.
    await openConsole(page, '/console/people', 'populated', {
      answer: (url) =>
        url.pathname === '/api/admin/people'
          ? { status: 500, body: { error: 'internal' } }
          : undefined,
    });
    await settle(page);
    await shoot(page, `people-error-${theme}`);

    await page.unrouteAll();
    await openConsole(page, '/console/people', 'populated', {
      answer: (url) =>
        url.pathname === '/api/me'
          ? { status: 401, body: { error: 'sign_in_required' } }
          : undefined,
    });
    await settle(page);
    await shoot(page, `session-ended-${theme}`);

    await page.unrouteAll();
    await openConsole(page, '/console/people', 'populated', {
      answer: (url) =>
        url.pathname === '/api/me'
          ? {
              status: 200,
              body: {
                id: 'x',
                display_name: 'Ada',
                email: null,
                status: 'active',
                groups: [],
                admin: false,
              },
            }
          : undefined,
    });
    await settle(page);
    await shoot(page, `not-admin-${theme}`);

    // The page the server itself sends a member who isn't an admin.
    await page.unrouteAll();
    await openConsole(page, '/console/', 'populated', {
      page: 'console-not-admin.html',
    });
    await expect(
      page.getByRole('heading', { name: 'You need to be an admin' }),
    ).toBeVisible();
    await shoot(page, `not-admin-server-page-${theme}`);

    await page.unrouteAll();
    await openConsole(page, '/console/people');
    await settle(page);
    await page.getByRole('button', { name: 'Suspend Ada Lovelace' }).click();
    await shoot(page, `people-confirm-suspend-${theme}`);
    await page.keyboard.press('Escape');

    await page.goto('https://org.example.test/console/models?tab=endpoints');
    await settle(page);
    await page.getByRole('button', { name: 'Edit Anthropic' }).click();
    await shoot(page, `models-endpoint-edit-${theme}`);
    await page.keyboard.press('Escape');

    await page.goto('https://org.example.test/console/policy?tab=company');
    await settle(page);
    await page.getByRole('switch', { name: 'Local models' }).click();
    await shoot(page, `policy-company-unsaved-${theme}`);
    await page.getByRole('button', { name: 'Save policy' }).click();
    await expect(page.getByText('Some groups still get')).toBeVisible();
    await shoot(page, `policy-company-saved-${theme}`);

    await page.goto('https://org.example.test/console/policy?tab=groups');
    await settle(page);
    await page.getByRole('button', { name: /Change what/ }).click();
    await shoot(page, `policy-groups-edit-${theme}`);
    await page.keyboard.press('Escape');

    await page.goto('https://org.example.test/console/audit');
    await settle(page);
    await page.getByRole('button', { name: 'Check integrity' }).click();
    await expect(page.getByText('The audit log is intact')).toBeVisible();
    await page
      .getByRole('button', { name: /Show details/ })
      .nth(1)
      .click();
    await shoot(page, `audit-checked-${theme}`);

    // Knowledge: no models yet, an entry's details, and a file uploading.
    await page.unrouteAll();
    await openConsole(
      page,
      `/console/knowledge?space=${COMPANY_SPACE}`,
      'populated',
      {
        answer: (url) =>
          url.pathname === '/api/admin/models'
            ? { status: 200, body: { models: [] } }
            : undefined,
      },
    );
    await settle(page);
    await shoot(page, `knowledge-no-models-${theme}`);

    await page.goto(
      `https://org.example.test/console/knowledge?space=${COMPANY_SPACE}&tab=graph`,
    );
    await settle(page);
    await page.getByRole('button', { name: 'Show details for Atlas' }).click();
    await expect(page.getByText('The new expenses app.')).toBeVisible();
    await shoot(page, `knowledge-entry-${theme}`);

    await page.unrouteAll();
    await openConsole(page, `/console/knowledge?space=${COMPANY_SPACE}`);
    await settle(page);
    // The upload takes a moment here, so it is seen uploading.
    await page.route('**/documents', async (route) => {
      if (route.request().method() === 'POST')
        await new Promise((done) => setTimeout(done, 3_000));
      await route.fallback();
    });
    await page.getByLabel('Choose files to upload').setInputFiles({
      name: 'onboarding.md',
      mimeType: 'text/markdown',
      buffer: Buffer.from('# Onboarding'),
    });
    await expect(page.getByText('Uploading')).toBeVisible();
    await shoot(page, `knowledge-uploading-${theme}`);

    await page.unrouteAll();
    await page.setViewportSize({ width: 1440, height: 900 });
    await openConsole(page, '/console/devices', 'populated', { delay: 60_000 });
    await page.waitForTimeout(400);
    await page.screenshot({
      path: resolve(folder as string, `console-loading-${theme}.png`),
    });
  });
}

test('phone pages', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openConsole(page, '/console/people');
  for (const [name, path] of PAGES) {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`https://org.example.test/console/${path}`);
    await settle(page);
    await shoot(page, `${name}-phone-light`, 390);
  }
});
