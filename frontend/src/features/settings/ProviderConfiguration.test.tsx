import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import ProviderConfiguration, {
  ProviderConfigurationSession,
} from './ProviderConfiguration';
import type {
  ProviderConfigurationPage,
  ProviderEndpointFields,
} from '../../api/types';

const fields: ProviderEndpointFields = {
  endpoint_id: 'synthetic',
  display_name: 'Synthetic endpoint',
  base_url: 'http://127.0.0.1:8123/v1',
  profile: 'generic_openai',
  execution_location: 'local',
  enabled: true,
  auth_required: false,
  vision_mode: 'auto',
  tool_mode: 'auto',
  context_window: null,
  reasoning_mode: 'auto',
  thinking_budget: null,
  supports_reasoning_content: false,
  supports_reasoning_replay: false,
  extra_body_json: '{}',
};
const page: ProviderConfigurationPage = {
  schema_version: 1,
  revision: 'a'.repeat(64),
  items: [
    {
      provider_id: 'custom_openai_synthetic',
      fields,
      probe_state: 'unknown',
      model_count: null,
      runtime_state: 'unknown',
    },
  ],
  total: 1,
  next_cursor: null,
  profiles: ['generic_openai', 'vllm'],
};
function options() {
  return {
    load: vi.fn().mockResolvedValue(page),
    review: vi.fn(async (operation: string, revision: string) => ({
      operation,
      configuration_revision: revision,
      action_digest: 'digest',
      nonce: 'original-session-nonce',
    })),
    apply: vi
      .fn()
      .mockResolvedValue({ configuration_revision: 'b'.repeat(64) }),
    receipt: vi.fn().mockResolvedValue('uncertain'),
    onSaved: vi.fn(),
    onCredentials: vi.fn(),
  };
}
it('runs a custom endpoint probe from its compact row action and reloads the result', async () => {
  const props = options();
  props.load.mockResolvedValueOnce(page).mockResolvedValue({
    ...page,
    items: [
      {
        ...page.items[0],
        probe_state: 'agent_ready',
        probe_components: [{ name: 'Tool round trip', status: 'ok' }],
      },
    ],
  });
  render(<ProviderConfiguration {...props} compact />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Probe Synthetic endpoint' }),
  );
  await waitFor(() =>
    expect(props.apply).toHaveBeenCalledWith(
      'provider.endpoint.probe',
      page.revision,
      { endpoint_id: 'synthetic' },
      expect.any(String),
      expect.objectContaining({ nonce: 'original-session-nonce' }),
    ),
  );
  expect(await screen.findByText('agent ready')).toBeVisible();
  fireEvent.click(
    screen.getByRole('button', {
      name: 'Show Synthetic endpoint probe details',
    }),
  );
  expect(screen.getByText('Tool round trip: ok')).toBeVisible();
  expect(
    screen.queryByRole('button', { name: 'Review configuration' }),
  ).toBeNull();
});
it('refreshes a saved no-key endpoint using the reviewed endpoint operation', async () => {
  const props = options();
  render(<ProviderConfiguration {...props} compact />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Edit Synthetic endpoint' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  await waitFor(() => expect(props.apply).toHaveBeenCalledTimes(2));
  expect(vi.mocked(props.apply).mock.calls.map((call) => call[0])).toEqual([
    'provider.endpoint.save',
    'provider.endpoint.refresh',
  ]);
  expect(props.review).toHaveBeenNthCalledWith(
    2,
    'provider.endpoint.refresh',
    page.revision,
    { endpoint_id: 'synthetic' },
  );
  expect(
    await screen.findByText('Endpoint saved and models refreshed.'),
  ).toBeVisible();
});
it('opens the credential dialog after creating an endpoint that needs a key', async () => {
  const props = options();
  render(<ProviderConfiguration {...props} compact />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Add custom endpoint' }),
  );
  fireEvent.change(screen.getByLabelText('Endpoint id'), {
    target: { value: 'keyed' },
  });
  fireEvent.change(screen.getByLabelText('Display name'), {
    target: { value: 'Keyed endpoint' },
  });
  fireEvent.change(screen.getByLabelText('Base URL'), {
    target: { value: 'https://example.invalid/v1' },
  });
  fireEvent.click(screen.getByLabelText('API key required'));
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  await waitFor(() =>
    expect(props.onCredentials).toHaveBeenCalledWith('custom_openai_keyed'),
  );
  expect(vi.mocked(props.apply).mock.calls).toHaveLength(1);
});
it('refreshes endpoint models once after a new API key was saved', async () => {
  const props = options();
  const view = render(<ProviderConfiguration {...props} compact />);
  await screen.findByRole('button', {
    name: 'Refresh Synthetic endpoint models',
  });
  view.rerender(
    <ProviderConfiguration
      {...props}
      compact
      credentialRefreshRequest={{
        providerId: 'custom_openai_synthetic',
        token: 1,
      }}
    />,
  );
  await waitFor(() =>
    expect(props.apply).toHaveBeenCalledWith(
      'provider.endpoint.refresh',
      page.revision,
      { endpoint_id: 'synthetic' },
      expect.any(String),
      expect.objectContaining({ nonce: 'original-session-nonce' }),
    ),
  );
  view.rerender(
    <ProviderConfiguration
      {...props}
      compact
      credentialRefreshRequest={{
        providerId: 'custom_openai_synthetic',
        token: 1,
      }}
    />,
  );
  expect(props.apply).toHaveBeenCalledOnce();
});
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (cause: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
async function edit() {
  fireEvent.click(
    await screen.findByRole('button', { name: 'Edit Synthetic endpoint' }),
  );
}
async function review() {
  fireEvent.click(screen.getByRole('button', { name: 'Review configuration' }));
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: 'Confirm configuration' }),
    ).toBeEnabled(),
  );
}

it('reads saved status without implicit probes, secrets or configuration changes', async () => {
  const props = options();
  render(<ProviderConfiguration {...props} />);
  expect(
    await screen.findByRole('heading', {
      name: 'Custom / Self-Hosted Endpoints',
    }),
  ).toBeVisible();
  expect(screen.getByText(fields.base_url)).toBeVisible();
  expect(
    screen.getByLabelText('Synthetic endpoint saved configuration'),
  ).toHaveTextContent('Enabledlocalgeneric_openaimodels unknown');
  expect(screen.getByText('Not checked')).toBeVisible();
  await edit();
  expect(screen.getByLabelText('Base URL')).toHaveValue(fields.base_url);
  expect(screen.getByLabelText('Endpoint ID')).toBeDisabled();
  expect(screen.getByLabelText('Endpoint profile')).toBeDisabled();
  expect(screen.getByLabelText('Execution location')).toBeDisabled();
  expect(document.querySelector('input[type=password]')).toBeNull();
  expect(props.apply).not.toHaveBeenCalled();
  expect(props.review).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Manage credentials' }));
  expect(props.onCredentials).toHaveBeenCalledOnce();
});

it('creates through the distinct collision-safe action with editable endpoint fields', async () => {
  const props = options();
  render(<ProviderConfiguration {...props} />);
  fireEvent.click(await screen.findByRole('button', { name: 'New endpoint' }));
  fireEvent.change(screen.getByLabelText('Endpoint ID'), {
    target: { value: 'new-endpoint' },
  });
  fireEvent.change(screen.getByLabelText('Display name'), {
    target: { value: 'New endpoint label' },
  });
  fireEvent.change(screen.getByLabelText('Base URL'), {
    target: { value: 'http://127.0.0.1:8124/v1' },
  });
  expect(screen.getByLabelText('Display name')).toBeEnabled();
  expect(screen.getByLabelText('Base URL')).toBeEnabled();
  await review();
  expect(props.review).toHaveBeenCalledWith(
    'provider.endpoint.create',
    page.revision,
    expect.objectContaining({
      endpoint_id: 'new-endpoint',
      display_name: 'New endpoint label',
      base_url: 'http://127.0.0.1:8124/v1',
    }),
    expect.any(AbortSignal),
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm configuration' }),
  );
  await waitFor(() =>
    expect(props.apply).toHaveBeenCalledWith(
      'provider.endpoint.create',
      page.revision,
      expect.objectContaining({ endpoint_id: 'new-endpoint' }),
      expect.any(String),
      expect.objectContaining({ nonce: 'original-session-nonce' }),
    ),
  );
});

it('reviews and saves exact advanced endpoint fields once without starting a model', async () => {
  const props = options();
  render(<ProviderConfiguration {...props} />);
  await edit();
  fireEvent.change(screen.getByLabelText('Display name'), {
    target: { value: 'Renamed endpoint' },
  });
  fireEvent.click(screen.getByText('Advanced capabilities and reasoning'));
  fireEvent.change(screen.getByLabelText('Reasoning mode'), {
    target: { value: 'on' },
  });
  fireEvent.change(screen.getByLabelText('Thinking budget'), {
    target: { value: '256' },
  });
  await review();
  expect(props.review).toHaveBeenCalledWith(
    'provider.endpoint.save',
    page.revision,
    expect.objectContaining({
      display_name: 'Renamed endpoint',
      reasoning_mode: 'on',
      thinking_budget: 256,
    }),
    expect.any(AbortSignal),
  );
  const button = screen.getByRole('button', { name: 'Confirm configuration' });
  fireEvent.click(button);
  fireEvent.click(button);
  await screen.findByText('Configuration saved. No model was started.');
  expect(props.apply).toHaveBeenCalledOnce();
  expect(props.onSaved).toHaveBeenCalledOnce();
});

it('invalidates review on field edits and exposes explicit network effect scope', async () => {
  const props = options();
  render(<ProviderConfiguration {...props} />);
  await edit();
  await review();
  fireEvent.change(screen.getByLabelText('Configuration action'), {
    target: { value: 'provider.endpoint.probe' },
  });
  expect(
    screen.getByRole('button', { name: 'Confirm configuration' }),
  ).toBeDisabled();
  expect(
    screen.getByText(
      /contacts the saved endpoint and may consume provider quota/,
    ),
  ).toBeInTheDocument();
  expect(props.apply).not.toHaveBeenCalled();
  await review();
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm configuration' }),
  );
  await waitFor(() =>
    expect(props.apply).toHaveBeenCalledWith(
      'provider.endpoint.probe',
      page.revision,
      { endpoint_id: 'synthetic' },
      expect.any(String),
      expect.objectContaining({ action_digest: expect.any(String) }),
    ),
  );
});

it('keeps unknown readiness truthful and preserves provider-qualified model picker intent', async () => {
  const props = options();
  render(<ProviderConfiguration {...props} />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Model picker settings' }),
  );
  fireEvent.change(screen.getByLabelText('Provider ID'), {
    target: { value: 'openai' },
  });
  fireEvent.change(screen.getByLabelText('Exact model ID'), {
    target: { value: 'same-model' },
  });
  fireEvent.change(screen.getByLabelText('Picker'), {
    target: { value: 'vision' },
  });
  await review();
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm configuration' }),
  );
  await waitFor(() =>
    expect(props.apply).toHaveBeenCalledWith(
      'provider.model.pin',
      page.revision,
      { provider_id: 'openai', model_id: 'same-model', surface: 'vision' },
      expect.any(String),
      expect.objectContaining({ action_digest: expect.any(String) }),
    ),
  );
});

it('retains a reviewed draft and late uncertain command across full remount without replay', async () => {
  const props = options(),
    session = new ProviderConfigurationSession(),
    pending = deferred<{ configuration_revision: string }>();
  props.apply.mockReturnValue(pending.promise);
  const first = render(<ProviderConfiguration {...props} session={session} />);
  await edit();
  fireEvent.change(screen.getByLabelText('Display name'), {
    target: { value: 'Retained draft' },
  });
  await review();
  first.unmount();
  const second = render(<ProviderConfiguration {...props} session={session} />);
  expect(screen.getByLabelText('Display name')).toHaveValue('Retained draft');
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm configuration' }),
  );
  const originalId = props.apply.mock.calls[0][3];
  expect(props.review).toHaveBeenCalledOnce();
  expect(props.apply.mock.calls[0][4]).toEqual({
    operation: 'provider.endpoint.save',
    configuration_revision: page.revision,
    action_digest: 'digest',
    nonce: 'original-session-nonce',
  });
  expect(session.getSnapshot().pending?.review.nonce).toBe(
    'original-session-nonce',
  );
  second.unmount();
  await act(async () => pending.reject({ code: 'operation_uncertain' }));
  render(<ProviderConfiguration {...props} session={session} />);
  expect(
    screen.getByRole('button', { name: 'Discard unsent changes' }),
  ).toBeDisabled();
  fireEvent.click(
    screen.getByRole('button', {
      name: 'Check original configuration receipt',
    }),
  );
  await waitFor(() =>
    expect(props.receipt).toHaveBeenCalledWith(
      originalId,
      expect.any(AbortSignal),
    ),
  );
  expect(props.apply).toHaveBeenCalledOnce();
  expect(props.load).toHaveBeenCalledOnce();
  expect(props.onSaved).not.toHaveBeenCalled();
});

it('clears private draft state and ignores late completion after authentication owner disposal', async () => {
  const props = options(),
    session = new ProviderConfigurationSession(),
    pending = deferred<{ configuration_revision: string }>();
  props.apply.mockReturnValue(pending.promise);
  render(<ProviderConfiguration {...props} session={session} />);
  await edit();
  await review();
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm configuration' }),
  );
  act(() => session.dispose());
  await act(async () =>
    pending.resolve({ configuration_revision: 'b'.repeat(64) }),
  );
  expect(session.getSnapshot().fields.endpoint_id).toBe('');
  expect(session.getSnapshot().pending).toBeNull();
  expect(session.hasRetained()).toBe(false);
  expect(props.onSaved).not.toHaveBeenCalled();
});

it('pages forward without accumulating rows and rejects stale page responses', async () => {
  const props = options();
  props.load
    .mockResolvedValueOnce({ ...page, next_cursor: 'next', total: 2 })
    .mockResolvedValueOnce({ ...page, revision: 'b'.repeat(64) });
  render(<ProviderConfiguration {...props} />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Next endpoint page' }),
  );
  await screen.findByRole('alert');
  expect(props.load).toHaveBeenLastCalledWith(
    '',
    'next',
    expect.any(AbortSignal),
  );
  expect(
    screen.getAllByRole('button', { name: 'Edit Synthetic endpoint' }),
  ).toHaveLength(1);
});

it('labels rejected receipts as rejection and allows explicit unsent discard', async () => {
  const props = options();
  props.apply.mockRejectedValue({ code: 'revision_conflict' });
  props.receipt.mockResolvedValue('rejected');
  render(<ProviderConfiguration {...props} />);
  await edit();
  await review();
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm configuration' }),
  );
  await waitFor(() =>
    expect(
      screen.getByRole('button', {
        name: 'Check original configuration receipt',
      }),
    ).toBeEnabled(),
  );
  fireEvent.click(
    screen.getByRole('button', {
      name: 'Check original configuration receipt',
    }),
  );
  await screen.findByText(
    'The original change was rejected. Reload saved status before a fresh review.',
  );
  expect(props.onSaved).not.toHaveBeenCalled();
  expect(
    screen.getByRole('button', { name: 'Discard unsent changes' }),
  ).toBeEnabled();
});
