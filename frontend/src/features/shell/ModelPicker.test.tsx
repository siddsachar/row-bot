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
