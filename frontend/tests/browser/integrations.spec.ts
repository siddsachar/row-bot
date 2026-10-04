import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';
import {
  blockFixtureServiceWorkers,
  clickNewChat,
  composer,
  fixtureState,
} from './unified-helpers';
import type { Page } from '@playwright/test';
import type { IntegrationItem } from '../../src/api/types';

function headers() {
  return {
    'X-Fixture-Token': process.env.ROW_BOT_BROWSER_CONTROL_TOKEN!,
    Origin: new URL(process.env.ROW_BOT_BROWSER_BASE_URL!).origin,
  };
}
async function confirm(page: Page) {
  await page
    .getByRole('dialog')
    .getByRole('button', { name: 'Confirm', exact: true })
    .click();
}

async function openIntegrationsFromChat(page: Page) {
  if ((page.viewportSize()?.width ?? 0) < 1024)
    await page
      .getByRole('button', { name: 'Toggle navigation', exact: true })
      .click();
  await page
    .getByRole('navigation', { name: 'Workspace navigation', exact: true })
    .getByRole('link', { name: 'Settings', exact: true })
    .click();
  if ((page.viewportSize()?.width ?? 0) < 900)
    await page
      .getByRole('combobox', { name: 'Settings section' })
      .selectOption('integrations');
  else
    await page
      .getByRole('navigation', { name: 'Settings sections' })
      .getByRole('link', { name: 'Integrations', exact: true })
      .click();
  await page.getByRole('button', { name: 'Installed', exact: true }).click();
  await page
    .getByRole('button', { name: 'Browser Writing', exact: true })
    .click();
}

test('portable package installs off, manages its children and removes only owned data', async ({
  page,
  context,
}, info) => {
  await blockFixtureServiceWorkers(context);
  await page.goto('/app-v2/settings/integrations?tab=discover&type=plugin');
  await page
    .getByRole('button', { name: 'Add from link or file', exact: true })
    .click();
  await page
    .getByRole('textbox', { name: 'Source link' })
    .fill('bundled:local-text-tools');
  await page
    .getByRole('button', { name: 'Inspect source', exact: true })
    .click();
  await expect(
    page.getByText('Skill: text-review', { exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Add package', exact: true }).click();
  await confirm(page);
  await expect(page).toHaveURL(/selected=plugin%3A/);
  await expect(
    page.getByRole('heading', { name: 'Included capabilities' }),
  ).toBeVisible();
  await page
    .getByRole('button', { name: 'Run local package checks', exact: true })
    .click();
  await confirm(page);
  await page
    .getByRole('button', { name: 'Turn on package', exact: true })
    .click();
  await confirm(page);
  await expect(
    page.getByText('Setup needed · Row-Bot', { exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: /Continue setup:/ }).click();
  await expect(page.getByRole('region', { name: /Set up/ })).toBeVisible();
  await expect(
    page.getByRole('search', { name: 'Search plugins' }),
  ).toHaveCount(0);
  await page.reload();
  await expect(
    page.getByRole('heading', { name: 'Included capabilities' }),
  ).toBeVisible();
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `integrations-package-${appearance}`);
    await accessibility(page, info, `integrations-package-${appearance}`);
  }

  const setupViewport = page.viewportSize()!;
  await page.setViewportSize({ width: 320, height: 800 });
  await assertNoOverflow(page);
  await screenshot(page, info, 'integrations-package-setup-320');
  await accessibility(page, info, 'integrations-package-setup-320');
  await page.setViewportSize(setupViewport);

  await page
    .getByRole('button', { name: 'Turn off package', exact: true })
    .click();
  await confirm(page);
  await expect(
    page.getByRole('button', { name: 'Turn on package', exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole('button', { name: 'Turn on package', exact: true }),
  ).toBeVisible();
  await page
    .getByRole('button', { name: 'Remove package', exact: true })
    .click();
  await confirm(page);
  await expect(
    page.getByRole('button', { name: 'Delete saved data', exact: true }),
  ).toBeEnabled();
  await page.reload();
  await expect(
    page.getByText('Saved data retained · portable', { exact: true }),
  ).toBeVisible();
  await page
    .getByRole('button', { name: 'Delete saved data', exact: true })
    .click();
  await confirm(page);
  await expect(page).not.toHaveURL(/selected=/);
  await expect(
    page.getByRole('button', { name: 'local-text-tools', exact: true }),
  ).toHaveCount(0);
});

test('MCP setup tests once, accepts its tools and connects through existing authority', async ({
  page,
  context,
}, info) => {
  await blockFixtureServiceWorkers(context);
  expect(
    (
      await page.request.post('/__p4_fixture/mcp-catalog', {
        headers: headers(),
      })
    ).ok(),
  ).toBe(true);
  await page.goto('/app-v2/settings/integrations?type=mcp');
  await page
    .getByRole('button', { name: 'Synthetic lifecycle', exact: true })
    .click();
  await page
    .getByRole('button', { name: 'Test connection', exact: true })
    .click();
  await confirm(page);
  const catalog = page.getByRole('region', {
    name: 'Accept tested MCP tools',
    exact: true,
  });
  await expect(
    catalog.getByText('3 tools found.', { exact: true }),
  ).toBeVisible();
  await page.reload();
  await catalog
    .getByRole('button', { name: 'Accept tools', exact: true })
    .click();
  await expect(
    page.getByText('Tools accepted. Choose access before connecting.', {
      exact: true,
    }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole('region', { name: 'Accept tested MCP tools', exact: true }),
  ).toHaveCount(0);
  expect(
    (
      await (
        await page.request.get('/__p4_fixture/mcp-catalog', {
          headers: headers(),
        })
      ).json()
    ).calls,
  ).toEqual(['connect', 'list_tools']);
  // The fixture can start with access on. Establish an explicit enable step.
  const serverAccess = page.getByRole('switch', {
    name: 'Server access',
    exact: true,
  });
  await expect(serverAccess).toBeEnabled();
  if (await serverAccess.isChecked()) await serverAccess.uncheck();
  await expect(serverAccess).not.toBeChecked();
  await expect(serverAccess).toBeEnabled();
  // Publish one enable through the real local owner, then make only its client
  // response ambiguous. Recovery must read that original receipt, not replay it.
  let originalCommand = '';
  const published: string[] = [];
  await page.route('**/api/v1/settings/mcp/commands', async (route) => {
    const request = route.request();
    if (request.method() !== 'POST') return route.continue();
    const command = request.postDataJSON();
    if (command.type !== 'mcp.configuration.control') return route.continue();
    published.push(command.command_id);
    if (originalCommand) return route.continue();
    originalCommand = command.command_id;
    const response = await route.fetch();
    const receipt = await response.json();
    expect(receipt.status).toBe('completed');
    await route.fulfill({ response, json: { ...receipt, status: 'partial' } });
  });
  await page
    .getByRole('button', { name: 'Turn on and connect', exact: true })
    .click();
  await confirm(page);
  await expect(
    page.getByRole('button', { name: 'Check original setup operation' }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole('switch', { name: 'Server access', exact: true }),
  ).toBeDisabled();
  await page.getByText('Advanced connection actions', { exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Remove connection', exact: true }),
  ).toBeDisabled();
  await page
    .getByRole('button', { name: 'Check original setup operation' })
    .click();
  await expect(
    page.getByRole('button', { name: 'Check original setup operation' }),
  ).toHaveCount(0);
  expect(published).toEqual([originalCommand]);
  await expect(
    page.getByRole('switch', { name: 'Server access', exact: true }),
  ).toBeEnabled();
  await page.getByText('Advanced connection actions', { exact: true }).click();
  await page
    .getByRole('button', { name: 'Turn on and connect', exact: true })
    .click();
  await confirm(page);
  await expect(page.getByText('Connected.', { exact: true })).toBeVisible();
  // Runtime control may return accepted before its durable completion is read.
  const originalRuntime = page.getByRole('button', {
    name: 'Check original setup operation',
  });
  if (await originalRuntime.count()) {
    await originalRuntime.click();
    await expect(originalRuntime).toHaveCount(0);
  }
  await expect(page.getByText('Ready · custom', { exact: true })).toBeVisible();
  await assertNoOverflow(page);
  await screenshot(page, info, 'integrations-mcp-connected');

  const setupViewport = page.viewportSize()!;
  await page.setViewportSize({ width: 320, height: 800 });
  await assertNoOverflow(page);
  await screenshot(page, info, 'integrations-mcp-setup-320');
  await accessibility(page, info, 'integrations-mcp-setup-320');
  await page.setViewportSize(setupViewport);

  await page.getByText('Advanced connection actions', { exact: true }).click();
  await page
    .getByRole('button', { name: 'Remove connection', exact: true })
    .click();
  await confirm(page);
  await expect(
    page.getByRole('button', { name: 'Synthetic lifecycle', exact: true }),
  ).toHaveCount(0);
});

test('public skill imports its resources then remains manageable after reload', async ({
  page,
  context,
}, info) => {
  await blockFixtureServiceWorkers(context);
  expect(
    (
      await page.request.post('/__p4_fixture/integration-skills', {
        headers: headers(),
      })
    ).ok(),
  ).toBe(true);
  await page.goto(
    '/app-v2/settings/integrations?tab=discover&type=skill&source=clawhub',
  );
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await page
    .getByRole('button', { name: 'Browser writing', exact: true })
    .click();
  await page
    .getByRole('button', { name: 'Inspect integration', exact: true })
    .click();
  await page.getByText('references/checklist.txt', { exact: true }).click();
  await expect(
    page.getByText('references/checklist.txt', { exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Add skill', exact: true }).click();
  await confirm(page);
  await expect(page).toHaveURL(/tab=my.*selected=skill%3Abrowser_writing/);
  await page.reload();
  await expect(
    page.getByRole('button', { name: 'Make available in chats', exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Pin for new work', exact: true }),
  ).toBeVisible();
  await page
    .getByRole('button', { name: 'Make available in chats', exact: true })
    .click();
  await expect(
    page.getByText('Ready · clawhub', { exact: true }),
  ).toBeVisible();
  await clickNewChat(page);
  await expect(page).toHaveURL(/\/conversations\//);
  const chatUrl = page.url();
  const chatId = new URL(chatUrl).pathname.split('/').at(-1)!;
  await composer(page).fill('Existing local draft');
  const before = await (
    await page.request.get(`/__p3_fixture/conversation/${chatId}`, {
      headers: headers(),
    })
  ).json();
  const callsBefore = await fixtureState(page);
  await openIntegrationsFromChat(page);
  await expect(
    page.getByRole('button', { name: 'Try in chat', exact: true }),
  ).toBeEnabled();
  await page.getByRole('button', { name: 'Try in chat', exact: true }).click();
  await expect(page).toHaveURL(chatUrl);
  await expect(composer(page)).toHaveValue(
    /Existing local draft\n\nHelp me use Browser Writing/,
  );
  const after = await (
    await page.request.get(`/__p3_fixture/conversation/${chatId}`, {
      headers: headers(),
    })
  ).json();
  expect(after.workspace.controls).toEqual(before.workspace.controls);
  expect(await fixtureState(page)).toEqual(callsBefore);
  await screenshot(page, info, 'integrations-skill-draft-only');
  await openIntegrationsFromChat(page);
  await assertNoOverflow(page);
  await screenshot(page, info, 'integrations-skill-installed');

  const setupViewport = page.viewportSize()!;
  await page.setViewportSize({ width: 320, height: 800 });
  await assertNoOverflow(page);
  await screenshot(page, info, 'integrations-skill-setup-320');
  await accessibility(page, info, 'integrations-skill-setup-320');
  await page.setViewportSize(setupViewport);
  await page
    .getByRole('button', { name: 'Check for skill updates', exact: true })
    .click();
  await expect(
    page.getByRole('dialog', { name: 'Review skill update' }),
  ).toBeVisible();
  await page
    .getByRole('button', { name: 'Keep current version', exact: true })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(
    page.getByText('Ready · clawhub', { exact: true }),
  ).toBeVisible();
  await page
    .getByRole('button', { name: 'Turn off skill', exact: true })
    .click();
  await expect(
    page.getByRole('button', { name: 'Make available in chats', exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole('button', { name: 'Make available in chats', exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Remove skill', exact: true }).click();
  await expect(
    page.getByRole('dialog', { name: 'Remove skill' }),
  ).toBeVisible();
  await screenshot(page, info, 'integrations-skill-remove-review');
  await page
    .getByRole('button', { name: 'Confirm skill change', exact: true })
    .click();
  await expect(page).not.toHaveURL(/selected=/);
});

test('discovery applies the draft to one combined search and keeps navigation passive', async ({
  page,
  context,
}, info) => {
  await blockFixtureServiceWorkers(context);
  expect(
    (
      await page.request.post('/__p4_fixture/integration-skills', {
        headers: headers(),
      })
    ).ok(),
  ).toBe(true);
  await page.goto(
    '/app-v2/settings/integrations?tab=discover&type=skill&source=clawhub',
  );
  const search = page.getByRole('textbox', {
    name: 'Search integrations',
    exact: true,
  });
  await expect(
    page.getByText('Include unsupported entries', { exact: true }),
  ).toBeVisible();
  await search.fill('Browser writing');
  const publicRequest = page.waitForRequest(
    (request) =>
      request.url().endsWith('/settings/integrations/search') &&
      request.postDataJSON().refresh === true,
  );
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  expect((await publicRequest).postDataJSON()).toMatchObject({
    query: 'Browser writing',
    kind: 'skill',
    refresh: true,
  });
  await expect(page).toHaveURL(/q=Browser\+writing/);
  await expect(
    page.getByRole('button', { name: 'Browser writing', exact: true }),
  ).toBeVisible();
  const requests: unknown[] = [];
  page.on('request', (request) => {
    if (
      request.url().endsWith('/settings/integrations/search') &&
      request.postDataJSON().refresh
    )
      requests.push(request.postDataJSON());
  });
  await page
    .getByRole('button', { name: 'Browser writing', exact: true })
    .click();
  await expect(
    page.getByRole('heading', { name: 'Browser writing', exact: true }),
  ).toBeFocused();
  await page.goBack();
  await expect(
    page.getByRole('button', { name: 'Browser writing', exact: true }),
  ).toBeFocused();
  await page.goForward();
  await expect(
    page.getByRole('heading', { name: 'Browser writing', exact: true }),
  ).toBeFocused();
  await page.getByRole('button', { name: 'Back to integrations' }).click();
  await expect(search).toHaveValue('Browser writing');
  expect(requests).toEqual([]);
  await assertNoOverflow(page);
  await screenshot(page, info, 'integrations-discovery-search');
});

test('type-first entry, catalog choices and legacy routes work at 320px with keyboard and long content', async ({
  page,
  context,
}, info) => {
  await blockFixtureServiceWorkers(context);
  await page.setViewportSize({ width: 320, height: 720 });
  await page.goto('/app-v2/settings/integrations');
  await expect(
    page.getByText('Connect services and give Row-Bot tools.', { exact: true }),
  ).toBeVisible();
  await assertNoOverflow(page);
  await screenshot(page, info, 'integrations-320-chooser');
  await page
    .getByRole('button', { name: 'Apps & tools · MCP', exact: true })
    .focus();
  await page.keyboard.press('Enter');
  await expect(
    page.getByRole('button', { name: 'Search', exact: true }),
  ).toBeVisible();
  await page
    .getByText('Advanced configuration and existing editors', { exact: true })
    .click();
  await page.getByRole('button', { name: 'Catalogs', exact: true }).click();
  const registry = page.getByRole('switch', {
    name: 'Official MCP Registry',
    exact: true,
  });
  await expect(registry).toBeChecked();
  await registry.click();
  await expect(registry).not.toBeChecked();
  await expect(
    page.getByRole('switch', { name: 'Glama', exact: true }),
  ).toBeDisabled();
  await page.getByText('Snapshot provenance', { exact: true }).click();
  await assertNoOverflow(page);
  await screenshot(page, info, 'integrations-320-catalogs');
  await accessibility(page, info, 'integrations-320-catalogs');
  await page.reload();
  await expect(
    page.getByRole('switch', { name: 'Official MCP Registry', exact: true }),
  ).not.toBeChecked();
  await page.getByRole('button', { name: 'Back to integrations' }).click();
  await page.goto('/app-v2/settings/skills');
  await expect(page).toHaveURL(/settings\/integrations.*type=skill/);
  await expect(
    page.getByRole('button', { name: 'Skills', exact: true }),
  ).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('button', { name: 'Add from link or file' }).click();
  await expect(page.getByRole('combobox', { name: 'Import type' })).toHaveValue(
    'skill',
  );
  await expect(
    page.getByRole('button', { name: 'Import a skill file or create a skill' }),
  ).toBeVisible();
  await page.keyboard.press('Escape');
  await page.goto('/app-v2/settings/mcp');
  await expect(page).toHaveURL(/settings\/integrations.*type=mcp/);
  await page.goto('/app-v2/settings/plugins');
  await expect(page).toHaveURL(/settings\/integrations.*type=plugin/);
  await assertNoOverflow(page);
});

test('partial catalog and offline cached results remain inspectable with long content', async ({
  page,
  context,
}, info) => {
  await blockFixtureServiceWorkers(context);
  const item = {
    id: 'mcp:long',
    kind: 'mcp',
    owner_ref: 'long',
    name: 'Long catalog example',
    description: 'A long description '.repeat(40),
    parent_id: null,
    source: 'official',
    publisher: 'Example publisher',
    source_url: 'https://example.test',
    version: '1',
    pin: '',
    license: '',
    compatibility: 'not_inspected',
    reasons: [],
    platforms: [],
    evidence: 'Metadata only',
    installed: false,
    enabled: false,
    status: 'discover',
    revision: 'a'.repeat(64),
    actions: ['preview'],
    auth_status: 'none',
    account_label: '',
    children: [],
    target: null,
    attributions: [],
    evidence_stage: 'listed',
    auth_requirement: 'unknown',
    canonical_identity: '',
  } satisfies IntegrationItem;
  await page.route('**/api/v1/settings/integrations/search', async (route) => {
    const response = await route.fetch();
    await route.fulfill({
      response,
      json: {
        schema_version: 1,
        revision: 'a'.repeat(64),
        items: [
          ...Array.from({ length: 15 }, (_, i) => ({
            ...item,
            id: `mcp:filler-${i}`,
            name: `Example ${i}`,
          })),
          item,
        ],
        total: 16,
        next_cursor: null,
        sources: [
          {
            source: 'official',
            status: 'stale',
            message: 'Saved snapshot while offline',
            fetched_at: 1,
            eligibility: 'eligible',
            access: 'snapshot',
          },
          {
            source: 'hermes_mcp',
            status: 'timeout',
            message: 'Synthetic catalog unavailable',
            fetched_at: null,
            eligibility: 'eligible',
            access: 'public',
          },
        ],
      },
    });
  });
  await page.goto('/app-v2/settings/integrations?type=mcp&tab=discover');
  await expect(
    page.getByText('Some catalogs unavailable. Available results are shown.', {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByText(/Showing local snapshots or saved catalog results/),
  ).toBeVisible();
  await expect(page.getByText(/By Example publisher/).first()).toBeVisible();
  const row = page.getByRole('button', {
    name: 'Long catalog example',
    exact: true,
  });
  await row.scrollIntoViewIfNeeded();
  const originalViewport = page.viewportSize()!;
  const scrollBefore = await page
    .locator('.settings-page-content')
    .evaluate((element) => element.scrollTop);
  await row.click();
  await expect(
    page.getByRole('region', { name: 'Integration details', exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole('textbox', { name: 'Search integrations' }),
  ).toHaveCount(0);
  for (const width of [1280, 320]) {
    await page.setViewportSize({ width, height: 800 });
    await assertNoOverflow(page);
    await screenshot(page, info, `integrations-long-detail-${width}`);
    await accessibility(page, info, `integrations-long-detail-${width}`);
  }
  await page.setViewportSize(originalViewport);
  await page.getByRole('button', { name: 'Back to integrations' }).click();
  await expect(row).toBeFocused();
  await expect
    .poll(() =>
      page
        .locator('.settings-page-content')
        .evaluate((element) => element.scrollTop),
    )
    .toBe(scrollBefore);
});

test('Back cancels a pending inspection and a later inspection can succeed', async ({
  page,
  context,
}, info) => {
  await blockFixtureServiceWorkers(context);
  expect(
    (
      await page.request.post('/__p4_fixture/integration-skills', {
        headers: headers(),
      })
    ).ok(),
  ).toBe(true);
  await page.goto('/app-v2/settings/integrations?type=skill&tab=discover');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  const row = page.getByRole('button', {
    name: 'Browser writing',
    exact: true,
  });
  await row.click();
  let responseReady!: () => void;
  const ready = new Promise<void>((resolve) => {
    responseReady = resolve;
  });
  let releaseResponse!: () => void;
  const release = new Promise<void>((resolve) => {
    releaseResponse = resolve;
  });
  let responseFinished!: () => void;
  const finished = new Promise<void>((resolve) => {
    responseFinished = resolve;
  });
  let first = true;
  await page.route('**/api/v1/settings/integrations/preview', async (route) => {
    if (!first) {
      await route.continue();
      return;
    }
    first = false;
    const response = await route.fetch();
    responseReady();
    await release;
    // The browser may already have cancelled the request before its delayed reply.
    try {
      await route.fulfill({ response });
    } catch {
      /* Aborted request. */
    }
    responseFinished();
  });
  await page
    .getByRole('button', { name: 'Inspect integration', exact: true })
    .click();
  await ready;
  await page
    .getByRole('button', { name: 'Back to integrations', exact: true })
    .click();
  releaseResponse();
  await finished;
  await expect(row).toBeFocused();
  await expect(
    page.getByRole('region', { name: 'Integration details', exact: true }),
  ).toHaveCount(0);
  await expect(page.locator('.integrations-page [role="status"]')).toHaveCount(
    0,
  );
  await row.click();
  await page
    .getByRole('button', { name: 'Inspect integration', exact: true })
    .click();
  await expect(
    page.getByRole('button', { name: 'Add skill', exact: true }),
  ).toBeVisible();
  await assertNoOverflow(page);
  await screenshot(page, info, 'integrations-inspection-after-cancel');
});
