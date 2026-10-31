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
  accounts: ['codex', 'xai_oauth'].map((provider_id) => ({
    provider_id,
    revision,
    saved_state: 'disconnected',
    credential_storage: 'unknown',
    has_recovery: false,
    expires_at: null,
    runtime_state: 'unknown',
  })) as SubscriptionAccountsSnapshot['accounts'],
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
async function startSignIn(p: SubscriptionAccountsProps) {
  fireEvent.click(await screen.findByRole('button', { name: 'Start sign-in' }));
  await waitFor(() => expect(p.apply).toHaveBeenCalledOnce());
}
it('starts subscription sign-in from the compact row action and shows its flow', async () => {
  const p = props({
    compact: true,
    initialProvider: 'codex',
    initialAction: 'connect',
  });
  render(<SubscriptionAccounts {...p} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Connect' }));
  await waitFor(() => expect(p.apply).toHaveBeenCalledOnce());
  expect(vi.mocked(p.apply).mock.calls[0][1].nonce).toBe('original-nonce');
  expect(await screen.findByText('Waiting for you to sign in')).toBeVisible();
  // The device code is shown large with Copy and the steps (U7).
  expect(screen.getByText('SYNTHETIC')).toBeVisible();
  expect(
    screen.getByRole('button', { name: 'Copy device code' }),
  ).toBeVisible();
  expect(screen.getByText('Enter this code when it asks.')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Review sign-in' })).toBeNull();
});

it('follows a waiting ChatGPT sign-in, which the server checks itself, until it connects (B342)', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    const p = props({
      compact: true,
      initialProvider: 'codex',
      initialAction: 'connect',
    });
    render(<SubscriptionAccounts {...p} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Connect' }));
    const waiting = await screen.findByText('Waiting for you to sign in');
    const started = vi.mocked(p.readFlow).mock.results.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5100);
    });
    await waitFor(() =>
      expect(vi.mocked(p.readFlow).mock.calls.length).toBeGreaterThan(started),
    );
    // Only reads: the server checks the code, so the page sends nothing more.
    expect(p.apply).toHaveBeenCalledTimes(1);
    expect(waiting).toBeVisible();
    const flow = vi.mocked(p.readFlow).mock.calls[0][0];
    vi.mocked(p.readFlow).mockResolvedValue({
      ...flow,
      state: 'connected',
      quiescent: true,
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5100);
    });
    expect(
      await screen.findByText('Signed in to ChatGPT / Codex.'),
    ).toBeVisible();
    expect(p.onSaved).toHaveBeenCalled();
    expect(p.apply).toHaveBeenCalledTimes(1);
  } finally {
    vi.useRealTimers();
  }
});
it('a saved sign-in that expired says so and offers Reconnect (B342)', async () => {
  const saved = {
    ...snapshot,
    accounts: snapshot.accounts.map((account) =>
      account.provider_id === 'codex'
        ? {
            ...account,
            saved_state: 'saved',
            expires_at: '2026-10-14T05:32:00+00:00',
          }
        : account,
    ),
  } as SubscriptionAccountsSnapshot;
  const p = props({
    compact: true,
    initialProvider: 'codex',
    initialAction: 'manage',
    signInExpired: true,
    load: vi.fn().mockResolvedValue(saved),
  });
  render(<SubscriptionAccounts {...p} />);
  expect(
    await screen.findByRole('button', { name: 'Reconnect' }),
  ).toBeVisible();
  expect(screen.getByText(/Sign-in expired/)).toBeVisible();
  expect(screen.queryByText(/Expires/)).toBeNull();
});
it('Check now on a ChatGPT sign-in reads it, so one the server finished shows as signed in (B357)', async () => {
  const p = props({
    compact: true,
    initialProvider: 'codex',
    initialAction: 'connect',
  });
  render(<SubscriptionAccounts {...p} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Connect' }));
  await screen.findByText('Waiting for you to sign in');
  const flow = vi.mocked(p.apply).mock.calls[0];
  expect(flow).toBeDefined();
  vi.mocked(p.readFlow).mockResolvedValue({
    ...(await vi.mocked(p.apply).mock.results[0].value).flow,
    state: 'connected',
    quiescent: true,
  });
  fireEvent.click(screen.getByRole('button', { name: 'Check now' }));
  expect(
    await screen.findByText('Signed in to ChatGPT / Codex.'),
  ).toBeVisible();
  // A read, never a Check command whose revision the server's finish moved.
  expect(p.review).toHaveBeenCalledTimes(1);
  expect(p.apply).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole('alert')).toBeNull();
});
it('keeps an active sign-in attached to its provider when another row is opened', async () => {
  const session = new SubscriptionAccountsSession();
  const p = props({
    session,
    compact: true,
    initialProvider: 'codex',
    initialAction: 'connect',
  });
  const first = render(<SubscriptionAccounts {...p} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Connect' }));
  await screen.findByText('Waiting for you to sign in');
  first.unmount();
  render(
    <SubscriptionAccounts
      {...p}
      initialProvider="xai_oauth"
      initialAction="disconnect"
    />,
  );
  expect(
    screen.getByText(/Finish or cancel this sign-in before opening xAI Grok/),
  ).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Disconnect' })).toBeNull();
  expect(vi.mocked(p.apply).mock.calls).toHaveLength(1);
  session.dispose();
});
it('keeps secondary account actions collapsed at rest while loading passively', async () => {
  const p = props({ collapsedAtRest: true });
  render(<SubscriptionAccounts {...p} />);
  const disclosure = screen
    .getByText('Subscription accounts')
    .closest('details');
  expect(disclosure).not.toHaveAttribute('open');
  await waitFor(() => expect(p.load).toHaveBeenCalledOnce());
  expect(p.review).not.toHaveBeenCalled();
});

it('loads saved state passively and sends one exact command on click', async () => {
  const p = props();
  render(<SubscriptionAccounts {...p} />);
  await screen.findByText(/Saved status:/);
  expect(p.review).not.toHaveBeenCalled();
  expect(p.apply).not.toHaveBeenCalled();
  expect(p.readFlow).not.toHaveBeenCalled();
  await startSignIn(p);
  await screen.findByRole('link', { name: 'Open ChatGPT / Codex sign-in' });
  expect(p.apply).toHaveBeenCalledTimes(1);
  expect(vi.mocked(p.apply).mock.calls[0][1].nonce).toBe('original-nonce');
  expect(p.readFlow).not.toHaveBeenCalled();
  expect(screen.getByLabelText('Device code')).toHaveValue('SYNTHETIC');
  fireEvent.click(screen.getByRole('button', { name: 'Read sign-in status' }));
  await waitFor(() => expect(p.readFlow).toHaveBeenCalledTimes(1));
  expect(p.apply).toHaveBeenCalledTimes(1);
});
it('retains an unsent private code across remount and allows editing', async () => {
  const session = new SubscriptionAccountsSession();
  const xai: SubscriptionFlowSnapshot = {
    ...flow,
    provider_id: 'xai_oauth',
    method: 'loopback',
    device_code: null,
  };
  const p = props({
    session,
    apply: vi.fn<SubscriptionAccountsProps['apply']>(
      async (_intent, _review, commandId) => ({
        command_id: commandId,
        status: 'completed',
        accounts: snapshot,
        flow: xai,
      }),
    ),
  });
  const view = render(<SubscriptionAccounts {...p} />);
  await screen.findByText(/Saved status:/);
  fireEvent.change(screen.getByLabelText('Subscription provider'), {
    target: { value: 'xai_oauth' },
  });
  await startSignIn(p);
  fireEvent.change(
    await screen.findByLabelText('Authorization code or callback URL'),
    { target: { value: 'synthetic-private-code' } },
  );
  view.unmount();
  render(<SubscriptionAccounts {...p} />);
  expect(
    screen.getByLabelText('Authorization code or callback URL'),
  ).toHaveValue('synthetic-private-code');
  expect(session.hasRetained()).toBe(true);
  fireEvent.change(
    screen.getByLabelText('Authorization code or callback URL'),
    {
      target: { value: 'replacement-code' },
    },
  );
  expect(
    screen.getByRole('button', { name: 'Connect with code' }),
  ).toBeEnabled();
  expect(p.apply).toHaveBeenCalledOnce(); // the start; the code was never sent
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
  await startSignIn(p);
  await screen.findByRole('button', { name: 'Check account' });
  const id = vi.mocked(p.apply).mock.calls[0][2];
  view.unmount();
  render(<SubscriptionAccounts {...p} />);
  expect(screen.getByRole('button', { name: 'Start sign-in' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Check account' }));
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
it('a Start refused by a locked keychain says why and offers Connect and Close again (B413)', async () => {
  const session = new SubscriptionAccountsSession();
  const p = props({
    session,
    compact: true,
    initialProvider: 'codex',
    initialAction: 'connect',
    apply: vi
      .fn<SubscriptionAccountsProps['apply']>()
      .mockRejectedValue({ code: 'subscription_storage_unavailable' }),
  });
  const view = render(<SubscriptionAccounts {...p} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Connect' }));

  expect(await screen.findByRole('alert')).toHaveTextContent(
    /keychain is locked or unavailable/,
  );
  expect(screen.queryByText(/couldn't confirm/)).toBeNull();
  expect(screen.queryByRole('button', { name: 'Check again' })).toBeNull();
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Connect' })).toBeEnabled(),
  );
  expect(screen.getByRole('button', { name: 'Close' })).toBeEnabled();
  expect(session.hasRetained()).toBe(false);

  // Closed and opened again (the keychain unlocked meanwhile), Connect tries again.
  view.unmount();
  render(<SubscriptionAccounts {...p} />);
  vi.mocked(p.apply).mockImplementationOnce(
    async (_intent, _review, commandId) => ({
      command_id: commandId,
      status: 'completed',
      accounts: snapshot,
      flow,
    }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
  expect(await screen.findByText('Waiting for you to sign in')).toBeVisible();
  expect(p.apply).toHaveBeenCalledTimes(2);
  session.dispose();
});
it('a Start whose outcome is unknown keeps it to check, with Close held (B413)', async () => {
  const session = new SubscriptionAccountsSession();
  const p = props({
    session,
    compact: true,
    initialProvider: 'codex',
    initialAction: 'connect',
    apply: vi
      .fn<SubscriptionAccountsProps['apply']>()
      .mockRejectedValue({ code: 'network_unavailable' }),
  });
  render(<SubscriptionAccounts {...p} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Connect' }));

  expect(await screen.findByText(/couldn't confirm that/)).toBeVisible();
  expect(screen.getByRole('button', { name: 'Check again' })).toBeEnabled();
  expect(screen.getByRole('button', { name: 'Connect' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Close' })).toBeDisabled();
  expect(session.hasRetained()).toBe(true);
  session.dispose();
});
it('supports explicit cancel while a login check is still running', async () => {
  let reject!: (reason: unknown) => void;
  const p = props();
  // ChatGPT's check reads the sign-in (the server checks the code itself).
  vi.mocked(p.readFlow).mockImplementationOnce(
    () =>
      new Promise((_resolve, deny) => {
        reject = deny;
      }),
  );
  render(<SubscriptionAccounts {...p} />);
  await startSignIn(p);
  await screen.findByRole('link', { name: 'Open ChatGPT / Codex sign-in' });
  fireEvent.click(screen.getByRole('button', { name: 'Check login' }));
  await waitFor(() => expect(p.readFlow).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole('button', { name: 'Cancel sign-in' }));
  await waitFor(() => expect(p.cancel).toHaveBeenCalledTimes(1));
  await act(async () => reject({ code: 'subscription_cancelled' }));
  expect(p.apply).toHaveBeenCalledTimes(1);
  expect(screen.getByText('Sign-in is cancelled.')).toBeVisible();
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
  await startSignIn(p);
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
  await startSignIn(p);
  await screen.findByText(/Sign-in: waiting/);
  expect(screen.queryByRole('link')).not.toBeInTheDocument();
});

it('recovers only the immutable original reviewed command after explicit request', async () => {
  const p = props();
  vi.mocked(p.apply).mockRejectedValueOnce({ code: 'operation_uncertain' });
  render(<SubscriptionAccounts {...p} />);
  await startSignIn(p);
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
  await startSignIn(p);
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
  await startSignIn(p);
  await screen.findByRole('link', { name: 'Open ChatGPT / Codex sign-in' });
  fireEvent.click(screen.getByRole('button', { name: 'Cancel sign-in' }));
  await screen.findByText('Sign-in completed before cancellation.');
  expect(screen.queryByText('Sign-in is cancelled.')).not.toBeInTheDocument();
  expect(screen.getByText(/Sign-in: connected/)).toBeInTheDocument();
});

it('starts at once for a hosted first run, says its one line, and offers a new code when it expires (U3)', async () => {
  const beforeStart = vi.fn();
  const onStarted = vi.fn();
  const p = props({
    compact: true,
    autoStart: true,
    remote: true,
    initialProvider: 'codex',
    initialAction: 'connect',
    guide: 'Paste the code and approve.',
    beforeStart,
    onStarted,
  });
  const { rerender } = render(<SubscriptionAccounts {...p} />);
  expect(await screen.findByText('Paste the code and approve.')).toBeVisible();
  expect(p.apply).toHaveBeenCalledOnce();
  expect(onStarted).toHaveBeenCalledWith(flow);
  expect(screen.queryByText('Enter this code when it asks.')).toBeNull();
  rerender(<SubscriptionAccounts {...p} />);
  expect(p.apply).toHaveBeenCalledOnce();

  // The code ran out: one button gets a new one, copied from its own click.
  vi.mocked(p.readFlow).mockResolvedValue({ ...flow, state: 'expired' });
  fireEvent.click(screen.getByRole('button', { name: 'Check now' }));
  fireEvent.click(
    await screen.findByRole('button', { name: 'Get a new code' }),
  );
  expect(beforeStart).toHaveBeenCalledOnce();
  await waitFor(() => expect(p.apply).toHaveBeenCalledTimes(2));
});

it('tells a browser on another device what to paste for Grok (H5)', async () => {
  const grok = {
    ...flow,
    provider_id: 'xai_oauth',
    method: 'loopback',
    device_code: null,
  } as SubscriptionFlowSnapshot;
  const p = props({
    compact: true,
    remote: true,
    initialProvider: 'xai_oauth',
    initialAction: 'connect',
    apply: vi.fn<SubscriptionAccountsProps['apply']>(async (_i, _r, id) => ({
      command_id: id,
      status: 'completed',
      accounts: snapshot,
      flow: grok,
    })),
  });
  render(<SubscriptionAccounts {...p} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Connect' }));
  expect(
    await screen.findByText(
      'Sign in on the page that opens. It then sends you to a page that doesn’t load: copy that page’s address from the address bar and paste it below.',
    ),
  ).toBeVisible();
  expect(
    screen.getByLabelText('Address of the page that didn’t load'),
  ).toBeVisible();
});
