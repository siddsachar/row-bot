import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { useState } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type {
  AccountAuthCommand,
  AccountAuthSnapshot,
  SettingsMutationReceipt,
  SettingsMutationRequest,
  SettingsMutationReview,
  SettingsSnapshot,
} from '../../api/types';
import type { ClientPlatform } from '../../platform';
import { RuntimeContext } from '../../runtime';
import { OverlayProvider } from '../../ui/overlays';
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
      local_owner_control_available: true,
      platform: 'windows' as const,
      disclosure_acknowledged: true,
      system_binary_configured: false,
      status_message: 'Computer Use is off.',
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
      local_owner_control_available: true,
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
      runtime_state: 'cached',
    },
    migration: { available: false, sources: [] },
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

it('renders real voice controls without probing a device or provider', async () => {
  renderSetting('voice');
  expect(screen.getByLabelText('Listen with')).toHaveValue('local');
  // The provider select says where it runs (B258).
  expect(
    screen.getAllByRole('option', { name: 'Whisper · on this computer' }),
  ).toHaveLength(2);
  expect(screen.getByText('Talk and dictation on this computer')).toBeVisible();
  expect(screen.getByLabelText('Speech model')).toHaveValue('base');
  expect(screen.queryByLabelText('Realtime voice')).toBeNull();
  expect(screen.queryByLabelText('Fall back to this computer')).toBeNull();
  expect(screen.queryByLabelText('Start automatically')).toBeNull();
  expect(screen.queryByLabelText('Provider voice')).toBeNull();
  expect(screen.getByLabelText('Read replies aloud')).toBeChecked();
  fireEvent.click(screen.getByText('Advanced'));
  expect(screen.getByText('Whisper base')).toBeVisible();
  expect(screen.getByText('SenseVoice')).toBeVisible();
  expect(screen.getByText('Kokoro')).toBeVisible();
  expect(screen.getByText('Credential saved · not checked')).toBeVisible();
  expect(screen.getAllByText(/Not checked/).length).toBeGreaterThan(0);
  expect(
    screen.queryByRole('button', {
      name: /OpenAI Realtime API key/,
    }),
  ).toBeNull();
  fireEvent.change(screen.getByLabelText('Listen with'), {
    target: { value: 'openai_realtime' },
  });
  expect(screen.getByLabelText('Realtime voice')).toBeVisible();
  expect(screen.getByLabelText('Fall back to this computer')).toBeVisible();
  // A choice saves at once (decision 19); nothing probes a device.
  await waitFor(() =>
    expect(mutation.review).toHaveBeenCalledWith(
      expect.objectContaining({
        field: 'runtime.talk_provider',
        value: 'openai_realtime',
      }),
      expect.any(AbortSignal),
    ),
  );
  expect(
    screen.getByRole('link', { name: 'Open conversation voice' }),
  ).toHaveAttribute('href', '/conversations/conversation-a');
  expect(document.body).not.toHaveTextContent('must-not-render');
});

it('shows Realtime-only voice controls only for Realtime and hides uninstalled local TTS controls', () => {
  mutation.page = 'voice';
  render(
    <MemoryRouter>
      <VoiceSnapshotPanel
        snapshot={{
          ...snapshot.voice,
          runtime: {
            ...snapshot.voice.runtime,
            talk_provider: 'openai_realtime',
          },
          tts: { ...snapshot.voice.tts, installed: false },
        }}
        conversationId="conversation-a"
        mutation={mutation}
      />
    </MemoryRouter>,
  );
  expect(screen.getByLabelText('Live captions')).toBeChecked();
  expect(screen.getByLabelText('Realtime voice')).toHaveValue('alloy');
  expect(screen.getByLabelText('Fall back to this computer')).toBeChecked();
  // Kokoro's install is a text action in the Read aloud row's status line.
  expect(screen.getByText('Kokoro isn’t installed yet')).toBeVisible();
  expect(screen.queryByLabelText('Read replies aloud')).toBeNull();
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
  const controller = { speechTest } as unknown as ClientController;
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
  expect(screen.getByText('Computer Use unavailable')).toBeVisible();
  expect(screen.getByText('Filesystem tool unavailable')).toBeVisible();
  expect(screen.queryByLabelText(/Additional blocked patterns/)).toBeNull();
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
    within(setup).getByText('Computer Use setup details').closest('details'),
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
  expect(within(setup).queryByLabelText('Computer Use (Beta)')).toBeNull();
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
  expect(screen.getByText('Computer Use is off.')).toBeVisible();
  expect(mutation.review).not.toHaveBeenCalled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Check Computer Use setup' }),
  );
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

it('shows Computer Use status without host actions in a remote session', () => {
  mutation.page = 'system';
  render(
    <SystemSnapshotPanel
      snapshot={{
        ...snapshot.system,
        computer_use: {
          ...snapshot.system.computer_use,
          local_owner_control_available: false,
        },
      }}
      mutation={mutation}
    />,
  );
  openSetup();
  expect(screen.getByText('Host setup is local only')).toBeVisible();
  expect(
    screen.queryByRole('button', { name: 'Check Computer Use setup' }),
  ).toBeNull();
  expect(
    screen.queryByRole('button', { name: 'Install Computer Use runtime' }),
  ).toBeNull();
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
  fireEvent.click(screen.getByText('Advanced system Cua executable'));
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
    screen.getByRole('button', { name: 'Use managed Cua runtime' }),
  ).toBeVisible();
});

it('requires confirmation only for removing the managed Computer Use runtime', async () => {
  mutation.page = 'system';
  render(
    <SystemSnapshotPanel snapshot={snapshot.system} mutation={mutation} />,
  );
  fireEvent.click(screen.getByText('Danger zone'));
  fireEvent.click(
    screen.getByRole('button', { name: 'Remove managed Cua runtime' }),
  );
  expect(mutation.review).not.toHaveBeenCalled();
  expect(
    screen.getByText(/This deletes the managed runtime files/),
  ).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(mutation.review).not.toHaveBeenCalled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Remove managed Cua runtime' }),
  );
  fireEvent.click(
    screen
      .getAllByRole('button', { name: 'Remove managed Cua runtime' })
      .at(-1)!,
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

it('shows the public link to other devices without its controls', () => {
  mutation.page = 'system';
  render(
    <SystemSnapshotPanel
      snapshot={{
        ...snapshot.system,
        tunnel: {
          ...snapshot.system.tunnel,
          main_app_enabled: true,
          main_app_url: null,
          local_owner_control_available: false,
        },
      }}
      mutation={mutation}
      part="access"
    />,
  );
  expect(
    screen.getByText(
      'Only Row-Bot’s owner on the computer running it can start or stop the public link.',
    ),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Start public link' }),
  ).toBeNull();
  expect(screen.queryByText(/api\/webhook/)).toBeNull();
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
  renderSetting('voice');
  fireEvent.click(screen.getByText('Advanced'));
  const model = screen.getByLabelText('Talk model');
  fireEvent.change(model, {
    target: { value: 'medium' },
  });
  expect(mutation.review).not.toHaveBeenCalled();
  expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
  expect(screen.queryByRole('button', { name: /^Revert/ })).toBeNull();
  fireEvent.blur(model);
  await waitFor(() => expect(mutation.execute).toHaveBeenCalledTimes(1));
  const saved = await notice('Talk model saved');
  // Never a line in the page (B258).
  expect(document.querySelector('.settings-saved-note')).toBeNull();
  fireEvent.click(
    within(saved.closest('li')!).getByRole('button', { name: 'Undo' }),
  );
  await waitFor(() => expect(mutation.execute).toHaveBeenCalledTimes(2));
  expect(mutation.review).toHaveBeenLastCalledWith(
    expect.objectContaining({ field: 'runtime.talk_model', value: 'small' }),
    expect.any(AbortSignal),
  );
  expect(await notice('Talk model changed back')).toBeInTheDocument();
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

it('marks a field that differs from its default and resets it in one step', async () => {
  mutation.defaults = { 'runtime.talk_model': 'local-whisper' };
  const { container } = renderSetting('voice');
  fireEvent.click(screen.getByText('Advanced'));
  const row = screen
    .getByLabelText('Talk model')
    .closest('.settings-saved-control')!;
  expect(row).toHaveAttribute('data-modified', 'true');
  expect(row.querySelector('.settings-modified-dot')).not.toBeNull();
  // The mark never becomes part of the control's name.
  expect(screen.getByRole('textbox', { name: 'Talk model' })).toBeVisible();
  // A field already at its default offers no reset.
  expect(
    screen.queryByRole('button', { name: 'Reset Listen with to default' }),
  ).toBeNull();
  fireEvent.click(
    screen.getByRole('button', { name: 'Reset Talk model to default' }),
  );
  await waitFor(() => expect(mutation.execute).toHaveBeenCalledTimes(1));
  expect(mutation.review).toHaveBeenCalledWith(
    expect.objectContaining({
      page: 'voice',
      field: 'runtime.talk_model',
      value: 'local-whisper',
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
  renderSetting('voice');
  fireEvent.change(screen.getByLabelText('Talk model'), {
    target: { value: 'medium' },
  });
  fireEvent.keyDown(screen.getByLabelText('Talk model'), { key: 'Enter' });
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

  fireEvent.click(
    screen.getByRole('button', { name: 'Delete All Tracker Data' }),
  );
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
    screen.getByRole('button', { name: 'Delete All Tracker Data' }),
  ).toBeEnabled();
});

it('retires a tracker deletion review when the Settings revision changes', async () => {
  mutation.page = 'tracker';
  const view = render(
    <TrackerSnapshotPanel snapshot={snapshot.tracker} mutation={mutation} />,
  );
  fireEvent.click(screen.getByText('Danger zone'));
  fireEvent.click(
    screen.getByRole('button', { name: 'Delete All Tracker Data' }),
  );
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
      name: 'Confirm Delete All Tracker Data',
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
  fireEvent.click(
    screen.getByRole('button', { name: 'Delete All Tracker Data' }),
  );
  await screen.findByText('saved locally');
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm Delete All Tracker Data' }),
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
  const first = renderSetting('voice');
  fireEvent.change(screen.getByLabelText('Talk model'), {
    target: { value: 'medium' },
  });
  first.unmount();

  expect(mutation.review).not.toHaveBeenCalled();
  expect(mutation.execute).not.toHaveBeenCalled();
  renderSetting('voice');
  expect(screen.getByLabelText('Talk model')).toHaveValue('medium');
  fireEvent.keyDown(screen.getByLabelText('Talk model'), { key: 'Escape' });
  expect(screen.getByLabelText('Talk model')).toHaveValue('small');
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
  expect(screen.getByLabelText('Enable Habit Tracker')).toBeChecked();
  expect(screen.getByText(/Water/)).toBeVisible();
  // Last entries read as relative time with the full date on hover.
  expect(
    tracker.container.querySelector('time[datetime^="2026-09-14"]'),
  ).not.toBeNull();
  tracker.unmount();

  const accounts = renderSetting('accounts');
  // One row per account with one status (B263); each setup guide folds.
  expect(accounts.container.querySelectorAll('.settings-account')).toHaveLength(
    3,
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
  expect(screen.getByText(/of 2 on\./)).toBeVisible();
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
  const auth = (account: 'google' | 'x'): AccountAuthSnapshot => ({
    schema_version: 1,
    account,
    revision: 'a'.repeat(64),
    configured: true,
    state: 'saved_unchecked',
    token_files: account === 'google' ? 2 : 1,
  });
  const controller = {
    accountAuth: vi.fn(async (account: 'google' | 'x') => auth(account)),
    accountAuthCommand: vi.fn(
      async (_account: 'google' | 'x', command: AccountAuthCommand) => ({
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
  expect(screen.getByLabelText('Dimension override')).toHaveValue(null);
  expect(screen.getByText('12 searchable')).toBeVisible();
  expect(screen.getByText('search runs on this computer')).toBeVisible();
  expect(screen.getByText(/In use: Qwen3 0\.6B \(local\)/)).toBeVisible();
  // Index health and maintenance sit under Advanced.
  expect(screen.getByText('Advanced').closest('details')).not.toHaveAttribute(
    'open',
  );
  fireEvent.click(screen.getByText('Advanced'));
  expect(screen.getByText(/Local model: cached/)).toBeVisible();
  expect(screen.getByText(/Memory index: pending/)).toBeVisible();
  expect(
    screen.getByRole('button', { name: 'rebuild document vectors' }),
  ).toBeEnabled();
  expect(
    screen.getByRole('button', { name: 'repair local model' }),
  ).toBeEnabled();
  fireEvent.click(
    screen.getByRole('button', { name: 'rebuild document vectors' }),
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
      name: 'Auto-select external tools (recommended)',
    }),
  ).toBeChecked();
  expect(screen.getByLabelText('Enable Web Search')).toBeChecked();
  expect(screen.getByText(/Search the live web with Tavily\./)).toBeVisible();
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
    <PreferencesSnapshotPanel
      snapshot={snapshot.preferences}
      mutation={mutation}
    />,
  );
  expect(screen.getByLabelText('Name')).toHaveValue('Row-Bot');
  expect(screen.getByRole('textbox', { name: 'Personality' })).toBeVisible();
  // The preview and the count are one muted line under the text box.
  expect(
    screen.getByText(/Row-Bot starts every chat as: “You are Row-Bot/),
  ).toBeVisible();
  expect(screen.getByText('0 / 200')).toBeVisible();
  expect(screen.getByLabelText('Learn new skills')).not.toBeChecked();
  const openIn = screen.getByRole('radiogroup', { name: 'Open in' });
  expect(
    within(openIn).getByRole('radio', { name: 'Ask each time' }),
  ).toHaveAttribute('aria-checked', 'true');
  expect(
    within(openIn).getByRole('radio', { name: 'App window' }),
  ).toBeVisible();
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
    'Web Search',
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
  expect(picker).toHaveDisplayValue("Conversation's model");
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
    <OverlayProvider>
      <PreferencesSnapshotPanel
        snapshot={snapshot.preferences}
        mutation={mutation}
      />
    </OverlayProvider>,
  );
  const openIn = screen.getByRole('radiogroup', { name: 'Open in' });
  fireEvent.click(within(openIn).getByRole('radio', { name: 'App window' }));
  await waitFor(() =>
    expect(mutation.review).toHaveBeenCalledWith(
      expect.objectContaining({
        page: 'preferences',
        field: 'window_mode',
        value: 'native',
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
