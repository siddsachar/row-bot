import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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
  label,
  source: 'saved_catalog',
  available,
  context_window: 272000,
  reason: '',
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
  } as unknown as ClientController;
  return controller;
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
    await screen.findByRole('combobox', { name: 'Default model' }),
  ).toHaveValue(brain);
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

it('reviews and saves the selected Brain default internally with the exact qualified identity', async () => {
  const { controller } = show();
  const select = await screen.findByRole('combobox', { name: 'Default model' });
  fireEvent.change(select, { target: { value: 'model:codex:gpt-5.5' } });
  await waitFor(() =>
    expect(controller.executeDefaultModel).toHaveBeenCalledTimes(1),
  );
  expect(controller.reviewDefaultModel).toHaveBeenCalledWith({
    settings_revision: 'a'.repeat(64),
    provider_id: 'codex',
    model_id: 'gpt-5.5',
  });
  await waitFor(() => expect(select).toHaveValue('model:codex:gpt-5.5'));
  expect(
    screen.queryByRole('button', { name: /receipt/i }),
  ).not.toBeInTheDocument();
});

it('updates media toggles, defaults, camera, context, and delegation through their typed owners', async () => {
  const { controller } = show();
  await screen.findByRole('combobox', { name: 'Image model' });
  fireEvent.click(screen.getAllByRole('checkbox', { name: 'Enabled' })[1]);
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
  fireEvent.change(
    screen.getByRole('spinbutton', { name: /Maximum work rounds/ }),
    { target: { value: '120' } },
  );
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
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
  await screen.findByRole('combobox', { name: 'Default model' });
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
  const selector = await screen.findByRole('combobox', {
    name: 'Default model',
  });
  fireEvent.change(selector, { target: { value: 'model:codex:gpt-5.5' } });
  const button = await screen.findByRole('button', {
    name: 'Check original Brain save receipt',
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
