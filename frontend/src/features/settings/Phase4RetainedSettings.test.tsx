import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import type {
  SettingsMutationReceipt,
  SettingsMutationRequest,
  SettingsMutationReview,
  SettingsSnapshot,
} from '../../api/types';
import Phase4RetainedSettings, {
  type Phase4RetainedSetting,
} from './Phase4RetainedSettings';
import {
  DocumentEmbeddingSnapshot,
  PreferencesSnapshotPanel,
  ToolConfigurationSnapshot,
  SettingsDraftOwner,
  type SettingsMutationIO,
} from './SettingsSnapshotPanels';

const account = {
  account_id: 'github' as const,
  enabled: null,
  configured: false,
  authentication_state: 'not_configured' as const,
  credentials_path: '',
  credential: null,
  operations: [],
  read_operations: [],
  post_operations: [],
  engage_operations: [],
};
const snapshot = {
  schema_version: 1,
  revision: 'settings-a',
  voice: {
    availability: 'available',
    runtime: {
      talk_provider: 'local',
      talk_model: 'small',
      dictation_provider: 'local',
      dictation_model: 'base',
      speech_output_provider: 'local',
      speech_output_model: 'kokoro',
      speech_output_voice: 'af_heart',
      realtime_voice: 'alloy',
      captions_enabled: true,
      talk_auto_start: false,
      realtime_fallback_to_local: true,
    },
    local: {
      whisper_model: 'base',
      sensevoice_path_configured: false,
      runtime_state: 'cached_unknown',
    },
    tts: {
      installed: true,
      enabled: true,
      voice: 'af_heart',
      speed: 1,
      auto_speak: false,
    },
    openai_realtime_credential: {
      configured: true,
      source: 'keyring',
      fingerprint: 'must-not-render',
    },
    talk_providers: [
      { value: 'local', label: 'Local' },
      { value: 'openai_realtime', label: 'OpenAI Realtime' },
    ],
    dictation_providers: [{ value: 'local', label: 'Local' }],
    speech_output_providers: [{ value: 'local', label: 'Local' }],
    whisper_options: [{ value: 'base', label: 'Base' }],
    realtime_voice_options: [{ value: 'alloy', label: 'Alloy' }],
    tts_voice_options: [{ value: 'af_heart', label: 'Heart' }],
  },
  system: {
    availability: 'available',
    workspace: { path: 'D:/Workspace', configured: true, exists: true },
    shell: { available: true, enabled: true, blocked_patterns: 'format c:' },
    browser: {
      available: true,
      enabled: true,
      runtime_state: 'cached_unknown',
    },
    computer_use: {
      available: true,
      enabled: false,
      runtime_state: 'cached_unknown',
      disclosure_acknowledged: true,
      system_binary_configured: false,
    },
    file_operations: {
      available: true,
      enabled: true,
      selected: ['read_file'],
      options: ['read_file', 'write_file', 'file_delete'],
    },
    tunnel: {
      provider: 'ngrok',
      credential: {
        configured: true,
        source: 'keyring',
        fingerprint: 'must-not-render',
      },
      runtime_state: 'not_checked',
      active_count: null,
    },
    remote_access: {
      listen_mode: 'local_only',
      configured_origins: ['http://127.0.0.1'],
      host_admission_managed_externally: false,
      tailscale_state: 'not_checked',
    },
    mobile_access: {
      availability: 'available',
      active_devices: 0,
      active_sessions: 1,
    },
    logging: { level: 'INFO', directory: 'D:/Logs' },
  },
  tracker: {
    availability: 'available',
    tool_available: true,
    enabled: true,
    items: [
      {
        tracker_id: 'water',
        name: 'Water',
        kind: 'counter',
        unit: 'glasses',
        icon: '💧',
        entry_count: 4,
        last_event_at: 'today',
      },
    ],
    total_entries: 4,
  },
  documents: {
    availability: 'available',
    embedding: {
      provider: 'local',
      local_model: 'qwen3-0.6b',
      cloud_model: 'openai:text-embedding-3-small',
      dimension: null,
      auto_unload: true,
      runtime_state: 'cached_unknown',
      local_options: [{ value: 'qwen3-0.6b', label: 'Qwen3' }],
      cloud_options: [
        {
          value: 'openai:text-embedding-3-small',
          label: 'OpenAI small',
        },
      ],
    },
  },
  tools: {
    availability: 'available',
    external_loading_mode: 'auto',
    compression_mode: 'off',
    items: [
      {
        tool_id: 'web_search',
        label: 'Web Search',
        available: true,
        enabled: true,
        configured_fields: ['provider'],
        credentials: [
          {
            label: 'Search API key',
            name: 'api_key',
            configured: true,
            source: 'keyring',
            fingerprint: 'must-not-render',
          },
        ],
      },
    ],
  },
  accounts: {
    availability: 'available',
    github: {
      ...account,
      configured: true,
      credential: {
        configured: true,
        source: 'keyring',
        fingerprint: 'must-not-render',
      },
    },
    gmail: {
      ...account,
      account_id: 'gmail',
      enabled: true,
      credentials_path: 'D:/credentials.json',
      operations: ['search_gmail'],
    },
    calendar: {
      ...account,
      account_id: 'calendar',
      enabled: false,
      operations: ['search_events'],
    },
    x: {
      ...account,
      account_id: 'x',
      enabled: true,
      read_operations: ['x_search'],
      post_operations: ['x_post_tweet'],
      engage_operations: ['x_like'],
    },
  },
  utilities: {
    availability: 'available',
    items: [
      {
        utility_id: 'calculator',
        label: 'Calculator',
        description: 'Evaluate local arithmetic.',
        available: true,
        enabled: true,
      },
    ],
  },
  plugins: {
    availability: 'available',
    total: 0,
    installed: 0,
    enabled: 0,
    items: [],
  },
  preferences: {
    availability: 'available',
    identity: {
      name: 'Row-Bot',
      personality: '',
      personality_max_length: 200,
      self_improvement_enabled: false,
    },
    window_mode: 'ask',
    dream_cycle: {
      enabled: false,
      window_start: 1,
      window_end: 5,
      last_run: null,
      last_summary: '',
    },
    updates: {
      current_version: '1.0.0',
      channel: 'stable',
      last_check: null,
      last_success: null,
      skipped_versions: [],
      runtime_state: 'cached',
    },
    migration: { available: false, sources: [] },
  },
} satisfies SettingsSnapshot;

let mutation: SettingsMutationIO;
beforeEach(() => {
  mutation = {
    revision: snapshot.revision,
    page: 'voice',
    review: vi.fn(
      async (
        request: SettingsMutationRequest,
      ): Promise<SettingsMutationReview> => ({
        schema_version: 1,
        operation: 'settings.update',
        settings_revision: request.settings_revision,
        page: request.page,
        field: request.field,
        value_summary: 'saved locally',
        secret: false,
        action_digest: 'a'.repeat(64),
        review_id: 'review-a',
      }),
    ),
    execute: vi.fn(
      async (
        request,
        _review,
        commandId,
      ): Promise<SettingsMutationReceipt> => ({
        command_id: commandId,
        status: 'completed',
        settings_revision: 'settings-b',
        snapshot: {
          ...snapshot,
          revision: 'settings-b',
          voice: {
            ...snapshot.voice,
            runtime: {
              ...snapshot.voice.runtime,
              talk_model: String(request.value),
            },
          },
        },
      }),
    ),
    receipt: vi.fn(),
    drafts: new SettingsDraftOwner(),
    onSnapshot: vi.fn(),
  };
});

function renderSetting(setting: Phase4RetainedSetting) {
  mutation.page = setting;
  return render(
    <MemoryRouter>
      <Phase4RetainedSettings
        setting={setting}
        snapshot={snapshot}
        mutation={mutation}
        selectedConversationId="conversation-a"
      />
    </MemoryRouter>,
  );
}

it('renders real voice controls without probing a device or provider', () => {
  renderSetting('voice');
  expect(screen.getByLabelText('Talk provider')).toHaveValue('local');
  expect(screen.getByLabelText('Whisper model size')).toHaveValue('base');
  expect(
    screen.getByRole('link', { name: 'Open conversation voice' }),
  ).toHaveAttribute('href', '/conversations/conversation-a');
  expect(document.body).not.toHaveTextContent('must-not-render');
  expect(mutation.review).not.toHaveBeenCalled();
});

it('reviews and saves one retained setting without replaying it', async () => {
  renderSetting('voice');
  fireEvent.change(screen.getByLabelText('Talk model'), {
    target: { value: 'medium' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Review change' }));
  await screen.findByText('Review ready: saved locally');
  fireEvent.click(screen.getByRole('button', { name: 'Save reviewed change' }));
  await waitFor(() => expect(mutation.execute).toHaveBeenCalledTimes(1));
  expect(mutation.review).toHaveBeenCalledWith(
    expect.objectContaining({
      page: 'voice',
      field: 'runtime.talk_model',
      value: 'medium',
    }),
    expect.any(AbortSignal),
  );
  expect(mutation.onSnapshot).toHaveBeenCalledWith(
    expect.objectContaining({ revision: 'settings-b' }),
  );
});

it('checks the original receipt instead of replaying an uncertain save', async () => {
  const execute = vi.fn().mockRejectedValue({ code: 'operation_uncertain' });
  mutation.execute = execute;
  mutation.receipt = vi.fn(
    async (commandId): Promise<SettingsMutationReceipt> => ({
      command_id: commandId,
      status: 'completed',
      settings_revision: 'settings-b',
      snapshot: { ...snapshot, revision: 'settings-b' },
    }),
  );
  renderSetting('voice');
  fireEvent.change(screen.getByLabelText('Talk model'), {
    target: { value: 'medium' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Review change' }));
  await screen.findByText('Review ready: saved locally');
  fireEvent.click(screen.getByRole('button', { name: 'Save reviewed change' }));
  fireEvent.click(
    await screen.findByRole('button', { name: 'Check original receipt' }),
  );
  await waitFor(() => expect(mutation.receipt).toHaveBeenCalledTimes(1));
  expect(execute).toHaveBeenCalledTimes(1);
  expect(mutation.onSnapshot).toHaveBeenCalledWith(
    expect.objectContaining({ revision: 'settings-b' }),
  );
});

it('retains a dirty page draft until it is explicitly reverted', () => {
  const first = renderSetting('voice');
  fireEvent.change(screen.getByLabelText('Talk model'), {
    target: { value: 'medium' },
  });
  first.unmount();

  expect(mutation.review).not.toHaveBeenCalled();
  expect(mutation.execute).not.toHaveBeenCalled();
  renderSetting('voice');
  expect(screen.getByLabelText('Talk model')).toHaveValue('medium');
  fireEvent.click(screen.getByRole('button', { name: 'Revert' }));
  expect(screen.getByLabelText('Talk model')).toHaveValue('small');
});

it('does not carry a write draft into a new authenticated session owner', () => {
  const first = renderSetting('accounts');
  fireEvent.click(screen.getByText('GitHub'));
  fireEvent.change(screen.getByLabelText('GitHub token'), {
    target: { value: 'sensitive-local-draft' },
  });
  first.unmount();

  mutation = { ...mutation, drafts: new SettingsDraftOwner() };
  renderSetting('accounts');
  fireEvent.click(screen.getByText('GitHub'));
  expect(screen.getByLabelText('GitHub token')).toHaveValue('');
  expect(mutation.review).not.toHaveBeenCalled();
  expect(mutation.execute).not.toHaveBeenCalled();
});

it('renders System, Tracker, Accounts, and Utilities controls from one snapshot', () => {
  const system = renderSetting('system');
  expect(screen.getByLabelText('Workspace folder')).toHaveValue('D:/Workspace');
  expect(screen.getByLabelText('Log level')).toHaveValue('INFO');
  system.unmount();

  const tracker = renderSetting('tracker');
  expect(screen.getByLabelText('Enable Tracker')).toBeChecked();
  expect(screen.getByText(/Water/)).toBeVisible();
  tracker.unmount();

  const accounts = renderSetting('accounts');
  fireEvent.click(screen.getByText('GitHub'));
  expect(screen.getByLabelText('GitHub token')).toHaveAttribute(
    'type',
    'password',
  );
  fireEvent.click(screen.getByText('X (Twitter)'));
  expect(screen.getByText('x search')).toBeVisible();
  accounts.unmount();

  renderSetting('utilities');
  expect(screen.getByLabelText('Enable Calculator')).toBeChecked();
});

it('renders editable document, tool, and preference owners', () => {
  mutation.page = 'documents';
  const documents = render(
    <DocumentEmbeddingSnapshot
      snapshot={snapshot.documents}
      mutation={mutation}
    />,
  );
  expect(screen.getByLabelText('Provider')).toHaveValue('local');
  expect(screen.getByLabelText('Dimension override')).toHaveValue(null);
  documents.unmount();

  mutation.page = 'tools';
  const tools = render(
    <ToolConfigurationSnapshot snapshot={snapshot.tools} mutation={mutation} />,
  );
  expect(screen.getByLabelText('External tool loading')).toHaveValue('auto');
  expect(screen.getByLabelText('Enable Web Search')).toBeChecked();
  expect(screen.getByLabelText('Search API key')).toHaveAttribute(
    'type',
    'password',
  );
  tools.unmount();

  mutation.page = 'preferences';
  render(
    <PreferencesSnapshotPanel
      snapshot={snapshot.preferences}
      mutation={mutation}
    />,
  );
  expect(screen.getByLabelText('Name')).toHaveValue('Row-Bot');
  expect(screen.getByLabelText('Window mode')).toHaveValue('ask');
  expect(screen.getByLabelText('Start hour')).toHaveValue(1);
  expect(screen.getByText(/Update status is cached/)).toBeVisible();
});
