import type { Page, Route, TestInfo } from '@playwright/test';
import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';
import { clickNewChat, newConversation } from './unified-helpers';

// Settings › Company (Row-Bot for Teams, C1b): Join, the browser sign-in,
// joined, Leave, and the disconnected and unreachable states. The company
// routes are answered here, so no company server and no browser sign-in are
// needed; everything else is the fixture backend.

const COMPANY = 'rowbot.example.test';

type View = Record<string, unknown>;

const base: View = {
  schema_version: 1,
  state: 'not_joined',
  company: null,
  origin: null,
  name: null,
  email: null,
  joined_at: null,
  computer_name: 'Fixture laptop',
  rejoining: false,
  failure: null,
  notice: null,
  on_this_computer: true,
};

const joined: View = {
  ...base,
  state: 'joined',
  company: COMPANY,
  origin: `https://${COMPANY}`,
  name: 'Ada Lovelace',
  email: 'ada@example.test',
  joined_at: '2026-11-23T09:00:00+00:00',
};

/** The company routes, answered from `state.view`; Join and Leave move it on. */
async function companyRoutes(
  page: Page,
  state: { view: View; asked: string[] },
) {
  await page.route('**/api/v1/company**', async (route: Route) => {
    const path = new URL(route.request().url()).pathname.replace(
      '/api/v1/company',
      '',
    );
    state.asked.push(path || '/');
    if (path === '/join')
      state.view = { ...base, state: 'waiting', company: COMPANY };
    if (path === '/cancel') state.view = { ...base, failure: 'cancelled' };
    if (path === '/leave')
      state.view = { ...base, notice: 'left', company: COMPANY };
    await route.fulfill({ status: 200, json: state.view });
  });
}

const phone = (info: TestInfo) =>
  (info.project.use.viewport?.width ?? 1440) < 700;

/** Light and dark, then an accessibility scan of what shows. */
async function shoot(page: Page, info: TestInfo, name: string) {
  const suffix = phone(info) ? '-phone' : '';
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    await assertNoOverflow(page);
    await screenshot(page, info, `${name}${suffix}-${appearance}`);
  }
  await accessibility(page, info, `${name}${suffix}`);
  await page.emulateMedia({ colorScheme: 'light' });
}

test('Settings › Company: join through the browser, joined, and leave', async ({
  page,
}, info) => {
  const state = { view: base, asked: [] as string[] };
  await companyRoutes(page, state);
  await page.goto('/app-v2/settings/company');

  const field = page.getByRole('textbox', {
    name: 'Your company’s Row-Bot server',
  });
  await expect(field).toBeVisible({ timeout: 15_000 });
  await expect(
    page.getByText('Joining lets Row-Bot use your company’s models and rules.'),
  ).toBeVisible();
  // Opening the page only reads the state: nothing is checked while not joined.
  expect(state.asked).toEqual(['/']);
  await shoot(page, info, 'company-not-joined');

  await field.fill(COMPANY);
  await page.getByRole('button', { name: 'Join', exact: true }).click();
  await expect(
    page.getByText('Finish signing in in your browser'),
  ).toBeVisible();
  await shoot(page, info, 'company-waiting');

  // The person pressed Connect in the browser; the page reads the state again.
  state.view = joined;
  await expect(page.getByText(COMPANY, { exact: true })).toBeVisible();
  await expect(page.getByText('Ada Lovelace · ada@example.test')).toBeVisible();
  await shoot(page, info, 'company-joined');

  await page.getByRole('button', { name: `Leave ${COMPANY}` }).click();
  const confirm = page.getByRole('alertdialog', { name: `Leave ${COMPANY}?` });
  await expect(confirm).toContainText(
    'Your own chats and memory stay on this computer.',
  );
  await shoot(page, info, 'company-leave-confirm');
  await confirm.getByRole('button', { name: 'Leave', exact: true }).click();
  await expect(page.getByText(`This Row-Bot left ${COMPANY}.`)).toBeVisible();
  expect(state.asked).toContain('/leave');
  await shoot(page, info, 'company-left');
});

test('Settings › Company: disconnected, unreachable and a refused address', async ({
  page,
}, info) => {
  const state = {
    view: { ...joined, state: 'disconnected' } as View,
    asked: [] as string[],
  };
  await companyRoutes(page, state);
  await page.goto('/app-v2/settings/company');
  await expect(
    page.getByText('Your company disconnected this Row-Bot'),
  ).toBeVisible({ timeout: 15_000 });
  await expect(
    page.getByRole('button', { name: 'Sign in again' }),
  ).toBeEnabled();
  await shoot(page, info, 'company-disconnected');

  state.view = { ...joined, state: 'unreachable' };
  await page.reload();
  await expect(
    page.getByText(`Can’t reach ${COMPANY} right now. Row-Bot stays joined.`),
  ).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
  await shoot(page, info, 'company-unreachable');

  state.view = { ...base, failure: 'not_company_server' };
  await page.reload();
  await expect(
    page.getByText(
      'That doesn’t look like a Row-Bot company server. Check the address with your IT team.',
    ),
  ).toBeVisible({ timeout: 15_000 });
  await shoot(page, info, 'company-error');
});

// Company models (C2): the joined company's models in the composer's picker
// as their own group, Settings › Company's line, and their group in
// Settings › Models. The model list and the company's catalog page are
// answered here; nothing reaches a company server.

const companyModels = [
  ['qwen3-32b', 'Qwen3 32B'],
  ['claude-team', 'Claude for the team'],
].map(([id, name]) => ({
  provider_id: 'company',
  model_ref: `model:company:${id}`,
  label: `${name} - ${COMPANY}`,
  available: true,
  unavailable_reason: null,
  billing: null,
  personal: false,
  chat_only: false,
}));

const catalogRow = {
  provider_id: 'company',
  model_id: 'qwen3-32b',
  selection_ref: 'model:company:qwen3-32b',
  display_name: 'Qwen3 32B',
  provider_display_name: COMPANY,
  categories: ['chat'],
  input_modalities: ['text'],
  output_modalities: ['text'],
  tool_calling: true,
  reasoning: null,
  context_window: 131072,
  installed: true,
  configured: true,
  runtime_ready: true,
  status_reason: '',
  runtime_mode: 'agent',
  source: 'company_catalog',
  runtime_state: 'ready',
  offered: false,
  favourite_rank: null,
};

async function companyModelRoutes(page: Page) {
  await page.route('**/api/v1/handshake', async (route: Route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.models = [...(body.models ?? []), ...companyModels];
    await route.fulfill({ response, json: body });
  });
  await page.route('**/api/v1/settings/models/catalog?*', async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get('provider_id') !== 'company')
      return route.fallback();
    await route.fulfill({
      status: 200,
      json: {
        schema_version: 1,
        revision: 'company',
        generated_at: null,
        freshness: 'unavailable',
        items: [catalogRow],
        total: 1,
        next_cursor: null,
      },
    });
  });
}

test('company models: their own group in the picker, the Company line and Settings › Models', async ({
  page,
}, info) => {
  const state = {
    view: {
      ...joined,
      model_count: 2,
      suggested_model_ref: 'model:company:qwen3-32b',
      suggested_model_name: 'Qwen3 32B',
    } as View,
    asked: [] as string[],
  };
  await companyRoutes(page, state);
  await companyModelRoutes(page);

  await page.goto('/app-v2/');
  await clickNewChat(page);
  const pill = page.getByRole('button', { name: 'Model', exact: true });
  const plus = page.getByRole('button', { name: 'Add files and more' });
  await expect(pill.or(plus).first()).toBeVisible({ timeout: 15_000 });
  // A phone opens it from the model under the chat's title.
  if (await pill.isVisible()) await pill.click();
  else
    await page
      .locator('header.conversation-heading')
      .getByRole('button', { name: /^(Model: |Choose a model$)/ })
      .click();
  const list = page.getByRole('listbox', { name: 'Models' });
  await expect(list).toBeVisible();
  const group = list.getByRole('group', { name: new RegExp(COMPANY) });
  await expect(group.getByRole('option', { name: /^Qwen3 32B/ })).toBeVisible();
  await expect(
    group.getByRole('option', { name: /^Claude for the team/ }),
  ).toBeVisible();
  await shoot(page, info, 'company-models-picker');
  await page.keyboard.press('Escape');

  await page.goto('/app-v2/settings/company');
  await expect(page.getByText(`2 models from ${COMPANY}`)).toBeVisible({
    timeout: 15_000,
  });
  await shoot(page, info, 'company-models-line');

  await page
    .getByRole('link', { name: `See ${COMPANY}’s models in Models` })
    .click();
  await expect(page.getByRole('heading', { name: COMPANY })).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByText('Qwen3 32B').first()).toBeVisible();
  await shoot(page, info, 'company-models-settings');
});

// Company policy (C3a): locked down after the offline lease ran out. The
// calm line and Retry in Settings › Company and the model picker, the rules
// behind their disclosure, and Settings › Models' quiet "Set by your
// company" lines. Every company route is answered here.

const lockedDown: View = {
  ...joined,
  state: 'unreachable',
  model_count: 2,
  policy_state: 'locked_down',
  policy_reason: 'lease_expired',
  policy_since: '2026-11-30T09:00:00+00:00',
  locked: [
    {
      setting: 'models.personal_api_keys',
      title: 'Allow personal API keys',
      value: 'Off',
      words: 'Your company turned off personal API keys',
    },
    {
      setting: 'models.consumer_sign_ins',
      title: 'Allow consumer sign-ins',
      value: 'Off',
      words: 'Your company turned off personal subscriptions',
    },
  ],
  rules: [
    {
      setting: 'models.local_models',
      title: 'Allow local models',
      help: 'Whether people may run models on their own computer (Ollama, llama.cpp).',
      value: 'On',
    },
    {
      setting: 'offline.max_days',
      title: 'Offline days',
      help: 'Days a Row-Bot may run on its cached policy.',
      value: '0 days',
    },
  ],
};

const LOCKDOWN_LINE = `Row-Bot can’t reach ${COMPANY}, so it’s using only what’s safe on this computer until it can.`;

test('company policy: locked down, with Retry, the rules and Settings › Models’ quiet lines', async ({
  page,
}, info) => {
  const state = { view: lockedDown, asked: [] as string[] };
  await companyRoutes(page, state);
  await page.route('**/api/v1/handshake', async (route: Route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.models = [
      ...(body.models ?? []),
      ...companyModels.map((model) => ({
        ...model,
        available: false,
        unavailable_reason: 'unavailable',
      })),
    ];
    await route.fulfill({ response, json: body });
  });

  await page.goto('/app-v2/settings/company');
  await expect(page.getByText(LOCKDOWN_LINE)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole('alertdialog')).toHaveCount(0);
  await page.getByText('Your company’s rules').click();
  await expect(page.getByText('Offline days')).toBeVisible();
  await shoot(page, info, 'company-locked-down');
  const asked = state.asked.length;
  await page.getByRole('button', { name: 'Retry' }).click();
  await expect.poll(() => state.asked.slice(asked)).toContain('/check');

  await page.goto('/app-v2/settings/models');
  await expect(
    page.getByText('Your company turned off personal API keys'),
  ).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText('Set by your company').first()).toBeVisible();
  await shoot(page, info, 'company-models-set-by-company');

  await page.goto('/app-v2/');
  await clickNewChat(page);
  const pill = page.getByRole('button', { name: 'Model', exact: true });
  const plus = page.getByRole('button', { name: 'Add files and more' });
  await expect(pill.or(plus).first()).toBeVisible({ timeout: 15_000 });
  if (await pill.isVisible()) await pill.click();
  else
    await page
      .locator('header.conversation-heading')
      .getByRole('button', { name: /^(Model: |Choose a model$)/ })
      .click();
  await expect(page.getByText(LOCKDOWN_LINE)).toBeVisible();
  await shoot(page, info, 'company-locked-down-picker');
});

// Company policy beyond models (C3b): a tool the company switched off shows
// "Set by your company" in place of its switch in Settings › Tools. Every
// company route is answered here.

const limited: View = {
  ...joined,
  model_count: 0,
  policy_state: 'current',
  policy_reason: null,
  policy_since: '2026-11-24T09:00:00+00:00',
  limits: {
    tools_off: ['web_search'],
    max_level: 'auto',
    chat_export: false,
    plugins: { mode: 'none', allowed: [] },
    mcp_servers: { mode: 'allow-list', allowed: ['github'] },
    skills_hub: { mode: 'any', allowed: [] },
    channels: { mode: 'allow-list', allowed: ['slack'] },
  },
};

test('company policy: a tool the company switched off reads Set by your company in Settings › Tools', async ({
  page,
}, info) => {
  const state = { view: limited, asked: [] as string[] };
  await companyRoutes(page, state);

  await page.goto('/app-v2/settings/tools');
  await expect(page.getByText('Tavily web search').first()).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByText('Set by your company').first()).toBeVisible();
  await expect(page.getByLabel('Enable Tavily web search')).toHaveCount(0);
  await shoot(page, info, 'company-tools-set-by-company');
});

// The company's knowledge graph (B8): Knowledge home's Company scope beside
// Mine, read only. The company's state, graph and entity are answered here;
// nothing reaches a company server.

const COMPANY_SPACE = '5a000000-0000-4000-8000-000000000001';
const SALES_SPACE = '5a000000-0000-4000-8000-000000000002';
const ATLAS = 'e0000000-0000-4000-8000-000000000001';
const ADA = 'e0000000-0000-4000-8000-000000000002';
const ORION = 'e0000000-0000-4000-8000-000000000003';

const entityNode = (
  id: string,
  subject: string,
  entityType: string,
  relations: number,
  description: string,
) => ({
  id,
  revision: 'a'.repeat(64),
  subject,
  description,
  entity_type: entityType,
  source: 'document',
  updated_at: '2026-11-20T09:00:00+00:00',
  relation_count: relations,
  orphan: false,
  is_user: false,
  status: 'active',
  tier: 'resource',
  source_label: '',
});

const companyKnowledge: View = {
  schema_version: 1,
  state: 'ready',
  company: COMPANY,
  reason: null,
  nodes: [
    entityNode(ATLAS, 'Atlas', 'project', 2, 'The billing platform rebuild.'),
    entityNode(ADA, 'Ada Lovelace', 'person', 1, 'Leads the platform team.'),
    entityNode(ORION, 'Orion Foods', 'customer', 1, 'A customer since 2024.'),
  ],
  edges: [
    {
      id: 'f0000000-0000-4000-8000-000000000001',
      source_id: ADA,
      target_id: ATLAS,
      relation_type: 'leads',
      updated_at: '2026-11-20T09:00:00+00:00',
    },
    {
      id: 'f0000000-0000-4000-8000-000000000002',
      source_id: ORION,
      target_id: ATLAS,
      relation_type: 'uses',
      updated_at: '2026-11-21T09:00:00+00:00',
    },
  ],
  spaces: [
    { id: COMPANY_SPACE, name: 'Company', kind: 'company', is_owner: false },
    { id: SALES_SPACE, name: 'Sales', kind: 'group', is_owner: true },
  ],
  space_ids: [],
  total_entities: 3,
  total_relations: 2,
  shown_entities: 3,
  shown_relations: 2,
  truncated: false,
  entity_types: ['customer', 'person', 'project'],
};

const noKnowledge: View = {
  ...companyKnowledge,
  nodes: [],
  edges: [],
  total_entities: 0,
  total_relations: 0,
  shown_entities: 0,
  shown_relations: 0,
  entity_types: [],
};

const atlasDetail: View = {
  schema_version: 1,
  state: 'ready',
  company: COMPANY,
  reason: null,
  entity: {
    id: ATLAS,
    subject: 'Atlas',
    entity_type: 'project',
    description: 'The billing platform rebuild.',
    aliases: ['Project Atlas'],
    status: 'active',
    space_id: SALES_SPACE,
    space_name: 'Sales',
    updated_at: '2026-11-22T09:00:00+00:00',
    edited_at: null,
  },
  relations: [
    {
      relation_type: 'leads',
      direction: 'incoming',
      peer_id: ADA,
      peer_subject: 'Ada Lovelace',
      peer_type: 'person',
    },
    {
      relation_type: 'uses',
      direction: 'incoming',
      peer_id: ORION,
      peer_subject: 'Orion Foods',
      peer_type: 'customer',
    },
  ],
  mentions: [
    {
      kind: 'document',
      space: 'Company',
      document: 'Platform plan 2027',
      excerpt: 'The billing rebuild replaces the old system by June.',
    },
    {
      kind: 'fact',
      space: 'Sales',
      document: null,
      excerpt: 'Orion Foods runs its invoicing on the new platform.',
    },
  ],
  mention_count: 2,
};

/** The company's graph and entity; a search answers nothing. */
async function companyKnowledgeRoutes(page: Page, asked: URL[]) {
  await page.route('**/api/v1/company/graph**', async (route: Route) => {
    const url = new URL(route.request().url());
    asked.push(url);
    const spaces = url.searchParams.get('spaces');
    await route.fulfill({
      status: 200,
      json: url.searchParams.get('q')
        ? noKnowledge
        : { ...companyKnowledge, space_ids: spaces ? spaces.split(',') : [] },
    });
  });
  await page.route('**/api/v1/company/entities/**', (route: Route) =>
    route.fulfill({ status: 200, json: atlasDetail }),
  );
}

test('company knowledge: the Company scope beside Mine, its graph, filters and an entity’s citations, read only', async ({
  page,
}, info) => {
  const state = { view: joined, asked: [] as string[] };
  await companyRoutes(page, state);
  const asked: URL[] = [];
  await companyKnowledgeRoutes(page, asked);

  await page.goto('/app-v2/?tab=knowledge');
  const scope = page.getByRole('radiogroup', { name: 'Knowledge scope' });
  await expect(scope).toBeVisible({ timeout: 15_000 });
  await expect(scope.getByRole('radio', { name: 'Mine' })).toHaveAttribute(
    'aria-checked',
    'true',
  );
  expect(asked).toEqual([]); // The company's graph is read only once chosen.
  await shoot(page, info, 'company-knowledge-mine');

  await scope.getByRole('radio', { name: 'Company' }).click();
  await expect(page.getByText('3 entities')).toBeVisible();
  const drawn = page.locator(
    '.company-graph .knowledge-network-shell[data-renderer-status="ready"]',
  );
  const noWebgl = page.getByRole('status').filter({ hasText: 'needs WebGL' });
  await expect(drawn.or(noWebgl)).toBeVisible();
  expect(asked[0].search).toBe('?limit=300');
  await shoot(page, info, 'company-knowledge-graph');

  await page
    .getByRole('combobox', { name: 'Space' })
    .selectOption({ label: 'Sales' });
  await expect
    .poll(() => asked.at(-1)?.searchParams.get('spaces'))
    .toBe(SALES_SPACE);
  await page
    .getByRole('searchbox', { name: 'Search company knowledge' })
    .fill('nobody');
  await expect(page.getByText('Nothing matches')).toBeVisible();
  await shoot(page, info, 'company-knowledge-nothing-matches');
  await page.getByRole('button', { name: 'Show everything' }).click();
  await expect(page.getByText('3 entities')).toBeVisible();

  // Where the canvas can't draw, the entities are listed: a row opens one.
  // Company stays chosen on this device.
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (
      this: HTMLCanvasElement,
      kind: string,
      ...rest: unknown[]
    ) {
      return kind.startsWith('webgl')
        ? null
        : (original as (...args: unknown[]) => unknown).call(
            this,
            kind,
            ...rest,
          );
    } as typeof original;
  });
  await page.reload();
  await expect(page.getByRole('radio', { name: 'Company' })).toHaveAttribute(
    'aria-checked',
    'true',
    { timeout: 15_000 },
  );
  await page.getByRole('row', { name: /Atlas/ }).click();
  const detail = page.getByRole('region', { name: 'Selected entity detail' });
  await expect(
    detail.getByText('From Company: Platform plan 2027'),
  ).toBeVisible();
  await expect(detail.getByText('From Sales')).toBeVisible();
  await expect(detail.getByText('Edited in the admin console')).toBeVisible();
  await expect(
    page.getByRole('button', { name: /^(Edit|Merge|Archive|Delete)/ }),
  ).toHaveCount(0);
  await shoot(page, info, 'company-knowledge-entity');

  // Disconnected: C1b's words and the way back.
  await page.unroute('**/api/v1/company/graph**');
  await page.route('**/api/v1/company/graph**', (route: Route) =>
    route.fulfill({
      status: 200,
      json: { ...noKnowledge, state: 'disconnected', spaces: [] },
    }),
  );
  await page.reload();
  await expect(
    page.getByText('Your company disconnected this Row-Bot'),
  ).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole('link', { name: 'Sign in again' })).toBeVisible();
  await shoot(page, info, 'company-knowledge-disconnected');
});

// Company documents (B3): a space owner adds a document to their space in
// Settings › Documents. The company's documents are answered here; nothing
// reaches a company server.

const companyDocument = (over: View = {}): View => ({
  id: 'd0000000-0000-4000-8000-000000000002',
  title: 'Accounts 2026',
  file_name: 'accounts.md',
  byte_count: 2048,
  status: 'ready',
  status_reason: null,
  graph_status: 'done',
  graph_reason: null,
  version: 1,
  uploaded_at: '2026-11-20T09:00:00+00:00',
  ...over,
});

const companyDocuments = (
  documents: View[],
  outcome: string | null = null,
): View => ({
  schema_version: 1,
  state: 'ready',
  company: COMPANY,
  reason: null,
  spaces: [{ id: SALES_SPACE, name: 'Sales', kind: 'group', is_owner: true }],
  space_id: SALES_SPACE,
  documents,
  max_upload_bytes: 25 * 1024 * 1024,
  supported_types: ['.docx', '.markdown', '.md', '.txt'],
  outcome,
});

test('company documents: an owner adds a document to their space in Settings › Documents', async ({
  page,
}, info) => {
  const state = { view: joined, asked: [] as string[] };
  await companyRoutes(page, state);
  let documents = [companyDocument()];
  const uploads: { name: string | undefined; body: string | null }[] = [];
  await page.route('**/api/v1/company/documents**', (route: Route) =>
    route.fulfill({ status: 200, json: companyDocuments(documents) }),
  );
  await page.route(
    '**/api/v1/company/spaces/*/documents',
    async (route: Route) => {
      const request = route.request();
      uploads.push({
        name: request.headers()['x-file-name'],
        body: request.postData(),
      });
      documents = [
        companyDocument({
          id: 'd0000000-0000-4000-8000-000000000009',
          title: 'holiday-notes',
          file_name: 'holiday-notes.md',
          byte_count: 24,
          status: 'queued',
          graph_status: 'pending',
        }),
        ...documents,
      ];
      await route.fulfill({
        status: 200,
        json: companyDocuments(documents, 'uploaded'),
      });
    },
  );

  await page.goto('/app-v2/settings/documents');
  const group = page.getByRole('region', { name: 'Company documents' });
  await expect(group).toBeVisible({ timeout: 15_000 });
  await expect(group.getByText('Accounts 2026')).toBeVisible();
  await expect(group.getByText('Ready', { exact: true })).toBeVisible();
  await shoot(page, info, 'company-documents');

  const chooser = page.waitForEvent('filechooser');
  await group.getByRole('button', { name: 'Add a document to Sales' }).click();
  await (
    await chooser
  ).setFiles({
    name: 'holiday-notes.md',
    mimeType: 'text/markdown',
    buffer: Buffer.from('Holiday policy: 25 days.'),
  });
  await expect(group.getByRole('status')).toHaveText(
    'holiday-notes.md was added. Your company’s server reads it next.',
  );
  await expect(group.getByText('Waiting to be read')).toBeVisible();
  expect(uploads).toEqual([
    { name: 'holiday-notes.md', body: 'Holiday policy: 25 days.' },
  ]);
  await shoot(page, info, 'company-documents-added');
});

// Share to company (B4b): a reply's action sends it to a space's review
// queue, and Settings › Company lists it under Your shares. The company's
// answers are given here; nothing reaches a company server.

const sharing: View = {
  ...limited,
  limits: {
    ...(limited.limits as View),
    chat_export: true,
    share_to_company: true,
  },
};

const sharesView = (shares: View[], outcome: string | null = null): View => ({
  schema_version: 1,
  state: 'ready',
  company: COMPANY,
  reason: null,
  allowed: true,
  spaces: [
    { id: SALES_SPACE, name: 'Sales', kind: 'group', is_owner: true },
    { id: COMPANY_SPACE, name: 'Company', kind: 'company', is_owner: false },
  ],
  shares,
  truncated: false,
  outcome,
  words: null,
});

test('share to company: a reply goes to Sales for review, then waits in Your shares', async ({
  page,
}, info) => {
  const state = { view: sharing, asked: [] as string[] };
  await companyRoutes(page, state);
  let shares: View[] = [];
  const sent: View[] = [];
  await page.route('**/api/v1/company/shares**', (route: Route) =>
    route.fulfill({ status: 200, json: sharesView(shares) }),
  );
  await page.route(
    '**/api/v1/company/spaces/*/shares',
    async (route: Route) => {
      const body = route.request().postDataJSON() as View;
      sent.push(body);
      shares = [
        {
          id: 'b0000000-0000-4000-8000-000000000001',
          title: body.title,
          text: body.text,
          source_note: body.source_note || null,
          space_id: SALES_SPACE,
          space_name: 'Sales',
          moved: false,
          status: 'pending',
          submitted_at: '2026-11-24T10:00:00+00:00',
          decided_at: null,
          reason: null,
          fact_id: null,
        },
      ];
      await route.fulfill({ status: 200, json: sharesView(shares, 'sent') });
    },
  );

  await newConversation(page);
  const log = page.getByRole('log', { name: 'Conversation' });
  await page
    .getByRole('textbox', { name: 'Message', exact: true })
    .fill('First fork fixture');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  const reply = log
    .getByRole('article', { name: 'Row-Bot message' })
    .filter({ hasText: 'Noted: First.' });
  await expect(reply).toBeVisible({ timeout: 15_000 });

  if (phone(info)) {
    await reply.locator('.message-text').click({ button: 'right' });
    await page
      .getByRole('menu')
      .getByRole('menuitem', { name: 'Share to company' })
      .click();
  } else {
    await reply.hover();
    await reply.getByRole('button', { name: 'Share to company' }).click();
  }
  const dialog = page.getByRole('dialog', { name: 'Share to company' });
  await expect(
    dialog.getByText('Owners of Sales review this before anyone else sees it.'),
  ).toBeVisible();
  await expect(dialog.getByLabel(/^Text/)).toHaveValue(/Noted: First\./);
  await expect(dialog.getByLabel(/^Note/)).toHaveValue(/^From a chat on /);
  await shoot(page, info, 'company-share-dialog');

  await page.getByRole('button', { name: 'Send for review' }).click();
  await expect(page.getByText('Sent to Sales for review')).toBeVisible();
  expect(sent).toHaveLength(1);
  expect(sent[0].title).toBe('Noted: First.');
  expect(String(sent[0].text)).toContain('Noted: First.');
  expect(String(sent[0].source_note)).toMatch(/^From a chat on /);

  await page.getByRole('button', { name: 'Your shares' }).click();
  await expect(page).toHaveURL(/\/app-v2\/settings\/company/);
  const group = page.getByRole('region', { name: 'Your shares' });
  await expect(group.getByText('Waiting for review')).toBeVisible({
    timeout: 15_000,
  });
  await expect(
    group.getByRole('button', { name: 'Withdraw “Noted: First.”' }),
  ).toBeVisible();
  await shoot(page, info, 'company-your-shares');
});
