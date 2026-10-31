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
    provider_id: 'xai_oauth',
    display_name: 'xAI Grok',
    group: 'subscription',
    risk_label: 'subscription',
    media_count: 0,
  };
  const testRuntime = vi.fn(async () => ({
    provider_id: 'xai_oauth' as const,
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
    name: 'More actions for xAI Grok',
  });
  await act(async () => fireEvent.keyDown(more, { key: 'Enter' }));
  fireEvent.click(
    screen.getByRole('menuitem', { name: 'Test xAI Grok runtime' }),
  );
  await waitFor(() => expect(testRuntime).toHaveBeenCalledWith('xai_oauth'));
  expect(
    await screen.findByText('xAI Grok: Runtime and tool calls work.'),
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
            provider_id: 'codex',
            display_name: 'ChatGPT / Codex',
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
      /Codex is signed in on this computer; not used yet · 4 models/,
    ),
  ).toBeVisible();
  expect(screen.queryByText(/CLI|chat/)).toBeNull();
});

it('asks for a ChatGPT sign-in, not a key, and names the plan as ChatGPT does (U8)', async () => {
  const codex = {
    ...card,
    provider_id: 'codex',
    display_name: 'ChatGPT / Codex',
    group: 'subscription' as const,
    fingerprint: '',
    model_count: 9,
    model_count_source: 'fallback_catalog',
  };
  const { unmount } = render(
    <ProviderStatus
      load={async () => ({
        schema_version: 1,
        providers: [
          {
            ...codex,
            configured: false,
            runtime_enabled: false,
            source: '',
          },
        ],
      })}
      refresh={vi.fn()}
      refreshState={vi.fn()}
    />,
  );
  expect(
    await screen.findByText(
      'Sign in with your ChatGPT account · 9 known models',
    ),
  ).toBeVisible();
  expect(screen.queryByText(/API key/)).toBeNull();
  unmount();
  render(
    <ProviderStatus
      load={async () => ({
        schema_version: 1,
        providers: [{ ...codex, source: 'oauth_device', plan_type: 'pro' }],
      })}
      refresh={vi.fn()}
      refreshState={vi.fn()}
    />,
  );
  expect(
    await screen.findByText(/^Signed in with ChatGPT · Pro plan · 9 /),
  ).toBeVisible();
});

it('a ChatGPT row that says Reconnect offers Reconnect and says the sign-in expired (B342)', async () => {
  const onSubscription = vi.fn();
  render(
    <ProviderStatus
      load={async () => ({
        schema_version: 1,
        providers: [
          {
            ...card,
            provider_id: 'codex',
            display_name: 'ChatGPT',
            group: 'subscription',
            configured: true,
            runtime_enabled: false,
            source: 'oauth_device',
          },
        ],
      })}
      refresh={vi.fn()}
      refreshState={vi.fn()}
      onSubscription={onSubscription}
    />,
  );
  fireEvent.click(
    await screen.findByRole('button', { name: 'Reconnect ChatGPT' }),
  );
  expect(onSubscription).toHaveBeenCalledWith('codex', 'connect', true);
});

const claude = (plan: Partial<NonNullable<ProviderLiveCard['claude_plan']>>) =>
  ({
    ...card,
    provider_id: 'claude_subscription',
    display_name: 'Claude (your Claude plan)',
    group: 'subscription',
    icon: 'C',
    configured: plan.state === 'ready',
    runtime_enabled: plan.state === 'ready',
    source: 'claude_code',
    model_count: plan.state === 'ready' ? 3 : null,
    fingerprint: '',
    risk_label: 'subscription',
    billing: 'subscription',
    claude_plan: {
      state: 'not_installed',
      version: '',
      minimum_version: '2.1.263',
      plan: '',
      detail: '',
      set_up_again: false,
      ...plan,
    },
  }) satisfies ProviderLiveCard;

it('Claude says what Claude Code says, and Set up Claude opens the one next step', async () => {
  const load = vi.fn(async () => ({
    schema_version: 1 as const,
    providers: [claude({ state: 'signed_out', version: '2.1.282' })],
  }));
  render(
    <ProviderStatus
      load={load}
      refresh={vi.fn()}
      refreshState={vi.fn()}
      onSubscription={vi.fn()}
    />,
  );
  const row = (await screen.findByText('Claude (your Claude plan)')).closest(
    'li',
  )!;
  expect(row).toHaveTextContent('Not signed in');
  expect(row).toHaveTextContent('Sign in to Claude to use your plan');
  // No Row-Bot sign-in, token import or disconnect: Claude Code signs in by itself.
  expect(
    within(row).queryByRole('button', { name: /More actions/ }),
  ).toBeNull();
  expect(
    within(row).queryByRole('button', { name: /Connect|Disconnect/ }),
  ).toBeNull();

  fireEvent.click(within(row).getByRole('button', { name: 'Set up Claude' }));
  const dialog = await screen.findByRole('dialog', {
    name: 'Claude (your Claude plan)',
  });
  expect(dialog).toHaveTextContent(
    'Row-Bot runs Claude Code, Anthropic’s program, with your own Claude plan. Its limits apply.',
  );
  // One next step, Claude's own sign-in; never a command to run in a terminal.
  expect(
    within(dialog).getByRole('button', { name: 'Use my Claude plan' }),
  ).toBeVisible();
  expect(dialog).not.toHaveTextContent(/terminal|claude auth login/i);
});

it('a Claude row that could not be checked asks Claude Code again from its dialog', async () => {
  const load = vi.fn(async () => ({
    schema_version: 1 as const,
    providers: [claude({ state: 'error', detail: 'It did not answer.' })],
  }));
  render(
    <ProviderStatus
      load={load}
      refresh={vi.fn()}
      refreshState={vi.fn()}
      onSubscription={vi.fn()}
    />,
  );
  const row = (await screen.findByText('Claude (your Claude plan)')).closest(
    'li',
  )!;
  fireEvent.click(within(row).getByRole('button', { name: 'Set up Claude' }));
  const dialog = await screen.findByRole('dialog', {
    name: 'Claude (your Claude plan)',
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Check again' }));
  await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
});

it('a ready Claude row: the plan and its models, with Claude Code’s status in the menu', async () => {
  render(
    <ProviderStatus
      load={async () => ({
        schema_version: 1,
        providers: [
          claude({ state: 'ready', version: '2.1.282', plan: 'Claude Max' }),
        ],
      })}
      refresh={vi.fn()}
      refreshState={vi.fn()}
      onSubscription={vi.fn()}
    />,
  );
  const row = (await screen.findByText('Claude (your Claude plan)')).closest(
    'li',
  )!;
  expect(row).toHaveTextContent('Ready');
  expect(row).toHaveTextContent(
    'Runs Claude Code with your Claude plan · Claude Max · 3 models',
  );
  expect(
    within(row).queryByRole('button', { name: 'Set up Claude' }),
  ).toBeNull();
  const more = within(row).getByRole('button', {
    name: 'More actions for Claude (your Claude plan)',
  });
  await act(async () => fireEvent.keyDown(more, { key: 'Enter' }));
  expect(
    screen.getAllByRole('menuitem').map((item) => item.textContent),
  ).toEqual(['Check Claude Code']);
  fireEvent.click(screen.getByRole('menuitem', { name: 'Check Claude Code' }));
  expect(
    await screen.findByRole('dialog', { name: 'Claude (your Claude plan)' }),
  ).toHaveTextContent(
    /Claude plan connected\s*Claude Max · Claude Code 2\.1\.282/,
  );
});

it('a profile the old Claude sign-in had set up says to set Claude up again', async () => {
  render(
    <ProviderStatus
      load={async () => ({
        schema_version: 1,
        providers: [claude({ state: 'signed_out', set_up_again: true })],
      })}
      refresh={vi.fn()}
      refreshState={vi.fn()}
    />,
  );
  const row = (await screen.findByText('Claude (your Claude plan)')).closest(
    'li',
  )!;
  expect(row).toHaveTextContent('Set up again');
  expect(row).toHaveTextContent(
    'Claude now works through Claude Code. Set it up again',
  );
});

it('offers no Ollama on a hosted server, unless one is connected (U3)', async () => {
  const ollama: ProviderLiveCard = {
    ...card,
    provider_id: 'ollama',
    display_name: 'Ollama Local',
    group: 'local',
    configured: false,
    source: 'not_running',
  };
  const { unmount } = render(
    <ProviderStatus
      hideLocal
      load={async () => ({ schema_version: 1, providers: [card, ollama] })}
      refresh={vi.fn()}
      refreshState={vi.fn()}
    />,
  );
  expect(await screen.findByLabelText('API providers')).toBeVisible();
  expect(screen.queryByLabelText('On this device')).toBeNull();
  expect(screen.queryByText('Ollama Local')).toBeNull();
  unmount();
  render(
    <ProviderStatus
      hideLocal
      load={async () => ({
        schema_version: 1,
        providers: [
          card,
          { ...ollama, configured: true, source: 'local_daemon' },
        ],
      })}
      refresh={vi.fn()}
      refreshState={vi.fn()}
    />,
  );
  expect(await screen.findByText('Ollama Local')).toBeVisible();
});
