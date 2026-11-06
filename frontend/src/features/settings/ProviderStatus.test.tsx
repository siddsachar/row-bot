import {
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

/** Opens a provider's sheet from its tile. */
async function openSheet(name: string) {
  fireEvent.click(
    await screen.findByRole('button', {
      name: (accessible) => accessible.startsWith(name),
    }),
  );
  return screen.findByRole('dialog', { name });
}

/** Opens Add a provider and one of its tabs. */
async function openGallery(tab: string) {
  fireEvent.click(
    await screen.findByRole('button', { name: 'Add a provider' }),
  );
  const gallery = await screen.findByRole('dialog', { name: 'Add a provider' });
  const trigger = within(gallery).getByRole('tab', { name: tab });
  fireEvent.mouseDown(trigger);
  fireEvent.click(trigger);
  return gallery;
}

it('gives each connected provider a tile: how it is paid and how many models', async () => {
  const load = vi.fn(async () => ({
    schema_version: 1 as const,
    providers: [{ ...card, billing: 'pay_per_use' as const }],
  }));
  render(
    <ProviderStatus load={load} refresh={vi.fn()} refreshState={vi.fn()} />,
  );
  const tile = await screen.findByRole('button', { name: /^OpenAI API/ });
  expect(tile).toHaveTextContent('Pay per use · 91 models');
  expect(tile).toHaveTextContent('Connected');
  expect(
    within(screen.getByLabelText('API providers')).getAllByRole('listitem'),
  ).toHaveLength(1);
  // Bare counts ("85 chat", "6 media") meant nothing on their own.
  expect(screen.queryByText(/chat|media/)).toBeNull();
  expect(load).toHaveBeenCalledOnce();
});

it('a tile opens the provider’s sheet with its state and its models', async () => {
  const models = vi.fn((provider: ProviderLiveCard) => (
    <p>Models from {provider.display_name}</p>
  ));
  render(
    <ProviderStatus
      load={async () => ({
        schema_version: 1,
        providers: [{ ...card, billing: 'pay_per_use' }],
      })}
      refresh={vi.fn()}
      refreshState={vi.fn()}
      models={models}
    />,
  );
  const sheet = await openSheet('OpenAI API');
  expect(sheet).toHaveAccessibleDescription(
    'Connected · Pay per use · 91 models',
  );
  expect(within(sheet).getByText('Models from OpenAI API')).toBeVisible();
  fireEvent.click(
    within(sheet).getByRole('button', { name: 'Close OpenAI API' }),
  );
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
});

it('a link with ?provider= opens that provider’s sheet at once', async () => {
  render(
    <ProviderStatus
      load={async () => snapshot}
      refresh={vi.fn()}
      refreshState={vi.fn()}
      openProvider="openai"
    />,
  );
  expect(
    await screen.findByRole('dialog', { name: 'OpenAI API' }),
  ).toBeVisible();
});

it('refreshes one provider’s model list from its sheet and says how it went', async () => {
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
  const sheet = await openSheet('OpenAI API');
  fireEvent.click(
    within(sheet).getByRole('button', {
      name: 'Refresh OpenAI API model list',
    }),
  );
  await waitFor(() => expect(refresh).toHaveBeenCalledWith('openai'));
  expect(
    await within(sheet).findByText('OpenAI API catalog refreshed: 92 models.'),
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
  const sheet = await openSheet('OpenAI API');
  fireEvent.click(
    within(sheet).getByRole('button', {
      name: 'Refresh OpenAI API model list',
    }),
  );
  expect(
    await within(sheet).findByText('Model catalog refresh is already running.'),
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
  const sheet = await openSheet('OpenAI API');
  fireEvent.click(
    within(sheet).getByRole('button', {
      name: 'Refresh OpenAI API model list',
    }),
  );
  expect(
    await within(sheet).findByText('OpenAI API refresh failed.'),
  ).toBeVisible();
});

it('runs a real subscription runtime test from the sheet and shows its outcome', async () => {
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
  const sheet = await openSheet('xAI Grok');
  fireEvent.click(
    within(sheet).getByRole('button', { name: 'Test xAI Grok runtime' }),
  );
  await waitFor(() => expect(testRuntime).toHaveBeenCalledWith('xai_oauth'));
  expect(
    await within(sheet).findByText('xAI Grok: Runtime and tool calls work.'),
  ).toBeVisible();
});

it('keeps providers not connected in Add a provider, by how they are paid', async () => {
  const onAddServer = vi.fn();
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
      onAddServer={onAddServer}
    />,
  );
  // A local runtime always has its tile, and says when it isn't running.
  expect(
    await screen.findByRole('button', { name: /^Ollama Local/ }),
  ).toHaveTextContent('Not running');
  expect(screen.queryByRole('button', { name: /^Mistral/ })).toBeNull();
  // One mark style: a letter tile, never an emoji.
  expect(
    [...document.querySelectorAll('.settings-provider-mark')].map(
      (mark) => mark.textContent,
    ),
  ).toEqual(['O', 'O']);

  const gallery = await openGallery('API key');
  expect(
    within(gallery)
      .getAllByRole('tab')
      .map((tab) => tab.textContent),
  ).toEqual(['API key', 'Your server']);
  expect(
    within(gallery)
      .getAllByRole('button', { name: /Mistral|Groq/ })
      .map((option) => option.textContent),
  ).toEqual(['MMistralNeeds an API key', 'GGroqNeeds an API key']);
  fireEvent.click(within(gallery).getByRole('button', { name: /Mistral/ }));
  expect(await screen.findByRole('dialog', { name: 'Mistral' })).toBeVisible();
  expect(screen.queryByRole('dialog', { name: 'Add a provider' })).toBeNull();
});

it('Add a provider › Your server opens the custom endpoint dialog', async () => {
  const onAddServer = vi.fn();
  render(
    <ProviderStatus
      load={async () => snapshot}
      refresh={vi.fn()}
      refreshState={vi.fn()}
      onAddServer={onAddServer}
    />,
  );
  const gallery = await openGallery('Your server');
  fireEvent.click(
    within(gallery).getByRole('button', { name: /OpenAI-compatible server/ }),
  );
  expect(onAddServer).toHaveBeenCalledOnce();
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
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
  const gallery = await openGallery('Subscription');
  expect(
    within(gallery).getByText(
      /Codex is signed in on this computer; not used yet · 4 models/,
    ),
  ).toBeVisible();
  expect(within(gallery).queryByText(/CLI|chat/)).toBeNull();
});

it('asks for a ChatGPT sign-in, not a key, and names the plan as ChatGPT does (U8)', async () => {
  const onSubscription = vi.fn();
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
      onSubscription={onSubscription}
    />,
  );
  const gallery = await openGallery('Subscription');
  fireEvent.click(
    within(gallery).getByRole('button', { name: /ChatGPT \/ Codex/ }),
  );
  const sheet = await screen.findByRole('dialog', { name: 'ChatGPT / Codex' });
  expect(sheet).toHaveAccessibleDescription(
    'Not connected · Sign in with your ChatGPT account · 9 known models',
  );
  expect(within(sheet).queryByText(/API key/)).toBeNull();
  fireEvent.click(
    within(sheet).getByRole('button', { name: 'Connect ChatGPT / Codex' }),
  );
  // Its sign-in has its own dialog: the sheet steps aside.
  expect(onSubscription).toHaveBeenCalledWith('codex', 'connect', false);
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
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
  expect(await openSheet('ChatGPT / Codex')).toHaveAccessibleDescription(
    'Connected · Pro plan · 9 models',
  );
});

it('a ChatGPT sign-in that expired says Reconnect and offers it (B342)', async () => {
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
  expect(
    await screen.findByRole('button', { name: /^ChatGPT/ }),
  ).toHaveTextContent('Reconnect');
  const sheet = await openSheet('ChatGPT');
  fireEvent.click(
    within(sheet).getByRole('button', { name: 'Reconnect ChatGPT' }),
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

it('Claude says what Claude Code says, and its sheet has the one next step', async () => {
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
  const gallery = await openGallery('Subscription');
  const option = within(gallery).getByRole('button', {
    name: /Claude \(your Claude plan\)/,
  });
  expect(option).toHaveTextContent('Sign in to Claude Code to use your plan');
  fireEvent.click(option);
  const sheet = await screen.findByRole('dialog', {
    name: 'Claude (your Claude plan)',
  });
  expect(within(sheet).getByText('claude auth login')).toBeVisible();
  // No Row-Bot sign-in, token import or disconnect: Claude Code signs in by itself.
  expect(
    within(sheet).queryByRole('button', { name: /Connect|Disconnect/ }),
  ).toBeNull();
  fireEvent.click(within(sheet).getByRole('button', { name: 'Check again' }));
  await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
});

it('a ready Claude: the plan and its models, with Claude Code’s status in its sheet', async () => {
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
  expect(
    await screen.findByRole('button', { name: /^Claude \(your Claude plan\)/ }),
  ).toHaveTextContent('Subscription · 3 models');
  const sheet = await openSheet('Claude (your Claude plan)');
  expect(sheet).toHaveAccessibleDescription(
    'Ready · Runs Claude Code on this computer · Claude Max · 3 models',
  );
  expect(sheet).toHaveTextContent('Ready Claude Max · Claude Code 2.1.282');
  expect(
    within(sheet).queryByRole('button', { name: /Connect|Disconnect/ }),
  ).toBeNull();
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
  const gallery = await openGallery('Subscription');
  expect(
    within(gallery).getByRole('button', {
      name: /Claude \(your Claude plan\)/,
    }),
  ).toHaveTextContent('Claude now works through Claude Code. Set it up again');
});
