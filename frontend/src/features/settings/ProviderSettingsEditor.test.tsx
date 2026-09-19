import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { ProviderSettingsSnapshot } from '../../api/types';
import ProviderSettingsEditor from './ProviderSettingsEditor';
import { ProviderSettingsSession } from './provider-settings-sessions';

const snapshot: ProviderSettingsSnapshot = {
  schema_version: 1,
  provider_id: 'openai',
  display_name: 'OpenAI',
  revision: 'a'.repeat(64),
  configured: true,
  source: 'keyring',
  externally_managed: false,
  storage_unavailable: false,
  recovery_available: true,
  runtime_state: 'unknown',
};
function pending<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
function props() {
  return {
    providerId: 'openai',
    load: vi.fn().mockResolvedValue(snapshot),
    review: vi.fn().mockResolvedValue(snapshot),
    apply: vi.fn().mockResolvedValue({ ...snapshot, revision: 'b'.repeat(64) }),
    receipt: vi.fn().mockResolvedValue(null),
    onSaved: vi.fn(),
    onCancel: vi.fn(),
  };
}

it('saves a key from the compact provider dialog with one explicit action', async () => {
  const options = props();
  render(<ProviderSettingsEditor {...options} compact />);
  const input = await screen.findByLabelText('API key');
  fireEvent.change(input, { target: { value: 'synthetic-private-input' } });
  expect(screen.queryByRole('button', { name: 'Review change' })).toBeNull();
  expect(
    screen.queryByRole('button', { name: 'Reload saved status' }),
  ).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Replace key' }));
  await waitFor(() => expect(options.apply).toHaveBeenCalledOnce());
  expect(options.review).toHaveBeenCalledWith(
    'openai',
    snapshot.revision,
    'save',
    'synthetic-private-input',
    expect.any(AbortSignal),
  );
  expect(options.apply).toHaveBeenCalledWith(
    'openai',
    snapshot.revision,
    'save',
    'synthetic-private-input',
    expect.any(String),
  );
  expect(input).toHaveValue('');
  expect(options.onSaved).toHaveBeenCalledOnce();
  expect(document.body.textContent).not.toContain('synthetic-private-input');
});

it('retains the exact reviewed private draft across a full editor remount', async () => {
  const options = props(),
    session = new ProviderSettingsSession('openai');
  const first = render(
    <ProviderSettingsEditor {...options} session={session} />,
  );
  await enterAndReview();
  first.unmount();
  render(<ProviderSettingsEditor {...options} session={session} />);
  expect(screen.getByLabelText('New API key')).toHaveValue(
    'synthetic-private-input',
  );
  expect(
    screen.getByRole('button', { name: 'Confirm replacement' }),
  ).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: 'Confirm replacement' }));
  await screen.findByText(
    'Saved locally. Provider connectivity has not been tested.',
  );
  expect(options.load).toHaveBeenCalledOnce();
  expect(options.review).toHaveBeenCalledOnce();
  expect(options.apply).toHaveBeenCalledOnce();
  expect(session.retained()).toBe(false);
});

it('settles an in-flight result after unmount and preserves the original receipt identity on failure', async () => {
  const options = props(),
    session = new ProviderSettingsSession('openai'),
    response = pending<ProviderSettingsSnapshot>();
  options.apply.mockReturnValue(response.promise);
  const first = render(
    <ProviderSettingsEditor {...options} session={session} />,
  );
  await enterAndReview();
  fireEvent.click(screen.getByRole('button', { name: 'Confirm replacement' }));
  const originalId = options.apply.mock.calls[0][4];
  first.unmount();
  await act(async () => response.reject({ code: 'operation_uncertain' }));
  render(<ProviderSettingsEditor {...options} session={session} />);
  expect(screen.getByLabelText('New API key')).toHaveValue('');
  expect(
    screen.getByRole('button', { name: 'Reload saved status' }),
  ).toBeDisabled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Check original receipt' }),
  );
  await waitFor(() =>
    expect(options.receipt).toHaveBeenCalledWith(
      'openai',
      originalId,
      expect.any(AbortSignal),
    ),
  );
  expect(options.apply).toHaveBeenCalledOnce();
  expect(options.onSaved).not.toHaveBeenCalled();
  expect(session.canDiscard()).toBe(false);
});

it('updates a retained session after late success without notifying an unmounted view', async () => {
  const options = props(),
    session = new ProviderSettingsSession('openai'),
    response = pending<ProviderSettingsSnapshot>();
  options.apply.mockReturnValue(response.promise);
  const first = render(
    <ProviderSettingsEditor {...options} session={session} />,
  );
  await enterAndReview();
  fireEvent.click(screen.getByRole('button', { name: 'Confirm replacement' }));
  first.unmount();
  await act(async () =>
    response.resolve({ ...snapshot, revision: 'b'.repeat(64) }),
  );
  render(<ProviderSettingsEditor {...options} session={session} />);
  expect(
    screen.queryByRole('button', { name: 'Check original receipt' }),
  ).not.toBeInTheDocument();
  expect(session.retained()).toBe(false);
  expect(options.onSaved).not.toHaveBeenCalled();
  expect(options.load).toHaveBeenCalledOnce();
});

it('does not call a rejected receipt a successful replacement and unlocks fresh review', async () => {
  const options = props(),
    session = new ProviderSettingsSession('openai');
  options.apply.mockRejectedValue({ code: 'revision_conflict' });
  options.receipt.mockResolvedValue({ rejected: true, snapshot });
  render(<ProviderSettingsEditor {...options} session={session} />);
  await enterAndReview();
  fireEvent.click(screen.getByRole('button', { name: 'Confirm replacement' }));
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: 'Check original receipt' }),
    ).toBeEnabled(),
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Check original receipt' }),
  );
  await screen.findByText(
    'The original change was rejected. Review the current saved status before trying again.',
  );
  expect(options.onSaved).not.toHaveBeenCalled();
  expect(
    screen.getByRole('button', { name: 'Reload saved status' }),
  ).toBeEnabled();
  expect(session.canDiscard()).toBe(true);
});
async function enterAndReview() {
  fireEvent.change(await screen.findByLabelText('New API key'), {
    target: { value: 'synthetic-private-input' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Review change' }));
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: 'Confirm replacement' }),
    ).toBeEnabled(),
  );
}

it('loads redacted saved status and requires explicit review before replacement', async () => {
  const options = props();
  render(<ProviderSettingsEditor {...options} />);
  const input = await screen.findByLabelText('New API key');
  expect(input).toHaveAttribute('type', 'password');
  expect(input).toHaveAttribute('autocomplete', 'new-password');
  expect(input).toHaveValue('');
  expect(options.apply).not.toHaveBeenCalled();
  expect(
    screen.getByRole('button', { name: 'Confirm replacement' }),
  ).toBeDisabled();
  await enterAndReview();
  expect(options.review).toHaveBeenCalledWith(
    'openai',
    snapshot.revision,
    'save',
    'synthetic-private-input',
    expect.any(AbortSignal),
  );
  expect(document.body.textContent).not.toContain('synthetic-private-input');
  fireEvent.click(screen.getByRole('button', { name: 'Confirm replacement' }));
  await screen.findByText(
    'Saved locally. Provider connectivity has not been tested.',
  );
  expect(options.apply).toHaveBeenCalledWith(
    'openai',
    snapshot.revision,
    'save',
    'synthetic-private-input',
    expect.any(String),
  );
  expect(input).toHaveValue('');
  expect(options.onSaved).toHaveBeenCalledOnce();
});

it('coalesces double confirmation and clears password input before awaiting the response', async () => {
  const options = props(),
    response = pending<ProviderSettingsSnapshot>();
  options.apply.mockReturnValue(response.promise);
  render(<ProviderSettingsEditor {...options} />);
  await enterAndReview();
  const button = screen.getByRole('button', { name: 'Confirm replacement' });
  fireEvent.click(button);
  fireEvent.click(button);
  expect(options.apply).toHaveBeenCalledOnce();
  expect(screen.getByLabelText('New API key')).toHaveValue('');
  expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
  await act(async () => response.resolve(snapshot));
});

it('keeps uncertain identity and only reads the original receipt without resending the secret', async () => {
  const options = props();
  options.apply.mockRejectedValue(new Error('private backend details'));
  render(<ProviderSettingsEditor {...options} />);
  await enterAndReview();
  fireEvent.click(screen.getByRole('button', { name: 'Confirm replacement' }));
  const receipt = await screen.findByRole('button', {
    name: 'Check original receipt',
  });
  await waitFor(() => expect(receipt).toBeEnabled());
  const identity = options.apply.mock.calls[0][4];
  fireEvent.click(receipt);
  await screen.findByText(
    'No completed receipt is available. The original change has not been sent again.',
  );
  expect(options.receipt).toHaveBeenCalledWith(
    'openai',
    identity,
    expect.any(AbortSignal),
  );
  expect(options.apply).toHaveBeenCalledOnce();
  expect(document.body.textContent).not.toContain('private backend details');
  options.receipt.mockResolvedValue(snapshot);
  fireEvent.click(receipt);
  await screen.findByText(
    'The original change is confirmed. Provider connectivity has not been tested.',
  );
  expect(options.apply).toHaveBeenCalledOnce();
});

it.each(['clear', 'restore'] as const)(
  'reviews and explicitly confirms %s without sending a secret value',
  async (operation) => {
    const options = props();
    render(<ProviderSettingsEditor {...options} />);
    fireEvent.change(await screen.findByLabelText('Credential action'), {
      target: { value: operation },
    });
    expect(screen.queryByLabelText('New API key')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Review change' }));
    const button = screen.getByRole('button', {
      name: operation === 'clear' ? 'Confirm disconnect' : 'Confirm restore',
    });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    await waitFor(() => expect(options.apply).toHaveBeenCalledOnce());
    expect(options.apply.mock.calls[0].slice(0, 4)).toEqual([
      'openai',
      snapshot.revision,
      operation,
      undefined,
    ]);
  },
);

it.each([{ externally_managed: true }, { storage_unavailable: true }])(
  'disables credential changes for unavailable authority %j',
  async (state) => {
    const options = props();
    options.load.mockResolvedValue({ ...snapshot, ...state });
    render(<ProviderSettingsEditor {...options} />);
    expect(await screen.findByLabelText('New API key')).toBeDisabled();
    expect(
      screen.getByRole('button', { name: 'Review change' }),
    ).toBeDisabled();
    expect(options.apply).not.toHaveBeenCalled();
  },
);

it('allows explicit recovery of an unavailable active generation through the existing previous-value owner', async () => {
  const options = props();
  options.load.mockResolvedValue({ ...snapshot, storage_unavailable: true });
  options.review.mockResolvedValue({ ...snapshot, storage_unavailable: true });
  render(<ProviderSettingsEditor {...options} />);
  fireEvent.change(await screen.findByLabelText('Credential action'), {
    target: { value: 'restore' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Review change' }));
  const button = screen.getByRole('button', { name: 'Confirm restore' });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  await waitFor(() => expect(options.apply).toHaveBeenCalledOnce());
  expect(options.apply.mock.calls[0][2]).toBe('restore');
});

it('rejects a stale review and changing input invalidates a completed review', async () => {
  const options = props();
  options.review.mockResolvedValueOnce({ ...snapshot, revision: 'changed' });
  render(<ProviderSettingsEditor {...options} />);
  fireEvent.change(await screen.findByLabelText('New API key'), {
    target: { value: 'one' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Review change' }));
  await screen.findByRole('alert');
  expect(
    screen.getByRole('button', { name: 'Confirm replacement' }),
  ).toBeDisabled();
  await enterAndReview();
  fireEvent.change(screen.getByLabelText('New API key'), {
    target: { value: 'different' },
  });
  expect(
    screen.getByRole('button', { name: 'Confirm replacement' }),
  ).toBeDisabled();
  expect(options.apply).not.toHaveBeenCalled();
});

it('aborts late provider reads and never applies the previous provider result', async () => {
  const options = props(),
    response = pending<ProviderSettingsSnapshot>();
  options.load.mockReturnValueOnce(response.promise);
  const view = render(<ProviderSettingsEditor {...options} />);
  const firstSignal = options.load.mock.calls[0][1] as AbortSignal;
  options.load.mockResolvedValue({
    ...snapshot,
    provider_id: 'anthropic',
    display_name: 'Anthropic',
  });
  view.rerender(<ProviderSettingsEditor {...options} providerId="anthropic" />);
  await screen.findByRole('heading', { name: 'Anthropic credentials' });
  await act(async () => response.resolve(snapshot));
  expect(firstSignal.aborted).toBe(true);
  expect(screen.queryByText('OpenAI credentials')).not.toBeInTheDocument();
});

it('fences save results after unmount and cancellation before submit never writes', async () => {
  const options = props(),
    response = pending<ProviderSettingsSnapshot>();
  options.apply.mockReturnValue(response.promise);
  const view = render(<ProviderSettingsEditor {...options} />);
  await enterAndReview();
  fireEvent.click(screen.getByRole('button', { name: 'Confirm replacement' }));
  view.unmount();
  await act(async () => response.resolve(snapshot));
  expect(options.onSaved).not.toHaveBeenCalled();
  const next = props();
  render(<ProviderSettingsEditor {...next} />);
  await screen.findByLabelText('New API key');
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(next.onCancel).toHaveBeenCalledOnce();
  expect(next.apply).not.toHaveBeenCalled();
});
