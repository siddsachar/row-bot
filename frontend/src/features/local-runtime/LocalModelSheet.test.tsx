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

const GIB = 2 ** 30;
const KV = 34_816; // the 27B's cache per token
/** What the 27B 4-bit takes loaded at a window: file, cache and working memory. */
const need = (tokens: number) => 16_564_259_744 + KV * tokens + 700 * 2 ** 20;
const FIELDS: LocalModelSettingsView['sampling']['fields'] = [
  'temperature',
  'top_p',
  'top_k',
  'min_p',
  'presence_penalty',
];
const THINKING = {
  temperature: 1,
  top_p: 0.95,
  top_k: 20,
  min_p: 0,
  presence_penalty: 0,
};
const NOT_THINKING = {
  temperature: 0.7,
  top_p: 0.8,
  top_k: 20,
  min_p: 0,
  presence_penalty: 1.5,
};

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
      automatic_memory_bytes: need(65_536),
      memory_place: 'card',
      room_bytes: 24 * GIB,
      choices: [32_768, 65_536].map((tokens) => ({
        tokens,
        memory_bytes: need(tokens),
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
    sampling: {
      value: 'auto',
      custom: null,
      card: { thinking: THINKING, non_thinking: NOT_THINKING },
      thinks: true,
      fields: FIELDS,
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
      room_bytes: null,
      choices: [],
      current: 32_768,
      loaded: 32_768,
      reload_choices: [16_384, 65_536, 131_072],
    },
    thinking: { available: false, value: 'auto' },
    reply_limit: { value: null, automatic: null, choices: [4096, 8192] },
    sampling: {
      value: 'auto',
      custom: null,
      card: null,
      thinks: true,
      fields: FIELDS.filter((field) => field !== 'min_p'),
    },
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

/** Opens one of the sheet's size menus (the app's Menu) and picks an item. */
async function pick(menu: RegExp, item: string) {
  fireEvent.pointerDown(await screen.findByRole('button', { name: menu }), {
    button: 0,
  });
  fireEvent.click(await screen.findByRole('menuitem', { name: item }));
}

it('says what the model and its context take of the card, and saves a size that fits', async () => {
  open(builtIn());

  expect(
    await screen.findByRole('button', { name: 'Context: Automatic · 64K' }),
  ).toBeTruthy();
  expect(
    screen.getByText(
      'Reads 64K · model and context about 18.2 GB of your card’s 24 GB',
    ),
  ).toBeTruthy();
  mock.controller.saveLocalModelSettings.mockResolvedValue({
    ...builtIn({ chosen: 32_768, current: 32_768 }),
    applied: 'restarted',
  });

  await pick(/^Context:/, '32K');

  await screen.findByText('Saved. It applies from the next message.');
  expect(mock.controller.saveLocalModelSettings).toHaveBeenCalledWith({
    model_ref: 'model:custom_openai_llamacpp:qwen3.8-27b-q4',
    context: 32_768,
    thinking: 'auto',
    reply_limit: null,
    sampling: 'auto',
    sampling_values: null,
  });
  expect(
    screen.getByText(
      'Reads 32K · model and context about 17.2 GB of your card’s 24 GB',
    ),
  ).toBeTruthy();
});

it('opens with focus on the sheet itself, not a control that would show a ring', async () => {
  open(builtIn());
  await screen.findByRole('button', { name: /^Context:/ });

  expect(document.activeElement?.className).toContain(
    'local-model-sheet-focus',
  );
});

it('says when a Mac would be slow at a size, and when a busy server applies it', async () => {
  const mac = builtIn({
    memory_place: 'memory',
    room_bytes: 16 * GIB,
    choices: [
      { tokens: 32_768, memory_bytes: need(32_768), slow: false },
      { tokens: 65_536, memory_bytes: need(65_536), slow: true },
    ],
  });
  open(mac);
  fireEvent.pointerDown(
    await screen.findByRole('button', { name: /^Context:/ }),
    { button: 0 },
  );
  expect(
    await screen.findByText('About 18.2 GB · slow first reply on long chats'),
  ).toBeTruthy();
  mock.controller.saveLocalModelSettings.mockResolvedValue({
    ...mac,
    context: { ...mac.context, chosen: 65_536 },
    applied: 'when_idle',
  });

  fireEvent.click(screen.getByRole('menuitem', { name: '64K' }));

  await screen.findByText(
    'Saved. It applies once the reply being written finishes.',
  );
  expect(
    screen.getByText(
      'Reads 64K · model and context about 18.2 GB of this computer’s 16 GB · slow first reply on long chats',
    ),
  ).toBeTruthy();
});

it('saves Thinking and, under More, the reply length', async () => {
  open(builtIn());
  await screen.findByRole('button', { name: /^Context:/ });
  mock.controller.saveLocalModelSettings.mockResolvedValue(builtIn());

  fireEvent.click(screen.getByRole('radio', { name: 'Off' }));
  await waitFor(() =>
    expect(mock.controller.saveLocalModelSettings).toHaveBeenLastCalledWith(
      expect.objectContaining({ thinking: 'off' }),
    ),
  );
  fireEvent.click(screen.getByText('More'));
  expect(
    screen.getByRole('button', {
      name: 'Reply length: Automatic · up to 32K tokens',
    }),
  ).toBeTruthy();
  await pick(/^Reply length:/, '8K tokens');
  await waitFor(() =>
    expect(mock.controller.saveLocalModelSettings).toHaveBeenLastCalledWith(
      expect.objectContaining({ reply_limit: 8192 }),
    ),
  );
});

it('shows the model card’s sampling and lets the person set their own', async () => {
  open(builtIn());
  await screen.findByRole('button', { name: /^Context:/ });
  fireEvent.click(screen.getByText('More'));

  expect(
    screen.getByText(
      'Thinking: temperature 1 · top-p 0.95 · top-k 20 · min-p 0 · presence penalty 0',
    ),
  ).toBeTruthy();
  expect(
    screen.getByText(
      'Not thinking: temperature 0.7 · top-p 0.8 · top-k 20 · min-p 0 · presence penalty 1.5',
    ),
  ).toBeTruthy();
  const custom = builtIn();
  custom.sampling = { ...custom.sampling, value: 'custom', custom: THINKING };
  mock.controller.saveLocalModelSettings.mockResolvedValue(custom);

  // Custom starts from what a reply gets now: the card's thinking values.
  await pick(/^Sampling:/, 'Custom');
  await waitFor(() =>
    expect(mock.controller.saveLocalModelSettings).toHaveBeenLastCalledWith(
      expect.objectContaining({
        sampling: 'custom',
        sampling_values: THINKING,
      }),
    ),
  );
  const temperature = await screen.findByLabelText('Temperature');
  fireEvent.change(temperature, { target: { value: '0.4' } });
  fireEvent.blur(temperature);

  await waitFor(() =>
    expect(mock.controller.saveLocalModelSettings).toHaveBeenLastCalledWith(
      expect.objectContaining({
        sampling: 'custom',
        sampling_values: { ...THINKING, temperature: 0.4 },
      }),
    ),
  );
  fireEvent.change(temperature, { target: { value: '3' } });
  fireEvent.blur(temperature);
  expect(
    await screen.findByText('Temperature is between 0 and 2.'),
  ).toBeTruthy();
  expect(mock.controller.saveLocalModelSettings).toHaveBeenCalledTimes(2);
});

it('a model without card values gets the engine’s own until the person enters one', async () => {
  open(lmStudio());
  await screen.findByText(/LM Studio loaded it at 32K\./);
  fireEvent.click(screen.getByText('More'));
  expect(screen.getByText('LM Studio uses its own values.')).toBeTruthy();
  expect(
    screen.getByRole('button', { name: 'Sampling: Automatic' }),
  ).toBeTruthy();

  await pick(/^Sampling:/, 'Custom');
  expect(mock.controller.saveLocalModelSettings).not.toHaveBeenCalled();
  expect(screen.queryByLabelText('Min-p')).toBeNull(); // LM Studio isn't sent it
  mock.controller.saveLocalModelSettings.mockResolvedValue(lmStudio());
  const temperature = screen.getByLabelText('Temperature');
  fireEvent.change(temperature, { target: { value: '0.8' } });
  fireEvent.keyDown(temperature, { key: 'Enter' });

  await waitFor(() =>
    expect(mock.controller.saveLocalModelSettings).toHaveBeenCalledWith(
      expect.objectContaining({
        sampling: 'custom',
        sampling_values: { temperature: 0.8 },
      }),
    ),
  );
});

it('shows what a server the person runs loaded, and reloads LM Studio at another size', async () => {
  open(lmStudio());
  await screen.findByText(/LM Studio loaded it at 32K\./);

  expect(screen.queryByRole('button', { name: /^Context:/ })).toBeNull();
  expect(screen.queryByRole('radiogroup', { name: 'Thinking' })).toBeNull();
  // The next size up from what it loaded is offered first.
  expect(
    screen.getByRole('button', { name: 'Reload in LM Studio at 64K' }),
  ).toBeTruthy();
  mock.controller.reloadLocalModelInLmStudio.mockResolvedValue({
    ...lmStudio(),
    notice: 'LM Studio loaded it at 128K.',
  });
  await pick(/^Size to load it at in LM Studio:/, '128K');
  fireEvent.click(
    screen.getByRole('button', { name: 'Reload in LM Studio at 128K' }),
  );

  await screen.findByText('LM Studio loaded it at 128K.');
  expect(mock.controller.reloadLocalModelInLmStudio).toHaveBeenCalledWith({
    model_ref: 'model:custom_openai_lmstudio-1234:gemma-4',
    context: 131_072,
  });
});

it('offers a way back when the settings cannot be read', async () => {
  mock.controller.localModelSettings.mockRejectedValueOnce({
    code: 'model_configuration_unavailable',
  });
  open(builtIn());

  await screen.findByText('These settings can’t be shown');
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }));

  await screen.findByRole('button', { name: /^Context:/ });
});
