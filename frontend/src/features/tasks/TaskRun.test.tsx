import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type {
  TaskApprovalPage,
  TaskApprovalReview,
  TaskRunPage,
  TaskRunResult,
  TaskRunReview,
  TaskRunSummary,
} from '../../api/types';
import TaskRun, { type TaskRunProps } from './TaskRun';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const EMPTY_HISTORY = 'No runs yet. Run it when you are ready.';

const review: TaskRunReview = {
  task_id: 'task-a',
  task_revision: 'a'.repeat(64),
  policy_revision: 'b'.repeat(64),
  agent_profile_id: 'workflow-default',
  approval_mode: 'block',
  notify_only: false,
  steps_total: 2,
  conversation_id: null,
};
function runSummary(id = 'run-a', status = 'completed'): TaskRunSummary {
  return {
    id,
    task_id: 'task-a',
    conversation_id: 'conversation-a',
    status,
    started_at: '2026-01-01T10:00:00',
    finished_at: status === 'completed' ? '2026-01-01T10:01:00' : null,
    steps_total: 2,
    steps_done: status === 'completed' ? 2 : 0,
  };
}
function history(items: TaskRunSummary[] = []): TaskRunPage {
  return {
    task_id: 'task-a',
    revision: 'h'.repeat(64),
    items,
    next_cursor: null,
    total: items.length,
  };
}
function approval(
  overrides: Partial<TaskApprovalReview> = {},
): TaskApprovalReview {
  return {
    id: 'approval-a',
    task_id: 'task-a',
    run_id: 'run-a',
    revision: 'c'.repeat(64),
    message: 'Review the exact synthetic action',
    requested_at: '2026-01-01T10:00:00',
    expires_at: null,
    approval_mode: 'block',
    message_truncated: false,
    response_available: true,
    ...overrides,
  };
}
function approvalPage(items: TaskApprovalReview[] = []): TaskApprovalPage {
  return {
    items,
    revision: 'd'.repeat(64),
    total: items.length,
    next_cursor: null,
  };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (cause: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
function props(overrides: Partial<TaskRunProps> = {}): TaskRunProps {
  return {
    taskId: 'task-a',
    loadReview: vi.fn().mockResolvedValue(review),
    run: vi.fn().mockResolvedValue({ run: runSummary(), replayed: false }),
    loadHistory: vi.fn().mockResolvedValue(history()),
    loadApprovals: vi.fn().mockResolvedValue(approvalPage()),
    respondApproval: vi.fn().mockResolvedValue({
      approval_id: 'approval-a',
      decision: 'approved',
      run: runSummary(),
    }),
    stop: vi.fn().mockResolvedValue({
      run: runSummary('run-a', 'stopping'),
      stop_requested: true,
      quiesced: false,
    }),
    openConversation: vi.fn(),
    ...overrides,
  };
}

it('loads a review and saved history without running or granting approval', async () => {
  const callbacks = props();
  render(<TaskRun {...callbacks} />);
  await screen.findByText(EMPTY_HISTORY);
  expect(
    screen.getByRole('region', { name: 'Task runs and approvals' }),
  ).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Run now' })).toBeEnabled();
  expect(screen.getByRole('button', { name: 'Refresh runs' })).toBeEnabled();
  expect(callbacks.run).not.toHaveBeenCalled();
  expect(callbacks.respondApproval).not.toHaveBeenCalled();
  expect(callbacks.loadApprovals).not.toHaveBeenCalled();
  // The reviewed facts are shown in words, not as raw policy tokens.
  expect(screen.getByText('2 steps')).toBeInTheDocument();
  expect(screen.getByText('Profile Workflow default')).toBeInTheDocument();
  expect(screen.getByText('Blocks actions')).toBeInTheDocument();
  expect(
    screen.queryByRole('region', { name: 'Selected run' }),
  ).not.toBeInTheDocument();
});

it('starts only one explicit operation and requires fresh review for another run', async () => {
  const started = deferred<TaskRunResult>();
  const callbacks = props({ run: vi.fn().mockReturnValue(started.promise) });
  render(<TaskRun {...callbacks} />);
  await screen.findByText(EMPTY_HISTORY);
  const button = screen.getByRole('button', { name: 'Run now' });
  fireEvent.click(button);
  fireEvent.click(button);
  expect(callbacks.run).toHaveBeenCalledTimes(1);
  expect(callbacks.run).toHaveBeenCalledWith(review);
  await act(async () =>
    started.resolve({ run: runSummary(), replayed: false }),
  );
  expect(screen.getByRole('button', { name: 'Run now' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Run now' }));
  expect(callbacks.run).toHaveBeenCalledTimes(1);
  expect(screen.queryByText(EMPTY_HISTORY)).not.toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent(
    'Run admitted. Refresh to see its latest saved progress.',
  );
  expect(
    screen.getByRole('button', { name: 'Show run run-a' }),
  ).toHaveAttribute('aria-current', 'true');
  expect(
    within(screen.getByRole('region', { name: 'Selected run' })).getByText(
      'Completed',
    ),
  ).toBeInTheDocument();
});

it('preserves uncertainty and does not automatically retry the run', async () => {
  const callbacks = props({
    run: vi.fn().mockRejectedValue({ code: 'task_run_unconfirmed' }),
  });
  render(<TaskRun {...callbacks} />);
  await screen.findByText(EMPTY_HISTORY);
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Run now' })),
  );
  expect(callbacks.run).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('alert')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Run now' })).toBeDisabled();
  const refresh = screen.getByRole('button', { name: 'Refresh runs' });
  expect(refresh).toBeEnabled();
  // Refreshing loads a fresh review; it never replays the unconfirmed run.
  await act(async () => fireEvent.click(refresh));
  expect(callbacks.loadReview).toHaveBeenCalledTimes(2);
  expect(callbacks.run).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('button', { name: 'Run now' })).toBeEnabled();
});

it('binds approval to the exact reviewed card and keeps generated text inert', async () => {
  const card = approval({ message: '<script>window.taskAttack=1</script>' });
  const callbacks = props({
    loadHistory: vi
      .fn()
      .mockResolvedValue(history([runSummary('run-a', 'paused')])),
    loadApprovals: vi.fn().mockResolvedValue(approvalPage([card])),
  });
  const view = render(<TaskRun {...callbacks} />);
  await screen.findByText(card.message);
  expect(view.container.querySelector('script')).toBeNull();
  expect(
    within(
      screen.getByRole('article', { name: 'Pending task approval' }),
    ).getByRole('heading', { name: 'Approval needed' }),
  ).toBeInTheDocument();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Approve' })),
  );
  expect(callbacks.respondApproval).toHaveBeenCalledWith(card, true);
  expect(callbacks.run).not.toHaveBeenCalled();
});

it('rejects through the same approval owner without turning it into a run', async () => {
  const card = approval();
  const callbacks = props({
    loadHistory: vi
      .fn()
      .mockResolvedValue(history([runSummary('run-a', 'paused')])),
    loadApprovals: vi.fn().mockResolvedValue(approvalPage([card])),
  });
  render(<TaskRun {...callbacks} />);
  await screen.findByText(card.message);
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Reject' })),
  );
  expect(callbacks.respondApproval).toHaveBeenCalledWith(card, false);
  expect(callbacks.run).not.toHaveBeenCalled();
});

it('does not offer actions for a truncated or other-owner approval and opens its actual conversation', async () => {
  const card = approval({ response_available: false, message_truncated: true });
  const callbacks = props({
    loadHistory: vi
      .fn()
      .mockResolvedValue(history([runSummary('run-a', 'paused')])),
    loadApprovals: vi.fn().mockResolvedValue(approvalPage([card])),
  });
  render(<TaskRun {...callbacks} />);
  await screen.findByText(card.message);
  expect(screen.getByRole('button', { name: 'Approve' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Reject' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Open conversation' }));
  expect(callbacks.openConversation).toHaveBeenCalledWith('conversation-a');
});

it('shows a stop request without claiming cleanup completed', async () => {
  const callbacks = props({
    loadHistory: vi
      .fn()
      .mockResolvedValue(history([runSummary('run-a', 'running')])),
  });
  render(<TaskRun {...callbacks} />);
  await screen.findByRole('button', { name: 'Stop run' });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Stop run' })),
  );
  expect(callbacks.stop).toHaveBeenCalledWith('task-a', 'run-a');
  expect(screen.getByRole('status')).toHaveTextContent(
    'Waiting for confirmed worker cleanup',
  );
  expect(
    screen.getByText(/Completion and cleanup are not yet confirmed/),
  ).toBeInTheDocument();
});

it('shows the selected run status in words with its start time and step progress', async () => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date(2026, 0, 1, 12));
  const waiting = { ...runSummary('run-b', 'paused'), steps_done: 1 };
  const callbacks = props({
    loadHistory: vi
      .fn()
      .mockResolvedValue(history([waiting, runSummary('run-a', 'completed')])),
  });
  render(<TaskRun {...callbacks} />);
  const selected = await screen.findByRole('region', { name: 'Selected run' });
  const twoHoursAgo = new Intl.RelativeTimeFormat(undefined, {
    numeric: 'auto',
    style: 'long',
  }).format(-2, 'hour');
  expect(within(selected).getByText('Waiting for approval')).toBeVisible();
  expect(within(selected).queryByText(/paused/)).not.toBeInTheDocument();
  expect(within(selected).queryByText(/Saved status/)).not.toBeInTheDocument();
  expect(within(selected).getByText(/^Started/)).toHaveTextContent(
    `Started ${twoHoursAgo}`,
  );
  expect(within(selected).getByText(twoHoursAgo)).toHaveAttribute(
    'datetime',
    new Date(2026, 0, 1, 10).toISOString(),
  );
  expect(
    within(selected).getByRole('progressbar', { name: 'Run progress' }),
  ).toHaveAttribute('value', '1');
  expect(within(selected).getByText('1 of 2 steps')).toBeInTheDocument();
  expect(
    within(selected).getByRole('button', { name: 'Stop run' }),
  ).toBeEnabled();

  const waitingRow = screen.getByRole('button', { name: 'Show run run-b' });
  const completedRow = screen.getByRole('button', { name: 'Show run run-a' });
  expect(waitingRow).toHaveAttribute('aria-current', 'true');
  expect(waitingRow).toHaveTextContent('Waiting for approval');
  expect(waitingRow).toHaveTextContent('1/2');
  expect(completedRow).not.toHaveAttribute('aria-current');
  expect(completedRow).toHaveTextContent('Completed');

  await act(async () => fireEvent.click(completedRow));
  expect(completedRow).toHaveAttribute('aria-current', 'true');
  expect(waitingRow).not.toHaveAttribute('aria-current');
  expect(within(selected).getByText('Completed')).toBeVisible();
  expect(within(selected).getByText('2 of 2 steps')).toBeInTheDocument();
  expect(
    within(selected).queryByRole('button', { name: 'Stop run' }),
  ).not.toBeInTheDocument();
  // Only the non-terminal run asked for its pending approvals.
  expect(callbacks.loadApprovals).toHaveBeenCalledOnce();
  expect(callbacks.loadApprovals).toHaveBeenCalledWith(
    'task-a',
    'run-b',
    undefined,
    expect.any(AbortSignal),
  );
  expect(callbacks.run).not.toHaveBeenCalled();
  expect(callbacks.stop).not.toHaveBeenCalled();
});

it('aborts late load results when another task is selected', async () => {
  const delayed = deferred<TaskRunReview>();
  const loadReview = vi
    .fn()
    .mockReturnValueOnce(delayed.promise)
    .mockResolvedValue({
      ...review,
      task_id: 'task-b',
      agent_profile_id: 'second-profile',
    });
  const callbacks = props({ loadReview });
  const view = render(<TaskRun {...callbacks} />);
  const signal = loadReview.mock.calls[0][1] as AbortSignal;
  view.rerender(<TaskRun {...callbacks} taskId="task-b" />);
  await screen.findByText('Profile Second profile');
  expect(signal.aborted).toBe(true);
  await act(async () => delayed.resolve(review));
  expect(screen.getByText('Profile Second profile')).toBeInTheDocument();
  expect(
    screen.queryByText('Profile Workflow default'),
  ).not.toBeInTheDocument();
});

it('ignores run completion after unmount and retains it in the controller owner', async () => {
  const delayed = deferred<TaskRunResult>();
  const callbacks = props({ run: vi.fn().mockReturnValue(delayed.promise) });
  const view = render(<TaskRun {...callbacks} />);
  await screen.findByText(EMPTY_HISTORY);
  fireEvent.click(screen.getByRole('button', { name: 'Run now' }));
  view.unmount();
  await act(async () =>
    delayed.resolve({ run: runSummary(), replayed: false }),
  );
  expect(callbacks.run).toHaveBeenCalledOnce();
  expect(callbacks.openConversation).not.toHaveBeenCalled();
});

it('keeps a bounded history window while every forward page remains reachable', async () => {
  const loadHistory = vi.fn(async (_task: string, cursor?: string) => {
    const page = cursor ? Number(cursor) : 0;
    return {
      ...history(
        Array.from({ length: 100 }, (_, index) =>
          runSummary(`run-${page * 100 + index}`),
        ),
      ),
      next_cursor: page < 2 ? String(page + 1) : null,
      total: 300,
    };
  });
  render(<TaskRun {...props({ loadHistory })} />);
  await screen.findByRole('button', { name: 'Show run run-0' });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Load more runs' })),
  );
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Load more runs' })),
  );
  expect(screen.getAllByRole('button', { name: /^Show run/ })).toHaveLength(
    200,
  );
  expect(
    screen.queryByRole('button', { name: 'Show run run-0' }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: 'Show run run-299' }),
  ).toBeInTheDocument();
  expect(screen.getByText(/100 earlier loaded runs/)).toBeInTheDocument();
});

it('replaces a bounded approval page using the exact continuation cursor', async () => {
  const loadApprovals = vi
    .fn()
    .mockResolvedValueOnce({
      ...approvalPage([approval()]),
      total: 2,
      next_cursor: 'next-approval',
    })
    .mockResolvedValueOnce(
      approvalPage([approval({ id: 'approval-b', message: 'Second review' })]),
    );
  const callbacks = props({
    loadApprovals,
    loadHistory: vi
      .fn()
      .mockResolvedValue(history([runSummary('run-a', 'paused')])),
  });
  render(<TaskRun {...callbacks} />);
  await screen.findByRole('button', { name: 'Next approvals' });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Next approvals' })),
  );
  expect(loadApprovals.mock.calls[1].slice(0, 3)).toEqual([
    'task-a',
    'run-a',
    'next-approval',
  ]);
  expect(screen.getByText('Second review')).toBeInTheDocument();
  expect(
    screen.queryByText('Review the exact synthetic action'),
  ).not.toBeInTheDocument();
});

it('rejects changed history revisions instead of mixing stale pages', async () => {
  const loadHistory = vi
    .fn()
    .mockResolvedValueOnce({ ...history([runSummary()]), next_cursor: 'next' })
    .mockResolvedValueOnce({
      ...history([runSummary('run-b')]),
      revision: 'changed',
    });
  render(<TaskRun {...props({ loadHistory })} />);
  await screen.findByRole('button', { name: 'Load more runs' });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Load more runs' })),
  );
  expect(screen.getByRole('alert')).toHaveTextContent('Run history changed');
  expect(
    screen.queryByRole('button', { name: 'Show run run-b' }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Load more runs' })).toBeDisabled();
});
