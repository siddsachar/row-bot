import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { useState } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type {
  AccountAuthCommand,
  AccountAuthSnapshot,
  SettingsMutationReceipt,
  SettingsMutationRequest,
  SettingsMutationReview,
  SettingsSnapshot,
  UpdateSnapshot,
} from '../../api/types';
import type { ClientPlatform } from '../../platform';
import { RuntimeContext } from '../../runtime';
import { setPlace } from '../../api/place';
import { setMemoryLimit } from '../../api/server-memory';
import { OverlayProvider } from '../../ui/overlays';
import { ThemeProvider } from '../../ui/theme';
import { settingsRows, type SettingsLeafId } from './model';
import SettingRoute from './SettingRoute';
import Phase4RetainedSettings, {
  type Phase4RetainedSetting,
} from './Phase4RetainedSettings';
import {
  AccountsSnapshotPanel,
  DocumentEmbeddingSnapshot,
  DocumentModelSetting,
  PreferencesSnapshotPanel,
  SettingsDraftOwner,
  SystemSnapshotPanel,
  TrackerSnapshotPanel,
  ToolConfigurationSnapshot,
  VoiceSnapshotPanel,
  type SettingsMutationIO,
} from './SettingsSnapshotPanels';

const account = {
  account_id: 'github' as const,
  enabled: null,
  configured: false,
  authentication_state: 'not_configured' as const,
  credential: null,
  operations: [],
  read_operations: [],
  post_operations: [],
  engage_operations: [],
};
const snapshot = {
  schema_version: 1,
  revision: 'settings-a',
  buddy: {
    availability: 'available',
    enabled: true,
    visible: true,
    placement: 'docked',
    collapsed: false,
    personality: 'default',
    personality_description: 'Helpful local companion',
    bubble_verbosity: 'normal',
    hatch_prompt: '',
    pack_id: 'classic',
    personality_options: [{ value: 'default', label: 'Default' }],
    bubble_options: [{ value: 'normal', label: 'Normal' }],
    packs: [],
  },
  voice: {
    availability: 'available',
    runtime: {
      talk_provider: 'local',
      talk_model: 'small',
      dictation_provider: 'local',
      dictation_model: 'base',
      speech_output_provider: 'local',
      speech_output_model: 'kokoro',
      realtime_model: 'gpt-realtime-2-mini',
      realtime_voice: 'alloy',
      captions_enabled: true,
    },
    local: {
      whisper_model: 'base',
      sensevoice_path_configured: false,
      runtime_state: 'cached_unknown',
    },
    tts: {
      installed: true,
      voice: 'af_heart',
      speed: 1,
      language_voices: {
        es: 'ef_dora',
        fr: 'ff_siwis',
        hi: 'hf_alpha',
        it: 'if_sara',
        pt: 'pf_dora',
      },
    },
    live_providers: [
      {
        id: 'openai',
        label: 'OpenAI Realtime',
        company: 'OpenAI',
        price: 'about $0.10 a minute',
        price_checked: '22 Nov 2026',
        available: true,
        reason: 'available',
      },
      {
        id: 'gemini',
        label: 'Gemini Live',
        company: 'Google',
        price: 'about $0.02 a minute',
        price_checked: '22 Nov 2026',
        available: false,
        reason: 'realtime_key_missing',
      },
    ],
    talk_providers: [{ value: 'local', label: 'Local' }],
    dictation_providers: [{ value: 'local', label: 'Local' }],
    speech_output_providers: [{ value: 'local', label: 'Local' }],
    whisper_options: [{ value: 'base', label: 'Base' }],
    realtime_voice_options: [{ value: 'alloy', label: 'Alloy' }],
    tts_voice_options: [{ value: 'af_heart', label: 'Heart' }],
    language: {
      options: [
        { value: 'auto', label: 'Detect automatically' },
        { value: 'ar', label: 'Arabic' },
        { value: 'en', label: 'English' },
        { value: 'fr', label: 'French' },
        { value: 'de', label: 'German' },
        { value: 'hi', label: 'Hindi' },
        { value: 'it', label: 'Italian' },
        { value: 'pt', label: 'Portuguese' },
        { value: 'es', label: 'Spanish' },
      ],
      spoken: ['en', 'es', 'fr', 'hi', 'it', 'pt'],
      voice_options: [
        {
          language: 'es',
          label: 'Spanish',
          options: [
            { value: 'ef_dora', label: 'Dora (female)' },
            { value: 'em_alex', label: 'Alex (male)' },
          ],
        },
        {
          language: 'fr',
          label: 'French',
          options: [{ value: 'ff_siwis', label: 'Siwis (female)' }],
        },
      ],
      notices: [],
    },
  },
  system: {
    availability: 'available',
    workspace: { label: 'Workspace', configured: true, exists: true },
    shell: {
      available: true,
      enabled: true,
      blocked_patterns: 'format c:',
      allow_data_folder: false,
    },
    browser: {
      available: true,
      enabled: true,
      runtime_state: 'cached_unknown',
    },
    computer_use: {
      available: true,
      enabled: false,
      runtime_state: 'cached_unknown',
      screen_actions_available: true,
      platform: 'windows' as const,
      disclosure_acknowledged: true,
      system_binary_configured: false,
      status_message: 'Computer use is off.',
      remediation: '',
      disclosure_text: 'Cua Driver telemetry notice.',
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
      main_app_enabled: false,
      main_app_url: null,
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
    logging: { level: 'INFO', directory_available: true },
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
        last_event_at: '2026-09-14T18:42:00+01:00',
      },
    ],
    total_entries: 4,
  },
  knowledge: {
    availability: 'available',
    memory_available: true,
    memory_enabled: true,
    entities: 2,
    relations: 1,
    entity_types: [{ kind: 'person', count: 2 }],
    connected_components: 1,
    largest_component: 2,
    isolated_entities: 0,
  },
  wiki: {
    availability: 'available',
    enabled: true,
    vault_path: 'D:/Wiki',
    path_state: 'available',
    articles: 2,
    conversations: 1,
  },
  documents: {
    availability: 'available',
    indexed_documents: 12,
    active_embedding: 'Qwen3 0.6B (local)',
    document_vectors: {
      state: 'current',
      detail: 'Saved document vectors match the selected embedding setting.',
    },
    local_runtime: {
      state: 'cached',
      detail: 'Available in the local cache.',
    },
    memory_index: {
      state: 'pending',
      detail: 'Saved knowledge has pending semantic projection work.',
    },
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
      authentication_state: 'saved_unchecked',
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
    outlook_mail: { ...account, account_id: 'outlook_mail', enabled: false },
    outlook_calendar: {
      ...account,
      account_id: 'outlook_calendar',
      enabled: false,
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
      {
        utility_id: 'timer',
        label: 'Timer',
        description: 'Set lightweight local timers.',
        available: true,
        enabled: null,
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
    window_mode: 'native',
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
      runtime_state: 'cached',
    },
    migration: { available: false, sources: [] },
  },
  learning: {
    availability: 'available',
    enabled: false,
    paused: false,
    sources: { my_chats: true, workflows_and_goals: true, past_chats: true },
    apply_mode: 'ask_first',
    monthly_token_limit: 2_000_000,
    experimental: false,
    train_in_quiet_hours: true,
    train_where: 'this_computer',
    has_data: false,
  },
} satisfies SettingsSnapshot;

let mutation: SettingsMutationIO;
beforeEach(() => {
  sessionStorage.clear();
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

function renderSetting(setting: Phase4RetainedSetting | 'accounts') {
  // Access reads and writes the System snapshot page.
  mutation.page = setting === 'access' ? 'system' : setting;
  return render(
    <MemoryRouter>
      <OverlayProvider>
        {setting === 'accounts' ? (
          // Accounts are apps now; each app's settings show its account.
          <AccountsSnapshotPanel
            snapshot={snapshot.accounts}
            mutation={mutation}
          />
        ) : (
          <Phase4RetainedSettings
            setting={setting}
            snapshot={snapshot}
            mutation={mutation}
            selectedConversationId="conversation-a"
          />
        )}
      </OverlayProvider>
    </MemoryRouter>,
  );
}
/** Browser and computer use keep their reviewed setup behind "Set up…". */
function openSetup() {
  fireEvent.click(screen.getByRole('button', { name: 'Set up…' }));
  return screen.getByRole('dialog', { name: 'Browser and computer use' });
}
/** A save is confirmed by the floating notice (B258). */
function notice(text: string | RegExp) {
  return screen.findByText(text, { selector: '.toast *' });
}

afterEach(() => {
  setPlace(null);
  setMemoryLimit(null);
});

it('Voice says what runs where: this computer, or only the speech model on the Row-Bot server (U7)', () => {
  renderSetting('voice');
  expect(
    screen.getByText('On this computer keeps your voice private.'),
  ).toBeVisible();
  cleanup();
  setPlace({ kind: 'hosted', host_name: 'PikaPods' });
  renderSetting('voice');
  expect(
    screen.getAllByRole('option', { name: 'Whisper · on your Row-Bot server' }),
  ).toHaveLength(2);
  expect(
    screen.getByText(
      'Download the speech model to your Row-Bot server to talk',
    ),
  ).toBeVisible();
  // The audio does travel to the server: say so, not that it stays private.
  expect(
    screen.getByText(
      'Your voice goes only to your Row-Bot server at PikaPods, never to a speech service.',
    ),
  ).toBeVisible();
  expect(
    screen.getByText(
      'Downloads once from Hugging Face (Systran), then runs on your server.',
    ),
  ).toBeVisible();
  expect(document.body).not.toHaveTextContent(/private|offline/);
  expect(document.body).not.toHaveTextContent(/this computer/);
  cleanup();
  setPlace({ kind: 'server' });
  renderSetting('voice');
  expect(
    screen.getByText(
      'Your voice goes only to the computer Row-Bot runs on, never to a speech service.',
    ),
  ).toBeVisible();
  expect(document.body).not.toHaveTextContent(/this computer/);
});

it('renders real voice controls without probing a device or provider', async () => {
  renderSetting('voice');
  expect(screen.getByLabelText('Listen with')).toHaveValue('local');
  // The provider select says where it runs (B258).
  expect(
    screen.getAllByRole('option', { name: 'Whisper · on this computer' }),
  ).toHaveLength(2);
  // The speech model isn't downloaded here: the header says what to do, not that it's ready.
  expect(
    screen.getByText('Download the speech model to talk on this computer'),
  ).toBeVisible();
  expect(screen.getByLabelText('Speech model')).toHaveValue('base');
  // Listen with offers speech-to-text only: OpenAI Realtime is Live voice's.
  expect(
    within(screen.getByLabelText('Listen with'))
      .getAllByRole('option')
      .map((option) => option.textContent),
  ).toEqual(['Whisper · on this computer']);
  expect(document.body).not.toHaveTextContent('Realtime Talk online');
  expect(screen.queryByLabelText('Fall back to this computer')).toBeNull();
  expect(screen.queryByLabelText('Start automatically')).toBeNull();
  expect(screen.queryByLabelText('Provider voice')).toBeNull();
  // Read aloud says what speaks it; there is no switch that does nothing.
  expect(
    screen.getByText(
      'Replies you read aloud, Talk’s answers and voice-note replies speak with Kokoro, on this computer.',
    ),
  ).toBeVisible();
  expect(screen.getByLabelText('Voice')).toHaveValue('af_heart');
  expect(screen.getByLabelText('Speech speed')).toHaveValue(1);
  expect(screen.queryByLabelText('Read replies aloud')).toBeNull();
  expect(screen.queryByLabelText('Auto-speak voice responses')).toBeNull();
  fireEvent.click(screen.getByText('Advanced'));
  expect(screen.getByText('Whisper base')).toBeVisible();
  expect(screen.getByText('SenseVoice')).toBeVisible();
  expect(screen.getByText('Kokoro')).toBeVisible();
  // Live voice: each front's price, who hears the audio, and whether its key is saved.
  expect(screen.getByText('Key saved')).toBeVisible();
  expect(screen.getByText('Needs your Google API key')).toBeVisible();
  expect(
    screen.getByText(
      /About \$0\.02 a minute \(Google’s prices, 22 Nov 2026\)\. Your voice, the chat’s recent messages, the agent profile’s name and a few related memories go to Google \(no memories in an incognito chat\)\./,
    ),
  ).toBeVisible();
  expect(screen.getAllByText(/Not checked/).length).toBeGreaterThan(0);
  expect(
    screen.queryByRole('button', {
      name: /OpenAI Realtime API key/,
    }),
  ).toBeNull();
  // OpenAI Realtime's voice and model sit under Live voice, whatever Talk listens with.
  expect(screen.getByLabelText('Realtime voice')).toBeVisible();
  const model = screen.getByLabelText('Realtime model');
  expect(model).toHaveValue('gpt-realtime-2-mini');
  fireEvent.change(model, { target: { value: 'gpt-realtime-2' } });
  fireEvent.blur(model);
  // Nothing probes a device.
  await waitFor(() =>
    expect(mutation.review).toHaveBeenCalledWith(
      expect.objectContaining({
        field: 'runtime.realtime_model',
        value: 'gpt-realtime-2',
      }),
      expect.any(AbortSignal),
    ),
  );
  expect(
    screen.getByRole('link', { name: 'Open conversation voice' }),
  ).toHaveAttribute('href', '/conversations/conversation-a');
  expect(document.body).not.toHaveTextContent('must-not-render');
});

it('keeps OpenAI Realtime’s voice under Live voice and hides uninstalled local TTS controls', () => {
  mutation.page = 'voice';
  render(
    <MemoryRouter>
      <VoiceSnapshotPanel
        snapshot={{
          ...snapshot.voice,
          tts: { ...snapshot.voice.tts, installed: false },
        }}
        conversationId="conversation-a"
        mutation={mutation}
      />
    </MemoryRouter>,
  );
  expect(screen.getByLabelText('Live captions')).toBeChecked();
  expect(screen.getByLabelText('When you pause')).toHaveValue('normal');
  expect(screen.getByLabelText('Realtime voice')).toHaveValue('alloy');
  // Kokoro's install is a text action in the Read aloud row's status line.
  expect(screen.getByText('Kokoro isn’t installed yet')).toBeVisible();
  expect(screen.getByText('Kokoro voice')).toBeVisible();
  expect(screen.queryByLabelText('Speech speed')).toBeNull();
  const install = screen.getByRole('button', { name: 'Install Kokoro TTS' });
  expect(install).toHaveTextContent('Install');
  fireEvent.click(install);
  expect(mutation.review).toHaveBeenCalledWith(
    expect.objectContaining({
      page: 'voice',
      field: 'tts.install',
      value: true,
    }),
    expect.any(AbortSignal),
  );
});

function renderVoiceWith(speechTest: (signal: AbortSignal) => Promise<Blob>) {
  mutation.page = 'voice';
  const controller = {
    speechTest,
    dictationCapability: async () => ({ wake_reason: 'not_this_computer' }),
  } as unknown as ClientController;
  return render(
    <RuntimeContext.Provider
      value={{ controller, platform: {} as ClientPlatform }}
    >
      <MemoryRouter>
        <OverlayProvider>
          <Phase4RetainedSettings
            setting="voice"
            snapshot={snapshot}
            mutation={mutation}
            selectedConversationId="conversation-a"
          />
        </OverlayProvider>
      </MemoryRouter>
    </RuntimeContext.Provider>,
  );
}

it('plays Test voice in this browser instead of a reviewed host action', async () => {
  const original = {
    create: URL.createObjectURL,
    revoke: URL.revokeObjectURL,
  };
  const revoke = vi.fn();
  URL.createObjectURL = vi.fn(() => 'blob:voice-test');
  URL.revokeObjectURL = revoke;
  try {
    const played: string[] = [];
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function (
      this: HTMLMediaElement,
    ) {
      played.push(this.src);
      queueMicrotask(() => this.dispatchEvent(new Event('ended')));
      return Promise.resolve();
    });
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
    const speechTest = vi.fn(
      async () => new Blob(['RIFF'], { type: 'audio/wav' }),
    );
    renderVoiceWith(speechTest);
    // Test voice is an icon beside the Voice select (B258).
    const button = screen.getByRole('button', { name: 'Test voice' });
    fireEvent.click(button);
    await waitFor(() => expect(revoke).toHaveBeenCalledWith('blob:voice-test'));
    expect(played).toEqual(['blob:voice-test']);
    expect(speechTest).toHaveBeenCalledTimes(1);
    expect(button).toBeEnabled();
    expect(mutation.review).not.toHaveBeenCalled();
  } finally {
    URL.createObjectURL = original.create;
    URL.revokeObjectURL = original.revoke;
  }
});

it('says why Test voice could not speak, and waits for a client', async () => {
  const play = vi.spyOn(HTMLMediaElement.prototype, 'play');
  const view = renderVoiceWith(async () => {
    throw { code: 'voice_service_busy' };
  });
  fireEvent.click(screen.getByRole('button', { name: 'Test voice' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Speech recognition is busy.',
  );
  expect(play).not.toHaveBeenCalled();
  view.unmount();
  renderSetting('voice');
  expect(screen.getByRole('button', { name: 'Test voice' })).toBeDisabled();
});

it('reviews SenseVoice setup only after an explicit action', async () => {
  renderSetting('voice');
  fireEvent.click(screen.getByText('Advanced'));
  fireEvent.click(
    screen.getByRole('button', { name: 'Install SenseVoice Small' }),
  );
  await waitFor(() =>
    expect(mutation.review).toHaveBeenCalledWith(
      expect.objectContaining({ field: 'sensevoice.install', value: true }),
      expect.any(AbortSignal),
    ),
  );
});

it('keeps the data folder closed to the agent until the person opts in', async () => {
  renderSetting('system');
  fireEvent.click(screen.getByText('Advanced'));
  const toggle = screen.getByRole('switch', {
    name: "Let the agent read Row-Bot's data folder",
  });
  expect(toggle).not.toBeChecked();
  fireEvent.click(toggle);
  await waitFor(() =>
    expect(mutation.review).toHaveBeenCalledWith(
      expect.objectContaining({
        field: 'shell.allow_data_folder',
        value: true,
      }),
      expect.any(AbortSignal),
    ),
  );
});

it('does not expose writable System fields when their tool owner is unavailable', () => {
  mutation.page = 'system';
  render(
    <SystemSnapshotPanel
      snapshot={{
        ...snapshot.system,
        shell: { ...snapshot.system.shell, available: false, enabled: null },
        browser: {
          ...snapshot.system.browser,
          available: false,
          enabled: null,
        },
        computer_use: {
          ...snapshot.system.computer_use,
          available: false,
          enabled: null,
        },
        file_operations: {
          ...snapshot.system.file_operations,
          available: false,
          enabled: null,
        },
      }}
      mutation={mutation}
    />,
  );
  expect(screen.getByText('Shell tool not found')).toBeVisible();
  expect(screen.getByText('Browser tool not found')).toBeVisible();
  expect(screen.getByText("Computer use isn't available here")).toBeVisible();
  expect(screen.getByText('Filesystem tool unavailable')).toBeVisible();
  expect(screen.queryByLabelText('Blocked commands')).toBeNull();
  expect(
    screen.queryByRole('switch', { name: /Row-Bot's data folder/ }),
  ).toBeNull();
  expect(
    screen.queryByRole('radiogroup', { name: 'Work with files' }),
  ).toBeNull();
  expect(screen.queryByLabelText('Allowed operations')).toBeNull();
});

it('groups available filesystem operations behind Choose each and keeps runtime detail supplemental', () => {
  renderSetting('system');
  // Only read_file of read, write and delete: a choice made one by one.
  expect(
    screen.getByRole('radiogroup', { name: 'Work with files' }),
  ).toBeVisible();
  expect(
    screen.getByText(/Read\. Moving and deleting stay off\./),
  ).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Choose each…' }));
  const choose = screen.getByRole('dialog', {
    name: 'Choose each file operation',
  });
  expect(within(choose).getByText('Read-only')).toBeVisible();
  expect(within(choose).getByText('Write')).toBeVisible();
  expect(within(choose).getByText('Destructive')).toBeVisible();
  expect(within(choose).getByLabelText('Read files')).toBeChecked();
  fireEvent.click(within(choose).getByRole('button', { name: 'Done' }));
  const setup = openSetup();
  expect(within(setup).getAllByText(/Not checked/).length).toBeGreaterThan(0);
  expect(
    within(setup).getByText('Setup details').closest('details'),
  ).not.toHaveAttribute('open');
});

it('turns file access to a step through the same two saved fields (B258)', async () => {
  let current: SettingsSnapshot = structuredClone(snapshot);
  mutation.page = 'system';
  mutation.execute = vi.fn(async (request, _review, commandId) => {
    current = structuredClone(current);
    current.revision = `${current.revision}+`;
    if (request.field === 'file_operations.enabled')
      current.system.file_operations.enabled = request.value as boolean;
    if (request.field === 'file_operations.selected')
      current.system.file_operations.selected = request.value as string[];
    return {
      command_id: commandId,
      status: 'completed' as const,
      settings_revision: current.revision,
      snapshot: current,
    };
  });
  const view = render(
    <OverlayProvider>
      <SystemSnapshotPanel snapshot={current.system} mutation={mutation} />
    </OverlayProvider>,
  );
  mutation.onSnapshot = vi.fn((next) =>
    view.rerender(
      <OverlayProvider>
        <SystemSnapshotPanel
          snapshot={next.system}
          mutation={{ ...mutation, revision: next.revision }}
        />
      </OverlayProvider>,
    ),
  );
  const steps = screen.getByRole('radiogroup', { name: 'Work with files' });
  fireEvent.click(within(steps).getByRole('radio', { name: 'Read & write' }));
  // The options offer read_file, write_file and file_delete: Read & write
  // allows the first two; deleting stays off unless "All".
  await waitFor(() =>
    expect(mutation.review).toHaveBeenCalledWith(
      expect.objectContaining({
        field: 'file_operations.selected',
        value: ['read_file', 'write_file'],
      }),
    ),
  );
  expect(await notice('File access: read and write')).toBeInTheDocument();
  expect(
    within(steps).getByRole('radio', { name: 'Read & write' }),
  ).toHaveAttribute('aria-checked', 'true');
  fireEvent.click(within(steps).getByRole('radio', { name: 'Off' }));
  await waitFor(() =>
    expect(mutation.review).toHaveBeenLastCalledWith(
      expect.objectContaining({
        field: 'file_operations.enabled',
        value: false,
      }),
    ),
  );
  expect(screen.getByText('Row-Bot can’t read or change files.')).toBeVisible();
  // Coming back from Off switches the tool on first, then sets the step.
  fireEvent.click(within(steps).getByRole('radio', { name: 'All' }));
  await waitFor(() =>
    expect(mutation.review).toHaveBeenLastCalledWith(
      expect.objectContaining({
        field: 'file_operations.selected',
        value: ['read_file', 'write_file', 'file_delete'],
      }),
    ),
  );
  expect(mutation.review).toHaveBeenCalledWith(
    expect.objectContaining({ field: 'file_operations.enabled', value: true }),
  );
});

it('shows the Cua disclosure before enabling and accepts it with one toggle', async () => {
  mutation.page = 'system';
  render(
    <SystemSnapshotPanel
      snapshot={{
        ...snapshot.system,
        computer_use: {
          ...snapshot.system.computer_use,
          disclosure_acknowledged: false,
          runtime_state: 'disclosure_required',
        },
      }}
      mutation={mutation}
    />,
  );
  const setup = openSetup();
  expect(within(setup).getByText('Cua Driver telemetry notice.')).toBeVisible();
  expect(within(setup).queryByLabelText('Computer use')).toBeNull();
  expect(mutation.review).not.toHaveBeenCalled();
  fireEvent.click(
    within(setup).getByLabelText('Accept Cua Driver telemetry notice'),
  );
  await waitFor(() =>
    expect(mutation.execute).toHaveBeenCalledWith(
      expect.objectContaining({
        page: 'system',
        field: 'computer_use.disclosure_acknowledged',
        value: true,
      }),
      expect.anything(),
      expect.any(String),
    ),
  );
});

it('runs Computer Use diagnostics only after the explicit click', async () => {
  mutation.page = 'system';
  mutation.execute = vi.fn(async (_request, _review, commandId) => ({
    command_id: commandId,
    status: 'completed' as const,
    settings_revision: snapshot.revision,
    snapshot,
    action_result: {
      code: 'permission_missing',
      message: 'Cua Driver diagnostics need attention.',
      remediation: 'Grant the required permissions, then check again.',
    },
  }));
  render(
    <SystemSnapshotPanel snapshot={snapshot.system} mutation={mutation} />,
  );
  openSetup();
  expect(screen.getByText('Computer use is off.')).toBeVisible();
  expect(mutation.review).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Check setup' }));
  await waitFor(() =>
    expect(mutation.execute).toHaveBeenCalledWith(
      expect.objectContaining({
        page: 'system',
        field: 'computer_use.check',
        value: true,
      }),
      expect.anything(),
      expect.any(String),
    ),
  );
  expect(
    await screen.findByText(/Grant the required permissions, then check again/),
  ).toBeVisible();
});

it('says a failed Computer Use install failed, and what to do (B377)', async () => {
  mutation.page = 'system';
  mutation.execute = vi.fn(async (_request, _review, commandId) => ({
    command_id: commandId,
    status: 'rejected' as const,
    code: 'settings_action_failed',
    action_result: {
      code: 'failed',
      message: "Row-Bot couldn't install Cua Driver.",
      remediation:
        'Check your connection and try again. Settings › System › Logs says why.',
    },
  }));
  render(
    <SystemSnapshotPanel snapshot={snapshot.system} mutation={mutation} />,
  );
  openSetup();
  fireEvent.click(screen.getByRole('button', { name: 'Install Cua Driver' }));
  expect(
    await screen.findByText(
      /Row-Bot couldn't install Cua Driver\. Check your connection and try again/,
    ),
  ).toBeVisible();
  expect(screen.queryByText(/couldn't confirm/)).toBeNull();
});

it('sets up Computer Use from another device, leaving its screen actions to the desktop app', () => {
  mutation.page = 'system';
  render(
    <SystemSnapshotPanel
      snapshot={{
        ...snapshot.system,
        computer_use: {
          ...snapshot.system.computer_use,
          platform: 'macos',
          runtime_state: 'ready',
          screen_actions_available: false,
        },
      }}
      mutation={mutation}
    />,
  );
  openSetup();
  // Every signed-in device has the owner's authority over its setup.
  expect(screen.getByRole('switch', { name: 'Computer use' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Check setup' })).toBeVisible();
  expect(
    screen.getByRole('button', { name: 'Install Cua Driver' }),
  ).toBeVisible();
  // Testing and the macOS panes happen on that computer's own screen.
  expect(
    screen.getByText(
      /Testing and the macOS privacy settings open on the computer Row-Bot runs on/,
    ),
  ).toBeVisible();
  expect(
    screen.queryByRole('button', { name: 'Test with Calculator' }),
  ).toBeNull();
  expect(screen.queryByText('macOS permission recovery')).toBeNull();
  expect(screen.queryByText('Host setup is local only')).toBeNull();
  expect(mutation.review).not.toHaveBeenCalled();
});

it('tests a ready Computer Use runtime and verifies an explicit system binary', async () => {
  mutation.page = 'system';
  mutation.execute = vi.fn(async (_request, _review, commandId) => ({
    command_id: commandId,
    status: 'completed' as const,
    settings_revision: snapshot.revision,
    snapshot,
    action_result: {
      code: 'ready',
      message: 'Local Computer Use check passed.',
      remediation: '',
    },
  }));
  render(
    <SystemSnapshotPanel
      snapshot={{
        ...snapshot.system,
        computer_use: {
          ...snapshot.system.computer_use,
          runtime_state: 'ready',
          system_binary_configured: true,
        },
      }}
      mutation={mutation}
    />,
  );
  openSetup();
  fireEvent.click(screen.getByRole('button', { name: 'Test with Calculator' }));
  await waitFor(() =>
    expect(mutation.execute).toHaveBeenCalledWith(
      expect.objectContaining({ field: 'computer_use.test' }),
      expect.anything(),
      expect.any(String),
    ),
  );
  fireEvent.click(screen.getByText('Use your own Cua Driver'));
  const input = screen.getByLabelText('Verify system Cua executable');
  fireEvent.change(input, {
    target: { value: 'C:\\synthetic\\cua-driver.exe' },
  });
  const control = input.closest('.settings-saved-control');
  expect(control).not.toBeNull();
  // Enter in a text field saves it (decision 19); there is no Save button.
  expect(
    within(control as HTMLElement).queryByRole('button', { name: 'Save' }),
  ).toBeNull();
  fireEvent.keyDown(input, { key: 'Enter' });
  await waitFor(() =>
    expect(mutation.execute).toHaveBeenCalledWith(
      expect.objectContaining({
        field: 'computer_use.system_binary_verify',
        value: 'C:\\synthetic\\cua-driver.exe',
      }),
      expect.anything(),
      expect.any(String),
    ),
  );
  expect(input).toHaveValue('');
  expect(
    screen.getByRole('button', { name: "Use Row-Bot's Cua Driver" }),
  ).toBeVisible();
});

it('requires confirmation only for removing the managed Computer Use runtime', async () => {
  mutation.page = 'system';
  render(
    <SystemSnapshotPanel snapshot={snapshot.system} mutation={mutation} />,
  );
  fireEvent.click(screen.getByText('Danger zone'));
  fireEvent.click(screen.getByRole('button', { name: 'Remove Cua Driver' }));
  expect(mutation.review).not.toHaveBeenCalled();
  expect(
    screen.getByText(/This deletes Row-Bot's copy of Cua Driver/),
  ).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(mutation.review).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Remove Cua Driver' }));
  fireEvent.click(
    screen.getAllByRole('button', { name: 'Remove Cua Driver' }).at(-1)!,
  );
  await waitFor(() =>
    expect(mutation.execute).toHaveBeenCalledWith(
      expect.objectContaining({ field: 'computer_use.remove' }),
      expect.anything(),
      expect.any(String),
    ),
  );
});

it('offers one-click macOS permission recovery only on the local Mac host', async () => {
  mutation.page = 'system';
  render(
    <SystemSnapshotPanel
      snapshot={{
        ...snapshot.system,
        computer_use: { ...snapshot.system.computer_use, platform: 'macos' },
      }}
      mutation={mutation}
    />,
  );
  openSetup();
  fireEvent.click(screen.getByText('macOS permission recovery'));
  fireEvent.click(
    screen.getByRole('button', { name: 'Open Accessibility settings' }),
  );
  await waitFor(() =>
    expect(mutation.execute).toHaveBeenCalledWith(
      expect.objectContaining({ field: 'computer_use.open_accessibility' }),
      expect.anything(),
      expect.any(String),
    ),
  );
});

it('keeps System install network tunnel and OS actions explicit and reviewed', async () => {
  mutation.page = 'system';
  render(
    <SystemSnapshotPanel snapshot={snapshot.system} mutation={mutation} />,
  );
  expect(mutation.review).not.toHaveBeenCalled();
  expect(mutation.execute).not.toHaveBeenCalled();
  openSetup();
  fireEvent.click(
    screen.getByRole('button', { name: 'Install browser runtime' }),
  );
  await waitFor(() =>
    expect(mutation.review).toHaveBeenCalledWith(
      expect.objectContaining({
        page: 'system',
        field: 'browser.install',
        value: true,
      }),
      expect.any(AbortSignal),
    ),
  );
});

it('shows ngrok setup links only after opening the guide without a tunnel action', () => {
  renderSetting('access');
  expect(mutation.review).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText('Tunnel setup'));
  const provider = screen.getByRole('link', { name: 'ngrok.com' });
  const dashboard = screen.getByRole('link', { name: 'ngrok dashboard' });
  expect(provider).toHaveAttribute('href', 'https://ngrok.com/');
  expect(dashboard).toHaveAttribute(
    'href',
    'https://dashboard.ngrok.com/get-started/your-authtoken',
  );
  expect(provider).toHaveAttribute('rel', 'noopener noreferrer');
  expect(dashboard).toHaveAttribute('rel', 'noopener noreferrer');
  expect(mutation.review).not.toHaveBeenCalled();
  expect(mutation.execute).not.toHaveBeenCalled();
});

it('shows a running public link in one line with its address, Copy and Stop (row 51)', async () => {
  mutation.page = 'system';
  const writeText = vi
    .fn()
    .mockRejectedValueOnce(new Error('clipboard blocked'))
    .mockResolvedValueOnce(undefined);
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText },
  });
  render(
    <SystemSnapshotPanel
      snapshot={{
        ...snapshot.system,
        tunnel: {
          ...snapshot.system.tunnel,
          runtime_state: 'active',
          active_count: 1,
          main_app_enabled: true,
          main_app_url: 'https://synthetic.ngrok.example',
        },
      }}
      mutation={mutation}
      part="access"
    />,
  );
  expect(
    screen.getByText(/On at synthetic\.ngrok\.example\./),
  ).toBeInTheDocument();
  // The task webhook's address lives with its workflow now (row 52).
  expect(screen.queryByText(/api\/webhook/)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Copy address' }));
  expect(
    await screen.findByText('Row-Bot couldn’t copy it.'),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Copy address' }));
  expect(await screen.findByText('Copied.')).toBeInTheDocument();
  expect(writeText).toHaveBeenCalledWith('https://synthetic.ngrok.example');
  expect(
    screen.getByRole('button', { name: 'Stop public link' }),
  ).toBeInTheDocument();
  expect(mutation.review).not.toHaveBeenCalled();
});

it('asks before stopping the public link, which a device using it would lose', async () => {
  mutation.page = 'system';
  render(
    <SystemSnapshotPanel
      snapshot={{
        ...snapshot.system,
        tunnel: {
          ...snapshot.system.tunnel,
          runtime_state: 'active',
          active_count: 1,
          main_app_enabled: true,
          main_app_url: 'https://synthetic.ngrok.example',
        },
      }}
      mutation={mutation}
      part="access"
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Stop public link' }));
  expect(mutation.review).not.toHaveBeenCalled();
  expect(
    screen.getByText(/this one too if it does, lose their connection/),
  ).toBeVisible();
  fireEvent.click(
    screen.getAllByRole('button', { name: 'Stop public link' }).at(-1)!,
  );
  await waitFor(() =>
    expect(mutation.execute).toHaveBeenCalledWith(
      expect.objectContaining({ field: 'tunnel.stop_main' }),
      expect.anything(),
      expect.any(String),
    ),
  );
  expect(
    screen.queryByText(/Only Row-Bot’s owner on the computer running it/),
  ).toBeNull();
});

it('shows the real tunnel state and a failed start in words (B106)', () => {
  mutation.page = 'system';
  const failure =
    'ngrok refused a new tunnel: your ngrok account already has as many agents running as it allows.';
  const view = render(
    <SystemSnapshotPanel
      snapshot={{
        ...snapshot.system,
        tunnel: {
          ...snapshot.system.tunnel,
          runtime_state: 'failed',
          active_count: 0,
          last_error: failure,
        },
      }}
      mutation={mutation}
      part="access"
    />,
  );
  expect(screen.getByRole('alert')).toHaveTextContent(
    `Public Off. It didn’t start: ${failure}`,
  );
  expect(document.body).not.toHaveTextContent(/Ready|Runtime status/);
  view.rerender(
    <SystemSnapshotPanel
      snapshot={{
        ...snapshot.system,
        tunnel: {
          ...snapshot.system.tunnel,
          runtime_state: 'active',
          active_count: 1,
          last_error: null,
        },
      }}
      mutation={mutation}
      part="access"
    />,
  );
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.getByText(/On: 1 public address\./)).toBeInTheDocument();
});

it('recovers an interrupted tunnel command from its original receipt after remount', async () => {
  mutation.page = 'system';
  mutation.sessionId = 'fixture-tunnel-session';
  mutation.execute = vi.fn(async () => {
    throw new Error('synthetic response lost');
  });
  mutation.receipt = vi.fn(async (commandId) => ({
    command_id: commandId,
    status: 'partial' as const,
    code: 'settings_save_unconfirmed',
    settings_revision: null,
    snapshot: null,
  }));
  mutation.refreshSnapshot = vi.fn(async () => snapshot);
  const first = render(
    <SystemSnapshotPanel
      snapshot={snapshot.system}
      mutation={mutation}
      part="access"
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Start public link' }));
  // Starting the public link says what it means first.
  expect(mutation.review).not.toHaveBeenCalled();
  fireEvent.click(
    within(await screen.findByRole('dialog')).getByRole('button', {
      name: 'Start public link',
    }),
  );
  expect(
    await screen.findByRole('button', { name: 'Check again' }),
  ).toBeInTheDocument();
  const priorReviews = vi.mocked(mutation.review).mock.calls.length;
  first.unmount();
  render(
    <SystemSnapshotPanel
      snapshot={snapshot.system}
      mutation={mutation}
      part="access"
    />,
  );
  expect(
    screen.getByRole('button', { name: 'Check again' }),
  ).toBeInTheDocument();
  expect(vi.mocked(mutation.review).mock.calls.length).toBe(priorReviews);
  fireEvent.click(screen.getByRole('button', { name: 'Check again' }));
  expect(
    await screen.findByRole('button', { name: 'Inspect current tunnel state' }),
  ).toBeInTheDocument();
  fireEvent.click(
    screen.getByRole('button', { name: 'Inspect current tunnel state' }),
  );
  expect(
    await screen.findByRole('button', { name: 'Try another tunnel action' }),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/Current saved restart choice: disabled/),
  ).toBeInTheDocument();
  expect(mutation.refreshSnapshot).toHaveBeenCalledTimes(1);
  fireEvent.click(
    screen.getByRole('button', { name: 'Try another tunnel action' }),
  );
  expect(
    screen.getByRole('button', { name: 'Start public link' }),
  ).toBeInTheDocument();
  expect(mutation.execute).toHaveBeenCalledTimes(1);
});

it('uses an opaque local-owner folder grant and never renders a workspace path', async () => {
  mutation.page = 'system';
  const pickFolder = vi.fn().mockResolvedValue({
    status: 'selected' as const,
    grant_id: 'g'.repeat(43),
    name: 'Selected workspace',
  });
  render(
    <SystemSnapshotPanel
      snapshot={snapshot.system}
      mutation={mutation}
      pickFolder={pickFolder}
    />,
  );
  expect(document.body).not.toHaveTextContent('D:/Workspace');
  // The folder shows as a chip with Change… beside it (B258).
  expect(
    screen.getByText('Workspace', { selector: '.settings-folder-chip span' }),
  ).toBeVisible();
  fireEvent.click(
    screen.getByRole('button', { name: 'Change workspace folder' }),
  );
  expect(await screen.findByText('Selected: Selected workspace')).toBeVisible();
  // The pick itself is the change: it saves at once, without Undo.
  await waitFor(() =>
    expect(mutation.review).toHaveBeenCalledWith(
      expect.objectContaining({
        page: 'system',
        field: 'workspace.folder_grant',
        value: 'g'.repeat(43),
      }),
      expect.any(AbortSignal),
    ),
  );
});

it('saves one retained setting when the field is left, once, and offers Undo (decision 19)', async () => {
  renderSetting('system');
  fireEvent.click(screen.getByText('Advanced'));
  const model = screen.getByLabelText('Blocked commands');
  fireEvent.change(model, {
    target: { value: 'medium' },
  });
  expect(mutation.review).not.toHaveBeenCalled();
  expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
  expect(screen.queryByRole('button', { name: /^Revert/ })).toBeNull();
  fireEvent.blur(model);
  await waitFor(() => expect(mutation.execute).toHaveBeenCalledTimes(1));
  const saved = await notice('Blocked commands saved');
  // Never a line in the page (B258).
  expect(document.querySelector('.settings-saved-note')).toBeNull();
  fireEvent.click(
    within(saved.closest('li')!).getByRole('button', { name: 'Undo' }),
  );
  await waitFor(() => expect(mutation.execute).toHaveBeenCalledTimes(2));
  expect(mutation.review).toHaveBeenLastCalledWith(
    expect.objectContaining({
      field: 'shell.blocked_patterns',
      value: 'format c:',
    }),
    expect.any(AbortSignal),
  );
  expect(await notice('Blocked commands changed back')).toBeInTheDocument();
  expect(mutation.review).toHaveBeenCalledWith(
    expect.objectContaining({
      page: 'system',
      field: 'shell.blocked_patterns',
      value: 'medium',
    }),
    expect.any(AbortSignal),
  );
  expect(mutation.onSnapshot).toHaveBeenCalledWith(
    expect.objectContaining({ revision: 'settings-b' }),
  );
});

it('marks a field that differs from its default and resets it in one step', async () => {
  mutation.defaults = { 'shell.blocked_patterns': '' };
  const { container } = renderSetting('system');
  fireEvent.click(screen.getByText('Advanced'));
  const row = screen
    .getByLabelText('Blocked commands')
    .closest('.settings-saved-control')!;
  expect(row).toHaveAttribute('data-modified', 'true');
  expect(row.querySelector('.settings-modified-dot')).not.toBeNull();
  // The mark never becomes part of the control's name.
  expect(
    screen.getByRole('textbox', { name: 'Blocked commands' }),
  ).toBeVisible();
  // A field already at its default offers no reset.
  expect(
    screen.queryByRole('button', { name: 'Reset Log detail to default' }),
  ).toBeNull();
  fireEvent.click(
    screen.getByRole('button', { name: 'Reset Blocked commands to default' }),
  );
  await waitFor(() => expect(mutation.execute).toHaveBeenCalledTimes(1));
  expect(mutation.review).toHaveBeenCalledWith(
    expect.objectContaining({
      page: 'system',
      field: 'shell.blocked_patterns',
      value: '',
    }),
    expect.any(AbortSignal),
  );
  expect(container.querySelectorAll('[data-modified="true"]')).toHaveLength(1);
  mutation.defaults = undefined;
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
  renderSetting('system');
  fireEvent.change(screen.getByLabelText('Blocked commands'), {
    target: { value: 'medium' },
  });
  fireEvent.keyDown(screen.getByLabelText('Blocked commands'), {
    key: 'Enter',
  });
  fireEvent.click(await screen.findByRole('button', { name: 'Check again' }));
  await waitFor(() => expect(mutation.receipt).toHaveBeenCalledTimes(1));
  expect(execute).toHaveBeenCalledTimes(1);
  expect(mutation.onSnapshot).toHaveBeenCalledWith(
    expect.objectContaining({ revision: 'settings-b' }),
  );
});

it('reviews and cancels tracker deletion without executing it', async () => {
  renderSetting('tracker');
  const danger = screen.getByText('Danger zone').closest('details')!;
  expect(danger).not.toHaveAttribute('open');
  fireEvent.click(screen.getByText('Danger zone'));

  fireEvent.click(screen.getByRole('button', { name: 'Delete all trackers' }));
  expect(await screen.findByText('saved locally')).toBeVisible();
  expect(mutation.review).toHaveBeenCalledWith(
    {
      settings_revision: snapshot.revision,
      page: 'tracker',
      field: 'delete_all',
      value: true,
    },
    expect.any(AbortSignal),
  );

  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

  expect(screen.getByText(/Deletion cancelled/)).toBeVisible();
  expect(mutation.execute).not.toHaveBeenCalled();
  expect(
    screen.getByRole('button', { name: 'Delete all trackers' }),
  ).toBeEnabled();
});

it('retires a tracker deletion review when the Settings revision changes', async () => {
  mutation.page = 'tracker';
  const view = render(
    <TrackerSnapshotPanel snapshot={snapshot.tracker} mutation={mutation} />,
  );
  fireEvent.click(screen.getByText('Danger zone'));
  fireEvent.click(screen.getByRole('button', { name: 'Delete all trackers' }));
  expect(await screen.findByText('saved locally')).toBeVisible();

  mutation = { ...mutation, revision: 'settings-newer' };
  view.rerender(
    <TrackerSnapshotPanel snapshot={snapshot.tracker} mutation={mutation} />,
  );

  expect(
    await screen.findByText(/Tracker data changed. Review the deletion again/),
  ).toBeVisible();
  expect(
    screen.queryByRole('button', {
      name: 'Confirm permanent deletion',
    }),
  ).toBeNull();
  expect(mutation.execute).not.toHaveBeenCalled();
});

it('checks the original tracker deletion receipt without replaying it', async () => {
  mutation.execute = vi.fn().mockRejectedValue({ code: 'operation_uncertain' });
  mutation.receipt = vi.fn(
    async (commandId): Promise<SettingsMutationReceipt> => ({
      command_id: commandId,
      status: 'completed',
      settings_revision: 'settings-b',
      snapshot: {
        ...snapshot,
        revision: 'settings-b',
        tracker: { ...snapshot.tracker, items: [], total_entries: 0 },
      },
    }),
  );
  renderSetting('tracker');
  fireEvent.click(screen.getByRole('button', { name: 'Delete all trackers' }));
  await screen.findByText('saved locally');
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm permanent deletion' }),
  );
  fireEvent.click(await screen.findByRole('button', { name: 'Check again' }));

  await waitFor(() => expect(mutation.receipt).toHaveBeenCalledTimes(1));
  expect(mutation.execute).toHaveBeenCalledTimes(1);
  expect(mutation.onSnapshot).toHaveBeenCalledWith(
    expect.objectContaining({
      revision: 'settings-b',
      tracker: expect.objectContaining({ items: [], total_entries: 0 }),
    }),
  );
});

it('retains an unsaved draft across pages and Escape puts the saved value back', () => {
  const first = renderSetting('system');
  fireEvent.change(screen.getByLabelText('Blocked commands'), {
    target: { value: 'medium' },
  });
  first.unmount();

  expect(mutation.review).not.toHaveBeenCalled();
  expect(mutation.execute).not.toHaveBeenCalled();
  renderSetting('system');
  expect(screen.getByLabelText('Blocked commands')).toHaveValue('medium');
  fireEvent.keyDown(screen.getByLabelText('Blocked commands'), {
    key: 'Escape',
  });
  expect(screen.getByLabelText('Blocked commands')).toHaveValue('format c:');
  expect(mutation.review).not.toHaveBeenCalled();
});

it('does not carry a write draft into a new authenticated session owner', () => {
  const first = renderSetting('accounts');
  fireEvent.click(screen.getByText('GitHub'));
  fireEvent.click(
    screen.getByRole('button', { name: 'Replace or remove GitHub token' }),
  );
  fireEvent.change(screen.getByLabelText('GitHub token'), {
    target: { value: 'sensitive-local-draft' },
  });
  first.unmount();

  mutation = { ...mutation, drafts: new SettingsDraftOwner() };
  renderSetting('accounts');
  fireEvent.click(screen.getByText('GitHub'));
  fireEvent.click(
    screen.getByRole('button', { name: 'Replace or remove GitHub token' }),
  );
  expect(screen.getByLabelText('GitHub token')).toHaveValue('');
  expect(mutation.review).not.toHaveBeenCalled();
  expect(mutation.execute).not.toHaveBeenCalled();
});

it('renders System, Tracker, Accounts, and Utilities controls from one snapshot', () => {
  const system = renderSetting('system');
  expect(screen.getByText('Workspace folder ready')).toBeVisible();
  expect(
    screen.getByText('Workspace', { selector: '.settings-folder-chip span' }),
  ).toBeVisible();
  expect(screen.queryByDisplayValue(/D:\/Workspace/)).toBeNull();
  expect(screen.getByLabelText('Log detail')).toHaveValue('INFO');
  expect(screen.getByRole('option', { name: 'Normal' })).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: 'Mobile Access' })).toBeNull();
  // Remote reach moved to Access.
  expect(screen.queryByText('Public link')).toBeNull();
  system.unmount();
  const access = renderSetting('access');
  // Access keeps its rarely changed options in Advanced (Phase 14).
  expect(screen.getByText('Public link')).toBeInTheDocument();
  expect(screen.getByLabelText('Tunnel provider')).toHaveValue('ngrok');
  access.unmount();

  const tracker = renderSetting('tracker');
  expect(screen.getByLabelText('Track in chat')).toBeChecked();
  expect(screen.getByText(/Water/)).toBeVisible();
  // Last entries read as relative time with the full date on hover.
  expect(
    tracker.container.querySelector('time[datetime^="2026-09-14"]'),
  ).not.toBeNull();
  tracker.unmount();

  const accounts = renderSetting('accounts');
  // One row per account with one status (B263); each setup guide folds.
  expect(accounts.container.querySelectorAll('.settings-account')).toHaveLength(
    4,
  );
  expect(screen.getByText('Not checked yet')).toBeVisible();
  fireEvent.click(screen.getByText('How to set up GitHub'));
  expect(screen.queryByLabelText('GitHub token')).not.toBeInTheDocument();
  fireEvent.click(
    screen.getByRole('button', { name: 'Replace or remove GitHub token' }),
  );
  expect(screen.getByLabelText('GitHub token')).toHaveAttribute(
    'type',
    'password',
  );
  expect(screen.getByLabelText('Gmail access')).toBeChecked();
  expect(screen.getByLabelText('Calendar access')).not.toBeChecked();
  expect(accounts.container.textContent).not.toMatch(
    /[A-Z]:\\|\/Users\/|\/home\//,
  );
  expect(screen.getByText('Google sign-in file')).toBeVisible();
  fireEvent.click(
    screen.getByRole('button', { name: 'Choose x access actions' }),
  );
  expect(screen.getByText('Search posts')).toBeVisible();
  accounts.unmount();

  renderSetting('utilities');
  expect(screen.getByLabelText('Enable Calculator')).toBeChecked();
  expect(screen.getByText('Evaluate calculations locally.')).toBeVisible();
  // The count is of the tools listed: one without a switch isn't listed or counted.
  expect(screen.getByText(/1 of 1 on\./)).toBeVisible();
  expect(screen.queryByText('Timer')).not.toBeInTheDocument();
});

it('updates the Accounts header after Check without reloading the page (B263)', async () => {
  const google = (state: AccountAuthSnapshot['state']) =>
    ({
      ...snapshot.accounts,
      gmail: {
        ...snapshot.accounts.gmail,
        configured: true,
        authentication_state: state,
      },
      calendar: {
        ...snapshot.accounts.calendar,
        configured: true,
        authentication_state: state,
      },
    }) as SettingsSnapshot['accounts'];
  const saved = { ...snapshot, accounts: google('saved_unchecked') };
  const checked = {
    ...snapshot,
    revision: 'settings-checked',
    accounts: google('connected'),
  };
  const auth = (
    account: AccountAuthSnapshot['account'],
  ): AccountAuthSnapshot => ({
    schema_version: 1,
    account,
    revision: 'a'.repeat(64),
    configured: true,
    state: 'saved_unchecked',
    token_files: account === 'google' ? 2 : 1,
    sign_in: 'this_computer',
  });
  const state = { handshake: { authentication_kind: 'local_owner' } };
  const controller = {
    subscribe: () => () => undefined,
    getSnapshot: () => state,
    accountAuth: vi.fn(async (account: AccountAuthSnapshot['account']) =>
      auth(account),
    ),
    accountAuthCommand: vi.fn(
      async (
        _account: AccountAuthSnapshot['account'],
        command: AccountAuthCommand,
      ) => ({
        schema_version: 1,
        command_id: command.command_id,
        account: command.account,
        action: command.action,
        phase: 'completed',
        message: 'Account token is healthy.',
        snapshot: { ...auth(command.account), state: 'connected' },
      }),
    ),
    accountAuthReceipt: vi.fn(),
    cancelAccountAuth: vi.fn(),
    githubAccess: vi.fn(() => new Promise(() => {})),
  } as unknown as ClientController;
  const refreshSnapshot = vi.fn(async () => checked);
  function Page() {
    const [current, setCurrent] = useState<SettingsSnapshot>(saved);
    return (
      <AccountsSnapshotPanel
        snapshot={current.accounts}
        mutation={{
          ...mutation,
          page: 'accounts',
          refreshSnapshot,
          onSnapshot: setCurrent,
        }}
        showActions
      />
    );
  }
  render(
    <RuntimeContext.Provider
      value={{ controller, platform: {} as ClientPlatform }}
    >
      <MemoryRouter>
        <Page />
      </MemoryRouter>
    </RuntimeContext.Provider>,
  );
  expect(screen.getByText('0 connected')).toBeVisible();
  const googleRow = screen
    .getByText('Google', { selector: '.settings-row-label' })
    .closest('.settings-row') as HTMLElement;
  expect(within(googleRow).getByText('Not checked yet')).toBeVisible();
  fireEvent.click(
    await within(googleRow).findByRole('button', { name: 'Check Google' }),
  );
  // Gmail and Calendar are one Google account in the count.
  expect(await screen.findByText('1 connected')).toBeVisible();
  expect(within(googleRow).getByText('Connected')).toBeVisible();
  expect(refreshSnapshot).toHaveBeenCalledTimes(1);
});

it('says to download the search model rather than show documents as searchable', () => {
  mutation.page = 'documents';
  render(
    <DocumentEmbeddingSnapshot
      snapshot={{
        ...snapshot.documents,
        indexed_documents: 0,
        local_runtime: { state: 'missing', detail: 'Not downloaded.' },
      }}
      mutation={mutation}
    />,
  );
  expect(
    screen.getByText(
      'Download the search model to search documents (Advanced › Search model files)',
    ),
  ).toBeVisible();
  expect(screen.queryByText('0 searchable')).toBeNull();
});

it.each([
  ['missing', 'Not built yet'],
  ['not_checked', 'Not checked'],
  ['pending', 'Updating…'],
  ['ready', 'Ready'],
  ['current', 'Up to date'],
  ['stale', 'Needs a rebuild'],
  ['partial', 'Partly built'],
  ['failed', 'Needs a rebuild'],
  ['unavailable', "Couldn't be checked"],
  ['loading', 'Loading…'],
  ['inactive', 'Not in use'],
  ['idle', 'Not loaded'],
  ['cached', 'Downloaded'],
] as const)(
  'says a memory index that is %s in words, never as its id (B339)',
  (state, words) => {
    mutation.page = 'documents';
    render(
      <DocumentEmbeddingSnapshot
        snapshot={{
          ...snapshot.documents,
          document_vectors: { state: 'stale', detail: 'Rebuild it.' },
          local_runtime: { state: 'missing', detail: 'Not downloaded yet.' },
          memory_index: { state, detail: 'Its help.' },
        }}
        mutation={mutation}
      />,
    );
    fireEvent.click(screen.getByText('Advanced'));
    const memory = screen.getByText('Its help.').parentElement!;
    expect(within(memory).getByText(words)).toBeVisible();
    // The search model is downloaded, not built; the document index here needs a rebuild.
    expect(screen.getAllByText('Not downloaded')[0]).toBeVisible();
    expect(screen.getAllByText('Needs a rebuild')[0]).toBeVisible();
    expect(document.body).not.toHaveTextContent(
      /not_checked|Memory index: |Local model: |Document index: /,
    );
  },
);

it('Documents say where search runs: this computer, or the Row-Bot server (U7)', () => {
  mutation.page = 'documents';
  const show = () =>
    render(
      <DocumentEmbeddingSnapshot
        snapshot={snapshot.documents}
        mutation={mutation}
      />,
    );
  const desktop = show();
  expect(
    screen.getByText('On this computer, your documents never leave it.'),
  ).toBeVisible();
  desktop.unmount();
  setPlace({ kind: 'hosted', host_name: 'PikaPods' });
  const hosted = show();
  const runs = screen.getByRole('radiogroup', { name: 'Search runs' });
  expect(
    within(runs).getByRole('radio', { name: 'Your Row-Bot server' }),
  ).toHaveAttribute('aria-checked', 'true');
  expect(screen.getByText('search runs on your Row-Bot server')).toBeVisible();
  expect(
    screen.getByText(
      'On your Row-Bot server at PikaPods, your documents never leave it.',
    ),
  ).toBeVisible();
  hosted.unmount();
  setPlace({ kind: 'server' });
  show();
  expect(
    screen.getByText(
      'On the computer Row-Bot runs on, your documents never leave it.',
    ),
  ).toBeVisible();
  expect(document.body).not.toHaveTextContent(/this computer/);
});

it('renders editable document, tool, and preference owners', async () => {
  mutation.page = 'documents';
  const documents = render(
    <DocumentEmbeddingSnapshot
      snapshot={snapshot.documents}
      mutation={mutation}
    />,
  );
  const runs = screen.getByRole('radiogroup', { name: 'Search runs' });
  expect(
    within(runs).getByRole('radio', { name: 'This computer' }),
  ).toHaveAttribute('aria-checked', 'true');
  expect(screen.getByLabelText('Search model')).toHaveValue('qwen3-0.6b');
  expect(screen.getByLabelText('Free memory when idle')).toBeChecked();
  expect(screen.getByLabelText('Index size')).toHaveValue(null);
  expect(screen.getByText('12 searchable')).toBeVisible();
  expect(screen.getByText('search runs on this computer')).toBeVisible();
  expect(screen.getByText(/In use: Qwen3 0\.6B \(local\)/)).toBeVisible();
  // Index health and maintenance sit under Advanced.
  expect(screen.getByText('Advanced').closest('details')).not.toHaveAttribute(
    'open',
  );
  fireEvent.click(screen.getByText('Advanced'));
  expect(screen.getAllByText('Downloaded')[0]).toBeVisible();
  expect(screen.getByText('Updating…')).toBeVisible();
  expect(
    screen.getByRole('button', { name: 'Rebuild document index' }),
  ).toBeEnabled();
  expect(
    screen.getByRole('button', { name: 'repair local model' }),
  ).toBeEnabled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Rebuild document index' }),
  );
  await waitFor(() =>
    expect(mutation.review).toHaveBeenCalledWith(
      expect.objectContaining({
        page: 'documents',
        field: 'vectors.rebuild',
        value: true,
      }),
      expect.any(AbortSignal),
    ),
  );
  fireEvent.click(within(runs).getByRole('radio', { name: 'Cloud' }));
  expect(screen.getByLabelText('Search model')).toHaveValue(
    'openai:text-embedding-3-small',
  );
  // Data would leave this computer, so it asks first (decision 19).
  const ask = screen.getByRole('group', { name: 'Confirm Search runs' });
  expect(ask).toHaveTextContent(/sent to the cloud embedding provider/);
  expect(mutation.review).not.toHaveBeenCalledWith(
    expect.objectContaining({ field: 'embedding.provider' }),
    expect.anything(),
  );
  fireEvent.click(within(ask).getByRole('button', { name: 'Cancel' }));
  expect(screen.getByLabelText('Search model')).toHaveValue('qwen3-0.6b');
  documents.unmount();

  mutation.page = 'tools';
  const tools = render(
    <ToolConfigurationSnapshot snapshot={snapshot.tools} mutation={mutation} />,
  );
  expect(
    screen.getByRole('radio', {
      name: 'Only the ones a request needs (recommended)',
    }),
  ).toBeChecked();
  // Named for what it is, as Apps › Tavily names the same switch.
  expect(screen.getByLabelText('Enable Tavily web search')).toBeChecked();
  expect(
    screen.getByText(/Search the live web with your own Tavily key\./),
  ).toBeVisible();
  expect(screen.getByLabelText('Search research tools')).toBeVisible();
  expect(screen.queryByLabelText('Search API key')).not.toBeInTheDocument();
  fireEvent.click(screen.getByText('Credentials & setup'));
  fireEvent.click(
    screen.getByRole('button', { name: 'Replace or remove Search API key' }),
  );
  expect(screen.getByLabelText('Search API key')).toHaveAttribute(
    'type',
    'password',
  );
  tools.unmount();

  mutation.page = 'preferences';
  const preferences = render(
    <MemoryRouter>
      <PreferencesSnapshotPanel
        snapshot={snapshot.preferences}
        mutation={mutation}
      />
    </MemoryRouter>,
  );
  expect(screen.getByLabelText('Name')).toHaveValue('Row-Bot');
  expect(screen.getByRole('textbox', { name: 'Personality' })).toBeVisible();
  // The preview and the count are one muted line under the text box.
  expect(
    screen.getByText(/Row-Bot starts every chat as: “You are Row-Bot/),
  ).toBeVisible();
  expect(screen.getByText('0 / 200')).toBeVisible();
  // "Learn new skills" lives in Settings › Learning (D29).
  expect(screen.queryByLabelText('Learn new skills')).toBeNull();
  const openIn = screen.getByRole('radiogroup', { name: 'Open in' });
  // Two choices: the app window (the browser opens when it can't) or the browser.
  expect(
    within(openIn)
      .getAllByRole('radio')
      .map((radio) => radio.textContent?.trim()),
  ).toEqual(['App window', 'Browser']);
  expect(
    within(openIn).getByRole('radio', { name: 'App window' }),
  ).toHaveAttribute('aria-checked', 'true');
  // Dream Cycle's hours are one row of two times.
  expect(screen.getByLabelText('Start hour')).toHaveValue('1');
  expect(screen.getByLabelText('End hour')).toHaveValue('5');
  // Whole hours, in the reader's own clock.
  expect(
    within(screen.getByLabelText('Start hour')).getAllByRole('option'),
  ).toHaveLength(24);
  expect(screen.getByText('Dream Cycle off')).toBeVisible();
  // Updates and migration moved to their own System pages.
  expect(screen.queryByText(/No update check runs/)).toBeNull();
  preferences.unmount();
  render(
    <PreferencesSnapshotPanel
      snapshot={snapshot.preferences}
      mutation={mutation}
      part="updates"
    />,
  );
  expect(screen.getAllByText('Row-Bot 1.0.0')[0]).toBeVisible();
  expect(screen.getByText('Advanced').closest('details')).not.toHaveAttribute(
    'open',
  );
  expect(screen.getByText(/No update check runs/)).toBeVisible();
  expect(
    within(
      screen.getByRole('radiogroup', { name: 'Update channel' }),
    ).getByRole('radio', { name: 'Stable' }),
  ).toHaveAttribute('aria-checked', 'true');
});

it('a known host’s Updates page shows the version and who updates it, and nothing to set (H3, U6)', async () => {
  const hosted = {
    ...snapshot,
    system: {
      ...snapshot.system,
      hosted_by: {
        id: 'pikapods',
        name: 'PikaPods',
        home_url: 'https://www.pikapods.com/',
        support_url: null,
        status_url: null,
        manage_url: null,
        unit: 'pod',
        runs: 'the server, billing and backups',
        applies_updates: true,
        updates_help:
          'They test each release first, so new versions usually arrive within a week.',
      },
    },
  } satisfies SettingsSnapshot;
  const updates: UpdateSnapshot = {
    schema_version: 1,
    revision: 'a'.repeat(64),
    channel: 'stable',
    current_version: '1.0.0',
    last_check: null,
    last_success: null,
    skipped_versions: [],
    available: null,
    dev_install: false,
    mode: 'external',
    auto_check: true,
    auto_download: true,
    engine: {
      phase: 'idle',
      version: null,
      step: null,
      message: null,
      error: null,
      retryable: false,
      apply_when: null,
      full_installer_url: null,
      downloaded: 0,
      total: 0,
    },
    previous_version: null,
    legacy_install: false,
    host: {
      state: 'unknown',
      current_version: null,
      available_version: null,
      message: null,
      notes_url: 'https://github.com/example/row-bot/releases/tag/v1.0.0',
      updated_at: null,
      request: null,
    },
    active_work: [],
    held_back: null,
    feed_url: null,
  };
  const state = { conversations: [], selectedConversationId: null };
  const known: Record<string, unknown> = {
    getSnapshot: () => state,
    subscribe: () => () => {},
    settingsSnapshot: async () => hosted,
    updates: async () => updates,
  };
  const controller = new Proxy(known, {
    get: (target, key: string) =>
      key in target ? target[key] : () => new Promise(() => {}),
  }) as unknown as ClientController;
  render(
    <RuntimeContext.Provider
      value={{ controller, platform: {} as ClientPlatform }}
    >
      <OverlayProvider>
        <MemoryRouter initialEntries={['/settings/updates']}>
          <Routes>
            <Route path="settings/:setting/*" element={<SettingRoute />} />
          </Routes>
        </MemoryRouter>
      </OverlayProvider>
    </RuntimeContext.Provider>,
  );
  expect(await screen.findByText('Updated by PikaPods')).toBeVisible();
  expect(screen.getByText('Row-Bot 1.0.0')).toBeVisible();
  expect(
    screen.getByRole('link', { name: 'What’s new in Row-Bot 1.0.0' }),
  ).toBeVisible();
  // The host picks and installs releases: no channel, checks or details.
  expect(
    screen.queryByRole('radiogroup', { name: 'Update channel' }),
  ).toBeNull();
  expect(screen.queryByText(/checked/)).toBeNull();
  expect(screen.queryByText('Advanced')).toBeNull();
  expect(
    screen.queryByRole('button', { name: 'Check for updates' }),
  ).toBeNull();
});

it('System starts with the Hosted by card only when a known host runs Row-Bot (U5)', async () => {
  const state = { conversations: [], selectedConversationId: null };
  const show = (value: SettingsSnapshot) => {
    const known: Record<string, unknown> = {
      getSnapshot: () => state,
      subscribe: () => () => {},
      settingsSnapshot: async () => value,
    };
    const controller = new Proxy(known, {
      get: (target, key: string) =>
        key in target ? target[key] : () => new Promise(() => {}),
    }) as unknown as ClientController;
    return render(
      <RuntimeContext.Provider
        value={{ controller, platform: {} as ClientPlatform }}
      >
        <OverlayProvider>
          <MemoryRouter initialEntries={['/settings/system']}>
            <Routes>
              <Route path="settings/:setting/*" element={<SettingRoute />} />
            </Routes>
          </MemoryRouter>
        </OverlayProvider>
      </RuntimeContext.Provider>,
    );
  };
  const hosted = {
    ...snapshot,
    system: {
      ...snapshot.system,
      hosted_by: {
        id: 'pikapods',
        name: 'PikaPods',
        home_url: 'https://www.pikapods.com/',
        support_url: 'https://docs.pikapods.com/',
        status_url: null,
        manage_url: 'https://www.pikapods.com/pods',
        unit: 'pod',
        runs: 'the server, billing and backups',
        applies_updates: true,
        updates_help:
          'They test each release first, so new versions usually arrive within a week.',
      },
      memory: { limit_bytes: 2 * 2 ** 30, used_bytes: 2 ** 29 },
    },
  } satisfies SettingsSnapshot;
  show(hosted);
  const card = await screen.findByRole('region', {
    name: 'Hosted by PikaPods',
  });
  expect(
    within(card).getByText('Row-Bot 1.0.0 · updated by PikaPods'),
  ).toBeVisible();
  expect(within(card).getByText('Memory 0.5 GB of 2 GB')).toBeVisible();
  // At the top: before the page's own groups.
  const workspace = screen.getByRole('region', { name: 'Workspace' });
  expect(
    card.compareDocumentPosition(workspace) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  cleanup();

  // A server with no known host (or the desktop): today's System page.
  show({
    ...snapshot,
    system: {
      ...snapshot.system,
      memory: { limit_bytes: 2 * 2 ** 30, used_bytes: 2 ** 29 },
    },
  });
  expect(
    await screen.findByRole('region', { name: 'Workspace' }),
  ).toBeVisible();
  expect(screen.queryByText(/Hosted by/)).toBeNull();
  expect(screen.queryByText(/Memory .* of/)).toBeNull();
});

it('Devices on a hosted Row-Bot keeps the devices and drops how the server is reached', () => {
  setPlace({ kind: 'hosted', host_name: 'PikaPods' });
  renderSetting('access');
  expect(screen.getByText('0 devices')).toBeVisible();
  // Its host runs the network: no listen mode, Tailscale or public link here.
  expect(screen.queryByText('Your Row-Bot server only')).toBeNull();
  expect(
    screen.queryByText('Network, allowed addresses and the public link'),
  ).toBeNull();
  cleanup();

  // A self-hosted server keeps all of it.
  setPlace({ kind: 'server' });
  renderSetting('access');
  expect(screen.getByText('Your Row-Bot server only')).toBeVisible();
  expect(
    screen.getByText('Network, allowed addresses and the public link'),
  ).toBeVisible();
});

it('uses NiceGUI friendly research-tool labels and owner order', () => {
  mutation.page = 'tools';
  const configured = {
    ...snapshot.tools,
    items: [
      ...snapshot.tools.items.map((tool) => ({
        ...tool,
        label: 'web_search',
      })),
      {
        tool_id: 'youtube',
        label: 'youtube',
        available: true,
        enabled: true,
        configured_fields: [],
        credentials: [],
      },
      {
        tool_id: 'wolfram_alpha',
        label: 'wolfram_alpha',
        available: true,
        enabled: false,
        configured_fields: [],
        credentials: [],
      },
      {
        tool_id: 'arxiv',
        label: 'arxiv',
        available: true,
        enabled: true,
        configured_fields: [],
        credentials: [],
      },
      {
        tool_id: 'wikipedia',
        label: 'wikipedia',
        available: true,
        enabled: true,
        configured_fields: [],
        credentials: [],
      },
      {
        tool_id: 'duckduckgo',
        label: 'duckduckgo',
        available: true,
        enabled: true,
        configured_fields: [],
        credentials: [],
      },
    ],
  };
  const { container } = render(
    <ToolConfigurationSnapshot snapshot={configured} mutation={mutation} />,
  );
  expect(
    [
      ...container.querySelectorAll('.settings-toggle-list > li > div strong'),
    ].map((node) => node.textContent),
  ).toEqual([
    'arXiv',
    'DuckDuckGo',
    'Tavily web search',
    'Wikipedia',
    'Wolfram Alpha',
    'YouTube',
  ]);
  expect(screen.getByLabelText('Enable arXiv')).toBeChecked();
  expect(screen.queryByText('web_search')).not.toBeInTheDocument();
});

it('installs the chosen Whisper size from Voice settings (B140)', () => {
  mutation.page = 'voice';
  const { rerender } = render(
    <MemoryRouter>
      <VoiceSnapshotPanel
        snapshot={{
          ...snapshot.voice,
          local: { ...snapshot.voice.local, whisper_model: 'base' },
          whisper_options: [
            { value: 'base', label: 'Base (~74 MB, balanced)' },
          ],
        }}
        conversationId="conversation-a"
        mutation={mutation}
      />
    </MemoryRouter>,
  );
  expect(screen.getByText('Not installed')).toBeVisible();
  expect(
    screen.getByText(/Downloads once from Hugging Face \(Systran\)/),
  ).toBeVisible();
  // The size reads "Base · 74 MB"; Install sits beside it.
  expect(
    screen.getByRole('option', { name: 'Base · 74 MB' }),
  ).toBeInTheDocument();
  const install = screen.getByRole('button', {
    name: 'Install Whisper Base (~74 MB)',
  });
  expect(install).toHaveTextContent('Install');
  fireEvent.click(install);
  expect(mutation.review).toHaveBeenCalledWith(
    expect.objectContaining({
      page: 'voice',
      field: 'whisper.install',
      value: true,
    }),
    expect.any(AbortSignal),
  );
  rerender(
    <MemoryRouter>
      <VoiceSnapshotPanel
        snapshot={{
          ...snapshot.voice,
          local: { ...snapshot.voice.local, whisper_installed: true },
          whisper_options: [
            { value: 'base', label: 'Base (~74 MB, balanced)' },
          ],
        }}
        conversationId="conversation-a"
        mutation={mutation}
      />
    </MemoryRouter>,
  );
  expect(screen.getByText('Whisper Base (~74 MB) installed')).toBeVisible();
  expect(screen.getByText('Talk and dictation on this computer')).toBeVisible();
  expect(screen.queryByRole('button', { name: /Install Whisper/ })).toBeNull();
});

it('picks the model for documents next to the queue and saves at once (U45)', async () => {
  mutation.page = 'documents';
  // Nothing picked is the default: no "Reset … to default" until a model is.
  mutation.defaults = { processing_model: '' };
  render(
    <OverlayProvider>
      <DocumentModelSetting
        snapshot={snapshot.documents}
        mutation={mutation}
        models={[
          {
            model_ref: 'model:ollama:qwen3.8:27b',
            label: 'qwen3.8:27b',
            available: true,
          },
          {
            model_ref: 'model:openai:gpt-4o',
            label: 'GPT-4o',
            available: false,
          },
        ]}
      />
    </OverlayProvider>,
  );
  const picker = screen.getByLabelText('Read documents with', { exact: true });
  // Settings processes without a chat: nothing picked is the default model.
  expect(picker).toHaveDisplayValue('Default model');
  expect(
    screen.queryByRole('button', {
      name: 'Reset Read documents with to default',
    }),
  ).toBeNull();
  expect(
    screen.queryByRole('option', { name: 'GPT-4o (not available)' }),
  ).toBeNull();
  fireEvent.change(picker, { target: { value: 'model:ollama:qwen3.8:27b' } });
  await waitFor(() => expect(mutation.execute).toHaveBeenCalledTimes(1));
  expect(mutation.review).toHaveBeenCalledWith(
    expect.objectContaining({
      page: 'documents',
      field: 'processing_model',
      value: 'model:ollama:qwen3.8:27b',
    }),
    expect.any(AbortSignal),
  );
  expect(
    await notice('Documents are read with qwen3.8:27b'),
  ).toBeInTheDocument();
});

it('saves Open in and the Dream Cycle hours at once, confirmed by the floating notice (B258)', async () => {
  mutation.page = 'preferences';
  render(
    <MemoryRouter>
      <OverlayProvider>
        <PreferencesSnapshotPanel
          snapshot={snapshot.preferences}
          mutation={mutation}
        />
      </OverlayProvider>
    </MemoryRouter>,
  );
  const openIn = screen.getByRole('radiogroup', { name: 'Open in' });
  fireEvent.click(within(openIn).getByRole('radio', { name: 'Browser' }));
  await waitFor(() =>
    expect(mutation.review).toHaveBeenCalledWith(
      expect.objectContaining({
        page: 'preferences',
        field: 'window_mode',
        value: 'browser',
      }),
      expect.any(AbortSignal),
    ),
  );
  expect(
    await notice('Row-Bot opens there from the next launch'),
  ).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('End hour'), {
    target: { value: '6' },
  });
  await waitFor(() =>
    expect(mutation.review).toHaveBeenLastCalledWith(
      expect.objectContaining({ field: 'dream_cycle.window_end', value: 6 }),
      expect.any(AbortSignal),
    ),
  );
});

it('shows one account alone as its app’s own settings', () => {
  mutation.page = 'accounts';
  const { container } = render(
    <MemoryRouter>
      <OverlayProvider>
        <AccountsSnapshotPanel
          snapshot={snapshot.accounts}
          mutation={mutation}
          only="google"
        />
      </OverlayProvider>
    </MemoryRouter>,
  );
  expect(container.querySelectorAll('.settings-account')).toHaveLength(1);
  expect(screen.getByText('Google')).toBeVisible();
  expect(screen.queryByText('GitHub')).toBeNull();
});

/**
 * Settings search jumps to `data-setting-anchor` rows: every result for a
 * page must name a row that page really renders, or the jump lands nowhere.
 * (Pages whose rows need live owners check theirs in their own tests.)
 */
it.each<SettingsLeafId>([
  'preferences',
  'appearance',
  'voice',
  'tracker',
  'tools',
  'system',
  'access',
  'updates',
  'data',
])(
  'every search result for Settings › %s lands on a rendered row',
  async (leaf) => {
    const everyRow = {
      ...snapshot,
      // A cloud voice is offered, so Read aloud shows Speak with.
      voice: {
        ...snapshot.voice,
        speech_output_providers: [
          { value: 'local', label: 'Local' },
          { value: 'elevenlabs', label: 'ElevenLabs Flash' },
        ],
      },
      tools: {
        ...snapshot.tools,
        items: [
          ...snapshot.tools.items,
          {
            tool_id: 'wolfram_alpha',
            label: 'Wolfram Alpha',
            available: true,
            enabled: false,
            configured_fields: [],
            credentials: [
              {
                label: 'Wolfram Alpha App ID',
                name: 'app_id',
                configured: false,
                source: '',
                fingerprint: '',
              },
            ],
          },
        ],
      },
    } satisfies SettingsSnapshot;
    const state = { conversations: [], selectedConversationId: null };
    const known: Record<string, unknown> = {
      getSnapshot: () => state,
      subscribe: () => () => {},
      settingsSnapshot: async () => everyRow,
      // Data › Sandboxes reads its own list.
      rowBotSandboxes: async () => ({ items: [], packs: [], available: true }),
      // System › Privacy reads install stats (N6) on its own.
      installCount: async () => ({
        available: true,
        turned_off_here: false,
        enabled: true,
        service: 'row-bot-count.row-bot.workers.dev',
        version: '5.3.0',
        os: 'windows',
        last_sent_on: null,
        can_delete: false,
      }),
    };
    // Any other read stays pending: only the page's own rows matter here.
    const pending = (values: Record<string, unknown>) =>
      new Proxy(values, {
        get: (target, key: string) =>
          key in target ? target[key] : () => new Promise(() => {}),
      });
    const controller = pending(known) as unknown as ClientController;
    const platform = pending({}) as unknown as ClientPlatform;
    const { container } = render(
      <RuntimeContext.Provider value={{ controller, platform }}>
        <ThemeProvider>
          <OverlayProvider>
            <MemoryRouter initialEntries={[`/settings/${leaf}`]}>
              <Routes>
                <Route path="settings/:setting/*" element={<SettingRoute />} />
              </Routes>
            </MemoryRouter>
          </OverlayProvider>
        </ThemeProvider>
      </RuntimeContext.Provider>,
    );
    const wanted = settingsRows
      .filter((row) => row.leaf === leaf && !row.href)
      .map((row) => row.anchor);
    expect(wanted.length).toBeGreaterThan(0);
    await waitFor(() => {
      const rendered = new Set(
        [
          ...container.querySelectorAll<HTMLElement>('[data-setting-anchor]'),
        ].map((element) => element.dataset.settingAnchor),
      );
      expect(wanted.filter((anchor) => !rendered.has(anchor))).toEqual([]);
    });
  },
);

it('offers a speech model choice for Talk and Dictation only once there is one', () => {
  mutation.page = 'voice';
  const voice = (sensevoice: boolean) => (
    <MemoryRouter>
      <OverlayProvider>
        <VoiceSnapshotPanel
          snapshot={{
            ...snapshot.voice,
            runtime: {
              ...snapshot.voice.runtime,
              talk_model: 'local-whisper',
              dictation_model: 'local-whisper',
            },
            local: {
              ...snapshot.voice.local,
              sensevoice_path_configured: sensevoice,
            },
          }}
          conversationId="conversation-a"
          mutation={mutation}
        />
      </OverlayProvider>
    </MemoryRouter>
  );
  const view = render(voice(false));
  fireEvent.click(screen.getByText('Advanced'));
  // Saved ids such as "local-whisper" are never typed in by hand; only Live voice's OpenAI model is.
  expect(screen.queryAllByRole('textbox', { name: /model/i })).toHaveLength(1);
  expect(screen.getByLabelText('Realtime model')).toHaveValue(
    'gpt-realtime-2-mini',
  );
  expect(screen.queryByLabelText('Talk listens with')).toBeNull();
  expect(screen.queryByDisplayValue('local-whisper')).toBeNull();
  view.rerender(voice(true));
  const talk = screen.getByLabelText('Talk listens with');
  expect(
    within(talk)
      .getAllByRole('option')
      .map((option) => option.textContent),
  ).toEqual(['Whisper', 'SenseVoice']);
  expect(talk).toHaveValue('local-whisper');
  expect(screen.getByLabelText('Dictation listens with')).toBeVisible();
  // Read aloud always speaks with Kokoro: nothing to choose.
  expect(screen.queryByLabelText(/Read-aloud model/)).toBeNull();
});

function showLanguage(voice: Partial<SettingsSnapshot['voice']> = {}) {
  mutation.page = 'voice';
  return render(
    <MemoryRouter>
      <OverlayProvider>
        <VoiceSnapshotPanel
          snapshot={{ ...snapshot.voice, ...voice }}
          conversationId={null}
          mutation={mutation}
        />
      </OverlayProvider>
    </MemoryRouter>,
  );
}

it('Language detects what you speak by default, and a chosen one saves at once (V8)', async () => {
  showLanguage();
  const language = screen.getByRole('combobox', { name: 'Language' });
  expect(language).toHaveValue('auto');
  expect(
    within(language).getByRole('option', { name: 'Detect automatically' }),
  ).toBeVisible();
  // The voices for other languages wait behind one line.
  expect(screen.getByLabelText('Spanish voice')).not.toBeVisible();
  fireEvent.click(screen.getByText('Voices for other languages'));
  expect(
    screen.getByText(
      'Kokoro speaks English, Spanish, French, Hindi, Italian and Portuguese. Answers in other languages stay on screen.',
    ),
  ).toBeVisible();
  fireEvent.change(language, { target: { value: 'fr' } });
  await waitFor(() =>
    expect(mutation.review).toHaveBeenCalledWith(
      expect.objectContaining({
        field: 'runtime.speech_language',
        value: 'fr',
      }),
      expect.any(AbortSignal),
    ),
  );
});

it('an English-only Talk model says so for another language and switches to Whisper only when asked (V8)', async () => {
  showLanguage({
    runtime: {
      ...snapshot.voice.runtime,
      talk_model: 'local-moonshine-base-en',
      speech_language: 'fr',
    },
    language: {
      ...snapshot.voice.language,
      notices: [
        {
          use: 'talk',
          engine: 'Moonshine Base',
          understands: ['English'],
          language: 'fr',
          language_label: 'French',
          heard: false,
          alternatives: ['Whisper'],
          switch_field: 'runtime.talk_model',
          switch_value: 'local-whisper',
        },
      ],
    },
  });
  expect(
    screen.getByText(
      'Moonshine Base understands English only; for French, use Whisper.',
    ),
  ).toBeVisible();
  expect(mutation.review).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Use Whisper for Talk' }));
  await waitFor(() =>
    expect(mutation.review).toHaveBeenCalledWith(
      expect.objectContaining({
        field: 'runtime.talk_model',
        value: 'local-whisper',
      }),
      expect.any(AbortSignal),
    ),
  );
  expect(await notice('Talk now listens with Whisper')).toBeVisible();
});

it('each other language Kokoro speaks has its voice, and one it can’t speak says so (V8)', async () => {
  const view = showLanguage();
  fireEvent.click(screen.getByText('Voices for other languages'));
  const spanish = screen.getByLabelText('Spanish voice');
  expect(spanish).toHaveValue('ef_dora');
  // French has one voice: it is named, not offered as a choice.
  expect(screen.getByText('Siwis (female)')).toBeVisible();
  expect(screen.queryByLabelText('French voice')).toBeNull();
  fireEvent.change(spanish, { target: { value: 'em_alex' } });
  await waitFor(() =>
    expect(mutation.review).toHaveBeenCalledWith(
      expect.objectContaining({
        field: 'tts.language_voices.es',
        value: 'em_alex',
      }),
      expect.any(AbortSignal),
    ),
  );
  view.unmount();
  showLanguage({
    runtime: { ...snapshot.voice.runtime, speech_language: 'de' },
  });
  expect(
    screen.getByText(
      'Kokoro can’t speak German yet, so answers in German stay on screen.',
    ),
  ).toBeVisible();
  expect(screen.queryByLabelText('Spanish voice')).toBeNull();
});

const GB = 2 ** 30;

function showVoice(
  talkProvider: string,
  host: SettingsSnapshot['system']['hosted_by'] = null,
  cloud: Cloud[] = [deepgram, geminiListen],
) {
  mutation.page = 'voice';
  return render(
    <MemoryRouter>
      <OverlayProvider>
        <VoiceSnapshotPanel
          snapshot={{
            ...snapshot.voice,
            runtime: { ...snapshot.voice.runtime, talk_provider: talkProvider },
            cloud_speech: cloud,
            talk_providers: [
              { value: 'local', label: 'Local' },
              ...cloud.map((option) => ({
                value: option.id,
                label: option.label,
              })),
            ],
          }}
          conversationId={null}
          mutation={mutation}
          host={host}
        />
      </OverlayProvider>
    </MemoryRouter>,
  );
}

it('on a server under 3.5 GB local speech-to-text says it needs 4 GB and asks before it is chosen (U8)', async () => {
  setMemoryLimit(2 * GB);
  showVoice('gemini_transcribe');
  // Dictation listens with Whisper here: its quiet pill. Listen with is in the cloud: none.
  expect(screen.getAllByText('Needs a 4 GB server')).toHaveLength(1);

  const listen = screen.getByLabelText('Listen with');
  fireEvent.change(listen, { target: { value: 'local' } });
  const sheet = screen.getByRole('dialog', {
    name: 'Use local speech-to-text?',
  });
  expect(sheet).toHaveTextContent(
    'This server has 2 GB. Local speech-to-text may make Row-Bot restart.',
  );
  // The cloud way is the service already chosen, with its saved key.
  expect(sheet).toHaveTextContent(
    'Cloud speech-to-text listens with Gemini Transcribe Live, with your saved key.',
  );
  expect(listen).toHaveValue('gemini_transcribe');
  fireEvent.click(
    within(sheet).getByRole('button', { name: 'Use cloud speech-to-text' }),
  );
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(listen).toHaveValue('gemini_transcribe');
  expect(mutation.review).not.toHaveBeenCalled();

  fireEvent.change(listen, { target: { value: 'local' } });
  fireEvent.click(
    within(
      screen.getByRole('dialog', { name: 'Use local speech-to-text?' }),
    ).getByRole('button', { name: 'Use local anyway' }),
  );
  await waitFor(() =>
    expect(mutation.review).toHaveBeenCalledWith(
      expect.objectContaining({
        field: 'runtime.talk_provider',
        value: 'local',
      }),
      expect.anything(),
    ),
  );
  expect(listen).toHaveValue('local');
  expect(screen.getAllByText('Needs a 4 GB server')).toHaveLength(2);
});

it('with no cloud key saved, the small-server sheet picks a cloud service and asks for its key (U8)', async () => {
  setMemoryLimit(2 * GB);
  showVoice('local', null, [deepgram]);
  const listen = screen.getByLabelText('Listen with');
  fireEvent.change(listen, { target: { value: 'deepgram' } });
  await waitFor(() =>
    expect(mutation.review).toHaveBeenCalledWith(
      expect.objectContaining({
        field: 'runtime.talk_provider',
        value: 'deepgram',
      }),
      expect.anything(),
    ),
  );
  // Without its key Talk still listens here, so the pill stays on Listen with.
  expect(screen.getAllByText('Needs a 4 GB server')).toHaveLength(2);
  vi.mocked(mutation.review).mockClear();
  fireEvent.change(listen, { target: { value: 'local' } });
  const sheet = screen.getByRole('dialog', {
    name: 'Use local speech-to-text?',
  });
  expect(sheet).toHaveTextContent(
    'Cloud speech-to-text listens with Deepgram Nova-3 and needs your Deepgram key, which you add next.',
  );
  fireEvent.click(
    within(sheet).getByRole('button', { name: 'Use cloud speech-to-text' }),
  );
  expect(listen).toHaveValue('deepgram');
  expect(screen.getByText(/Until you add it, Talk listens/)).toBeVisible();
  expect(mutation.review).not.toHaveBeenCalledWith(
    expect.objectContaining({ value: 'openai_realtime' }),
    expect.anything(),
  );
});

it.each([
  ['with 4 GB', 4 * GB],
  ['on the desktop, with no limit', null],
])(
  'local speech-to-text needs no pill or sheet %s (U8)',
  async (_where, limit) => {
    setMemoryLimit(limit);
    showVoice('gemini_transcribe');
    expect(screen.queryByText('Needs a 4 GB server')).toBeNull();
    fireEvent.change(screen.getByLabelText('Listen with'), {
      target: { value: 'local' },
    });
    expect(screen.queryByRole('dialog')).toBeNull();
    await waitFor(() =>
      expect(mutation.review).toHaveBeenCalledWith(
        expect.objectContaining({
          field: 'runtime.talk_provider',
          value: 'local',
        }),
        expect.anything(),
      ),
    );
  },
);

it('suggests Free memory when idle where the search model is chosen on a small server (U8)', () => {
  mutation.page = 'documents';
  const show = (autoUnload: boolean, provider: 'local' | 'cloud' = 'local') =>
    render(
      <DocumentEmbeddingSnapshot
        snapshot={{
          ...snapshot.documents,
          embedding: {
            ...snapshot.documents.embedding,
            provider,
            auto_unload: autoUnload,
          },
        }}
        mutation={mutation}
      />,
    );
  setMemoryLimit(2 * GB);
  const off = show(false);
  const suggestion = screen.getByText('Suggested on a 2 GB server');
  expect(suggestion.closest('[data-setting-anchor]')).toHaveAttribute(
    'data-setting-anchor',
    'embedding.auto_unload',
  );
  off.unmount();
  // Already on, or search in the cloud: nothing to suggest.
  show(true).unmount();
  show(false, 'cloud').unmount();
  expect(screen.queryByText(/Suggested on a/)).toBeNull();
  setMemoryLimit(4 * GB);
  show(false);
  expect(screen.queryByText(/Suggested on a/)).toBeNull();
});

const railwayHost = {
  id: 'railway',
  name: 'Railway',
  home_url: 'https://railway.com/',
  support_url: 'https://station.railway.com/',
  status_url: 'https://status.railway.com/',
  manage_url:
    'https://railway.com/project/0f6e3c1a-5b2d-4c8e-9a71-2d4b6f8e1c30/service/7a1c9e42-3f5b-4d6a-8e20-b1c3d5e7f901?environmentId=c2e4a6b8-1d3f-4a5c-9e7b-0f2d4c6e8a10',
  unit: 'service',
  runs: 'the server and billing',
  applies_updates: false,
  updates_help: 'Turn on auto updates in Railway.',
  settings_url:
    'https://railway.com/project/0f6e3c1a-5b2d-4c8e-9a71-2d4b6f8e1c30/service/7a1c9e42-3f5b-4d6a-8e20-b1c3d5e7f901/settings?environmentId=c2e4a6b8-1d3f-4a5c-9e7b-0f2d4c6e8a10',
  usage_url: 'https://railway.com/workspace/usage',
  trial: null,
};

it.each([
  [
    'its service’s settings',
    railwayHost,
    'Raise the memory limit',
    'https://railway.com/project/0f6e3c1a-5b2d-4c8e-9a71-2d4b6f8e1c30/service/7a1c9e42-3f5b-4d6a-8e20-b1c3d5e7f901/settings?environmentId=c2e4a6b8-1d3f-4a5c-9e7b-0f2d4c6e8a10',
  ],
  [
    'its plans, on the trial',
    {
      ...railwayHost,
      trial: {
        note: 'Railway’s trial gives Row-Bot 1 GB.',
        upgrade_url: 'https://railway.com/workspace/plans',
      },
    },
    'Upgrade in Railway',
    'https://railway.com/workspace/plans',
  ],
])(
  'on Railway the local speech-to-text sheet links %s (A2, A4)',
  (_what, host, label, url) => {
    setMemoryLimit(2 * GB);
    showVoice('gemini_transcribe', host);
    fireEvent.change(screen.getByLabelText('Listen with'), {
      target: { value: 'local' },
    });
    const sheet = screen.getByRole('dialog', {
      name: 'Use local speech-to-text?',
    });
    const link = within(sheet).getByRole('link', { name: label });
    expect(link).toHaveAttribute('href', url);
    expect(link).toHaveAttribute('target', '_blank');
  },
);

it('without the host’s page, the sheet links nowhere (A2)', () => {
  setMemoryLimit(2 * GB);
  showVoice('gemini_transcribe', { ...railwayHost, settings_url: null });
  fireEvent.change(screen.getByLabelText('Listen with'), {
    target: { value: 'local' },
  });
  const sheet = screen.getByRole('dialog', {
    name: 'Use local speech-to-text?',
  });
  expect(within(sheet).queryByRole('link')).toBeNull();
});

it('shows the tool switches as saved when they change elsewhere, as after Start from defaults', () => {
  mutation.page = 'system';
  // While tool settings can't be read every tool is off.
  const off: SettingsSnapshot = structuredClone(snapshot);
  off.revision = 'unreadable';
  off.system.shell.enabled = false;
  off.system.browser.enabled = false;
  const panel = (current: SettingsSnapshot) => (
    <OverlayProvider>
      <SystemSnapshotPanel
        snapshot={current.system}
        mutation={{ ...mutation, revision: current.revision }}
      />
    </OverlayProvider>
  );
  const view = render(panel(off));
  // The open setup dialog hides the page behind it, so Run commands is found with hidden.
  const setup = openSetup();
  expect(
    screen.getByRole('switch', { name: 'Run commands', hidden: true }),
  ).not.toBeChecked();
  expect(
    within(setup).getByRole('switch', { name: 'Enable Browser tool' }),
  ).not.toBeChecked();

  // The reset's snapshot: the tools are back at their defaults, shell and browser on.
  view.rerender(panel({ ...snapshot, revision: 'after-reset' }));

  expect(
    screen.getByRole('switch', { name: 'Run commands', hidden: true }),
  ).toBeChecked();
  expect(
    within(setup).getByRole('switch', { name: 'Enable Browser tool' }),
  ).toBeChecked();
});

it('Devices on a phone puts Connect a phone or computer first under Advanced, hosted or not (C3, G20)', () => {
  const connect = <section aria-label="Connect a phone or computer" />;
  mutation.page = 'system';
  const show = () =>
    render(
      <MemoryRouter>
        <OverlayProvider>
          <Phase4RetainedSettings
            setting="access"
            snapshot={snapshot}
            mutation={mutation}
            selectedConversationId="conversation-a"
            accessConnect={connect}
          />
        </OverlayProvider>
      </MemoryRouter>,
    );
  setPlace({ kind: 'server' });
  show();
  const advanced = screen.getByText('Advanced').closest('details')!;
  expect(advanced).not.toHaveAttribute('open');
  const inside = within(advanced).getByRole('region', {
    name: 'Connect a phone or computer',
  });
  // First in Advanced, before Network.
  expect(
    inside.compareDocumentPosition(within(advanced).getByText('Network')) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(
    screen.getByText('Connect a device, network and the public link'),
  ).toBeInTheDocument();
  cleanup();

  // A hosted Row-Bot has no network to set: Advanced holds Connect alone.
  setPlace({ kind: 'hosted', host_name: 'PikaPods' });
  show();
  const hosted = screen.getByText('Advanced').closest('details')!;
  expect(
    within(hosted).getByRole('region', { name: 'Connect a phone or computer' }),
  ).toBeInTheDocument();
  expect(within(hosted).queryByText('Network')).toBeNull();
});

type Cloud = NonNullable<SettingsSnapshot['voice']['cloud_speech']>[number];
const deepgram: Cloud = {
  id: 'deepgram',
  part: 'listen',
  label: 'Deepgram Nova-3',
  company: 'Deepgram',
  price: 'about $0.005 a minute',
  price_checked: '23 Nov 2026',
  key_saved: false,
  key_field: 'deepgram.credential',
  key: { configured: false, source: 'none', fingerprint: '' },
  describe: '',
};
const geminiListen: Cloud = {
  ...deepgram,
  id: 'gemini_transcribe',
  label: 'Gemini Transcribe Live',
  company: 'Google',
  price: 'about $0.009 a minute',
  key_saved: true,
  key_field: null,
  key: null,
};
const elevenlabs: Cloud = {
  id: 'elevenlabs',
  part: 'speak',
  label: 'ElevenLabs Flash',
  company: 'ElevenLabs',
  price: 'about $0.04 a minute of speech',
  price_checked: '23 Nov 2026',
  key_saved: true,
  key_field: 'elevenlabs.credential',
  key: { configured: true, source: 'keyring', fingerprint: '****9f2a' },
  voices: [
    { value: 'EXAVITQu4vr4xnSDxMaL', label: 'Sarah' },
    { value: 'JBFqnCBsd6RMkjVDRZzb', label: 'George' },
  ],
  voice: 'EXAVITQu4vr4xnSDxMaL',
  describe: 'design',
  description: 'A calm, warm voice with a slight smile.',
  designed: false,
};
const geminiSpeak: Cloud = {
  ...elevenlabs,
  id: 'gemini_tts',
  label: 'Gemini Flash TTS',
  company: 'Google',
  key_saved: false,
  key_field: null,
  key: null,
  voices: [{ value: 'Kore', label: 'Kore (firm)' }],
  voice: 'Kore',
  describe: 'style',
  description: '',
};

function showCloudVoice(talk: string, speak: string) {
  mutation.page = 'voice';
  render(
    <MemoryRouter>
      <OverlayProvider>
        <VoiceSnapshotPanel
          snapshot={{
            ...snapshot.voice,
            runtime: {
              ...snapshot.voice.runtime,
              talk_provider: talk,
              speech_output_provider: speak,
            },
            local: { ...snapshot.voice.local, whisper_installed: true },
            cloud_speech: [deepgram, geminiListen, elevenlabs, geminiSpeak],
            talk_providers: [
              { value: 'local', label: 'Local' },
              { value: 'deepgram', label: 'Deepgram Nova-3' },
              { value: 'gemini_transcribe', label: 'Gemini Transcribe Live' },
            ],
            speech_output_providers: [
              { value: 'local', label: 'Local' },
              { value: 'elevenlabs', label: 'ElevenLabs Flash' },
              { value: 'gemini_tts', label: 'Gemini Flash TTS' },
            ],
          }}
          conversationId="conversation-a"
          mutation={mutation}
        />
      </OverlayProvider>
    </MemoryRouter>,
  );
}

it('lists local listening and speaking first, then cloud ones with their price and key (V5)', () => {
  showCloudVoice('local', 'local');
  const listen = screen.getByLabelText('Listen with');
  expect(
    within(listen)
      .getAllByRole('option')
      .map((option) => option.textContent),
  ).toEqual([
    'Whisper · on this computer',
    'Deepgram Nova-3 · cloud · needs key',
    'Gemini Transcribe Live · cloud',
  ]);
  const speak = screen.getByLabelText('Speak with');
  expect(speak).toHaveValue('local');
  expect(within(speak).getAllByRole('option')[0]).toHaveTextContent(
    'Kokoro · on this computer',
  );
  // Locally, nothing leaves: no company is named, and Kokoro's voices are shown.
  expect(screen.queryByText(/goes to/)).toBeNull();
  expect(screen.getByLabelText('Voice')).toHaveValue('af_heart');
});

it('says whose servers hear the voice, what it costs and that it needs a key until one is added', () => {
  showCloudVoice('deepgram', 'local');
  expect(
    screen.getByText(
      'Your voice goes to Deepgram while you talk. About $0.005 a minute (Deepgram’s prices, 23 Nov 2026).',
    ),
  ).toBeVisible();
  expect(screen.getByText('Needs your Deepgram key')).toBeVisible();
  expect(
    screen.getByText('Until you add it, Talk listens on this computer.'),
  ).toBeVisible();
  expect(
    screen.getByRole('button', { name: 'Add Deepgram API key' }),
  ).toBeVisible();
});

it('a Google key lives in Providers, and Speak with names who reads the text, voice notes included', () => {
  showCloudVoice('gemini_transcribe', 'gemini_tts');
  expect(screen.getByText('Talk listens with Google')).toBeVisible();
  expect(
    screen.getByText(
      'Text goes to Google to be spoken. About $0.04 a minute of speech (Google’s prices, 23 Nov 2026).',
    ),
  ).toBeVisible();
  // No key yet: Kokoro speaks, and the note says so; the key is added in Providers.
  expect(
    screen.getByText(/voice-note replies speak with Kokoro/),
  ).toBeVisible();
  expect(
    screen.getByRole('link', { name: 'Add it in Providers' }),
  ).toHaveAttribute('href', '/settings/models#providers-api');
  expect(screen.getByLabelText('Voice')).toHaveValue('Kore');
  expect(screen.getByLabelText('Describe the voice')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Make this voice' })).toBeNull();
});

it('picks a cloud voice and makes one from a description only, after a review (VD11)', async () => {
  showCloudVoice('local', 'elevenlabs');
  expect(
    screen.getByText(
      /voice-note replies in chat apps are spoken by ElevenLabs: their text goes to ElevenLabs/,
    ),
  ).toBeVisible();
  const voice = screen.getByLabelText('Voice');
  expect(
    within(voice)
      .getAllByRole('option')
      .map((option) => option.textContent),
  ).toEqual(['Sarah', 'George']);
  expect(
    screen.getByText(/no recordings, no copies of real people/),
  ).toBeVisible();
  // Nothing here takes a recording of a voice.
  expect(document.querySelector('input[type="file"]')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Make this voice' }));
  await waitFor(() =>
    expect(mutation.review).toHaveBeenCalledWith(
      expect.objectContaining({
        page: 'voice',
        field: 'elevenlabs.design_voice',
        value: true,
      }),
      expect.any(AbortSignal),
    ),
  );
});

it('a cloud voice says which chosen language it can’t speak, not Kokoro (V5, V8)', () => {
  showLanguage({
    runtime: {
      ...snapshot.voice.runtime,
      speech_language: 'ar',
      speech_output_provider: 'elevenlabs',
    },
    cloud_speech: [
      {
        id: 'elevenlabs',
        part: 'speak',
        label: 'ElevenLabs Flash',
        company: 'ElevenLabs',
        price: 'about $0.04 a minute of speech',
        price_checked: '23 Nov 2026',
        key_saved: true,
      },
    ],
    language: { ...snapshot.voice.language, spoken: ['de', 'en', 'es'] },
  });
  expect(
    screen.getByText(
      'ElevenLabs Flash can’t speak Arabic yet, so answers in Arabic stay on screen.',
    ),
  ).toBeVisible();
  expect(screen.queryByText(/Kokoro can’t speak/)).toBeNull();
});

function showVoiceOn(wakeReason: string) {
  mutation.page = 'voice';
  const controller = {
    dictationCapability: vi.fn(async () => ({ wake_reason: wakeReason })),
  } as unknown as ClientController;
  return render(
    <RuntimeContext.Provider
      value={{ controller, platform: {} as ClientPlatform }}
    >
      <MemoryRouter>
        <OverlayProvider>
          <VoiceSnapshotPanel
            snapshot={snapshot.voice}
            conversationId={null}
            mutation={mutation}
          />
        </OverlayProvider>
      </MemoryRouter>
    </RuntimeContext.Provider>,
  );
}

function phoneWidth(phone: boolean) {
  vi.mocked(window.matchMedia).mockImplementation(
    (query: string) =>
      ({
        matches: phone && query === '(max-width: 767px)',
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }) as unknown as MediaQueryList,
  );
}

it('the wake word shows in the app on this computer, off until turned on, with its privacy note', async () => {
  showVoiceOn('available');
  const toggle = await screen.findByLabelText('Hey Row-Bot');
  expect(toggle).not.toBeChecked();
  expect(
    screen.getByText(
      'Listens on this computer for “Hey Row-Bot” while a Row-Bot window is open; nothing is recorded or sent anywhere.',
    ),
  ).toBeVisible();
  fireEvent.click(toggle);
  await waitFor(() =>
    expect(mutation.review).toHaveBeenCalledWith(
      expect.objectContaining({
        page: 'voice',
        field: 'runtime.wake_word',
        value: true,
      }),
      expect.any(AbortSignal),
    ),
  );
});

it('the wake word is absent where it cannot listen: another device, a hosted server, or a phone', async () => {
  const other = showVoiceOn('not_this_computer');
  await waitFor(() =>
    expect(screen.getByLabelText('Live captions')).toBeVisible(),
  );
  await Promise.resolve();
  expect(screen.queryByLabelText('Hey Row-Bot')).toBeNull();
  other.unmount();

  for (const place of [
    { kind: 'hosted', host_name: 'PikaPods' },
    { kind: 'server' },
  ] as const) {
    setPlace(place);
    const away = showVoiceOn('available');
    await waitFor(() =>
      expect(screen.getByLabelText('Live captions')).toBeVisible(),
    );
    await Promise.resolve();
    expect(screen.queryByLabelText('Hey Row-Bot')).toBeNull();
    expect(screen.queryByText(/this computer/)).toBeNull();
    away.unmount();
  }
  setPlace(null);

  phoneWidth(true);
  try {
    showVoiceOn('available');
    await waitFor(() =>
      expect(screen.getByLabelText('Live captions')).toBeVisible(),
    );
    await Promise.resolve();
    expect(screen.queryByLabelText('Hey Row-Bot')).toBeNull();
  } finally {
    phoneWidth(false);
  }
});
