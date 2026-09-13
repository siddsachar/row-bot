import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';
import { blockFixtureServiceWorkers } from './unified-helpers';
import { captureBrowserDownload } from './download-helpers';
import type { Page } from '@playwright/test';

async function seed(page: Page, kind: 'tasks' | 'tools', state = 'populated') {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated Phase 4 runner');
  const response = await page.request.post(`/__p4_fixture/${kind}/${state}`, {
    headers: { 'X-Fixture-Token': token, Origin: new URL(base).origin },
  });
  expect(response.ok(), await response.text()).toBe(true);
}

test.use({ serviceWorkers: 'allow', nativeNetwork: true });
test.beforeEach(async ({ context, page }) => {
  await blockFixtureServiceWorkers(context);
  await page.addInitScript(() => {
    if (window !== window.top) return;
    localStorage.setItem(
      'row-bot.appearance.v1',
      JSON.stringify({ version: 1, appearance: 'system', accent: 'blue' }),
    );
  });
});

test('Phase 4 workflow create and edit retain saved fields without starting a run', async ({
  page,
}, info) => {
  await seed(page, 'tasks', 'empty');
  await page.goto('/app-v2/');
  await page.getByRole('button', { name: 'Workflows', exact: true }).click();
  await page.getByRole('button', { name: 'New workflow', exact: true }).click();
  const editor = page.getByRole('form', { name: 'Create task', exact: true });
  const name = `Phase 4 browser workflow ${info.project.name}`;
  await editor.getByRole('textbox', { name: 'Name', exact: true }).fill(name);
  await editor
    .getByRole('textbox', { name: 'Prompt 1', exact: true })
    .fill('Summarize synthetic sample only');
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `workflow-editor-${appearance}`);
    await accessibility(page, info, `workflow-editor-${appearance}`);
  }
  await editor.getByRole('button', { name: 'Save task', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: 'Workflows', exact: true }),
  ).toBeVisible();
  await page.getByRole('searchbox', { name: 'Search tasks' }).fill(name);
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  const row = page.locator('.settings-results > li').filter({ hasText: name });
  await expect(row).toHaveCount(1);
  await row.locator('summary').click();
  await expect(
    row.getByText('No recorded run time', { exact: true }),
  ).toBeVisible();
  await row.getByRole('button', { name: 'Edit workflow', exact: true }).click();
  const edit = page.getByRole('form', { name: 'Edit task', exact: true });
  await expect(
    edit.getByRole('textbox', { name: 'Name', exact: true }),
  ).toHaveValue(name);
  await expect(
    edit.getByRole('textbox', { name: 'Prompt 1', exact: true }),
  ).toHaveValue('Summarize synthetic sample only');
  await edit
    .getByRole('textbox', { name: 'Description', exact: true })
    .fill('Edited in the unified client');
  await edit.getByRole('button', { name: 'Save task', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: 'Workflows', exact: true }),
  ).toBeVisible();
  await page.getByRole('searchbox', { name: 'Search tasks' }).fill(name);
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.locator('.settings-results > li')).toHaveCount(1);
  await page.locator('.settings-results summary').click();
  await expect(
    page.getByText('Edited in the unified client', { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText('No recorded run time', { exact: true }),
  ).toBeVisible();
  await screenshot(page, info, 'workflow-saved');
});

test('Phase 4 workflow graph saves stable steps without starting a run', async ({
  page,
}, info) => {
  await seed(page, 'tasks');
  await page.goto('/app-v2/');
  await page.getByRole('button', { name: 'Workflows', exact: true }).click();
  await page
    .getByRole('searchbox', { name: 'Search tasks' })
    .fill('Phase 4 saved task 103');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.locator('.settings-results > li')).toHaveCount(1);
  await page.locator('.settings-results summary').click();
  await page
    .getByRole('button', { name: 'Edit workflow steps', exact: true })
    .click();
  const order = page.getByRole('list', { name: 'Workflow step order' });
  await expect(order.getByRole('button')).toHaveCount(0);
  await page.getByRole('button', { name: 'Add step', exact: true }).click();
  const original = await order.getByRole('button').first().innerText();
  await page
    .getByRole('textbox', { name: 'Prompt', exact: true })
    .fill('Synthetic graph edit');
  await page.getByRole('button', { name: 'Add step', exact: true }).click();
  await page
    .getByRole('textbox', { name: 'Prompt', exact: true })
    .fill('Second synthetic step');
  await page.getByRole('button', { name: 'Move step up', exact: true }).click();
  await expect(order.getByRole('button').last()).toContainText(
    original.replace(/^1\./, '2.'),
  );
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `workflow-graph-${appearance}`);
    await accessibility(page, info, `workflow-graph-${appearance}`);
  }
  await page.getByRole('button', { name: 'Save graph', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: 'Workflows', exact: true }),
  ).toBeVisible();
  await page
    .getByRole('searchbox', { name: 'Search tasks' })
    .fill('Phase 4 saved task 103');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.locator('.settings-results > li')).toHaveCount(1);
  await page.locator('.settings-results summary').click();
  await expect(
    page.getByText('No recorded run time', { exact: true }),
  ).toBeVisible();
  await page
    .getByRole('button', { name: 'Edit workflow steps', exact: true })
    .click();
  await expect(order.getByRole('button')).toHaveCount(2);
  await expect(
    page.getByRole('textbox', { name: 'Prompt', exact: true }),
  ).toHaveValue('Second synthetic step');
  await order.getByRole('button').last().click();
  await expect(
    page.getByRole('textbox', { name: 'Prompt', exact: true }),
  ).toHaveValue('Synthetic graph edit');
});

test('Phase 4 workflow settings require review and preserve saved configuration', async ({
  page,
}, info) => {
  await seed(page, 'tasks');
  await page.goto('/app-v2/');
  await page.getByRole('button', { name: 'Workflows', exact: true }).click();
  await page
    .getByRole('searchbox', { name: 'Search tasks' })
    .fill('Phase 4 saved task 102');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.locator('.settings-results > li')).toHaveCount(1);
  await page.locator('.settings-results summary').click();
  await page
    .getByRole('button', { name: 'Workflow settings', exact: true })
    .click();
  const editor = page.getByRole('region', {
    name: 'Workflow settings editor',
    exact: true,
  });
  await editor
    .getByRole('textbox', { name: 'Concurrency group', exact: true })
    .fill('synthetic-browser-group');
  await editor
    .getByRole('combobox', { name: 'Trigger', exact: true })
    .selectOption('webhook');
  const home = page
    .getByRole('navigation', { name: 'Workspace navigation', exact: true })
    .getByRole('link', { name: 'Home', exact: true });
  if (!(await home.isVisible()))
    await page
      .getByRole('button', { name: 'Toggle navigation', exact: true })
      .click();
  await home.click();
  await expect(editor).toHaveCount(0);
  await page.getByRole('button', { name: 'Workflows', exact: true }).click();
  await expect(
    editor.getByRole('textbox', { name: 'Concurrency group', exact: true }),
  ).toHaveValue('synthetic-browser-group');
  await expect(
    editor.getByRole('combobox', { name: 'Trigger', exact: true }),
  ).toHaveValue('webhook');
  await expect(
    editor.getByRole('button', { name: 'Save settings', exact: true }),
  ).toBeDisabled();
  await editor
    .getByRole('button', { name: 'Review settings', exact: true })
    .click();
  await expect(
    editor.getByRole('button', { name: 'Save settings', exact: true }),
  ).toBeEnabled();
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `workflow-settings-${appearance}`);
    await accessibility(page, info, `workflow-settings-${appearance}`);
  }
  await editor
    .getByRole('button', { name: 'Save settings', exact: true })
    .click();
  await expect(
    page.getByRole('heading', { name: 'Workflows', exact: true }),
  ).toBeVisible();
  await page
    .getByRole('searchbox', { name: 'Search tasks' })
    .fill('Phase 4 saved task 102');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.locator('.settings-results > li')).toHaveCount(1);
  await page.locator('.settings-results summary').click();
  await expect(
    page.getByText('No recorded run time', { exact: true }),
  ).toBeVisible();
  await page
    .getByRole('button', { name: 'Workflow settings', exact: true })
    .click();
  await expect(
    editor.getByRole('textbox', { name: 'Concurrency group', exact: true }),
  ).toHaveValue('synthetic-browser-group');
  await expect(
    editor.getByRole('combobox', { name: 'Trigger', exact: true }),
  ).toHaveValue('webhook');
  const download = await captureBrowserDownload(page, () =>
    editor
      .getByRole('button', {
        name: 'Download private webhook configuration',
        exact: true,
      })
      .click(),
  );
  expect(download.name).toBe('workflow-webhook.json');
  expect(download.mimeType).toBe('application/json');
  expect(download.bytes.length).toBeLessThanOrEqual(65_536);
  const webhook = JSON.parse(download.bytes.toString('utf8')) as {
    method: string;
    note: string;
    relative_url: string;
  };
  expect(webhook.method).toBe('POST');
  expect(webhook.note).toContain('Keep this file private.');
  const webhookUrl = new URL(webhook.relative_url, 'http://row-bot.local');
  expect(webhookUrl.pathname).toBe('/api/webhook/p4-task-102');
  const secret = webhookUrl.searchParams.get('secret');
  expect(secret).toBeTruthy();
  expect(await editor.textContent()).not.toContain(secret!);
  await expect(
    editor.getByText(
      'The private webhook configuration was downloaded. Keep it private.',
      { exact: true },
    ),
  ).toBeVisible();
});

test('Phase 4 Home discovers saved tasks with bounded paging and recorded details', async ({
  page,
}, info) => {
  await seed(page, 'tasks');
  await page.goto('/app-v2/');
  await page.getByRole('button', { name: 'Workflows', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: 'Workflows', exact: true }),
  ).toBeVisible();
  await page
    .getByRole('searchbox', { name: 'Search tasks' })
    .fill('Phase 4 saved task');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(
    page.getByText('105 matching tasks', { exact: true }),
  ).toBeVisible();
  await expect(page.locator('.settings-results > li')).toHaveCount(50);
  await page
    .getByRole('button', { name: 'Load more tasks', exact: true })
    .click();
  await expect(page.locator('.settings-results > li')).toHaveCount(100);
  await page
    .getByRole('button', { name: 'Load more tasks', exact: true })
    .click();
  await expect(page.locator('.settings-results > li')).toHaveCount(105);
  await page
    .getByRole('searchbox', { name: 'Search tasks' })
    .fill('Phase 4 saved task 104');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.locator('.settings-results > li')).toHaveCount(1);
  await page.locator('.settings-results summary').click();
  await expect(
    page.getByText('No recorded status', { exact: true }),
  ).toBeVisible();
  await expect(page.getByText('Reminder', { exact: true })).toBeVisible();
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await expect(page.locator('html')).toHaveAttribute(
      'data-theme',
      appearance,
    );
    await assertNoOverflow(page);
    await screenshot(page, info, `saved-task-${appearance}`);
    await accessibility(page, info, `saved-task-${appearance}`);
  }
  await page
    .getByRole('combobox', { name: 'Task status' })
    .selectOption('enabled');
  await expect(page.getByText('No matching saved tasks')).toBeVisible();
});

test('Phase 4 explicit workflow run records history and opens the unified conversation', async ({
  page,
}, info) => {
  await seed(page, 'tasks');
  await page.goto('/app-v2/');
  await page.getByRole('button', { name: 'Workflows', exact: true }).click();
  await page
    .getByRole('searchbox', { name: 'Search tasks' })
    .fill('Phase 4 saved task 104');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.locator('.settings-results > li')).toHaveCount(1);
  await page.locator('.settings-results summary').click();
  await page
    .getByRole('button', { name: 'Run and history', exact: true })
    .click();
  const runs = page.getByRole('region', {
    name: 'Task runs and approvals',
    exact: true,
  });
  await expect(
    runs.getByRole('button', { name: 'Run now', exact: true }),
  ).toBeEnabled();
  const before = await runs.getByRole('button', { name: /^Show run / }).count();
  await screenshot(page, info, 'workflow-run-review');
  await accessibility(page, info, 'workflow-run-review');
  await runs.getByRole('button', { name: 'Run now', exact: true }).click();
  await expect(
    runs.getByRole('region', { name: 'Selected run', exact: true }),
  ).toContainText('Saved status: completed.');
  await expect(
    runs.getByRole('button', { name: 'Run now', exact: true }),
  ).toBeDisabled();
  await expect(runs.getByRole('button', { name: /^Show run / })).toHaveCount(
    before + 1,
  );
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `workflow-run-${appearance}`);
    await accessibility(page, info, `workflow-run-${appearance}`);
  }
  await runs
    .getByRole('button', { name: 'Open conversation', exact: true })
    .click();
  await expect(page).toHaveURL(/\/app-v2\/conversations\/[a-f0-9-]+$/);
  await expect(
    page.getByRole('region', { name: 'Conversation', exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole('textbox', { name: 'Message', exact: true }),
  ).toHaveCount(1);
});

test('Phase 4 Settings discovers passive tools with source filters and unknown readiness', async ({
  page,
}, info) => {
  await seed(page, 'tools');
  await page.goto('/app-v2/');
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByRole('link', { name: 'Tools', exact: true }).click();
  await page
    .getByRole('combobox', { name: 'Tool source' })
    .selectOption('core');
  await page.getByRole('searchbox', { name: 'Search tools' }).fill('p4-tool-');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(
    page.getByText('105 matching recorded entries', { exact: true }),
  ).toBeVisible();
  await expect(page.locator('.settings-results > li')).toHaveCount(50);
  await page
    .getByRole('button', { name: 'Load more tools', exact: true })
    .click();
  await expect(page.locator('.settings-results > li')).toHaveCount(100);
  await page
    .getByRole('button', { name: 'Load more tools', exact: true })
    .click();
  await expect(page.locator('.settings-results > li')).toHaveCount(105);
  await page
    .getByRole('searchbox', { name: 'Search tools' })
    .fill('p4-tool-104');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.locator('.settings-results > li')).toHaveCount(1);
  await page.locator('.settings-results summary').click();
  await expect(
    page.getByText('Runtime readiness', { exact: true }),
  ).toBeVisible();
  await expect(
    page.locator('.settings-results dd').filter({ hasText: /^Unknown$/ }),
  ).toHaveCount(3);
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await expect(page.locator('html')).toHaveAttribute(
      'data-theme',
      appearance,
    );
    await assertNoOverflow(page);
    await screenshot(page, info, `saved-tool-${appearance}`);
    await accessibility(page, info, `saved-tool-${appearance}`);
  }
  await seed(page, 'tools', 'empty');
  await page
    .getByRole('button', { name: 'Reload cached tools', exact: true })
    .click();
  await expect(page.getByText('No matching cached tools')).toBeVisible();
});
