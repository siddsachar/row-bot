import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type {
  MemoryReviewOverview,
  MemoryReviewProposal,
  MemoryReviewReceipt,
  MemoryReviewRun,
} from '../../api/types';
import { OverlayProvider } from '../../ui/overlays';
import MemoryReviewDialog from './MemoryReviewDialog';
import type { MemoryReviewIO } from './memory-review-io';
import { wordDiff } from './memory-review-io';

const RUN = 'c'.repeat(32);
const REVISION = 'd'.repeat(64);

function overview(over: Partial<MemoryReviewOverview> = {}) {
  return {
    memories: 6,
    model_calls: 1,
    many: false,
    model: { name: 'qwen3:8b', provider: 'Local', on_this_computer: true },
    recent: [],
    ...over,
  } satisfies MemoryReviewOverview;
}

function run(over: Partial<MemoryReviewRun> = {}): MemoryReviewRun {
  return {
    run_id: RUN,
    status: 'open',
    job: { kind: 'review', status: 'completed', done: 1, total: 1 },
    counts: { remove: 2, merge: 0, rewrite: 1, split: 0, keep: 3 },
    revision: REVISION,
    stale: false,
    warnings: [],
    ...over,
  };
}

function proposal(
  memory: string,
  over: Partial<MemoryReviewProposal>,
): MemoryReviewProposal {
  return {
    proposal_id: `memory:${memory}`,
    memory_id: memory,
    action: 'keep',
    reason_code: 'keep',
    reason: 'Nothing to change',
    subject: memory,
    category: 'fact',
    text: `${memory} text`,
    length: 10,
    marks: [],
    selected: false,
    removable: false,
    ...over,
  };
}

const ITEMS: MemoryReviewProposal[] = [
  proposal('fixture', {
    action: 'remove',
    reason_code: 'test_data',
    reason: 'Test data from development',
    subject: '__MTEST_alpha',
    selected: true,
  }),
  proposal('weather', {
    action: 'remove',
    reason_code: 'not_about_the_person',
    reason: 'Not about you: general knowledge or a passing topic',
    subject: 'Weather in Leeds',
    selected: true,
  }),
  proposal('dentist', {
    action: 'rewrite',
    reason_code: 'rewrite',
    reason: 'Says “next week” without a date; the date is written out',
    subject: 'Dentist',
    text: 'Has a dentist appointment next week.',
    proposed_text: 'Has a dentist appointment in the week of 17 November 2026.',
    selected: true,
  }),
  proposal('register', {
    reason_code: 'long_record',
    reason: 'Kept — long record, check it yourself (5,400 characters)',
    subject: 'Creator outreach register',
    length: 5400,
    text: 'Emailed creators about the launch',
    marks: ['long_record'],
    removable: true,
  }),
  proposal('lesson', {
    reason_code: 'lesson',
    reason: 'A lesson Row-Bot learned; kept exactly as it is',
    subject: 'Check before done',
    category: 'self_knowledge',
    marks: ['lesson'],
    removable: true,
  }),
  proposal('cello', { subject: 'Cello' }),
];

function receipt(over: Partial<MemoryReviewReceipt> = {}): MemoryReviewReceipt {
  return {
    run_id: RUN,
    command_id: '11111111-1111-4111-8111-111111111111',
    status: 'applied',
    applied_at: '2026-11-12T10:00:00',
    undo_until: '2026-12-12T10:00:00',
    removed: 2,
    merged: 0,
    rewritten: 1,
    split: 0,
    added: 0,
    failed: 0,
    backup: 'memory.db.pre-review-20261112-100000',
    ...over,
  };
}

function fakeIO(over: Partial<MemoryReviewIO> = {}) {
  const io: MemoryReviewIO = {
    overview: vi.fn(async () => overview()),
    begin: vi.fn(async () =>
      run({
        job: { kind: 'review', status: 'running', done: 0, total: 1 },
        revision: null,
      }),
    ),
    run: vi.fn(async () => run()),
    discard: vi.fn(async () => ({ cancelled: true as const })),
    cancel: vi.fn(async () => run()),
    proposals: vi.fn(async () => ({
      run_id: RUN,
      revision: REVISION,
      items: ITEMS,
    })),
    apply: vi.fn(async () => receipt()),
    undo: vi.fn(async () =>
      receipt({ status: 'undone', undone_at: '2026-11-12T10:05:00' }),
    ),
    ...over,
  };
  return io;
}

function show(io: MemoryReviewIO, onChanged = vi.fn(), importBatch?: string) {
  render(
    <OverlayProvider>
      <MemoryReviewDialog
        open
        onOpenChange={vi.fn()}
        io={io}
        importBatch={importBatch}
        onChanged={onChanged}
      />
    </OverlayProvider>,
  );
  return { onChanged };
}

function showOnly(value: string) {
  fireEvent.change(screen.getByRole('combobox', { name: 'Show' }), {
    target: { value },
  });
}

async function startReview() {
  fireEvent.click(await screen.findByRole('button', { name: 'Start review' }));
  return screen.findByRole('list', { name: 'Remove suggestions' });
}

describe('MemoryReviewDialog', () => {
  it('says what a review does and with which model before anything runs', async () => {
    const io = fakeIO({
      overview: vi.fn(async () =>
        overview({ memories: 740, model_calls: 93, many: true }),
      ),
    });
    show(io);
    expect(
      await screen.findByText(/reads each of your 740 memories/),
    ).toBeVisible();
    expect(
      screen.getByText(/Nothing changes until you approve it/),
    ).toBeVisible();
    expect(
      screen.getByText(
        /Uses qwen3:8b on this computer\. With this many memories it takes about 93 calls/,
      ),
    ).toBeVisible();
    expect(io.begin).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toMatch(/entit|LLM|reconcile/i);
  });

  it('groups suggestions with reasons, keeps long records and lessons unchosen, and applies the chosen ones', async () => {
    const io = fakeIO();
    const { onChanged } = show(io);
    const removals = await startReview();
    expect(
      within(removals).getByText('Test data from development'),
    ).toBeVisible();
    const rewrite = screen.getByRole('list', { name: 'Rewrite suggestions' });
    expect(within(rewrite).getByText('Now')).toBeVisible();
    expect(within(rewrite).getByText('Becomes')).toBeVisible();
    expect(within(rewrite).getByText(/17/)).toBeVisible();
    // Leave one removal out.
    fireEvent.click(
      within(removals).getByRole('checkbox', { name: 'Weather in Leeds' }),
    );
    // Kept long records and lessons are marked, and no one chose them.
    showOnly('marked');
    const kept = screen.getByRole('list', { name: 'Keep suggestions' });
    expect(within(kept).getByText('Long record')).toBeVisible();
    expect(within(kept).getByText('Lesson')).toBeVisible();
    expect(within(kept).queryByRole('checkbox')).toBeNull();
    expect(
      screen.queryByRole('list', { name: 'Remove suggestions' }),
    ).toBeNull();
    showOnly('');

    fireEvent.click(screen.getByRole('button', { name: 'Review 2 changes' }));
    expect(
      await screen.findByText('2 changes; memory is backed up first'),
    ).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Make 2 changes' }));
    expect(
      await screen.findByRole('heading', { name: 'Memory tidied up' }),
    ).toBeVisible();
    expect(io.apply).toHaveBeenCalledWith(RUN, {
      command_id: expect.any(String),
      revision: REVISION,
      choices: [
        { proposal_id: 'memory:fixture' },
        { proposal_id: 'memory:dentist' },
      ],
    });
    expect(onChanged).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/You can undo this review until/)).toBeVisible();

    fireEvent.click(screen.getByRole('button', { name: 'Undo review' }));
    expect(
      await screen.findByRole('heading', { name: 'Review undone' }),
    ).toBeVisible();
    expect(io.undo).toHaveBeenCalledWith(RUN, expect.any(String));
  });

  it('removes a long record only after a confirmation naming it, and sends its name', async () => {
    const io = fakeIO();
    show(io);
    await startReview();
    showOnly('marked');
    fireEvent.click(
      screen.getByRole('button', { name: 'Remove Creator outreach register…' }),
    );
    const alert = await screen.findByRole('alertdialog', {
      name: 'Remove “Creator outreach register”?',
    });
    expect(within(alert).getByText(/who it already emailed/)).toBeVisible();
    fireEvent.click(within(alert).getByRole('button', { name: /^Remove/ }));
    await waitFor(() =>
      expect(screen.getByText('You chose to remove it')).toBeVisible(),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Review 4 changes' }));
    expect(await screen.findByText(/You chose to remove/)).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Make 4 changes' }));
    await screen.findByRole('heading', { name: 'Memory tidied up' });
    expect(vi.mocked(io.apply).mock.calls[0][1].choices).toContainEqual({
      proposal_id: 'memory:register',
      remove: true,
      confirm: 'Creator outreach register',
    });
  });

  it('sends an edited rewrite and asks for a new review when memory changed', async () => {
    const stale = Object.assign(new Error('stale'), { code: 'review_stale' });
    const io = fakeIO({ apply: vi.fn(async () => Promise.reject(stale)) });
    show(io);
    await startReview();
    fireEvent.click(
      screen.getByRole('button', { name: 'Edit the new text of Dentist' }),
    );
    fireEvent.change(
      screen.getByRole('textbox', { name: 'New text for Dentist' }),
      {
        target: { value: 'Has a dentist appointment on 18 November 2026.' },
      },
    );
    fireEvent.click(screen.getByRole('button', { name: 'Save edit' }));
    expect(screen.getByText('Edited by you')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Review 3 changes' }));
    fireEvent.click(
      await screen.findByRole('button', { name: 'Make 3 changes' }),
    );
    expect(await screen.findByText('Memory changed')).toBeVisible();
    expect(vi.mocked(io.apply).mock.calls[0][1].choices).toContainEqual({
      proposal_id: 'memory:dentist',
      text: 'Has a dentist appointment on 18 November 2026.',
    });
    fireEvent.click(screen.getByRole('button', { name: 'Review again' }));
    await waitFor(() => expect(io.begin).toHaveBeenCalledTimes(2));
  });

  it('tidies up after an import and says when the model did not answer', async () => {
    const io = fakeIO({
      overview: vi.fn(async () => overview({ memories: 4 })),
      run: vi.fn(async () =>
        run({
          job: {
            kind: 'review',
            status: 'failed',
            done: 0,
            total: 1,
            code: 'review_model_unavailable',
          },
        }),
      ),
    });
    show(io, vi.fn(), 'ab'.repeat(16));
    expect(
      await screen.findByRole('heading', { name: 'Tidy up after your import' }),
    ).toBeVisible();
    expect(io.overview).toHaveBeenCalledWith(
      'ab'.repeat(16),
      expect.anything(),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Start review' }));
    expect(
      await screen.findByText(/Your chat model didn't answer/),
    ).toBeVisible();
    expect(io.begin).toHaveBeenCalledWith('ab'.repeat(16));
  });
});

describe('wordDiff', () => {
  it('marks the words each side changed', () => {
    const diff = wordDiff('meet next week', 'meet on 17 November');
    expect(
      diff.before.filter((part) => part.changed).map((part) => part.word),
    ).toEqual(['next', 'week']);
    expect(
      diff.after.filter((part) => part.changed).map((part) => part.word),
    ).toEqual(['on', '17', 'November']);
  });
});
