import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import { OverlayProvider } from '../../ui/overlays';
import LearningHome, { type LearningView } from './LearningHome';
import type { LearningIO } from './learning-state';
import {
  downloadState,
  fakeIO,
  installedModel,
  learningSnapshot,
  NO_GPU,
  personalModel,
  runningRun,
} from './test-data';

function show(io: LearningIO, view: LearningView = 'overview') {
  const onViewChange = vi.fn();
  const onOpenSettings = vi.fn();
  const result = render(
    <MemoryRouter>
      <OverlayProvider>
        <LearningHome
          io={io}
          view={view}
          onViewChange={onViewChange}
          onOpenSettings={onOpenSettings}
        />
      </OverlayProvider>
    </MemoryRouter>,
  );
  return { ...result, onViewChange, onOpenSettings };
}

const on = (overrides = {}) => learningSnapshot({ state: 'on', ...overrides });

it('Overview starts empty, says how to help, and never shows made-up progress', () => {
  show(fakeIO(on()));

  expect(screen.getByRole('heading', { name: 'Learning' })).toBeVisible();
  expect(screen.getByText('On')).toBeVisible();
  // Nothing runs in quiet hours yet, so no "next look" is promised.
  expect(
    screen.getByText(/^Quiet hours .+ · 0 of 2M tokens this month$/),
  ).toBeVisible();
  for (const section of [
    'Needs you',
    'Overnight',
    'What Row-Bot is learning',
    'How Row-Bot learns',
  ])
    expect(screen.getByRole('region', { name: section })).toBeVisible();
  expect(screen.getByText('Nothing learned yet.')).toBeVisible();
  expect(screen.getByText(/The quickest way to help is/).textContent).toContain(
    'Teach on a reply that went wrong.',
  );
  expect(screen.queryByRole('progressbar')).toBeNull();
  expect(
    screen.getByRole('radiogroup', { name: 'Learning view' }),
  ).toBeVisible();
});

it('Overview while paused offers Resume', async () => {
  const io = fakeIO(on({ state: 'paused' }), {
    after: () => ({ state: 'on' }),
  });
  show(io);

  expect(
    screen.getByText(
      'Learning is paused. Nothing new is recorded, tested or trained.',
    ),
  ).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Resume' }));
  await act(async () => undefined);
  expect(io.run).toHaveBeenCalledWith('resume');
});

it('Overview at the monthly limit says when testing resumes and offers Raise limit', () => {
  const { onOpenSettings } = show(
    fakeIO(
      on({
        spending: {
          used_tokens: 2_000_000,
          limit_tokens: 2_000_000,
          resets_on: '2026-11-01',
          model_name: 'GPT-5.6 Sol',
          limit_reached: true,
        },
      }),
    ),
  );

  expect(
    // The reader's date order: "1 Nov" or "Nov 1".
    screen.getByText(
      /^Monthly limit reached\. Testing resumes on (1 Nov|Nov 1)\. /,
    ),
  ).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Raise limit' }));
  expect(onOpenSettings).toHaveBeenCalledWith('monthly_token_limit');
});

it('Overview while reading past chats counts them with a real denominator', () => {
  show(fakeIO(on({ past_chats: { state: 'reading', total: 214, done: 61 } })));

  expect(screen.getByText('Reading 214 past chats · 61 done')).toBeVisible();
  const bar = screen.getByRole('progressbar', {
    name: 'Reading 214 past chats',
  });
  expect(bar).toHaveAttribute('max', '214');
  expect(bar).toHaveAttribute('value', '61');
});

it('the … menu pauses, opens settings and deletes learning data after a confirm', async () => {
  const io = fakeIO(on({ data: { exists: true, bytes: 3_400_000 } }));
  const { onOpenSettings } = show(io);
  const user = userEvent.setup();
  const more = () => screen.getByRole('button', { name: 'More for Learning' });

  await user.click(more());
  await user.click(await screen.findByRole('menuitem', { name: 'Pause' }));
  expect(io.run).toHaveBeenCalledWith('pause');

  await user.click(more());
  await user.click(
    await screen.findByRole('menuitem', { name: 'Learning settings' }),
  );
  expect(onOpenSettings).toHaveBeenCalledWith();

  await user.click(more());
  await user.click(
    await screen.findByRole('menuitem', { name: 'Delete learning data…' }),
  );
  const dialog = await screen.findByRole('alertdialog', {
    name: 'Delete learning data?',
  });
  expect(dialog).toHaveTextContent(
    'everything Learning recorded from your chats, workflows and goals',
  );
  expect(dialog).toHaveTextContent('chats you switched off stay switched off.');
  // No personal-model versions exist yet, so none are listed (never "0").
  expect(dialog.textContent).not.toMatch(/personal model|version/i);
  await user.click(
    within(dialog).getByRole('button', { name: 'Delete learning data' }),
  );
  await act(async () => undefined);
  expect(io.run).toHaveBeenCalledWith('delete_data');
});

it('Changes has its four filters, each with a real empty state', () => {
  show(fakeIO(on()), 'changes');

  const filters = screen.getByRole('radiogroup', { name: 'Show changes' });
  expect(
    within(filters)
      .getAllByRole('radio')
      .map((item) => item.textContent),
  ).toEqual(['Ready', 'Testing', 'In use', 'Not used']);
  expect(screen.getByText(/No changes are ready to review\./)).toBeVisible();
  fireEvent.click(within(filters).getByRole('radio', { name: 'In use' }));
  expect(screen.getByText(/No changes are in use\./)).toBeVisible();
});

it('Lessons lists what Row-Bot saved for itself as Not tested, with search', async () => {
  const io = fakeIO(on());
  io.lessons = vi.fn(async () => ({
    total: 2,
    items: [
      {
        id: 'm1',
        title: 'Browser: login page detection',
        text: 'Wait for the form.',
        origin: 'agent_saved' as const,
        status: 'not_tested' as const,
        updated_at: '',
      },
      {
        id: 'm2',
        title: 'Shell: pip needs --user on macOS',
        text: '',
        origin: 'agent_saved' as const,
        status: 'not_tested' as const,
        updated_at: '',
      },
    ],
  }));
  show(io, 'lessons');

  const list = await screen.findByRole('list', { name: 'Lessons' });
  expect(within(list).getAllByRole('listitem')).toHaveLength(2);
  expect(within(list).getAllByText('Not tested')).toHaveLength(2);
  fireEvent.change(screen.getByRole('searchbox', { name: 'Search lessons' }), {
    target: { value: 'browser' },
  });
  expect(within(list).getAllByRole('listitem')).toHaveLength(1);
  fireEvent.change(screen.getByRole('searchbox', { name: 'Search lessons' }), {
    target: { value: 'nothing like it' },
  });
  expect(screen.getByText('No lessons match “nothing like it”.')).toBeVisible();
});

it('Lessons with none says what will appear there', async () => {
  show(fakeIO(on()), 'lessons');
  expect(
    await screen.findByText(/^No lessons yet\. Lessons Row-Bot saves/),
  ).toBeVisible();
});

it('Personal model: not set up offers the download ask', async () => {
  const io = fakeIO(on());
  show(io, 'model');

  expect(
    screen.getByRole('heading', { name: 'No personal model yet' }),
  ).toBeVisible();
  expect(
    await screen.findByText('NVIDIA GeForce RTX 5070 Laptop (8 GB)'),
  ).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Download…' }));
  expect(
    await screen.findByRole('dialog', {
      name: 'Download the training engine and Qwen3.5 2B?',
    }),
  ).toBeVisible();
});

it('Personal model: an earlier Download answer reads as Ready to download, and one tap downloads (O36)', async () => {
  const io = fakeIO(
    on({
      personal_model: personalModel({
        download: 'waiting',
        download_state: downloadState({ state: 'ready' }),
      }),
    }),
  );
  show(io, 'model');

  expect(screen.getByText('Ready to download')).toBeVisible();
  // The sizes and the free space again, where it comes from, and usage reporting off.
  expect(
    screen.getByText(
      'The training engine (2.2 GB) and Qwen3.5 2B (4.6 GB). 512 GB free on this computer.',
    ),
  ).toBeVisible();
  expect(
    screen.getByText(
      'From PyPI, PyTorch, Hugging Face and GitHub (llama.cpp). The training engine’s own usage reporting is switched off.',
    ),
  ).toBeVisible();
  expect(io.run).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Download' }));
  await act(async () => undefined);
  expect(io.run).toHaveBeenCalledWith('start_download');
});

it('Personal model: without a suitable GPU it says so and points to the settings', async () => {
  const { onOpenSettings } = show(fakeIO(on(), { hardware: NO_GPU }), 'model');
  expect(
    await screen.findByRole('heading', {
      name: 'This computer can’t train a model',
    }),
  ).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Learning settings' }));
  expect(onOpenSettings).toHaveBeenCalledWith('personal-model');
});

it('Personal model: a training run replaces the setup states, and keeps itself fresh while shown', async () => {
  const io = fakeIO(
    on({
      personal_model: installedModel(),
      training: runningRun(),
    }),
  );
  const stop = vi.fn();
  io.watchTraining = vi.fn(() => stop);
  const { unmount } = show(io, 'model');
  expect(
    screen.getByRole('progressbar', { name: 'Training progress' }),
  ).toBeVisible();
  expect(
    screen.queryByRole('heading', { name: 'No personal model yet' }),
  ).toBeNull();
  const facts = screen.getByLabelText('Personal model');
  expect(facts).toHaveTextContent(
    'Qwen3.5 2B · Apache 2.0 · On this computer (4.6 GB)',
  );
  expect(facts).toHaveTextContent('Training engineInstalled (4 GB)');
  expect(io.watchTraining).toHaveBeenCalledTimes(1);
  unmount();
  expect(stop).toHaveBeenCalledTimes(1);
});

it('Personal model: after two nights without room on the graphics card it says so and offers a smaller model (O112)', async () => {
  const snapshot = on();
  const io = fakeIO({
    ...snapshot,
    personal_model: {
      ...snapshot.personal_model,
      starting_model: {
        id: 'qwen3.5-9b',
        name: 'Qwen3.5 9B',
        licence: 'Apache 2.0',
        download_bytes: 19_300_000_000,
      },
      room: {
        nights: 2,
        need_gb: 15.3,
        free_gb: 9.2,
        smaller: {
          id: 'qwen3.5-2b',
          name: 'Qwen3.5 2B',
          licence: 'Apache 2.0',
          download_bytes: 4_570_000_000,
        },
      },
    },
  });
  show(io, 'model');

  const notice = await screen.findByRole('status', {
    name: 'No room on the graphics card',
  });
  expect(notice).toHaveTextContent(
    'No room on the graphics card for 2 nights. Training Qwen3.5 9B needs about 16 GB free on it; it had about 9 GB (your display and other apps use the rest).',
  );
  fireEvent.click(
    within(notice).getByRole('button', { name: 'Use Qwen3.5 2B instead…' }),
  );
  const ask = await screen.findByRole('alertdialog', {
    name: 'Use Qwen3.5 2B instead?',
  });
  expect(ask).toHaveTextContent(
    'Row-Bot downloads Qwen3.5 2B (4.6 GB) and trains it in quiet hours instead of Qwen3.5 9B.',
  );
  fireEvent.click(within(ask).getByRole('button', { name: 'Use Qwen3.5 2B' }));
  await act(async () => undefined);
  expect(io.run).toHaveBeenCalledWith('use_smaller_model', {
    model: 'qwen3.5-2b',
  });
});
