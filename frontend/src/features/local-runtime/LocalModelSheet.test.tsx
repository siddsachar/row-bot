import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { LocalModelSettingsView } from '../../api/types';
import { OverlayProvider } from '../../ui/overlays';
import { LocalModelSettingsButton } from './LocalModelSheet';

const mock = vi.hoisted(() => ({
  controller: {
    localModelSettings: vi.fn(),
    saveLocalModelSettings: vi.fn(),
    reloadLocalModelInLmStudio: vi.fn(),
  },
}));

const KV = 34_816; // the 27B's cache per token

function builtIn(
  extra: Partial<LocalModelSettingsView['context']> = {},
): LocalModelSettingsView {
  return {
    schema_version: 1,
    model_ref: 'model:custom_openai_llamacpp:qwen3.8-27b-q4',
    name: 'Qwen3.8 27B · 4-bit',
    engine: 'built_in',
    engine_name: 'Row-Bot (llama.cpp)',
    profile: 'llama_cpp',
    context: {
      editable: true,
      chosen: null,
      automatic: 65_536,
      automatic_memory_bytes: KV * 65_536,
      memory_place: 'card',
      choices: [32_768, 65_536].map((tokens) => ({
        tokens,
        memory_bytes: KV * tokens,
        slow: false,
      })),
      current: 65_536,
      loaded: null,
      reload_choices: [],
      ...extra,
    },
    thinking: { available: true, value: 'auto' },
    reply_limit: {
      value: null,
      automatic: 32_768,
      choices: [4096, 8192, 16_384, 32_768],
    },
    applied: null,
    notice: null,
  };
}

function lmStudio(): LocalModelSettingsView {
  return {
    ...builtIn(),
    model_ref: 'model:custom_openai_lmstudio-1234:gemma-4',
    name: 'gemma-4',
    engine: 'server',
    engine_name: 'LM Studio',
    profile: 'lmstudio',
    context: {
      editable: false,
      chosen: null,
      automatic: null,
      automatic_memory_bytes: null,
      memory_place: null,
      choices: [],
      current: 32_768,
      loaded: 32_768,
      reload_choices: [16_384, 65_536, 131_072],
    },
    thinking: { available: false, value: 'auto' },
    reply_limit: { value: null, automatic: null, choices: [4096, 8192] },
  };
}

beforeEach(() => {
  for (const fn of Object.values(mock.controller)) fn.mockReset();
});

function open(view: LocalModelSettingsView) {
  mock.controller.localModelSettings.mockResolvedValue(view);
  render(
    <OverlayProvider>
      <LocalModelSettingsButton
        controller={mock.controller as unknown as ClientController}
        modelRef={view.model_ref}
        name={view.name}
      />
    </OverlayProvider>,
  );
  fireEvent.click(
    screen.getByRole('button', { name: `Settings for ${view.name}` }),
  );
}

it('shows the window Automatic picks with its memory, and saves a size that fits', async () => {
  open(builtIn());
  const context = await screen.findByRole('combobox', { name: 'Context' });

  expect(screen.getByRole('option', { name: 'Automatic · 64K' })).toBeTruthy();
  expect(
    screen.getByText('Reads 64K now · about 2.3 GB on your card'),
  ).toBeTruthy();
  mock.controller.saveLocalModelSettings.mockResolvedValue({
    ...builtIn({ chosen: 32_768, current: 32_768 }),
    applied: 'restarted',
  });

  fireEvent.change(context, { target: { value: '32768' } });

  await screen.findByText('Saved. It applies from the next message.');
  expect(mock.controller.saveLocalModelSettings).toHaveBeenCalledWith({
    model_ref: 'model:custom_openai_llamacpp:qwen3.8-27b-q4',
    context: 32_768,
    thinking: 'auto',
    reply_limit: null,
  });
  expect(
    screen.getByText('Reads 32K now · about 1.1 GB on your card'),
  ).toBeTruthy();
});

it('says when a Mac would be slow at a size, and when a busy server applies it', async () => {
  const mac = builtIn({
    memory_place: 'memory',
    choices: [
      { tokens: 32_768, memory_bytes: KV * 32_768, slow: false },
      { tokens: 65_536, memory_bytes: KV * 65_536, slow: true },
    ],
  });
  open(mac);
  const context = await screen.findByRole('combobox', { name: 'Context' });
  expect(
    screen.getByRole('option', {
      name: '64K · slow first reply on long chats',
    }),
  ).toBeTruthy();
  mock.controller.saveLocalModelSettings.mockResolvedValue({
    ...mac,
    context: { ...mac.context, chosen: 65_536 },
    applied: 'when_idle',
  });

  fireEvent.change(context, { target: { value: '65536' } });

  await screen.findByText(
    'Saved. It applies once the reply being written finishes.',
  );
  expect(
    screen.getByText(
      'Reads 64K now · about 2.3 GB in memory · slow first reply on long chats',
    ),
  ).toBeTruthy();
});

it('saves Thinking and, under More, the reply length', async () => {
  open(builtIn());
  await screen.findByRole('combobox', { name: 'Context' });
  mock.controller.saveLocalModelSettings.mockResolvedValue(builtIn());

  fireEvent.click(screen.getByRole('radio', { name: 'Off' }));
  await waitFor(() =>
    expect(mock.controller.saveLocalModelSettings).toHaveBeenLastCalledWith(
      expect.objectContaining({ thinking: 'off' }),
    ),
  );
  fireEvent.click(screen.getByText('More'));
  expect(
    screen.getByRole('option', { name: 'Automatic · up to 32K' }),
  ).toBeTruthy();
  fireEvent.change(screen.getByRole('combobox', { name: 'Reply length' }), {
    target: { value: '8192' },
  });
  await waitFor(() =>
    expect(mock.controller.saveLocalModelSettings).toHaveBeenLastCalledWith(
      expect.objectContaining({ reply_limit: 8192 }),
    ),
  );
});

it('shows what a server the person runs loaded, and reloads LM Studio at another size', async () => {
  open(lmStudio());
  await screen.findByText(/LM Studio loaded it at 32K\./);

  expect(screen.queryByRole('combobox', { name: 'Context' })).toBeNull();
  expect(screen.queryByRole('radiogroup', { name: 'Thinking' })).toBeNull();
  mock.controller.reloadLocalModelInLmStudio.mockResolvedValue({
    ...lmStudio(),
    notice: 'LM Studio loaded it at 64K.',
  });
  fireEvent.change(screen.getByLabelText('Size to load it at in LM Studio'), {
    target: { value: '65536' },
  });
  fireEvent.click(
    screen.getByRole('button', { name: 'Reload in LM Studio at 64K' }),
  );

  await screen.findByText('LM Studio loaded it at 64K.');
  expect(mock.controller.reloadLocalModelInLmStudio).toHaveBeenCalledWith({
    model_ref: 'model:custom_openai_lmstudio-1234:gemma-4',
    context: 65_536,
  });
});

it('offers a way back when the settings cannot be read', async () => {
  mock.controller.localModelSettings.mockRejectedValueOnce({
    code: 'model_configuration_unavailable',
  });
  open(builtIn());

  await screen.findByText('These settings can’t be shown');
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }));

  await screen.findByRole('combobox', { name: 'Context' });
});
