import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type {
  IntegrationDetail,
  IntegrationEntry,
  InstallPlan,
  PlanStep,
} from '../../api/types';
import type { ClientPlatform } from '../../platform';
import { RuntimeContext } from '../../runtime';
import { WorkspaceActionsContext } from '../shell/workspace-actions';
import AccessSheet from './AccessSheet';
import AppsArea from './AppsArea';

const digest = 'd'.repeat(64);
const revision = 'a'.repeat(64);

function entry(fields: Partial<IntegrationEntry> = {}): IntegrationEntry {
  return {
    id: 'mcp:curated:notion',
    kind: 'mcp',
    parent_id: null,
    name: 'Notion',
    description: 'Pages and databases.',
    app: null,
    icon: 'letter:N',
    verified: false,
    source: 'recommended',
    method: 'hosted_sign_in',
    publisher: 'Notion',
    version: '',
    installed: false,
    enabled: false,
    account_label: '',
    compatibility: 'not_inspected',
    evidence: 'listed',
    tested_with_row_bot: false,
    lifecycle: 'available',
    readiness: null,
    blockers: [],
    next_action: { kind: 'connect', label: 'Connect' },
    attributions: [],
    children: [],
    ...fields,
  };
}

function step(
  type: string,
  state: PlanStep['state'],
  extra: Partial<PlanStep> = {},
): PlanStep {
  return {
    id: type,
    type: type as PlanStep['type'],
    state,
    title: `Step ${type}`,
    message: '',
    ...extra,
  };
}

function plan(fields: Partial<InstallPlan> = {}): InstallPlan {
  return {
    schema_version: 1,
    plan_id: null,
    item_id: 'mcp:curated:notion',
    kind: 'mcp',
    name: 'Notion',
    intent: 'connect',
    digest,
    state: 'ready',
    pause: null,
    current_step: 'consent',
    steps: [
      step('consent', 'pending'),
      step('sign_in', 'pending'),
      step('access', 'pending'),
      step('enable', 'pending'),
    ],
    consent: {
      destinations: ['https://mcp.notion.com/mcp'],
      runs_locally: false,
      downloads: [],
      access_preset: 'ask',
    },
    supported: true,
    unsupported_reason: '',
    message: '',
    next_action: { kind: 'connect', label: 'Connect' },
    consent_token: 'token',
    ...fields,
  };
}

function detail(fields: Partial<IntegrationDetail> = {}): IntegrationDetail {
  return {
    entry: entry(),
    plan: plan(),
    about: {
      license: '',
      source_url: '',
      pin: '',
      identifier: 'mcp:curated:notion',
      destination: 'https://mcp.notion.com/mcp',
      runs_locally: false,
      saved_key: false,
      signs_in: true,
      signed_in: false,
      requirements: [],
      access: null,
      package: '',
      files: [],
      profiles: [],
      actions: [],
    },
    ...fields,
  };
}

const access = {
  preset: 'ask' as const,
  tools_digest: 't'.repeat(64),
  tools: [
    {
      name: 'search_pages',
      title: 'Search pages',
      description: 'Find pages.',
      effect: 'read_only' as const,
      state: 'use' as const,
      always_asks: false,
    },
    {
      name: 'update_page',
      title: 'Update page',
      description: 'Change a page.',
      effect: 'mutation' as const,
      state: 'ask' as const,
      always_asks: false,
    },
    {
      name: 'delete_page',
      title: 'Delete page',
      description: 'Delete a page.',
      effect: 'mutation' as const,
      state: 'ask' as const,
      always_asks: true,
    },
  ],
};

function Where() {
  const location = useLocation();
  return (
    <output aria-label="Location">{location.pathname + location.search}</output>
  );
}

function show(
  path: string,
  controller: Record<string, unknown>,
  newChat = vi.fn(),
) {
  const platform = {
    openExternal: vi.fn(async () => ({ status: 'ok' })),
  } as unknown as ClientPlatform;
  const fake = {
    integrationIcon: vi.fn(async () => new Blob(['x'], { type: 'image/png' })),
    integrationApps: vi.fn(async () => ({ schema_version: 1, items: [] })),
    ...controller,
  } as unknown as ClientController;
  render(
    <RuntimeContext.Provider value={{ controller: fake, platform }}>
      <WorkspaceActionsContext.Provider
        value={{ resetLayout: vi.fn(), newChat }}
      >
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route
              path="settings/:setting/*"
              element={
                <>
                  <Area />
                  <Where />
                </>
              }
            />
          </Routes>
        </MemoryRouter>
      </WorkspaceActionsContext.Provider>
    </RuntimeContext.Provider>,
  );
  return {
    controller: fake as unknown as Record<string, ReturnType<typeof vi.fn>>,
    platform,
    newChat,
  };
}

function Area() {
  const location = useLocation();
  const [, , leaf, ...rest] = location.pathname.split('/');
  return (
    <AppsArea
      kind={leaf === 'skills' ? 'skill' : 'app'}
      item={rest.join('/')}
      editor={(kind, id) => <p>Editor for {id}</p>}
      chat={null}
    />
  );
}

const page = (items: IntegrationEntry[], sources: unknown[] = []) => ({
  schema_version: 1,
  revision,
  items,
  total: items.length,
  next_cursor: null,
  sources,
});

afterEach(() => {
  vi.useRealTimers();
});

it('puts your apps first, attention first, and only searches online when asked', async () => {
  const integrationItems = vi.fn(async ({ scope }: { scope: string }) =>
    scope === 'installed'
      ? page([
          entry({
            id: 'mcp:a',
            name: 'Calm',
            installed: true,
            lifecycle: 'installed',
            readiness: 'ready',
          }),
          entry({
            id: 'mcp:b',
            name: 'Broken',
            installed: true,
            lifecycle: 'installed',
            readiness: 'attention',
          }),
        ])
      : page(
          [entry()],
          [
            {
              source: 'official',
              status: 'pending',
              message: '',
              fetched_at: null,
            },
          ],
        ),
  );
  const searchIntegrationItems = vi.fn(async () =>
    page([entry({ id: 'mcp:online', name: 'Online' })]),
  );
  show('/settings/apps', { integrationItems, searchIntegrationItems });
  const yours = await screen.findByRole('region', { name: 'Your apps' });
  const cards = within(yours).getAllByRole('link');
  expect(cards[0]).toHaveAccessibleName(/^Broken.*Needs attention/);
  expect(cards[1]).toHaveAccessibleName(/^Calm.*Ready/);
  expect(screen.getByText(/Preparing the app catalog/)).toBeVisible();
  fireEvent.change(screen.getByRole('searchbox', { name: 'Search apps' }), {
    target: { value: 'notes' },
  });
  await waitFor(() =>
    expect(integrationItems).toHaveBeenCalledWith(
      expect.objectContaining({ scope: 'catalog', query: 'notes' }),
      expect.anything(),
    ),
  );
  expect(searchIntegrationItems).not.toHaveBeenCalled();
  fireEvent.click(
    await screen.findByRole('button', { name: 'Search online catalogs' }),
  );
  await waitFor(() =>
    expect(searchIntegrationItems).toHaveBeenCalledWith(
      expect.objectContaining({ query: 'notes', refresh: true, kind: 'app' }),
    ),
  );
  expect(await screen.findByRole('link', { name: /^Online/ })).toHaveAttribute(
    'href',
    expect.stringContaining('item?id=mcp%3Aonline&r='),
  );
});

it('offers a way on when nothing matches', async () => {
  show('/settings/apps?q=zzz', {
    integrationItems: vi.fn(async () => page([])),
  });
  const empty = await screen.findByRole('heading', {
    name: 'No apps match “zzz”',
  });
  const state = empty.closest('.empty-state') as HTMLElement;
  expect(
    within(state).getByRole('button', { name: 'Search online catalogs' }),
  ).toBeVisible();
  expect(
    within(state).getByRole('button', { name: 'Add from link or file' }),
  ).toBeVisible();
});

it('loads icons only from the local icon route, as blobs', async () => {
  const { controller } = show('/settings/apps', {
    integrationItems: vi.fn(async () => page([entry({ icon: 'si:notion' })])),
  });
  await screen.findAllByRole('link', { name: /^Notion/ });
  await waitFor(() =>
    expect(controller.integrationIcon).toHaveBeenCalledWith('si:notion'),
  );
  await waitFor(() =>
    expect(
      document.querySelector('.app-icon img')?.getAttribute('src'),
    ).toMatch(/^blob:/),
  );
});

it('connects after one consent, follows the plan, and lets the access sheet choose', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const running = plan({
    plan_id: 'b1b1b1b1-0000-4000-8000-000000000001',
    state: 'running',
    current_step: 'sign_in',
    steps: [
      step('consent', 'done'),
      step('sign_in', 'running'),
      step('access', 'pending'),
      step('enable', 'pending'),
    ],
  });
  const paused = {
    ...running,
    state: 'paused' as const,
    pause: 'access' as const,
    current_step: 'access',
    steps: [
      step('consent', 'done'),
      step('sign_in', 'done'),
      step('access', 'waiting', { access }),
      step('enable', 'pending'),
    ],
  };
  const completed = {
    ...paused,
    state: 'completed' as const,
    pause: null,
    installed_id: 'mcp:abc',
    message: 'Ready to use.',
  };
  const controller = {
    integrationDetail: vi.fn(async () => detail()),
    reviewInstallPlan: vi.fn(async () => plan()),
    startInstallPlan: vi.fn(async () => running),
    // One missed look (the network blinked) does not stop the plan's progress.
    installPlan: vi
      .fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValue(paused),
    continueInstallPlan: vi.fn(async () => completed),
  };
  show('/settings/apps/item?id=mcp%3Acurated%3Anotion', controller);
  fireEvent.click(await screen.findByRole('button', { name: 'Connect' }));
  const consent = await screen.findByRole('dialog', { name: 'Connect Notion' });
  expect(
    within(consent).getByText('What you ask goes to mcp.notion.com.'),
  ).toBeVisible();
  expect(
    within(consent).getByText('You sign in to Notion in your browser.'),
  ).toBeVisible();
  expect(controller.startInstallPlan).not.toHaveBeenCalled(); // Nothing starts before consent.
  fireEvent.click(within(consent).getByRole('button', { name: 'Connect' }));
  await waitFor(() =>
    expect(controller.startInstallPlan).toHaveBeenCalledWith(
      expect.objectContaining({
        item_id: 'mcp:curated:notion',
        digest,
        consent_token: 'token',
        intent: 'connect',
      }),
    ),
  );
  expect(
    await screen.findByRole('list', { name: 'Setup steps' }),
  ).toHaveTextContent('Step sign_in: running');
  await act(() => vi.advanceTimersByTimeAsync(800));
  await act(() => vi.advanceTimersByTimeAsync(3100));
  expect(controller.installPlan).toHaveBeenCalledTimes(2);
  const sheet = await screen.findByRole('dialog', {
    name: "Here's what Notion can do",
  });
  fireEvent.click(within(sheet).getByRole('radio', { name: /Full access/ }));
  fireEvent.click(within(sheet).getByText('Customise'));
  const locked = within(sheet).getByRole('combobox', {
    name: 'What Delete page may do',
  });
  expect(
    within(locked).queryByRole('option', { name: 'Use without asking' }),
  ).toBeNull();
  fireEvent.change(
    within(sheet).getByRole('combobox', { name: 'What Update page may do' }),
    { target: { value: 'use' } },
  );
  fireEvent.click(within(sheet).getByRole('button', { name: 'Allow' }));
  await waitFor(() =>
    expect(controller.continueInstallPlan).toHaveBeenCalledWith(
      running.plan_id,
      {
        preset: 'full',
        overrides: { update_page: 'use' },
        tools_digest: access.tools_digest,
      },
    ),
  );
  await waitFor(() =>
    expect(screen.getByLabelText('Location')).toHaveTextContent(
      '/settings/apps/item?id=mcp%3Aabc',
    ),
  );
});

it('shows the recovery banner for an unfinished change, and Retry continues the same plan', async () => {
  const uncertain = plan({
    plan_id: 'b1b1b1b1-0000-4000-8000-000000000002',
    state: 'uncertain',
    current_step: 'enable',
    steps: [step('consent', 'done'), step('enable', 'running')],
  });
  const controller = {
    integrationDetail: vi.fn(async () => detail({ plan: uncertain })),
    reviewInstallPlan: vi.fn(async () => uncertain),
    continueInstallPlan: vi.fn(async () => ({
      ...uncertain,
      state: 'completed' as const,
    })),
  };
  show('/settings/apps/item?id=mcp%3Acurated%3Anotion', controller);
  expect(await screen.findByText('Finishing your last change…')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await waitFor(() =>
    expect(controller.continueInstallPlan).toHaveBeenCalledWith(
      uncertain.plan_id,
      {},
    ),
  );
});

it('removes only with a danger confirmation, and cleanup is part of what is agreed', async () => {
  const installed = entry({
    id: 'mcp:abc',
    installed: true,
    lifecycle: 'installed',
    readiness: 'ready',
    next_action: { kind: 'try', label: 'Try it' },
  });
  const review = vi.fn(async ({ cleanup }: { cleanup?: boolean }) =>
    plan({
      intent: 'remove',
      item_id: 'mcp:abc',
      steps: [step('consent', 'pending'), step('enable', 'pending')],
      consent: {
        destinations: [],
        runs_locally: true,
        downloads: [],
        access_preset: 'ask',
        cleanup: Boolean(cleanup),
      },
    }),
  );
  show('/settings/apps/item?id=mcp%3Aabc', {
    integrationDetail: vi.fn(async () =>
      detail({
        entry: installed,
        plan: null,
        about: {
          ...detail().about,
          signed_in: true,
          actions: ['turn_off', 'remove'],
        },
      }),
    ),
    reviewInstallPlan: review,
  });
  fireEvent.pointerDown(
    await screen.findByRole('button', { name: 'More for Notion' }),
    { button: 0 },
  );
  fireEvent.click(await screen.findByRole('menuitem', { name: 'Remove…' }));
  const dialog = await screen.findByRole('dialog', { name: 'Remove Notion' });
  await waitFor(() =>
    expect(
      within(dialog).getByRole('button', { name: 'Cancel' }),
    ).toHaveFocus(),
  );
  expect(within(dialog).getByRole('button', { name: 'Remove' })).toHaveClass(
    'danger',
  );
  fireEvent.click(
    within(dialog).getByRole('switch', {
      name: 'Also delete saved keys and data',
    }),
  );
  await waitFor(() =>
    expect(review).toHaveBeenLastCalledWith(
      expect.objectContaining({ intent: 'remove', cleanup: true }),
    ),
  );
});

it('renders a step type it has never seen as title, state and message', async () => {
  const future = plan({
    plan_id: 'b1b1b1b1-0000-4000-8000-000000000003',
    state: 'paused',
    pause: 'resume',
    current_step: 'oauth_client',
    steps: [
      step('consent', 'done'),
      step('oauth_client', 'waiting', {
        title: 'Add your own sign-in app',
        message: 'Paste its client ID.',
      }),
    ],
  });
  show('/settings/apps/item?id=mcp%3Acurated%3Anotion', {
    integrationDetail: vi.fn(async () => detail({ plan: future })),
    reviewInstallPlan: vi.fn(async () => future),
  });
  const steps = await screen.findByRole('list', { name: 'Setup steps' });
  expect(steps).toHaveTextContent('Add your own sign-in app: waiting');
  expect(steps).toHaveTextContent('Paste its client ID.');
  expect(screen.getByRole('button', { name: 'Continue' })).toBeVisible();
});

it('shows what is inside a skill, its chat command, and Try it only drafts a new chat', async () => {
  const skill = entry({
    id: 'skill:pdf-helper',
    kind: 'skill',
    name: 'pdf-helper',
    method: '',
    installed: true,
    lifecycle: 'installed',
    readiness: 'ready',
    next_action: { kind: 'try', label: 'Try it' },
  });
  const { newChat } = show('/settings/skills/pdf-helper', {
    integrationDetail: vi.fn(async () =>
      detail({
        entry: skill,
        plan: null,
        about: {
          ...detail().about,
          destination: '',
          profiles: ['Research'],
          files: [
            { path: 'SKILL.md', size_bytes: 10, executable: false },
            { path: 'run.sh', size_bytes: 4, executable: true },
          ],
        },
      }),
    ),
  });
  expect(await screen.findByText('/pdf-helper')).toBeVisible();
  const inside = screen.getByText("What's inside").closest('details')!;
  expect(inside).not.toHaveAttribute('open');
  expect(inside).toHaveTextContent('2 files · 1 script');
  expect(
    screen.getByText(/Also in these agent profiles: Research/),
  ).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Try it' }));
  expect(newChat).toHaveBeenCalledWith('/pdf-helper ');
});

it('adds from a pasted link without fetching anything, then opens what it found', async () => {
  const resolveIntegration = vi.fn(async () =>
    page([
      entry({ id: 'mcp:link:abc', name: 'mcp.example.com', source: 'link' }),
    ]),
  );
  show('/settings/apps', {
    integrationItems: vi.fn(async () => page([])),
    integrationDetail: vi.fn(() => new Promise(() => {})),
    resolveIntegration,
  });
  fireEvent.click(
    (
      await screen.findAllByRole('button', { name: 'Add from link or file' })
    )[0],
  );
  const dialog = await screen.findByRole('dialog', {
    name: 'Add from link or file',
  });
  fireEvent.change(within(dialog).getByRole('textbox', { name: /^Link/ }), {
    target: { value: 'https://mcp.example.com/mcp' },
  });
  expect(within(dialog).getByLabelText(/^Or choose a file/)).toHaveAttribute(
    'accept',
    '.zip,.skill,.mcpb',
  );
  fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }));
  await waitFor(() =>
    expect(resolveIntegration).toHaveBeenCalledWith({
      reference: 'https://mcp.example.com/mcp',
      kind: '',
    }),
  );
  await waitFor(() =>
    expect(screen.getByLabelText('Location')).toHaveTextContent(
      '/settings/apps/item?id=mcp%3Alink%3Aabc&r=',
    ),
  );
});

it('keeps a custom access policy as it is when the person saves without changing it', async () => {
  const onAllow = vi.fn();
  render(
    <AccessSheet
      open
      change
      name="Notion"
      busy={false}
      onCancel={() => undefined}
      onAllow={onAllow}
      access={{
        preset: 'custom',
        tools_digest: digest,
        tools: [
          {
            name: 'search',
            title: 'Search',
            effect: 'read_only',
            state: 'off',
          },
          {
            name: 'update_page',
            title: 'Update page',
            effect: 'mutation',
            state: 'use',
          },
          {
            name: 'delete_page',
            title: 'Delete page',
            effect: 'mutation',
            state: 'ask',
            always_asks: true,
          },
        ],
      }}
    />,
  );
  const dialog = await screen.findByRole('dialog', {
    name: 'Change what Notion can do',
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
  expect(onAllow).toHaveBeenCalledWith({
    preset: 'ask',
    tools_digest: digest,
    overrides: { search: 'off', update_page: 'use', delete_page: 'ask' },
  });
  // Picking a preset means it: every tool follows it again.
  fireEvent.click(within(dialog).getByRole('radio', { name: /Read only/ }));
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
  expect(onAllow).toHaveBeenLastCalledWith({
    preset: 'read_only',
    tools_digest: digest,
    overrides: {},
  });
});

it('checks an unfinished change again when no plan owns it, and never starts a new one', async () => {
  const stuck = entry({
    id: 'plugin:kit',
    kind: 'plugin',
    installed: true,
    lifecycle: 'installed',
    readiness: 'attention',
    blockers: [
      {
        code: 'change_unconfirmed',
        severity: 'blocking',
        message: '',
        subject: '',
      },
    ],
    next_action: { kind: 'retry', label: 'Retry' },
  });
  const controller = {
    integrationDetail: vi.fn(async () => detail({ entry: stuck, plan: null })),
    settleIntegration: vi.fn(async () =>
      detail({
        entry: {
          ...stuck,
          readiness: 'ready',
          blockers: [],
          next_action: { kind: 'none', label: '' },
        },
        plan: null,
      }),
    ),
    reviewInstallPlan: vi.fn(),
  };
  show('/settings/apps/item?id=plugin%3Akit', controller);
  expect(await screen.findByText('Finishing your last change…')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await waitFor(() =>
    expect(controller.settleIntegration).toHaveBeenCalledWith({
      item_id: 'plugin:kit',
    }),
  );
  await waitFor(() =>
    expect(
      screen.queryByText('Finishing your last change…'),
    ).not.toBeInTheDocument(),
  );
  expect(controller.reviewInstallPlan).not.toHaveBeenCalled();
});

it('lets each tool be chosen one by one when its app overlaps Row-Bot, with no preset to pick', async () => {
  const onAllow = vi.fn();
  render(
    <AccessSheet
      open
      change={false}
      name="Notion"
      busy={false}
      onCancel={() => undefined}
      onAllow={onAllow}
      access={{
        preset: 'ask',
        manual: true,
        tools_digest: digest,
        tools: [
          {
            name: 'search',
            title: 'Search',
            effect: 'read_only',
            state: 'off',
          },
          {
            name: 'delete_page',
            title: 'Delete page',
            effect: 'mutation',
            state: 'off',
            always_asks: true,
          },
        ],
      }}
    />,
  );
  const dialog = await screen.findByRole('dialog', {
    name: "Here's what Notion can do",
  });
  expect(within(dialog).queryByRole('radio')).toBeNull();
  expect(
    within(dialog).getByText(/each\s+stays off until you turn it on/),
  ).toBeVisible();
  fireEvent.change(
    within(dialog).getByRole('combobox', { name: 'What Search may do' }),
    {
      target: { value: 'use' },
    },
  );
  fireEvent.click(within(dialog).getByRole('button', { name: 'Allow' }));
  expect(onAllow).toHaveBeenCalledWith({
    preset: 'ask',
    tools_digest: digest,
    overrides: { search: 'use', delete_page: 'off' },
  });
});

it('stops every app at once from Advanced with the reviewed policy command', async () => {
  const page = {
    schema_version: 1,
    revision: revision,
    server_id: null,
    availability: 'available',
    global_enabled: true,
    server_enabled: null,
    resources_enabled: null,
    prompts_enabled: null,
    items: [],
    total: 0,
    next_cursor: null,
  };
  const controller = {
    integrationSources: vi.fn(async () => ({ schema_version: 1, items: [] })),
    catalogSchedule: vi.fn(async () => null),
    mcpPolicy: vi
      .fn()
      .mockResolvedValueOnce(page)
      .mockResolvedValue({ ...page, global_enabled: false }),
    reviewMcpPolicy: vi.fn(async () => ({ nonce: 'n' })),
    executeMcpConfiguration: vi.fn(async () => ({ status: 'completed' })),
  };
  show('/settings/apps?view=advanced', controller);
  const use = await screen.findByRole('switch', { name: 'Use apps' });
  await waitFor(() => expect(use).toBeChecked());
  fireEvent.click(use);
  await waitFor(() =>
    expect(controller.reviewMcpPolicy).toHaveBeenCalledWith({
      configuration_revision: revision,
      intent: { operation: 'global_enabled', enabled: false },
    }),
  );
  expect(controller.executeMcpConfiguration).toHaveBeenCalledWith(
    expect.objectContaining({ type: 'mcp.configuration.control' }),
    { nonce: 'n' },
  );
  await waitFor(() => expect(use).not.toBeChecked());
});
