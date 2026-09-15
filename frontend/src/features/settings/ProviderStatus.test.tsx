import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { ProviderLiveCard, ProviderLiveSnapshot } from '../../api/types';
import ProviderStatus from './ProviderStatus';

const card: ProviderLiveCard = {
  provider_id: 'openai',
  display_name: 'OpenAI API',
  group: 'api',
  icon: '◇',
  configured: true,
  source: 'keyring',
  runtime_enabled: true,
  model_count: 91,
  model_count_source: 'cloud_cache',
  chat_count: 85,
  media_count: 6,
  plan_type: '',
  fingerprint: '****QBA',
  account_id_hash: '',
  user_hash: '',
  oauth_client_id_fingerprint: '',
  oauth_client_id_configured: false,
  external_reference_exists: false,
  risk_label: 'api_key',
  last_runtime_probe_ok: null,
};
const snapshot: ProviderLiveSnapshot = { schema_version: 1, providers: [card] };

it('shows the live NiceGUI connection facts without saved-catalog internals', async () => {
  const load = vi.fn(async () => snapshot);
  render(
    <ProviderStatus load={load} refresh={vi.fn()} refreshState={vi.fn()} />,
  );
  expect(await screen.findByText('OpenAI API')).toBeVisible();
  expect(screen.getByText('Connected')).toBeVisible();
  expect(
    screen.getByText(/Saved in keyring · 91 models · 85 chat · 6 media/),
  ).toBeVisible();
  expect(screen.getByLabelText('Provider summary')).toHaveTextContent(
    '1 connected',
  );
  expect(screen.getByLabelText('API Providers')).toBeVisible();
  expect(screen.queryByText(/connection readiness not checked/i)).toBeNull();
  expect(
    screen.queryByRole('button', { name: /Reload saved status/ }),
  ).toBeNull();
  expect(load).toHaveBeenCalledOnce();
});

it('uses the row refresh icon to request and display a targeted catalog update', async () => {
  const load = vi.fn(async () => snapshot);
  const refresh = vi.fn(async () => ({ running: true, started: true }));
  const refreshState = vi.fn(async () => ({
    running: false,
    started: false,
    provider_id: 'openai',
    ok: true,
    model_count: 92,
    message: '',
  }));
  render(
    <ProviderStatus
      load={load}
      refresh={refresh}
      refreshState={refreshState}
    />,
  );
  fireEvent.click(
    await screen.findByRole('button', {
      name: 'Refresh OpenAI API provider status and catalog',
    }),
  );
  await waitFor(() => expect(refresh).toHaveBeenCalledWith('openai'));
  expect(
    await screen.findByText('OpenAI API catalog refreshed: 92 models.'),
  ).toBeVisible();
  await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
});

it('does not credit another running refresh to the selected provider', async () => {
  const refreshState = vi.fn();
  render(
    <ProviderStatus
      load={async () => snapshot}
      refresh={async () => ({ running: true, started: false })}
      refreshState={refreshState}
    />,
  );
  fireEvent.click(
    await screen.findByRole('button', {
      name: 'Refresh OpenAI API provider status and catalog',
    }),
  );
  expect(
    await screen.findByText('Model catalog refresh is already running.'),
  ).toBeVisible();
  expect(refreshState).not.toHaveBeenCalled();
});
it('reports a targeted refresh failure even if the catalog worker had no provider result', async () => {
  render(
    <ProviderStatus
      load={async () => snapshot}
      refresh={async () => ({
        running: true,
        started: true,
        provider_id: 'openai',
      })}
      refreshState={async () => ({ running: false, started: false, ok: false })}
    />,
  );
  fireEvent.click(
    await screen.findByRole('button', {
      name: 'Refresh OpenAI API provider status and catalog',
    }),
  );
  expect(await screen.findByText('OpenAI API refresh failed.')).toBeVisible();
});

it('runs a real subscription runtime test from the row and shows its outcome', async () => {
  const subscription: ProviderLiveCard = {
    ...card,
    provider_id: 'claude_subscription',
    display_name: 'Claude Subscription',
    group: 'subscription',
    risk_label: 'subscription',
    media_count: 0,
  };
  const testRuntime = vi.fn(async () => ({
    provider_id: 'claude_subscription' as const,
    ok: true,
    detail: 'Runtime and tool calls work',
  }));
  render(
    <ProviderStatus
      load={async () => ({ schema_version: 1, providers: [subscription] })}
      refresh={vi.fn()}
      refreshState={vi.fn()}
      testRuntime={testRuntime}
    />,
  );
  fireEvent.click(
    await screen.findByRole('button', {
      name: 'Test Claude Subscription runtime',
    }),
  );
  await waitFor(() =>
    expect(testRuntime).toHaveBeenCalledWith('claude_subscription'),
  );
  expect(
    await screen.findByText(
      'Claude Subscription: Runtime and tool calls work.',
    ),
  ).toBeVisible();
});
