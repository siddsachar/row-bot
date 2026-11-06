import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { ConversationModelStatus, ModelChoice } from '../../api/types';
import ModelPicker from './ModelPicker';

const models: ModelChoice[] = [
  {
    provider_id: 'codex',
    model_ref: 'model:codex:gpt-5.6-sol',
    label: 'GPT-5.6-Sol - ChatGPT / Codex',
    available: true,
    billing: 'subscription',
  },
  {
    provider_id: 'openrouter',
    model_ref: 'model:openrouter:openai/gpt-5.6-sol',
    label: 'OpenAI: GPT-5.6 Sol - OpenRouter',
    available: true,
    billing: 'credits',
  },
  {
    provider_id: 'ollama',
    model_ref: 'model:ollama:qwen3.8:27b',
    label: 'qwen3.8:27b - Ollama Local',
    available: true,
    billing: 'local',
  },
];

function show(
  current: string | undefined,
  status: ConversationModelStatus | null,
  extra: Partial<Parameters<typeof ModelPicker>[0]> = {},
) {
  const handlers = {
    onOpenChange: vi.fn(),
    onChoose: vi.fn(),
    onConnect: vi.fn(),
    onReconnect: vi.fn(),
    onManage: vi.fn(),
    onThinking: vi.fn(),
  };
  const view = render(
    <ModelPicker
      models={models}
      current={current}
      status={status}
      disabled={false}
      open={false}
      reasoning={null}
      thinkingLabel="Default"
      {...handlers}
      {...extra}
    />,
  );
  return { ...handlers, ...view };
}

const pill = () => screen.getByRole('button', { name: 'Model' });

it('carries a cloud or local glyph and never says Ready for an unavailable model (B115)', () => {
  const { rerender } = show('model:codex:gpt-5.6-sol', {
    state: 'ready',
    local: false,
  });
  expect(
    within(pill()).getByRole('img', { name: 'Cloud model' }),
  ).toBeVisible();
  expect(pill()).toHaveTextContent('GPT-5.6-Sol');
  rerender(
    <ModelPicker
      models={models}
      current="model:ollama:qwen3.8:27b"
      status={{ state: 'ready', local: true }}
      disabled={false}
      open={false}
      onOpenChange={vi.fn()}
      onChoose={vi.fn()}
      reasoning={null}
      thinkingLabel="Default"
      onThinking={vi.fn()}
      onConnect={vi.fn()}
      onManage={vi.fn()}
    />,
  );
  expect(
    within(pill()).getByRole('img', { name: 'Runs on this device' }),
  ).toBeVisible();
  rerender(
    <ModelPicker
      models={models}
      current="model:ollama:qwen3.8:27b"
      status={{
        state: 'unavailable',
        reason: "Ollama isn't running",
        fix: 'reconnect',
        local: true,
      }}
      disabled={false}
      open={false}
      onOpenChange={vi.fn()}
      onChoose={vi.fn()}
      reasoning={null}
      thinkingLabel="Default"
      onThinking={vi.fn()}
      onConnect={vi.fn()}
      onManage={vi.fn()}
    />,
  );
  expect(
    within(pill()).getByRole('img', { name: 'Unavailable' }),
  ).toBeVisible();
  expect(within(pill()).queryByText('Ready')).toBeNull();
  expect(pill()).toHaveAccessibleDescription(
    "Model: qwen3.8:27b · Unavailable — Ollama isn't running",
  );
});

it('says Chat only when tools are off', () => {
  show(
    'model:codex:gpt-5.6-sol',
    { state: 'ready', local: false },
    { runtimeMode: 'chat_only' },
  );
  expect(within(pill()).getByText('Chat only')).toBeVisible();
});

it('asks to choose a model when none is chosen yet', () => {
  show(undefined, {
    state: 'missing',
    reason: 'No model chosen yet',
    fix: 'choose',
  });
  expect(pill()).toHaveTextContent('Choose a model');
  expect(pill()).toHaveAccessibleDescription('No model chosen yet');
});

it('opens with the reason and one fix for an unavailable model', async () => {
  const handlers = show(
    'model:ollama:qwen3.8:27b',
    {
      state: 'unavailable',
      reason: "Ollama isn't running",
      fix: 'reconnect',
      local: true,
    },
    { open: true },
  );
  const banner = await screen.findByRole('status');
  expect(banner).toHaveTextContent(
    "qwen3.8:27b · Unavailable — Ollama isn't running",
  );
  await act(async () =>
    fireEvent.click(within(banner).getByRole('button', { name: 'Reconnect' })),
  );
  expect(handlers.onReconnect).toHaveBeenCalledTimes(1);
  expect(
    within(banner).getByRole('button', { name: 'Choose another model' }),
  ).toBeVisible();
});

it('tags each route with how it is paid for (U13)', async () => {
  show('model:codex:gpt-5.6-sol', { state: 'ready' }, { open: true });
  const list = await screen.findByRole('listbox', { name: 'Models' });
  const groups = within(list).getAllByRole('group');
  expect(
    groups.map((group) => group.getAttribute('aria-labelledby')),
  ).toHaveLength(3);
  expect(within(list).getByText('Subscription')).toBeVisible();
  expect(within(list).getByText('Credits')).toBeVisible();
  expect(within(list).getByText('Local · free')).toBeVisible();
});

// F10: favourites first, hidden models under "More models".
const local = (
  name: string,
  extra: Partial<ModelChoice> = {},
): ModelChoice => ({
  provider_id: 'ollama',
  model_ref: `model:ollama:${name}`,
  label: `${name} - Ollama Local`,
  available: true,
  billing: 'local',
  ...extra,
});
const shelf: ModelChoice[] = [
  models[0],
  local('gemma3:4b'),
  local('llama3.1:8b', { favourite_rank: 2 }),
  local('mistral:7b'),
  local('qwen3:8b', { favourite_rank: 1 }),
];
const names = (list: HTMLElement) =>
  within(list)
    .getAllByRole('option')
    .map((option) => option.textContent ?? '');
const groupNames = (list: HTMLElement) =>
  within(list)
    .getAllByRole('group')
    .map(
      (group) =>
        document.getElementById(group.getAttribute('aria-labelledby')!)
          ?.textContent,
    );

it('lists your models first in their order, then the providers', async () => {
  show(
    'model:codex:gpt-5.6-sol',
    { state: 'ready' },
    {
      open: true,
      models: shelf,
    },
  );
  const list = await screen.findByRole('listbox', { name: 'Models' });
  expect(groupNames(list)[0]).toBe('Your models');
  const rows = names(list);
  expect(rows[0]).toMatch(/^qwen3:8b/);
  expect(rows[1]).toMatch(/^llama3\.1:8b/);
  // Each starred model appears once; the others stay in their provider group.
  expect(rows.filter((row) => row.startsWith('qwen3:8b'))).toHaveLength(1);
  expect(rows.some((row) => row.startsWith('gemma3:4b'))).toBe(true);
  expect(rows.some((row) => row.startsWith('More models'))).toBe(false);
});

it('stars or unstars a model from its row, by pointer or keyboard', async () => {
  const onVisibility = vi.fn();
  show(
    'model:codex:gpt-5.6-sol',
    { state: 'ready' },
    {
      open: true,
      models: shelf,
      onVisibility,
    },
  );
  // A pointer's way in (hidden from assistive tech, which uses Shift+F10).
  const star = await screen.findByRole('button', {
    name: 'Add mistral:7b to your models',
    hidden: true,
  });
  await act(async () => fireEvent.click(star));
  expect(onVisibility).toHaveBeenCalledWith(
    expect.objectContaining({ model_ref: 'model:ollama:mistral:7b' }),
    'favourite',
  );

  // Keyboard: highlight a starred model, then Shift+F10 unstars it.
  const search = screen.getByRole('combobox', { name: 'Search models' });
  fireEvent.change(search, { target: { value: 'llama3' } });
  await act(async () => {
    fireEvent.keyDown(search, { key: 'F10', shiftKey: true });
  });
  expect(onVisibility).toHaveBeenLastCalledWith(
    expect.objectContaining({ model_ref: 'model:ollama:llama3.1:8b' }),
    'unfavourite',
  );
});

it('has no row star where a list only chooses', async () => {
  show(
    'model:codex:gpt-5.6-sol',
    { state: 'ready' },
    {
      open: true,
      models: shelf,
    },
  );
  await screen.findByRole('listbox', { name: 'Models' });
  expect(
    screen.queryByRole('button', { name: /your models$/, hidden: true }),
  ).toBeNull();
});

it('offers a ready personal model with a Personal tag; it can be starred like any model (Learning, D11)', async () => {
  const onVisibility = vi.fn();
  const personal = local('row-bot-personal-v1:ab12cd34', {
    label: 'Personal model v1 - Ollama Local',
    personal: true,
  });
  show(
    'model:codex:gpt-5.6-sol',
    { state: 'ready' },
    { open: true, models: [...shelf, personal], onVisibility },
  );
  const list = await screen.findByRole('listbox', { name: 'Models' });
  const row = within(list).getByRole('option', { name: /^Personal model v1/ });
  expect(row).toHaveTextContent('Personal');
  expect(row).not.toHaveTextContent('row-bot-personal');
  await act(async () =>
    fireEvent.click(
      await screen.findByRole('button', {
        name: 'Add Personal model v1 to your models',
        hidden: true,
      }),
    ),
  );
  expect(onVisibility).toHaveBeenCalledWith(
    expect.objectContaining({ model_ref: personal.model_ref }),
    'favourite',
  );
});
