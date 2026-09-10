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
  controller: { intent: vi.fn(), getSelectionVersion: vi.fn() },
}));
vi.mock('../../runtime', () => ({
  useClientState: () => mock.state,
  useRuntime: () => ({ controller: mock.controller }),
}));
beforeEach(() => {
  vi.resetAllMocks();
  mock.version = 1;
  mock.controller.getSelectionVersion.mockImplementation(() => mock.version);
  mock.controller.intent.mockResolvedValue({ status: 'completed' });
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
async function thinking() {
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Thinking' })),
  );
  return within(screen.getByRole('dialog', { name: 'Thinking' }));
}

it('shows compact current values with only the exact server-supplied Thinking choices', async () => {
  render(<ComposerControls onError={vi.fn()} />);
  const controls = within(
    screen.getByRole('group', { name: 'Conversation controls' }),
  );
  expect(controls.getByRole('button', { name: 'Model' })).toHaveTextContent(
    'Exact effort model',
  );
  expect(controls.getByRole('button', { name: 'Approvals' })).toHaveTextContent(
    'Ask',
  );
  expect(
    controls.getByRole('button', { name: 'More conversation controls' }),
  ).toHaveTextContent('Agent · Writer');
  expect(controls.getByRole('button', { name: 'Thinking' })).toHaveTextContent(
    'Provider default',
  );
  expect(screen.queryByRole('combobox')).toBeNull();
  const popover = await thinking();
  expect(
    popover.getByRole('button', { name: 'Provider default' }),
  ).toHaveAttribute('aria-pressed', 'true');
  expect(popover.getByRole('button', { name: 'Careful' })).toBeEnabled();
  expect(popover.queryByRole('button', { name: 'High' })).toBeNull();
  expect(popover.queryByLabelText('Thinking budget')).toBeNull();
});

it.each([
  ['Careful', { kind: 'effort', effort: 'careful-exact-model' }],
  ['Provider default', { kind: 'provider_default' }],
] as const)(
  'sends the exact %s reasoning choice and capability revision through conversation.controls',
  async (label, selection) => {
    const onError = vi.fn();
    render(<ComposerControls onError={onError} />);
    const popover = await thinking();
    await act(async () =>
      fireEvent.click(popover.getByRole('button', { name: label })),
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
    expect(screen.queryByRole('button', { name: 'Thinking' })).toBeNull();
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
  const popover = await thinking();
  const input = popover.getByRole('spinbutton', {
    name: 'Thinking budget 256–2048 tokens',
  });
  const useBudget = popover.getByRole('button', { name: 'Use budget' });
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
  const popover = await thinking();
  expect(popover.getByRole('status')).toHaveTextContent('no longer available');
  await act(async () =>
    fireEvent.click(popover.getByRole('button', { name: 'Provider default' })),
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
  const items = await menu('Model');
  expect(
    items.getByRole('menuitem', { name: 'Unavailable model' }),
  ).toHaveAttribute('aria-disabled', 'true');
  await act(async () =>
    fireEvent.click(
      items.getByRole('menuitem', { name: 'Other provider model' }),
    ),
  );
  expect(mock.controller.intent.mock.calls[0][2]).toEqual({
    model_selection: { provider_id: 'other', model_ref: 'other::no-reasoning' },
    runtime_mode: 'agent',
    profile_id: 'writer',
    approval_mode: 'approve',
  });
});

it('removes old Thinking content immediately after a model switch without sending an old capability', async () => {
  const view = render(<ComposerControls onError={vi.fn()} />);
  await thinking();
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
  expect(screen.queryByRole('dialog', { name: 'Thinking' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Thinking' })).toBeNull();
  expect(mock.controller.intent).not.toHaveBeenCalled();
});

it('keeps approval, runtime and profile distinct in their compact menus', async () => {
  render(<ComposerControls onError={vi.fn()} />);
  const approvals = await menu('Approvals');
  await act(async () =>
    fireEvent.click(approvals.getByRole('menuitem', { name: 'Block' })),
  );
  expect(mock.controller.intent.mock.calls[0][2].approval_mode).toBe('block');
  const more = await menu('More conversation controls');
  expect(
    more.getByRole('menuitem', { name: 'Runtime: Agent (selected)' }),
  ).toBeVisible();
  expect(
    more.getByRole('menuitem', { name: 'Profile: Writer (selected)' }),
  ).toBeVisible();
  await act(async () =>
    fireEvent.click(more.getByRole('menuitem', { name: 'Runtime: Chat only' })),
  );
  expect(mock.controller.intent.mock.calls[1][2].runtime_mode).toBe(
    'chat_only',
  );
  expect(mock.controller.intent.mock.calls[1][2].profile_id).toBe('writer');
});

it('blocks running and disconnected controls without removing their current values', () => {
  const view = render(<ComposerControls disabled onError={vi.fn()} />);
  for (const button of screen.getAllByRole('button'))
    expect(button).toBeDisabled();
  mock.state.status = 'disconnected';
  view.rerender(<ComposerControls onError={vi.fn()} />);
  for (const button of screen.getAllByRole('button'))
    expect(button).toBeDisabled();
  expect(mock.controller.intent).not.toHaveBeenCalled();
});

it('retains an error on rejected controls and ignores a late failure from another conversation', async () => {
  const onError = vi.fn();
  mock.controller.intent.mockRejectedValueOnce({ code: 'revision_conflict' });
  render(<ComposerControls onError={onError} />);
  const popover = await thinking();
  await act(async () =>
    fireEvent.click(popover.getByRole('button', { name: 'Careful' })),
  );
  expect(onError).toHaveBeenCalledTimes(1);
  expect(onError.mock.calls[0][0]).not.toBe('');
  expect(screen.getByRole('dialog', { name: 'Thinking' })).toBeVisible();
  let fail!: (reason: unknown) => void;
  mock.controller.intent.mockImplementationOnce(
    () =>
      new Promise((_resolve, reject) => {
        fail = reject;
      }),
  );
  await act(async () =>
    fireEvent.click(popover.getByRole('button', { name: 'Careful' })),
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
    const choices = await menu('Model');
    const unavailable = choices.getByRole('menuitem', {
      name: 'No cached models. Open Models in Preferences.',
    });
    expect(unavailable).toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(unavailable);
    expect(mock.controller.intent).not.toHaveBeenCalled();
  },
);
