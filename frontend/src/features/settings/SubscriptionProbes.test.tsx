import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type {
  SubscriptionProbeResult,
  SubscriptionProbeSnapshot,
} from '../../api/types';
import SubscriptionProbes, {
  SubscriptionProbesSession,
  type SubscriptionProbesProps,
} from './SubscriptionProbes';

const result: SubscriptionProbeResult = {
  provider_id: 'codex',
  kind: 'tokens',
  model_ref: null,
  status: 'missing',
  chat_ok: null,
  tool_calling: null,
  tool_round_trip: null,
  vision_ok: null,
  checked_at: null,
};
const snapshot: SubscriptionProbeSnapshot = {
  schema_version: 1,
  revision: 'a'.repeat(64),
  items: [result],
};
function props(
  overrides: Partial<SubscriptionProbesProps> = {},
): SubscriptionProbesProps {
  return {
    load: vi.fn().mockResolvedValue(snapshot),
    review: vi.fn<SubscriptionProbesProps['review']>(async (intent) => ({
      ...intent,
      action_digest: 'b'.repeat(64),
      nonce: 'original-nonce',
    })),
    apply: vi.fn().mockResolvedValue(result),
    status: vi.fn<SubscriptionProbesProps['status']>(async (id) => ({
      command_id: id,
      provider_id: 'codex',
      state: 'uncertain',
      quiescent: true,
      result: null,
    })),
    cancel: vi.fn<SubscriptionProbesProps['cancel']>(async (id) => ({
      command_id: id,
      provider_id: 'codex',
      state: 'draining',
      quiescent: false,
      result: null,
    })),
    receipt: vi.fn<SubscriptionProbesProps['receipt']>(async (id) => ({
      command_id: id,
      provider_id: 'codex',
      status: 'uncertain',
      published: false,
    })),
    onSaved: vi.fn(),
    ...overrides,
  };
}
async function review() {
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Review check' })).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Review check' }));
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Confirm check' })).toBeEnabled(),
  );
}
it('keeps consequential subscription checks collapsed at rest', async () => {
  const p = props({ collapsedAtRest: true });
  render(<SubscriptionProbes {...p} />);
  const disclosure = screen.getByText('Subscription checks').closest('details');
  expect(disclosure).not.toHaveAttribute('open');
  await waitFor(() => expect(p.load).toHaveBeenCalledOnce());
  expect(p.review).not.toHaveBeenCalled();
  expect(p.apply).not.toHaveBeenCalled();
});

it('loads saved metadata without probing and confirms one exact reviewed command', async () => {
  const p = props();
  render(<SubscriptionProbes {...p} />);
  await screen.findByText(/Stored credentials and expiry: missing/);
  expect(p.apply).not.toHaveBeenCalled();
  expect(p.review).not.toHaveBeenCalled();
  expect(
    screen.getByText(/It does not contact the provider/),
  ).toBeInTheDocument();
  await review();
  fireEvent.click(screen.getByRole('button', { name: 'Confirm check' }));
  fireEvent.click(screen.getByRole('button', { name: 'Confirm check' }));
  await screen.findByText(/The check completed/);
  expect(p.apply).toHaveBeenCalledTimes(1);
  expect(vi.mocked(p.apply).mock.calls[0][0]).toEqual({
    provider_id: 'codex',
    provider_revision: snapshot.revision,
    kind: 'tokens',
    model_ref: null,
    action_digest: 'b'.repeat(64),
    nonce: 'original-nonce',
  });
  expect(p.onSaved).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: 'Reload saved checks' }));
  await waitFor(() => expect(p.load).toHaveBeenCalledTimes(2));
});
it('retains a qualified model draft and invalidates its review when edited', async () => {
  const session = new SubscriptionProbesSession();
  const p = props({ session, onBrowseModels: vi.fn() });
  const view = render(<SubscriptionProbes {...p} />);
  await screen.findByText(/Stored credentials and expiry: missing/);
  fireEvent.change(screen.getByLabelText('Subscription provider'), {
    target: { value: 'xai_oauth' },
  });
  fireEvent.change(screen.getByLabelText('Check type'), {
    target: { value: 'vision' },
  });
  fireEvent.change(
    screen.getByLabelText('Provider-qualified model reference'),
    { target: { value: 'model:xai_oauth:grok-4' } },
  );
  expect(
    screen.getByText(/Confirmation sends a small synthetic image/),
  ).toBeInTheDocument();
  await review();
  expect(session.hasRetained()).toBe(true);
  view.unmount();
  render(<SubscriptionProbes {...p} />);
  expect(
    screen.getByLabelText('Provider-qualified model reference'),
  ).toHaveValue('model:xai_oauth:grok-4');
  expect(screen.getByRole('button', { name: 'Confirm check' })).toBeEnabled();
  fireEvent.change(
    screen.getByLabelText('Provider-qualified model reference'),
    { target: { value: 'model:xai_oauth:different' } },
  );
  expect(screen.getByRole('button', { name: 'Confirm check' })).toBeDisabled();
  expect(p.load).toHaveBeenCalledTimes(1);
});
it('cancels while apply is pending and retains uncertainty until a proven stopped result', async () => {
  let reject!: (cause: unknown) => void;
  const session = new SubscriptionProbesSession();
  const p = props({
    session,
    apply: vi.fn(
      () =>
        new Promise<SubscriptionProbeResult>((_, no) => {
          reject = no;
        }),
    ),
  });
  const view = render(<SubscriptionProbes {...p} />);
  await review();
  fireEvent.click(screen.getByRole('button', { name: 'Confirm check' }));
  fireEvent.click(
    screen.getByRole('button', { name: 'Cancel original check' }),
  );
  await screen.findByText(/Waiting for the original work to stop/);
  expect(screen.getByLabelText('Subscription provider')).toBeDisabled();
  const id = vi.mocked(p.apply).mock.calls[0][1];
  expect(p.cancel).toHaveBeenCalledWith(id);
  await act(async () => reject({ code: 'operation_uncertain' }));
  view.unmount();
  render(<SubscriptionProbes {...p} />);
  fireEvent.click(
    screen.getByRole('button', { name: 'Read original check receipt' }),
  );
  await screen.findByText(/No provider request was replayed/);
  expect(p.receipt).toHaveBeenCalledWith(id, expect.any(AbortSignal));
  expect(p.apply).toHaveBeenCalledTimes(1);
  expect(
    screen.getByRole('button', { name: 'Discard unsent check' }),
  ).toBeDisabled();
  expect(session.hasRetained()).toBe(true);
  vi.mocked(p.status).mockImplementation(async (command_id) => ({
    command_id,
    provider_id: 'codex',
    state: 'completed',
    quiescent: true,
    result,
  }));
  fireEvent.click(
    screen.getByRole('button', { name: 'Check original progress' }),
  );
  await screen.findByText(/confirmed and its work has stopped/);
  expect(session.hasRetained()).toBe(false);
});
it('does not unlock published receipt while the original work remains active', async () => {
  const p = props({
    apply: vi.fn().mockRejectedValue({ code: 'operation_uncertain' }),
    receipt: vi.fn<SubscriptionProbesProps['receipt']>(async (command_id) => ({
      command_id,
      provider_id: 'codex',
      status: 'completed',
      published: true,
    })),
    status: vi.fn<SubscriptionProbesProps['status']>(async (command_id) => ({
      command_id,
      provider_id: 'codex',
      state: 'draining',
      quiescent: false,
      result: null,
    })),
  });
  render(<SubscriptionProbes {...p} />);
  await review();
  fireEvent.click(screen.getByRole('button', { name: 'Confirm check' }));
  await screen.findByText(/The original outcome is unconfirmed/);
  fireEvent.click(
    screen.getByRole('button', { name: 'Read original check receipt' }),
  );
  await screen.findByText(/Waiting for the original work to stop/);
  expect(screen.getByRole('button', { name: 'Review check' })).toBeDisabled();
  expect(p.apply).toHaveBeenCalledTimes(1);
});
it('settles a late result in the retained session without calling an unmounted callback', async () => {
  let resolve!: (value: SubscriptionProbeResult) => void;
  const session = new SubscriptionProbesSession();
  const p = props({
    session,
    apply: vi.fn(
      () =>
        new Promise<SubscriptionProbeResult>((yes) => {
          resolve = yes;
        }),
    ),
  });
  const view = render(<SubscriptionProbes {...p} />);
  await review();
  fireEvent.click(screen.getByRole('button', { name: 'Confirm check' }));
  view.unmount();
  await act(async () => resolve(result));
  render(<SubscriptionProbes {...p} />);
  expect(screen.getByText(/Last result: Codex/)).toBeInTheDocument();
  expect(p.onSaved).not.toHaveBeenCalled();
  await waitFor(() => expect(session.hasRetained()).toBe(false));
});
it('purges the draft and fences late completion on authentication disposal', async () => {
  let resolve!: (value: SubscriptionProbeResult) => void;
  const session = new SubscriptionProbesSession();
  const p = props({
    session,
    apply: vi.fn(
      () =>
        new Promise<SubscriptionProbeResult>((yes) => {
          resolve = yes;
        }),
    ),
  });
  render(<SubscriptionProbes {...p} />);
  await review();
  fireEvent.click(screen.getByRole('button', { name: 'Confirm check' }));
  act(() => session.dispose());
  await act(async () => resolve(result));
  expect(p.onSaved).not.toHaveBeenCalled();
  expect(session.hasRetained()).toBe(false);
  expect(screen.queryByText(/Last result:/)).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Review check' })).toBeDisabled();
});
it('rejects a mismatched review and shows saved model text as plain content', async () => {
  const p = props({
    load: vi.fn().mockResolvedValue({
      ...snapshot,
      items: [
        {
          ...result,
          provider_id: 'xai_oauth',
          kind: 'runtime',
          model_ref: '<script>synthetic</script>',
        },
      ],
    }),
    review: vi.fn<SubscriptionProbesProps['review']>(async (intent) => ({
      ...intent,
      provider_id: 'xai_oauth',
      action_digest: 'b'.repeat(64),
      nonce: 'wrong',
    })),
  });
  render(<SubscriptionProbes {...p} />);
  await screen.findByText('<script>synthetic</script>');
  expect(document.querySelector('script')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Review check' }));
  await screen.findByRole('alert');
  expect(screen.getByRole('button', { name: 'Confirm check' })).toBeDisabled();
  expect(p.apply).not.toHaveBeenCalled();
});
it('does not settle a result or receipt for a different original intent', async () => {
  const p = props({
    apply: vi.fn().mockResolvedValue({ ...result, provider_id: 'xai_oauth' }),
    receipt: vi.fn().mockResolvedValue({
      command_id: 'different-command',
      provider_id: 'codex',
      status: 'completed',
      published: true,
    }),
  });
  render(<SubscriptionProbes {...p} />);
  await review();
  fireEvent.click(screen.getByRole('button', { name: 'Confirm check' }));
  await screen.findByText(/The original outcome is unconfirmed/);
  fireEvent.click(
    screen.getByRole('button', { name: 'Read original check receipt' }),
  );
  await waitFor(() => expect(p.receipt).toHaveBeenCalledTimes(1));
  expect(p.status).not.toHaveBeenCalled();
  expect(
    screen.getByRole('button', { name: 'Discard unsent check' }),
  ).toBeDisabled();
  expect(p.onSaved).not.toHaveBeenCalled();
});
