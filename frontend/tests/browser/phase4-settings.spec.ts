import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
  writeEvidence,
} from './evidence';
import { blockFixtureServiceWorkers } from './unified-helpers';
import type { Locator, Page } from '@playwright/test';
import { createHash } from 'node:crypto';

// Legacy Providers cases below exercise the removed review/reload page. The
// row-based Providers route has browser coverage in providers-parity.spec.ts;
// retained command and receipt behavior remains in focused component/API tests.

async function seed(page: Page, state: 'populated' | 'empty' | 'changed') {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated Phase 4 runner');
  const response = await page.request.post(`/__p4_fixture/catalog/${state}`, {
    headers: { 'X-Fixture-Token': token, Origin: new URL(base).origin },
  });
  expect(response.ok()).toBe(true);
}

async function expectFocusedHome(page: Page) {
  await expect(
    page.getByRole('heading', { name: 'Home', exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole('tab', { name: 'Overview', exact: true }),
  ).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('tab', { name: 'Designer' })).toHaveCount(0);
  await expect(page.getByRole('tab', { name: 'Developer' })).toHaveCount(0);
}

async function findDocumentRow(
  page: Page,
  target: { document_id: string; name: string },
) {
  const search = page.getByRole('searchbox', {
    name: 'Search documents',
    exact: true,
  });
  await expect(search).toBeVisible();
  await search.fill(target.name);
  await search.press('Enter');
  const row = page
    .getByRole('listitem')
    .filter({ hasText: target.document_id });
  await expect(row).toBeVisible();
  return row;
}

async function chooseFromMenu(scope: Locator, trigger: string, item: string) {
  await scope.getByRole('button', { name: trigger, exact: true }).click();
  await scope.page().getByRole('menuitem', { name: item, exact: true }).click();
}

async function openHomeThroughNavigation(page: Page) {
  const home = page
    .getByRole('navigation', { name: 'Workspace navigation', exact: true })
    .getByRole('link', { name: 'Home', exact: true });
  const openedCompactNavigation = !(await home.isVisible());
  if (openedCompactNavigation)
    await page
      .getByRole('button', { name: 'Toggle navigation', exact: true })
      .click();
  const navigated = page.waitForURL((url) =>
    /^\/app-v2\/?$/.test(url.pathname),
  );
  await home.focus();
  await home.press('Enter');
  await navigated;
  if (openedCompactNavigation) await expect(home).toHaveCount(0);
  else await expect(home).toHaveAttribute('aria-current', 'page');
  await expect(
    page.getByRole('heading', { name: 'Home', exact: true }),
  ).toBeVisible();
}

async function activateRoute(
  page: Page,
  trigger: Locator,
  route: { path: string; headingName: string },
) {
  const navigated = page.waitForURL((url) => url.pathname === route.path);
  await trigger.focus();
  await trigger.press('Enter');
  await navigated;
  await expect(
    page.getByRole('heading', { name: route.headingName, exact: true }),
  ).toBeVisible();
}

async function openSettingsRouteFromHome(
  page: Page,
  route: { linkName: string; path: string; headingName: string },
) {
  const navigation = page.getByRole('navigation', {
    name: 'Workspace navigation',
    exact: true,
  });
  if ((page.viewportSize()?.width ?? 0) >= 900) {
    await expect(navigation).toBeVisible();
  } else {
    await page
      .getByRole('button', { name: 'Expand navigation', exact: true })
      .click();
  }
  const settings = navigation.getByRole('link', {
    name: 'Settings',
    exact: true,
  });
  await activateRoute(page, settings, {
    path: '/app-v2/settings/providers',
    headingName: 'Providers',
  });

  if (route.path === '/app-v2/settings/providers') return;

  if ((page.viewportSize()?.width ?? 0) < 900) {
    await page
      .getByRole('combobox', { name: 'Settings section' })
      .selectOption(route.path.split('/').at(-1)!);
    await expect(page).toHaveURL(new RegExp(`${route.path}$`));
    await expect(page.locator('.settings-pane-header h2')).toHaveText(
      route.headingName,
    );
    return;
  }

  const settingsNavigation = page.getByRole('navigation', {
    name: 'Settings sections',
  });
  await settingsNavigation
    .getByRole('searchbox', { name: 'Find a setting' })
    .fill(route.linkName);
  const routeLink = page
    .getByRole('navigation', { name: 'Settings sections' })
    .getByRole('link', {
      name: route.linkName,
      exact: true,
    });
  await activateRoute(page, routeLink, route);
}

async function openDocumentsFromHome(page: Page) {
  await openSettingsRouteFromHome(page, {
    linkName: 'Documents',
    path: '/app-v2/settings/documents',
    headingName: 'Documents',
  });
  await expect(
    page.getByRole('region', { name: 'Document uploads', exact: true }),
  ).toBeVisible();
}

test.use({ serviceWorkers: 'allow' });

test('Owner-review Settings shell keeps every routed owner in one grouped responsive hierarchy', async ({
  context,
  page,
}, info) => {
  test.setTimeout(180_000);
  await blockFixtureServiceWorkers(context);
  await page.addInitScript(() => {
    localStorage.setItem(
      'row-bot.appearance.v1',
      JSON.stringify({
        version: 1,
        appearance: 'dark',
        accent: 'blue',
        density: 'compact',
        reduce_transparency: false,
      }),
    );
  });
  const leaves = [
    'preferences',
    'appearance',
    'buddy',
    'providers',
    'models',
    'voice',
    'knowledge',
    'documents',
    'tracker',
    'tools',
    'skills',
    'plugins',
    'mcp',
    'accounts',
    'channels',
    'profiles',
    'system',
    'access',
    'updates',
    'data',
  ] as const;
  const labels: Record<string, string> = {
    mcp: 'MCP',
    knowledge: 'Memory',
    profiles: 'Agent profiles',
  };
  const label = (id: string) => labels[id] ?? id[0].toUpperCase() + id.slice(1);

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/app-v2/settings');
  await expect(page).toHaveURL(/\/app-v2\/settings\/providers$/);
  const settingsNavigation = page.getByRole('navigation', {
    name: 'Settings sections',
  });
  const settingsHeading = page
    .getByRole('region', { name: 'Settings', exact: true })
    .locator('.settings-pane-header h2');
  // Seven named groups, every page listed (no collapsed categories).
  await expect(settingsNavigation.getByRole('list')).toHaveCount(7);
  await expect(settingsNavigation.getByRole('link')).toHaveCount(leaves.length);
  for (const id of leaves) {
    await page.goto(`/app-v2/settings/${id}`);
    await expect(settingsHeading).toHaveText(label(id));
    await expect(settingsHeading).toBeVisible();
    await expect(
      settingsNavigation.getByRole('link', {
        name: label(id),
        exact: true,
      }),
    ).toHaveAttribute('aria-current', 'page');
    await assertNoOverflow(page);
    await screenshot(page, info, `settings-shell-${id}`);
    await accessibility(page, info, `settings-shell-${id}`);
  }

  // Legacy ids and moved pages land on their new page and row.
  await page.goto('/app-v2/settings/google');
  await expect(page).toHaveURL(/\/app-v2\/settings\/accounts#google$/);
  await page.goto('/app-v2/settings/wiki');
  await expect(page).toHaveURL(/\/app-v2\/settings\/knowledge#wiki-vault$/);
  await page.goto('/app-v2/settings/utilities');
  await expect(page).toHaveURL(/\/app-v2\/settings\/tools#built-in-tools$/);
  await expect(
    page.getByRole('heading', { name: 'Built-in tools', exact: true }),
  ).toBeVisible();
  await page.goto('/app-v2/settings/models');
  await page.goto('/app-v2/settings/accounts');
  await page.goBack();
  await expect(page).toHaveURL(/\/app-v2\/settings\/models$/);
  await page.goForward();
  await expect(page).toHaveURL(/\/app-v2\/settings\/accounts$/);
  await page.reload();
  await expect(settingsHeading).toHaveText('Accounts');
  await expect(settingsHeading).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/app-v2/settings/providers');
  await expect(settingsNavigation).toBeHidden();
  const picker = page.getByRole('combobox', { name: 'Settings section' });
  await expect(picker).toBeVisible();
  expect((await picker.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  await picker.selectOption('knowledge');
  await expect(page).toHaveURL(/\/app-v2\/settings\/knowledge$/);
  await expect(settingsHeading).toHaveText('Memory');
  await expect(settingsHeading).toBeFocused();
  await assertNoOverflow(page);
  await screenshot(page, info, 'settings-shell-narrow-knowledge');
  await accessibility(page, info, 'settings-shell-narrow-knowledge');

  await page.goto('/app-v2/tasks');
  await expect(page).toHaveURL(/\/app-v2\/?\?tab=workflows$/);
  await expect(
    page.getByRole('tab', { name: 'Workflows', exact: true }),
  ).toHaveAttribute('aria-selected', 'true');
});

test('Owner-review narrow Settings keeps representative owners behind one accessible picker', async ({
  context,
  page,
}, info) => {
  test.setTimeout(90_000);
  await blockFixtureServiceWorkers(context);
  await page.addInitScript(() => {
    localStorage.setItem(
      'row-bot.appearance.v1',
      JSON.stringify({
        version: 1,
        appearance: 'light',
        accent: 'blue',
        density: 'compact',
        reduce_transparency: false,
      }),
    );
  });
  // The accessible picker replaces the side navigation below 1024px.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/app-v2/settings/providers');
  const settings = page.getByRole('region', {
    name: 'Settings',
    exact: true,
  });
  const heading = settings.locator('.settings-pane-header h2');
  const picker = page.getByRole('combobox', { name: 'Settings section' });
  await expect(
    page.getByRole('navigation', { name: 'Settings sections' }),
  ).toBeHidden();
  await expect(picker).toBeVisible();
  expect((await picker.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  for (const id of ['providers', 'documents', 'mcp', 'preferences'] as const) {
    if ((await picker.inputValue()) !== id) await picker.selectOption(id);
    const label = id === 'mcp' ? 'MCP' : id[0].toUpperCase() + id.slice(1);
    await expect(page).toHaveURL(new RegExp(`/app-v2/settings/${id}$`));
    await expect(heading).toHaveText(label);
    await assertNoOverflow(page);
    await screenshot(page, info, `settings-shell-phone-${id}`);
    await accessibility(page, info, `settings-shell-phone-${id}`);
  }
});

test('Channels Plugins and Skills keep reviewed local settings through the real owners', async ({
  page,
}, info) => {
  test.setTimeout(180_000);
  const headers = {
    'X-Fixture-Token': process.env.ROW_BOT_BROWSER_CONTROL_TOKEN!,
    Origin: new URL(process.env.ROW_BOT_BROWSER_BASE_URL!).origin,
  };
  const setup = await page.request.post('/__p4_fixture/capability-settings', {
    headers,
  });
  expect(setup.ok()).toBe(true);

  await page.goto('/app-v2/settings/channels');
  const channels = page.getByRole('region', {
    name: 'Channels',
    exact: true,
  });
  // Channels render as collapsed rows; non-destructive saves are reviewed by
  // the server and applied in one step (destructive actions still confirm).
  const channel = channels.getByRole('group', {
    name: 'Synthetic local channel channel',
    exact: true,
  });
  await expect(channel).toBeVisible();
  await channel.locator('summary').click();
  await channel.getByLabel(/^New Local label/).fill('browser-local');
  await channel
    .getByRole('button', { name: 'Save Local label', exact: true })
    .click();
  await expect(
    channels.getByText('Channel action completed.', { exact: true }),
  ).toBeVisible();
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `channels-saved-${appearance}`);
    await accessibility(page, info, `channels-saved-${appearance}`);
  }

  await page.goto('/app-v2/settings/plugins');
  const plugins = page.getByRole('region', {
    name: 'Plugin Center',
    exact: true,
  });
  // Search is inline; Enter searches at once.
  const pluginSearch = plugins.getByRole('searchbox', {
    name: 'Search plugins',
    exact: true,
  });
  await pluginSearch.fill('Synthetic settings');
  await pluginSearch.press('Enter');
  await plugins
    .getByRole('button', {
      name: 'Manage Synthetic settings plugin',
      exact: true,
    })
    .click();
  const region = plugins.getByRole('combobox', {
    name: /^Region(?: |$)/,
  });
  await expect(region).toBeEnabled();
  await region.selectOption({ label: 'isolated' });
  await plugins
    .getByRole('button', { name: 'Save configuration', exact: true })
    .click();
  await expect(plugins.getByText(/^Plugin change completed\./)).toBeVisible();
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `plugins-saved-${appearance}`);
    await accessibility(page, info, `plugins-saved-${appearance}`);
  }

  await page.goto('/app-v2/settings/skills');
  const skills = page.getByRole('region', { name: 'Skills', exact: true });
  // The public skill hub has its own Search; submit the installed-skill search.
  const skillSearch = skills.getByLabel('Search skills', { exact: true });
  await skillSearch.fill('Synthetic browser skill');
  await skillSearch.press('Enter');
  const skill = skills.getByRole('listitem').filter({
    hasText: 'Synthetic browser skill',
  });
  await expect(skill).toContainText('Available');
  const available = skill.getByRole('switch', {
    name: 'Synthetic browser skill available',
    exact: true,
  });
  await expect(available).toBeChecked();
  // The switch applies through the server, so its state changes after the
  // saved outcome rather than on the click itself.
  await expect(available).toBeEnabled();
  await available.click();
  await expect(skill).toContainText('Unavailable');
  await expect(available).not.toBeChecked();
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `skills-saved-${appearance}`);
    await accessibility(page, info, `skills-saved-${appearance}`);
  }

  const state = await page.request
    .get('/__p4_fixture/capability-settings', { headers })
    .then(async (response) => response.json());
  expect(state).toEqual({
    channel_label: 'browser-local',
    plugin_region: 'isolated',
    skill_available: false,
  });
  await writeEvidence(info, 'capability-settings-result.json', {
    channels_reviewed: true,
    plugin_configuration_reviewed: true,
    skill_preference_reviewed: true,
    provider_calls: 0,
    channel_deliveries: 0,
  });
});

test('Wiki uses an authorized vault and imports only the explicitly reviewed external version', async ({
  page,
}, info) => {
  const headers = {
    'X-Fixture-Token': process.env.ROW_BOT_BROWSER_CONTROL_TOKEN!,
    Origin: new URL(process.env.ROW_BOT_BROWSER_BASE_URL!).origin,
  };
  await page.goto('/app-v2/');
  await openSettingsRouteFromHome(page, {
    linkName: 'Memory',
    path: '/app-v2/settings/knowledge',
    headingName: 'Memory',
  });
  const wiki = page.getByRole('region', { name: 'Wiki vault', exact: true });
  await expect(wiki.getByText(/Select an authorized vault/)).toBeVisible();
  await wiki.getByRole('button', { name: 'Browse', exact: true }).click();
  await expect(wiki.getByText(/Authorized folder selected/)).toBeVisible();
  const enabled = wiki.getByRole('switch', { name: 'Enable Wiki Vault' });
  if (!(await enabled.isChecked())) await enabled.check();
  // "Use selected vault" is reviewed by the server and applied in one step.
  await wiki
    .getByRole('button', { name: 'Use selected vault', exact: true })
    .click();
  const syncStatus = wiki.getByText(/^Sync status:/);
  await expect(syncStatus).toContainText('Finished');
  const setup = await page.request.post('/__p4_fixture/wiki', { headers });
  expect(setup.ok()).toBe(true);
  const created = await setup.json();
  await wiki
    .getByRole('button', { name: 'Check vault sync', exact: true })
    .click();
  await expect(wiki.getByText(created.title, { exact: true })).toBeVisible();
  await expect(wiki.getByText('edited', { exact: true })).toBeVisible();
  // Inspect both versions before importing the external edit. Plain edits
  // import after the server review; only conflicts need a confirmation.
  await wiki
    .getByRole('button', { name: `Open ${created.title}`, exact: true })
    .click();
  await expect(
    wiki.getByLabel(`Database version: ${created.title}`),
  ).toContainText('saved database description');
  await expect(
    wiki.getByLabel(`Vault version: ${created.title}`),
  ).toContainText('externally edited vault description');
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `wiki-versions-${appearance}`);
    await accessibility(page, info, `wiki-versions-${appearance}`);
  }
  await wiki
    .getByRole('button', {
      name: `Import edit: ${created.title}`,
      exact: true,
    })
    .click();
  await expect(syncStatus).toContainText('Finished: 1 completed');
  const state = await page.request
    .get('/__p4_fixture/wiki', { headers })
    .then(async (response) => response.json());
  expect(state).toEqual({
    entity_id: created.entity_id,
    description:
      'The externally edited vault description is reviewed before database import.',
  });
  await writeEvidence(info, 'wiki-result.json', {
    authorized_folder: true,
    passive_open: true,
    reviewed_import: true,
    path_disclosed: false,
  });
});

test('Document processing reviews the selected conversation and runs the admitted canonical worker', async ({
  page,
}, info) => {
  const headers = {
    'X-Fixture-Token': process.env.ROW_BOT_BROWSER_CONTROL_TOKEN!,
    Origin: new URL(process.env.ROW_BOT_BROWSER_BASE_URL!).origin,
  };
  const setup = await page.request.post('/__p4_fixture/document-processing', {
    headers,
  });
  expect(setup.ok()).toBe(true);
  const { conversation_id, pending_knowledge_embeddings } = await setup.json();
  expect(Number.isSafeInteger(pending_knowledge_embeddings)).toBe(true);
  expect(pending_knowledge_embeddings).toBeGreaterThanOrEqual(0);
  const opened = page.waitForResponse((response) =>
    response.url().endsWith(`/conversations/${conversation_id}/open`),
  );
  await page.goto(`/app-v2/conversations/${conversation_id}`);
  expect((await opened).ok()).toBe(true);
  await expect(
    page.getByRole('heading', {
      name: 'Synthetic processing conversation',
      exact: true,
    }),
  ).toBeVisible();
  await openHomeThroughNavigation(page);
  await openDocumentsFromHome(page);
  const upload = page.getByRole('region', {
    name: 'Document uploads',
    exact: true,
  });
  await upload.getByLabel('Choose documents', { exact: true }).setInputFiles({
    name: `Reviewed ${info.project.name}.txt`,
    mimeType: 'text/plain',
    buffer: Buffer.from(
      `Synthetic reviewed source bytes for the canonical document processing pipeline: ${info.project.name}.`,
    ),
  });
  // Uploads are reviewed by the server and staged paused in one step.
  const uploaded = page.waitForResponse(
    (response) =>
      response.url().endsWith('/api/v1/documents/uploads/commands') &&
      response.request().method() === 'POST',
  );
  await upload
    .getByRole('button', { name: 'Upload selected', exact: true })
    .click();
  const uploadResponse = await uploaded;
  expect(uploadResponse.ok()).toBe(true);
  const { batch_id } = await uploadResponse.json();
  await page
    .getByRole('button', { name: `Process ${batch_id}`, exact: true })
    .click();
  const processing = page.getByRole('region', {
    name: 'Document processing',
    exact: true,
  });
  // Lines read in words; the exact ids stay in their titles.
  await expect(processing.locator(`p[title="${batch_id}"]`)).toHaveText(
    /^Batch: Upload · /,
  );
  // Selecting a batch never starts provider work on its own.
  expect(
    await (
      await page.request.get('/__p4_fixture/document-processing', { headers })
    ).json(),
  ).toEqual({ embeddings: 0, source_embeddings: 0, chats: 0, starts: 0 });
  await openHomeThroughNavigation(page);
  await openDocumentsFromHome(page);
  await expect(processing.locator(`p[title="${conversation_id}"]`)).toHaveText(
    /^Conversation: /,
  );
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `document-processing-selected-${appearance}`);
    await accessibility(
      page,
      info,
      `document-processing-selected-${appearance}`,
    );
  }
  const admitted = page.waitForResponse(
    (response) =>
      response
        .url()
        .endsWith(
          `/conversations/${conversation_id}/documents/processing/commands`,
        ) && response.request().method() === 'POST',
  );
  await processing
    .getByRole('button', { name: 'Start processing', exact: true })
    .click();
  const admissionResponse = await admitted;
  expect(admissionResponse.ok()).toBe(true);
  expect((await admissionResponse.json()).processing).toBe('admitted');
  // The reviewed providers stay disclosed with the admitted command.
  await expect(
    processing.getByText(/^Chat model: .* · OpenAI · /),
  ).toBeVisible();
  await expect(processing.getByRole('status')).toContainText(
    'Processing admitted.',
  );
  const run = await page.request.post(
    `/__p4_fixture/document-processing/${batch_id}/run`,
    { headers },
  );
  expect(run.ok()).toBe(true);
  const result = await run.json();
  expect(result).toMatchObject({
    status: 'completed',
    jobs: ['completed'],
    source_embeddings: 1,
    chats: 3,
    starts: 1,
  });
  expect(result.embedding_inputs).toHaveLength(result.embeddings);
  expect(
    result.embedding_inputs.filter((value: string) =>
      value.startsWith('Synthetic reviewed source bytes'),
    ),
  ).toHaveLength(1);
  expect(
    result.embedding_inputs.filter((value: string) =>
      value.startsWith(`media | Reviewed ${info.project.name}`),
    ),
  ).toHaveLength(1);
  // The canonical worker embeds the source body and media metadata, saves and
  // embeds the newly extracted knowledge, then drains the pending projections
  // captured before admission.
  expect(result.embeddings).toBe(pending_knowledge_embeddings + 3);
  await processing
    .getByRole('button', {
      name: 'Check original processing receipt',
      exact: true,
    })
    .click();
  await expect(processing.getByRole('status')).toContainText(
    'Processing admitted.',
  );
  expect(
    await (
      await page.request.get('/__p4_fixture/document-processing', { headers })
    ).json(),
  ).toEqual({
    embeddings: result.embeddings,
    source_embeddings: 1,
    chats: 3,
    starts: 1,
  });
  await page
    .getByRole('button', { name: 'Refresh queue', exact: true })
    .click();
  // The row states the batch status in words beside its actions.
  await expect(
    page
      .locator('.document-batch-row')
      .filter({
        has: page.getByRole('button', {
          name: `Inspect batch ${batch_id}`,
          exact: true,
        }),
      })
      .getByText('Completed', { exact: true }),
  ).toBeVisible();
  await writeEvidence(info, 'document-processing-result.json', {
    result,
    original_receipt_no_repeat: true,
    retained_conversation_review: true,
  });
});

test('Document upload retains selected files and stages exact streamed bytes paused without processing', async ({
  page,
}, info) => {
  const headers = {
    'X-Fixture-Token': process.env.ROW_BOT_BROWSER_CONTROL_TOKEN!,
    Origin: new URL(process.env.ROW_BOT_BROWSER_BASE_URL!).origin,
  };
  await page.goto('/app-v2/settings/documents');
  const upload = page.getByRole('region', {
    name: 'Document uploads',
    exact: true,
  });
  const files = [
    {
      name: `Synthetic ${info.project.name}.txt`,
      mimeType: 'text/plain',
      buffer: Buffer.from('Synthetic UTF-8 café document'),
    },
    {
      name: `Synthetic bounded ${info.project.name}.md`,
      mimeType: 'text/markdown',
      buffer: Buffer.alloc(2 * 1024 * 1024 + 17, 65),
    },
  ];
  await upload
    .getByLabel('Choose documents', { exact: true })
    .setInputFiles(files);
  // The selection is retained by its owner across navigation.
  await openHomeThroughNavigation(page);
  await openDocumentsFromHome(page);
  await expect(
    upload.getByText(`${files[0].name} · ${files[0].buffer.length} bytes`, {
      exact: true,
    }),
  ).toBeVisible();
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `document-upload-selected-${appearance}`);
    await accessibility(page, info, `document-upload-selected-${appearance}`);
  }
  const completed = page.waitForResponse(
    (response) =>
      response.url().endsWith('/api/v1/documents/uploads/commands') &&
      response.request().method() === 'POST',
  );
  await upload
    .getByRole('button', { name: 'Upload selected', exact: true })
    .click();
  const response = await completed;
  expect(response.ok()).toBe(true);
  const receipt = await response.json();
  expect(receipt.status).toBe('completed');
  await expect(upload.getByRole('status')).toHaveText(
    'Upload staged. The batch is paused.',
  );
  const stored = await page.request.get(
    `/__p4_fixture/document-upload/${receipt.batch_id}`,
    { headers },
  );
  expect(stored.ok()).toBe(true);
  const state = await stored.json();
  expect(state.paused).toBe(true);
  expect(state.files).toEqual(
    files.map((file) => ({
      name: file.name,
      size_bytes: file.buffer.length,
      sha256: createHash('sha256').update(file.buffer).digest('hex'),
      record_matches: true,
      status: 'queued',
    })),
  );
  await expect(
    page.getByRole('button', {
      name: `Inspect batch ${receipt.batch_id}`,
      exact: true,
    }),
  ).toBeVisible();
});

test('Document queue reviews pause resume cancellation and clearing while preserving source bytes', async ({
  page,
}, info) => {
  const headers = {
    'X-Fixture-Token': process.env.ROW_BOT_BROWSER_CONTROL_TOKEN!,
    Origin: new URL(process.env.ROW_BOT_BROWSER_BASE_URL!).origin,
  };
  const response = await page.request.post('/__p4_fixture/document-queue', {
    headers,
  });
  expect(response.ok()).toBe(true);
  const { batch_id } = await response.json();
  const saved = async () => {
    const response = await page.request.get('/__p4_fixture/document-queue', {
      headers,
    });
    expect(response.ok()).toBe(true);
    return response.json();
  };
  await page.goto('/app-v2/settings/documents');
  const queue = page.getByRole('region', {
    name: 'Document ingestion queue',
    exact: true,
  });
  const batch = queue
    .locator('div')
    .filter({
      has: page.getByRole('button', {
        name: `Inspect batch ${batch_id}`,
        exact: true,
      }),
    })
    .first();
  const status = queue.getByRole('status');
  // Pause and resume are reviewed by the server and applied in one step.
  await batch.getByRole('button', { name: 'Pause', exact: true }).click();
  await expect(status).toContainText('Saved queue outcome: paused');
  expect((await saved()).paused).toBe(true);
  await queue
    .getByRole('button', { name: 'Refresh queue', exact: true })
    .click();
  await batch.getByRole('button', { name: 'Resume', exact: true }).click();
  await expect(status).toContainText('Saved queue outcome: resumed');
  expect((await saved()).paused).toBe(false);
  await queue
    .getByRole('button', { name: 'Refresh queue', exact: true })
    .click();
  // Cancellation still requires an explicit confirmation, retained across
  // navigation, before anything changes.
  await batch
    .getByRole('button', { name: 'Cancel remaining', exact: true })
    .click();
  const confirmation = queue.getByRole('group', {
    name: 'Confirm document queue action',
    exact: true,
  });
  await expect(confirmation).toContainText('Cancel the rest of this batch?');
  expect((await saved()).status).not.toBe('cancelled');
  await openHomeThroughNavigation(page);
  await openDocumentsFromHome(page);
  await expect(confirmation).toContainText('Cancel the rest of this batch?');
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `document-queue-review-${appearance}`);
    await accessibility(page, info, `document-queue-review-${appearance}`);
  }
  await confirmation
    .getByRole('button', { name: 'Confirm cancellation', exact: true })
    .click();
  await expect(status).toContainText(
    'Saved queue outcome: cancellation_requested',
  );
  expect((await saved()).status).toBe('cancelled');
  await queue
    .getByRole('button', { name: 'Refresh queue', exact: true })
    .click();
  await queue
    .getByLabel(`Select finished batch ${batch_id}`, { exact: true })
    .check();
  await queue
    .getByRole('button', { name: 'Clear selected finished', exact: true })
    .click();
  await confirmation
    .getByRole('button', { name: 'Confirm clear selected', exact: true })
    .click();
  await expect(status).toContainText('Saved queue outcome: cleared');
  expect(await saved()).toEqual({
    status: null,
    paused: null,
    source_retained: true,
  });
});

test('Managed runtimes review metadata then install exact archive and retain route state', async ({
  page,
}, info) => {
  const headers = {
    'X-Fixture-Token': process.env.ROW_BOT_BROWSER_CONTROL_TOKEN!,
    Origin: new URL(process.env.ROW_BOT_BROWSER_BASE_URL!).origin,
  };
  const setup = await page.request.post('/__p4_fixture/runtime-installation', {
    headers,
  });
  expect(setup.ok()).toBe(true);
  const saved = async () => {
    const response = await page.request.get(
      '/__p4_fixture/runtime-installation',
      { headers },
    );
    expect(response.ok()).toBe(true);
    return response.json();
  };
  await page.goto('/app-v2/settings/mcp');
  // Managed runtimes live behind the MCP page's Advanced disclosure.
  const runtimes = page.locator('details', {
    has: page.locator('summary', { hasText: 'Managed runtimes' }),
  });
  const expandRuntimes = async () => {
    if (!(await runtimes.evaluate((node) => (node as HTMLDetailsElement).open)))
      await runtimes.locator('summary').first().click();
  };
  await expandRuntimes();
  const runtime = page.getByRole('region', {
    name: 'node managed runtime installation',
    exact: true,
  });
  expect((await saved()).calls).toEqual([]);
  // Resolution and installation are reviewed by the server and run in one step.
  await runtime
    .getByRole('button', { name: 'Resolve metadata', exact: true })
    .click();
  await expect(
    runtime.getByText(
      'Original operation: resolved. Worker cleanup: confirmed.',
      { exact: true },
    ),
  ).toBeVisible();
  expect((await saved()).calls).toEqual(['resolve']);
  await openHomeThroughNavigation(page);
  await openSettingsRouteFromHome(page, {
    linkName: 'MCP',
    path: '/app-v2/settings/mcp',
    headingName: 'MCP',
  });
  await expandRuntimes();
  // The resolved operation is retained by its owner across navigation.
  await expect(
    runtime.getByText(
      'Original operation: resolved. Worker cleanup: confirmed.',
      { exact: true },
    ),
  ).toBeVisible();
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `runtime-installation-resolved-${appearance}`);
    await accessibility(
      page,
      info,
      `runtime-installation-resolved-${appearance}`,
    );
  }
  await runtime
    .getByRole('button', { name: 'Install pinned runtime', exact: true })
    .click();
  await expect(
    runtime.getByText(
      'Original operation: installed. Worker cleanup: confirmed.',
      { exact: true },
    ),
  ).toBeVisible();
  expect(await saved()).toEqual({
    calls: ['resolve', 'download'],
    installed: true,
    synthetic_bytes: true,
  });
  await runtime
    .getByRole('button', { name: 'Refresh installation status', exact: true })
    .click();
  expect((await saved()).calls).toEqual(['resolve', 'download']);
});

test('Knowledge Settings omits create, retains modal drafts, and confirms lifecycle changes', async ({
  page,
}, info) => {
  const headers = {
    'X-Fixture-Token': process.env.ROW_BOT_BROWSER_CONTROL_TOKEN!,
    Origin: new URL(process.env.ROW_BOT_BROWSER_BASE_URL!).origin,
  };
  expect(
    (
      await page.request.post('/__p4_fixture/knowledge/populated', { headers })
    ).ok(),
  ).toBe(true);
  await page.goto('/app-v2/settings/knowledge');
  await expect(
    page.getByRole('button', { name: 'Create knowledge', exact: true }),
  ).toHaveCount(0);
  // Earlier specs in the shared fixture may add entries; the page size is fixed.
  await expect(
    page.getByText(/^Showing 25 of \d+ matching entries\.$/),
  ).toBeVisible();
  await page
    .getByRole('searchbox', { name: 'Search knowledge' })
    .fill('Phase 4 knowledge 002');
  await expect(
    page.getByText('Showing 1 of 1 matching entries.'),
  ).toBeVisible();
  const filtered = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.pathname.endsWith('/knowledge/entities') &&
      url.searchParams.get('query') === 'Phase 4 knowledge 002' &&
      url.searchParams.get('status') === 'active'
    );
  });
  await page.getByRole('combobox', { name: 'Status' }).selectOption('active');
  await filtered;
  await expect(
    page.getByText('Showing 1 of 1 matching entries.'),
  ).toBeVisible();
  const entry = page.locator('.settings-knowledge-result').first();
  await entry.locator('summary').click();
  await entry.getByRole('button', { name: 'Edit', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Edit knowledge' });
  const editor = dialog;
  await expect(dialog).toBeVisible();
  await editor
    .getByRole('textbox', { name: 'Description', exact: true })
    .fill('Synthetic retained modal draft');
  // Close without saving: the owner retains the unsaved draft.
  await dialog.getByRole('button', { name: 'Close knowledge editor' }).click();
  await expect(dialog).toHaveCount(0);
  await openHomeThroughNavigation(page);
  await openSettingsRouteFromHome(page, {
    linkName: 'Memory',
    path: '/app-v2/settings/knowledge',
    headingName: 'Memory',
  });
  await expect(
    page.getByText(/^Showing 25 of \d+ matching entries\.$/),
  ).toBeVisible();
  await page
    .getByRole('searchbox', { name: 'Search knowledge' })
    .fill('Phase 4 knowledge 002');
  await expect(
    page.getByText('Showing 1 of 1 matching entries.'),
  ).toBeVisible();
  const reopenedFilter = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.pathname.endsWith('/knowledge/entities') &&
      url.searchParams.get('query') === 'Phase 4 knowledge 002' &&
      url.searchParams.get('status') === 'active'
    );
  });
  await page.getByRole('combobox', { name: 'Status' }).selectOption('active');
  await reopenedFilter;
  await expect(
    page.getByText('Showing 1 of 1 matching entries.'),
  ).toBeVisible();
  const reopenedEntry = page
    .getByRole('listitem')
    .filter({ hasText: 'Phase 4 knowledge 002' })
    .first();
  await reopenedEntry.locator('summary').click();
  await reopenedEntry
    .getByRole('button', { name: 'Edit', exact: true })
    .click();
  await expect(dialog).toBeVisible();
  await expect(
    editor.getByRole('textbox', { name: 'Description', exact: true }),
  ).toHaveValue('Synthetic retained modal draft');
  // Saving is reviewed by the server and applied in one step.
  await editor
    .getByRole('button', { name: 'Save knowledge', exact: true })
    .click();
  await expect(
    editor.getByText(
      'Knowledge saved. Search and wiki projections remain pending. Reload the saved entry to continue.',
      { exact: true },
    ),
  ).toBeVisible();
  const refreshedCatalog = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.pathname.endsWith('/knowledge/entities') &&
      url.searchParams.get('query') === 'Phase 4 knowledge 002' &&
      url.searchParams.get('status') === 'active'
    );
  });
  await editor.getByRole('button', { name: /reload saved entry$/i }).click();
  await refreshedCatalog;
  await dialog.getByRole('button', { name: 'Close knowledge editor' }).click();
  await expect(dialog).toHaveCount(0);
  const archive = reopenedEntry.getByRole('button', {
    name: 'Archive',
    exact: true,
  });
  // The reloaded catalog can replace the row (collapsed) at any moment, so
  // open it idempotently until its details are showing.
  await expect(async () => {
    const open = await reopenedEntry
      .locator('details')
      .first()
      .evaluate((element) => (element as HTMLDetailsElement).open);
    if (!open) await reopenedEntry.locator('summary').first().click();
    await expect(
      reopenedEntry.getByText('p4-entity-002', { exact: true }),
    ).toBeVisible({ timeout: 1_000 });
  }).toPass();
  // Archive and restore are reviewed by the server and applied in one step.
  await archive.click();
  await expect(page.getByText('No matching knowledge')).toBeVisible();
  await page.getByRole('combobox', { name: 'Status' }).selectOption('archived');
  const archived = page.locator('.settings-knowledge-result').first();
  await archived.locator('summary').click();
  await archived.getByRole('button', { name: 'Restore', exact: true }).click();
  await expect(
    page.locator('.settings-knowledge-result', {
      hasText: 'Phase 4 knowledge 002',
    }),
  ).toHaveCount(0);
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `knowledge-editor-${appearance}`);
    await accessibility(page, info, `knowledge-editor-${appearance}`);
  }
});

test('Knowledge relations retain reviewed targets and save directed edges removal and replacement', async ({
  page,
}, info) => {
  const headers = {
    'X-Fixture-Token': process.env.ROW_BOT_BROWSER_CONTROL_TOKEN!,
    Origin: new URL(process.env.ROW_BOT_BROWSER_BASE_URL!).origin,
  };
  expect(
    (
      await page.request.post('/__p4_fixture/knowledge/populated', { headers })
    ).ok(),
  ).toBe(true);
  await page.goto('/app-v2/settings/knowledge');
  const entry = page
    .getByRole('listitem')
    .filter({ hasText: 'Phase 4 knowledge 000' })
    .first();
  await entry.locator('summary').click();
  await entry.getByRole('button', { name: 'Edit', exact: true }).click();
  await page
    .getByRole('button', { name: 'Relations and replacement', exact: true })
    .click();
  const relations = page.getByRole('region', {
    name: 'Knowledge relations',
    exact: true,
  });
  await relations
    .getByLabel('Find target knowledge', { exact: true })
    .fill('Phase 4 knowledge 001');
  await relations
    .getByRole('button', { name: 'Search targets', exact: true })
    .click();
  await relations
    .getByRole('button', { name: 'Phase 4 knowledge 001 · fact', exact: true })
    .click();
  const dialog = page.getByRole('dialog', { name: 'Edit knowledge' });
  await dialog.getByRole('button', { name: 'Close knowledge editor' }).click();
  await expect(dialog).toHaveCount(0);
  await openHomeThroughNavigation(page);
  await openSettingsRouteFromHome(page, {
    linkName: 'Memory',
    path: '/app-v2/settings/knowledge',
    headingName: 'Memory',
  });
  const reopenedEntry = page
    .getByRole('listitem')
    .filter({ hasText: 'Phase 4 knowledge 000' })
    .first();
  await reopenedEntry.locator('summary').click();
  await reopenedEntry
    .getByRole('button', { name: 'Edit', exact: true })
    .click();
  await expect(relations).toBeVisible();
  await expect(
    relations.getByText(/^Selected: Phase 4 knowledge 001/),
  ).toBeVisible();
  const commit = async (action: Locator, outcome: string) => {
    const result = page.waitForResponse(
      (response) =>
        response.url().endsWith('/knowledge/relations/commands') &&
        response.request().method() === 'POST',
    );
    await action.click();
    const response = await result;
    expect(response.ok()).toBe(true);
    expect((await response.json()).outcome).toBe(outcome);
    await relations
      .getByRole('button', { name: 'Reload relations', exact: true })
      .click();
  };
  await commit(
    relations.getByRole('button', { name: 'Add relation', exact: true }),
    'saved',
  );
  await expect(
    relations.getByText('Outgoing · knows · Phase 4 knowledge 001', {
      exact: true,
    }),
  ).toBeVisible();
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `knowledge-relations-${appearance}`);
    await accessibility(page, info, `knowledge-relations-${appearance}`);
  }
  await relations
    .getByRole('button', { name: 'Remove knows relation', exact: true })
    .click();
  await commit(
    relations
      .getByRole('group', { name: 'Confirm relation removal', exact: true })
      .getByRole('button', { name: 'Confirm relation removal', exact: true }),
    'removed',
  );
  await expect(
    relations.getByText('1 saved relations. 1 shown on this page.', {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    relations.getByText('Outgoing · supports · Phase 4 knowledge 001', {
      exact: true,
    }),
  ).toBeVisible();
  await commit(
    relations.getByRole('button', {
      name: 'Supersede with selected entry',
      exact: true,
    }),
    'superseded',
  );
  const editor = page.getByRole('region', {
    name: 'Knowledge editor',
    exact: true,
  });
  await editor
    .getByRole('button', { name: 'Reload saved entry', exact: true })
    .click();
  await expect(
    editor.getByText(
      'Saved status: superseded. Projection readiness: unknown.',
      { exact: true },
    ),
  ).toBeVisible();
});

test('Buddy keeps appearance edits through navigation and serves bundled media with reduced motion', async ({
  page,
}, info) => {
  const headers = {
    'X-Fixture-Token': process.env.ROW_BOT_BROWSER_CONTROL_TOKEN!,
    Origin: new URL(process.env.ROW_BOT_BROWSER_BASE_URL!).origin,
  };
  const seeded = await page.request.post('/__p4_fixture/buddy', { headers });
  expect(seeded.ok()).toBe(true);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/app-v2/conversations/p1-browser-a');
  await expect(
    page.getByRole('heading', { name: 'Phase 1 conversation A', exact: true }),
  ).toBeVisible();
  const navigation = page.getByRole('navigation', {
    name: 'Workspace navigation',
    exact: true,
  });
  if (!(await navigation.isVisible()))
    await page
      .getByRole('button', { name: 'Toggle navigation', exact: true })
      .click();
  await activateRoute(
    page,
    navigation.getByRole('button', {
      name: 'Buddy settings',
      exact: true,
    }),
    { path: '/app-v2/settings/buddy', headingName: 'Buddy' },
  );
  await expect(page.getByRole('dialog')).toHaveCount(0);
  const preferences = page.getByRole('region', {
    name: 'Buddy preferences',
    exact: true,
  });
  // Name and visual style live in the "Advanced companion" disclosure.
  const advanced = preferences.locator('details.settings-buddy-advanced');
  const openAdvanced = async () => {
    if (!(await advanced.evaluate((node) => (node as HTMLDetailsElement).open)))
      await advanced.locator('summary').click();
  };
  await openAdvanced();
  await preferences
    .getByLabel('Buddy name', { exact: true })
    .fill('Synthetic companion');
  await preferences
    .getByLabel('Bubble style', { exact: true })
    .selectOption('chatty');
  await page
    .getByLabel('Describe your Buddy', { exact: true })
    .fill('Retained synthetic description');
  await openHomeThroughNavigation(page);
  await expectFocusedHome(page);
  await openSettingsRouteFromHome(page, {
    linkName: 'Buddy',
    path: '/app-v2/settings/buddy',
    headingName: 'Buddy',
  });
  await openAdvanced();
  await expect(
    preferences.getByLabel('Buddy name', { exact: true }),
  ).toHaveValue('Synthetic companion');
  await expect(
    page.getByLabel('Describe your Buddy', { exact: true }),
  ).toHaveValue('Retained synthetic description');
  await preferences
    .getByRole('button', { name: 'Save Buddy preferences', exact: true })
    .click();
  await expect(
    page
      .getByRole('region', { name: 'Buddy', exact: true })
      .getByText('Buddy preferences saved.', { exact: true }),
  ).toBeVisible();
  await expect(
    page.locator('.buddy-avatar[src^="blob:"]').first(),
  ).toBeVisible();
  await expect(page.locator('video.buddy-avatar')).toHaveCount(0);
  const looks = preferences.getByRole('group', {
    name: 'Buddy looks',
    exact: true,
  });
  await expect(looks.locator('img[src^="blob:"]')).toHaveCount(6);
  await expect
    .poll(() =>
      looks
        .locator('img')
        .evaluateAll((images) =>
          images.every((image) => (image as HTMLImageElement).naturalWidth > 0),
        ),
    )
    .toBe(true);
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({
      colorScheme: appearance,
      reducedMotion: 'reduce',
    });
    await assertNoOverflow(page);
    await screenshot(page, info, `buddy-preferences-${appearance}`);
    await accessibility(page, info, `buddy-preferences-${appearance}`);
  }
  await page.getByLabel('Describe your Buddy', { exact: true }).fill('');
  await page.reload();
  await expect(
    preferences.getByLabel('Buddy name', { exact: true }),
  ).toHaveValue('Synthetic companion');
  await expect(
    preferences.getByLabel('Bubble style', { exact: true }),
  ).toHaveValue('chatty');
});

test('Buddy plays the saved bundled motion and switches to its still for reduced motion', async ({
  page,
}, info) => {
  const headers = {
    'X-Fixture-Token': process.env.ROW_BOT_BROWSER_CONTROL_TOKEN!,
    Origin: new URL(process.env.ROW_BOT_BROWSER_BASE_URL!).origin,
  };
  expect(
    (await page.request.post('/__p4_fixture/buddy', { headers })).ok(),
  ).toBe(true);
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  const bundledStill = page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith(
      '/buddy/packs/glyph/media/preview',
    ),
  );
  const bundledMotion = page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith('/buddy/packs/glyph/media/idle'),
  );
  await page.goto('/app-v2/conversations/p1-browser-a');
  await expect(
    page.getByRole('heading', { name: 'Phase 1 conversation A', exact: true }),
  ).toBeVisible();
  const navigation = page.getByRole('navigation', {
    name: 'Workspace navigation',
    exact: true,
  });
  if (!(await navigation.isVisible()))
    await page
      .getByRole('button', { name: 'Toggle navigation', exact: true })
      .click();
  await expect(navigation).toBeVisible();
  await expect(
    navigation.getByRole('button', { name: 'Buddy settings', exact: true }),
  ).toBeVisible();
  const [stillResponse, motionResponse] = await Promise.all([
    bundledStill,
    bundledMotion,
  ]);
  const expectedOrigin = new URL(process.env.ROW_BOT_BROWSER_BASE_URL!).origin;
  expect(new URL(stillResponse.url()).origin).toBe(expectedOrigin);
  expect(new URL(motionResponse.url()).origin).toBe(expectedOrigin);
  expect(stillResponse.ok()).toBe(true);
  expect(motionResponse.ok()).toBe(true);
  expect(stillResponse.headers()['content-type']).toContain('image/png');
  expect(motionResponse.headers()['content-type']).toContain('video/mp4');
  const requestHeaders = await motionResponse.request().allHeaders();
  const clientSession = requestHeaders['x-client-session'];
  const csrfToken = requestHeaders['x-csrf-token'];
  expect(clientSession).toBeTruthy();
  expect(csrfToken).toBeTruthy();
  const mediaHeaders = {
    'X-Client-Session': clientSession,
    'X-CSRF-Token': csrfToken,
  };
  const [verifiedStill, verifiedMotion] = await Promise.all([
    page.request.get(stillResponse.url(), { headers: mediaHeaders }),
    page.request.get(motionResponse.url(), { headers: mediaHeaders }),
  ]);
  expect(verifiedStill.ok()).toBe(true);
  expect(verifiedMotion.ok()).toBe(true);
  expect(verifiedStill.headers()['content-type']).toContain('image/png');
  expect(verifiedMotion.headers()['content-type']).toContain('video/mp4');
  const stillBytes = await verifiedStill.body();
  const motionBytes = await verifiedMotion.body();
  expect(stillBytes.subarray(0, 8)).toEqual(
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
  );
  expect(motionBytes.subarray(4, 8).toString('ascii')).toBe('ftyp');
  const video = navigation.locator('video.buddy-avatar');
  const playback = await page.evaluate(async (encoded) => {
    const binary = atob(encoded);
    const bytes = Uint8Array.from(binary, (value) => value.charCodeAt(0));
    const blob = new Blob([bytes], { type: 'video/mp4' });
    const url = URL.createObjectURL(blob);
    const probe = document.createElement('video');
    probe.muted = true;
    probe.playsInline = true;
    probe.style.position = 'fixed';
    probe.style.width = '1px';
    probe.style.height = '1px';
    probe.style.opacity = '0';
    document.body.append(probe);
    return await new Promise<{ supported: boolean; reason: string }>(
      (resolve) => {
        let complete = false;
        const finish = (supported: boolean, reason: string) => {
          if (complete) return;
          complete = true;
          clearTimeout(timeout);
          probe.remove();
          URL.revokeObjectURL(url);
          resolve({ supported, reason });
        };
        const timeout = window.setTimeout(
          () => finish(false, 'playback-timeout'),
          5_000,
        );
        probe.addEventListener('playing', () => finish(true, 'playing'), {
          once: true,
        });
        probe.addEventListener(
          'error',
          () => finish(false, `media-error-${probe.error?.code ?? 0}`),
          { once: true },
        );
        probe.src = url;
        void probe
          .play()
          .catch(() =>
            finish(false, `play-rejected-${probe.error?.code ?? 0}`),
          );
      },
    );
  }, motionBytes.toString('base64'));
  await info.attach('buddy-motion-playback-capability', {
    body: Buffer.from(JSON.stringify(playback, null, 2)),
    contentType: 'application/json',
  });
  let motionSafety:
    | {
        muted: boolean;
        defaultMuted: boolean;
        autoplay: boolean;
        controls: boolean;
        ariaHidden: string | null;
      }
    | undefined;
  if (playback.supported) {
    await expect(video).toBeVisible();
    await expect
      .poll(() =>
        video.evaluate(
          (node: HTMLVideoElement) =>
            node.readyState >= 2 && !node.paused && node.videoWidth > 0,
        ),
      )
      .toBe(true);
    motionSafety = await video.evaluate((node: HTMLVideoElement) => ({
      muted: node.muted,
      defaultMuted: node.defaultMuted,
      autoplay: node.autoplay,
      controls: node.controls,
      ariaHidden: node.getAttribute('aria-hidden'),
    }));
    expect(motionSafety).toMatchObject({
      muted: true,
      autoplay: true,
      controls: false,
      ariaHidden: 'true',
    });
  } else {
    await expect(video).toHaveCount(0);
    await expect(
      navigation.locator('img.buddy-avatar[src^="blob:"]'),
    ).toBeVisible();
  }
  await screenshot(page, info, 'buddy-bundled-motion');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await expect(video).toHaveCount(0);
  const still = navigation.locator('img.buddy-avatar[src^="blob:"]');
  await expect(still).toBeVisible();
  await expect
    .poll(() => still.evaluate((node: HTMLImageElement) => node.naturalWidth))
    .toBeGreaterThan(0);
  await writeEvidence(info, 'buddy-motion-safety', {
    motionSafety,
    playbackSupported: playback.supported,
    reducedMotionRemovesAutoplayVideo: (await video.count()) === 0,
    policy:
      'Decorative Buddy motion is aria-hidden and muted whenever autoplay is supported; reduced motion removes the video and uses the still image.',
  });
  await assertNoOverflow(page);
  await accessibility(page, info, 'buddy-reduced-motion');
});

test.skip('Subscription checks retain the original review and expose actual cancellation without replay', async ({
  page,
}, info) => {
  if (info.project.use.browserName !== 'firefox')
    info.annotations.push({
      type: 'expected-console-error',
      description: JSON.stringify({
        signature:
          'Failed to load resource: the server responded with a status of 409 (Conflict)',
        count: 1,
        owner: 'subscription-probes',
        fixture:
          'Explicitly cancelled original synthetic provider request returns subscription_cancelled',
      }),
    });
  const headers = {
    'X-Fixture-Token': process.env.ROW_BOT_BROWSER_CONTROL_TOKEN!,
    Origin: new URL(process.env.ROW_BOT_BROWSER_BASE_URL!).origin,
  };
  expect(
    (
      await page.request.post('/__p4_fixture/subscription-probes/ready', {
        headers,
      })
    ).ok(),
  ).toBe(true);
  await page.goto('/app-v2/settings/providers');
  const editor = page.getByRole('region', {
    name: 'Subscription checks',
    exact: true,
  });
  await editor
    .getByRole('button', { name: 'Review check', exact: true })
    .click();
  await editor
    .getByRole('button', { name: 'Confirm check', exact: true })
    .click();
  await expect(
    editor.getByText(/Last result: Codex.*(?:missing|unavailable)/),
  ).toBeVisible();
  await editor
    .getByRole('button', { name: 'Reload saved checks', exact: true })
    .click();
  await editor
    .getByLabel('Subscription provider', { exact: true })
    .selectOption('xai_oauth');
  await editor
    .getByLabel('Check type', { exact: true })
    .selectOption('runtime');
  await editor
    .getByLabel('Provider-qualified model reference', { exact: true })
    .fill('model:xai_oauth:grok-4');
  await editor
    .getByRole('button', { name: 'Review check', exact: true })
    .click();
  await expect(editor.getByText(/Reviewed: xAI subscription/)).toBeVisible();
  await activateRoute(
    page,
    editor.getByRole('button', {
      name: 'Browse saved models',
      exact: true,
    }),
    { path: '/app-v2/settings/models', headingName: 'Models' },
  );
  await openHomeThroughNavigation(page);
  await expectFocusedHome(page);
  await openSettingsRouteFromHome(page, {
    linkName: 'Providers',
    path: '/app-v2/settings/providers',
    headingName: 'Providers',
  });
  await expect(editor.getByText(/Reviewed: xAI subscription/)).toBeVisible();
  await editor
    .getByRole('button', { name: 'Confirm check', exact: true })
    .click();
  await expect(
    editor.getByText(/Last result: xAI subscription.*passed/),
  ).toBeVisible();
  const success = await page.request.get('/__p4_fixture/subscription-probes', {
    headers,
  });
  expect((await success.json()).calls).toBe(3);
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `subscription-checks-${appearance}`);
    await accessibility(page, info, `subscription-checks-${appearance}`);
  }
  expect(
    (
      await page.request.post('/__p4_fixture/subscription-probes/blocked', {
        headers,
      })
    ).ok(),
  ).toBe(true);
  await editor
    .getByRole('button', { name: 'Reload saved checks', exact: true })
    .click();
  await editor
    .getByRole('button', { name: 'Review check', exact: true })
    .click();
  await editor
    .getByRole('button', { name: 'Confirm check', exact: true })
    .click();
  try {
    await expect
      .poll(
        async () =>
          (
            await (
              await page.request.get('/__p4_fixture/subscription-probes', {
                headers,
              })
            ).json()
          ).entered,
      )
      .toBe(true);
    await editor
      .getByRole('button', { name: 'Cancel original check', exact: true })
      .click();
    await expect(
      editor.getByText(/Cancellation requested. Waiting/),
    ).toBeVisible();
    await expect(
      editor.getByRole('button', { name: 'Confirm check', exact: true }),
    ).toBeDisabled();
  } finally {
    expect(
      (
        await page.request.post('/__p4_fixture/subscription-probes/release', {
          headers,
        })
      ).ok(),
    ).toBe(true);
  }
  await expect(
    editor.getByRole('button', {
      name: 'Read original check receipt',
      exact: true,
    }),
  ).toBeEnabled();
  await editor
    .getByRole('button', { name: 'Read original check receipt', exact: true })
    .click();
  await expect(
    editor.getByText(/Original work: cancelled. Stopped./),
  ).toBeVisible();
  await expect(
    editor.getByRole('button', { name: 'Confirm check', exact: true }),
  ).toBeDisabled();
  expect(
    (
      await (
        await page.request.get('/__p4_fixture/subscription-probes', { headers })
      ).json()
    ).calls,
  ).toBe(1);
  await screenshot(page, info, 'subscription-checks-cancelled-original');
});
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

test('Models catalog applies a default and retains it across Providers navigation', async ({
  page,
}, info) => {
  await seed(page, 'populated');
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN!;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL!;
  const headers = { 'X-Fixture-Token': token, Origin: new URL(base).origin };
  expect(
    (
      await page.request.post('/__p4_fixture/provider-credentials', { headers })
    ).ok(),
  ).toBe(true);
  await page.goto('/app-v2/settings/models');
  const models = page.locator('[aria-label="Models settings"]');
  const defaultPicker = models.getByRole('combobox', { name: 'Default model' });
  await expect(defaultPicker).toBeVisible();
  const index = (await defaultPicker.inputValue()).endsWith('-104')
    ? '103'
    : '104';
  const label = 'Saved example ' + index;
  await models.getByRole('button', { name: 'Model Catalog' }).click();
  await models.getByRole('button', { name: 'Open' }).first().click();
  await models.getByRole('button', { name: 'Show more models' }).click();
  const row = models
    .locator('.settings-model-row-list > li')
    .filter({ hasText: label });
  await expect(row).toBeVisible();
  await row.getByRole('button', { name: 'Pin ' + label + ' for chat' }).click();
  await expect(
    row.getByRole('button', { name: 'Unpin ' + label + ' for chat' }),
  ).toBeVisible();
  await row
    .getByRole('button', { name: 'Set ' + label + ' as chat default' })
    .click();
  await expect(defaultPicker).toHaveValue('model:openai:phase4-' + index);
  await models.getByRole('link', { name: 'Provider connections' }).click();
  await expect(page).toHaveURL(/\/app-v2\/settings\/providers$/);
  await page.goBack();
  await expect(defaultPicker).toHaveValue('model:openai:phase4-' + index);
  await assertNoOverflow(page);
  await screenshot(page, info, 'models-default-retained');
  await accessibility(page, info, 'models-default-retained');
});

test.skip('Phase 4 saved providers lead to bounded searchable model details without a live probe', async ({
  page,
}, info) => {
  await seed(page, 'populated');
  await page.goto('/app-v2/settings');
  await activateRoute(
    page,
    page.getByRole('link', { name: 'Providers', exact: true }),
    { path: '/app-v2/settings/providers', headingName: 'Providers' },
  );
  await expect(
    page.getByText(
      /Account access and runtime readiness have not been checked/,
    ),
  ).toBeVisible();
  const provider = page
    .locator('.settings-results a')
    .filter({ hasText: '106 saved models' });
  await expect(provider).toHaveCount(1);
  await screenshot(page, info, 'saved-providers');
  await accessibility(page, info, 'saved-providers');
  await activateRoute(page, provider, {
    path: '/app-v2/settings/models',
    headingName: 'Models',
  });
  await expect(page.getByText('106 matching models')).toBeVisible();
  await expect(page.locator('.settings-results > li')).toHaveCount(50);
  await page
    .getByRole('button', { name: 'Load more models', exact: true })
    .click();
  await expect(page.locator('.settings-results > li')).toHaveCount(100);
  await page
    .getByRole('button', { name: 'Load more models', exact: true })
    .click();
  await expect(page.locator('.settings-results > li')).toHaveCount(106);
  await expect(
    page.getByRole('button', { name: 'Load more models', exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole('searchbox', { name: 'Search models' })
    .fill('Long model');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.locator('.settings-results > li')).toHaveCount(1);
  await page.locator('.settings-results summary').click();
  await expect(
    page.locator('.settings-results dd').filter({ hasText: /^Unknown$/ }),
  ).toHaveCount(6);
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await expect(page.locator('html')).toHaveAttribute(
      'data-theme',
      appearance,
    );
    await assertNoOverflow(page);
    await screenshot(page, info, `saved-model-long-identity-${appearance}`);
    await accessibility(page, info, `saved-model-${appearance}`);
  }
  await page.reload();
  await expect(
    page.getByRole('combobox', { name: 'Provider', exact: true }),
  ).toHaveValue('openai');
  await expect(page.getByText('106 matching models')).toBeVisible();
  await writeEvidence(info, 'saved-catalog-reads', {
    provider: 'openai',
    models: 106,
    pages: [50, 50, 6],
    query: 'Long model',
    runtimeReadiness: 'unknown',
  });
});

test('Models catalog recovers an expired page cursor only when requested', async ({
  page,
}, info) => {
  await seed(page, 'populated');
  await page.goto('/app-v2/settings/models?provider=openai');
  const models = page.locator('[aria-label="Models settings"]');
  await expect(models.locator('.settings-model-row-list > li')).toHaveCount(80);
  await seed(page, 'changed');
  if (info.project.use.browserName !== 'firefox')
    info.annotations.push({
      type: 'expected-console-error',
      description: JSON.stringify({
        signature:
          'Failed to load resource: the server responded with a status of 410 (Gone)',
        count: 1,
        owner: 'saved catalog cursor validation',
        fixture: 'changed saved catalog after page one',
      }),
    });
  await models.getByRole('button', { name: 'Show more models' }).click();
  await expect(
    models.getByRole('button', { name: 'Reload catalog results' }),
  ).toBeVisible();
  await expect(
    models.getByRole('button', { name: 'Show more models' }),
  ).toBeDisabled();
  await models.getByRole('button', { name: 'Reload catalog results' }).click();
  await expect(models.locator('.settings-model-row-list > li')).toHaveCount(80);
  await models.getByRole('button', { name: 'Show more models' }).click();
  await expect(models.locator('.settings-model-row-list > li')).toHaveCount(
    105,
  );
  await seed(page, 'empty');
  await models.getByRole('combobox', { name: 'Provider' }).selectOption('');
  await models
    .getByRole('combobox', { name: 'Provider' })
    .selectOption('openai');
  await expect(models.getByText('No matching models')).toBeVisible();
  await assertNoOverflow(page);
  await screenshot(page, info, 'saved-models-empty');
  await accessibility(page, info, 'saved-models-empty');
});

test.skip('Phase 4 credentials retain a private reviewed draft and save disconnect restore locally', async ({
  page,
}, info) => {
  await seed(page, 'populated');
  const response = await page.request.post(
    '/__p4_fixture/provider-credentials',
    {
      headers: {
        'X-Fixture-Token': process.env.ROW_BOT_BROWSER_CONTROL_TOKEN!,
        Origin: new URL(process.env.ROW_BOT_BROWSER_BASE_URL!).origin,
      },
    },
  );
  expect(response.ok()).toBe(true);
  await page.goto('/app-v2/settings/providers');
  const open = page.getByRole('button', {
    name: 'Edit OpenAI API credentials',
    exact: true,
  });
  await open.click();
  const editor = page.getByRole('region', {
    name: 'Provider credential settings',
    exact: true,
  });
  await editor
    .getByLabel('New API key', { exact: true })
    .fill('synthetic-browser-replacement');
  await openHomeThroughNavigation(page);
  await expect(editor).toHaveCount(0);
  await openSettingsRouteFromHome(page, {
    linkName: 'Providers',
    path: '/app-v2/settings/providers',
    headingName: 'Providers',
  });
  await open.click();
  await expect(editor.getByLabel('New API key', { exact: true })).toHaveValue(
    'synthetic-browser-replacement',
  );
  await editor
    .getByRole('button', { name: 'Review change', exact: true })
    .click();
  await expect(
    editor.getByRole('button', { name: 'Confirm replacement', exact: true }),
  ).toBeEnabled();
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `provider-credential-review-${appearance}`);
    await accessibility(page, info, `provider-credential-review-${appearance}`);
  }
  await editor
    .getByRole('button', { name: 'Confirm replacement', exact: true })
    .click();
  await expect(
    page.getByText('Credential settings saved.', { exact: true }),
  ).toBeVisible();
  for (const [action, button] of [
    ['clear', 'Confirm disconnect'],
    ['restore', 'Confirm restore'],
  ]) {
    await open.click();
    await editor
      .getByRole('combobox', { name: 'Credential action', exact: true })
      .selectOption(action);
    await editor
      .getByRole('button', { name: 'Review change', exact: true })
      .click();
    await editor.getByRole('button', { name: button, exact: true }).click();
    await expect(
      page.getByText('Credential settings saved.', { exact: true }),
    ).toBeVisible();
  }
  await open.click();
  await expect(editor.getByText(/Credential configured/)).toBeVisible();
  await editor
    .getByRole('combobox', { name: 'Credential action', exact: true })
    .selectOption('save');
  await expect(editor.getByLabel('New API key', { exact: true })).toHaveValue(
    '',
  );
});

test.skip('Phase 4 endpoint configuration retains drafts and reviews create edit remove', async ({
  page,
}, info) => {
  await seed(page, 'populated');
  await page.goto('/app-v2/settings/providers');
  const editor = page.getByRole('region', {
    name: 'Provider configuration',
    exact: true,
  });
  await editor
    .getByRole('button', { name: 'New endpoint', exact: true })
    .click();
  const id = `browser-${info.project.name}`;
  await editor.getByLabel('Endpoint ID', { exact: true }).fill(id);
  await editor
    .getByLabel('Display name', { exact: true })
    .fill('Synthetic reviewed endpoint');
  await editor
    .getByLabel('Base URL', { exact: true })
    .fill('http://127.0.0.1:9/v1');
  await openHomeThroughNavigation(page);
  await openSettingsRouteFromHome(page, {
    linkName: 'Providers',
    path: '/app-v2/settings/providers',
    headingName: 'Providers',
  });
  await expect(editor.getByLabel('Endpoint ID', { exact: true })).toHaveValue(
    id,
  );
  const confirm = async () => {
    await editor
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    await expect(
      editor.getByText(
        'Review complete. Confirm the displayed action and target.',
        { exact: true },
      ),
    ).toBeVisible();
    await editor
      .getByRole('button', { name: 'Confirm configuration', exact: true })
      .click();
    await expect(
      editor.getByText('Configuration saved. No model was started.', {
        exact: true,
      }),
    ).toBeVisible();
    await editor
      .getByRole('button', { name: 'Reload saved configuration', exact: true })
      .click();
  };
  await confirm();
  await editor
    .getByRole('button', {
      name: 'Edit Synthetic reviewed endpoint',
      exact: true,
    })
    .click();
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `provider-configuration-${appearance}`);
    await accessibility(page, info, `provider-configuration-${appearance}`);
  }
  await editor
    .getByLabel('Display name', { exact: true })
    .fill('Synthetic edited endpoint');
  await confirm();
  await editor
    .getByRole('button', {
      name: 'Edit Synthetic edited endpoint',
      exact: true,
    })
    .click();
  await editor
    .getByRole('combobox', { name: 'Configuration action', exact: true })
    .selectOption('provider.endpoint.delete');
  await confirm();
  await expect(
    editor.getByRole('button', {
      name: 'Edit Synthetic edited endpoint',
      exact: true,
    }),
  ).toHaveCount(0);
});

test('MCP tested tools retain their review and accept the saved catalog without retesting', async ({
  page,
}, info) => {
  const headers = {
    'X-Fixture-Token': process.env.ROW_BOT_BROWSER_CONTROL_TOKEN!,
    Origin: new URL(process.env.ROW_BOT_BROWSER_BASE_URL!).origin,
  };
  expect(
    (await page.request.post('/__p4_fixture/mcp-catalog', { headers })).ok(),
  ).toBe(true);
  await page.goto('/app-v2/settings/mcp');
  await page
    .getByRole('button', {
      name: 'Connection Synthetic lifecycle',
      exact: true,
    })
    .click();
  const connection = page.getByRole('region', {
    name: 'MCP connection',
    exact: true,
  });
  // Test is reviewed by the server and runs in one step.
  await connection.getByRole('button', { name: 'Test', exact: true }).click();
  await expect(
    connection.getByText(
      'Test completed and the temporary connection closed.',
      { exact: true },
    ),
  ).toBeVisible();
  const catalog = page.getByRole('region', {
    name: 'Accept tested MCP tools',
    exact: true,
  });
  await expect(
    catalog.getByText('3 matching tested tools.', { exact: true }),
  ).toBeVisible();
  await expect(
    catalog.getByRole('listitem').filter({ hasText: 'delete_record' }),
  ).toContainText('Disabled after acceptance. Approval required.');
  // The tested catalog is retained by its owner across navigation.
  await openHomeThroughNavigation(page);
  await openSettingsRouteFromHome(page, {
    linkName: 'MCP',
    path: '/app-v2/settings/mcp',
    headingName: 'MCP',
  });
  await catalog
    .getByRole('button', { name: 'Accept tools', exact: true })
    .click();
  await expect(
    catalog.getByText(
      'Tested tools accepted. Review saved tool permissions before connecting; no server was retested or started.',
      { exact: true },
    ),
  ).toBeVisible();
  expect(
    (
      await (
        await page.request.get('/__p4_fixture/mcp-catalog', { headers })
      ).json()
    ).calls,
  ).toEqual(['connect', 'list_tools']);
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `mcp-tested-catalog-${appearance}`);
    await accessibility(page, info, `mcp-tested-catalog-${appearance}`);
  }
});

test('MCP runtime reviews survive navigation and explicitly test connect disconnect the owned fake session', async ({
  page,
}, info) => {
  const seed = await page.request.post('/__p4_fixture/mcp-runtime', {
    headers: {
      'X-Fixture-Token': process.env.ROW_BOT_BROWSER_CONTROL_TOKEN!,
      Origin: new URL(process.env.ROW_BOT_BROWSER_BASE_URL!).origin,
    },
  });
  expect(seed.ok()).toBe(true);
  await page.goto('/app-v2/settings/mcp');
  await page
    .getByRole('button', {
      name: 'Connection Synthetic lifecycle',
      exact: true,
    })
    .click();
  const connection = page.getByRole('region', {
    name: 'MCP connection',
    exact: true,
  });
  // Test, Connect and Disconnect are reviewed by the server and run in one
  // step; the connection owner retains the outcome across navigation.
  await connection.getByRole('button', { name: 'Test', exact: true }).click();
  const tested = connection.getByText(
    'Test completed and the temporary connection closed.',
    { exact: true },
  );
  await expect(tested).toBeVisible();
  await openHomeThroughNavigation(page);
  await openSettingsRouteFromHome(page, {
    linkName: 'MCP',
    path: '/app-v2/settings/mcp',
    headingName: 'MCP',
  });
  await expect(tested).toBeVisible();
  await connection
    .getByRole('button', { name: 'Connect', exact: true })
    .click();
  await expect(
    connection.getByText('Connected.', { exact: true }),
  ).toBeVisible();
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `mcp-connected-${appearance}`);
    await accessibility(page, info, `mcp-connected-${appearance}`);
  }
  await connection
    .getByRole('button', { name: 'Disconnect', exact: true })
    .click();
  await expect(
    connection.getByText('Connection cleanup completed.', { exact: true }),
  ).toBeVisible();
  await expect(
    connection.getByRole('button', { name: 'Connect', exact: true }),
  ).toBeEnabled();
});

test.skip('Subscription accounts retain the reviewed sign-in and publish disconnect recover synthetic credentials', async ({
  page,
}, info) => {
  const seeded = await page.request.post('/__p4_fixture/subscriptions', {
    headers: {
      'X-Fixture-Token': process.env.ROW_BOT_BROWSER_CONTROL_TOKEN!,
      Origin: new URL(process.env.ROW_BOT_BROWSER_BASE_URL!).origin,
    },
  });
  expect(seeded.ok()).toBe(true);
  await page.goto('/app-v2/settings/providers');
  const editor = page.getByRole('region', {
    name: 'Subscription accounts',
    exact: true,
  });
  await editor
    .getByRole('button', { name: 'Review sign-in', exact: true })
    .click();
  await expect(
    editor.getByRole('region', { name: 'Review account action' }),
  ).toContainText('Confirm start for ChatGPT / Codex');
  await openHomeThroughNavigation(page);
  await openSettingsRouteFromHome(page, {
    linkName: 'Providers',
    path: '/app-v2/settings/providers',
    headingName: 'Providers',
  });
  await editor
    .getByRole('button', { name: 'Confirm account action', exact: true })
    .click();
  await expect(editor.getByLabel('Device code', { exact: true })).toHaveValue(
    'SYNTHETIC',
  );
  await expect(
    editor.getByRole('link', { name: 'Open ChatGPT / Codex sign-in' }),
  ).toHaveAttribute('href', 'https://example.invalid/device');
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `subscription-signin-${appearance}`);
    await accessibility(page, info, `subscription-signin-${appearance}`);
  }
  await editor
    .getByRole('button', { name: 'Review login check', exact: true })
    .click();
  await editor
    .getByRole('button', { name: 'Confirm account action', exact: true })
    .click();
  await expect(editor.getByText(/Saved status: saved/)).toBeVisible();
  await expect(editor.getByLabel('Device code', { exact: true })).toHaveCount(
    0,
  );
  await editor
    .getByRole('button', { name: 'Review disconnect', exact: true })
    .click();
  await editor
    .getByRole('button', { name: 'Confirm account action', exact: true })
    .click();
  await expect(editor.getByText(/Saved status: disconnected/)).toBeVisible();
  await editor
    .getByRole('button', { name: 'Review account recovery', exact: true })
    .click();
  await editor
    .getByRole('button', { name: 'Confirm account action', exact: true })
    .click();
  await expect(editor.getByText(/Saved status: saved/)).toBeVisible();
});

test.skip('Subscription options retain exact reviews and save reference override reset through canonical owners', async ({
  page,
}, info) => {
  const seeded = await page.request.post('/__p4_fixture/subscription-options', {
    headers: {
      'X-Fixture-Token': process.env.ROW_BOT_BROWSER_CONTROL_TOKEN!,
      Origin: new URL(process.env.ROW_BOT_BROWSER_BASE_URL!).origin,
    },
  });
  expect(seeded.ok()).toBe(true);
  await page.goto('/app-v2/settings/providers');
  const editor = page.getByRole('region', {
    name: 'Subscription account options',
    exact: true,
  });
  await editor
    .getByRole('button', { name: 'Review Codex CLI reference', exact: true })
    .click();
  await editor
    .getByRole('button', { name: 'Confirm account option', exact: true })
    .click();
  await expect(
    editor.getByText('Codex CLI: metadata reference saved', { exact: true }),
  ).toBeVisible();
  await editor
    .getByRole('button', { name: 'Review Claude Code reference', exact: true })
    .click();
  await editor
    .getByRole('button', { name: 'Confirm account option', exact: true })
    .click();
  await expect(
    editor.getByText('Claude Code: metadata reference saved', { exact: true }),
  ).toBeVisible();
  await editor
    .getByLabel('xAI OAuth client ID override', { exact: true })
    .fill('synthetic-browser-client');
  await editor
    .getByRole('button', { name: 'Review client ID override', exact: true })
    .click();
  await openHomeThroughNavigation(page);
  await expectFocusedHome(page);
  await openSettingsRouteFromHome(page, {
    linkName: 'Providers',
    path: '/app-v2/settings/providers',
    headingName: 'Providers',
  });
  await expect(
    editor.getByRole('status').filter({ hasText: 'Reviewed:' }),
  ).toContainText('synthetic-browser-client');
  await editor
    .getByRole('button', { name: 'Confirm account option', exact: true })
    .click();
  await expect(
    editor.getByText(/xAI OAuth client source: override/),
  ).toBeVisible();
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `subscription-options-${appearance}`);
    await accessibility(page, info, `subscription-options-${appearance}`);
  }
  await editor
    .getByRole('button', { name: 'Review reset to default', exact: true })
    .click();
  await editor
    .getByRole('button', { name: 'Confirm account option', exact: true })
    .click();
  await expect(
    editor.getByLabel('xAI OAuth client ID override', { exact: true }),
  ).toHaveValue('');
  await expect(
    editor.getByText(/xAI OAuth client source: override/),
  ).toHaveCount(0);
});

test('MCP saved permissions preserve mandatory approval and apply explicit reviewed access changes', async ({
  page,
}, info) => {
  const seeded = await page.request.post('/__p4_fixture/mcp-policy', {
    headers: {
      'X-Fixture-Token': process.env.ROW_BOT_BROWSER_CONTROL_TOKEN!,
      Origin: new URL(process.env.ROW_BOT_BROWSER_BASE_URL!).origin,
    },
  });
  expect(seeded.ok()).toBe(true);
  await page.goto('/app-v2/settings/mcp');
  // Global permissions live behind their own disclosure; each change is
  // reviewed by the server and saved in one step.
  const disclosure = page.locator('details', {
    has: page.locator('summary', { hasText: 'Saved MCP permissions' }),
  });
  await disclosure.locator('summary').click();
  const globals = disclosure.getByRole('region', {
    name: 'Saved MCP permissions',
    exact: true,
  });
  await globals
    .getByRole('button', { name: 'Disable MCP access', exact: true })
    .click();
  await expect(globals.getByText(/Permission saved/)).toBeVisible();
  await page
    .getByRole('button', {
      name: 'Connection Synthetic lifecycle',
      exact: true,
    })
    .click();
  const permissions = page
    .getByRole('region', { name: 'Saved MCP permissions', exact: true })
    .last();
  await expect(
    permissions.getByRole('button', {
      name: 'Disable delete_record approval',
      exact: true,
    }),
  ).toBeDisabled();
  for (const action of [
    'Disable Server access',
    'Disable read_record access',
    'Enable read_record approval',
    'Enable Resource access',
    'Enable Prompt access',
  ]) {
    await permissions
      .getByRole('button', { name: action, exact: true })
      .click();
    await expect(permissions.getByText(/Permission saved/)).toBeVisible();
    await permissions
      .getByRole('button', { name: 'Refresh permissions', exact: true })
      .click();
    await expect(
      permissions.getByText('Saved permissions: available.', { exact: true }),
    ).toBeVisible();
  }
  await expect(
    permissions.getByRole('group', { name: 'Server access', exact: true }),
  ).toContainText('Disabled');
  await expect(
    permissions.getByRole('group', { name: 'read_record access', exact: true }),
  ).toContainText('Disabled');
  await expect(
    permissions.getByRole('group', {
      name: 'read_record approval',
      exact: true,
    }),
  ).toContainText('Enabled');
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `mcp-permissions-${appearance}`);
    await accessibility(page, info, `mcp-permissions-${appearance}`);
  }
});

test('Document removal retains its review and original partial cleanup until explicit recovery', async ({
  page,
}, info) => {
  const seeded = await page.request.post('/__p4_fixture/document-removal', {
    headers: {
      'X-Fixture-Token': process.env.ROW_BOT_BROWSER_CONTROL_TOKEN!,
      Origin: new URL(process.env.ROW_BOT_BROWSER_BASE_URL!).origin,
    },
  });
  expect(seeded.ok()).toBe(true);
  const target = await seeded.json();
  await page.goto('/app-v2/settings/documents');
  let row = await findDocumentRow(page, target);
  await row
    .getByRole('button', { name: `Remove ${target.name}`, exact: true })
    .click();
  const removal = page.getByRole('region', {
    name: 'Document removal',
    exact: true,
  });
  await removal
    .getByRole('button', { name: 'Remove document', exact: true })
    .click();
  await openHomeThroughNavigation(page);
  await expectFocusedHome(page);
  await openDocumentsFromHome(page);
  await removal
    .getByRole('button', { name: 'Confirm document removal', exact: true })
    .click();
  await expect(
    removal.getByText('Removal is incomplete. Completed stages are saved.', {
      exact: true,
    }),
  ).toBeVisible();
  await removal
    .getByRole('button', { name: 'Refresh original removal', exact: true })
    .click();
  await expect(
    removal.getByText('Removal is incomplete. Completed stages are saved.', {
      exact: true,
    }),
  ).toBeVisible();
  await removal
    .getByRole('button', { name: 'Continue cleanup', exact: true })
    .click();
  await removal
    .getByRole('button', { name: 'Confirm remaining cleanup', exact: true })
    .click();
  await expect(
    removal.getByText('Removal complete.', { exact: true }),
  ).toBeVisible();
  await expect(
    removal.getByText('Derived knowledge removed: 1', { exact: true }),
  ).toBeVisible();
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `document-removal-${appearance}`);
    await accessibility(page, info, `document-removal-${appearance}`);
  }
  await page.reload();
  await expect(
    page.getByRole('heading', { name: 'Documents', exact: true }),
  ).toBeVisible();
  // The canonical owner retains ingestion history, while removing search data.
  row = await findDocumentRow(page, target);
  await row.locator('summary').click();
  await expect(
    row.getByText('Removed from search; ingestion history retained', {
      exact: true,
    }),
  ).toBeVisible();
  const actual = await page.request.get(
    `/__p4_fixture/document-removal/${target.document_id}`,
    {
      headers: {
        'X-Fixture-Token': process.env.ROW_BOT_BROWSER_CONTROL_TOKEN!,
      },
    },
  );
  expect(actual.ok()).toBe(true);
  expect(await actual.json()).toEqual({
    status: 'complete',
    derived_count: 0,
    indexed: false,
    recovery_copies_retained: true,
    historical_job_retained: true,
  });
});

test('MCP settings retain reviewed private fields and save add edit rename import disabled', async ({
  page,
}, info) => {
  await page.goto('/app-v2/settings/mcp');
  const editor = page.getByRole('region', {
    name: 'MCP configuration',
    exact: true,
  });
  const name = `Synthetic MCP ${info.project.name}`;
  const renamed = `${name} renamed`;
  // The server editor opens from Add server behind its own disclosure.
  await editor.getByRole('button', { name: 'Add server', exact: true }).click();
  await editor.getByLabel('Server name', { exact: true }).fill(name);
  await editor
    .getByLabel('New command', { exact: true })
    .fill('synthetic-unused-command');
  await editor
    .getByLabel('New arguments (JSON array)', { exact: true })
    .fill('["--synthetic-private"]');
  await editor
    .getByLabel('Additional settings (JSON)', { exact: true })
    .fill('{"env":{"SYNTHETIC":"private-test-value"}}');
  // The unsaved draft, including write-only fields, is retained by its owner
  // across navigation and reopens the editor.
  await openHomeThroughNavigation(page);
  await openSettingsRouteFromHome(page, {
    linkName: 'MCP',
    path: '/app-v2/settings/mcp',
    headingName: 'MCP',
  });
  await expect(editor.getByLabel('New command', { exact: true })).toHaveValue(
    'synthetic-unused-command',
  );
  // Saving is reviewed by the server and applied, disabled, in one step.
  const save = async () => {
    await editor
      .getByRole('button', { name: 'Save Disabled', exact: true })
      .click();
    await expect(editor.getByText(/^Saved disabled\./)).toBeVisible();
    await chooseFromMenu(editor, 'More MCP actions', 'Refresh');
    await expect(
      editor.getByRole('button', { name: 'Save Disabled', exact: true }),
    ).toBeEnabled();
  };
  await save();
  // Row verbs other than Connection sit in the server's ⋯ menu.
  await chooseFromMenu(editor, `More actions for ${name}`, `Edit ${name}`);
  await expect(editor.getByLabel('New command', { exact: true })).toHaveValue(
    '',
  );
  await expect(
    editor.getByLabel('Additional settings (JSON)', { exact: true }),
  ).toHaveValue('');
  await editor
    .getByLabel('Additional settings (JSON)', { exact: true })
    .fill('{"output_limit":500}');
  await save();
  await chooseFromMenu(editor, `More actions for ${name}`, `Rename ${name}`);
  await editor.getByLabel('New server name', { exact: true }).fill(renamed);
  await save();
  await expect(
    editor.getByRole('listitem').filter({ hasText: renamed }),
  ).toContainText('Disabled');
  await editor
    .getByRole('combobox', { name: 'Operation', exact: true })
    .selectOption('import');
  await editor.getByLabel('Server import JSON', { exact: true }).fill(
    JSON.stringify({
      mcpServers: {
        [`Imported ${info.project.name}`]: {
          command: 'synthetic-imported-command',
        },
      },
    }),
  );
  await save();
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `mcp-settings-${appearance}`);
    await accessibility(page, info, `mcp-settings-${appearance}`);
  }
});
