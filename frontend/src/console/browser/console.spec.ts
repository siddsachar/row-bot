import { expect, test } from '@playwright/test';
import { HR_POLICIES } from './fake-connectors';
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

test('connectors: add and test one, map someone unmatched, then pause it', async ({
  page,
}) => {
  const { api, violations } = await openConsole(page, '/console/connectors');
  await expect(page.getByRole('heading', { name: 'Connectors' })).toBeVisible();
  const connectors = page.getByRole('list', { name: 'Connectors' });
  await expect(connectors.getByText('Needs a look')).toHaveCount(2);

  await page.getByRole('button', { name: 'Add connector' }).click();
  const dialog = page.getByRole('dialog');
  await dialog
    .getByLabel(/^Tenant id/)
    .fill('0b1c2d3e-4f5a-4b6c-8d7e-9f0a1b2c3d4e');
  await dialog
    .getByLabel(/^Application \(client\) id/)
    .fill('1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d');
  await dialog.getByLabel(/^Client secret/).fill('test-client-secret');
  await dialog.getByLabel(/^Drive id/).fill('b!finance-drive');
  await dialog.getByLabel(/^Name/).fill('Finance');
  await dialog.getByRole('button', { name: 'Connect and test' }).click();
  await expect(page.getByRole('dialog', { name: 'Connected' })).toBeVisible();
  await page.getByRole('button', { name: 'Done' }).click();
  await expect(connectors.getByText('Finance', { exact: true })).toBeVisible();

  await page
    .getByRole('button', { name: 'Show details for HR policies' })
    .click();
  await expect(page.getByText('4 files only admins can read')).toBeVisible();
  await page
    .getByRole('button', { name: 'Map HR Members to a Row-Bot group' })
    .click();
  await page
    .getByRole('dialog')
    .getByLabel(/^HR Members/)
    .fill('Sales');
  await page.getByRole('button', { name: 'Save the map' }).click();
  await expect(
    page.getByText(
      'SharePoint site group · Mapped to Sales: applies when its files are read again',
    ),
  ).toBeVisible();

  await page.getByRole('button', { name: 'Pause HR policies' }).click();
  await page
    .getByRole('alertdialog')
    .getByRole('button', { name: 'Pause' })
    .click();
  await expect(
    page.getByRole('button', { name: 'Resume HR policies' }),
  ).toBeVisible();
  const paths = api.calls.map((call) => `${call.method} ${call.path}`);
  expect(paths).toContain(`PATCH /api/admin/connectors/${HR_POLICIES}`);
  expect(paths).toContain(`POST /api/admin/connectors/${HR_POLICIES}/pause`);
  expect(
    JSON.stringify(api.calls.filter((call) => call.method === 'GET')),
  ).not.toContain('test-client-secret');
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
    'connectors',
  ]) {
    await page.goto(`${ORIGIN}/console/${path}`);
    await expect(page.locator('.console-page h1')).toBeVisible();
    await expect(page.getByRole('status', { name: /^Loading/ })).toHaveCount(0);
    const overflow = await page.evaluate(() =>
      [document.documentElement, ...document.querySelectorAll('.console-main')]
        .filter((element) => element.scrollWidth > element.clientWidth)
        .map((element) => element.className || element.tagName),
    );
    expect(overflow, path).toEqual([]);
  }
});
