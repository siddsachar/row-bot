import {
  act,
  render,
  screen,
  fireEvent,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { RuntimeContext } from '../../runtime';
import type { ClientController } from '../../api/controller';
import type { ClientPlatform } from '../../platform';
import type { IntegrationItem, IntegrationSetup } from '../../api/types';
import { readRetainedCommand, retainCommand } from '../../api/retained-command';
import McpSetup from './McpSetup';
import ProfileContext from './ProfileContext';
import { PluginSetup, SkillReview, SkillSetup } from './OwnerSetup';

const setup: IntegrationSetup = {
  auth_mode: 'none',
  execution: 'local',
  destination: 'Local process',
  bindings: [],
  credential_configured: false,
  catalog_accepted: false,
  requirements: [],
  runtime_status: 'not_connected',
  package_prepared: false,
  package_required: false,
  account_requirements: 'None declared',
  cost: 'Not supplied',
  evidence: 'Fixture only',
};
const item: IntegrationItem = {
  id: 'mcp:fixture',
  kind: 'mcp',
  owner_ref: 'a'.repeat(64),
  name: 'Fixture',
  description: '',
  parent_id: null,
  source: 'custom',
  publisher: '',
  source_url: '',
  version: '',
  pin: '',
  license: '',
  compatibility: 'supported',
  reasons: [],
  platforms: [],
  evidence: '',
  installed: true,
  enabled: false,
  status: 'setup',
  revision: 'a'.repeat(64),
  actions: [],
  auth_status: 'none',
  account_label: '',
  children: [],
  target: { kind: 'standalone' },
  setup,
};
function fixture(
  patch: Partial<IntegrationSetup> = {},
  pending = '',
  row = item,
) {
  if (pending) retainCommand('integration-mcp:' + item.id, pending);
  const controller = {
    mcpConfiguration: vi.fn().mockResolvedValue({
      revision: item.revision,
      availability: 'available',
      items: [
        {
          server_id: item.owner_ref,
          transport: patch.execution === 'hosted' ? 'streamable_http' : 'stdio',
          requirements: patch.requirements ?? [],
        },
      ],
    }),
    reviewMcpAuth: vi.fn().mockResolvedValue({
      nonce: 'review',
      disclosures: ['Connection-scoped secret'],
    }),
    executeMcpAuth: vi
      .fn()
      .mockResolvedValue({ state: 'signed_in', message: 'Secret saved' }),
    mcpAuthStatus: vi
      .fn()
      .mockResolvedValue({ state: 'signed_in', message: 'Secret saved' }),
    reconcileIntegrationOperation: vi
      .fn()
      .mockResolvedValue({ message: 'Original operation checked' }),
    receipt: vi.fn().mockResolvedValue({ status: 'completed' }),
    mcpPolicy: vi.fn().mockResolvedValue({
      schema_version: 1,
      revision: item.revision,
      server_id: item.owner_ref,
      availability: 'available',
      global_enabled: false,
      server_enabled: false,
      items: [],
      total: 0,
    }),
    reviewMcpPolicy: vi
      .fn()
      .mockImplementation(async (payload) => ({ ...payload, nonce: 'review' })),
    mcpTestedCatalog: vi.fn().mockImplementation(async (query) => ({
      schema_version: 1,
      ...query,
      configuration_revision: item.revision,
      availability: 'available',
      total: 1,
      next_cursor: null,
      manual_selection_required: false,
      items: [
        {
          tool_id: 'b'.repeat(64),
          name: 'get_record',
          effect: 'read_only',
          enabled_after_accept: true,
          requires_approval: false,
          destructive: false,
        },
      ],
    })),
    executeMcpConfiguration: vi
      .fn()
      .mockRejectedValue(new Error('Response lost')),
    mcpRuntime: vi.fn(),
  };
  const changed = vi.fn();
  const view = render(
    <RuntimeContext.Provider
      value={{
        controller: controller as unknown as ClientController,
        platform: {} as ClientPlatform,
      }}
    >
      <McpSetup
        item={{ ...row, setup: { ...setup, ...patch } }}
        onChanged={changed}
      />
    </RuntimeContext.Provider>,
  );
  return { ...controller, changed, ...view };
}
afterEach(() => {
  sessionStorage.clear();
  localStorage.clear();
});
it('no-auth skips all account fields and never starts a connection passively', async () => {
  const io = fixture();
  await screen.findByText(
    'No account fields are required for this connection.',
  );
  expect(screen.queryByLabelText('Account label')).toBeNull();
  expect(screen.queryByRole('button', { name: 'Sign in' })).toBeNull();
  expect(io.reviewMcpAuth).not.toHaveBeenCalled();
  expect(
    await screen.findByRole('button', { name: 'Test connection' }),
  ).toBeEnabled();
});
it('OAuth appears only for a declared supported flow and testing waits for sign-in', async () => {
  fixture({ auth_mode: 'oauth', execution: 'hosted' });
  expect(await screen.findByLabelText('Account label')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Sign in' })).toBeVisible();
  expect(
    screen.getByRole('button', { name: 'Test connection' }),
  ).toBeDisabled();
  expect(
    screen.queryByRole('button', { name: 'Review secret setup' }),
  ).toBeNull();
});
it('API key uses only declared protected bindings and preserves Work context', async () => {
  const io = fixture({
    auth_mode: 'api_key',
    execution: 'hosted',
    bindings: [
      { kind: 'header', name: 'X-Service-Key', key: 'service', prefix: '' },
    ],
  });
  fireEvent.change(await screen.findByLabelText('Account label'), {
    target: { value: 'Work' },
  });
  fireEvent.change(screen.getByLabelText('X-Service-Key (header)'), {
    target: { value: 'fixture-key' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Review secret setup' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Confirm' }));
  await waitFor(() =>
    expect(io.executeMcpAuth).toHaveBeenCalledWith(
      expect.objectContaining({
        label: 'Work',
        values: { service: 'fixture-key' },
        bindings: [
          { kind: 'header', name: 'X-Service-Key', key: 'service', prefix: '' },
        ],
        target: { kind: 'standalone' },
      }),
    ),
  );
  expect(screen.getByLabelText('X-Service-Key (header)')).toHaveValue('');
});
it.each(['unknown', 'unsupported'] as const)(
  '%s auth is never presented as no-auth or invented OAuth',
  async (auth_mode) => {
    fixture({ auth_mode, execution: 'hosted' });
    expect(
      await screen.findByText(
        /Review publisher instructions in advanced configuration/,
      ),
    ).toBeVisible();
    expect(screen.queryByLabelText('Account label')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Sign in' })).toBeNull();
    if (auth_mode === 'unsupported')
      expect(
        screen.getByRole('button', { name: 'Test connection' }),
      ).toBeDisabled();
  },
);
it('missing local runtime blocks test and discloses a manual next step', async () => {
  fixture({
    requirements: [
      {
        id: 'other',
        label: 'Fixture runtime',
        available: false,
        managed: false,
        installable: false,
        source: 'missing',
      },
    ],
  });
  expect(await screen.findByText(/Fixture runtime: Unavailable/)).toBeVisible();
  expect(
    screen.getByRole('button', { name: 'Test connection' }),
  ).toBeDisabled();
});
it('reload checks the original uncertain operation instead of publishing another', async () => {
  const id = crypto.randomUUID();
  const io = fixture({}, id);
  fireEvent.click(
    await screen.findByRole('button', {
      name: 'Check original setup operation',
    }),
  );
  await waitFor(() => expect(io.receipt).toHaveBeenCalledWith(id));
  expect(io.executeMcpAuth).not.toHaveBeenCalled();
  expect(io.reconcileIntegrationOperation).toHaveBeenCalledWith('mcp', id);
});
it('package checklist names partial availability and opens the required child directly', async () => {
  const plugin = {
    ...item,
    id: 'plugin:fixture',
    kind: 'plugin' as const,
    children: [
      {
        ...item,
        name: 'Required service',
        parent_id: 'plugin:fixture',
        required: true,
      },
      {
        ...item,
        id: 'skill:optional',
        kind: 'skill' as const,
        name: 'Optional instructions',
        required: false,
        status: 'ready' as const,
      },
    ],
  };
  const controller = {
    plugin: vi.fn().mockResolvedValue({
      settings: [],
      secrets: [],
      health: { status: 'passed' },
      permissions: [],
      capabilities: {},
      enabled: true,
    }),
    mcpConfiguration: vi.fn().mockResolvedValue({
      revision: item.revision,
      availability: 'available',
      items: [],
    }),
  };
  render(
    <RuntimeContext.Provider
      value={{
        controller: controller as unknown as ClientController,
        platform: {} as ClientPlatform,
      }}
    >
      <PluginSetup item={plugin} onChanged={vi.fn()} onAdvanced={vi.fn()} />
    </RuntimeContext.Provider>,
  );
  fireEvent.click(
    await screen.findByRole('button', {
      name: 'Continue setup: Required service',
    }),
  );
  expect(
    await screen.findByRole('region', { name: 'Set up Required service' }),
  ).toBeVisible();
  expect(
    screen.getByText(/Optional instructions · Optional · Usable/),
  ).toBeVisible();
  expect(screen.queryByRole('search', { name: 'Search plugins' })).toBeNull();
});
it('skill review shows full instruction and script contents before add without MCP controls', () => {
  render(
    <SkillReview
      preview={{
        schema_version: 1,
        preview_id: 'a'.repeat(32),
        content_hash: 'a'.repeat(64),
        entry: {
          id: 'x',
          name: 'x',
          author: 'Fixture publisher',
          source: 'github',
          url: '',
          description: '',
          trust_level: 'community',
          tags: [],
          installed: false,
        },
        skill_name: 'x',
        primary_text: 'short',
        files: ['SKILL.md', 'scripts/check.py'],
        scan: { blocked: false, findings: [], token_estimate: 1 },
        review_files: [
          {
            path: 'SKILL.md',
            text: 'Full instructions end',
            size_bytes: 21,
            sha256: 'a'.repeat(64),
            executable: false,
          },
          {
            path: 'scripts/check.py',
            text: 'full script content',
            size_bytes: 19,
            sha256: 'b'.repeat(64),
            executable: true,
          },
        ],
      }}
    />,
  );
  expect(screen.getByText('Full instructions end')).toBeVisible();
  fireEvent.click(screen.getByText('scripts/check.py · Executable script'));
  expect(screen.getByText('full script content')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Test connection' })).toBeNull();
});

it('refreshes aggregate readiness when a retained sign-in completes', async () => {
  const id = crypto.randomUUID();
  retainCommand('integration-mcp:' + item.id + ':auth', id);
  const io = fixture({ auth_mode: 'oauth', execution: 'hosted' });
  await waitFor(() => expect(io.changed).toHaveBeenCalled());
  expect(io.mcpAuthStatus).toHaveBeenCalledWith(id);
  expect(io.executeMcpAuth).not.toHaveBeenCalled();
});

it('shows owner-derived current profile and model restrictions without changing them', async () => {
  const workspace = {
    conversation_id: 'chat',
    controls: { profile_id: 'reader', runtime_mode: 'chat_only' },
    profiles: [{ id: 'reader', label: 'Reader' }],
    model_status: { state: 'unavailable', reason: 'Choose a model' },
  };
  const controller = {
    subscribe: () => () => undefined,
    getSnapshot: () => ({ selectedConversationId: 'chat', workspace }),
    profile: vi.fn().mockResolvedValue({
      profile: {
        display_name: 'Reader',
        enabled: true,
        capability: 'read_only',
        allow_tools: ['read_file'],
        skills: ['writing'],
        approval_mode: 'approve',
      },
    }),
  };
  render(
    <RuntimeContext.Provider
      value={{
        controller: controller as unknown as ClientController,
        platform: {} as ClientPlatform,
      }}
    >
      <ProfileContext />
    </RuntimeContext.Provider>,
  );
  expect(
    await screen.findByText(/This profile permits read-only work/),
  ).toBeVisible();
  expect(
    screen.getByText(/Tools are unavailable in Chat only mode/),
  ).toBeVisible();
  expect(screen.getByText(/Current model is unavailable/)).toBeVisible();
  fireEvent.click(screen.getByText('Current profile restrictions'));
  expect(screen.getByText('Tool scope: read_file.')).toBeVisible();
  expect(controller.profile).toHaveBeenCalledWith(
    'reader',
    expect.any(AbortSignal),
  );
});

it('does not borrow a stale conversation profile when no matching chat is selected', () => {
  const controller = {
    subscribe: () => () => undefined,
    getSnapshot: () => ({
      selectedConversationId: null,
      workspace: { conversation_id: 'old-chat' },
    }),
    profile: vi.fn(),
  };
  render(
    <RuntimeContext.Provider
      value={{
        controller: controller as unknown as ClientController,
        platform: {} as ClientPlatform,
      }}
    >
      <ProfileContext />
    </RuntimeContext.Provider>,
  );
  expect(screen.getByText(/No chat is selected/)).toBeVisible();
  expect(controller.profile).not.toHaveBeenCalled();
});

it('retains an uncertain enable command and checks it before connecting or republishing', async () => {
  const io = fixture({ catalog_accepted: true });
  fireEvent.click(
    await screen.findByRole('button', { name: 'Turn on and connect' }),
  );
  fireEvent.click(await screen.findByRole('button', { name: 'Confirm' }));
  await screen.findByRole('button', { name: 'Check original setup operation' });
  expect(io.executeMcpConfiguration).toHaveBeenCalledTimes(1);
  const command = io.executeMcpConfiguration.mock.calls[0][0];
  expect(command.type).toBe('mcp.configuration.control');
  expect(io.mcpRuntime).not.toHaveBeenCalled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Check original setup operation' }),
  );
  await waitFor(() =>
    expect(io.receipt).toHaveBeenCalledWith(command.command_id),
  );
  expect(io.executeMcpConfiguration).toHaveBeenCalledTimes(1);
  expect(io.mcpRuntime).not.toHaveBeenCalled();
});

it('remount blocks every MCP mutation until the original operation settles, without locking another connection', async () => {
  const id = crypto.randomUUID();
  const first = fixture(
    { catalog_accepted: true, credential_configured: true },
    id,
  );
  first.unmount();
  const io = fixture({ catalog_accepted: true, credential_configured: true });
  await waitFor(() => expect(io.mcpPolicy).toHaveBeenCalled());
  expect(
    await screen.findByRole('switch', { name: 'Server access' }),
  ).toBeDisabled();
  fireEvent.click(screen.getByText('Advanced connection actions'));
  expect(
    screen.getByRole('button', { name: 'Disconnect account' }),
  ).toBeDisabled();
  expect(
    screen.getByRole('button', { name: 'Remove connection' }),
  ).toBeDisabled();
  const other = fixture({}, '', { ...item, id: 'mcp:other', name: 'Other' });
  expect(
    await within(
      screen.getByRole('region', { name: 'Set up Other' }),
    ).findByRole('button', { name: 'Test connection' }),
  ).toBeEnabled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Check original setup operation' }),
  );
  await waitFor(() => expect(io.receipt).toHaveBeenCalledWith(id));
  await waitFor(() =>
    expect(screen.getByRole('switch', { name: 'Server access' })).toBeEnabled(),
  );
  expect(io.executeMcpConfiguration).not.toHaveBeenCalled();
  other.unmount();
});

it('retained uncertainty blocks catalog acceptance but permits passive catalog reads and original recovery', async () => {
  const id = crypto.randomUUID();
  retainCommand('integration-mcp:' + item.id + ':test', crypto.randomUUID());
  const io = fixture({}, id);
  expect(await screen.findByText('get_record')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Accept tools' })).toBeDisabled();
  expect(io.mcpTestedCatalog).toHaveBeenCalled();
  expect(readRetainedCommand('integration-mcp:' + item.id)).toBe(id);
  fireEvent.click(
    screen.getByRole('button', { name: 'Check original setup operation' }),
  );
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Accept tools' })).toBeEnabled(),
  );
  expect(io.executeMcpConfiguration).not.toHaveBeenCalled();
});

it('a policy request locks runtime and advanced mutations while pending and after a lost response', async () => {
  let release!: (value: unknown) => void;
  const io = fixture({ catalog_accepted: true, credential_configured: true });
  io.executeMcpConfiguration.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        release = resolve;
      }),
  );
  const control = await screen.findByRole('switch', { name: 'Server access' });
  await waitFor(() => expect(control).toBeEnabled());
  fireEvent.click(control);
  await waitFor(() =>
    expect(io.executeMcpConfiguration).toHaveBeenCalledTimes(1),
  );
  const id = io.executeMcpConfiguration.mock.calls[0][0].command_id;
  fireEvent.click(screen.getByText('Advanced connection actions'));
  const removeWasDisabled = screen
    .getByRole('button', { name: 'Remove connection' })
    .hasAttribute('disabled');
  await act(async () => release({ command_id: id, status: 'partial' }));
  expect(removeWasDisabled).toBe(true);
  expect(
    screen.getByRole('button', { name: 'Disconnect account' }),
  ).toBeDisabled();
  expect(readRetainedCommand('integration-mcp:' + item.id)).toBe(id);
  expect(io.mcpRuntime).not.toHaveBeenCalled();
});

function skillFixture(status: string) {
  const id = crypto.randomUUID();
  retainCommand('integration-skill:skill:fixture', id);
  const controller = {
    skill: vi.fn().mockResolvedValue({
      library_revision: item.revision,
      skill: {
        activation: {},
        instructions: 'Instructions',
        available: false,
        pinned: false,
      },
    }),
    receipt: vi.fn().mockResolvedValue({ command_id: id, status }),
    skillReceipt: vi.fn().mockResolvedValue({
      command_id: id,
      status: status === 'rejected' ? 'rejected' : 'partial',
    }),
    reviewSkill: vi.fn().mockResolvedValue({ review_id: 'review' }),
    executeSkill: vi.fn().mockResolvedValue({ status: 'completed' }),
  };
  render(
    <RuntimeContext.Provider
      value={{
        controller: controller as unknown as ClientController,
        platform: {} as ClientPlatform,
      }}
    >
      <SkillSetup
        item={{
          ...item,
          id: 'skill:fixture',
          kind: 'skill',
          owner_ref: 'fixture',
        }}
        onChanged={vi.fn()}
        onAdvanced={vi.fn()}
      />
    </RuntimeContext.Provider>,
  );
  return { ...controller, id };
}

it('canonical skill rejection refreshes owner state and permits a newly reviewed preference', async () => {
  const io = skillFixture('rejected');
  fireEvent.click(
    await screen.findByRole('button', { name: 'Check original skill change' }),
  );
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: 'Make available in chats' }),
    ).toBeEnabled(),
  );
  expect(screen.getByRole('status')).toHaveTextContent(/rejected/i);
  expect(readRetainedCommand('integration-skill:skill:fixture')).toBe('');
  expect(io.skill).toHaveBeenCalledTimes(2);
  io.skillReceipt.mockResolvedValue({ command_id: io.id, status: 'partial' });
  fireEvent.click(
    screen.getByRole('button', { name: 'Make available in chats' }),
  );
  await waitFor(() => expect(io.reviewSkill).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(io.executeSkill).toHaveBeenCalledTimes(1));
  expect(io.executeSkill.mock.calls[0][0].command_id).not.toBe(io.id);
});

it.each(['partial', 'accepted'])(
  'keeps %s skill outcomes locked without replay',
  async (status) => {
    const io = skillFixture(status);
    fireEvent.click(
      await screen.findByRole('button', {
        name: 'Check original skill change',
      }),
    );
    await waitFor(() => expect(io.skillReceipt).toHaveBeenCalledWith(io.id));
    expect(
      screen.getByRole('button', { name: 'Make available in chats' }),
    ).toBeDisabled();
    expect(readRetainedCommand('integration-skill:skill:fixture')).toBe(io.id);
    expect(io.executeSkill).not.toHaveBeenCalled();
  },
);

it('keeps an uncertain original available after remount when session storage is blocked', async () => {
  const blocked = vi
    .spyOn(Storage.prototype, 'setItem')
    .mockImplementation(() => {
      throw new Error('Blocked');
    });
  const first = fixture({ catalog_accepted: true });
  fireEvent.click(
    await screen.findByRole('button', { name: 'Turn on and connect' }),
  );
  fireEvent.click(await screen.findByRole('button', { name: 'Confirm' }));
  await screen.findByRole('button', { name: 'Check original setup operation' });
  const id = first.executeMcpConfiguration.mock.calls[0][0].command_id;
  first.unmount();
  const next = fixture({ catalog_accepted: true });
  const check = await screen.findByRole('button', {
    name: 'Check original setup operation',
  });
  expect(
    await screen.findByRole('switch', { name: 'Server access' }),
  ).toBeDisabled();
  fireEvent.click(check);
  await waitFor(() => expect(next.receipt).toHaveBeenCalledWith(id));
  await waitFor(() =>
    expect(
      screen.queryByRole('button', { name: 'Check original setup operation' }),
    ).toBeNull(),
  );
  blocked.mockRestore();
});

it('recovery of completed local skill removal never rereads or replays the deleted skill', async () => {
  const io = skillFixture('partial');
  io.skillReceipt.mockResolvedValue({
    command_id: io.id,
    status: 'completed',
    action: 'skill.delete',
  });
  await screen.findByText('Instructions');
  fireEvent.click(
    screen.getByRole('button', { name: 'Check original skill change' }),
  );
  await waitFor(() =>
    expect(readRetainedCommand('integration-skill:skill:fixture')).toBe(''),
  );
  expect(io.skill).toHaveBeenCalledTimes(1);
  expect(io.executeSkill).not.toHaveBeenCalled();
});
