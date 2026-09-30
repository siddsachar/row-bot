import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type {
  AgentRuntimeSettingsState,
  DefaultModelSnapshot,
  ModelsSettingsState,
} from '../../api/types';
import { DefaultModelSession } from './DefaultModelSettings';
import ModelsPanel from './ModelsPanel';

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
/** The default model is the composer's searchable picker (U12). */
async function chooseDefault(name: RegExp) {
  fireEvent.click(await screen.findByRole('button', { name: 'Default model' }));
  const option = await screen.findByRole('option', { name });
  await act(async () => fireEvent.click(option));
}
function show(controller = fixture()) {
  const session = new DefaultModelSession();
  render(
    <MemoryRouter>
      <ModelsPanel controller={controller} session={session} />
    </MemoryRouter>,
  );
  return { controller, session };
}

it('renders actual defaults and limits while leaving the catalog off the initial row path', async () => {
  const { controller } = show();
  expect(
    await screen.findByRole('button', { name: 'Default model' }),
  ).toHaveTextContent('GPT-6-Astra');
  expect(screen.getByRole('combobox', { name: 'Vision model' })).toHaveValue(
    vision,
  );
  expect(screen.getByRole('combobox', { name: 'Image model' })).toHaveValue(
    image,
  );
  expect(screen.getByRole('combobox', { name: 'Video model' })).toHaveValue(
    video,
  );
  expect(
    screen.getByText(/Current Video default is unavailable/),
  ).toBeVisible();
  expect(
    screen.getByRole('spinbutton', { name: /Maximum work rounds/ }),
  ).toHaveValue(90);
  expect(screen.getByRole('button', { name: 'Model Catalog' })).toHaveAttribute(
    'aria-expanded',
    'false',
  );
  expect(controller.modelCatalogSummary).not.toHaveBeenCalled();
  expect(controller.modelCatalogPage).not.toHaveBeenCalled();
  expect(
    screen.queryByText(
      /Readiness not checked|Runtime not checked|Brain draft|Review default/,
    ),
  ).not.toBeInTheDocument();
});

it('offers explicit Ollama setup navigation without starting an install on render', async () => {
  const { controller } = show();
  const link = await screen.findByRole('link', { name: 'Download Ollama' });
  expect(link).toHaveAttribute('href', 'https://ollama.com/download');
  expect(link).toHaveAttribute('target', '_blank');
  expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  expect(controller.executeDefaultModel).not.toHaveBeenCalled();
});

it('offers Download Ollama only when Ollama is not running (B117)', async () => {
  const controller = fixture();
  vi.spyOn(controller, 'localRuntime').mockResolvedValue({
    schema_version: 1,
    state: 'running',
    platform: 'windows',
    download_url: 'https://ollama.com/download',
    models: [],
  });
  show(controller);
  await screen.findByRole('button', { name: 'Default model' });
  await waitFor(() => expect(controller.localRuntime).toHaveBeenCalled());
  expect(screen.queryByRole('link', { name: 'Download Ollama' })).toBeNull();
});

it('lets Vision follow the chat model (decision 11)', async () => {
  const { controller } = show();
  const vision = await screen.findByRole('combobox', { name: 'Vision model' });
  expect(
    screen.getByRole('option', { name: 'Same as chat model' }),
  ).toBeInTheDocument();
  fireEvent.change(vision, { target: { value: '' } });
  await waitFor(() =>
    expect(controller.updateModelSurface).toHaveBeenCalledWith({
      surface: 'vision',
      action: 'default',
      selection_ref: '',
    }),
  );
});

it('reviews and saves the selected Brain default internally with the exact qualified identity', async () => {
  const { controller } = show();
  const select = await screen.findByRole('button', { name: 'Default model' });
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

it('updates media toggles, defaults, camera, context, and delegation through their typed owners', async () => {
  const { controller } = show();
  await screen.findByRole('combobox', { name: 'Image model' });
  fireEvent.click(screen.getByRole('switch', { name: 'Enable image' }));
  await waitFor(() =>
    expect(controller.updateModelSurface).toHaveBeenCalledWith({
      surface: 'image',
      action: 'enabled',
      enabled: false,
    }),
  );
  fireEvent.change(screen.getByRole('combobox', { name: 'Image model' }), {
    target: { value: 'model:openai:gpt-image-1.5' },
  });
  await waitFor(() =>
    expect(controller.updateModelSurface).toHaveBeenCalledWith({
      surface: 'image',
      action: 'default',
      selection_ref: 'model:openai:gpt-image-1.5',
    }),
  );
  fireEvent.change(screen.getByRole('combobox', { name: 'Vision model' }), {
    target: { value: 'model:openai:gpt-4.1' },
  });
  await waitFor(() =>
    expect(controller.updateModelSurface).toHaveBeenCalledWith({
      surface: 'vision',
      action: 'default',
      selection_ref: 'model:openai:gpt-4.1',
    }),
  );
  fireEvent.change(screen.getByRole('combobox', { name: 'Video model' }), {
    target: { value: 'model:google:veo-3.1' },
  });
  await waitFor(() =>
    expect(controller.updateModelSurface).toHaveBeenCalledWith({
      surface: 'video',
      action: 'default',
      selection_ref: 'model:google:veo-3.1',
    }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Refresh camera list' }));
  await screen.findByText('2 camera(s) detected');
  fireEvent.change(screen.getByRole('combobox', { name: 'Camera' }), {
    target: { value: '1' },
  });
  await waitFor(() =>
    expect(controller.updateModelSurface).toHaveBeenCalledWith({
      surface: 'vision',
      action: 'camera',
      camera_index: 1,
    }),
  );
  fireEvent.click(screen.getByText('Advanced context'));
  fireEvent.change(
    screen.getByRole('combobox', { name: 'Provider context cap' }),
    { target: { value: '65536' } },
  );
  await waitFor(() =>
    expect(controller.updateModelContext).toHaveBeenCalledWith({
      policy_kind: 'provider',
      cap: 65536,
    }),
  );
  const rounds = screen.getByRole('spinbutton', {
    name: /Maximum work rounds/,
  });
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

it('refreshes the catalog only from the explicit action and keeps its rows closed', async () => {
  const { controller } = show();
  await screen.findByRole('button', { name: 'Default model' });
  expect(controller.refreshModelsCatalog).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Refresh catalog' }));
  await waitFor(() =>
    expect(controller.refreshModelsCatalog).toHaveBeenCalledTimes(1),
  );
  await screen.findByText('Model catalog refreshed.');
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
    name: 'Default model',
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
  const picker = await screen.findByRole('button', { name: 'Default model' });
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
  fireEvent.click(await screen.findByRole('button', { name: 'Default model' }));
  const row = await screen.findByRole('option', { name: /Claude Mystery/ });
  expect(row).toHaveAttribute('aria-disabled', 'true');
  expect(row).toHaveTextContent('No saved details for this model yet.');
  fireEvent.click(screen.getByRole('button', { name: 'Refresh the catalog' }));
  await waitFor(() =>
    expect(controller.refreshModelsCatalog).toHaveBeenCalledTimes(1),
  );
  expect(controller.reviewDefaultModel).not.toHaveBeenCalled();
});

it('links to Providers inside the router, never under a doubled basename (B31)', async () => {
  show();
  const link = await screen.findByRole('link', {
    name: 'Provider connections',
  });
  expect(link).toHaveAttribute('href', '/settings/providers');
});

it('names the default model in the page summary and follows a saved change', async () => {
  show();
  const chip = await screen.findByTitle(
    'Default model: GPT-6-Astra - ChatGPT / Codex',
  );
  expect(chip).toHaveTextContent('GPT-6-Astra');
  expect(chip).not.toHaveTextContent('ChatGPT / Codex');
  await chooseDefault(/GPT-5\.5/);
  expect(
    await screen.findByTitle('Default model: GPT-5.5 - ChatGPT / Codex'),
  ).toHaveTextContent('GPT-5.5');
});
