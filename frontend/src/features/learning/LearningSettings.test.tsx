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
  GPU_16GB,
  installedModel,
  learningSnapshot,
  NO_GPU,
  SMALL_GPU,
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
    await within(sheet).findByText('NVIDIA GeForce RTX 5060 Ti (16 GB)'),
  ).toBeVisible();
  expect(
    within(sheet).getByText('Can train models up to about 9B on your tasks.'),
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
  ).toHaveDisplayValue('Qwen3.5 9B · Apache 2.0 · 19.3 GB download');
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
    name: 'Download the training engine and Qwen3.5 9B?',
  });
  expect(io.run).toHaveBeenCalledWith('turn_on', {
    setup: {
      sources: { my_chats: true, workflows_and_goals: true, past_chats: true },
      apply_mode: 'ask_first',
      monthly_token_limit: 2_000_000,
      train_in_quiet_hours: true,
      train_where: 'this_computer',
      starting_model: 'qwen3.5-9b',
    },
  });
  expect(ask).toHaveAccessibleDescription(
    'The training engine (2.2 GB) and Qwen3.5 9B (19.3 GB). 512 GB free on this computer.',
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
  await within(sheet).findByText('NVIDIA GeForce RTX 5060 Ti (16 GB)');
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

it('with no NVIDIA card the sheet says lessons still work and offers no Hugging Face (O131)', async () => {
  const io = fakeIO(learningSnapshot(), { hardware: NO_GPU });
  show(io);

  fireEvent.click(screen.getByRole('button', { name: 'Turn on Learning' }));
  const sheet = await screen.findByRole('dialog', { name: 'Turn on Learning' });
  expect(
    await within(sheet).findByText(
      'A personal model runs on an NVIDIA graphics card with 8 GB or more, and this computer has none.',
    ),
  ).toBeVisible();
  expect(
    within(sheet).getByText(
      'Learning still improves Row-Bot with lessons and ways of working.',
    ),
  ).toBeVisible();
  expect(
    within(sheet).queryByRole('button', { name: 'Use Hugging Face' }),
  ).toBeNull();
});

it('a card too small to train says so plainly and offers Hugging Face (O124)', async () => {
  const io = fakeIO(learningSnapshot(), {
    hardware: SMALL_GPU,
    after: (action) => (action === 'turn_on' ? { state: 'on' } : {}),
  });
  show(io);

  fireEvent.click(screen.getByRole('button', { name: 'Turn on Learning' }));
  const sheet = await screen.findByRole('dialog', { name: 'Turn on Learning' });
  expect(
    await within(sheet).findByText(
      'You can train one on Hugging Face instead (your own account, priced before each run), or skip it: Learning still improves Row-Bot with lessons and ways of working.',
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
      data: { ...learningSnapshot().data, exists: true, bytes: 12_000_000 },
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
  const limit = screen.getByRole('combobox', { name: 'Monthly limit' });
  expect(limit).toHaveDisplayValue('2M tokens');
  // A large subscription can give Learning up to 50M tokens a month.
  expect(
    within(limit)
      .getAllByRole('option')
      .map((option) => option.textContent),
  ).toEqual([
    '500K tokens',
    '1M tokens',
    '2M tokens',
    '5M tokens',
    '10M tokens',
    '20M tokens',
    '50M tokens',
    'No model calls',
  ]);

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
    .mockResolvedValueOnce(GPU_16GB);
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
    await within(sheet).findByText('NVIDIA GeForce RTX 5060 Ti (16 GB)'),
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
    name: 'Delete Qwen3.5 9B?',
  });
  expect(remove2).toHaveTextContent('It frees 4.6 GB.');
  fireEvent.click(within(remove2).getByRole('button', { name: 'Delete' }));
  await act(async () => undefined);
  expect(io.run).toHaveBeenCalledWith('delete_starting_model');
});

it('an NVIDIA driver older than 570 says to update it, in the setup sheet', async () => {
  const io = fakeIO(learningSnapshot(), {
    hardware: { ...GPU_16GB, state: 'old_driver' },
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

it('a 32 GB card offers its own tier’s model first, the smaller tiers’ as choices (L2x)', async () => {
  const GPU_32GB = {
    ...GPU_16GB,
    gpu: {
      name: 'NVIDIA GeForce RTX 5090',
      memory_gb: 32,
      label: 'NVIDIA GeForce RTX 5090 (32 GB)',
    },
    tier: {
      id: '32gb',
      label: '32 GB',
      certified: true,
      summary: 'Can train models up to about 9B on your tasks.',
    },
    starting_models: [
      {
        id: 'qwen3.5-9b',
        name: 'Qwen3.5 9B',
        licence: 'Apache 2.0',
        download_bytes: 19_331_993_000,
      },
      {
        id: 'test-small',
        name: 'A small model',
        licence: 'Apache 2.0',
        download_bytes: 4_570_000_000,
      },
    ],
  } satisfies typeof GPU_16GB;
  const io = fakeIO(learningSnapshot(), {
    hardware: GPU_32GB,
    after: (action) => (action === 'turn_on' ? { state: 'on' } : {}),
  });
  show(io);

  fireEvent.click(screen.getByRole('button', { name: 'Turn on Learning' }));
  const sheet = await screen.findByRole('dialog', { name: 'Turn on Learning' });
  expect(
    await within(sheet).findByText(
      'Can train models up to about 9B on your tasks.',
    ),
  ).toBeVisible();
  const starting = within(sheet).getByRole('combobox', {
    name: 'Starting model',
  });
  expect(starting).toHaveDisplayValue(
    /^Qwen3\.5 9B · Apache 2\.0 · 19\.3\sGB download$/,
  );
  expect(
    within(starting).getByRole('option', {
      name: /^A small model · Apache 2\.0 · 4\.6\sGB download$/,
    }),
  ).toBeInTheDocument();
  fireEvent.change(starting, { target: { value: 'test-small' } });
  fireEvent.click(
    within(sheet).getByRole('button', { name: 'Turn on Learning' }),
  );
  await act(async () => undefined);
  expect(io.run).toHaveBeenCalledWith(
    'turn_on',
    expect.objectContaining({
      setup: expect.objectContaining({ starting_model: 'test-small' }),
    }),
  );
});

it('the setup sheet says what training needs of the computer’s memory, and what to do when it can’t hold a tier (O105)', async () => {
  const GPU_32GB = {
    ...GPU_16GB,
    gpu: {
      name: 'NVIDIA GeForce RTX 5090',
      memory_gb: 32,
      label: 'NVIDIA GeForce RTX 5090 (32 GB)',
    },
    tier: {
      id: '16gb',
      label: '16 GB',
      certified: true,
      summary: 'Can train models up to about 9B on your tasks.',
      system_memory_gb: 30,
    },
    memory_short: [
      {
        id: '24gb',
        label: '24 GB',
        certified: true,
        summary: 'Can train models up to about 9B on your tasks.',
        system_memory_gb: 41,
      },
    ],
    memory: { limit_gb: 36, free_gb: 20, system: 'windows' as const },
  } satisfies typeof GPU_16GB;
  const io = fakeIO(learningSnapshot(), { hardware: GPU_32GB });
  show(io);

  fireEvent.click(screen.getByRole('button', { name: 'Turn on Learning' }));
  const sheet = await screen.findByRole('dialog', { name: 'Turn on Learning' });
  expect(
    await within(sheet).findByText(
      'Training needs about 30 GB of this computer’s memory free (RAM and virtual memory).',
    ),
  ).toBeVisible();
  expect(
    within(sheet).getByText(
      /^Larger models need about 41 GB of RAM and virtual memory to train, and this computer has 36 GB\. Raise Windows’ virtual memory/,
    ),
  ).toBeVisible();
  fireEvent.click(within(sheet).getByRole('button', { name: 'Check again' }));
  await act(async () => undefined);
  expect(io.hardware).toHaveBeenLastCalledWith(true);
});

it('a computer whose memory can’t hold training says so up front, with what to do (O105)', async () => {
  const io = fakeIO(learningSnapshot(), {
    hardware: {
      ...GPU_16GB,
      state: 'low_memory',
      tier: { ...GPU_16GB.tier!, system_memory_gb: 15 },
      starting_models: [],
      memory: { limit_gb: 12, free_gb: 5, system: 'linux' },
    },
  });
  show(io);

  fireEvent.click(screen.getByRole('button', { name: 'Turn on Learning' }));
  const sheet = await screen.findByRole('dialog', { name: 'Turn on Learning' });
  expect(
    await within(sheet).findByText(
      'This computer’s memory can’t hold training yet: it needs about 15 GB of RAM and virtual memory, and this computer has 12 GB.',
    ),
  ).toBeVisible();
  expect(
    within(sheet).getByText('Add swap space or RAM, then check again.'),
  ).toBeVisible();
  expect(
    within(sheet).queryByRole('combobox', { name: 'Starting model' }),
  ).toBeNull();
  expect(
    within(sheet).getByRole('button', { name: 'Check again' }),
  ).toBeVisible();
});

it('setup says why it recommends a smaller model when the graphics card has little free (O112)', async () => {
  const io = fakeIO(learningSnapshot(), {
    hardware: {
      ...GPU_16GB,
      gpu: {
        name: 'NVIDIA GeForce RTX 4080',
        memory_gb: 16,
        label: 'NVIDIA GeForce RTX 4080 (16 GB)',
      },
      tier: {
        id: '16gb',
        label: '16 GB',
        certified: true,
        summary: 'Can train models up to about 9B on your tasks.',
        card_need_gb: 15.3,
      },
      recommended: {
        id: 'small',
        label: 'small',
        certified: true,
        summary: '',
        card_need_gb: 8,
      },
      card_free_gb: 9.6,
      starting_models: [
        {
          id: 'test-small',
          name: 'A small model',
          licence: 'Apache 2.0',
          download_bytes: 4_570_000_000,
        },
        GPU_16GB.starting_models[0],
      ],
    },
  });
  show(io);

  fireEvent.click(screen.getByRole('button', { name: 'Turn on Learning' }));
  const sheet = await screen.findByRole('dialog', { name: 'Turn on Learning' });
  expect(
    await within(sheet).findByText(
      'Your graphics card has about 9 GB free now (your display and other apps use the rest), so Row-Bot recommends A small model. Larger models need about 16 GB free on it.',
    ),
  ).toBeVisible();
  expect(
    within(sheet).getByRole('combobox', { name: 'Starting model' }),
  ).toHaveDisplayValue(/^A small model/);
});

it('the learning data line says how much of it is browser and computer pictures', () => {
  const data = {
    ...learningSnapshot().data,
    exists: true,
    bytes: 1_240_000_000,
    picture_bytes: 830_000_000,
  };
  show(fakeIO(learningSnapshot({ state: 'on', data })));

  expect(
    screen.getByText(
      '1.2 GB, including 830 MB of browser and computer pictures (up to 10 GB; the oldest go first). Turning Learning off keeps it.',
    ),
  ).toBeVisible();
});
