import type {
  MemoryReviewApply,
  MemoryReviewOverview,
  MemoryReviewProposal,
  MemoryReviewProposals,
  MemoryReviewReceipt,
  MemoryReviewRun,
  UploadCancelled,
} from '../../api/types';
import { plural } from './memory-import-io';

/** What the Review memory dialog asks of the server (the controller, or a fake in tests). */
export type MemoryReviewIO = {
  overview(
    importBatch?: string,
    signal?: AbortSignal,
  ): Promise<MemoryReviewOverview>;
  begin(importBatch?: string): Promise<MemoryReviewRun>;
  run(run: string, signal?: AbortSignal): Promise<MemoryReviewRun>;
  discard(run: string): Promise<UploadCancelled>;
  cancel(run: string): Promise<MemoryReviewRun>;
  proposals(run: string, signal?: AbortSignal): Promise<MemoryReviewProposals>;
  apply(
    run: string,
    command: Omit<MemoryReviewApply, 'client_session_id'>,
  ): Promise<MemoryReviewReceipt>;
  undo(run: string, command: string): Promise<MemoryReviewReceipt>;
};

type ReviewController = {
  memoryReviewOverview: MemoryReviewIO['overview'];
  beginMemoryReview: MemoryReviewIO['begin'];
  memoryReview: MemoryReviewIO['run'];
  discardMemoryReview: MemoryReviewIO['discard'];
  cancelMemoryReview: MemoryReviewIO['cancel'];
  memoryReviewProposals: MemoryReviewIO['proposals'];
  applyMemoryReview: MemoryReviewIO['apply'];
  undoMemoryReview: MemoryReviewIO['undo'];
};

export function memoryReviewIO(controller: ReviewController): MemoryReviewIO {
  return {
    overview: (batch, signal) => controller.memoryReviewOverview(batch, signal),
    begin: (batch) => controller.beginMemoryReview(batch),
    run: (run, signal) => controller.memoryReview(run, signal),
    discard: (run) => controller.discardMemoryReview(run),
    cancel: (run) => controller.cancelMemoryReview(run),
    proposals: (run, signal) => controller.memoryReviewProposals(run, signal),
    apply: (run, command) => controller.applyMemoryReview(run, command),
    undo: (run, command) => controller.undoMemoryReview(run, command),
  };
}

export type ReviewAction = MemoryReviewProposal['action'];

/** The groups of the list, in the order they show. */
export const GROUPS: readonly {
  action: ReviewAction;
  title: string;
  hint: string;
}[] = [
  {
    action: 'remove',
    title: 'Remove',
    hint: 'Test data, out-of-date facts, run logs and repeats.',
  },
  {
    action: 'merge',
    title: 'Merge',
    hint: 'Two memories that say the same thing become one.',
  },
  {
    action: 'rewrite',
    title: 'Rewrite',
    hint: 'Right, but hard to read or missing a date.',
  },
  {
    action: 'split',
    title: 'Split your own record',
    hint: 'A short profile, and the rest as separate memories.',
  },
  { action: 'keep', title: 'Keep', hint: 'Nothing to change.' },
];

/** The words a run's failure code shows, without the code itself. */
export function reviewProblem(code: string | null | undefined): string {
  switch (code) {
    case 'review_model_unavailable':
      return "Your chat model didn't answer. Check it in Settings › Models, then try again.";
    case 'model_selection_required':
      return 'Choose a chat model in Settings › Models first.';
    default:
      return 'Something went wrong. Try again.';
  }
}

export function receiptWords(receipt: MemoryReviewReceipt) {
  const parts = [];
  if (receipt.removed)
    parts.push(`${receipt.removed.toLocaleString()} removed`);
  if (receipt.merged) parts.push(`${receipt.merged.toLocaleString()} merged`);
  if (receipt.rewritten)
    parts.push(`${receipt.rewritten.toLocaleString()} rewritten`);
  if (receipt.split)
    parts.push(
      `your record split into ${plural(receipt.added, 'new memory', 'new memories')}`,
    );
  return parts.join(' · ') || 'Nothing changed';
}

/** Words of *before* and *after*, each marked where the other differs. */
export function wordDiff(before: string, after: string) {
  const a = before.split(/\s+/).filter(Boolean);
  const b = after.split(/\s+/).filter(Boolean);
  if (a.length * b.length > 250_000)
    return {
      before: a.map((word) => ({ word, changed: true })),
      after: b.map((word) => ({ word, changed: true })),
    };
  const table = Array.from({ length: a.length + 1 }, () =>
    new Array<number>(b.length + 1).fill(0),
  );
  for (let i = a.length - 1; i >= 0; i -= 1)
    for (let j = b.length - 1; j >= 0; j -= 1)
      table[i][j] =
        a[i] === b[j]
          ? table[i + 1][j + 1] + 1
          : Math.max(table[i + 1][j], table[i][j + 1]);
  const kept = { before: new Set<number>(), after: new Set<number>() };
  for (let i = 0, j = 0; i < a.length && j < b.length;)
    if (a[i] === b[j]) {
      kept.before.add(i++);
      kept.after.add(j++);
    } else if (table[i + 1][j] >= table[i][j + 1]) i += 1;
    else j += 1;
  return {
    before: a.map((word, index) => ({
      word,
      changed: !kept.before.has(index),
    })),
    after: b.map((word, index) => ({ word, changed: !kept.after.has(index) })),
  };
}
