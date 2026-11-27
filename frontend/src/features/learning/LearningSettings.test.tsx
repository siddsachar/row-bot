import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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
  experimental: false,
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

it('the setup sheet fills in the saved choices, turns on with one tap and never asks to download (O195)', async () => {
  const io = fakeIO(learningSnapshot(), {
    after: (action) => (action === 'turn_on' ? { state: 'on' } : {}),
  });
  const { onOpenLearning } = show(io);

  fireEvent.click(screen.getByRole('button', { name: 'Turn on Learning' }));
  const sheet = await screen.findByRole('dialog', { name: 'Turn on Learning' });
  for (const name of [
    /^My chats/,
    'Workflows and goals',
    'Also learn from the last 30 days of chats',
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
    within(sheet).getByRole('combobox', { name: 'Monthly limit' }),
  ).toHaveDisplayValue('2M tokens');
  expect(
    within(sheet).getByText(/GPT-5\.6 Sol \(your default model\)/),
  ).toBeVisible();
  expect(
    within(sheet).getByText(
      'A personal model is set up on its own, under Personal model. Nothing downloads when you turn Learning on.',
    ),
  ).toBeVisible();
  // Nothing about the personal model to choose here, so the computer isn't read.
  expect(
    within(sheet).queryByRole('combobox', { name: 'Starting model' }),
  ).toBeNull();
  expect(io.hardware).not.toHaveBeenCalled();

  fireEvent.click(
    within(sheet).getByRole('button', { name: 'Turn on Learning' }),
  );

  expect(await screen.findByText('Learning is on.')).toBeVisible();
  expect(io.run).toHaveBeenCalledWith('turn_on', {
    setup: {
      sources: { my_chats: true, workflows_and_goals: true, past_chats: true },
      apply_mode: 'ask_first',
      monthly_token_limit: 2_000_000,
    },
  });
  expect(screen.queryByRole('dialog', { name: /^Download/ })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Open Learning' }));
  expect(onOpenLearning).toHaveBeenCalledOnce();
});
/** Settings › Learning, on, with a mutation whose saves come back with `saved`'s Learning settings. */
function showSaving(
  io: LearningIO,
  learning: SettingsSnapshot['learning'],
  saved: Partial<SettingsSnapshot['learning']>,
) {
  const learningMutation = {
    ...savingMutation(),
    execute: vi.fn<SettingsMutationIO['execute']>(
      async (_request, _review, commandId) => ({
        command_id: commandId,
        status: 'completed',
        settings_revision: 'c'.repeat(64),
        snapshot: {
          revision: 'c'.repeat(64),
          learning: { ...learning, ...saved },
        } as SettingsSnapshot,
      }),
    ),
  };
  render(
    <MemoryRouter>
      <OverlayProvider>
        <LearningSettings
          io={io}
          preferences={preferences}
          preferencesMutation={mutation('preferences')}
          learning={learning}
          learningMutation={learningMutation}
          onSettingsChanged={vi.fn()}
          onOpenLearning={vi.fn()}
        />
      </OverlayProvider>
    </MemoryRouter>,
  );
  return learningMutation;
}

it('turning on Train during quiet hours asks before downloading, and Not now records the answer (O195)', async () => {
  const io = fakeIO(
    learningSnapshot({
      state: 'on',
      personal_model: personalModel({ train_in_quiet_hours: false }),
    }),
  );
  const learning = {
    ...learningSettings,
    enabled: true,
    train_in_quiet_hours: false,
  };
  const saving = showSaving(io, learning, { train_in_quiet_hours: true });

  fireEvent.click(
    screen.getByRole('switch', { name: 'Train during quiet hours' }),
  );

  const ask = await screen.findByRole('dialog', {
    name: 'Download the training engine and Qwen3.5 9B?',
  });
  expect(saving.review).toHaveBeenLastCalledWith(
    expect.objectContaining({ field: 'train_in_quiet_hours', value: true }),
    expect.anything(),
  );
  expect(ask).toHaveAccessibleDescription(
    /^The training engine \(2\.2\sGB\) and Qwen3\.5 9B \(19\.3\sGB\)\. 512\sGB free on this computer\.$/,
  );
  fireEvent.click(within(ask).getByRole('button', { name: 'Not now' }));
  await act(async () => undefined);
  expect(io.run).toHaveBeenLastCalledWith('answer_download', {
    download: 'not_now',
  });
});

it('a computer that can’t train asks nothing when training is turned on', async () => {
  const io = fakeIO(
    learningSnapshot({
      state: 'on',
      personal_model: personalModel({ train_in_quiet_hours: false }),
    }),
    { hardware: NO_GPU },
  );
  const learning = {
    ...learningSettings,
    enabled: true,
    train_in_quiet_hours: false,
  };
  showSaving(io, learning, { train_in_quiet_hours: true });

  fireEvent.click(
    screen.getByRole('switch', { name: 'Train during quiet hours' }),
  );

  expect(
    await screen.findByText('Train during quiet hours turned on'),
  ).toBeVisible();
  await act(async () => undefined);
  expect(io.hardware).toHaveBeenCalled();
  expect(screen.queryByRole('dialog', { name: /^Download/ })).toBeNull();
});

it('Experimental is its own switch, off, and says what it adds and costs (O195)', async () => {
  const io = fakeIO(learningSnapshot({ state: 'on' }));
  const learning = { ...learningSettings, enabled: true };
  const saving = showSaving(io, learning, { experimental: true });

  expect(screen.getByRole('heading', { name: 'Experimental' })).toBeVisible();
  const toggle = screen.getByRole('switch', {
    name: 'Try experimental learning',
  });
  expect(toggle).not.toBeChecked();
  expect(toggle).toHaveAccessibleDescription(
    'Practice on tasks Row-Bot makes up from its own mistakes, a quick question when it can’t tell what to change, and trials that compare ways of learning. They use more of the monthly limit.',
  );
  // Nothing is kept back for comparing methods while they are off.
  expect(
    screen.getByRole('combobox', { name: 'Monthly limit' }),
  ).toHaveAccessibleDescription(/About 2 tests of a change\. Local models/);

  fireEvent.click(toggle);
  expect(
    await screen.findByText('Try experimental learning turned on'),
  ).toBeVisible();
  expect(saving.review).toHaveBeenLastCalledWith(
    {
      settings_revision: 'b'.repeat(64),
      page: 'learning',
      field: 'experimental',
      value: true,
    },
    expect.anything(),
  );
});

it('with experimental learning on, the limit says a fifth is kept for comparing methods', () => {
  render(
    <MemoryRouter>
      <OverlayProvider>
        <LearningSettings
          io={fakeIO(learningSnapshot({ state: 'on' }))}
          preferences={preferences}
          preferencesMutation={mutation('preferences')}
          learning={{ ...learningSettings, enabled: true, experimental: true }}
          learningMutation={mutation('learning')}
          onSettingsChanged={vi.fn()}
          onOpenLearning={vi.fn()}
        />
      </OverlayProvider>
    </MemoryRouter>,
  );
  expect(
    screen.getByRole('combobox', { name: 'Monthly limit' }),
  ).toHaveAccessibleDescription(
    /About 1 test of a change\. A fifth of it is kept for comparing methods\./,
  );
});
it('the setup sheet turns Learning on with no monthly limit and says what that means', async () => {
  const io = fakeIO(learningSnapshot(), {
    after: (action) => (action === 'turn_on' ? { state: 'on' } : {}),
  });
  show(io);

  fireEvent.click(screen.getByRole('button', { name: 'Turn on Learning' }));
  const sheet = await screen.findByRole('dialog', { name: 'Turn on Learning' });
  fireEvent.change(
    within(sheet).getByRole('combobox', { name: 'Monthly limit' }),
    { target: { value: 'none' } },
  );
  expect(
    within(sheet).getByText(
      "No monthly cap: nothing limits Learning's spending. What it spends still shows each month. Local models don't count.",
    ),
  ).toBeVisible();
  fireEvent.click(
    within(sheet).getByRole('button', { name: 'Turn on Learning' }),
  );

  expect(await screen.findByText('Learning is on.')).toBeVisible();
  expect(io.run).toHaveBeenCalledWith('turn_on', {
    setup: expect.objectContaining({ monthly_token_limit: null }),
  });
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
  // A large subscription can give Learning up to 50M tokens a month, any amount up to 1,000M, or no limit.
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
    'Custom amount…',
    'No limit',
    'No model calls',
  ]);

  fireEvent.click(screen.getByRole('switch', { name: 'Learning' }));
  const confirm = await screen.findByRole('dialog', {
    name: 'Turn off Learning?',
  });
  expect(confirm).toHaveAccessibleDescription(
    'Row-Bot stops recording, testing and training. Changes and models in use stay in use: to take one out, undo it in Learning first, or turn Learning on again later. Your learning data is kept until you delete it.',
  );
  fireEvent.click(within(confirm).getByRole('button', { name: 'Turn off' }));
  await act(async () => undefined);
  expect(io.run).toHaveBeenCalledWith('turn_off');
});

it('a Pause or Resume that takes a while says so, and is not sent twice', async () => {
  const io = fakeIO(learningSnapshot({ state: 'on' }), {
    after: (action) => ({ state: action === 'pause' ? 'paused' : 'on' }),
  });
  // Each command waits until the test lets it finish, as on a large profile.
  const answer = io.run;
  let finish = () => {};
  io.run = vi.fn(
    (...args: Parameters<LearningIO['run']>) =>
      new Promise<Awaited<ReturnType<LearningIO['run']>>>((resolve) => {
        finish = () => resolve(answer(...args));
      }),
  );
  show(io);
  const user = userEvent.setup();
  const more = () => screen.getByRole('button', { name: 'More for Learning' });

  await user.click(more());
  await user.click(
    await screen.findByRole('menuitem', { name: 'Pause Learning' }),
  );
  expect(screen.getByText('Pausing Learning…')).toBeVisible();
  await user.click(more());
  expect(
    await screen.findByRole('menuitem', { name: 'Pause Learning' }),
  ).toHaveAttribute('aria-disabled', 'true');
  await user.keyboard('{Escape}');
  await act(async () => finish());
  expect(screen.getByText('Learning paused')).toBeVisible();

  await user.click(screen.getByRole('button', { name: 'Resume' }));
  expect(screen.getByText('Resuming Learning…')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Resume' })).toBeDisabled();
  await act(async () => finish());
  expect(screen.getByText('Learning on')).toBeVisible();
  expect(io.run).toHaveBeenCalledTimes(2);
});

/** A Learning settings mutation whose reviews and saves complete. */
function savingMutation(): SettingsMutationIO {
  return {
    ...mutation('learning'),
    review: vi.fn<SettingsMutationIO['review']>(async (request) => ({
      schema_version: 1,
      operation: 'settings.update',
      settings_revision: request.settings_revision,
      page: request.page,
      field: request.field,
      value_summary: String(request.value),
      secret: false,
      action_digest: 'd'.repeat(64),
      review_id: 'review-limit',
    })),
    execute: vi.fn<SettingsMutationIO['execute']>(
      async (_request, _review, commandId) => ({
        command_id: commandId,
        status: 'completed',
        settings_revision: 'c'.repeat(64),
        snapshot: { revision: 'c'.repeat(64) } as SettingsSnapshot,
      }),
    ),
  };
}

it('the monthly limit takes a custom amount at once and asks before No limit', async () => {
  const learningMutation = savingMutation();
  render(
    <MemoryRouter>
      <OverlayProvider>
        <LearningSettings
          io={fakeIO(learningSnapshot({ state: 'on' }))}
          preferences={preferences}
          preferencesMutation={mutation('preferences')}
          learning={{ ...learningSettings, enabled: true }}
          learningMutation={learningMutation}
          onSettingsChanged={vi.fn()}
          onOpenLearning={vi.fn()}
        />
      </OverlayProvider>
    </MemoryRouter>,
  );
  const limit = screen.getByRole('combobox', { name: 'Monthly limit' });

  fireEvent.change(limit, { target: { value: 'custom' } });
  const amount = screen.getByRole('textbox', {
    name: 'Custom monthly limit, in millions of tokens',
  });
  fireEvent.change(amount, { target: { value: '75' } });
  fireEvent.keyDown(amount, { key: 'Enter' });
  expect(
    await screen.findByText('Monthly limit set to 75M tokens'),
  ).toBeVisible();
  expect(learningMutation.review).toHaveBeenLastCalledWith(
    {
      settings_revision: 'b'.repeat(64),
      page: 'learning',
      field: 'monthly_token_limit',
      value: 75_000_000,
    },
    expect.anything(),
  );

  fireEvent.change(limit, { target: { value: 'none' } });
  const ask = screen.getByRole('group', { name: 'Confirm Monthly limit' });
  expect(ask).toHaveTextContent(
    "With no limit, nothing caps what Learning spends each month, comparing methods included: it can use as many of your default model's tokens as it needs. What it spends still shows here each month.",
  );
  expect(learningMutation.review).toHaveBeenCalledTimes(1);
  fireEvent.click(within(ask).getByRole('button', { name: 'Change it' }));
  expect(
    await screen.findByText('Learning has no monthly limit now'),
  ).toBeVisible();
  expect(learningMutation.review).toHaveBeenLastCalledWith(
    expect.objectContaining({ field: 'monthly_token_limit', value: null }),
    expect.anything(),
  );
});

it('with no monthly limit, what this month used still shows', () => {
  const io = fakeIO(
    learningSnapshot({
      state: 'on',
      spending: {
        ...learningSnapshot().spending,
        used_tokens: 3_000_000,
        limit_tokens: null,
      },
    }),
  );
  render(
    <MemoryRouter>
      <OverlayProvider>
        <LearningSettings
          io={io}
          preferences={preferences}
          preferencesMutation={mutation('preferences')}
          learning={{
            ...learningSettings,
            enabled: true,
            monthly_token_limit: null,
          }}
          learningMutation={mutation('learning')}
          onSettingsChanged={vi.fn()}
          onOpenLearning={vi.fn()}
        />
      </OverlayProvider>
    </MemoryRouter>,
  );

  expect(screen.getByText('3M tokens this month (no limit)')).toBeVisible();
  expect(
    screen.getByText('3M tokens used this month (no limit)'),
  ).toBeVisible();
  const limit = screen.getByRole('combobox', { name: 'Monthly limit' });
  expect(limit).toHaveDisplayValue('No limit');
  expect(limit).toHaveAccessibleDescription(
    /No monthly cap: nothing limits Learning's spending\. What it spends still shows each month\./,
  );
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
