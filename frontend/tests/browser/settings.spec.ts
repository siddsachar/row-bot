import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
  writeEvidence,
} from './evidence';
import { blockFixtureServiceWorkers } from './unified-helpers';
import { installDesktopFolderBridge } from './surface-helpers';
import type { Locator, Page } from '@playwright/test';
import { createHash } from 'node:crypto';

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
  // The row is the one whose own Remove button names the document (the
  // saved id is no longer shown, U59).
  const row = page.getByRole('listitem').filter({
    has: page.getByRole('button', {
      name: `Remove ${target.name}`,
      exact: true,
    }),
  });
  await expect(row).toBeVisible();
  return row;
}

/** On a phone a memory's detail is a full-width drawer over the toolbar. */
async function closeDetailOnPhone(page: Page, detail: Locator) {
  if ((page.viewportSize()?.width ?? 1280) >= 768) return;
  await detail
    .getByRole('button', { name: 'Close memory detail', exact: true })
    .click();
  await expect(detail).toHaveCount(0);
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
  // Below the desktop breakpoint the sidebar is a drawer.
  if ((page.viewportSize()?.width ?? 0) >= 1024) {
    await expect(navigation).toBeVisible();
  } else {
    await page
      .getByRole('button', { name: 'Toggle navigation', exact: true })
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
    'system',
    'access',
    'updates',
    'data',
  ] as const;
  const labels: Record<string, string> = {
    mcp: 'MCP',
    knowledge: 'Memory',
    access: 'Devices & remote access',
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
  // Six named groups, every page listed (no collapsed categories).
  await expect(settingsNavigation.getByRole('list')).toHaveCount(6);
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
  // The vault is picked in the desktop window (B280): a stand-in bridge
  // "chooses" the fixture's synthetic vault folder.
  await installDesktopFolderBridge(page);
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
    'Processing started.',
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
      name: 'Check processing',
      exact: true,
    })
    .click();
  await expect(processing.getByRole('status')).toContainText(
    'Processing started.',
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

test('Managed runtimes install the reviewed exact archive with one Install and keep their state across routes', async ({
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
  // Runtimes are one status row at the top of the MCP page (B262).
  const runtime = page.getByRole('group', {
    name: 'Node.js runtime',
    exact: true,
  });
  await expect(
    runtime.getByText('Not installed', { exact: true }),
  ).toBeVisible();
  expect((await saved()).calls).toEqual([]);
  // One Install: the server reviews the metadata resolution and then the
  // pinned archive, and the row follows it until it is installed.
  await runtime
    .getByRole('button', { name: 'Install Node.js', exact: true })
    .click();
  await expect(
    runtime.getByText('Installed v1.2.3', { exact: true }),
  ).toBeVisible();
  expect(await saved()).toEqual({
    calls: ['resolve', 'download'],
    installed: true,
    synthetic_bytes: true,
  });
  await openHomeThroughNavigation(page);
  await openSettingsRouteFromHome(page, {
    linkName: 'MCP',
    path: '/app-v2/settings/mcp',
    headingName: 'MCP',
  });
  // The installed state is read again, not repeated, after navigation.
  await expect(
    runtime.getByText('Installed v1.2.3', { exact: true }),
  ).toBeVisible();
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(
      page,
      info,
      `runtime-installation-installed-${appearance}`,
    );
    await accessibility(
      page,
      info,
      `runtime-installation-installed-${appearance}`,
    );
  }
  await page
    .getByRole('button', { name: 'More runtime actions', exact: true })
    .click();
  await page
    .getByRole('menuitem', { name: 'Check again', exact: true })
    .click();
  expect((await saved()).calls).toEqual(['resolve', 'download']);
});

/** Home › Knowledge as a list (the graph needs WebGL), then one memory. */
async function openKnowledgeMemory(page: Page, subject: string) {
  await page.getByRole('radio', { name: 'List', exact: true }).click();
  await page
    .getByRole('table', { name: 'Knowledge entities', exact: true })
    .getByRole('button', { name: subject, exact: true })
    .click();
  const inspector = page.getByRole('dialog', { name: subject, exact: true });
  await expect(inspector).toBeVisible();
  return inspector;
}

// B264: memories are browsed and edited in Knowledge; Settings › Memory keeps
// the settings and leads there.
test('Knowledge adds memory, searches the library, retains editor drafts, and confirms lifecycle changes', async ({
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
  await expect(page.getByRole('searchbox')).toHaveCount(0);
  await page.getByRole('link', { name: 'Open Knowledge', exact: true }).click();
  // `/?tab=knowledge` under the /app-v2 base: with or without its slash.
  await expect(page).toHaveURL(/\/app-v2\/?\?tab=knowledge$/);
  // Phase 13: Add memory opens a blank editor (closing it keeps nothing).
  await page.getByRole('button', { name: 'Add memory', exact: true }).click();
  const adding = page.getByRole('dialog', { name: 'Add memory', exact: true });
  await expect(adding.getByRole('textbox', { name: 'Subject' })).toHaveValue(
    '',
  );
  await page.keyboard.press('Escape');
  await expect(adding).toHaveCount(0);
  // Search reads the whole saved library, not only the memories in the map.
  const searched = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.pathname.endsWith('/knowledge/entities') &&
      url.searchParams.get('query') === 'Phase 4 knowledge 002'
    );
  });
  await page
    .getByRole('combobox', { name: 'Search memories', exact: true })
    .fill('Phase 4 knowledge 002');
  expect((await searched).ok()).toBe(true);
  await page
    .getByRole('listbox', { name: 'Matching memories', exact: true })
    .getByRole('option', { name: /^Phase 4 knowledge 002/ })
    .click();
  let inspector = page.getByRole('dialog', {
    name: 'Phase 4 knowledge 002',
    exact: true,
  });
  await inspector
    .getByRole('button', { name: 'Edit memory', exact: true })
    .click();
  const dialog = page.getByRole('dialog', { name: 'Edit knowledge' });
  const editor = dialog;
  await expect(dialog).toBeVisible();
  await editor
    .getByRole('textbox', { name: 'Description', exact: true })
    .fill('Synthetic retained modal draft');
  // Close without saving: the owner retains the unsaved draft.
  await dialog.getByRole('button', { name: 'Close knowledge editor' }).click();
  await expect(dialog).toHaveCount(0);
  await openSettingsRouteFromHome(page, {
    linkName: 'Memory',
    path: '/app-v2/settings/knowledge',
    headingName: 'Memory',
  });
  await page.getByRole('link', { name: 'Open Knowledge', exact: true }).click();
  inspector = await openKnowledgeMemory(page, 'Phase 4 knowledge 002');
  await inspector
    .getByRole('button', { name: 'Edit memory', exact: true })
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
      'Knowledge saved. Search and the wiki catch up in a moment. Reload the saved entry to continue.',
      { exact: true },
    ),
  ).toBeVisible();
  await editor.getByRole('button', { name: /reload saved entry$/i }).click();
  await dialog.getByRole('button', { name: 'Close knowledge editor' }).click();
  await expect(dialog).toHaveCount(0);
  // The memory's detail shows what was saved.
  await expect(
    inspector.getByText('Synthetic retained modal draft', { exact: true }),
  ).toBeVisible();
  // Archive and restore are reviewed by the server and applied in one step.
  await inspector
    .getByRole('button', { name: 'Archive memory', exact: true })
    .click();
  await expect(
    page.getByText('Phase 4 knowledge 002 archived.', { exact: true }),
  ).toBeVisible();
  await closeDetailOnPhone(page, inspector);
  await page.getByRole('button', { name: 'Filters', exact: true }).click();
  await page
    .getByRole('dialog', { name: 'Memory filters', exact: true })
    .getByRole('combobox', { name: 'Status', exact: true })
    .selectOption('archived');
  await page.keyboard.press('Escape');
  const list = page.getByRole('table', {
    name: 'Knowledge entities',
    exact: true,
  });
  const archived = list.getByRole('button', {
    name: 'Phase 4 knowledge 002',
    exact: true,
  });
  await expect(archived).toBeVisible();
  // Closed on a phone above: open its detail again.
  if (!(await inspector.isVisible())) await archived.click();
  await inspector
    .getByRole('button', { name: 'Restore memory', exact: true })
    .click();
  await expect(
    page.getByText('Phase 4 knowledge 002 restored.', { exact: true }),
  ).toBeVisible();
  await expect(
    list.getByRole('button', { name: 'Phase 4 knowledge 002', exact: true }),
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
  await page.goto('/app-v2/?tab=knowledge');
  let inspector = await openKnowledgeMemory(page, 'Phase 4 knowledge 000');
  await inspector
    .getByRole('button', { name: 'Merge or replace', exact: true })
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
    .getByRole('button', { name: 'Phase 4 knowledge 001 · Fact', exact: true })
    .click();
  const dialog = page.getByRole('dialog', { name: 'Edit knowledge' });
  await dialog.getByRole('button', { name: 'Close knowledge editor' }).click();
  await expect(dialog).toHaveCount(0);
  await openHomeThroughNavigation(page);
  await page.getByRole('tab', { name: 'Knowledge', exact: true }).click();
  inspector = await openKnowledgeMemory(page, 'Phase 4 knowledge 000');
  await inspector
    .getByRole('button', { name: 'Edit memory', exact: true })
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
    editor.getByText('Status: Superseded.', { exact: true }),
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
  // Name and compact size live in the Advanced disclosure (B258).
  const advanced = preferences.locator('.settings-advanced details');
  const openAdvanced = async () => {
    if (!(await advanced.evaluate((node) => (node as HTMLDetailsElement).open)))
      await advanced.locator('summary').click();
  };
  await openAdvanced();
  await preferences
    .getByLabel('Buddy name', { exact: true })
    .fill('Synthetic companion');
  const bubbles = preferences.getByRole('radiogroup', {
    name: 'Bubbles',
    exact: true,
  });
  await bubbles.getByRole('radio', { name: 'Chatty', exact: true }).click();
  // Phase 13 (decision 19): each change saves when it is made; the floating
  // notice confirms it with Undo (B258).
  await expect(
    page
      .locator('.toast')
      .filter({ hasText: 'Bubbles saved' })
      .getByRole('button', { name: 'Undo', exact: true }),
  ).toBeVisible();
  // The generation flow opens from the last look tile.
  await preferences.getByRole('button', { name: /New look/ }).click();
  const describe = page.getByLabel('Describe your Buddy', { exact: true });
  // The pack's description seeds the empty field once it loads; replace it
  // after that, or typing races the seed.
  await expect(describe).not.toHaveValue('');
  await describe.fill('Retained synthetic description');
  await page.keyboard.press('Escape');
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
  await preferences.getByRole('button', { name: /New look/ }).click();
  await expect(
    page.getByLabel('Describe your Buddy', { exact: true }),
  ).toHaveValue('Retained synthetic description');
  // Its Close button: a notice raised meanwhile (the name's autosave) takes
  // Escape first.
  await page
    .getByRole('dialog', { name: 'New look', exact: true })
    .getByRole('button', { name: 'Close dialog', exact: true })
    .click();
  await expect(
    preferences.getByRole('button', {
      name: 'Save Buddy preferences',
      exact: true,
    }),
  ).toHaveCount(0);
  await expect(
    bubbles.getByRole('radio', { name: 'Chatty', exact: true }),
  ).toHaveAttribute('aria-checked', 'true');
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
  await preferences.getByRole('button', { name: /New look/ }).click();
  await page.getByLabel('Describe your Buddy', { exact: true }).fill('');
  await page.keyboard.press('Escape');
  await page.reload();
  await expect(
    preferences.getByLabel('Buddy name', { exact: true }),
  ).toHaveValue('Synthetic companion');
  await expect(
    bubbles.getByRole('radio', { name: 'Chatty', exact: true }),
  ).toHaveAttribute('aria-checked', 'true');
});

test('Buddy plays the saved bundled motion and switches to its still for reduced motion', async ({
  page,
}, info) => {
  test.skip(
    info.project.name === 'chromium-phone',
    'Headless mobile emulation does not autoplay the muted motion; playback is the same at every width.',
  );
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
  // The default is a searchable picker (U12): its button names the model.
  const defaultPicker = models.getByRole('button', { name: 'Brain model' });
  await expect(defaultPicker).toBeVisible();
  const index = /Saved example 104\b/.test(
    (await defaultPicker.textContent()) ?? '',
  )
    ? '103'
    : '104';
  const label = 'Saved example ' + index;
  await models
    .getByRole('button', { name: /^Open / })
    .first()
    .click();
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
  await expect(defaultPicker).toContainText(label);
  // Provider connections sit in the page's ⋯ (B229).
  await chooseFromMenu(
    page.locator('.settings-pane-header'),
    'More model actions',
    'Provider connections',
  );
  await expect(page).toHaveURL(/\/app-v2\/settings\/providers$/);
  await page.goBack();
  await expect(defaultPicker).toContainText(label);
  await assertNoOverflow(page);
  await screenshot(page, info, 'models-default-retained');
  await accessibility(page, info, 'models-default-retained');
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
  // Emptied, the catalog keeps only models a job uses or you pinned (B226).
  await expect(models.getByText(/^Showing \d of \d models$/)).toBeVisible();
  const kept = await models.locator('.settings-model-row-list > li').all();
  for (const row of kept)
    await expect(
      row
        .getByText('Default', { exact: true })
        .or(row.getByRole('button', { name: /^Unpin / }))
        .first(),
    ).toBeVisible();
  if (!kept.length)
    await expect(models.getByText('No matching models')).toBeVisible();
  await assertNoOverflow(page);
  await screenshot(page, info, 'saved-models-empty');
  await accessibility(page, info, 'saved-models-empty');
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
  // A server's details open in a drawer from its name (B262).
  await page
    .getByRole('button', {
      name: 'Synthetic lifecycle details',
      exact: true,
    })
    .click();
  const details = page.getByRole('dialog', {
    name: 'Synthetic lifecycle',
    exact: true,
  });
  const connection = details.getByRole('region', {
    name: 'Connection',
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
  const catalog = details.getByRole('region', {
    name: 'Accept tested MCP tools',
    exact: true,
  });
  await expect(
    catalog.getByText('3 tools found.', { exact: true }),
  ).toBeVisible();
  await expect(
    catalog.getByRole('listitem').filter({ hasText: 'delete_record' }),
  ).toContainText('Stays off until you turn it on · asks first');
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
      'Tools accepted. Check their switches under Tools before you connect; nothing was retested or started.',
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
      name: 'Synthetic lifecycle details',
      exact: true,
    })
    .click();
  const connection = page
    .getByRole('dialog', { name: 'Synthetic lifecycle', exact: true })
    .getByRole('region', { name: 'Connection', exact: true });
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
    connection.getByText('Connected', { exact: true }),
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
    connection.getByText('Disconnected.', { exact: true }),
  ).toBeVisible();
  await expect(
    connection.getByRole('button', { name: 'Connect', exact: true }),
  ).toBeEnabled();
  // The server's row follows with one action by state (B262).
  await page
    .getByRole('button', { name: 'Close server details', exact: true })
    .click();
  const connect = page.getByRole('button', {
    name: 'Connect Synthetic lifecycle',
    exact: true,
  });
  const disconnect = page.getByRole('button', {
    name: 'Disconnect Synthetic lifecycle',
    exact: true,
  });
  await connect.click();
  await disconnect.click();
  await expect(connect).toBeVisible();
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
  // "Use MCP servers" is the page's first switch; each change is reviewed by
  // the server and saved in one step (B262).
  const useMcp = page.getByRole('switch', {
    name: 'Use MCP servers',
    exact: true,
  });
  await expect(useMcp).toBeChecked();
  await useMcp.click();
  await expect(useMcp).toBeEnabled();
  await expect(useMcp).not.toBeChecked();
  await page
    .getByRole('button', {
      name: 'Synthetic lifecycle details',
      exact: true,
    })
    .click();
  const permissions = page
    .getByRole('dialog', { name: 'Synthetic lifecycle', exact: true })
    .getByRole('region', { name: 'Saved MCP permissions', exact: true });
  const locked = permissions.getByRole('switch', {
    name: 'Ask before delete_record runs',
    exact: true,
  });
  await expect(locked).toBeChecked();
  await expect(locked).toBeDisabled();
  for (const [name, after] of [
    ['Server access', false],
    ['Use read_record', false],
    ['Ask before read_record runs', true],
    ['Resource access', true],
    ['Prompt access', true],
  ] as const) {
    const control = permissions.getByRole('switch', { name, exact: true });
    await control.click();
    await expect(permissions.getByText(/Permission saved/)).toBeVisible();
    await expect(control).toBeEnabled();
    await permissions
      .getByRole('button', { name: 'Refresh permissions', exact: true })
      .click();
    if (after) await expect(control).toBeChecked();
    else await expect(control).not.toBeChecked();
  }
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
  // Add server opens a dialog; Manual fills in the details (B262).
  const addDialog = page.getByRole('dialog', {
    name: 'Add a server',
    exact: true,
  });
  await editor.getByRole('button', { name: 'Add server', exact: true }).click();
  await addDialog.getByRole('radio', { name: 'Manual', exact: true }).click();
  await addDialog.getByLabel('Server name', { exact: true }).fill(name);
  await addDialog
    .getByLabel('Command', { exact: true })
    .fill('synthetic-unused-command');
  // Arguments one per line; environment values are masked name/value rows.
  await addDialog
    .getByLabel('Arguments (one per line)', { exact: true })
    .fill('--synthetic-private');
  await addDialog
    .getByRole('button', { name: 'Add variable', exact: true })
    .click();
  await addDialog
    .getByLabel('Environment variables name 1', { exact: true })
    .fill('SYNTHETIC');
  const secretValue = addDialog.getByLabel('Environment variables value 1', {
    exact: true,
  });
  await secretValue.fill('private-test-value');
  await expect(secretValue).toHaveAttribute('type', 'password');
  // Closed (and across navigation), the unsaved draft, write-only fields
  // included, is kept by its owner; the page offers to continue it.
  await page.keyboard.press('Escape');
  await expect(addDialog).toBeHidden();
  await openHomeThroughNavigation(page);
  await openSettingsRouteFromHome(page, {
    linkName: 'MCP',
    path: '/app-v2/settings/mcp',
    headingName: 'MCP',
  });
  await expect(editor.getByText('You have an unsaved server.')).toBeVisible();
  await editor.getByRole('button', { name: 'Continue', exact: true }).click();
  await addDialog.getByRole('radio', { name: 'Manual', exact: true }).click();
  await expect(addDialog.getByLabel('Command', { exact: true })).toHaveValue(
    'synthetic-unused-command',
  );
  // Each save is reviewed by the server; a server is added turned off, and
  // adding opens its details.
  await addDialog
    .getByRole('button', { name: 'Add turned off', exact: true })
    .click();
  await expect(addDialog).toBeHidden();
  await page
    .getByRole('button', { name: 'Close server details', exact: true })
    .click();
  await expect(editor.getByText(/^Added\. It stays turned off/)).toBeVisible();
  // Edit and Rename sit in the server's ⋯ menu; the write-only command and
  // additional settings are never read back.
  await chooseFromMenu(editor, `More actions for ${name}`, 'Edit settings…');
  const editDialog = page.getByRole('dialog', {
    name: `Edit ${name}`,
    exact: true,
  });
  await expect(
    editDialog.getByLabel('New command', { exact: true }),
  ).toHaveValue('');
  await editDialog.getByText('More settings', { exact: true }).click();
  await expect(
    editDialog.getByLabel('Additional settings (JSON)', { exact: true }),
  ).toHaveValue('');
  await editDialog
    .getByLabel('Additional settings (JSON)', { exact: true })
    .fill('{"output_limit":500}');
  await editDialog.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(editDialog).toBeHidden();
  await expect(editor.getByText(/^Saved\. It stays turned off/)).toBeVisible();
  await chooseFromMenu(editor, `More actions for ${name}`, 'Rename…');
  const renameDialog = page.getByRole('dialog', {
    name: `Rename ${name}`,
    exact: true,
  });
  await renameDialog
    .getByLabel('New server name', { exact: true })
    .fill(renamed);
  await renameDialog
    .getByRole('button', { name: 'Rename', exact: true })
    .click();
  await expect(renameDialog).toBeHidden();
  await expect(
    editor.getByRole('button', { name: `${renamed} details`, exact: true }),
  ).toBeVisible();
  // Paste JSON adds each server in a standard mcpServers block, turned off.
  await editor.getByRole('button', { name: 'Add server', exact: true }).click();
  await addDialog
    .getByRole('radio', { name: 'Paste JSON', exact: true })
    .click();
  await addDialog
    .getByLabel('Server configuration (JSON)', { exact: true })
    .fill(
      JSON.stringify({
        mcpServers: {
          [`Imported ${info.project.name}`]: {
            command: 'synthetic-imported-command',
          },
        },
      }),
    );
  await addDialog
    .getByRole('button', { name: 'Add from JSON', exact: true })
    .click();
  await expect(addDialog).toBeHidden();
  await expect(
    editor.getByRole('button', {
      name: `Imported ${info.project.name} details`,
      exact: true,
    }),
  ).toBeVisible();
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `mcp-settings-${appearance}`);
    await accessibility(page, info, `mcp-settings-${appearance}`);
  }
});

async function seedKnowledge(page: Page, state: 'populated' | 'empty') {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated Phase 4 runner');
  const response = await page.request.post(`/__p4_fixture/knowledge/${state}`, {
    headers: { 'X-Fixture-Token': token, Origin: new URL(base).origin },
  });
  expect(response.ok(), await response.text()).toBe(true);
}

test.describe('Knowledge settings', () => {
  // The file-level hooks already block service workers and seed the same
  // system appearance; Knowledge also runs on the native network path.
  test.use({ nativeNetwork: true });

  // B264: the library's filters, search, record, activity and bulk deletion
  // live in Knowledge; Settings › Memory keeps the switch, the wiki vault and
  // Delete all.
  test('Knowledge keeps the library flows and Settings keeps memory, the wiki vault and Delete all', async ({
    page,
  }, info) => {
    await seedKnowledge(page, 'populated');
    await page.goto('/app-v2/?tab=knowledge');
    const stats = page.getByLabel('Knowledge statistics', { exact: true });
    await expect(stats).toBeVisible();
    // Earlier specs in the shared fixture may add entries.
    const total = Number(
      /^([\d,]+) memor/
        .exec((await stats.textContent())!)![1]
        .replaceAll(',', ''),
    );
    expect(total).toBeGreaterThanOrEqual(105);

    // The review queue reads the whole library.
    await stats.getByRole('button', { name: /needs? review$/ }).click();
    await expect(
      page
        .getByRole('region', { name: 'Needs review', exact: true })
        .getByRole('group', { name: 'Phase 4 knowledge 000', exact: true }),
    ).toBeVisible();

    // Status and memory type filters.
    await page.getByRole('radio', { name: 'List', exact: true }).click();
    const list = page.getByRole('table', {
      name: 'Knowledge entities',
      exact: true,
    });
    await page.getByRole('button', { name: 'Filters', exact: true }).click();
    const filters = page.getByRole('dialog', {
      name: 'Memory filters',
      exact: true,
    });
    await filters
      .getByRole('combobox', { name: 'Status', exact: true })
      .selectOption('archived');
    await expect(
      list.getByRole('button', { name: 'Phase 4 knowledge 001', exact: true }),
    ).toBeVisible();
    await expect(
      list.getByRole('button', { name: 'Phase 4 knowledge 002', exact: true }),
    ).toHaveCount(0);
    await filters
      .getByRole('combobox', { name: 'Status', exact: true })
      .selectOption('');
    await filters
      .getByRole('combobox', { name: 'Memory type', exact: true })
      .selectOption('core');
    await expect(
      list.getByRole('button', { name: 'Phase 4 knowledge 000', exact: true }),
    ).toBeVisible();
    await expect(
      list.getByRole('button', { name: 'Phase 4 knowledge 002', exact: true }),
    ).toHaveCount(0);
    await filters
      .getByRole('button', { name: 'Show everything', exact: true })
      .click();
    await page.keyboard.press('Escape');
    // Closed, the filters give focus back to their button: search after that.
    await expect(filters).toBeHidden();
    await expect(
      page.getByRole('button', { name: 'Filters', exact: true }),
    ).toBeFocused();

    // Search reads the whole library: these words sit past the part of the
    // description the map loads.
    await page
      .getByRole('combobox', { name: 'Search memories', exact: true })
      .fill('tail needle');
    await page
      .getByRole('listbox', { name: 'Matching memories', exact: true })
      .getByRole('option', { name: /^Phase 4 knowledge 104/ })
      .click();
    const inspector = page.getByRole('dialog', {
      name: 'Phase 4 knowledge 104',
      exact: true,
    });
    await expect(
      inspector.getByText('Long-term knowledge', { exact: true }),
    ).toBeVisible();
    await inspector.getByText('Details', { exact: true }).click();
    await expect(
      inspector.getByText('p4-entity-104', { exact: true }),
    ).toBeVisible();
    await expect(
      inspector.getByText('Synthetic browser evidence', { exact: true }),
    ).toBeVisible();
    const edit = inspector.getByRole('button', {
      name: 'Edit memory',
      exact: true,
    });
    await edit.click();
    const editor = page.getByRole('dialog', { name: 'Edit knowledge' });
    await expect(editor.getByRole('textbox', { name: 'Subject' })).toHaveValue(
      'Phase 4 knowledge 104',
    );
    await editor
      .getByRole('button', { name: 'Close knowledge editor' })
      .click();
    await expect(editor).toHaveCount(0);
    await expect(edit).toBeFocused();

    // Activity: what recall used and what changed.
    await closeDetailOnPhone(page, inspector);
    await page.getByRole('radio', { name: 'Activity', exact: true }).click();
    const recalls = page.getByRole('region', {
      name: 'Recall decisions',
      exact: true,
    });
    await expect(
      recalls.getByText('Memory used', { exact: true }),
    ).toBeVisible();
    await expect(
      recalls.getByText(/Phase 4 knowledge 000 \(0\.93\)/),
    ).toBeVisible();
    await expect(
      page
        .getByRole('region', { name: 'Memory changes', exact: true })
        .getByText('Mark needs review', { exact: true }),
    ).toBeVisible();
    for (const appearance of ['light', 'dark'] as const) {
      await page.emulateMedia({ colorScheme: appearance });
      await expect(page.locator('html')).toHaveAttribute(
        'data-theme',
        appearance,
      );
      await assertNoOverflow(page);
      await screenshot(page, info, `knowledge-parity-${appearance}`);
      await accessibility(page, info, `knowledge-parity-${appearance}`);
    }

    // Bulk deletion stays destructive: the reviewed selection needs a confirm.
    await page.getByRole('radio', { name: 'List', exact: true }).click();
    const memoryColumn = list.getByRole('button', {
      name: 'Memory',
      exact: true,
    });
    await memoryColumn.click();
    await memoryColumn.click();
    await list
      .getByRole('checkbox', {
        name: 'Select Phase 4 knowledge 104',
        exact: true,
      })
      .click();
    const selection = page.getByRole('toolbar', {
      name: 'Selected memories',
      exact: true,
    });
    await expect(selection).toContainText('1 selected');
    await selection
      .getByRole('button', { name: 'Delete selected memories', exact: true })
      .click();
    await page
      .getByRole('alertdialog', { name: "Delete 'Phase 4 knowledge 104'?" })
      .getByRole('button', { name: 'Delete memory', exact: true })
      .click();
    await expect(
      page.getByText('Phase 4 knowledge 104 deleted.', { exact: true }),
    ).toBeVisible();

    // Settings › Memory: settings only.
    await page.goto('/app-v2/settings/knowledge');
    await expect(page.getByRole('searchbox')).toHaveCount(0);
    const wiki = page.getByRole('region', { name: 'Wiki vault', exact: true });
    await expect(
      wiki.getByRole('button', { name: 'Browse', exact: true }),
    ).toBeVisible();
    await expect(
      wiki.getByRole('button', { name: 'Check vault sync', exact: true }),
    ).toBeVisible();
    const memory = page.getByRole('switch', { name: 'Enable Memory' });
    const wasEnabled = await memory.isChecked();
    // The memory setting is reviewed by the server and applied in one step.
    await memory.click();
    await expect(memory).toBeChecked({ checked: !wasEnabled });
    await expect(
      page.getByText(wasEnabled ? 'Memory off' : 'Memory on', { exact: true }),
    ).toBeVisible();
    // Later specs share this fixture: put the setting back.
    await memory.click();
    await expect(memory).toBeChecked({ checked: wasEnabled });

    // Store-wide deletion lives in the collapsed Danger zone.
    const dangerZone = page.locator('.settings-danger-zone details');
    await expect(dangerZone).not.toHaveAttribute('open', '');
    await dangerZone.locator('summary').click();
    await expect(dangerZone).toHaveAttribute('open', '');
    const deleteAll = page.getByRole('button', {
      name: /Delete all knowledge/,
    });
    await expect(deleteAll).toBeEnabled();
    await deleteAll.click();
    await expect(
      page.getByRole('region', { name: 'Reviewed knowledge deletion' }),
    ).toContainText(`${total - 1} entries`);
    await page
      .getByRole('button', { name: 'Confirm permanent deletion' })
      .click();
    await expect(
      page.getByRole('button', { name: 'Delete all knowledge (0)' }),
    ).toBeDisabled();
  });
});
