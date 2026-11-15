import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import { OverlayProvider } from '../../ui/overlays';
import LearningHome, { type LearningView } from './LearningHome';
import type { LearningIO } from './learning-state';
import {
  downloadState,
  draftedChange,
  fakeIO,
  installedModel,
  learningSnapshot,
  NO_GPU,
  SMALL_GPU,
  overnight,
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
  const io = fakeIO(
    on({
      data: { ...learningSnapshot().data, exists: true, bytes: 3_400_000 },
    }),
  );
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

it('the header says when Learning next looks for things to improve', () => {
  const tonight = new Date(Date.now() + 3 * 3_600_000);
  tonight.setMinutes(0, 0, 0);
  show(
    fakeIO(
      on({
        quiet_hours: {
          window_start: 1,
          window_end: 5,
          next_look_at: tonight.toISOString(),
        },
      }),
    ),
  );

  expect(
    screen.getByText(/^Next look tonight at .+ · 0 of 2M tokens this month$/),
  ).toBeVisible();
});

it('Overnight says what last night found and drafted, in counts', () => {
  show(
    fakeIO(
      on({
        overnight: overnight({
          needs_examples: 1,
          left_out: 1,
          looks_like_bug: 1,
        }),
        kinds_learning: [
          { kind: 'document_lookups', state: 'testing_change' },
          { kind: 'tool_workflows', state: 'needs_examples' },
        ],
      }),
    ),
  );

  const night = screen.getByRole('region', { name: 'Overnight' });
  expect(
    within(night).getByText('Found 3 things to improve · drafted 2'),
  ).toBeVisible();
  expect(
    within(night).getByText('1 kind of work needs more examples'),
  ).toBeVisible();
  expect(
    within(night).getByText('1 of them looks like a bug in Row-Bot itself'),
  ).toBeVisible();
  expect(
    within(night).getByText(
      /^Left out 1 idea: Learning needs your feedback first/,
    ),
  ).toBeVisible();
  const kinds = screen.getByRole('table', {
    name: 'Kinds of work and where they stand',
  });
  expect(
    within(kinds).getByRole('rowheader', { name: 'Document lookups' }),
  ).toBeVisible();
  expect(within(kinds).getByText('A change waits to be tested')).toBeVisible();
  expect(within(kinds).getByText('Needs more examples')).toBeVisible();
  expect(screen.queryByText('Nothing learned yet.')).toBeNull();
});

it('Overnight on the Claude plan says Learning needs another model and makes no promise', () => {
  show(
    fakeIO(
      on({
        overnight: overnight({ state: 'claude_plan', found: 2, drafted: 0 }),
      }),
    ),
  );

  expect(
    screen.getByText(
      /^Learning’s improvement work needs a model other than your Claude plan\./,
    ),
  ).toBeVisible();
  expect(screen.queryByText(/Found 2 things/)).toBeNull();
});

it('Overnight when the person came back says it carries on later', () => {
  show(
    fakeIO(
      on({ overnight: overnight({ state: 'stopped', found: 2, drafted: 1 }) }),
    ),
  );

  expect(
    screen.getByText('Found 2 things to improve · drafted 1'),
  ).toBeVisible();
  expect(
    screen.getByText(/^Stopped when you came back to Row-Bot/),
  ).toBeVisible();
});

it('Changes lists drafts as Waiting to be tested, with why in counts, never as Ready', () => {
  show(
    fakeIO(
      on({
        changes: [
          draftedChange(),
          draftedChange({
            id: 'proposal_2',
            change: 'way_of_working',
            title: 'Check the calendar first',
            kind: 'tool_workflows',
            never_automatic: true,
            why: {
              chats: 1,
              corrections: 0,
              stops: 2,
              retries: 0,
              denials: 0,
              failures: 0,
              imported: 1,
            },
          }),
        ],
      }),
    ),
    'changes',
  );

  const filters = screen.getByRole('radiogroup', { name: 'Show changes' });
  expect(within(filters).getByRole('radio', { name: 'Testing' })).toBeChecked();
  const list = screen.getByRole('list', { name: 'Changes being tested' });
  expect(within(list).getAllByText('Waiting to be tested')).toHaveLength(2);
  expect(within(list).getByText(/^Lesson · Document lookups/)).toBeVisible();
  expect(within(list).queryByText('Ready')).toBeNull();
  fireEvent.click(
    within(list).getByRole('button', {
      name: 'Review Check the calendar first',
    }),
  );
  const sheet = screen.getByRole('dialog', {
    name: 'Check the calendar first',
  });
  expect(
    within(sheet).getByText(
      /you stopped it twice \(1 from past chats Learning read\)\. It rests only on what tools or web pages said, so it’s never used by itself\./,
    ),
  ).toBeVisible();
  expect(within(sheet).getByText(/^Not tested yet\./)).toBeVisible();
  expect(within(sheet).getByRole('button', { name: 'Test now' })).toBeVisible();
  expect(
    within(sheet).queryByRole('button', { name: 'Use this change' }),
  ).toBeNull();
  fireEvent.click(within(sheet).getByRole('button', { name: 'Close' }));
  fireEvent.click(within(filters).getByRole('radio', { name: 'Ready' }));
  expect(screen.getByText(/No changes are ready to review\./)).toBeVisible();
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
    await screen.findByText(/^No lessons yet\. Lessons Row-Bot learns/),
  ).toBeVisible();
});

it('Personal model: not set up offers the download ask', async () => {
  const io = fakeIO(on());
  show(io, 'model');

  expect(
    screen.getByRole('heading', { name: 'No personal model yet' }),
  ).toBeVisible();
  expect(
    await screen.findByText('NVIDIA GeForce RTX 5060 Ti (16 GB)'),
  ).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Download…' }));
  expect(
    await screen.findByRole('dialog', {
      name: 'Download the training engine and Qwen3.5 9B?',
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
      'The training engine (2.2 GB) and Qwen3.5 9B (19.3 GB). 512 GB free on this computer.',
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

it('Personal model: a card too small to train points to Hugging Face in the settings (O124)', async () => {
  const { onOpenSettings } = show(
    fakeIO(on(), { hardware: SMALL_GPU }),
    'model',
  );
  expect(
    await screen.findByRole('heading', {
      name: 'This computer can’t train a model',
    }),
  ).toBeVisible();
  expect(
    screen.getByText(/You can train one on Hugging Face instead/),
  ).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Learning settings' }));
  expect(onOpenSettings).toHaveBeenCalledWith('personal-model');
});

it('Personal model: with no NVIDIA card it says plainly that lessons still work, and offers no Hugging Face (O131)', async () => {
  show(fakeIO(on(), { hardware: NO_GPU }), 'model');
  expect(
    await screen.findByText(
      /A personal model needs an NVIDIA graphics card\. Learning still improves Row-Bot with lessons and ways of working\./,
    ),
  ).toBeVisible();
  expect(screen.queryByText(/Hugging Face/)).toBeNull();
  expect(
    screen.queryByRole('button', { name: 'Learning settings' }),
  ).toBeNull();
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
  const facts = screen.getByLabelText('Personal model', { selector: 'dl' });
  expect(facts).toHaveTextContent(
    'Qwen3.5 9B · Apache 2.0 · On this computer (4.6 GB)',
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
          id: 'test-small',
          name: 'A small model',
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
    within(notice).getByRole('button', { name: 'Use A small model instead…' }),
  );
  const ask = await screen.findByRole('alertdialog', {
    name: 'Use A small model instead?',
  });
  expect(ask).toHaveTextContent(
    'Row-Bot downloads A small model (4.6 GB) and trains it in quiet hours instead of Qwen3.5 9B.',
  );
  fireEvent.click(
    within(ask).getByRole('button', { name: 'Use A small model' }),
  );
  await act(async () => undefined);
  expect(io.run).toHaveBeenCalledWith('use_smaller_model', {
    model: 'test-small',
  });
});
