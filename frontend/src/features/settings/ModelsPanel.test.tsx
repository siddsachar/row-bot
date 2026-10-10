import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type {
  AgentRuntimeSettingsState,
  DefaultModelSnapshot,
  ModelsSettingsState,
} from '../../api/types';
import { OverlayProvider } from '../../ui/overlays';
import { DefaultModelSession } from './DefaultModelSettings';
import ModelsPanel from './ModelsPanel';
import { settingsRows } from './model';

const brain = 'model:codex:gpt-6-astra';
const vision = 'model:codex:gpt-6-astra';
const image = 'model:openai:gpt-image-2';
const video = 'model:xai:grok-imagine-video';
const option = (selection_ref: string, label: string, available = true) => ({
  selection_ref,
  provider_id: selection_ref.split(':')[1],
  label,
  source: 'saved_catalog',
  available,
  context_window: 272000,
  reason: '',
  billing: selection_ref.startsWith('model:codex:')
    ? ('subscription' as const)
    : ('pay_per_use' as const),
});
const baseState: ModelsSettingsState = {
  schema_version: 1,
  brain: {
    current_ref: brain,
    enabled: null,
    warning: '',
    options: [
      option(brain, 'GPT-6-Astra - ChatGPT / Codex'),
      option('model:codex:gpt-5.5', 'GPT-5.5 - ChatGPT / Codex'),
    ],
  },
  vision: {
    current_ref: vision,
    enabled: true,
    warning: '',
    options: [
      option(vision, 'GPT-6-Astra - ChatGPT / Codex'),
      option('model:openai:gpt-4.1', 'GPT-4.1 - OpenAI'),
    ],
  },
  image: {
    current_ref: image,
    enabled: true,
    warning: '',
    options: [
      option(image, 'gpt-image-2 - OpenAI'),
      option('model:openai:gpt-image-1.5', 'gpt-image-1.5 - OpenAI'),
    ],
  },
  video: {
    current_ref: video,
    enabled: true,
    warning: 'Current Video default is unavailable. Connect the provider.',
    options: [
      option(video, 'Grok Imagine Video - xAI', false),
      option('model:google:veo-3.1', 'Veo 3.1 - Google'),
    ],
  },
  camera_index: 0,
  context: {
    policy_kind: 'provider',
    selected_cap: null,
    native_max: 272000,
    effective_cap: 272000,
    warning: '',
  },
  freshness: 'fresh',
  generated_at: 1000,
  refresh_running: false,
};
const defaultSnapshot: DefaultModelSnapshot = {
  schema_version: 1,
  revision: 'a'.repeat(64),
  selection_ref: brain,
  provider_id: 'codex',
  model_id: 'gpt-6-astra',
  saved_state: 'saved',
  runtime_state: 'unknown',
};
const agent: AgentRuntimeSettingsState = {
  schema_version: 1,
  max_iterations: 90,
  max_spawn_depth: 1,
  max_concurrent_children: 3,
  max_active_children_global: 8,
  child_timeout_seconds: 0,
  goal_max_turns: 0,
};
const clientSnapshot = { handshake: { models: [] } };
function fixture() {
  const state = structuredClone(baseState);
  let saved = structuredClone(defaultSnapshot);
  const controller = {
    modelsSettings: vi.fn(async () => structuredClone(state)),
    defaultModel: vi.fn(async () => structuredClone(saved)),
    agentRuntimeSettings: vi.fn(async () => structuredClone(agent)),
    reviewDefaultModel: vi.fn(async (body) => ({
      ...body,
      operation: 'provider.default_model.save',
      action_digest: 'digest',
      nonce: 'review-nonce',
      snapshot: saved,
    })),
    executeDefaultModel: vi.fn(async (_review, _commandId) => {
      saved = {
        ...saved,
        selection_ref: 'model:codex:gpt-5.5',
        model_id: 'gpt-5.5',
        revision: 'b'.repeat(64),
      };
      state.brain.current_ref = saved.selection_ref!;
      return structuredClone(saved);
    }),
    defaultModelReceipt: vi.fn(async () => ({
      status: 'uncertain',
      selection: saved,
    })),
    updateModelSurface: vi.fn(
      async (body: {
        surface: 'vision' | 'image' | 'video';
        action: string;
        enabled?: boolean;
        selection_ref?: string;
        camera_index?: number;
      }) => {
        if (body.action === 'enabled')
          state[body.surface].enabled = body.enabled;
        if (body.action === 'default')
          state[body.surface].current_ref = body.selection_ref ?? '';
        if (body.action === 'camera')
          state.camera_index = body.camera_index ?? 0;
        return structuredClone(state);
      },
    ),
    updateModelContext: vi.fn(async (body: { cap: number | null }) => {
      state.context.selected_cap = body.cap;
      return structuredClone(state);
    }),
    saveAgentRuntimeSettings: vi.fn(async (body) => body),
    resetAgentRuntimeSettings: vi.fn(async () => structuredClone(agent)),
    refreshModelCameras: vi.fn(async () => ({ cameras: [0, 1] })),
    refreshModelsCatalog: vi.fn(async () => ({
      running: true,
      started: true,
      provider_id: '',
    })),
    liveProviderRefresh: vi.fn(async () => ({
      running: false,
      started: false,
      provider_id: '',
      ok: true,
      model_count: null,
      message: '',
    })),
    modelCatalogSummary: vi.fn(async () => ({
      schema_version: 1,
      revision: 'one',
      surface: 'chat',
      providers: [],
    })),
    modelCatalogPage: vi.fn(),
    localRuntime: vi.fn(async () => ({
      schema_version: 1,
      state: 'not_installed',
      platform: 'windows',
      download_url: 'https://ollama.com/download',
      models: [],
    })),
    refreshChoices: vi.fn(async () => undefined),
    subscribe: vi.fn(() => () => undefined),
    getSnapshot: vi.fn(() => clientSnapshot),
  } as unknown as ClientController;
  return controller;
}
/** Every Models picker is the composer's searchable list (U12, B227). */
async function chooseDefault(name: RegExp, picker = 'Brain model') {
  const button = await screen.findByRole('button', { name: picker });
  // A save in flight disables the pickers until it settles.
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  const option = await screen.findByRole('option', { name });
  await act(async () => fireEvent.click(option));
}
function Location() {
  return <output aria-label="Location">{useLocation().pathname}</output>;
}
function show(controller = fixture(), openExternal = vi.fn()) {
  const session = new DefaultModelSession();
  render(
    <MemoryRouter initialEntries={['/settings/models']}>
      <OverlayProvider>
        <ModelsPanel
          controller={controller}
          session={session}
          openExternal={openExternal}
        />
        <Location />
      </OverlayProvider>
    </MemoryRouter>,
  );
  return { controller, session, openExternal };
}
/** The floating notice that confirms a save (B258). */
async function notice(text: string | RegExp) {
  const notices = await screen.findByRole('region', { name: /Notifications/ });
  return within(notices.parentElement!).findByText(text);
}

it('says the Brain picker lists pinned models and where to pin more', async () => {
  show();
  fireEvent.click(await screen.findByRole('button', { name: 'Brain model' }));
  expect(
    await screen.findByText(
      'Pinned models are listed here. Pin more in the Catalog below.',
    ),
  ).toBeVisible();
});

it('renders actual defaults and limits while leaving catalog rows unloaded', async () => {
  const { controller } = show();
  expect(
    await screen.findByRole('button', { name: 'Brain model' }),
  ).toHaveTextContent('GPT-6-Astra');
  expect(
    screen.getByRole('button', { name: 'Vision model' }),
  ).toHaveTextContent('GPT-6-Astra');
  expect(screen.getByRole('button', { name: 'Image model' })).toHaveTextContent(
    'gpt-image-2',
  );
  expect(screen.getByRole('button', { name: 'Video model' })).toHaveTextContent(
    'Grok Imagine Video',
  );
  expect(
    screen.getByText(/Current Video default is unavailable/),
  ).toBeVisible();
  expect(screen.getByRole('spinbutton', { name: /Steps per run/ })).toHaveValue(
    90,
  );
  // The catalog lists its providers; model rows wait for a provider or search.
  expect(controller.modelCatalogPage).not.toHaveBeenCalled();
  expect(
    screen.queryByText(
      /Readiness not checked|Runtime not checked|Brain draft|Review default/,
    ),
  ).not.toBeInTheDocument();
});

it('has a row for every Models result of Settings search', async () => {
  show();
  await screen.findByRole('button', { name: 'Brain model' });
  await screen.findByRole('spinbutton', { name: /Steps per run/ });
  const rendered = new Set(
    [...document.querySelectorAll<HTMLElement>('[data-setting-anchor]')].map(
      (element) => element.dataset.settingAnchor,
    ),
  );
  expect(
    settingsRows
      .filter((row) => row.leaf === 'models' && !rendered.has(row.anchor))
      .map((row) => row.label),
  ).toEqual([]);
});

it('gives the four jobs one row each: name, purpose, switch, picker (B229)', async () => {
  show();
  await screen.findByRole('button', { name: 'Brain model' });
  expect(screen.getByText('Chats, agents and workflows.')).toBeVisible();
  expect(
    screen.getByText('Reads images, screenshots and your camera.'),
  ).toBeVisible();
  expect(screen.getByText('Makes and edits pictures.')).toBeVisible();
  expect(
    screen.getByText('Makes short clips and animates pictures.'),
  ).toBeVisible();
  // Vision, Image and Video can be switched off; the Brain can't.
  expect(
    screen.getAllByRole('switch').map((toggle) => toggle.ariaLabel),
  ).toEqual(['Enable vision', 'Enable image', 'Enable video']);
  // No stray save line, no chip repeating the Brain model.
  expect(screen.queryByText(/Brain default saved/)).toBeNull();
  expect(screen.queryByTitle(/Default model:/)).toBeNull();
});

it('says who is connected and how much there is to choose in one status line (B229)', async () => {
  show();
  await screen.findByRole('button', { name: 'Brain model' });
  // ChatGPT / Codex, OpenAI and Google offer available models; the unavailable
  // xAI video model counts for neither.
  expect(screen.getByText('3 providers connected')).toBeVisible();
  expect(screen.getByText('6 models to choose from')).toBeVisible();
});

it('offers models for this computer from the page menu only when Ollama is not running (B117)', async () => {
  const user = userEvent.setup();
  const { controller, openExternal } = show();
  await screen.findByRole('button', { name: 'Brain model' });
  await waitFor(() => expect(controller.localRuntime).toHaveBeenCalled());
  await user.click(screen.getByRole('button', { name: 'More model actions' }));
  await user.click(
    await screen.findByRole('menuitem', {
      name: 'Get models for this computer…',
    }),
  );
  expect(openExternal).toHaveBeenCalledWith('https://ollama.com/download');
  expect(controller.executeDefaultModel).not.toHaveBeenCalled();
});

it('leaves the download out of the page menu while Ollama runs (B117)', async () => {
  const user = userEvent.setup();
  const controller = fixture();
  vi.spyOn(controller, 'localRuntime').mockResolvedValue({
    schema_version: 1,
    state: 'running',
    platform: 'windows',
    download_url: 'https://ollama.com/download',
    models: [],
  });
  show(controller);
  await screen.findByRole('button', { name: 'Brain model' });
  await waitFor(() => expect(controller.localRuntime).toHaveBeenCalled());
  await user.click(screen.getByRole('button', { name: 'More model actions' }));
  expect(
    await screen.findByRole('menuitem', { name: 'Provider connections' }),
  ).toBeVisible();
  expect(
    screen.queryByRole('menuitem', { name: /Get models for this computer/ }),
  ).toBeNull();
});

it('opens Provider connections inside the router from the page menu (B31)', async () => {
  const user = userEvent.setup();
  show();
  await screen.findByRole('button', { name: 'Brain model' });
  await user.click(screen.getByRole('button', { name: 'More model actions' }));
  await user.click(
    await screen.findByRole('menuitem', { name: 'Provider connections' }),
  );
  expect(screen.getByRole('status', { name: 'Location' })).toHaveTextContent(
    '/settings/providers',
  );
});

it('re-reads the model settings from the page menu', async () => {
  const user = userEvent.setup();
  const { controller } = show();
  await screen.findByRole('button', { name: 'Brain model' });
  expect(controller.modelsSettings).toHaveBeenCalledTimes(1);
  await user.click(screen.getByRole('button', { name: 'More model actions' }));
  await user.click(
    await screen.findByRole('menuitem', { name: 'Re-read model settings' }),
  );
  await waitFor(() =>
    expect(controller.modelsSettings).toHaveBeenCalledTimes(2),
  );
});

it('lets Vision follow the Brain from the top of its list (decision 11, B227)', async () => {
  const { controller } = show();
  fireEvent.click(await screen.findByRole('button', { name: 'Vision model' }));
  const list = await screen.findByRole('listbox', { name: 'Models' });
  const [first] = within(list).getAllByRole('option');
  expect(first).toHaveTextContent('Same as Brain');
  // It names the model it follows.
  expect(first).toHaveTextContent('GPT-6-Astra');
  await act(async () => fireEvent.click(first));
  await waitFor(() =>
    expect(controller.updateModelSurface).toHaveBeenCalledWith({
      surface: 'vision',
      action: 'default',
      selection_ref: '',
    }),
  );
  expect(await notice('Vision now follows the Brain')).toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: 'Vision model' }),
  ).toHaveTextContent('Same as Brain');
});

it('lists only the Vision choices, grouped by provider with how each is paid for (B227)', async () => {
  const { controller } = show();
  fireEvent.click(await screen.findByRole('button', { name: 'Vision model' }));
  const list = await screen.findByRole('listbox', { name: 'Models' });
  expect(
    within(list)
      .getAllByRole('option')
      .map((option) => option.textContent),
  ).toEqual([
    expect.stringContaining('Same as Brain'),
    expect.stringContaining('GPT-6-Astra'),
    expect.stringContaining('GPT-4.1'),
  ]);
  // GPT-5.5 is a Brain choice the server did not offer for Vision.
  expect(within(list).queryByRole('option', { name: /GPT-5\.5/ })).toBeNull();
  expect(
    within(list).getByRole('group', { name: /ChatGPT \/ Codex/ }),
  ).toHaveTextContent('Subscription');
  expect(within(list).getByRole('group', { name: /OpenAI/ })).toHaveTextContent(
    'Pay per use',
  );
  fireEvent.change(screen.getByRole('combobox', { name: 'Search models' }), {
    target: { value: '4.1' },
  });
  expect(within(list).getAllByRole('option')).toHaveLength(1);
  await act(async () =>
    fireEvent.click(within(list).getByRole('option', { name: /GPT-4\.1/ })),
  );
  await waitFor(() =>
    expect(controller.updateModelSurface).toHaveBeenCalledWith({
      surface: 'vision',
      action: 'default',
      selection_ref: 'model:openai:gpt-4.1',
    }),
  );
  expect(
    await screen.findByRole('button', { name: 'Vision model' }),
  ).toHaveTextContent('GPT-4.1OpenAI · Pay per use');
  expect(await notice('Vision is now GPT-4.1')).toBeInTheDocument();
});

it('keeps an unset Image model unset and lists an unavailable Video model with its reason (B227)', async () => {
  const controller = fixture();
  const state = structuredClone(baseState);
  state.image.current_ref = '';
  state.video.options[0] = {
    ...state.video.options[0],
    unavailable_reason: 'configuration_required',
    reason: 'Connect this provider before using this model.',
  };
  vi.spyOn(controller, 'modelsSettings').mockResolvedValue(state);
  show(controller);
  const imagePicker = await screen.findByRole('button', {
    name: 'Image model',
  });
  expect(imagePicker).toHaveTextContent('Choose a model');
  fireEvent.click(imagePicker);
  await screen.findByRole('listbox', { name: 'Models' });
  // Only Vision can follow the Brain; Image and Video have no such row.
  expect(screen.queryByRole('option', { name: /Same as Brain/ })).toBeNull();
  fireEvent.keyDown(screen.getByRole('combobox', { name: 'Search models' }), {
    key: 'Escape',
  });
  await waitFor(() => expect(screen.queryByRole('listbox')).toBeNull());
  fireEvent.click(screen.getByRole('button', { name: 'Video model' }));
  const unavailable = await screen.findByRole('option', {
    name: /Grok Imagine Video/,
  });
  expect(unavailable).toHaveAttribute('aria-disabled', 'true');
  expect(unavailable).toHaveTextContent(
    'Connect this provider before using this model.',
  );
  fireEvent.click(unavailable);
  expect(controller.updateModelSurface).not.toHaveBeenCalled();
});

it('reviews and saves the selected Brain default internally with the exact qualified identity', async () => {
  const { controller } = show();
  const select = await screen.findByRole('button', { name: 'Brain model' });
  await chooseDefault(/GPT-5\.5/);
  await waitFor(() =>
    expect(controller.executeDefaultModel).toHaveBeenCalledTimes(1),
  );
  expect(controller.reviewDefaultModel).toHaveBeenCalledWith({
    settings_revision: 'a'.repeat(64),
    provider_id: 'codex',
    model_id: 'gpt-5.5',
  });
  await waitFor(() => expect(select).toHaveTextContent('GPT-5.5'));
  // The composer's picker offers the new default at once.
  expect(controller.refreshChoices).toHaveBeenCalled();
  expect(
    screen.queryByRole('button', { name: /receipt/i }),
  ).not.toBeInTheDocument();
});

it('confirms a Brain change with the floating notice, whose Undo saves the previous model back (B229)', async () => {
  const { controller } = show();
  await chooseDefault(/GPT-5\.5/);
  const confirmation = await notice('Brain is now GPT-5.5');
  const undo = within(confirmation.closest('li')!).getByRole('button', {
    name: 'Undo',
  });
  await act(async () => fireEvent.click(undo));
  await waitFor(() =>
    expect(controller.reviewDefaultModel).toHaveBeenLastCalledWith(
      expect.objectContaining({
        provider_id: 'codex',
        model_id: 'gpt-6-astra',
      }),
    ),
  );
  expect(controller.executeDefaultModel).toHaveBeenCalledTimes(2);
});

it('updates media toggles, defaults, camera, context, and delegation through their typed owners', async () => {
  const { controller } = show();
  await screen.findByRole('button', { name: 'Image model' });
  fireEvent.click(screen.getByRole('switch', { name: 'Enable image' }));
  await waitFor(() =>
    expect(controller.updateModelSurface).toHaveBeenCalledWith({
      surface: 'image',
      action: 'enabled',
      enabled: false,
    }),
  );
  fireEvent.click(screen.getByRole('switch', { name: 'Enable image' }));
  await waitFor(() =>
    expect(controller.updateModelSurface).toHaveBeenLastCalledWith({
      surface: 'image',
      action: 'enabled',
      enabled: true,
    }),
  );
  await chooseDefault(/gpt-image-1\.5/, 'Image model');
  await waitFor(() =>
    expect(controller.updateModelSurface).toHaveBeenCalledWith({
      surface: 'image',
      action: 'default',
      selection_ref: 'model:openai:gpt-image-1.5',
    }),
  );
  await chooseDefault(/Veo 3\.1/, 'Video model');
  await waitFor(() =>
    expect(controller.updateModelSurface).toHaveBeenCalledWith({
      surface: 'video',
      action: 'default',
      selection_ref: 'model:google:veo-3.1',
    }),
  );
  // The camera list loads when its select is opened, not on page load.
  const camera = screen.getByRole('combobox', { name: 'Camera' });
  expect(controller.refreshModelCameras).not.toHaveBeenCalled();
  fireEvent.focus(camera);
  await waitFor(() =>
    expect(within(camera).getAllByRole('option')).toHaveLength(2),
  );
  expect(controller.refreshModelCameras).toHaveBeenCalledTimes(1);
  fireEvent.change(camera, { target: { value: '1' } });
  await waitFor(() =>
    expect(controller.updateModelSurface).toHaveBeenCalledWith({
      surface: 'vision',
      action: 'camera',
      camera_index: 1,
    }),
  );
  fireEvent.click(screen.getByText('Advanced context'));
  expect(
    screen.getByText(
      'GPT-6-Astra can read about 272,000 tokens at once, roughly 820 pages. Row-Bot uses all of it.',
    ),
  ).toBeVisible();
  fireEvent.click(screen.getByRole('radio', { name: 'Limit…' }));
  const limit = screen.getByRole('spinbutton', { name: 'Limit in tokens' });
  fireEvent.change(limit, { target: { value: '65536' } });
  fireEvent.blur(limit);
  await waitFor(() =>
    expect(controller.updateModelContext).toHaveBeenCalledWith({
      policy_kind: 'provider',
      cap: 65536,
    }),
  );
  fireEvent.click(screen.getByRole('radio', { name: 'Automatic' }));
  await waitFor(() =>
    expect(controller.updateModelContext).toHaveBeenLastCalledWith({
      policy_kind: 'provider',
      cap: null,
    }),
  );
  const rounds = screen.getByRole('spinbutton', { name: /Steps per run/ });
  fireEvent.change(rounds, { target: { value: '120' } });
  // Leaving the field saves it (decision 19); there is no Save button.
  expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
  fireEvent.blur(rounds);
  await waitFor(() =>
    expect(controller.saveAgentRuntimeSettings).toHaveBeenCalledWith(
      expect.objectContaining({
        max_iterations: 120,
        child_timeout_seconds: 0,
      }),
    ),
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Restore recommended defaults' }),
  );
  await waitFor(() =>
    expect(controller.resetAgentRuntimeSettings).toHaveBeenCalledTimes(1),
  );
});

it('shows Camera only under Vision while Vision is on (B229)', async () => {
  const { controller } = show();
  expect(
    await screen.findByRole('combobox', { name: 'Camera' }),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole('switch', { name: 'Enable vision' }));
  await waitFor(() =>
    expect(controller.updateModelSurface).toHaveBeenCalledWith({
      surface: 'vision',
      action: 'enabled',
      enabled: false,
    }),
  );
  expect(screen.queryByRole('combobox', { name: 'Camera' })).toBeNull();
  // A job that is off keeps its picker in view, but closed.
  expect(screen.getByRole('button', { name: 'Vision model' })).toBeDisabled();
  expect(await notice('Vision turned off')).toBeInTheDocument();
});

it('sets a default goal turn limit, where 0 means no limit (B243)', async () => {
  const { controller } = show();
  const limit = await screen.findByRole('spinbutton', {
    name: /Goal turn limit/,
  });
  expect(limit).toHaveValue(0);
  fireEvent.change(limit, { target: { value: '30' } });
  fireEvent.blur(limit);
  await waitFor(() =>
    expect(controller.saveAgentRuntimeSettings).toHaveBeenLastCalledWith(
      expect.objectContaining({ goal_max_turns: 30 }),
    ),
  );
  fireEvent.change(limit, { target: { value: '0' } });
  fireEvent.blur(limit);
  await waitFor(() =>
    expect(controller.saveAgentRuntimeSettings).toHaveBeenLastCalledWith(
      expect.objectContaining({ goal_max_turns: 0 }),
    ),
  );
});

it('refreshes the catalog only from its status line and keeps its rows closed', async () => {
  const { controller } = show();
  await screen.findByRole('button', { name: 'Brain model' });
  expect(controller.refreshModelsCatalog).not.toHaveBeenCalled();
  expect(screen.getByText(/^Updated /)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Refresh catalog' }));
  await waitFor(() =>
    expect(controller.refreshModelsCatalog).toHaveBeenCalledTimes(1),
  );
  expect(await notice('Model catalog refreshed.')).toBeInTheDocument();
  expect(controller.liveProviderRefresh).toHaveBeenCalled();
  expect(controller.modelCatalogPage).not.toHaveBeenCalled();
});

it('keeps only the original receipt action visible while a Brain save is uncertain', async () => {
  const controller = fixture();
  vi.spyOn(controller, 'executeDefaultModel').mockRejectedValue({
    code: 'operation_uncertain',
  });
  const receipt = vi
    .spyOn(controller, 'defaultModelReceipt')
    .mockResolvedValue({
      command_id: crypto.randomUUID(),
      status: 'uncertain',
      published: false,
      selection: defaultSnapshot,
    });
  show(controller);
  const selector = await screen.findByRole('button', {
    name: 'Brain model',
  });
  await chooseDefault(/GPT-5\.5/);
  const button = await screen.findByRole('button', {
    name: 'Check the save',
  });
  expect(button).toBeVisible();
  expect(selector).toBeDisabled();
  receipt.mockResolvedValue({
    command_id: crypto.randomUUID(),
    status: 'completed',
    published: true,
    selection: { ...defaultSnapshot, selection_ref: 'model:codex:gpt-5.5' },
  });
  fireEvent.click(button);
  await waitFor(() => expect(button).not.toBeInTheDocument());
});

it("offers the page's one model list in the Brain picker, never a second list (B226)", async () => {
  const controller = fixture();
  // A composer-only entry must not reach the Brain picker: the page's list
  // and the save share one rule, so the picker never offers what it refuses.
  vi.spyOn(controller, 'getSnapshot').mockReturnValue({
    handshake: {
      models: [
        {
          provider_id: 'codex',
          model_ref: 'model:codex:composer-only',
          label: 'Composer Only - ChatGPT / Codex',
          available: true,
          billing: 'subscription',
        },
      ],
    },
  } as never);
  show(controller);
  const picker = await screen.findByRole('button', { name: 'Brain model' });
  expect(picker).toHaveTextContent('ChatGPT / Codex · Subscription');
  fireEvent.click(picker);
  expect(
    await screen.findByRole('option', { name: /GPT-5\.5/ }),
  ).toBeInTheDocument();
  expect(screen.queryByRole('option', { name: /Composer Only/ })).toBeNull();
});

it('lists a pinned model without saved details with its reason and a catalog refresh (B226)', async () => {
  const controller = fixture();
  vi.spyOn(controller, 'modelsSettings').mockResolvedValue({
    ...structuredClone(baseState),
    brain: {
      ...structuredClone(baseState.brain),
      options: [
        ...structuredClone(baseState.brain.options),
        {
          ...option(
            'model:claude_subscription:claude-mystery',
            'Claude Mystery - Claude Subscription',
            false,
          ),
          unavailable_reason: 'metadata_missing',
          reason: 'No saved details for this model yet. Refresh the catalog.',
        },
      ],
    },
  });
  show(controller);
  fireEvent.click(await screen.findByRole('button', { name: 'Brain model' }));
  const row = await screen.findByRole('option', { name: /Claude Mystery/ });
  expect(row).toHaveAttribute('aria-disabled', 'true');
  expect(row).toHaveTextContent('No saved details for this model yet.');
  fireEvent.click(screen.getByRole('button', { name: 'Refresh the catalog' }));
  await waitFor(() =>
    expect(controller.refreshModelsCatalog).toHaveBeenCalledTimes(1),
  );
  expect(controller.reviewDefaultModel).not.toHaveBeenCalled();
});
