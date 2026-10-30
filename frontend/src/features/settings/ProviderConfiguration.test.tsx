import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import ProviderConfiguration, {
  ProviderConfigurationSession,
} from './ProviderConfiguration';
import type {
  ProviderConfigurationPage,
  ProviderEndpointFields,
} from '../../api/types';

/** Compact endpoint rows keep Refresh visible; the rest is in their ⋯. */
async function chooseEndpoint(item: string) {
  const more = await screen.findByRole('button', {
    name: 'More actions for Synthetic endpoint',
  });
  await act(async () => fireEvent.keyDown(more, { key: 'Enter' }));
  await act(async () =>
    fireEvent.click(screen.getByRole('menuitem', { name: item })),
  );
}

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
  allow_private_address: false,
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
  profiles: [
    { id: 'generic_openai', name: 'OpenAI-compatible' },
    { id: 'llama_cpp', name: 'llama.cpp' },
    { id: 'vllm', name: 'vLLM' },
  ],
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
  await chooseEndpoint('Probe Synthetic endpoint');
  await waitFor(() =>
    expect(props.apply).toHaveBeenCalledWith(
      'provider.endpoint.probe',
      page.revision,
      { endpoint_id: 'synthetic' },
      expect.any(String),
      expect.objectContaining({ nonce: 'original-session-nonce' }),
    ),
  );
  expect(await screen.findByText('Agent ready')).toBeVisible();
  await chooseEndpoint('Show Synthetic endpoint probe details');
  expect(screen.getByText('Tool round trip: ok')).toBeVisible();
  expect(
    screen.queryByRole('button', { name: 'Review configuration' }),
  ).toBeNull();
});
it('refreshes a saved no-key endpoint using the reviewed endpoint operation', async () => {
  const props = options();
  render(<ProviderConfiguration {...props} compact />);
  await chooseEndpoint('Edit Synthetic endpoint');
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
  expect(screen.getByTestId('shared-dialog-task')).toHaveAttribute(
    'data-overlay-kind',
    'dialog',
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
  // Saving the key changed the configuration: the refresh is reviewed against it.
  const saved = { ...page, revision: 'c'.repeat(64) };
  props.load.mockResolvedValueOnce(page).mockResolvedValue(saved);
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
      saved.revision,
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
async function save(label = 'Save endpoint') {
  fireEvent.click(screen.getByRole('button', { name: label }));
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
  ).toHaveTextContent('EnabledlocalOpenAI-compatiblemodels unknown');
  expect(screen.getByText('Not checked')).toBeVisible();
  await edit();
  expect(screen.getByLabelText('Base URL')).toHaveValue(fields.base_url);
  expect(screen.getByLabelText('Endpoint ID')).toBeDisabled();
  expect(screen.getByLabelText('Server type')).toBeDisabled();
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
  await save('Create endpoint');
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

it('saves exact advanced endpoint fields without starting a model', async () => {
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
  await save();
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
  await screen.findByText(
    /Endpoint settings saved|Endpoint saved and models refreshed/,
  );
  expect(props.apply).toHaveBeenCalled();
  expect(props.onSaved).toHaveBeenCalled();
});

it('exposes explicit network effect scope before the one-click check', async () => {
  const props = options();
  render(<ProviderConfiguration {...props} />);
  await edit();
  fireEvent.change(screen.getByLabelText('Configuration action'), {
    target: { value: 'provider.endpoint.probe' },
  });
  expect(screen.getByRole('button', { name: 'Check endpoint' })).toBeEnabled();
  expect(
    screen.getByText(
      /contacts the saved endpoint and may consume provider quota/,
    ),
  ).toBeInTheDocument();
  expect(props.apply).not.toHaveBeenCalled();
  await save('Check endpoint');
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
  await save('List model');
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

it('retains an unsent draft and late uncertain command across remount without replay', async () => {
  const props = options(),
    session = new ProviderConfigurationSession(),
    pending = deferred<{ configuration_revision: string }>();
  props.apply.mockReturnValue(pending.promise);
  const first = render(<ProviderConfiguration {...props} session={session} />);
  await edit();
  fireEvent.change(screen.getByLabelText('Display name'), {
    target: { value: 'Retained draft' },
  });
  first.unmount();
  const second = render(<ProviderConfiguration {...props} session={session} />);
  expect(screen.getByLabelText('Display name')).toHaveValue('Retained draft');
  await save();
  await waitFor(() => expect(props.apply).toHaveBeenCalled());
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
      name: 'Check configuration',
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
  await save();
  await waitFor(() => expect(props.apply).toHaveBeenCalled());
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
  await save();
  await waitFor(() =>
    expect(
      screen.getByRole('button', {
        name: 'Check configuration',
      }),
    ).toBeEnabled(),
  );
  fireEvent.click(
    screen.getByRole('button', {
      name: 'Check configuration',
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

it('says what is wrong inside the endpoint dialog and keeps the list after a refresh that fails (B114)', async () => {
  const props = options();
  render(<ProviderConfiguration {...props} compact />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Add custom endpoint' }),
  );
  fireEvent.change(screen.getByLabelText('Endpoint id'), {
    target: { value: 'Bad id!' },
  });
  fireEvent.change(screen.getByLabelText('Display name'), {
    target: { value: 'Unreachable endpoint' },
  });
  fireEvent.change(screen.getByLabelText('Base URL'), {
    target: { value: 'http://127.0.0.1:1299/v1' },
  });
  props.review.mockRejectedValueOnce({ code: 'invalid_fields' });
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  // The refusal is inside the dialog, not behind it.
  const dialog = screen.getByTestId('shared-dialog-task');
  expect(
    await within(dialog).findByText(/Check the fields you filled in/),
  ).toBeVisible();
  fireEvent.change(screen.getByLabelText('Endpoint id'), {
    target: { value: 'unreachable' },
  });
  // Saved, then the endpoint doesn't answer its model refresh.
  props.apply
    .mockResolvedValueOnce({ configuration_revision: 'b'.repeat(64) })
    .mockRejectedValueOnce({ code: 'endpoint_unreachable' });
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  expect(
    await screen.findByText(
      "Endpoint saved. Row-Bot couldn't reach http://127.0.0.1:1299/v1. Check the address and that the server is running.",
    ),
  ).toBeVisible();
  // The list stays, nothing is left "unconfirmed", Add works again.
  expect(screen.getByText('Synthetic endpoint')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Check again' })).toBeNull();
  expect(
    screen.getByRole('button', { name: 'Add custom endpoint' }),
  ).toBeEnabled();
});

async function addEndpoint(baseUrl: string) {
  fireEvent.click(
    await screen.findByRole('button', { name: 'Add custom endpoint' }),
  );
  fireEvent.change(screen.getByLabelText('Endpoint id'), {
    target: { value: 'office' },
  });
  fireEvent.change(screen.getByLabelText('Display name'), {
    target: { value: 'Office GPU' },
  });
  fireEvent.change(screen.getByLabelText('Base URL'), {
    target: { value: baseUrl },
  });
}

it('names each server type, with Ollama saved as OpenAI-compatible', async () => {
  const props = options();
  render(<ProviderConfiguration {...props} compact />);
  expect(
    await screen.findByText(
      'http://127.0.0.1:8123/v1 · Local · OpenAI-compatible',
    ),
  ).toBeVisible();
  await addEndpoint('http://192.168.1.20:11434/v1');
  const type = screen.getByLabelText('Server type');
  expect(
    within(type)
      .getAllByRole('option')
      .map((option) => option.textContent),
  ).toEqual(['OpenAI-compatible', 'Ollama', 'llama.cpp', 'vLLM']);
  fireEvent.change(type, { target: { value: 'ollama' } });
  expect(
    screen.getByText(
      "Ollama's address ends in /v1, such as http://<computer>:11434/v1. Set its context size under Advanced: Ollama doesn't report it.",
    ),
  ).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  await waitFor(() =>
    expect(props.review).toHaveBeenCalledWith(
      'provider.endpoint.create',
      page.revision,
      expect.objectContaining({
        base_url: 'http://192.168.1.20:11434/v1',
        profile: 'generic_openai',
      }),
      expect.any(AbortSignal),
    ),
  );
});

it('asks before a hosted endpoint reaches a private address, and saves once allowed (H16)', async () => {
  const props = options();
  props.review.mockRejectedValueOnce({
    code: 'endpoint_private_address',
    status: 409,
  });
  render(<ProviderConfiguration {...props} compact hostName="PikaPods" />);
  await addEndpoint('http://10.0.0.5:11434/v1');
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  const ask = await screen.findByRole('alertdialog', {
    name: 'Allow a private address',
  });
  expect(ask).toHaveTextContent('Allow a private address?');
  expect(ask).toHaveTextContent(
    "10.0.0.5 is a private address inside PikaPods' network, not on the internet. Row-Bot connects there only if you allow it for this endpoint. Allow it only if your model server runs there.",
  );
  // The ask is the one next step: the dialog's own Save waits behind it.
  expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
  expect(props.apply).not.toHaveBeenCalled();
  fireEvent.click(within(ask).getByRole('button', { name: 'Allow and save' }));
  await waitFor(() =>
    expect(props.apply).toHaveBeenCalledWith(
      'provider.endpoint.create',
      page.revision,
      expect.objectContaining({
        base_url: 'http://10.0.0.5:11434/v1',
        allow_private_address: true,
      }),
      expect.any(String),
      expect.objectContaining({ nonce: 'original-session-nonce' }),
    ),
  );
  await waitFor(() =>
    expect(props.review).toHaveBeenLastCalledWith(
      'provider.endpoint.refresh',
      page.revision,
      { endpoint_id: 'office' },
    ),
  );
});

it('says a name leads to a private address, and asks again for a changed address', async () => {
  const props = options();
  props.review.mockRejectedValue({
    code: 'endpoint_private_address',
    status: 409,
  });
  render(<ProviderConfiguration {...props} compact hostName="PikaPods" />);
  await addEndpoint('http://models.internal.example/v1');
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  expect(
    await screen.findByRole('alertdialog', { name: 'Allow a private address' }),
  ).toHaveTextContent(
    "models.internal.example leads to a private address inside PikaPods' network",
  );
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(screen.queryByRole('alertdialog')).toBeNull();
  fireEvent.change(screen.getByLabelText('Base URL'), {
    target: { value: 'http://10.0.0.9/v1' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  await screen.findByRole('alertdialog', { name: 'Allow a private address' });
  expect(props.review).toHaveBeenLastCalledWith(
    'provider.endpoint.create',
    page.revision,
    expect.objectContaining({
      base_url: 'http://10.0.0.9/v1',
      allow_private_address: false,
    }),
    expect.any(AbortSignal),
  );
});

it('shows an allowed private address and keeps the OK while other fields change', async () => {
  const props = options();
  const allowed = {
    ...fields,
    base_url: 'http://10.0.0.5:11434/v1',
    allow_private_address: true,
  };
  props.load.mockResolvedValue({
    ...page,
    items: [{ ...page.items[0], fields: allowed }],
  });
  render(<ProviderConfiguration {...props} compact hostName="PikaPods" />);
  await chooseEndpoint('Edit Synthetic endpoint');
  expect(
    screen.getByText(
      "Allowed to use a private address inside PikaPods' network.",
    ),
  ).toBeVisible();
  fireEvent.change(screen.getByLabelText('Display name'), {
    target: { value: 'Office GPU' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  await waitFor(() =>
    expect(props.review).toHaveBeenCalledWith(
      'provider.endpoint.save',
      page.revision,
      expect.objectContaining({
        display_name: 'Office GPU',
        allow_private_address: true,
      }),
      expect.any(AbortSignal),
    ),
  );
});

it('says the key was refused, not that the server was unreachable, and offers the key', async () => {
  const props = options();
  props.apply.mockRejectedValueOnce({
    code: 'endpoint_key_rejected',
    status: 409,
  });
  render(<ProviderConfiguration {...props} compact />);
  fireEvent.click(
    await screen.findByRole('button', {
      name: 'Refresh Synthetic endpoint models',
    }),
  );
  expect(
    await screen.findByText(
      'The model server refused the API key. Check the key and try again.',
    ),
  ).toBeVisible();
  expect(screen.queryByText(/couldn't reach/)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Enter API key' }));
  expect(props.onCredentials).toHaveBeenCalledWith('custom_openai_synthetic');
});

it("names the address it couldn't reach, in words for a hosted server", async () => {
  const props = options();
  const remote = { ...fields, base_url: 'https://models.example.com/v1' };
  props.load.mockResolvedValue({
    ...page,
    items: [{ ...page.items[0], fields: remote }],
  });
  props.apply.mockRejectedValueOnce({
    code: 'endpoint_unreachable',
    status: 409,
  });
  render(<ProviderConfiguration {...props} compact hostName="PikaPods" />);
  fireEvent.click(
    await screen.findByRole('button', {
      name: 'Refresh Synthetic endpoint models',
    }),
  );
  expect(
    await screen.findByText(
      "Row-Bot couldn't reach https://models.example.com/v1. Check it's running and reachable from the internet.",
    ),
  ).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Enter API key' })).toBeNull();
});

it('explains a private address refused on refresh, and a metadata address never allowed', async () => {
  const props = options();
  const office = { ...fields, base_url: 'http://192.168.1.20:11434/v1' };
  props.load.mockResolvedValue({
    ...page,
    items: [{ ...page.items[0], fields: office }],
  });
  props.apply
    .mockRejectedValueOnce({ code: 'endpoint_private_address', status: 409 })
    .mockRejectedValueOnce({ code: 'endpoint_address_blocked', status: 409 });
  render(<ProviderConfiguration {...props} compact hostName="PikaPods" />);
  const refresh = await screen.findByRole('button', {
    name: 'Refresh Synthetic endpoint models',
  });
  fireEvent.click(refresh);
  expect(
    await screen.findByText(
      "192.168.1.20 is on PikaPods' private network. Edit the endpoint to allow it.",
    ),
  ).toBeVisible();
  await waitFor(() => expect(refresh).toBeEnabled());
  fireEvent.click(refresh);
  expect(
    await screen.findByText(
      "That address belongs to the hosting company's own computer, not a model server. Row-Bot never connects to it.",
    ),
  ).toBeVisible();
});
