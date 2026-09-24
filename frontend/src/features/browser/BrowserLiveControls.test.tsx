import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import BrowserLiveControls, {
  createBrowserControlSession,
  type BrowserAction,
  type BrowserCommand,
  type BrowserControlState,
  type BrowserReceipt,
} from './BrowserLiveControls';

const conversationId = 'conversation-browser-1';
const revision = 'a'.repeat(64);
const nextRevision = 'b'.repeat(64);

const availability = (active: boolean) => ({
  'browser.navigate': {
    state: active ? ('available' as const) : ('check_on_use' as const),
    code: null,
  },
  'browser.take_over': {
    state: active ? ('available' as const) : ('unavailable' as const),
    code: active ? null : 'browser_session_inactive',
  },
  'browser.check': {
    state: active ? ('available' as const) : ('unavailable' as const),
    code: active ? null : 'browser_session_inactive',
  },
  'browser.back': {
    state: active ? ('available' as const) : ('unavailable' as const),
    code: active ? null : 'browser_session_inactive',
  },
  'browser.end': {
    state: active ? ('available' as const) : ('unavailable' as const),
    code: active ? null : 'browser_session_inactive',
  },
  'browser.click': {
    state: 'unavailable' as const,
    code: 'exact_page_target_contract_required',
  },
  'browser.type': {
    state: 'unavailable' as const,
    code: 'exact_page_target_and_hidden_text_contract_required',
  },
  'browser.scroll': {
    state: 'unavailable' as const,
    code: 'semantic_page_observation_contract_required',
  },
  'browser.tab': {
    state: 'unavailable' as const,
    code: 'owned_tab_identity_contract_required',
  },
  'browser.screenshot': {
    state: 'unavailable' as const,
    code: 'private_preview_export_unavailable',
  },
  'browser.external.attach': {
    state: 'unavailable' as const,
    code: 'use_computer_use_for_external_browser',
  },
});

const idle: BrowserControlState = {
  schema_version: 1,
  conversation_id: conversationId,
  revision,
  active: false,
  paused: false,
  state: 'idle',
  site: '',
  url: '',
  last_action: '',
  availability: availability(false),
};

const active: BrowserControlState = {
  ...idle,
  active: true,
  state: 'observing',
  site: 'example.test',
  url: 'https://example.test/path',
  last_action: 'Opened website',
  availability: availability(true),
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

function completed(command: BrowserCommand): BrowserReceipt {
  const browser_control: BrowserControlState = {
    ...active,
    revision: nextRevision,
    paused: command.type === 'browser.take_over',
    state:
      command.type === 'browser.take_over'
        ? 'waiting_user'
        : command.type === 'browser.end'
          ? 'idle'
          : 'observing',
    active: command.type !== 'browser.end',
  };
  return {
    schema_version: 1,
    command_id: command.command_id,
    action: command.type,
    conversation_id: conversationId,
    status: 'completed',
    code: null,
    revision: nextRevision,
    browser_control,
  };
}

function options(value: BrowserControlState = active) {
  return {
    session: createBrowserControlSession(conversationId),
    load: vi.fn().mockResolvedValue(value),
    loadPreview: vi
      .fn()
      .mockImplementation(async (_conversation: string, expected: string) => ({
        schema_version: 1,
        conversation_id: conversationId,
        revision: expected,
        state: 'waiting',
        image_base64: null,
      })),
    review: vi
      .fn()
      .mockImplementation(
        async (
          action: BrowserAction,
          payload: { revision: string; url?: string },
        ) => ({
          schema_version: 1,
          action,
          conversation_id: conversationId,
          revision: payload.revision,
          policy_action:
            action === 'browser.navigate'
              ? 'browser_navigate'
              : 'browser_snapshot',
          policy_decision: action === 'browser.navigate' ? 'allow' : 'ask',
          policy_reason:
            action === 'browser.navigate'
              ? ''
              : 'This live-control action requires explicit confirmation.',
          approval_required: true,
          origin_and_path:
            action === 'browser.navigate'
              ? 'https://destination.test/path'
              : value.url,
          query_present: Boolean(payload.url?.includes('?')),
          disclosures: ['This is the exact reviewed browser action.'],
          action_digest: 'd'.repeat(64),
          nonce: 'signed-browser-review',
        }),
      ),
    execute: vi
      .fn()
      .mockImplementation(async (command: BrowserCommand) =>
        completed(command),
      ),
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

it('passively reads status without starting a browser action', async () => {
  const props = options(idle);
  render(<BrowserLiveControls {...props} />);

  await screen.findByText('No managed-browser activity for this conversation.');
  expect(props.load).toHaveBeenCalledTimes(1);
  expect(props.review).not.toHaveBeenCalled();
  expect(props.execute).not.toHaveBeenCalled();
  expect(
    screen.getByRole('button', { name: 'Take over browser' }),
  ).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Open address' })).toBeDisabled();
});

it('shows an ephemeral picture and clears it when hidden', async () => {
  const props = options(active);
  props.loadPreview.mockImplementation(
    async (_conversation: string, expected: string) => ({
      schema_version: 1,
      conversation_id: conversationId,
      revision: expected,
      state: 'available',
      image_base64: 'c3ludGhldGlj',
    }),
  );
  render(<BrowserLiveControls {...props} />);
  expect(
    await screen.findByAltText('Ephemeral managed browser picture'),
  ).toHaveAttribute('src', 'data:image/png;base64,c3ludGhldGlj');
  fireEvent.click(screen.getByRole('button', { name: 'Hide browser picture' }));
  expect(screen.queryByAltText('Ephemeral managed browser picture')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Show browser picture' }));
  expect(
    await screen.findByAltText('Ephemeral managed browser picture'),
  ).toBeVisible();
});

it('shields a picture when browser control moves to a protected state', async () => {
  const props = options({ ...active, state: 'waiting_user' });
  props.loadPreview.mockImplementation(
    async (_conversation: string, expected: string) => ({
      schema_version: 1,
      conversation_id: conversationId,
      revision: expected,
      state: 'shielded',
      image_base64: null,
    }),
  );
  render(<BrowserLiveControls {...props} />);
  expect(
    await screen.findByText(/Picture hidden while you control/),
  ).toBeVisible();
  expect(screen.queryByAltText('Ephemeral managed browser picture')).toBeNull();
});

it('opens an address in one click with the exact reviewed command', async () => {
  const props = options();
  render(<BrowserLiveControls {...props} />);
  await screen.findByText('The managed browser is ready.');

  fireEvent.change(screen.getByLabelText('Address'), {
    target: { value: 'https://destination.test/path?token=private' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Open address' }));

  await screen.findByText('Open address completed.');
  expect(props.execute).toHaveBeenCalledTimes(1);
  const [command, review] = props.execute.mock.calls[0];
  expect(command).toMatchObject({
    type: 'browser.navigate',
    payload: {
      revision,
      url: 'https://destination.test/path?token=private',
      nonce: 'signed-browser-review',
    },
  });
  expect(review.action_digest).toBe('d'.repeat(64));
  expect(props.session.hasRetained()).toBe(false);
});

it('does not submit an invalid address', async () => {
  const props = options();
  render(<BrowserLiveControls {...props} />);
  await screen.findByText('The managed browser is ready.');
  fireEvent.change(screen.getByLabelText('Address'), {
    target: { value: 'file:///private' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Open address' }));
  expect(
    screen.getByText('Enter a complete http:// or https:// address.'),
  ).toBeVisible();
  expect(props.review).not.toHaveBeenCalled();
  expect(props.execute).not.toHaveBeenCalled();
  expect(props.session.hasRetained()).toBe(false);
});

it('retains an uncertain original action across remount without implicit replay', async () => {
  const props = options();
  props.execute.mockRejectedValueOnce(Error('synthetic response loss'));
  const first = render(<BrowserLiveControls {...props} />);
  await screen.findByText('The managed browser is ready.');
  fireEvent.click(screen.getByRole('button', { name: 'Check current page' }));
  await screen.findByText(/outcome is uncertain/);
  const original = props.session.getSnapshot().retained;
  first.unmount();

  render(<BrowserLiveControls {...props} />);
  expect(props.execute).toHaveBeenCalledTimes(1);
  fireEvent.click(
    screen.getByRole('button', { name: 'Check original browser action' }),
  );
  await screen.findByText('Check current page completed.');
  expect(props.execute.mock.calls[1]).toEqual([
    original?.command,
    original?.review,
  ]);
});

it('reconciles only the original command after an uncertain outcome', async () => {
  const props = options();
  props.execute
    .mockRejectedValueOnce(Error('synthetic response loss'))
    .mockImplementationOnce(async (command) => completed(command));
  render(<BrowserLiveControls {...props} />);
  await screen.findByText('The managed browser is ready.');
  fireEvent.click(screen.getByRole('button', { name: 'Go back' }));
  await screen.findByText(/outcome is uncertain/);
  const original = props.execute.mock.calls[0];

  fireEvent.click(
    screen.getByRole('button', { name: 'Check original browser action' }),
  );
  await screen.findByText('Go back completed.');
  expect(props.execute.mock.calls[1]).toEqual(original);
  expect(props.review).toHaveBeenCalledTimes(1);
});

it('keeps partial outcomes retained and blocks replacement actions', async () => {
  const props = options();
  props.execute.mockImplementation(async (command) => ({
    ...completed(command),
    status: 'partial',
    code: 'browser_outcome_uncertain',
    browser_control: null,
  }));
  render(<BrowserLiveControls {...props} />);
  await screen.findByText('The managed browser is ready.');
  fireEvent.click(screen.getByRole('button', { name: 'End browser activity' }));

  await screen.findByText(/outcome is not confirmed/);
  expect(props.session.hasRetained()).toBe(true);
  expect(screen.getByRole('button', { name: 'Go back' })).toBeDisabled();
  expect(
    screen.getByRole('button', { name: 'Check original browser action' }),
  ).toBeEnabled();
});

it('rejects a mismatched review scope without enabling execution', async () => {
  const props = options();
  props.review.mockImplementationOnce(async (action, payload) => ({
    schema_version: 1,
    action,
    conversation_id: 'different-conversation',
    revision: payload.revision,
    policy_action: 'browser_snapshot',
    policy_decision: 'ask',
    policy_reason: '',
    approval_required: true,
    origin_and_path: '',
    query_present: false,
    disclosures: ['Mismatch'],
    action_digest: 'd'.repeat(64),
    nonce: 'signed-browser-review',
  }));
  render(<BrowserLiveControls {...props} />);
  await screen.findByText('The managed browser is ready.');
  fireEvent.click(screen.getByRole('button', { name: 'Check current page' }));

  await screen.findByText(/could not be reviewed/);
  expect(props.execute).not.toHaveBeenCalled();
});

it('tombstones a late effect result when authenticated ownership is disposed', async () => {
  const props = options();
  const pending = deferred<BrowserReceipt>();
  props.execute.mockReturnValue(pending.promise);
  render(<BrowserLiveControls {...props} />);
  await screen.findByText('The managed browser is ready.');
  fireEvent.click(screen.getByRole('button', { name: 'Check current page' }));
  await waitFor(() => expect(props.execute).toHaveBeenCalledOnce());
  const command = props.execute.mock.calls[0][0];

  act(() => props.session.dispose());
  await act(async () => pending.resolve(completed(command)));

  expect(props.session.getSnapshot().snapshot).toBeNull();
  expect(props.session.hasRetained()).toBe(false);
  expect(
    screen.getByRole('button', { name: 'Check current page' }),
  ).toBeDisabled();
});

it('explains unsupported page-target, preview, tab, and external-browser controls', async () => {
  const props = options();
  render(<BrowserLiveControls {...props} />);
  await screen.findByText('The managed browser is ready.');
  fireEvent.click(screen.getByText('Unavailable browser controls'));

  expect(
    screen.getByText(/Page clicks need an exact page-target/),
  ).toBeVisible();
  expect(
    screen.getByText(/Private browser previews cannot be exported/),
  ).toBeVisible();
  expect(screen.getByText(/Use Computer Use/)).toBeVisible();
  expect(props.execute).not.toHaveBeenCalled();
});
