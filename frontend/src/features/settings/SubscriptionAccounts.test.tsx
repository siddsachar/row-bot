import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type {
  SubscriptionAccountsSnapshot,
  SubscriptionFlowSnapshot,
} from '../../api/types';
import SubscriptionAccounts, {
  SubscriptionAccountsSession,
  type SubscriptionAccountsProps,
} from './SubscriptionAccounts';
const revision = 'a'.repeat(64);
const snapshot: SubscriptionAccountsSnapshot = {
  schema_version: 1,
  revision,
  accounts: ['codex', 'claude_subscription', 'xai_oauth'].map(
    (provider_id) => ({
      provider_id,
      revision,
      saved_state: 'disconnected',
      credential_storage: 'unknown',
      has_recovery: false,
      expires_at: null,
      runtime_state: 'unknown',
    }),
  ) as SubscriptionAccountsSnapshot['accounts'],
};
const flow: SubscriptionFlowSnapshot = {
  flow_id: '10000000-0000-4000-8000-000000000001',
  server_epoch: '10000000-0000-4000-8000-000000000002',
  provider_id: 'codex',
  provider_revision: revision,
  state: 'waiting',
  method: 'device_code',
  authorization_url: 'https://example.invalid/login',
  device_code: 'SYNTHETIC',
  expires_at: null,
  quiescent: true,
};
function props(
  overrides: Partial<SubscriptionAccountsProps> = {},
): SubscriptionAccountsProps {
  return {
    load: vi.fn().mockResolvedValue(snapshot),
    review: vi.fn<SubscriptionAccountsProps['review']>(async (intent) => ({
      provider_id: intent.provider_id,
      provider_revision: intent.provider_revision,
      operation: intent.operation,
      flow_id: intent.flow_id ?? null,
      server_epoch: intent.server_epoch ?? null,
      action_digest: 'b'.repeat(64),
      nonce: 'original-nonce',
    })),
    apply: vi.fn<SubscriptionAccountsProps['apply']>(
      async (_intent, _review, commandId) => ({
        command_id: commandId,
        status: 'completed',
        accounts: snapshot,
        flow,
      }),
    ),
    readFlow: vi.fn().mockResolvedValue(flow),
    cancel: vi.fn<SubscriptionAccountsProps['cancel']>(async (_flow, id) => ({
      command_id: id,
      status: 'completed',
      accounts: snapshot,
      flow: { ...flow, state: 'cancelled', quiescent: true },
    })),
    cancelStart: vi
      .fn()
      .mockResolvedValue({ ...flow, state: 'draining', quiescent: false }),
    receipt: vi.fn<SubscriptionAccountsProps['receipt']>(
      async (_provider, id) => ({
        command_id: id,
        status: 'uncertain',
        published: false,
        accounts: snapshot,
      }),
    ),
    onSaved: vi.fn(),
    ...overrides,
  };
}
async function reviewStart() {
  fireEvent.click(
    await screen.findByRole('button', { name: 'Review sign-in' }),
  );
  await screen.findByRole('button', { name: 'Confirm account action' });
}
it('loads saved state passively and sends one exact explicitly reviewed command', async () => {
  const p = props();
  render(<SubscriptionAccounts {...p} />);
  await screen.findByText(/Saved status:/);
  expect(p.review).not.toHaveBeenCalled();
  expect(p.apply).not.toHaveBeenCalled();
  expect(p.readFlow).not.toHaveBeenCalled();
  await reviewStart();
  const confirm = screen.getByRole('button', {
    name: 'Confirm account action',
  });
  fireEvent.click(confirm);
  fireEvent.click(confirm);
  await screen.findByRole('link', { name: 'Open ChatGPT / Codex sign-in' });
  expect(p.apply).toHaveBeenCalledTimes(1);
  expect(vi.mocked(p.apply).mock.calls[0][1].nonce).toBe('original-nonce');
  expect(p.readFlow).not.toHaveBeenCalled();
  expect(screen.getByLabelText('Device code')).toHaveValue('SYNTHETIC');
  fireEvent.click(screen.getByRole('button', { name: 'Read sign-in status' }));
  await waitFor(() => expect(p.readFlow).toHaveBeenCalledTimes(1));
  expect(p.apply).toHaveBeenCalledTimes(1);
});
it('retains a private code across remount and invalidates the review when edited', async () => {
  const session = new SubscriptionAccountsSession();
  const p = props({ session });
  const view = render(<SubscriptionAccounts {...p} />);
  await screen.findByText(/Saved status:/);
  fireEvent.change(screen.getByLabelText('Subscription provider'), {
    target: { value: 'claude_subscription' },
  });
  fireEvent.change(screen.getByLabelText('Claude setup token'), {
    target: { value: 'synthetic-private-token' },
  });
  fireEvent.click(
    screen.getByRole('button', { name: 'Review setup token import' }),
  );
  await screen.findByRole('button', { name: 'Confirm account action' });
  view.unmount();
  render(<SubscriptionAccounts {...p} />);
  expect(screen.getByLabelText('Claude setup token')).toHaveValue(
    'synthetic-private-token',
  );
  expect(session.hasRetained()).toBe(true);
  fireEvent.change(screen.getByLabelText('Claude setup token'), {
    target: { value: 'replacement-token' },
  });
  expect(
    screen.queryByRole('button', { name: 'Confirm account action' }),
  ).not.toBeInTheDocument();
  expect(p.apply).not.toHaveBeenCalled();
  session.dispose();
});
it('retains an uncertain original receipt and locks input without resending', async () => {
  const session = new SubscriptionAccountsSession();
  const p = props({
    session,
    apply: vi
      .fn<SubscriptionAccountsProps['apply']>()
      .mockRejectedValue({ code: 'operation_uncertain' }),
  });
  const view = render(<SubscriptionAccounts {...p} />);
  await reviewStart();
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm account action' }),
  );
  await screen.findByRole('button', { name: 'Read original account receipt' });
  const id = vi.mocked(p.apply).mock.calls[0][2];
  view.unmount();
  render(<SubscriptionAccounts {...p} />);
  expect(screen.getByRole('button', { name: 'Review sign-in' })).toBeDisabled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Read original account receipt' }),
  );
  await waitFor(() =>
    expect(p.receipt).toHaveBeenCalledWith(
      'codex',
      id,
      expect.any(AbortSignal),
    ),
  );
  expect(p.apply).toHaveBeenCalledTimes(1);
  expect(session.hasRetained()).toBe(true);
  session.dispose();
});
it('supports explicit cancel while a login check is still running', async () => {
  let reject!: (reason: unknown) => void;
  const p = props();
  const apply = vi.mocked(p.apply);
  apply
    .mockImplementationOnce(async (_a, _r, id) => ({
      command_id: id,
      status: 'completed',
      accounts: snapshot,
      flow,
    }))
    .mockImplementationOnce(
      () =>
        new Promise<Awaited<ReturnType<SubscriptionAccountsProps['apply']>>>(
          (_resolve, deny) => {
            reject = deny;
          },
        ),
    );
  render(<SubscriptionAccounts {...p} />);
  await reviewStart();
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm account action' }),
  );
  await screen.findByRole('link', { name: 'Open ChatGPT / Codex sign-in' });
  fireEvent.click(screen.getByRole('button', { name: 'Review login check' }));
  await screen.findByRole('button', { name: 'Confirm account action' });
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm account action' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Cancel sign-in' }));
  await waitFor(() => expect(p.cancel).toHaveBeenCalledTimes(1));
  await act(async () => reject({ code: 'subscription_cancelled' }));
  expect(apply).toHaveBeenCalledTimes(2);
  expect(
    screen.getByRole('button', { name: 'Read original account receipt' }),
  ).toBeEnabled();
});
it('purges private intent on authentication disposal and ignores late success', async () => {
  let resolve!: (
    result: Awaited<ReturnType<SubscriptionAccountsProps['apply']>>,
  ) => void;
  const session = new SubscriptionAccountsSession();
  const p = props({
    session,
    apply: vi.fn<SubscriptionAccountsProps['apply']>(
      () =>
        new Promise<Awaited<ReturnType<SubscriptionAccountsProps['apply']>>>(
          (done) => {
            resolve = done;
          },
        ),
    ),
  });
  render(<SubscriptionAccounts {...p} />);
  await reviewStart();
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm account action' }),
  );
  const id = vi.mocked(p.apply).mock.calls[0][2];
  act(() => session.dispose());
  await act(async () =>
    resolve({ command_id: id, status: 'completed', accounts: snapshot, flow }),
  );
  expect(p.onSaved).not.toHaveBeenCalled();
  expect(screen.queryByRole('link')).not.toBeInTheDocument();
  expect(session.hasRetained()).toBe(false);
});
it('never exposes an active link for an invalid login URL', async () => {
  const p = props({
    apply: vi.fn<SubscriptionAccountsProps['apply']>(async (_a, _r, id) => ({
      command_id: id,
      status: 'completed',
      accounts: snapshot,
      flow: { ...flow, authorization_url: 'javascript:alert(1)' },
    })),
  });
  render(<SubscriptionAccounts {...p} />);
  await reviewStart();
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm account action' }),
  );
  await screen.findByText(/Sign-in: waiting/);
  expect(screen.queryByRole('link')).not.toBeInTheDocument();
});

it('recovers only the immutable original reviewed command after explicit request', async () => {
  const p = props();
  vi.mocked(p.apply).mockRejectedValueOnce({ code: 'operation_uncertain' });
  render(<SubscriptionAccounts {...p} />);
  await reviewStart();
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm account action' }),
  );
  await screen.findByRole('button', {
    name: 'Recover original account action',
  });
  const original = vi.mocked(p.apply).mock.calls[0];
  fireEvent.click(
    screen.getByRole('button', { name: 'Recover original account action' }),
  );
  await screen.findByRole('link', { name: 'Open ChatGPT / Codex sign-in' });
  expect(p.apply).toHaveBeenCalledTimes(2);
  expect(vi.mocked(p.apply).mock.calls[1]).toEqual(original);
  expect(p.review).toHaveBeenCalledTimes(1);
});

it('can cancel the original Start before its public flow handle arrives', async () => {
  let reject!: (reason: unknown) => void;
  const p = props({
    apply: vi.fn<SubscriptionAccountsProps['apply']>(
      () =>
        new Promise<Awaited<ReturnType<SubscriptionAccountsProps['apply']>>>(
          (_resolve, deny) => {
            reject = deny;
          },
        ),
    ),
  });
  render(<SubscriptionAccounts {...p} />);
  await reviewStart();
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm account action' }),
  );
  const id = vi.mocked(p.apply).mock.calls[0][2];
  fireEvent.click(screen.getByRole('button', { name: 'Cancel sign-in' }));
  await waitFor(() => expect(p.cancelStart).toHaveBeenCalledWith(id));
  await act(async () => reject({ code: 'subscription_cancelled' }));
  expect(screen.getByText(/Sign-in: draining/)).toBeInTheDocument();
  expect(p.apply).toHaveBeenCalledTimes(1);
});

it('reports successful publication when cancellation arrives after connection', async () => {
  const p = props({
    cancel: vi.fn<SubscriptionAccountsProps['cancel']>(async (_flow, id) => ({
      command_id: id,
      status: 'completed',
      accounts: snapshot,
      flow: { ...flow, state: 'connected', quiescent: false },
    })),
  });
  render(<SubscriptionAccounts {...p} />);
  await reviewStart();
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm account action' }),
  );
  await screen.findByRole('link', { name: 'Open ChatGPT / Codex sign-in' });
  fireEvent.click(screen.getByRole('button', { name: 'Cancel sign-in' }));
  await screen.findByText('Sign-in completed before cancellation.');
  expect(screen.queryByText('Sign-in is cancelled.')).not.toBeInTheDocument();
  expect(screen.getByText(/Sign-in: connected/)).toBeInTheDocument();
});
