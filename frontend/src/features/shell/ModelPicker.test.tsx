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
  local('gemma3:4b', { hidden: true }),
  local('llama3.1:8b', { favourite_rank: 2 }),
  local('mistral:7b'),
  local('phi4:14b', { hidden: true }),
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

it('lists favourites first in their order and hidden models under More models', async () => {
  show(
    'model:codex:gpt-5.6-sol',
    { state: 'ready' },
    {
      open: true,
      models: shelf,
    },
  );
  const list = await screen.findByRole('listbox', { name: 'Models' });
  expect(groupNames(list)[0]).toBe('Favourites');
  const rows = names(list);
  expect(rows[0]).toMatch(/^qwen3:8b/);
  expect(rows[1]).toMatch(/^llama3\.1:8b/);
  // Each favourite appears once; the hidden ones wait behind one row.
  expect(rows.filter((row) => row.startsWith('qwen3:8b'))).toHaveLength(1);
  expect(rows.some((row) => row.startsWith('gemma3:4b'))).toBe(false);
  expect(rows.at(-1)).toBe('More models (2)');

  await act(async () =>
    fireEvent.click(screen.getByRole('option', { name: 'More models (2)' })),
  );
  expect(groupNames(list).at(-1)).toBe('More models');
  expect(
    names(list)
      .slice(-2)
      .map((row) => row.split('Ollama')[0]),
  ).toEqual(['gemma3:4b', 'phi4:14b']);
});

it('keeps a hidden model that is in use in view, still chosen', async () => {
  show(
    'model:ollama:gemma3:4b',
    { state: 'ready', local: true },
    {
      open: true,
      models: shelf,
    },
  );
  expect(pill()).toHaveTextContent('gemma3:4b');
  const list = await screen.findByRole('listbox', { name: 'Models' });
  const row = within(list).getByRole('option', { name: /^gemma3:4b/ });
  expect(row).toHaveTextContent('Hidden');
  expect(within(row).getByRole('img', { name: 'Current' })).toBeVisible();
  // Only the other hidden model waits under More models.
  expect(
    within(list).getByRole('option', { name: 'More models (1)' }),
  ).toBeVisible();
});

it('a search finds hidden models without opening More models first', async () => {
  show(
    'model:codex:gpt-5.6-sol',
    { state: 'ready' },
    {
      open: true,
      models: shelf,
    },
  );
  fireEvent.change(
    await screen.findByRole('combobox', { name: 'Search models' }),
    {
      target: { value: 'phi' },
    },
  );
  const list = screen.getByRole('listbox', { name: 'Models' });
  expect(groupNames(list)).toEqual(['More models']);
  expect(within(list).getByRole('option', { name: /^phi4:14b/ })).toBeVisible();
});

it('favourites or hides a model from its row menu, by pointer or keyboard', async () => {
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
  const menu = await screen.findByRole('button', {
    name: 'More for mistral:7b',
    hidden: true,
  });
  await act(async () => {
    fireEvent.pointerDown(menu, { button: 0, pointerType: 'mouse' });
  });
  await act(async () =>
    fireEvent.click(
      await screen.findByRole('menuitem', { name: /Add to favourites/ }),
    ),
  );
  expect(onVisibility).toHaveBeenCalledWith(
    expect.objectContaining({ model_ref: 'model:ollama:mistral:7b' }),
    'favourite',
  );

  // Keyboard: highlight a favourite, then Shift+F10 opens its menu.
  const search = screen.getByRole('combobox', { name: 'Search models' });
  fireEvent.change(search, { target: { value: 'llama3' } });
  await act(async () => {
    fireEvent.keyDown(search, { key: 'F10', shiftKey: true });
  });
  expect(
    await screen.findByRole('menuitem', { name: /Remove from favourites/ }),
  ).toBeVisible();
  await act(async () =>
    fireEvent.click(screen.getByRole('menuitem', { name: /^Hide/ })),
  );
  expect(onVisibility).toHaveBeenLastCalledWith(
    expect.objectContaining({ model_ref: 'model:ollama:llama3.1:8b' }),
    'hide',
  );
  // Said once in the list, not as a toast over it; Undo puts it back as it was.
  const note = screen.getByText('llama3.1:8b is under More models.');
  await act(async () =>
    fireEvent.click(
      within(note.parentElement!).getByRole('button', { name: 'Undo' }),
    ),
  );
  expect(onVisibility).toHaveBeenLastCalledWith(
    expect.objectContaining({ model_ref: 'model:ollama:llama3.1:8b' }),
    'favourite',
  );
  expect(screen.queryByText(/is under More models/)).toBeNull();
});

it('has no row menu where a list only chooses', async () => {
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
    screen.queryByRole('button', { name: /^More for /, hidden: true }),
  ).toBeNull();
});
