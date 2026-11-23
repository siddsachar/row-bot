import type { Page, TestInfo } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';
import { fixtureHeaders } from './surface-helpers';
import {
  blockFixtureServiceWorkers,
  composer,
  dismissNotices,
  newConversation,
} from './unified-helpers';

/*
 * S12 · 5.3 ledger fixes, as a person sees them: a monthly workflow whose step
 * names its app and whose delivery lists only channels that are set up
 * (B345), an app's access after Save (B346), Google after its first sign-in
 * (F29), an approval for an app tool in plain words without a keyboard hint
 * on a phone (B388, B389), and maths in a reply. Every app and account is a
 * local fake; the model is scripted.
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

/** Light and dark at desktop width, then at phone width (after ``settle``). */
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
  await expect(settle).toPass();
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

async function openWorkflows(page: Page) {
  await page.goto('/app-v2/?tab=workflows');
  await page
    .getByRole('searchbox', { name: 'Search workflows' })
    .fill('Month-end close');
  await page
    .getByRole('button', { name: 'Search workflows', exact: true })
    .click();
}

test.beforeEach(async ({ context }) => {
  await blockFixtureServiceWorkers(context);
});

test('a monthly workflow reads as Monthly, and delivery lists only set-up channels (B345)', async ({
  page,
}, info) => {
  test.slow();
  await page.setViewportSize(DESKTOP);
  await seed(page, '/__p6_fixture/views');
  try {
    await seed(page, '/__s12_fixture/monthly-workflow');
    await openWorkflows(page);
    await page
      .getByRole('button', {
        name: 'Edit workflow: Month-end close',
        exact: true,
      })
      .click();
    const rail = page.getByRole('complementary', {
      name: 'Schedule, delivery and policy',
    });
    await expect(rail.getByLabel('How often')).toHaveValue('monthly');
    await expect(rail.getByLabel('Day of the month')).toHaveValue('1');
    await expect(rail.getByText(/^Monthly on the 1st at /)).toBeVisible();
    await expect(rail.getByLabel('Cron expression')).toHaveCount(0);
    // WhatsApp ships with Row-Bot, but no phone is linked: it is not offered.
    const delivery = rail.getByRole('group', { name: 'Send results to' });
    await expect(delivery).toContainText('Always in this app');
    await expect(delivery).not.toContainText('WhatsApp');
    await views(page, info, 'monthly-preset');
    await delivery.scrollIntoViewIfNeeded();
    await views(page, info, 'delivery-list');
    // A phone edits workflows on the computer (C2, D11 A): the same editor
    // says so there, and is back as it was on a wider window.
    await page.setViewportSize(PHONE);
    await expect(
      page.getByRole('region', { name: 'The workflow editor' }),
    ).toContainText('Open this on your computer');
    await views(page, info, 'workflow-editor-phone');
    await page.setViewportSize(DESKTOP);
    await expect(delivery).toContainText('Always in this app');
  } finally {
    await seed(page, '/__s12_fixture/remove');
    await seed(page, '/__p6_fixture/views/remove');
  }
});

test('saving an app’s access says Saving…, then shows what was saved with no setup steps (B346)', async ({
  page,
}, info) => {
  test.slow();
  await page.setViewportSize(DESKTOP);
  await seed(page, '/__p6_fixture/views');
  try {
    await page.goto('/app-v2/settings/apps');
    await page
      .getByRole('region', { name: /^Your apps$/ })
      .getByRole('link', { name: /^Counter/ })
      .click();
    const article = page.getByRole('article', { name: 'Counter' });
    await expect(article).toBeVisible();
    await article.getByRole('button', { name: 'Choose', exact: true }).click();
    const sheet = page.getByRole('dialog', {
      name: 'Change what Counter can do',
    });
    await sheet.getByRole('radio', { name: /^Read only/ }).check();
    await sheet.getByRole('button', { name: 'Save', exact: true }).click();
    await expect(sheet).toBeHidden();
    await expect(article.getByText(/actions on · Read only$/)).toBeVisible();
    await expect(page.getByRole('list', { name: 'Setup steps' })).toHaveCount(
      0,
    );
    await expect(page.getByText('Saving…')).toHaveCount(0);
    await everywhere(page, info, 'app-access-saved');
  } finally {
    await seed(page, '/__p6_fixture/views/remove');
  }
});

test('Google reads Connected with Gmail and Calendar on after its first sign-in (F29)', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  const signedIn = await seed(page, '/__s12_fixture/google/signed-in');
  expect(signedIn).toEqual({ gmail: true, calendar: true });
  try {
    // Apps › Google is ready to use, never "Off · Turn on".
    await page.goto('/app-v2/settings/apps/google');
    const article = page.getByRole('article', { name: 'Google account' });
    await expect(article).toContainText(/Built in\s*Ready/);
    await expect(article).not.toContainText('Turn on');
    await everywhere(page, info, 'google-ready');
    // Its own settings: Connected, with Gmail and Calendar on.
    await article.getByRole('link', { name: 'Open', exact: true }).click();
    await expect(
      page.getByText('Connected', { exact: true }).filter({ visible: true }),
    ).toBeVisible();
    await expect(
      page.getByRole('switch', { name: 'Gmail access' }),
    ).toBeChecked();
    await expect(
      page.getByRole('switch', { name: 'Calendar access' }),
    ).toBeChecked();
    await everywhere(page, info, 'google-connected');
  } finally {
    await seed(page, '/__s12_fixture/google/remove');
  }
});

test('an approval for an app tool reads as the app and the action, and a phone shows no keyboard hint (B388, B389)', async ({
  page,
}, info) => {
  test.slow();
  await page.setViewportSize(DESKTOP);
  await seed(page, '/__p6_fixture/views');
  try {
    await newConversation(page);
    await send(page, 'Start a tally unnamed app tool fixture');
    const card = page.getByRole('complementary', {
      name: 'Approval required for Create tally in Counter',
    });
    await expect(
      card.getByText('Allow Counter to create a tally?'),
    ).toBeVisible();
    await expect(card).not.toContainText('mcp_');
    // Its step row names the app too, never "MCP counter create tally" (B391).
    const log = page.getByRole('log', { name: 'Conversation', exact: true });
    // The steps fold under one line, which reads "Waiting for your approval"
    // once the turn waits on the card: open them to read the step.
    const activity = log.getByRole('group', { name: 'Tool activity' });
    const fold = activity.locator('summary').first();
    await fold.click();
    await expect(
      activity
        .getByRole('list', { name: 'Steps' })
        .getByText(/Counter · Create tally/),
    ).toBeVisible();
    // (Its raw name stays in the step's own collapsed details.)
    await expect(log).not.toContainText('MCP');
    await fold.click();
    const approve = card.getByRole('button', { name: 'Approve', exact: true });
    // Ctrl ↵ where a keyboard is likely: a fine pointer (B389), never touch.
    if (await page.evaluate(() => matchMedia('(pointer: fine)').matches))
      await expect(approve).not.toHaveText('Approve');
    else await expect(approve).toHaveText('Approve');
    await everywhere(page, info, 'approval-plain-title', async () => {
      if ((page.viewportSize()?.width ?? 0) < 641)
        await expect(approve).toHaveText('Approve');
    });
    await card.getByRole('button', { name: 'Deny', exact: true }).click();
    await expect(card).toHaveCount(0);
  } finally {
    await seed(page, '/__p6_fixture/views/remove');
  }
});

test('maths in a reply is typeset inline and as display, and a price stays text', async ({
  page,
}, info) => {
  await page.setViewportSize(DESKTOP);
  await newConversation(page);
  await send(page, 'Show me the area maths fixture');
  const log = page.getByRole('log', { name: 'Conversation', exact: true });
  await expect(log.getByRole('math')).toHaveCount(4);
  await expect(log).toContainText('and the plan costs $5 a month.');
  await expect(log.getByText('$x^2$', { exact: true })).toBeVisible();
  await everywhere(page, info, 'maths-message');
});
