import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { LocalModelRuntimeView } from '../../api/types';
import { OverlayProvider } from '../../ui/overlays';
import LocalModelsSettings from './LocalModelsSettings';

const mock = vi.hoisted(() => ({
  controller: {
    localModelRuntime: vi.fn(),
    liveProviderStatus: vi.fn(),
    installLocalModel: vi.fn(),
    stopLocalModelRuntime: vi.fn(),
    removeLocalModelRuntime: vi.fn(),
    repairLocalModelRuntime: vi.fn(),
  },
}));

const GB = 1_000_000_000;

function view(
  installed: string[],
  extra: Partial<LocalModelRuntimeView> = {},
): LocalModelRuntimeView {
  const row = (
    id: string,
    name: string,
    quantization: string,
    older: boolean,
    size: number,
  ) => ({
    id,
    name,
    model_ref: `model:custom_openai_llamacpp:${id}`,
    size_bytes: size,
    fit: 'gpu' as const,
    context_tokens: older ? 131_072 : 32_768,
    everyday: size < 5 * GB,
    quantization,
    older,
    installed: installed.includes(id),
    download_bytes: installed.includes(id)
      ? 0
      : size + (installed.length ? 0 : 0.6 * GB),
  });
  return {
    schema_version: 1,
    state: installed.length ? 'installed' : 'not_installed',
    computer: 'NVIDIA GeForce RTX 4070 (12 GB)',
    acceleration: 'cuda-13',
    acceleration_label: 'NVIDIA (CUDA 13)',
    note: null,
    release: 'b12359',
    build_installed: installed.length > 0,
    update_bytes: 0,
    recommended: 'qwen3.8-27b-q2',
    models: [
      row('qwen3.8-27b-q2', 'Qwen3.8 27B', '2-bit', false, 9.9 * GB),
      row('qwen3.5-4b', 'Qwen3.5 4B', '4-bit', true, 2.7 * GB),
    ],
    download: null,
    failure: null,
    disk_bytes: installed.length ? 11.3 * GB : 0,
    free_bytes: 200 * GB,
    on_this_computer: true,
    ...extra,
  };
}

const card = (provider_id: string, display_name: string, models: number) => ({
  provider_id,
  display_name,
  group: 'local' as const,
  icon: '',
  configured: true,
  source: '',
  runtime_enabled: true,
  model_count: models,
  model_count_source: '',
  chat_count: models,
  media_count: 0,
  plan_type: '',
  fingerprint: '',
  account_id_hash: '',
  user_hash: '',
  oauth_client_id_fingerprint: '',
  oauth_client_id_configured: false,
  external_reference_exists: false,
  risk_label: '',
  billing: 'local' as const,
});

function show() {
  render(
    <MemoryRouter>
      <OverlayProvider>
        <LocalModelsSettings
          controller={mock.controller as unknown as ClientController}
        />
      </OverlayProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  for (const fn of Object.values(mock.controller)) fn.mockReset();
  mock.controller.liveProviderStatus.mockResolvedValue({
    schema_version: 1,
    providers: [
      card('ollama', 'Ollama', 3),
      card('custom_openai_lmstudio-1234', 'LM Studio', 2),
      card('custom_openai_llamacpp', 'Row-Bot (llama.cpp)', 1),
      { ...card('openai', 'OpenAI', 40), billing: 'pay_per_use' as const },
    ],
  });
});

it('lists every connected local runtime and Row-Bot’s download as one entry among them', async () => {
  mock.controller.localModelRuntime.mockResolvedValue(view(['qwen3.8-27b-q2']));
  show();

  const group = await screen.findByRole('region', { name: 'Local' });
  await within(group).findByText('LM Studio');
  expect(within(group).getByText('Ollama')).toBeVisible();
  expect(within(group).queryByText('OpenAI')).toBeNull();
  expect(
    within(group).getByText('Download with Row-Bot (llama.cpp)'),
  ).toBeVisible();
  expect(
    within(group).getByText(
      'Ready · 1 model · 11.3 GB on disk. It starts when a chat needs it.',
    ),
  ).toBeVisible();
  expect(
    within(group).getByRole('list', { name: 'Models Row-Bot downloaded' }),
  ).toHaveTextContent('Qwen3.8 27B · 2-bit9.9 GB · reads 32K');
  // Manage opens that runtime's own sheet on this page (F11).
  expect(
    within(group).getByRole('link', { name: 'Manage Ollama' }),
  ).toHaveAttribute('href', '/settings/models?provider=ollama');
});

it('Remove asks first, names the space it frees and says what it freed', async () => {
  mock.controller.localModelRuntime.mockResolvedValue(view(['qwen3.8-27b-q2']));
  mock.controller.removeLocalModelRuntime.mockResolvedValue({
    ...view([]),
    freed_bytes: 11.3 * GB,
  });
  show();

  fireEvent.click(
    await screen.findByRole('button', {
      name: 'Remove Row-Bot’s download and its models',
    }),
  );
  const dialog = await screen.findByRole('alertdialog', {
    name: 'Remove Row-Bot’s download and its models?',
  });
  expect(dialog).toHaveTextContent('freeing 11.3 GB');
  expect(dialog).toHaveTextContent('Other local runtimes aren’t touched');
  expect(mock.controller.removeLocalModelRuntime).not.toHaveBeenCalled();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Remove' }));

  await waitFor(() =>
    expect(mock.controller.removeLocalModelRuntime).toHaveBeenCalledWith(
      undefined,
    ),
  );
  expect(await screen.findByText('Removed. 11.3 GB freed.')).toBeVisible();
});

it('one model can be removed on its own', async () => {
  mock.controller.localModelRuntime.mockResolvedValue(
    view(['qwen3.8-27b-q2', 'qwen3.5-4b']),
  );
  mock.controller.removeLocalModelRuntime.mockResolvedValue(
    view(['qwen3.8-27b-q2']),
  );
  show();

  fireEvent.click(
    await screen.findByRole('button', { name: 'Remove Qwen3.5 4B' }),
  );
  const dialog = await screen.findByRole('alertdialog', {
    name: 'Remove Qwen3.5 4B?',
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Remove' }));

  await waitFor(() =>
    expect(mock.controller.removeLocalModelRuntime).toHaveBeenCalledWith(
      'qwen3.5-4b',
    ),
  );
});

it('Add offers every way to run models locally, Row-Bot’s download among them; Repair checks the computer again', async () => {
  mock.controller.localModelRuntime.mockResolvedValue(view(['qwen3.8-27b-q2']));
  mock.controller.repairLocalModelRuntime.mockResolvedValue(
    view(['qwen3.8-27b-q2']),
  );
  show();

  fireEvent.click(
    await screen.findByRole('button', {
      name: 'Add a way to run models locally',
    }),
  );
  const ways = screen.getByRole('list', {
    name: 'Ways to run models on this computer',
  });
  expect(within(ways).getAllByRole('listitem')).toHaveLength(5);
  expect(
    within(ways).getByRole('link', { name: 'Set up Ollama' }),
  ).toHaveAttribute('href', '/settings/models?add=custom-endpoint&type=ollama');
  fireEvent.click(
    within(ways).getByRole('button', {
      name: 'Set up Download with Row-Bot (llama.cpp)',
    }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Choose another model' }));
  fireEvent.click(screen.getByRole('button', { name: /Qwen3.5 4B/ }));
  expect(screen.getByRole('button', { name: 'Download 2.7 GB' })).toBeVisible();

  fireEvent.click(
    screen.getByRole('button', {
      name: 'Repair: check this computer’s graphics card again',
    }),
  );
  expect(
    await screen.findByText(
      'Checked: NVIDIA GeForce RTX 4070 (12 GB) · NVIDIA (CUDA 13).',
    ),
  ).toBeVisible();
});

it('on a Mac, Repair checks this Mac’s memory, not a graphics card (M2)', async () => {
  const mac = view(['qwen3.8-27b-q2'], {
    computer: 'Apple M4 (16 GB memory)',
    acceleration: 'metal',
    acceleration_label: 'Apple silicon (Metal)',
  });
  mock.controller.localModelRuntime.mockResolvedValue(mac);
  mock.controller.repairLocalModelRuntime.mockResolvedValue(mac);
  show();

  fireEvent.click(
    await screen.findByRole('button', {
      name: 'Repair: check this Mac’s memory again',
    }),
  );
  expect(screen.queryByRole('button', { name: /graphics card/ })).toBeNull();
  expect(
    await screen.findByText(
      'Checked: Apple M4 (16 GB memory) · Apple silicon (Metal).',
    ),
  ).toBeVisible();
});

it('an outdated build keeps working and offers its update with the size', async () => {
  const outdated = view(['qwen3.8-27b-q2'], { update_bytes: 0.6 * GB });
  outdated.models[0] = { ...outdated.models[0], download_bytes: 0.6 * GB };
  mock.controller.localModelRuntime.mockResolvedValue(outdated);
  show();

  expect(
    await screen.findByRole('button', { name: 'Update (600 MB)' }),
  ).toBeVisible();
});

it('with nothing connected, says so and offers Add', async () => {
  mock.controller.liveProviderStatus.mockResolvedValue({
    schema_version: 1,
    providers: [],
  });
  mock.controller.localModelRuntime.mockResolvedValue(view([]));
  show();

  expect(await screen.findByText('Nothing connected yet')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Add' }));
  expect(
    screen.getByRole('list', { name: 'Ways to run models on this computer' }),
  ).toBeVisible();
});
