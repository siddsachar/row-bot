import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import type { SettingsSnapshot } from '../../api/types';
import { OverlayProvider } from '../../ui/overlays';
import {
  SettingsDraftOwner,
  type SettingsMutationIO,
  type SettingsPage,
} from '../settings/SettingsSnapshotPanels';
import LearningSettings from './LearningSettings';
import {
  downloadState,
  fakeIO,
  GPU_8GB,
  installedModel,
  learningSnapshot,
  NO_GPU,
  personalModel,
} from './test-data';
import type { LearningIO } from './learning-state';

function mutation(page: SettingsPage): SettingsMutationIO {
  return {
    revision: 'b'.repeat(64),
    page,
    review: vi.fn(),
    execute: vi.fn(),
    receipt: vi.fn(),
    drafts: new SettingsDraftOwner(),
    onSnapshot: vi.fn(),
  };
}

const preferences = {
  availability: 'available',
  identity: {
    name: 'Row-Bot',
    personality: '',
    personality_max_length: 200,
    self_improvement_enabled: true,
  },
  window_mode: 'ask',
  dream_cycle: {
    enabled: true,
    window_start: 1,
    window_end: 5,
    last_run: null,
    last_summary: '',
  },
  updates: {
    current_version: '5.4.0',
    channel: 'stable',
    last_check: null,
    last_success: null,
    runtime_state: 'cached',
  },
  migration: { available: false, sources: [] },
} satisfies SettingsSnapshot['preferences'];

const learningSettings = {
  availability: 'available',
  enabled: false,
  paused: false,
  sources: { my_chats: true, workflows_and_goals: true, past_chats: true },
  apply_mode: 'ask_first',
  monthly_token_limit: 2_000_000,
  train_in_quiet_hours: true,
  train_where: 'this_computer',
  has_data: false,
} satisfies SettingsSnapshot['learning'];

function show(io: LearningIO, onOpenLearning = vi.fn()) {
  const view = render(
    <MemoryRouter>
      <OverlayProvider>
        <LearningSettings
          io={io}
          preferences={preferences}
          preferencesMutation={mutation('preferences')}
          learning={{
            ...learningSettings,
            enabled: io.snapshot?.state !== 'off',
          }}
          learningMutation={mutation('learning')}
          onSettingsChanged={vi.fn()}
          onOpenLearning={onOpenLearning}
        />
      </OverlayProvider>
    </MemoryRouter>,
  );
  return { ...view, onOpenLearning };
}

it('off: one card with Turn on Learning, How it works and Create skills while chatting', () => {
  show(fakeIO(learningSnapshot()));

  expect(screen.getByText('Learning off')).toBeVisible();
  expect(
    screen.getByText(/Row-Bot learns from your work, tests ideas/),
  ).toBeVisible();
  expect(
    screen.getByRole('button', { name: 'Turn on Learning' }),
  ).toBeVisible();
  expect(screen.getByText('How it works')).toBeVisible();
  // D29: the same setting as Preferences' "Learn new skills", now here.
  expect(
    screen.getByRole('switch', { name: 'Create skills while chatting' }),
  ).toBeChecked();
  // Nothing about data that doesn't exist.
  expect(screen.queryByText(/Delete learning data/)).toBeNull();
});

it('the setup sheet fills in every default, turns on with one tap and then asks before downloading', async () => {
  const io = fakeIO(learningSnapshot(), {
    after: (action) =>
      action === 'turn_on'
        ? { state: 'on' }
        : {
            personal_model: {
              ...learningSnapshot().personal_model,
              download: 'waiting',
            },
          },
  });
  const { onOpenLearning } = show(io);

  fireEvent.click(screen.getByRole('button', { name: 'Turn on Learning' }));
  const sheet = await screen.findByRole('dialog', { name: 'Turn on Learning' });
  expect(
    await within(sheet).findByText('NVIDIA GeForce RTX 5070 Laptop (8 GB)'),
  ).toBeVisible();
  expect(
    within(sheet).getByText(
      'Can train small models (up to about 2B) on your tasks.',
    ),
  ).toBeVisible();
  for (const name of [
    /^My chats/,
    'Workflows and goals',
    'Also learn from the last 30 days of chats',
    /^Train a personal model during quiet hours/,
  ])
    expect(within(sheet).getByRole('checkbox', { name })).toBeChecked();
  expect(
    within(sheet).getByText(
      'Chats with other people are never used. Switch any chat off from its menu.',
    ),
  ).toBeVisible();
  expect(
    within(sheet).getByRole('radio', { name: 'Ask me first' }),
  ).toBeChecked();
  expect(
    within(sheet).getByRole('combobox', { name: 'Starting model' }),
  ).toHaveDisplayValue('Qwen3.5 2B · Apache 2.0 · 4.6 GB download');
  expect(
    within(sheet).getByRole('combobox', { name: 'Monthly limit' }),
  ).toHaveDisplayValue('2M tokens');
  expect(
    within(sheet).getByText(/GPT-5\.6 Sol \(your default model\)/),
  ).toBeVisible();
  expect(
    within(sheet).getByText(
      /Training on answers from OpenAI, Anthropic or Google models may be limited by their terms/,
    ),
  ).toBeVisible();

  fireEvent.click(
    within(sheet).getByRole('button', { name: 'Turn on Learning' }),
  );

  const ask = await screen.findByRole('dialog', {
    name: 'Download the training engine and Qwen3.5 2B?',
  });
  expect(io.run).toHaveBeenCalledWith('turn_on', {
    setup: {
      sources: { my_chats: true, workflows_and_goals: true, past_chats: true },
      apply_mode: 'ask_first',
      monthly_token_limit: 2_000_000,
      train_in_quiet_hours: true,
      train_where: 'this_computer',
      starting_model: 'qwen3.5-2b',
    },
  });
  expect(ask).toHaveAccessibleDescription(
    'The training engine (2.2 GB) and Qwen3.5 2B (4.6 GB). 512 GB free on this computer.',
  );
  fireEvent.click(within(ask).getByRole('button', { name: 'Download' }));
  await act(async () => undefined);
  expect(io.run).toHaveBeenLastCalledWith('answer_download', {
    download: 'download',
  });
  expect(await screen.findByText('Learning is on.')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Open Learning' }));
  expect(onOpenLearning).toHaveBeenCalledOnce();
});

it('Not now records the answer and nothing downloads', async () => {
  const io = fakeIO(learningSnapshot(), {
    after: (action) => (action === 'turn_on' ? { state: 'on' } : {}),
  });
  show(io);
  fireEvent.click(screen.getByRole('button', { name: 'Turn on Learning' }));
  const sheet = await screen.findByRole('dialog', { name: 'Turn on Learning' });
  await within(sheet).findByText('NVIDIA GeForce RTX 5070 Laptop (8 GB)');
  fireEvent.click(
    within(sheet).getByRole('button', { name: 'Turn on Learning' }),
  );
  const ask = await screen.findByRole('dialog', {
    name: /^Download the training engine/,
  });
  fireEvent.click(within(ask).getByRole('button', { name: 'Not now' }));
  await act(async () => undefined);
  expect(io.run).toHaveBeenLastCalledWith('answer_download', {
    download: 'not_now',
  });
});

it('without a suitable GPU the sheet says so plainly and offers Hugging Face', async () => {
  const io = fakeIO(learningSnapshot(), {
    hardware: NO_GPU,
    after: (action) => (action === 'turn_on' ? { state: 'on' } : {}),
  });
  show(io);

  fireEvent.click(screen.getByRole('button', { name: 'Turn on Learning' }));
  const sheet = await screen.findByRole('dialog', { name: 'Turn on Learning' });
  expect(
    await within(sheet).findByText(
      'This computer can’t train a model. You can train on Hugging Face (your account, priced before each run) or skip this for now.',
    ),
  ).toBeVisible();
  fireEvent.click(
    within(sheet).getByRole('button', { name: 'Use Hugging Face' }),
  );
  expect(
    within(sheet).getByText(/will train on Hugging Face, in your own account/),
  ).toBeVisible();
  fireEvent.click(
    within(sheet).getByRole('button', { name: 'Turn on Learning' }),
  );

  expect(await screen.findByText('Learning is on.')).toBeVisible();
  expect(io.run).toHaveBeenCalledWith('turn_on', {
    setup: expect.objectContaining({ train_where: 'hugging_face' }),
  });
  // Nothing to download on this computer: no download ask.
  expect(screen.queryByRole('dialog', { name: /^Download/ })).toBeNull();
});

it('on: its settings, and turning Learning off asks with what stays', async () => {
  const io = fakeIO(
    learningSnapshot({
      state: 'on',
      data: { exists: true, bytes: 12_000_000 },
      spending: {
        ...learningSnapshot().spending,
        used_tokens: 240_000,
      },
    }),
    { after: (action) => (action === 'turn_off' ? { state: 'off' } : {}) },
  );
  show(io);

  expect(screen.getByText('Learning on')).toBeVisible();
  expect(screen.getByText('240K of 2M tokens this month')).toBeVisible();
  for (const heading of [
    'Learn from',
    'Changes and spending',
    'Personal model',
    'While chatting',
    'Learning data',
  ])
    expect(screen.getByRole('heading', { name: heading })).toBeVisible();
  expect(
    screen.getByText('12 MB. Turning Learning off keeps it.'),
  ).toBeVisible();
  expect(
    screen.getByRole('button', { name: 'Delete learning data…' }),
  ).toBeVisible();
  expect(screen.getAllByText('Not downloaded')).toHaveLength(2);
  expect(
    screen.getByRole('combobox', { name: 'Monthly limit' }),
  ).toHaveDisplayValue('2M tokens');

  fireEvent.click(screen.getByRole('switch', { name: 'Learning' }));
  const confirm = await screen.findByRole('dialog', {
    name: 'Turn off Learning?',
  });
  expect(confirm).toHaveAccessibleDescription(
    'Row-Bot stops recording, testing and training. Changes and models in use stay in use (undo them in Learning). Your learning data is kept until you delete it.',
  );
  fireEvent.click(within(confirm).getByRole('button', { name: 'Turn off' }));
  await act(async () => undefined);
  expect(io.run).toHaveBeenCalledWith('turn_off');
});

it('an earlier Download answer reads as Ready to download, and a pairing-only channel is explained', () => {
  show(
    fakeIO(
      learningSnapshot({
        state: 'paused',
        personal_model: personalModel({
          download: 'waiting',
          download_state: downloadState({ state: 'ready' }),
        }),
        owner_unset_channels: [
          {
            channel: 'telegram',
            name: 'Telegram',
            note: 'Telegram chats are used once your Telegram user ID is set in Settings › Apps › Telegram.',
          },
        ],
      }),
    ),
  );

  expect(screen.getByText('Learning paused')).toBeVisible();
  expect(
    screen.getByText('Paused: nothing new is recorded, tested or trained'),
  ).toBeVisible();
  expect(screen.getByRole('button', { name: 'Resume' })).toBeVisible();
  expect(screen.getByText('Ready to download')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Download' })).toBeVisible();
  expect(screen.getAllByText('Not downloaded')).toHaveLength(2);
  expect(
    screen.getByText(
      'Telegram chats are used once your Telegram user ID is set in Settings › Apps › Telegram.',
    ),
  ).toBeVisible();
});

it('the hardware check that fails offers Check again', async () => {
  const io = fakeIO(learningSnapshot());
  io.hardware = vi
    .fn()
    .mockResolvedValueOnce({ ...NO_GPU, state: 'unknown' })
    .mockResolvedValueOnce(GPU_8GB);
  show(io);
  fireEvent.click(screen.getByRole('button', { name: 'Turn on Learning' }));
  const sheet = await screen.findByRole('dialog', { name: 'Turn on Learning' });
  expect(
    await within(sheet).findByText(
      'Row-Bot couldn’t check this computer’s graphics card.',
    ),
  ).toBeVisible();
  fireEvent.click(within(sheet).getByRole('button', { name: 'Check again' }));
  expect(
    await within(sheet).findByText('NVIDIA GeForce RTX 5070 Laptop (8 GB)'),
  ).toBeVisible();
  expect(io.hardware).toHaveBeenLastCalledWith(true);
});

it('the engine and starting model on disk show their sizes, and Remove and Delete ask first', async () => {
  const io = fakeIO(
    learningSnapshot({ state: 'on', personal_model: installedModel() }),
  );
  show(io);

  expect(screen.getByText('Installed, 4 GB')).toBeVisible();
  expect(screen.getByText('On this computer, 4.6 GB')).toBeVisible();

  fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
  const remove = await screen.findByRole('alertdialog', {
    name: 'Remove the training engine?',
  });
  expect(remove).toHaveTextContent('It frees 4 GB.');
  expect(io.run).not.toHaveBeenCalled();
  fireEvent.click(
    within(remove).getByRole('button', { name: 'Remove engine' }),
  );
  await act(async () => undefined);
  expect(io.run).toHaveBeenCalledWith('remove_engine');

  fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
  const remove2 = await screen.findByRole('alertdialog', {
    name: 'Delete Qwen3.5 2B?',
  });
  expect(remove2).toHaveTextContent('It frees 4.6 GB.');
  fireEvent.click(within(remove2).getByRole('button', { name: 'Delete' }));
  await act(async () => undefined);
  expect(io.run).toHaveBeenCalledWith('delete_starting_model');
});

it('an NVIDIA driver older than 570 says to update it, in the setup sheet', async () => {
  const io = fakeIO(learningSnapshot(), {
    hardware: { ...GPU_8GB, state: 'old_driver' },
  });
  show(io);

  fireEvent.click(screen.getByRole('button', { name: 'Turn on Learning' }));
  const sheet = await screen.findByRole('dialog', { name: 'Turn on Learning' });
  expect(
    await within(sheet).findByText('Update your NVIDIA driver to train here.'),
  ).toBeVisible();
  expect(
    within(sheet).getByRole('button', { name: 'Check again' }),
  ).toBeVisible();
});
