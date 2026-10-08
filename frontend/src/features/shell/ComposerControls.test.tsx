import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import type { ConversationWorkspace, ModelChoice } from '../../api/types';
import ComposerControls from './ComposerControls';

const mock = vi.hoisted(() => ({
  state: {
    selectedConversationId: 'conversation-a',
    status: 'ready',
    workspace: null as ConversationWorkspace | null,
    handshake: { models: [] as ModelChoice[] },
  },
  version: 1,
  navigate: vi.fn(),
  drafts: new Map<string, { text: string; attachments: [] }>(),
  controller: {
    intent: vi.fn(),
    getSelectionVersion: vi.fn(),
    skills: vi.fn(),
    getDraft: vi.fn(),
    setDraft: vi.fn(),
  },
}));
vi.mock('../../runtime', () => ({
  useClientState: () => mock.state,
  useRuntime: () => ({ controller: mock.controller }),
}));
vi.mock('react-router-dom', () => ({ useNavigate: () => mock.navigate }));
beforeEach(() => {
  vi.resetAllMocks();
  mock.version = 1;
  mock.controller.getSelectionVersion.mockImplementation(() => mock.version);
  mock.controller.intent.mockResolvedValue({ status: 'completed' });
  mock.controller.skills.mockResolvedValue({
    schema_version: 1,
    revision: 'skills-a',
    availability: 'available',
    items: [],
    total: 0,
    next_cursor: null,
  });
  mock.drafts.clear();
  mock.controller.getDraft.mockImplementation(
    (id: string) => mock.drafts.get(id) ?? { text: '', attachments: [] as [] },
  );
  mock.controller.setDraft.mockImplementation((id, draft) =>
    mock.drafts.set(id, draft),
  );
  mock.state.status = 'ready';
  mock.state.selectedConversationId = 'conversation-a';
  mock.state.handshake.models = [
    {
      provider_id: 'fixture',
      model_ref: 'fixture::effort',
      label: 'Exact effort model',
      available: true,
    },
    {
      provider_id: 'other',
      model_ref: 'other::no-reasoning',
      label: 'Other provider model',
      available: true,
    },
    {
      provider_id: 'fixture',
      model_ref: 'fixture::unavailable',
      label: 'Unavailable model',
      available: false,
    },
  ];
  mock.state.workspace = {
    conversation_id: 'conversation-a',
    revision: '17',
    controls: {
      model_selection: { provider_id: 'fixture', model_ref: 'fixture::effort' },
      runtime_mode: 'agent',
      approval_mode: 'approve',
      profile_id: 'writer',
    },
    profiles: [{ id: 'writer', label: 'Writer' }],
    resources: [],
    actions: [],
    reasoning: {
      model_ref: 'fixture::effort',
      capability_revision: 'capability-a',
      available: true,
      selection: { kind: 'provider_default' },
      choices: [
        { label: 'Provider default', selection: { kind: 'provider_default' } },
        {
          label: 'Careful',
          selection: { kind: 'effort', effort: 'careful-exact-model' },
        },
      ],
      supports_budget: false,
      budget_min: 0,
      budget_max: 0,
      stale: false,
    },
  };
});

async function menu(name: string) {
  await act(async () =>
    fireEvent.keyDown(screen.getByRole('button', { name }), {
      key: 'Enter',
    }),
  );
  return within(screen.getByRole('menu'));
}
async function picker() {
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Model' })),
  );
  return within(screen.getByRole('dialog', { name: 'Choose a model' }));
}
async function submenu(parent: ReturnType<typeof within>, name: RegExp) {
  await act(async () =>
    fireEvent.keyDown(parent.getByRole('menuitem', { name }), {
      key: 'ArrowRight',
    }),
  );
  return within(screen.getAllByRole('menu').at(-1)!);
}

it('shows compact current values with only the exact server-supplied Thinking choices', async () => {
  render(<ComposerControls onError={vi.fn()} />);
  const controls = within(
    screen.getByRole('group', { name: 'Conversation controls' }),
  );
  expect(controls.getByRole('button', { name: 'Model' })).toHaveTextContent(
    'Exact effort model',
  );
  expect(controls.getByRole('button', { name: 'Model' })).toHaveAttribute(
    'aria-description',
    'Model: Exact effort model · Thinking: Provider default',
  );
  expect(controls.getByRole('button', { name: 'Approvals' })).toHaveAttribute(
    'aria-description',
    'Approvals: Ask',
  );
  expect(
    controls.getByRole('button', { name: 'Add files and more' }),
  ).toBeEnabled();
  expect(screen.queryByRole('combobox')).toBeNull();
  const models = await picker();
  const choices = within(models.getByRole('radiogroup', { name: 'Thinking' }));
  expect(choices.getByRole('radio', { name: 'Default' })).toHaveAttribute(
    'aria-checked',
    'true',
  );
  expect(choices.getByRole('radio', { name: 'Careful' })).toBeEnabled();
  expect(choices.queryByRole('radio', { name: 'High' })).toBeNull();
  expect(models.queryByLabelText('Thinking budget')).toBeNull();
});

it.each([
  ['Careful', { kind: 'effort', effort: 'careful-exact-model' }],
  ['Default', { kind: 'provider_default' }],
] as const)(
  'sends the exact %s reasoning choice and capability revision through conversation.controls',
  async (label, selection) => {
    const onError = vi.fn();
    render(<ComposerControls onError={onError} />);
    const models = await picker();
    await act(async () =>
      fireEvent.click(models.getByRole('radio', { name: label })),
    );
    expect(mock.controller.intent).toHaveBeenCalledExactlyOnceWith(
      'conversation-a',
      'conversation.controls',
      {
        ...mock.state.workspace!.controls,
        reasoning: {
          model_ref: 'fixture::effort',
          capability_revision: 'capability-a',
          selection,
        },
      },
      '17',
    );
    expect(onError).toHaveBeenCalledWith('');
  },
);

it.each(['unsupported', 'mismatched'] as const)(
  'hides active Thinking for an %s model',
  async (mode) => {
    if (mode === 'unsupported')
      mock.state.workspace!.reasoning!.available = false;
    else mock.state.workspace!.reasoning!.model_ref = 'other::stale-model';
    render(<ComposerControls onError={vi.fn()} />);
    const models = await picker();
    expect(models.queryByRole('radiogroup', { name: 'Thinking' })).toBeNull();
    expect(mock.controller.intent).not.toHaveBeenCalled();
  },
);

it('validates budget bounds and integer values before sending the exact numeric budget', async () => {
  Object.assign(mock.state.workspace!.reasoning!, {
    supports_budget: true,
    budget_min: 256,
    budget_max: 2048,
  });
  render(<ComposerControls onError={vi.fn()} />);
  const models = await picker();
  const input = models.getByRole('spinbutton', { name: 'Thinking budget' });
  expect(input).toHaveAttribute('placeholder', '256–2048 tokens');
  const useBudget = models.getByRole('button', { name: 'Use budget' });
  for (const invalid of ['', '255', '2049', '512.5']) {
    fireEvent.change(input, { target: { value: invalid } });
    expect(useBudget).toBeDisabled();
  }
  fireEvent.change(input, { target: { value: '1024' } });
  expect(useBudget).toBeEnabled();
  await act(async () => fireEvent.click(useBudget));
  expect(mock.controller.intent.mock.calls[0][2].reasoning).toEqual({
    model_ref: 'fixture::effort',
    capability_revision: 'capability-a',
    selection: { kind: 'budget', budget: 1024 },
  });
});

it('keeps stale capability feedback visible and sends its revision for server revalidation', async () => {
  mock.state.workspace!.reasoning!.stale = true;
  render(<ComposerControls onError={vi.fn()} />);
  const models = await picker();
  expect(models.getByRole('status')).toHaveTextContent('no longer available');
  await act(async () =>
    fireEvent.click(models.getByRole('radio', { name: 'Careful' })),
  );
  expect(
    mock.controller.intent.mock.calls[0][2].reasoning.capability_revision,
  ).toBe('capability-a');
});

it('changes model without forwarding the previous model reasoning setting', async () => {
  mock.state.workspace!.controls.reasoning = {
    model_ref: 'fixture::effort',
    capability_revision: 'capability-a',
    selection: { kind: 'effort', effort: 'careful-exact-model' },
  };
  render(<ComposerControls onError={vi.fn()} />);
  const models = await picker();
  const unavailable = models.getByRole('option', {
    name: /^Unavailable model/,
  });
  expect(unavailable).toHaveAttribute('aria-disabled', 'true');
  expect(
    within(unavailable).getByRole('button', { name: 'Connect' }),
  ).toBeVisible();
  await act(async () =>
    fireEvent.click(
      models.getByRole('option', { name: 'Other provider model' }),
    ),
  );
  expect(mock.controller.intent.mock.calls[0][2]).toEqual({
    model_selection: { provider_id: 'other', model_ref: 'other::no-reasoning' },
    runtime_mode: 'agent',
    profile_id: 'writer',
    approval_mode: 'approve',
  });
});

it('searches models by name and provider and chooses with the keyboard', async () => {
  render(<ComposerControls onError={vi.fn()} />);
  const models = await picker();
  const search = models.getByRole('combobox', { name: 'Search models' });
  expect(search).toHaveFocus();
  fireEvent.change(search, { target: { value: 'other' } });
  expect(models.getAllByRole('option')).toHaveLength(1);
  await act(async () => fireEvent.keyDown(search, { key: 'Enter' }));
  expect(mock.controller.intent.mock.calls[0][2].model_selection).toEqual({
    provider_id: 'other',
    model_ref: 'other::no-reasoning',
  });
  expect(screen.queryByRole('dialog', { name: 'Choose a model' })).toBeNull();
});

it('removes old Thinking content immediately after a model switch without sending an old capability', async () => {
  const view = render(<ComposerControls onError={vi.fn()} />);
  await picker();
  mock.state.workspace = {
    ...mock.state.workspace!,
    controls: {
      ...mock.state.workspace!.controls,
      model_selection: {
        provider_id: 'other',
        model_ref: 'other::no-reasoning',
      },
    },
  };
  view.rerender(<ComposerControls onError={vi.fn()} />);
  expect(screen.queryByRole('radiogroup', { name: 'Thinking' })).toBeNull();
  expect(mock.controller.intent).not.toHaveBeenCalled();
});

it('keeps approval, runtime and profile distinct in their compact menus', async () => {
  render(<ComposerControls onError={vi.fn()} />);
  const approvals = await menu('Approvals');
  // Each choice says what it does.
  expect(approvals.getByRole('menuitem', { name: /^Ask/ })).toHaveAttribute(
    'aria-current',
    'true',
  );
  expect(
    approvals.getByText('Asks before anything that makes changes'),
  ).toBeVisible();
  await act(async () =>
    fireEvent.click(approvals.getByRole('menuitem', { name: /^Block/ })),
  );
  expect(mock.controller.intent.mock.calls[0][2].approval_mode).toBe('block');
  const more = await menu('Add files and more');
  const profiles = await submenu(more, /^Agent profile/);
  expect(
    profiles.getByRole('menuitemradio', { name: 'Writer' }),
  ).toHaveAttribute('aria-checked', 'true');
  await act(async () =>
    fireEvent.keyDown(screen.getAllByRole('menu').at(-1)!, {
      key: 'ArrowLeft',
    }),
  );
  const modes = await submenu(within(screen.getAllByRole('menu')[0]), /^Mode/);
  expect(modes.getByRole('menuitemradio', { name: /^Agent/ })).toHaveAttribute(
    'aria-checked',
    'true',
  );
  await act(async () =>
    fireEvent.click(modes.getByRole('menuitemradio', { name: /^Chat only/ })),
  );
  expect(mock.controller.intent.mock.calls[1][2].runtime_mode).toBe(
    'chat_only',
  );
  expect(mock.controller.intent.mock.calls[1][2].profile_id).toBe('writer');
});

it('blocks running and disconnected controls without removing their current values', () => {
  const view = render(<ComposerControls disabled onError={vi.fn()} />);
  // Attaching and adding resources stay available while a reply runs.
  for (const button of screen.getAllByRole('button'))
    if (button.getAttribute('aria-label') !== 'Add files and more')
      expect(button).toBeDisabled();
  mock.state.status = 'disconnected';
  view.rerender(<ComposerControls onError={vi.fn()} />);
  for (const button of screen.getAllByRole('button'))
    expect(button).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Model' })).toHaveTextContent(
    'Exact effort model',
  );
  expect(mock.controller.intent).not.toHaveBeenCalled();
});

it('retains an error on rejected controls and ignores a late failure from another conversation', async () => {
  const onError = vi.fn();
  mock.controller.intent.mockRejectedValueOnce({ code: 'revision_conflict' });
  render(<ComposerControls onError={onError} />);
  const models = await picker();
  await act(async () =>
    fireEvent.click(models.getByRole('radio', { name: 'Careful' })),
  );
  expect(onError).toHaveBeenCalledTimes(1);
  expect(onError.mock.calls[0][0]).not.toBe('');
  expect(screen.getByRole('dialog', { name: 'Choose a model' })).toBeVisible();
  let fail!: (reason: unknown) => void;
  mock.controller.intent.mockImplementationOnce(
    () =>
      new Promise((_resolve, reject) => {
        fail = reject;
      }),
  );
  await act(async () =>
    fireEvent.click(models.getByRole('radio', { name: 'Careful' })),
  );
  mock.version++;
  await act(async () => fail({ code: 'not_found' }));
  expect(onError).toHaveBeenCalledTimes(1);
});

it('renders no stale controls while the next conversation is loading', () => {
  mock.state.selectedConversationId = 'conversation-b';
  render(<ComposerControls onError={vi.fn()} />);
  expect(screen.queryByRole('button')).toBeNull();
});

it.each([
  ['fixture::saved-model', 'saved-model'],
  ['fixture/legacy-model', 'fixture/legacy-model'],
] as const)(
  'keeps the saved model visible with an empty cache for %s',
  async (reference, displayed) => {
    mock.state.handshake.models = [];
    mock.state.workspace!.controls.model_selection = {
      provider_id: 'fixture',
      model_ref: reference,
    };
    render(<ComposerControls onError={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Model' })).toHaveTextContent(
      displayed,
    );
    const models = await picker();
    expect(models.queryAllByRole('option')).toHaveLength(0);
    // Nothing to choose yet: the picker points to Setup (decision 10).
    expect(
      models.getByText('No models yet. Choose how Row-Bot thinks in Setup.'),
    ).toBeVisible();
    expect(
      models.getByRole('button', { name: 'Set up a model' }),
    ).toBeVisible();
    expect(mock.controller.intent).not.toHaveBeenCalled();
  },
);

it('folds the model, approvals and context usage into + on a one-line composer', async () => {
  const anchor = { current: document.createElement('div') };
  document.body.append(anchor.current);
  render(
    <ComposerControls
      onError={vi.fn()}
      singleLine
      anchor={anchor}
      contextUsage={null}
    />,
  );
  expect(screen.queryByRole('button', { name: 'Model' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Approvals' })).toBeNull();
  const more = await menu('Add files and more');
  expect(
    more.getByText('Context will appear after the next response'),
  ).toBeInTheDocument();
  expect(more.getByRole('menuitem', { name: /^Model/ })).toHaveTextContent(
    'Exact effort model',
  );
  const approvals = await submenu(more, /^Approvals/);
  expect(
    approvals.getByRole('menuitemradio', { name: /^Ask/ }),
  ).toHaveAttribute('aria-checked', 'true');
  await act(async () =>
    fireEvent.click(approvals.getByRole('menuitemradio', { name: /^Auto/ })),
  );
  expect(mock.controller.intent.mock.calls[0][2].approval_mode).toBe(
    'allow_all',
  );
  anchor.current.remove();
});

it('hands Add resource the + trigger so focus can return to it', async () => {
  const onAddResource = vi.fn();
  render(<ComposerControls onError={vi.fn()} onAddResource={onAddResource} />);
  const more = await menu('Add files and more');
  await act(async () =>
    fireEvent.click(more.getByRole('menuitem', { name: 'Add resource…' })),
  );
  expect(onAddResource).toHaveBeenCalledWith(
    screen.getByRole('button', { name: 'Add files and more' }),
  );
});

it('switches a ready app on or off for this chat, shows why the profile leaves one out, and finds more', async () => {
  const composer = {
    schema_version: 1 as const,
    conversation_id: 'conversation-a',
    conversation_revision: '17',
    composer_revision: 'composer-1',
    library: { availability: 'available' as const, revision: 'library-1' },
    smart_skills_off: false,
    active_skills: [],
    suggestions: [],
    commands: [],
    command_total: 0,
    commands_truncated: false,
    apps: [
      {
        item_id: 'mcp:linear',
        app_id: 'linear',
        name: 'Linear',
        icon: 'letter:L',
        on: true,
        available: true,
      },
      {
        item_id: 'mcp:notion',
        app_id: 'notion',
        name: 'Notion',
        icon: 'letter:N',
        on: false,
        available: true,
      },
      {
        item_id: 'mcp:figma',
        app_id: 'figma',
        name: 'Figma',
        icon: 'letter:F',
        on: true,
        available: false,
        reason: "This chat's agent profile doesn't use it.",
      },
      {
        item_id: 'builtin:account:github',
        app_id: 'github',
        name: 'GitHub account',
        icon: 'letter:G',
        on: true,
        available: true,
        switchable: false,
        reason: 'Used by skills and Developer, not by chat tools.',
      },
    ],
  };
  render(<ComposerControls composer={composer} onError={vi.fn()} />);
  const more = await menu('Add files and more');
  expect(more.getByRole('menuitem', { name: /^Apps.*1 on/ })).toBeVisible();
  const apps = await submenu(more, /^Apps/);
  expect(
    apps.getByRole('menuitemcheckbox', { name: 'Linear' }),
  ).toHaveAttribute('aria-checked', 'true');
  expect(
    apps.getByRole('menuitemcheckbox', { name: /^Figma/ }),
  ).toHaveAttribute('aria-disabled', 'true');
  expect(apps.getByText(/agent profile doesn't use it/)).toBeVisible();
  // Built in without chat tools: listed as in Your apps, with why it has no switch.
  expect(
    apps.getByRole('menuitem', { name: /^GitHub account/ }),
  ).toHaveAttribute('aria-disabled', 'true');
  expect(apps.queryByRole('menuitemcheckbox', { name: /^GitHub/ })).toBeNull();
  expect(apps.getByText(/not by chat tools/)).toBeVisible();
  await act(async () =>
    fireEvent.click(apps.getByRole('menuitemcheckbox', { name: 'Notion' })),
  );
  expect(mock.controller.intent).toHaveBeenCalledWith(
    'conversation-a',
    'conversation.apps',
    { item_id: 'mcp:notion', on: true },
    '17',
  );
  await act(async () =>
    fireEvent.click(apps.getByRole('menuitem', { name: 'Find more apps' })),
  );
  expect(mock.navigate).toHaveBeenCalledWith('/settings/apps');
});
