import { expect, test } from '@playwright/test';
import { COMPANY_SPACE } from './fake-brain';
import { openConsole, ORIGIN } from './serve';

test('people: search, a confirmed suspension, and no actions on yourself', async ({
  page,
}) => {
  const { api, violations } = await openConsole(page, '/console/');
  await expect(page).toHaveURL(`${ORIGIN}/console/people`);
  await expect(page.getByRole('heading', { name: 'People' })).toBeVisible();

  const self = page.getByRole('button', { name: "You can't suspend yourself" });
  await expect(self).toBeDisabled();
  await self.hover();
  await expect(page.getByRole('tooltip')).toContainText(
    "You can't suspend yourself",
  );

  await page.getByRole('searchbox', { name: 'Search people' }).fill('ada');
  const people = page.getByRole('list', { name: 'People' });
  await expect(people.getByRole('listitem')).toHaveCount(1);

  await page.getByRole('button', { name: 'Suspend Ada Lovelace' }).click();
  const confirm = page.getByRole('alertdialog');
  await expect(confirm).toContainText(
    'Suspending signs Ada Lovelace out everywhere',
  );
  await confirm.getByRole('button', { name: 'Suspend' }).click();
  await expect(people.getByText('Suspended')).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Resume Ada Lovelace' }),
  ).toBeVisible();
  expect(api.calls.map((call) => `${call.method} ${call.path}`)).toContain(
    'POST /api/admin/people/22222222-2222-4222-8222-222222222222/suspend',
  );
  expect(violations).toEqual([]);
});

test('policy: a change is saved as the server document, with what groups still get', async ({
  page,
}) => {
  const { api, violations } = await openConsole(page, '/console/policy');
  await expect(page.getByRole('heading', { name: 'Policy' })).toBeVisible();

  await page.getByRole('switch', { name: 'Local models' }).click();
  await page.getByRole('radio', { name: 'Hands-off' }).click();
  await expect(page.getByText('2 unsaved changes')).toBeVisible();
  await page.getByRole('button', { name: 'Save policy' }).click();

  await expect(
    page.getByText('Some groups still get what you just took away'),
  ).toBeVisible();
  await expect(page.getByText('Engineering')).toBeVisible();
  const saved = api.calls.find(
    (call) => call.method === 'PUT' && call.path === '/api/admin/policy',
  );
  expect(saved?.body).toEqual({
    version: 3,
    models: { personal_api_keys: false, local_models: false },
    tools: { disabled_groups: ['computer_use', 'shell'] },
    approvals: { max_level: 'full' },
    apps: { mcp_servers: 'allow-list', allowed_mcp_servers: ['jira'] },
  });
  await expect(page.getByText('unsaved change')).toHaveCount(0);

  await page.getByRole('tab', { name: 'Group overrides' }).click();
  await expect(page.getByText('What Engineering gets on top')).toBeVisible();
  await expect(page.getByText('Offline days: 30 days')).toBeVisible();
  expect(violations).toEqual([]);
});

test('audit: plain-words events, a filter, older pages, the integrity check and the export link', async ({
  page,
}) => {
  const { api, violations } = await openConsole(page, '/console/audit');
  const events = page.getByRole('list', { name: 'Audit events' });
  await expect(
    events.getByText('Sam Rivera suspended Lin Okafor'),
  ).toBeVisible();
  await expect(events.getByRole('listitem')).toHaveCount(6);

  await page.getByRole('button', { name: 'Show older events' }).click();
  await expect(events.getByRole('listitem')).toHaveCount(10);
  await expect(
    events.getByText('Lin Okafor was refused sign-in'),
  ).toBeVisible();

  await page.getByRole('button', { name: 'Check integrity' }).click();
  await expect(page.getByText('The audit log is intact')).toBeVisible();

  await page.getByLabel('What').selectOption({ label: 'Signed in' });
  await expect(events.getByRole('listitem')).toHaveCount(3);
  expect(
    api.calls.some((call) => call.path.includes('action=session.signed_in')),
  ).toBe(true);
  await expect(
    page.getByRole('link', { name: 'Export as JSON Lines' }),
  ).toHaveAttribute('href', '/api/admin/audit/export?action=session.signed_in');
  expect(violations).toEqual([]);
});

test('knowledge: an upload that moves on to ready, a fact and a merge', async ({
  page,
}) => {
  // The document list reads itself again every 5 s while one is being read.
  test.setTimeout(60_000);
  const { api, violations } = await openConsole(page, '/console/knowledge');
  await expect(page.getByRole('heading', { name: 'Knowledge' })).toBeVisible();
  const spaces = page.getByRole('list', { name: 'Spaces' });
  await expect(spaces.getByRole('listitem')).toHaveCount(3);
  await page.getByRole('button', { name: 'Open Company' }).click();

  await page.getByLabel('Choose files to upload').setInputFiles({
    name: 'onboarding.md',
    mimeType: 'text/markdown',
    buffer: Buffer.from('# Onboarding\n\nWelcome to the company.'),
  });
  const uploaded = page
    .getByRole('list', { name: 'Documents' })
    .getByRole('listitem')
    .filter({ hasText: 'onboarding.md' });
  await expect(uploaded).toContainText('Queued');
  await expect(uploaded).toContainText('Ready', { timeout: 20_000 });
  expect(api.calls.map((call) => `${call.method} ${call.path}`)).toContain(
    `POST /api/brain/spaces/${COMPANY_SPACE}/documents`,
  );

  await page.getByRole('tab', { name: 'Facts' }).click();
  await page.getByRole('button', { name: 'Add fact' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel(/^Fact/).fill('Visitors sign in at reception.');
  await dialog.getByRole('button', { name: 'Add fact' }).click();
  await expect(page.getByRole('list', { name: 'Facts' })).toContainText(
    'Visitors sign in at reception.',
  );

  await page.getByRole('tab', { name: 'Knowledge graph' }).click();
  const merge = page.getByRole('button', {
    name: 'Merge Atlas app into another entry',
  });
  await merge.click();
  await dialog.getByLabel('Into').selectOption({ label: 'Atlas (Project)' });
  await dialog.getByRole('button', { name: 'Merge' }).click();
  const knowledge = page.getByRole('list', { name: 'Company knowledge' });
  await expect(knowledge).toContainText('Also called Atlas app');
  const merged = knowledge.getByText('Atlas app', { exact: true });
  await expect(merged).toHaveCount(0);
  expect(violations).toEqual([]);
});

test('every page fits a 390 px phone without sideways scrolling', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openConsole(page, '/console/people');
  for (const path of [
    'people',
    'devices',
    'models?tab=endpoints',
    'models?tab=catalog',
    'models?tab=budgets',
    'models?tab=usage',
    'policy?tab=company',
    'policy?tab=groups',
    'policy?tab=history',
    'audit',
    'knowledge',
    `knowledge?space=${COMPANY_SPACE}&tab=documents`,
    `knowledge?space=${COMPANY_SPACE}&tab=facts`,
    `knowledge?space=${COMPANY_SPACE}&tab=graph`,
  ]) {
    await page.goto(`${ORIGIN}/console/${path}`);
    await expect(page.locator('.console-page h1')).toBeVisible();
    await expect(page.getByRole('status', { name: /^Loading/ })).toHaveCount(0);
    // The tab bar too: all six tabs fit, none is scrolled out of sight.
    const overflow = await page.evaluate(() =>
      [...document.querySelectorAll('html, .console-main, nav')]
        .filter((element) => element.scrollWidth > element.clientWidth)
        .map((element) => element.className || element.tagName),
    );
    expect(overflow, path).toEqual([]);
  }
});
