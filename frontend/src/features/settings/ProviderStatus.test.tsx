import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
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
  expect(screen.getByText(/Saved in keyring · 91 models · key/)).toBeVisible();
  // Bare counts ("85 chat", "6 media") meant nothing on their own.
  expect(screen.queryByText(/chat|media/)).toBeNull();
  expect(screen.getByLabelText('Provider summary')).toHaveTextContent(
    /^1 connected$/,
  );
  expect(screen.getByLabelText('API providers')).toBeVisible();
  expect(screen.getByText('API key')).toBeVisible();
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
      name: 'Refresh OpenAI API model list',
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
      name: 'Refresh OpenAI API model list',
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
      name: 'Refresh OpenAI API model list',
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
  const more = await screen.findByRole('button', {
    name: 'More actions for Claude Subscription',
  });
  await act(async () => fireEvent.keyDown(more, { key: 'Enter' }));
  fireEvent.click(
    screen.getByRole('menuitem', { name: 'Test Claude Subscription runtime' }),
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

it('lists connected providers first and folds the rest into one quiet list', async () => {
  const unconnected = (provider_id: string, display_name: string, icon = '◇') =>
    ({
      ...card,
      provider_id,
      display_name,
      icon,
      configured: false,
      runtime_enabled: false,
      source: 'none',
      model_count: null,
      fingerprint: '',
    }) satisfies ProviderLiveCard;
  render(
    <ProviderStatus
      load={async () => ({
        schema_version: 1,
        providers: [
          { ...card, icon: '🤖' },
          unconnected('mistral', 'Mistral'),
          unconnected('groq', 'Groq', 'G'),
          {
            ...unconnected('ollama', 'Ollama Local', '🖥️'),
            group: 'local',
            source: 'not_running',
          },
        ],
      })}
      refresh={vi.fn()}
      refreshState={vi.fn()}
    />,
  );
  const api = await screen.findByLabelText('API providers');
  const more = within(api).getByText('More API providers').closest('details')!;
  expect(more).not.toHaveAttribute('open');
  expect(within(api).getAllByRole('listitem')[0]).toHaveTextContent(
    'OpenAI API',
  );
  // Folded rows: the name only, no repeated status or per-row refresh.
  for (const name of ['Mistral', 'Groq']) {
    const row = within(more).getByText(name).closest('li')!;
    expect(row).not.toHaveTextContent(/Not connected|Needs an API key/);
    expect(within(row).queryByRole('button', { name: /Refresh/ })).toBeNull();
  }
  // A local runtime keeps its own refresh: it may have new models.
  expect(
    screen.getByRole('button', { name: 'Refresh Ollama Local model list' }),
  ).toBeVisible();
  // One mark style: a letter tile, never an emoji.
  expect(
    [...document.querySelectorAll('.settings-provider-mark')].map(
      (mark) => mark.textContent,
    ),
  ).toEqual(['O', 'O', 'M', 'G']);
});

it('says in plain words when a subscription can borrow another app’s sign-in', async () => {
  render(
    <ProviderStatus
      load={async () => ({
        schema_version: 1,
        providers: [
          {
            ...card,
            provider_id: 'claude_subscription',
            display_name: 'Claude Subscription',
            group: 'subscription',
            configured: false,
            runtime_enabled: false,
            source: 'external_cli_detected',
            model_count: 4,
            chat_count: 4,
          },
        ],
      })}
      refresh={vi.fn()}
      refreshState={vi.fn()}
    />,
  );
  expect(
    await screen.findByText(
      /Claude Code is signed in on this computer; not used yet · 4 models/,
    ),
  ).toBeVisible();
  expect(screen.queryByText(/CLI|chat/)).toBeNull();
});
