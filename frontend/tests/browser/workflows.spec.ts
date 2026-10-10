import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';
import { blockFixtureServiceWorkers } from './unified-helpers';
import { captureBrowserDownload } from './download-helpers';
import type { Page, TestInfo } from '@playwright/test';

async function seed(page: Page, kind: 'tasks' | 'tools', state = 'populated') {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated Phase 4 runner');
  const response = await page.request.post(`/__p4_fixture/${kind}/${state}`, {
    headers: { 'X-Fixture-Token': token, Origin: new URL(base).origin },
  });
  expect(response.ok(), await response.text()).toBe(true);
}

async function searchWorkflows(page: Page, query: string) {
  await page.getByRole('searchbox', { name: 'Search workflows' }).fill(query);
  await page
    .getByRole('button', { name: 'Search workflows', exact: true })
    .click();
}

function workflowCards(page: Page) {
  return page.locator('.workflow-grid > li');
}

async function chooseWorkflowAction(
  page: Page,
  workflowName: string,
  action: string,
) {
  const card = workflowCards(page).filter({ hasText: workflowName });
  await card
    .getByRole('button', {
      name: `More actions for ${workflowName}`,
      exact: true,
    })
    .click();
  await page.getByRole('menuitem', { name: action, exact: true }).click();
}

test.use({ serviceWorkers: 'allow', nativeNetwork: true });
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
});

test('Phase 4 workflow create and edit retain saved fields without starting a run', async ({
  page,
}, info) => {
  await seed(page, 'tasks', 'empty');
  await page.goto('/app-v2/?tab=workflows');
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
  await editor
    .getByRole('button', { name: 'Save workflow', exact: true })
    .click();
  await expect(
    page.getByRole('heading', { name: 'Workflows', exact: true }),
  ).toBeVisible();
  await searchWorkflows(page, name);
  const row = workflowCards(page).filter({ hasText: name });
  await expect(row).toHaveCount(1);
  await expect(row.getByText('Never run', { exact: true })).toBeVisible();
  await row
    .getByRole('button', { name: `Edit workflow: ${name}`, exact: true })
    .click();
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
  await edit.getByRole('button', { name: 'Cancel', exact: true }).click();
  const recovery = page.getByRole('region', {
    name: 'Continue editing workflows',
    exact: true,
  });
  const discard = recovery.getByRole('button', {
    name: 'Discard changes',
    exact: true,
  });
  await discard.click();
  const confirmation = page.getByRole('alertdialog', {
    name: `Discard changes to ${name}?`,
    exact: true,
  });
  await expect(confirmation).toBeVisible();
  await expect(
    confirmation.getByRole('button', { name: 'Cancel', exact: true }),
  ).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(confirmation).toHaveCount(0);
  await expect(discard).toBeFocused();
  await recovery
    .getByRole('button', { name: 'Continue editing', exact: true })
    .click();
  await expect(
    edit.getByRole('textbox', { name: 'Description', exact: true }),
  ).toHaveValue('Edited in the unified client');
  await edit
    .getByRole('button', { name: 'Save workflow', exact: true })
    .click();
  await expect(
    page.getByRole('heading', { name: 'Workflows', exact: true }),
  ).toBeVisible();
  await searchWorkflows(page, name);
  await expect(workflowCards(page)).toHaveCount(1);
  // Phones drop a row's description (it stays the row's title).
  const phone = info.project.use.viewport!.width < 640;
  const description = page.getByText('Edited in the unified client', {
    exact: true,
  });
  if (phone)
    await expect(description).toHaveAttribute(
      'title',
      'Edited in the unified client',
    );
  else await expect(description).toBeVisible();
  await expect(page.getByText('Never run', { exact: true })).toBeVisible();
  await screenshot(page, info, 'workflow-saved');
});

test('Phase 4 workflow graph saves stable steps without starting a run', async ({
  page,
}, info) => {
  await seed(page, 'tasks');
  await page.goto('/app-v2/?tab=workflows');
  await searchWorkflows(page, 'Phase 4 saved task 103');
  await expect(workflowCards(page)).toHaveCount(1);
  await chooseWorkflowAction(
    page,
    'Phase 4 saved task 103',
    'Edit workflow steps',
  );
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
  await searchWorkflows(page, 'Phase 4 saved task 103');
  await expect(workflowCards(page)).toHaveCount(1);
  await expect(page.getByText('Never run', { exact: true })).toBeVisible();
  await chooseWorkflowAction(
    page,
    'Phase 4 saved task 103',
    'Edit workflow steps',
  );
  await expect(order.getByRole('button')).toHaveCount(2);
  await expect(
    page.getByRole('textbox', { name: 'Prompt', exact: true }),
  ).toHaveValue('Second synthetic step');
  await order.getByRole('button').last().click();
  await expect(
    page.getByRole('textbox', { name: 'Prompt', exact: true }),
  ).toHaveValue('Synthetic graph edit');
});

test('Phase 4 workflow settings save in one reviewed step and preserve saved configuration', async ({
  page,
}, info) => {
  await seed(page, 'tasks');
  await page.goto('/app-v2/?tab=workflows');
  await searchWorkflows(page, 'Phase 4 saved task 102');
  await expect(workflowCards(page)).toHaveCount(1);
  await chooseWorkflowAction(
    page,
    'Phase 4 saved task 102',
    'Workflow settings',
  );
  const editor = page.getByRole('region', {
    name: 'Workflow settings editor',
    exact: true,
  });
  await editor
    .getByRole('textbox', { name: 'Queue name', exact: true })
    .fill('synthetic-browser-group');
  await editor
    .getByRole('combobox', { name: 'Trigger', exact: true })
    .selectOption('webhook');
  await page.getByRole('tab', { name: 'Knowledge', exact: true }).click();
  await expect(editor).toHaveCount(0);
  await page.getByRole('tab', { name: 'Workflows', exact: true }).click();
  await expect(
    page.getByRole('tab', { name: 'Workflows', exact: true }),
  ).toHaveAttribute('aria-selected', 'true');
  await expect(
    editor.getByRole('textbox', { name: 'Queue name', exact: true }),
  ).toHaveValue('synthetic-browser-group');
  await expect(
    editor.getByRole('combobox', { name: 'Trigger', exact: true }),
  ).toHaveValue('webhook');
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
  await searchWorkflows(page, 'Phase 4 saved task 102');
  await expect(workflowCards(page)).toHaveCount(1);
  await expect(page.getByText('Never run', { exact: true })).toBeVisible();
  await chooseWorkflowAction(
    page,
    'Phase 4 saved task 102',
    'Workflow settings',
  );
  await expect(
    editor.getByRole('textbox', { name: 'Queue name', exact: true }),
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
    relative_url_with_secret: string;
    headers: Record<string, string>;
  };
  expect(webhook.method).toBe('POST');
  expect(webhook.note).toContain('Keep this file private.');
  // The secret travels in a header, not in the address (B132).
  expect(webhook.relative_url).toBe('/api/webhook/p4-task-102');
  const secret = webhook.headers['X-Row-Bot-Webhook-Secret'];
  expect(secret).toBeTruthy();
  const olderForm = new URL(
    webhook.relative_url_with_secret,
    'http://row-bot.local',
  );
  expect(olderForm.pathname).toBe('/api/webhook/p4-task-102');
  expect(olderForm.searchParams.get('secret')).toBe(secret);
  expect(await editor.textContent()).not.toContain(secret!);
  await expect(
    editor.getByText(
      'The private webhook configuration was downloaded. Keep it private.',
      { exact: true },
    ),
  ).toBeVisible();
  // The address shows with its secret hidden; nothing is public until the
  // person asks for it (parity rows 20, 52). Never clicked here.
  const address = editor.getByRole('group', {
    name: 'Webhook address',
    exact: true,
  });
  await expect(address).toContainText('/api/webhook/p4-task-102');
  await expect(address).toContainText('X-Row-Bot-Webhook-Secret: ••••');
  await expect(
    address.getByRole('button', { name: 'Copy address', exact: true }),
  ).toBeVisible();
  await expect(
    editor.getByText('Not reachable from the internet.', { exact: false }),
  ).toBeVisible();
  expect(await editor.textContent()).not.toContain(secret!);
});

test('Phase 4 Home discovers saved tasks with bounded paging and recorded details', async ({
  page,
}, info) => {
  await seed(page, 'tasks');
  await page.goto('/app-v2/?tab=workflows');
  await expect(
    page.getByRole('heading', { name: 'Workflows', exact: true }),
  ).toBeVisible();
  await searchWorkflows(page, 'Phase 4 saved task');
  await expect(page.getByText('105 matching', { exact: true })).toBeVisible();
  await expect(workflowCards(page)).toHaveCount(50);
  await page
    .getByRole('button', { name: 'Load more workflows', exact: true })
    .click();
  await expect(workflowCards(page)).toHaveCount(100);
  await page
    .getByRole('button', { name: 'Load more workflows', exact: true })
    .click();
  await expect(workflowCards(page)).toHaveCount(105);
  await searchWorkflows(page, 'Phase 4 saved task 104');
  await expect(workflowCards(page)).toHaveCount(1);
  // Phones drop the run sparkline; the meta line still says it never ran.
  if (info.project.use.viewport!.width >= 640)
    await expect(page.getByText('No runs yet', { exact: true })).toBeVisible();
  await expect(page.getByText('Reminder', { exact: true })).toBeVisible();
  const card = workflowCards(page).first();
  const routineActions = [
    card.getByRole('button', {
      name: 'Run workflow: Phase 4 saved task 104',
      exact: true,
    }),
    card.getByRole('button', {
      name: 'Edit workflow: Phase 4 saved task 104',
      exact: true,
    }),
    card.getByRole('button', {
      name: 'More actions for Phase 4 saved task 104',
      exact: true,
    }),
  ];
  for (const action of routineActions) {
    await expect(action).toBeVisible();
    if (info.project.use.hasTouch)
      expect((await action.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  }
  await routineActions[2].click();
  await expect(
    page.getByRole('menuitem', { name: 'Run history', exact: true }),
  ).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(routineActions[2]).toBeFocused();
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
    .getByRole('radiogroup', { name: 'Show workflows' })
    .getByRole('radio', { name: 'Enabled', exact: true })
    .click();
  await expect(page.getByText('No matching workflows')).toBeVisible();
});

test('Phase 4 explicit workflow run records history and opens the unified conversation', async ({
  page,
}, info) => {
  await seed(page, 'tasks');
  await page.goto('/app-v2/?tab=workflows');
  await searchWorkflows(page, 'Phase 4 saved task 104');
  await expect(workflowCards(page)).toHaveCount(1);
  // ▶ reviews and starts the run in one click (B254); Run history follows it.
  const card = workflowCards(page).first();
  await card
    .getByRole('button', {
      name: 'Run workflow: Phase 4 saved task 104',
      exact: true,
    })
    .click();
  // Exact: the floating notice repeats it.
  await expect(page.getByText('Run started.', { exact: true })).toBeVisible();
  await screenshot(page, info, 'workflow-run-started');
  await accessibility(page, info, 'workflow-run-started');
  await card
    .getByRole('button', {
      name: 'More actions for Phase 4 saved task 104',
      exact: true,
    })
    .click();
  await page
    .getByRole('menuitem', { name: 'Run history', exact: true })
    .click();
  const runs = page.getByRole('region', {
    name: 'Task runs and approvals',
    exact: true,
  });
  await expect(
    runs.getByRole('region', { name: 'Selected run', exact: true }),
  ).toContainText('Completed');
  await expect(runs.getByRole('button', { name: /^Show run / })).toHaveCount(1);
  // A finished run is followed by a fresh review: Run now is ready again.
  await expect(
    runs.getByRole('button', { name: 'Run now', exact: true }),
  ).toBeEnabled();
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
  const workspaceNavigation = page.getByRole('navigation', {
    name: 'Workspace navigation',
  });
  if (info.project.use.viewport!.width >= 900) {
    await expect(workspaceNavigation).toBeVisible();
  } else {
    // Phones open the navigation as a drawer.
    await page
      .getByRole('button', { name: 'Toggle navigation', exact: true })
      .click();
  }
  await workspaceNavigation
    .getByRole('link', { name: 'Settings', exact: true })
    .click();
  // Below 900px Settings picks its page from the "Settings section" select.
  if (info.project.use.viewport!.width < 900)
    await page
      .getByRole('combobox', { name: 'Settings section' })
      .selectOption('tools');
  else
    await page
      .getByRole('navigation', { name: 'Settings sections' })
      .getByRole('link', { name: 'Tools', exact: true })
      .click();
  await page.locator('summary').filter({ hasText: 'All tools' }).click();
  await page
    .getByRole('combobox', { name: 'Tool source' })
    .selectOption('core');
  await page
    .getByRole('searchbox', { name: 'Search tools', exact: true })
    .fill('p4-tool-');
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
    .getByRole('searchbox', { name: 'Search tools', exact: true })
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

test('workflow rows and the new-workflow editor fit, retain drafts, and restore focus', async ({
  page,
}, info) => {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated Phase 4 runner');
  const seeded = await page.request.post('/__p4_fixture/tasks/populated', {
    headers: { 'X-Fixture-Token': token, Origin: new URL(base).origin },
  });
  expect(seeded.ok(), await seeded.text()).toBe(true);

  await page.goto('/app-v2/?tab=workflows');
  await page
    .getByRole('searchbox', { name: 'Search workflows' })
    .fill('Phase 4 saved task 104');
  await page
    .getByRole('button', { name: 'Search workflows', exact: true })
    .click();
  const card = page.locator('.workflow-grid > li').first();
  await expect(card).toBeVisible();
  await expect(card.locator('.workflow-metadata')).toHaveText(
    /Reminder.*Never run.*Run manually/,
  );
  await assertNoOverflow(page);
  await screenshot(page, info, 'slice5-card');

  const create = page.getByRole('button', {
    name: 'New workflow',
    exact: true,
  });
  await create.click();
  // New opens in the same full-page editor as Edit (B253).
  const view = page.getByRole('region', { name: 'Workflow editor' });
  const editor = view.getByRole('form', { name: 'Create task' });
  await expect(editor).toBeVisible();
  await expect(
    view.getByRole('heading', { level: 1, name: 'New workflow' }),
  ).toBeFocused();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await editor
    .getByRole('textbox', { name: 'Name', exact: true })
    .fill(`Slice 5 workflow ${info.project.name}`);
  await editor
    .getByRole('textbox', { name: 'Prompt 1', exact: true })
    .fill('Summarize synthetic sample only');
  await screenshot(page, info, 'slice5-create-editor');
  await assertNoOverflow(page);
  await view.getByRole('button', { name: 'Back to workflows' }).click();
  await expect(view).toHaveCount(0);
  await expect(create).toBeFocused();
  const recovery = page.getByRole('region', {
    name: 'Continue editing workflows',
  });
  await expect(recovery).toBeVisible();
  await recovery.getByRole('button', { name: 'Continue editing' }).click();
  await expect(
    editor.getByRole('textbox', { name: 'Name', exact: true }),
  ).toHaveValue(`Slice 5 workflow ${info.project.name}`);
  await editor.getByRole('button', { name: 'Save workflow' }).click();
  await expect(view).toHaveCount(0);
  await expect(create).toBeFocused();
  await page
    .getByRole('searchbox', { name: 'Search workflows' })
    .fill(`Slice 5 workflow ${info.project.name}`);
  await page
    .getByRole('button', { name: 'Search workflows', exact: true })
    .click();
  await expect(page.locator('.workflow-grid > li')).toHaveCount(1);
  await expect(page.locator('.workflow-metadata')).toContainText('1 step');
  await assertNoOverflow(page);
});

async function seedApps(page: Page, path: string) {
  const origin = new URL(process.env.ROW_BOT_BROWSER_BASE_URL!).origin;
  const response = await page.request.post(path, {
    headers: {
      'X-Fixture-Token': process.env.ROW_BOT_BROWSER_CONTROL_TOKEN!,
      'X-Fixture-Origin': origin,
      Origin: origin,
    },
  });
  expect(response.ok(), await response.text()).toBe(true);
}

test('app templates start switched off and offer Connect, and a step names the apps it uses', async ({
  page,
}, info) => {
  test.slow();
  await seedApps(page, '/__p6_fixture/views');
  await seedApps(page, '/__p6_fixture/app-workflow');
  try {
    await appTemplatesJourney(page, info);
  } finally {
    await seedApps(page, '/__p6_fixture/views/remove');
  }
});

async function appTemplatesJourney(page: Page, info: TestInfo) {
  await page.goto('/app-v2/?tab=workflows');
  const templates = page.getByRole('region', { name: 'Start from a template' });
  await expect(templates.getByText('GitHub pull-request digest')).toBeVisible();
  // A template without apps works with nothing connected.
  await expect(
    templates.getByRole('button', {
      name: 'Use template: Daily brief from the web',
    }),
  ).toBeVisible();
  // Linear isn't connected: its template offers Connect instead, never a workflow that can't work.
  const linear = templates
    .getByRole('listitem')
    .filter({ hasText: 'Linear triage' });
  await expect(
    linear.getByRole('link', { name: 'Connect Linear' }),
  ).toHaveAttribute('href', /\/settings\/apps\/linear$/);
  await expect(linear.getByRole('button')).toHaveCount(0);
  await expect(
    templates.getByText('Every day at 09:00 · starts off').first(),
  ).toBeVisible();
  await templates.scrollIntoViewIfNeeded();
  await assertNoOverflow(page);
  await screenshot(page, info, 'workflow-templates');

  await searchWorkflows(page, 'Counter morning digest');
  await chooseWorkflowAction(
    page,
    'Counter morning digest',
    'Edit workflow steps',
  );
  await expect(
    page.getByRole('switch', { name: 'Use Counter in this step' }),
  ).toBeChecked();
  await assertNoOverflow(page);
  await screenshot(page, info, 'workflow-step-apps');
}
