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

async function seed(page: Page, state: 'populated' | 'empty' | 'changed') {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated Phase 4 runner');
  const response = await page.request.post(`/__p4_fixture/catalog/${state}`, {
    headers: { 'X-Fixture-Token': token, Origin: new URL(base).origin },
  });
  expect(response.ok()).toBe(true);
}

async function expectPopulatedHomeLibraries(page: Page) {
  await expect(
    page.getByRole('heading', { name: 'Home', exact: true }),
  ).toBeVisible();
  for (const [regionName, browseName] of [
    ['Designer library', 'Browse all designs'],
    ['Developer library', 'Browse all workspaces'],
  ] as const) {
    const library = page.getByRole('region', {
      name: regionName,
      exact: true,
    });
    await expect(library.getByRole('listitem').first()).toBeVisible();
    await expect(
      library.getByRole('button', { name: browseName, exact: true }),
    ).toBeVisible();
  }
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
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  const row = page
    .getByRole('listitem')
    .filter({ hasText: target.document_id });
  await expect(row).toBeVisible();
  return row;
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
  const settings = page.getByRole('button', {
    name: 'Settings',
    exact: true,
  });
  await activateRoute(page, settings, {
    path: '/app-v2/settings',
    headingName: 'Settings',
  });

  const routeLink = page.getByRole('link', {
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
  await expect(
    channels.getByRole('heading', {
      name: 'Synthetic local channel',
      exact: true,
    }),
  ).toBeVisible();
  await channels.getByLabel(/^New Local label/).fill('browser-local');
  await channels
    .getByRole('button', { name: 'Review save Local label', exact: true })
    .click();
  await expect(
    channels.getByRole('region', {
      name: 'Review channel action',
      exact: true,
    }),
  ).toContainText('Confirm configure for p4_control');
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `channels-reviewed-${appearance}`);
    await accessibility(page, info, `channels-reviewed-${appearance}`);
  }
  await channels
    .getByRole('button', { name: 'Confirm channel action', exact: true })
    .click();
  await expect(
    channels.getByText('Channel action completed.', { exact: true }),
  ).toBeVisible();

  await page.goto('/app-v2/settings/plugins');
  const plugins = page.getByRole('region', {
    name: 'Plugin Center',
    exact: true,
  });
  await plugins
    .getByLabel('Search plugins', { exact: true })
    .fill('Synthetic settings');
  await plugins.getByRole('button', { name: 'Search', exact: true }).click();
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
    .getByRole('button', { name: 'Review configuration', exact: true })
    .click();
  await expect(
    plugins.getByRole('region', { name: 'Plugin change review', exact: true }),
  ).toBeVisible();
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `plugins-reviewed-${appearance}`);
    await accessibility(page, info, `plugins-reviewed-${appearance}`);
  }
  await plugins
    .getByRole('button', { name: 'Apply plugin change', exact: true })
    .click();
  await expect(
    plugins.getByText(
      'Plugin change completed. Refresh to view the saved state.',
      {
        exact: true,
      },
    ),
  ).toBeVisible();

  await page.goto('/app-v2/settings/skills');
  const skills = page.getByRole('region', { name: 'Skills', exact: true });
  await skills
    .getByLabel('Search skills', { exact: true })
    .fill('Synthetic browser skill');
  await skills.getByRole('button', { name: 'Search', exact: true }).click();
  const skill = skills.getByRole('listitem').filter({
    hasText: 'Synthetic browser skill',
  });
  await expect(skill).toContainText('Available');
  await skill
    .getByRole('button', { name: 'Make unavailable', exact: true })
    .click();
  await expect(
    skills.getByRole('heading', { name: 'Review skill change', exact: true }),
  ).toBeVisible();
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `skills-reviewed-${appearance}`);
    await accessibility(page, info, `skills-reviewed-${appearance}`);
  }
  await skills
    .getByRole('button', { name: 'Apply reviewed change', exact: true })
    .click();
  await expect(skill).toContainText('Unavailable');

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
    linkName: 'Wiki',
    path: '/app-v2/settings/wiki',
    headingName: 'Wiki vault',
  });
  const wiki = page.getByRole('region', { name: 'Wiki vault', exact: true });
  await expect(wiki.getByText(/Select an authorized vault/)).toBeVisible();
  await wiki
    .getByRole('button', { name: 'Choose authorized vault', exact: true })
    .click();
  await expect(wiki.getByText(/Authorized folder selected/)).toBeVisible();
  const enabled = wiki.getByRole('checkbox', { name: 'Enable wiki vault' });
  if (!(await enabled.isChecked())) await enabled.check();
  await wiki
    .getByRole('button', { name: 'Review configuration', exact: true })
    .click();
  const configuration = wiki.getByRole('region', {
    name: 'Reviewed wiki action',
    exact: true,
  });
  await expect(configuration).toContainText('Wiki will be enabled.');
  await configuration
    .getByRole('button', { name: 'Save wiki configuration', exact: true })
    .click();
  await expect(wiki.getByRole('status')).toContainText('Finished');
  const setup = await page.request.post('/__p4_fixture/wiki', { headers });
  expect(setup.ok()).toBe(true);
  const created = await setup.json();
  await wiki
    .getByRole('button', { name: 'Reload wiki status', exact: true })
    .click();
  await expect(wiki.getByText(created.title, { exact: true })).toBeVisible();
  await expect(wiki.getByText('edited', { exact: true })).toBeVisible();
  await wiki
    .getByRole('button', {
      name: `Review versions: ${created.title}`,
      exact: true,
    })
    .click();
  const review = wiki.getByRole('region', {
    name: 'Reviewed wiki action',
    exact: true,
  });
  await expect(
    review.getByLabel(`Database version: ${created.title}`),
  ).toContainText('saved database description');
  await expect(
    review.getByLabel(`Vault version: ${created.title}`),
  ).toContainText('externally edited vault description');
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `wiki-conflict-review-${appearance}`);
    await accessibility(page, info, `wiki-conflict-review-${appearance}`);
  }
  await review
    .getByRole('button', {
      name: 'Accept reviewed vault version',
      exact: true,
    })
    .click();
  await expect(wiki.getByRole('status')).toContainText('Finished: 1 completed');
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
  await upload
    .getByRole('button', { name: 'Review upload', exact: true })
    .click();
  const uploaded = page.waitForResponse(
    (response) =>
      response.url().endsWith('/api/v1/documents/uploads/commands') &&
      response.request().method() === 'POST',
  );
  await upload
    .getByRole('button', { name: 'Confirm upload', exact: true })
    .click();
  const uploadResponse = await uploaded;
  expect(uploadResponse.ok()).toBe(true);
  const { batch_id } = await uploadResponse.json();
  await page
    .getByRole('button', { name: `Review processing ${batch_id}`, exact: true })
    .click();
  const processing = page.getByRole('region', {
    name: 'Document processing review',
    exact: true,
  });
  await processing
    .getByRole('button', { name: 'Review processing policy', exact: true })
    .click();
  await expect(processing.getByText(/Chat provider: openai/)).toBeVisible();
  expect(
    await (
      await page.request.get('/__p4_fixture/document-processing', { headers })
    ).json(),
  ).toEqual({ embeddings: 0, source_embeddings: 0, chats: 0, starts: 0 });
  await openHomeThroughNavigation(page);
  await openDocumentsFromHome(page);
  await expect(
    processing.getByText(`Conversation: ${conversation_id}`, { exact: true }),
  ).toBeVisible();
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `document-processing-review-${appearance}`);
    await accessibility(page, info, `document-processing-review-${appearance}`);
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
    .getByRole('button', { name: 'Approve and start processing', exact: true })
    .click();
  const admissionResponse = await admitted;
  expect(admissionResponse.ok()).toBe(true);
  expect((await admissionResponse.json()).processing).toBe('admitted');
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
  await expect(
    page
      .getByRole('button', { name: `Inspect batch ${batch_id}`, exact: true })
      .locator('..')
      .getByText('Batch · completed', { exact: true }),
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
  await upload
    .getByRole('button', { name: 'Review upload', exact: true })
    .click();
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
    await screenshot(page, info, `document-upload-review-${appearance}`);
    await accessibility(page, info, `document-upload-review-${appearance}`);
  }
  const completed = page.waitForResponse(
    (response) =>
      response.url().endsWith('/api/v1/documents/uploads/commands') &&
      response.request().method() === 'POST',
  );
  await upload
    .getByRole('button', { name: 'Confirm upload', exact: true })
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
  await batch
    .getByRole('button', { name: 'Review pause', exact: true })
    .click();
  expect((await saved()).paused).toBe(false);
  await openHomeThroughNavigation(page);
  await openDocumentsFromHome(page);
  await expect(
    queue.getByText('Reviewed action: document.batch.pause', { exact: true }),
  ).toBeVisible();
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `document-queue-review-${appearance}`);
    await accessibility(page, info, `document-queue-review-${appearance}`);
  }
  await queue
    .getByRole('button', { name: 'Confirm queue action', exact: true })
    .click();
  await expect(queue.getByRole('status')).toContainText(
    'Saved queue outcome: paused',
  );
  expect((await saved()).paused).toBe(true);
  await queue
    .getByRole('button', { name: 'Refresh queue', exact: true })
    .click();
  await batch
    .getByRole('button', { name: 'Review resume', exact: true })
    .click();
  await queue
    .getByRole('button', { name: 'Confirm queue action', exact: true })
    .click();
  await expect(queue.getByRole('status')).toContainText(
    'Saved queue outcome: resumed',
  );
  expect((await saved()).paused).toBe(false);
  await queue
    .getByRole('button', { name: 'Refresh queue', exact: true })
    .click();
  await batch
    .getByRole('button', { name: 'Review cancel remaining', exact: true })
    .click();
  await queue
    .getByRole('button', { name: 'Confirm queue action', exact: true })
    .click();
  await expect(queue.getByRole('status')).toContainText(
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
    .getByRole('button', {
      name: 'Review clear selected finished',
      exact: true,
    })
    .click();
  await queue
    .getByRole('button', { name: 'Confirm queue action', exact: true })
    .click();
  await expect(queue.getByRole('status')).toContainText(
    'Saved queue outcome: cleared',
  );
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
  const runtime = page.getByRole('region', {
    name: 'node managed runtime installation',
    exact: true,
  });
  await runtime
    .getByRole('button', { name: 'Review metadata resolution', exact: true })
    .click();
  expect((await saved()).calls).toEqual([]);
  await runtime
    .getByRole('button', { name: 'Approve and resolve metadata', exact: true })
    .click();
  await expect(
    runtime.getByText(
      'Original operation: resolved. Worker cleanup: confirmed.',
      { exact: true },
    ),
  ).toBeVisible();
  await runtime
    .getByRole('button', { name: 'Review pinned installation', exact: true })
    .click();
  await expect(
    runtime.getByText('https://example.invalid/node.zip', { exact: true }),
  ).toBeVisible();
  expect((await saved()).calls).toEqual(['resolve']);
  await openHomeThroughNavigation(page);
  await openSettingsRouteFromHome(page, {
    linkName: 'MCP',
    path: '/app-v2/settings/mcp',
    headingName: 'MCP servers',
  });
  await expect(
    runtime.getByText('https://example.invalid/node.zip', { exact: true }),
  ).toBeVisible();
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `runtime-installation-review-${appearance}`);
    await accessibility(
      page,
      info,
      `runtime-installation-review-${appearance}`,
    );
  }
  await runtime
    .getByRole('button', {
      name: 'Approve and install pinned runtime',
      exact: true,
    })
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

test('Knowledge creates a reviewed entry retains its draft and applies archive restore through the real owner', async ({
  page,
}, info) => {
  const headers = {
    'X-Fixture-Token': process.env.ROW_BOT_BROWSER_CONTROL_TOKEN!,
    Origin: new URL(process.env.ROW_BOT_BROWSER_BASE_URL!).origin,
  };
  expect(
    (
      await page.request.post('/__p4_fixture/knowledge/empty', { headers })
    ).ok(),
  ).toBe(true);
  await page.goto('/app-v2/settings/knowledge');
  await page
    .getByRole('button', { name: 'Create knowledge', exact: true })
    .click();
  const editor = page.getByRole('region', {
    name: 'Knowledge editor',
    exact: true,
  });
  const subject = `Synthetic retained knowledge ${info.project.name}`;
  await expect(
    editor.getByRole('heading', { name: 'Create knowledge', exact: true }),
  ).toBeFocused();
  await editor.getByLabel('Subject', { exact: true }).fill(subject);
  await editor
    .getByLabel('Description', { exact: true })
    .fill('Synthetic retained description');
  await editor
    .getByRole('button', { name: 'Review save', exact: true })
    .click();
  await openHomeThroughNavigation(page);
  await openSettingsRouteFromHome(page, {
    linkName: 'Knowledge',
    path: '/app-v2/settings/knowledge',
    headingName: 'Knowledge',
  });
  await expect(editor.getByLabel('Subject', { exact: true })).toHaveValue(
    subject,
  );
  await editor
    .getByRole('button', { name: 'Confirm knowledge change', exact: true })
    .click();
  await expect(
    editor.getByText(
      'Knowledge saved. Search and wiki projections remain pending. Reload the saved entry to continue.',
      { exact: true },
    ),
  ).toBeVisible();
  await editor
    .getByRole('button', {
      name: 'Discard draft and reload saved entry',
      exact: true,
    })
    .click();
  for (const [action, status] of [
    ['Archive', 'archived'],
    ['Restore', 'active'],
  ] as const) {
    await editor.getByRole('button', { name: action, exact: true }).click();
    const saved = page.waitForResponse(
      (response) =>
        response.url().endsWith('/knowledge/entities/commands') &&
        response.request().method() === 'POST',
    );
    await editor
      .getByRole('button', { name: 'Confirm knowledge change', exact: true })
      .click();
    expect((await saved).ok()).toBe(true);
    await editor
      .getByRole('button', { name: 'Reload saved entry', exact: true })
      .click();
    await expect(
      editor.getByText(
        `Saved status: ${status}. Projection readiness: unknown.`,
        { exact: true },
      ),
    ).toBeVisible();
  }
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `knowledge-editor-${appearance}`);
    await accessibility(page, info, `knowledge-editor-${appearance}`);
  }
  await page
    .getByRole('button', { name: 'Reload knowledge', exact: true })
    .click();
  const entry = page.getByRole('listitem').filter({ hasText: subject });
  await entry.locator('summary').click();
  await entry
    .getByRole('button', { name: `Edit ${subject}`, exact: true })
    .click();
  await expect(
    page.getByRole('region', { name: 'Knowledge editor', exact: true }),
  ).toHaveCount(1);
  await page.reload();
  const reloaded = page.getByRole('listitem').filter({ hasText: subject });
  await reloaded.locator('summary').click();
  await reloaded
    .getByRole('button', { name: `Edit ${subject}`, exact: true })
    .click();
  await expect(
    editor.getByRole('heading', { name: 'Edit knowledge', exact: true }),
  ).toBeFocused();
  await expect(
    editor.getByRole('textbox', { name: 'Description', exact: true }),
  ).toHaveValue('Synthetic retained description');
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
  await entry
    .getByRole('button', { name: 'Edit Phase 4 knowledge 000', exact: true })
    .click();
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
  await relations
    .getByRole('button', { name: 'Review new relation', exact: true })
    .click();
  await openHomeThroughNavigation(page);
  await openSettingsRouteFromHome(page, {
    linkName: 'Knowledge',
    path: '/app-v2/settings/knowledge',
    headingName: 'Knowledge',
  });
  const confirm = async (outcome: string) => {
    const result = page.waitForResponse(
      (response) =>
        response.url().endsWith('/knowledge/relations/commands') &&
        response.request().method() === 'POST',
    );
    await relations
      .getByRole('button', { name: 'Confirm relation change', exact: true })
      .click();
    const response = await result;
    expect(response.ok()).toBe(true);
    expect((await response.json()).outcome).toBe(outcome);
    await relations
      .getByRole('button', { name: 'Reload relations', exact: true })
      .click();
  };
  await confirm('saved');
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
    .getByRole('button', { name: 'Review removal of knows', exact: true })
    .click();
  await confirm('removed');
  await expect(
    relations.getByText('0 saved relations. 0 shown on this page.', {
      exact: true,
    }),
  ).toBeVisible();
  await relations
    .getByRole('button', {
      name: 'Review Supersede with selected entry',
      exact: true,
    })
    .click();
  await confirm('superseded');
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
  await expectPopulatedHomeLibraries(page);
  await openSettingsRouteFromHome(page, {
    linkName: 'Buddy',
    path: '/app-v2/settings/buddy',
    headingName: 'Buddy',
  });
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

test('Subscription checks retain the original review and expose actual cancellation without replay', async ({
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
  await expectPopulatedHomeLibraries(page);
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

test('Default model selection uses the saved catalog and retains reviewed edits across navigation', async ({
  page,
}, info) => {
  await seed(page, 'populated');
  await page.goto('/app-v2/settings/providers');
  const editor = page.getByRole('region', {
    name: 'Default chat model',
    exact: true,
  });
  await activateRoute(
    page,
    editor.getByRole('button', {
      name: 'Browse saved models',
      exact: true,
    }),
    { path: '/app-v2/settings/models', headingName: 'Models' },
  );
  await page
    .getByRole('searchbox', { name: 'Search models' })
    .fill('phase4-104');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await activateRoute(
    page,
    page.getByRole('button', {
      name: 'Review Saved example 104 as default',
      exact: true,
    }),
    { path: '/app-v2/settings/providers', headingName: 'Providers' },
  );
  await expect(
    editor.getByRole('textbox', { name: 'Default provider ID' }),
  ).toHaveValue('openai');
  await expect(
    editor.getByRole('textbox', { name: 'Default exact model ID' }),
  ).toHaveValue('phase4-104');
  await editor
    .getByRole('button', { name: 'Review default model', exact: true })
    .click();
  await expect(
    editor.getByText(/Reviewed global default: openai/),
  ).toBeVisible();
  await activateRoute(
    page,
    editor.getByRole('button', {
      name: 'Browse saved models',
      exact: true,
    }),
    { path: '/app-v2/settings/models', headingName: 'Models' },
  );
  await openHomeThroughNavigation(page);
  await openSettingsRouteFromHome(page, {
    linkName: 'Providers',
    path: '/app-v2/settings/providers',
    headingName: 'Providers',
  });
  await expect(
    editor.getByRole('textbox', { name: 'Default exact model ID' }),
  ).toHaveValue('phase4-104');
  await editor
    .getByRole('button', { name: 'Confirm default model', exact: true })
    .click();
  await expect(
    editor.getByText(/Saved default: model:openai:phase4-104/),
  ).toBeVisible();
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await expect(page.locator('html')).toHaveAttribute(
      'data-theme',
      appearance,
    );
    await assertNoOverflow(page);
    await screenshot(page, info, `default-model-${appearance}`);
    await accessibility(page, info, `default-model-${appearance}`);
  }
});

test('Phase 4 saved providers lead to bounded searchable model details without a live probe', async ({
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

test('Phase 4 model pagination rejects changed snapshots and explicit reload recovers', async ({
  page,
}, info) => {
  await seed(page, 'populated');
  await page.goto('/app-v2/settings/models?provider=openai');
  await expect(page.getByText('106 matching models')).toBeVisible();
  await seed(page, 'changed');
  // The public protocol deliberately returns410 for this injected expired cursor.
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
  await page
    .getByRole('button', { name: 'Load more models', exact: true })
    .click();
  await expect(
    page.getByText('The catalog changed. Reload saved models to continue.'),
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Load more models', exact: true }),
  ).toBeDisabled();
  await expect(page.locator('.settings-results > li')).toHaveCount(50);
  await page
    .getByRole('button', { name: 'Reload saved models', exact: true })
    .click();
  await expect(page.getByRole('alert')).toHaveCount(0);
  await page
    .getByRole('button', { name: 'Load more models', exact: true })
    .click();
  await expect(page.locator('.settings-results > li')).toHaveCount(100);
  await seed(page, 'empty');
  await page
    .getByRole('button', { name: 'Reload saved models', exact: true })
    .click();
  await expect(page.getByText('No matching saved models')).toBeVisible();
  await assertNoOverflow(page);
  await screenshot(page, info, 'saved-models-empty');
  await accessibility(page, info, 'saved-models-empty');
});

test('Phase 4 credentials retain a private reviewed draft and save disconnect restore locally', async ({
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

test('Phase 4 endpoint configuration retains drafts and reviews create edit remove', async ({
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
  await connection
    .getByRole('button', { name: 'Review Test', exact: true })
    .click();
  await connection
    .getByRole('button', { name: 'Test now', exact: true })
    .click();
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
  await catalog
    .getByRole('button', { name: 'Review acceptance', exact: true })
    .click();
  await openHomeThroughNavigation(page);
  await openSettingsRouteFromHome(page, {
    linkName: 'MCP',
    path: '/app-v2/settings/mcp',
    headingName: 'MCP servers',
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
  await connection
    .getByRole('button', { name: 'Review Test', exact: true })
    .click();
  await openHomeThroughNavigation(page);
  await openSettingsRouteFromHome(page, {
    linkName: 'MCP',
    path: '/app-v2/settings/mcp',
    headingName: 'MCP servers',
  });
  await connection
    .getByRole('button', { name: 'Test now', exact: true })
    .click();
  await expect(
    connection.getByText(
      'Test completed and the temporary connection closed.',
      { exact: true },
    ),
  ).toBeVisible();
  await connection
    .getByRole('button', { name: 'Review Connect', exact: true })
    .click();
  await connection
    .getByRole('button', { name: 'Connect now', exact: true })
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
    .getByRole('button', { name: 'Review Disconnect', exact: true })
    .click();
  await connection
    .getByRole('button', { name: 'Disconnect now', exact: true })
    .click();
  await expect(
    connection.getByText('Connection cleanup completed.', { exact: true }),
  ).toBeVisible();
  await expect(
    connection.getByRole('button', { name: 'Review Connect', exact: true }),
  ).toBeEnabled();
});

test('Subscription accounts retain the reviewed sign-in and publish disconnect recover synthetic credentials', async ({
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

test('Subscription options retain exact reviews and save reference override reset through canonical owners', async ({
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
  await expectPopulatedHomeLibraries(page);
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
  const globals = page
    .getByRole('region', { name: 'Saved MCP permissions', exact: true })
    .first();
  await globals
    .getByRole('button', { name: 'Disable MCP access', exact: true })
    .click();
  await globals
    .getByRole('button', { name: 'Review permission', exact: true })
    .click();
  await globals
    .getByRole('button', { name: 'Save permission', exact: true })
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
    await permissions
      .getByRole('button', { name: 'Review permission', exact: true })
      .click();
    await permissions
      .getByRole('button', { name: 'Save permission', exact: true })
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
    .getByRole('button', { name: 'Review document removal', exact: true })
    .click();
  await openHomeThroughNavigation(page);
  await expectPopulatedHomeLibraries(page);
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
    .getByRole('button', { name: 'Review remaining cleanup', exact: true })
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
  await editor
    .getByRole('button', { name: 'Review settings', exact: true })
    .click();
  await expect(editor.getByText(/Review complete/)).toBeVisible();
  await openHomeThroughNavigation(page);
  await openSettingsRouteFromHome(page, {
    linkName: 'MCP',
    path: '/app-v2/settings/mcp',
    headingName: 'MCP servers',
  });
  await expect(editor.getByLabel('New command', { exact: true })).toHaveValue(
    'synthetic-unused-command',
  );
  const save = async (review = true) => {
    if (review)
      await editor
        .getByRole('button', { name: 'Review settings', exact: true })
        .click();
    await editor
      .getByRole('button', { name: 'Save Disabled', exact: true })
      .click();
    await expect(editor.getByText(/Saved disabled\. Refresh/)).toBeVisible();
    await editor.getByRole('button', { name: 'Refresh', exact: true }).click();
    await expect(
      editor.getByRole('button', { name: 'Review settings', exact: true }),
    ).toBeEnabled();
  };
  await save(false);
  await editor
    .getByRole('button', { name: `Edit ${name}`, exact: true })
    .click();
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
  await editor
    .getByRole('button', { name: `Rename ${name}`, exact: true })
    .click();
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
