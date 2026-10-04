import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { ClientPlatform } from '../../platform';
import type {
  IntegrationItem,
  IntegrationPage,
  IntegrationPreview,
} from '../../api/types';
import { RuntimeContext } from '../../runtime';
import { retainCommand } from '../../api/retained-command';
import IntegrationsPage from './IntegrationsPage';

const item: IntegrationItem = {
  id: 'skill:writing',
  kind: 'skill',
  owner_ref: 'writing',
  name: 'Writing',
  description: 'Local writing instructions',
  parent_id: null,
  source: 'local',
  publisher: '',
  source_url: '',
  version: '1',
  pin: '',
  license: 'MIT',
  compatibility: 'supported',
  reasons: [],
  platforms: [],
  evidence: 'Local only',
  installed: true,
  enabled: false,
  status: 'off',
  revision: 'a'.repeat(64),
  actions: ['configure'],
  auth_status: 'none',
  account_label: '',
  children: [],
  target: null,
};
const page = {
  schema_version: 1 as const,
  revision: 'a'.repeat(64),
  items: [item],
  total: 1,
  next_cursor: null,
  sources: [],
};
function Location() {
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <>
      <output aria-label="Location">{location.search}</output>
      <button onClick={() => navigate(-1)}>Browser back</button>
      <button onClick={() => navigate(1)}>Browser forward</button>
      <button
        onClick={() =>
          navigate('/settings/integrations?type=plugin&tab=discover')
        }
      >
        Go to plugins
      </button>
      <button
        onClick={() =>
          navigate(
            '/settings/integrations?type=skill&tab=discover&selected=skill%3Asecond',
          )
        }
      >
        Go to second skill
      </button>
    </>
  );
}
const source = (
  id: string,
  kind: 'skill' | 'mcp' | 'plugin',
  label: string,
  enabled = true,
) => ({
  id,
  kinds: [kind],
  label,
  access: enabled ? ('public' as const) : ('unavailable' as const),
  eligibility: enabled ? ('eligible' as const) : ('auth_required' as const),
  network: 'explicit' as const,
  enabled,
  message: '',
});
const sourceList = {
  schema_version: 1 as const,
  items: [
    source('recommended', 'mcp', 'Vendor recommendations'),
    source('official', 'mcp', 'Official MCP Registry'),
    source('clawhub', 'skill', 'ClawHub'),
    source('github', 'skill', 'GitHub'),
    source('skills_sh', 'skill', 'skills.sh', false),
    source('browse_sh', 'skill', 'browse.sh', false),
    source('lobehub', 'skill', 'LobeHub', false),
    source('hermes', 'plugin', 'Hermes'),
    source('native', 'plugin', 'Row-Bot marketplace'),
  ],
};
function fixture(
  path = '/settings/integrations?type=skill',
  discovery?: IntegrationPage,
  installed = item,
) {
  const controller = {
    integrationSources: vi.fn().mockResolvedValue(sourceList),
    updateIntegrationSource: vi.fn(),
    integrations: vi.fn().mockResolvedValue({ ...page, items: [installed] }),
    integration: vi.fn().mockResolvedValue(installed),
    plugin: vi.fn().mockResolvedValue({
      settings: [],
      secrets: [],
      health: { status: 'passed' },
      enabled: false,
      permissions: [],
      capabilities: { enable: { available: true } },
    }),
    mcpConfiguration: vi.fn().mockResolvedValue({
      revision: item.revision,
      availability: 'available',
      items: [],
    }),
    reviewPluginLifecycle: vi.fn(),
    executePluginLifecycle: vi.fn(),
    searchIntegrations: vi
      .fn()
      .mockResolvedValue(
        discovery ?? { ...page, items: [{ ...item, installed: false }] },
      ),
    skill: vi.fn().mockResolvedValue({
      schema_version: 1,
      library_revision: item.revision,
      skill: {
        id: 'writing',
        instructions: 'Writing instructions',
        activation: {},
        available: false,
        pinned: false,
      },
    }),
    previewIntegration: vi.fn(),
    installSkillHub: vi.fn(),
    searchSkillHub: vi.fn(),
    previewSkillHub: vi.fn(),
    reconcileIntegrationOperation: vi.fn().mockResolvedValue({
      settled: true,
      message: 'Recovered original package.',
    }),
  };
  const view = render(
    <RuntimeContext.Provider
      value={{
        controller: controller as unknown as ClientController,
        platform: {} as ClientPlatform,
      }}
    >
      <MemoryRouter initialEntries={[path]}>
        <Location />
        <IntegrationsPage
          renderDetail={(row) => <p>Editor for {row.name}</p>}
          renderAdvanced={() => <p>Advanced editor</p>}
        />
      </MemoryRouter>
    </RuntimeContext.Provider>,
  );
  return { ...controller, ...view };
}
afterEach(() => {
  sessionStorage.clear();
  localStorage.clear();
});

it('loads local inventory, focuses selected details and preserves URL filters', async () => {
  const controller = fixture('/settings/integrations?type=skill&q=writing');
  fireEvent.click(await screen.findByRole('button', { name: 'Writing' }));
  expect(
    await screen.findByRole('region', { name: 'Skill availability' }),
  ).toBeVisible();
  await waitFor(() =>
    expect(screen.getByRole('heading', { name: 'Writing' })).toHaveFocus(),
  );
  expect(screen.getByLabelText('Location')).toHaveTextContent(
    'type=skill&q=writing&selected=skill%3Awriting',
  );
  expect(controller.searchIntegrations).not.toHaveBeenCalled();
  expect(controller.previewIntegration).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Back to integrations' }));
  expect(screen.getByLabelText('Location')).not.toHaveTextContent('selected=');
  expect(screen.getByRole('button', { name: 'Writing' })).toHaveFocus();
});

it('recovers a retained operation without sending a second command', async () => {
  const id = crypto.randomUUID();
  retainCommand('integrations:plugin', id);
  const controller = fixture();
  fireEvent.click(
    await screen.findByRole('button', {
      name: 'Check original plugin operation',
    }),
  );
  expect(await screen.findByText('Recovered original package.')).toBeVisible();
  expect(
    controller.reconcileIntegrationOperation,
  ).toHaveBeenCalledExactlyOnceWith('plugin', id);
  expect(controller.previewIntegration).not.toHaveBeenCalled();
});

it('keeps specialized advanced editors in one active view', async () => {
  const controller = fixture();
  await screen.findByRole('button', { name: 'Writing' });
  fireEvent.click(
    screen.getByText('Advanced configuration and existing editors'),
  );
  fireEvent.click(
    screen.getByRole('button', {
      name: 'Create, import, pin and maintain skills',
    }),
  );
  expect(screen.getByText('Advanced editor')).toBeVisible();
  expect(
    screen.queryByRole('button', { name: 'Writing' }),
  ).not.toBeInTheDocument();
  controller.integrations.mockResolvedValue({
    ...page,
    items: [{ ...item, name: 'Edited writing' }],
  });
  fireEvent.click(screen.getByRole('button', { name: 'Back to integrations' }));
  expect(
    await screen.findByRole('button', { name: 'Edited writing' }),
  ).toBeVisible();
});

it('retains an uncertain skill installation for recovery instead of allowing a duplicate', async () => {
  const controller = fixture(
    '/settings/integrations?tab=discover&source=clawhub',
  );
  controller.previewIntegration.mockResolvedValue({
    kind: 'skill',
    skill: {
      skill_name: 'writing',
      preview_id: 'fixture',
      content_hash: 'a'.repeat(64),
      entry: { author: 'Fixture', source: 'fixture', url: '' },
      primary_text: 'Write clearly.',
      files: ['SKILL.md'],
      scan: { blocked: false, findings: [] },
    },
  });
  controller.installSkillHub.mockResolvedValue({
    success: false,
    message: 'Outcome unconfirmed.',
  });
  controller.reconcileIntegrationOperation.mockResolvedValue({
    settled: false,
    message: 'Review the original operation.',
  });
  fireEvent.click(await screen.findByRole('button', { name: 'Writing' }));
  fireEvent.click(screen.getByRole('button', { name: 'Inspect integration' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Add skill' }));
  fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
  expect(await screen.findByText('Outcome unconfirmed.')).toBeVisible();
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Add skill' })).toBeDisabled(),
  );
  expect(
    screen.getByRole('button', { name: 'Check original skill operation' }),
  ).toBeVisible();
  expect(controller.installSkillHub).toHaveBeenCalledTimes(1);
});

it('offers three purposes on first entry and remembers category while deep links win', async () => {
  const first = fixture('/settings/integrations');
  expect(
    screen.getByText('Connect services and give Row-Bot tools.'),
  ).toBeVisible();
  expect(first.integrations).not.toHaveBeenCalled();
  expect(first.searchIntegrations).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Skills' }));
  await waitFor(() =>
    expect(first.searchIntegrations).toHaveBeenCalledWith(
      { query: '', kind: 'skill', refresh: false },
      expect.any(AbortSignal),
    ),
  );
  first.unmount();
  const second = fixture('/settings/integrations');
  await waitFor(() =>
    expect(second.integrations).toHaveBeenCalledWith(
      { query: '', kind: 'skill', cursor: undefined },
      expect.any(AbortSignal),
    ),
  );
  second.unmount();
  const third = fixture('/settings/integrations?type=plugin&tab=discover');
  await waitFor(() =>
    expect(third.searchIntegrations).toHaveBeenCalledWith(
      { query: '', kind: 'plugin', refresh: false },
      expect.any(AbortSignal),
    ),
  );
});

it('makes one explicit combined Search, aborts previous work, and navigates passively', async () => {
  const c = fixture(
    '/settings/integrations?tab=discover&type=skill&source=clawhub',
  );
  await screen.findByRole('button', { name: 'Writing' });
  const initialSignal = c.searchIntegrations.mock.calls[0][1] as AbortSignal;
  c.searchIntegrations.mockClear();
  fireEvent.change(
    screen.getByRole('textbox', { name: 'Search integrations' }),
    { target: { value: 'notes' } },
  );
  expect(c.searchIntegrations).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  await waitFor(() =>
    expect(c.searchIntegrations).toHaveBeenCalledExactlyOnceWith(
      { query: 'notes', kind: 'skill', refresh: true },
      expect.any(AbortSignal),
    ),
  );
  expect(initialSignal.aborted).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Writing' }));
  expect(
    screen.queryByRole('textbox', { name: 'Search integrations' }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Browser back' }));
  expect(await screen.findByRole('button', { name: 'Writing' })).toHaveFocus();
  fireEvent.click(screen.getByRole('button', { name: 'Browser forward' }));
  expect(await screen.findByRole('heading', { name: 'Writing' })).toHaveFocus();
  expect(c.searchIntegrations).toHaveBeenCalledTimes(1);
});

it('rejects late search responses even when a transport ignores abort', async () => {
  const c = fixture('/settings/integrations?tab=discover&type=skill');
  await screen.findByRole('button', { name: 'Writing' });
  let finish!: (value: IntegrationPage) => void;
  c.searchIntegrations.mockReturnValueOnce(
    new Promise<IntegrationPage>((resolve) => {
      finish = resolve;
    }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  const signal = c.searchIntegrations.mock.calls.at(-1)![1] as AbortSignal;
  fireEvent.click(screen.getByRole('button', { name: 'Plugins' }));
  await waitFor(() =>
    expect(c.searchIntegrations).toHaveBeenLastCalledWith(
      { query: '', kind: 'plugin', refresh: false },
      expect.any(AbortSignal),
    ),
  );
  expect(signal.aborted).toBe(true);
  await act(async () =>
    finish({ ...page, items: [{ ...item, kind: 'plugin', name: 'Obsolete' }] }),
  );
  expect(
    screen.queryByRole('button', { name: 'Obsolete' }),
  ).not.toBeInTheDocument();
});

it('shows healthy results with one compact failure notice and explicit retry', async () => {
  const c = fixture('/settings/integrations?tab=discover&type=skill', {
    ...page,
    sources: [
      {
        source: 'github',
        status: 'timeout',
        message: 'Deadline reached',
        fetched_at: null,
        eligibility: 'eligible',
      },
      {
        source: 'clawhub',
        status: 'cached',
        message: '',
        fetched_at: 1,
        eligibility: 'eligible',
      },
    ],
  });
  expect(await screen.findByRole('button', { name: 'Writing' })).toBeVisible();
  fireEvent.click(
    screen.getByText('Some catalogs unavailable. Available results are shown.'),
  );
  expect(screen.getByText('GitHub: Deadline reached')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Retry search' }));
  await waitFor(() =>
    expect(c.searchIntegrations).toHaveBeenLastCalledWith(
      { query: '', kind: 'skill', refresh: true },
      expect.any(AbortSignal),
    ),
  );
});

it('retains pagination, query and incompatible filter without a public refetch on Back', async () => {
  const cursor = 'a'.repeat(64) + ':1';
  const c = fixture(
    '/settings/integrations?tab=discover&type=skill&q=writing&unsupported=1',
    { ...page, next_cursor: cursor, total: 2 },
  );
  await screen.findByRole('button', { name: 'Writing' });
  c.searchIntegrations.mockResolvedValue({
    ...page,
    items: [{ ...item, id: 'second', name: 'Second' }],
    total: 2,
  });
  fireEvent.click(screen.getByRole('button', { name: 'Show more' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Second' }));
  fireEvent.click(screen.getByRole('button', { name: 'Back to integrations' }));
  expect(screen.getByRole('button', { name: 'Writing' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Second' })).toHaveFocus();
  expect(
    screen.getByRole('textbox', { name: 'Search integrations' }),
  ).toHaveValue('writing');
  expect(
    screen.getByRole('switch', { name: 'Include unsupported entries' }),
  ).toBeChecked();
  expect(c.searchIntegrations).toHaveBeenLastCalledWith(
    {
      query: 'writing',
      kind: 'skill',
      cursor,
      include_incompatible: true,
      refresh: false,
    },
    expect.any(AbortSignal),
  );
  expect(c.searchIntegrations).toHaveBeenCalledTimes(2);
});

it.each([
  [{ state: 'done' }, 'Updated.'],
  [
    { state: 'failed', error: 'source_unavailable' },
    "This catalog couldn't be updated.",
  ],
])('updates one catalog only when asked: %o', async (catalog, message) => {
  const sources = [
    {
      source: 'clawhub',
      status: 'cached' as const,
      message: 'Public v1',
      fetched_at: 1,
      eligibility: 'eligible' as const,
      access: 'public' as const,
    },
  ];
  const c = fixture(
    '/settings/integrations?type=skill&tab=discover&view=catalogs',
    { ...page, sources },
  );
  const updated = {
    ...sourceList.items[2],
    catalog: {
      updated_at: 2,
      checked_at: 2,
      error: '',
      entries: 5,
      ...catalog,
    },
  };
  c.updateIntegrationSource.mockResolvedValue(updated);
  const button = await screen.findByRole('button', { name: 'Update ClawHub' });
  expect(c.updateIntegrationSource).not.toHaveBeenCalled();
  fireEvent.click(button);
  expect(await screen.findByText(message, { exact: false })).toBeVisible();
  expect(c.updateIntegrationSource).toHaveBeenCalledWith('clawhub');
});

it('persists source disable choices and leaves ineligible sources unavailable', async () => {
  const sources = [
    {
      source: 'clawhub',
      status: 'cached' as const,
      message: 'Public v1',
      fetched_at: 1,
      eligibility: 'eligible' as const,
      access: 'public' as const,
    },
    {
      source: 'skills_sh',
      status: 'unavailable' as const,
      message: 'Desktop access not implemented',
      fetched_at: null,
      eligibility: 'auth_required' as const,
    },
  ];
  const c = fixture(
    '/settings/integrations?type=skill&tab=discover&view=catalogs',
    { ...page, sources },
  );
  const toggle = await screen.findByRole('switch', { name: 'ClawHub' });
  expect(screen.getByRole('switch', { name: 'skills.sh' })).toBeDisabled();
  fireEvent.click(toggle);
  fireEvent.click(screen.getByRole('button', { name: 'Back to integrations' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Search' }));
  await waitFor(() =>
    expect(c.searchIntegrations).toHaveBeenLastCalledWith(
      expect.objectContaining({
        kind: 'skill',
        refresh: true,
        sources: ['github', 'skills_sh', 'browse_sh', 'lobehub'],
      }),
      expect.any(AbortSignal),
    ),
  );
  c.unmount();
  const next = fixture('/settings/integrations?type=skill&tab=discover');
  await waitFor(() =>
    expect(next.searchIntegrations).toHaveBeenLastCalledWith(
      expect.objectContaining({
        sources: ['github', 'skills_sh', 'browse_sh', 'lobehub'],
        refresh: false,
      }),
      expect.any(AbortSignal),
    ),
  );
});

it.each([
  ['skill', 'Skill link'],
  ['mcp', 'MCP endpoint'],
  ['plugin', 'Portable or native package'],
])(
  'defaults secondary import to %s and retains custom editors',
  async (kind, label) => {
    fixture(`/settings/integrations?type=${kind}`);
    fireEvent.click(
      screen.getByRole('button', { name: 'Add from link or file' }),
    );
    expect(
      screen.getByRole('combobox', { name: 'Import type' }),
    ).toHaveDisplayValue(label);
  },
);

it('keeps installed filtering local and distinguishes built-in skills and parent-owned children', async () => {
  const c = fixture();
  c.integrations.mockResolvedValue({
    ...page,
    items: [
      { ...item, source: 'bundled' },
      {
        ...item,
        id: 'parent',
        kind: 'plugin',
        name: 'Writer pack',
        children: [{ ...item, id: 'child', parent_id: 'parent' }],
      },
    ],
  });
  fireEvent.click(screen.getByRole('button', { name: 'Filter' }));
  expect(await screen.findByText(/Built-in skill/)).toBeVisible();
  expect(
    screen.getByText(
      'Writing · Included with Writer pack; managed by this plugin',
    ),
  ).toBeVisible();
  expect(c.searchIntegrations).not.toHaveBeenCalled();
});

it('distinguishes an unavailable search from an empty successful response', async () => {
  const c = fixture('/settings/integrations?type=skill&tab=discover', {
    ...page,
    items: [],
  });
  expect(await screen.findByText('No matching results')).toBeVisible();
  c.searchIntegrations.mockRejectedValue(new Error('Offline'));
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  expect(await screen.findByText('Catalog results unavailable')).toBeVisible();
  cleanup();
});

it('returns to the remembered category after a later choice without rewriting history', async () => {
  localStorage.setItem(
    'row-bot.integrations.preferences.v1',
    JSON.stringify({ category: 'skill', disabled: [] }),
  );
  fixture('/settings/integrations');
  await screen.findByRole('button', { name: 'Writing' });
  fireEvent.click(screen.getByRole('button', { name: 'Plugins' }));
  expect(screen.getByRole('button', { name: 'Search' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Browser back' }));
  expect(screen.getByRole('button', { name: 'Skills' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  expect(screen.getByRole('button', { name: 'Installed' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
});

it('hands a secondary skill link to the existing complete-bundle preview owner', async () => {
  const c = fixture('/settings/integrations?type=skill');
  c.searchSkillHub.mockResolvedValue({
    revision: 'saved',
    entries: [{ id: 'clawhub:writing' }],
  });
  c.previewSkillHub.mockResolvedValue({
    skill_name: 'writing',
    entry: { author: 'Fixture', source: 'fixture', url: '' },
    primary_text: 'Complete reviewed instructions',
    files: ['SKILL.md', 'references/check.txt'],
    scan: { blocked: false, findings: [] },
  });
  fireEvent.click(
    screen.getByRole('button', { name: 'Add from link or file' }),
  );
  fireEvent.change(screen.getByRole('textbox', { name: 'Source link' }), {
    target: { value: 'https://example.test/writing' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Inspect source' }));
  expect(
    await screen.findByText('Complete reviewed instructions'),
  ).toBeVisible();
  expect(c.searchSkillHub).toHaveBeenCalledExactlyOnceWith({
    query: 'https://example.test/writing',
    source: 'all',
    refresh: true,
  });
  expect(c.previewSkillHub).toHaveBeenCalledExactlyOnceWith({
    revision: 'saved',
    entry_id: 'clawhub:writing',
  });
  expect(c.installSkillHub).not.toHaveBeenCalled();
});

it('reviews a secondary custom MCP endpoint without connecting or asserting account readiness', async () => {
  const c = fixture('/settings/integrations?type=mcp');
  fireEvent.click(
    screen.getByRole('button', { name: 'Add from link or file' }),
  );
  fireEvent.change(screen.getByRole('textbox', { name: 'Source link' }), {
    target: { value: 'https://example.test/mcp' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Inspect source' }));
  expect(
    await screen.findByRole('textbox', { name: 'Connection name' }),
  ).toHaveValue('example.test');
  expect(screen.getByRole('button', { name: 'Connect' })).toBeVisible();
  expect(c.previewIntegration).not.toHaveBeenCalled();
  expect(c.searchIntegrations).not.toHaveBeenCalled();
});

it('opens local skill authoring from its secondary import action', async () => {
  fixture('/settings/integrations?type=skill');
  fireEvent.click(
    screen.getByRole('button', { name: 'Add from link or file' }),
  );
  fireEvent.click(
    screen.getByRole('button', {
      name: 'Import a skill file or create a skill',
    }),
  );
  expect(screen.getByText('Advanced editor')).toBeVisible();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

it('aborts pending discovery when leaving the page', async () => {
  const c = fixture('/settings/integrations?type=skill&tab=discover');
  await screen.findByRole('button', { name: 'Writing' });
  c.searchIntegrations.mockReturnValue(new Promise(() => {}));
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  const signal = c.searchIntegrations.mock.calls.at(-1)![1] as AbortSignal;
  c.unmount();
  expect(signal.aborted).toBe(true);
});

it('keeps unknown compatibility inspectable without claiming runtime readiness', async () => {
  fixture('/settings/integrations?type=mcp&tab=discover', {
    ...page,
    items: [
      {
        ...item,
        kind: 'mcp',
        installed: false,
        compatibility: 'not_inspected',
        auth_requirement: 'unknown',
        source: 'official',
        publisher: 'Example',
      },
    ],
  });
  fireEvent.click(await screen.findByRole('button', { name: 'Writing' }));
  expect(screen.getByText('Compatibility: Not yet verified')).toBeVisible();
  expect(
    screen.getByText('Setup and account requirements not yet verified'),
  ).toBeVisible();
  expect(
    screen.getByRole('button', { name: 'Inspect integration' }),
  ).toBeEnabled();
  fireEvent.click(screen.getByText('Source and provenance'));
  expect(screen.getByText('Official MCP Registry · Example')).toBeVisible();
});

it('preserves the owner status for retained data after a package is uninstalled', async () => {
  const c = fixture('/settings/integrations?type=plugin');
  const retained = {
    ...item,
    id: 'plugin:saved',
    kind: 'plugin' as const,
    installed: false,
    status: 'retained' as const,
    source: 'portable',
    name: 'Saved package',
  };
  c.integrations.mockResolvedValue({ ...page, items: [retained] });
  c.integration.mockResolvedValue(retained);
  fireEvent.click(screen.getByRole('button', { name: 'Filter' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Saved package' }));
  expect(
    await screen.findByText('Saved data retained · portable'),
  ).toBeVisible();
  expect(
    screen.queryByText('Available to inspect · portable'),
  ).not.toBeInTheDocument();
});

function deferredPreview() {
  let resolve!: (value: IntegrationPreview) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<IntegrationPreview>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
const inspectedSkill = (name: string): IntegrationPreview => ({
  kind: 'skill',
  skill: {
    schema_version: 1,
    preview_id: 'b'.repeat(32),
    content_hash: 'c'.repeat(64),
    entry: {
      id: `clawhub:${name}`,
      name,
      description: 'Synthetic instructions',
      source: 'clawhub',
      author: 'Fixture',
      trust_level: 'untrusted',
      tags: [],
      installed: false,
    },
    skill_name: name,
    primary_text: `Reviewed ${name}`,
    files: ['SKILL.md'],
    scan: { blocked: false, findings: [], token_estimate: 5 },
  },
});

it.each([
  'Back to integrations',
  'Browser back',
  'Go to plugins',
  'Go to second skill',
])(
  'suppresses ignored-abort inspection results after %s',
  async (destination) => {
    const c = fixture('/settings/integrations?type=skill&tab=discover', {
      ...page,
      items: [
        { ...item, installed: false },
        { ...item, id: 'skill:second', name: 'Second', installed: false },
      ],
    });
    const pending = deferredPreview();
    c.previewIntegration.mockReturnValue(pending.promise);
    fireEvent.click(await screen.findByRole('button', { name: 'Writing' }));
    fireEvent.click(
      screen.getByRole('button', { name: 'Inspect integration' }),
    );
    const signal = c.previewIntegration.mock.calls[0][1] as AbortSignal;
    fireEvent.click(screen.getByRole('button', { name: destination }));
    await act(async () => pending.resolve(inspectedSkill('obsolete')));
    expect(screen.queryByText('Reviewed obsolete')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Add skill' }),
    ).not.toBeInTheDocument();
    expect(signal.aborted).toBe(true);
    if (
      destination === 'Back to integrations' ||
      destination === 'Browser back'
    ) {
      expect(screen.getByRole('button', { name: 'Writing' })).toHaveFocus();
      expect(
        screen.getByRole('textbox', { name: 'Search integrations' }),
      ).toBeVisible();
    } else if (destination === 'Go to second skill') {
      expect(screen.getByRole('heading', { name: 'Second' })).toHaveFocus();
      expect(
        screen.getByRole('button', { name: 'Inspect integration' }),
      ).toBeEnabled();
    } else
      expect(screen.getByRole('button', { name: 'Plugins' })).toHaveAttribute(
        'aria-pressed',
        'true',
      );
  },
);

it('keeps a newer successful inspection when the cancelled request rejects late', async () => {
  const c = fixture('/settings/integrations?type=skill&tab=discover', {
    ...page,
    items: [
      { ...item, installed: false },
      { ...item, id: 'skill:second', name: 'Second', installed: false },
    ],
  });
  const first = deferredPreview();
  const second = deferredPreview();
  c.previewIntegration
    .mockReturnValueOnce(first.promise)
    .mockReturnValueOnce(second.promise);
  fireEvent.click(await screen.findByRole('button', { name: 'Writing' }));
  fireEvent.click(screen.getByRole('button', { name: 'Inspect integration' }));
  fireEvent.click(screen.getByRole('button', { name: 'Go to second skill' }));
  expect(
    screen.getByRole('button', { name: 'Inspect integration' }),
  ).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: 'Inspect integration' }));
  await act(async () => second.resolve(inspectedSkill('second')));
  expect(screen.getByText('Reviewed second')).toBeVisible();
  await act(async () =>
    first.reject(new DOMException('Inspection cancelled', 'AbortError')),
  );
  expect(screen.getByText('Reviewed second')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Add skill' })).toBeEnabled();
  expect(screen.queryByRole('status', { name: '' })).not.toBeInTheDocument();
});

it('retained child setup blocks parent configuration/removal after remount and keeps child recovery reachable', async () => {
  const child = {
    ...item,
    id: 'plugin:fixture:mcp:child',
    kind: 'mcp' as const,
    name: 'Child',
    parent_id: 'plugin:fixture',
    owner_ref: 'b'.repeat(64),
    target: {
      kind: 'plugin' as const,
      plugin_id: 'fixture',
      server_key: 'child',
    },
  };
  const plugin = {
    ...item,
    id: 'plugin:fixture',
    kind: 'plugin' as const,
    owner_ref: 'fixture',
    name: 'Package',
    children: [child],
    actions: ['remove'],
  };
  const id = crypto.randomUUID();
  retainCommand('integration-mcp:' + child.id, id);
  const io = fixture(
    '/settings/integrations?type=plugin&tab=my&selected=plugin%3Afixture',
    undefined,
    plugin,
  );
  expect(
    await screen.findByRole('button', { name: 'Remove package' }),
  ).toBeDisabled();
  expect(
    screen.getByRole('button', { name: 'Advanced package configuration' }),
  ).toBeDisabled();
  expect(
    await screen.findByRole('button', { name: 'Turn on package' }),
  ).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Set up Child' }));
  expect(
    await screen.findByRole('button', {
      name: 'Check original setup operation',
    }),
  ).toBeEnabled();
  expect(io.reviewPluginLifecycle).not.toHaveBeenCalled();
  expect(io.executePluginLifecycle).not.toHaveBeenCalled();
});
