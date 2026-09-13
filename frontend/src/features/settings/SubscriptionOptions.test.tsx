import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import SubscriptionOptions, {
  SubscriptionOptionsSession,
  type SubscriptionOptionsProps,
  type SubscriptionOptionsSnapshot,
} from './SubscriptionOptions';

const snapshot: SubscriptionOptionsSnapshot = {
  schema_version: 1,
  revision: 'a'.repeat(64),
  references: [
    { provider_id: 'codex', metadata_saved: false },
    { provider_id: 'claude_subscription', metadata_saved: false },
  ],
  xai_client_id_source: 'default',
  xai_saved_client_id: null,
  xai_default_available: true,
  runtime_state: 'unknown',
};
function props(
  overrides: Partial<SubscriptionOptionsProps> = {},
): SubscriptionOptionsProps {
  return {
    load: vi.fn().mockResolvedValue(snapshot),
    review: vi.fn<SubscriptionOptionsProps['review']>(async (intent) => ({
      ...intent,
      action_digest: 'b'.repeat(64),
      nonce: 'original-nonce',
      ...(intent.operation === 'reference'
        ? { reference_digest: 'c'.repeat(64) }
        : {}),
    })),
    apply: vi.fn().mockResolvedValue(snapshot),
    receipt: vi.fn<SubscriptionOptionsProps['receipt']>(async (id) => ({
      command_id: id,
      status: 'uncertain',
      published: false,
      options: snapshot,
    })),
    onSaved: vi.fn(),
    ...overrides,
  };
}
async function reviewReference() {
  fireEvent.click(
    await screen.findByRole('button', { name: 'Review Codex CLI reference' }),
  );
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: 'Confirm account option' }),
    ).toBeEnabled(),
  );
}
it('loads metadata passively and confirms one exact reviewed reference', async () => {
  const p = props();
  render(<SubscriptionOptions {...p} />);
  await screen.findByText(/xAI OAuth client source: default/);
  expect(p.review).not.toHaveBeenCalled();
  expect(p.apply).not.toHaveBeenCalled();
  await reviewReference();
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm account option' }),
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm account option' }),
  );
  await screen.findByText(
    'CLI reference saved as metadata only. No credentials were imported.',
  );
  expect(p.apply).toHaveBeenCalledTimes(1);
  expect(vi.mocked(p.apply).mock.calls[0][0]).toMatchObject({
    nonce: 'original-nonce',
    reference_digest: 'c'.repeat(64),
    operation: 'reference',
  });
});
it('retains an unsent client option across remount and invalidates its review on edit', async () => {
  const session = new SubscriptionOptionsSession();
  const p = props({ session });
  const view = render(<SubscriptionOptions {...p} />);
  await screen.findByText(/xAI OAuth client source:/);
  fireEvent.change(screen.getByLabelText('xAI OAuth client ID override'), {
    target: { value: 'synthetic-client' },
  });
  fireEvent.click(
    screen.getByRole('button', { name: 'Review client ID override' }),
  );
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: 'Confirm account option' }),
    ).toBeEnabled(),
  );
  expect(session.hasRetained()).toBe(true);
  view.unmount();
  render(<SubscriptionOptions {...p} />);
  expect(screen.getByLabelText('xAI OAuth client ID override')).toHaveValue(
    'synthetic-client',
  );
  expect(
    screen.getByRole('button', { name: 'Confirm account option' }),
  ).toBeEnabled();
  fireEvent.change(screen.getByLabelText('xAI OAuth client ID override'), {
    target: { value: 'changed-client' },
  });
  expect(
    screen.getByRole('button', { name: 'Confirm account option' }),
  ).toBeDisabled();
  expect(p.load).toHaveBeenCalledTimes(1);
});
it('keeps an uncertain original intent locked after remount and never replays it', async () => {
  const session = new SubscriptionOptionsSession();
  const p = props({
    session,
    apply: vi.fn().mockRejectedValue({ code: 'operation_uncertain' }),
  });
  const view = render(<SubscriptionOptions {...p} />);
  await reviewReference();
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm account option' }),
  );
  await screen.findByText(/The original outcome is unconfirmed/);
  const id = vi.mocked(p.apply).mock.calls[0][1];
  view.unmount();
  render(<SubscriptionOptions {...p} />);
  fireEvent.click(
    screen.getByRole('button', { name: 'Read original option receipt' }),
  );
  await screen.findByText(/The original outcome remains unconfirmed/);
  expect(p.receipt).toHaveBeenCalledWith(id, expect.any(AbortSignal));
  expect(p.apply).toHaveBeenCalledTimes(1);
  expect(screen.getByLabelText('xAI OAuth client ID override')).toBeDisabled();
  expect(
    screen.getByRole('button', { name: 'Discard unsent option' }),
  ).toBeDisabled();
});
it('settles an in-flight command in the retained session after panel unmount', async () => {
  let resolve!: (value: SubscriptionOptionsSnapshot) => void;
  const session = new SubscriptionOptionsSession();
  const p = props({
    session,
    apply: vi.fn(
      () =>
        new Promise<SubscriptionOptionsSnapshot>((done) => {
          resolve = done;
        }),
    ),
  });
  const view = render(<SubscriptionOptions {...p} />);
  await reviewReference();
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm account option' }),
  );
  view.unmount();
  await act(async () =>
    resolve({
      ...snapshot,
      references: [
        { provider_id: 'codex', metadata_saved: true },
        snapshot.references[1],
      ],
    }),
  );
  render(<SubscriptionOptions {...p} />);
  expect(
    screen.getByText('Codex CLI: metadata reference saved'),
  ).toBeInTheDocument();
  expect(p.onSaved).not.toHaveBeenCalled();
  expect(session.hasRetained()).toBe(false);
});
it('purges retained fields and fences late completion after authentication disposal', async () => {
  let resolve!: (value: SubscriptionOptionsSnapshot) => void;
  const session = new SubscriptionOptionsSession();
  const p = props({
    session,
    apply: vi.fn(
      () =>
        new Promise<SubscriptionOptionsSnapshot>((done) => {
          resolve = done;
        }),
    ),
  });
  render(<SubscriptionOptions {...p} />);
  await reviewReference();
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm account option' }),
  );
  act(() => session.dispose());
  await act(async () => resolve(snapshot));
  expect(p.onSaved).not.toHaveBeenCalled();
  expect(session.hasRetained()).toBe(false);
  expect(
    screen.queryByText(/xAI OAuth client source:/),
  ).not.toBeInTheDocument();
});
it('rejects a mismatched review and renders client IDs as plain text', async () => {
  const p = props({
    review: vi.fn<SubscriptionOptionsProps['review']>(async (intent) => ({
      ...intent,
      value: 'different',
      action_digest: 'b'.repeat(64),
      nonce: 'original-nonce',
    })),
  });
  render(<SubscriptionOptions {...p} />);
  await screen.findByText(/xAI OAuth client source:/);
  fireEvent.change(screen.getByLabelText('xAI OAuth client ID override'), {
    target: { value: '<script>synthetic</script>' },
  });
  fireEvent.click(
    screen.getByRole('button', { name: 'Review client ID override' }),
  );
  await screen.findByRole('alert');
  expect(
    screen.getByRole('button', { name: 'Confirm account option' }),
  ).toBeDisabled();
  expect(document.querySelector('script')).toBeNull();
  expect(p.apply).not.toHaveBeenCalled();
});
