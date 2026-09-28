import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import WorkspaceProcesses, {
  createWorkspaceProcessesSession,
  type WorkspaceProcessesProps,
  type WorkspaceProcessInfo,
  type WorkspaceProcessReview,
  type WorkspaceProcessSnapshot,
} from './WorkspaceProcesses';

const scope = {
  resource_id: 'workspace',
  conversation_id: 'chat',
  binding_id: 'binding',
  binding_revision: '1',
};
const snapshot: WorkspaceProcessSnapshot = {
  ...scope,
  resource_revision: 'resource-1',
  processes: [],
  schema_version: 1,
};
const process: WorkspaceProcessInfo = {
  process_id: 'owned-id',
  command_id: 'owned-id',
  run_id: 'run',
  command: 'python check.py',
  state: 'running',
  exit_code: null,
  quiesced: false,
};
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
function fixture(
  overrides: Partial<
    Pick<WorkspaceProcessesProps, 'load' | 'loadRecovery' | 'checks'>
  > = {},
) {
  const session = createWorkspaceProcessesSession(scope);
  const props = {
    scope,
    resourceRevision: 'resource-1',
    visible: true,
    session,
    loadRecovery: overrides.loadRecovery,
    checks: overrides.checks,
    load: vi.fn<WorkspaceProcessesProps['load']>(
      overrides.load ?? (async () => snapshot),
    ),
    output: vi
      .fn<WorkspaceProcessesProps['output']>()
      .mockImplementation(async (id: string, cursor: number) => ({
        process_id: id,
        entries: [],
        next_cursor: cursor,
        truncated: false,
        quiesced: true,
      })),
    review: vi
      .fn<WorkspaceProcessesProps['review']>()
      .mockImplementation(async (attempt) => ({
        ...scope,
        resource_revision: attempt.snapshot.resource_revision,
        command: attempt.command,
        command_id: attempt.command_id,
        decision: 'approved',
        approval_id: 'server-evidence',
      })),
    start: vi
      .fn<WorkspaceProcessesProps['start']>()
      .mockImplementation(async (attempt) => ({
        ...process,
        process_id: attempt.command_id,
        command_id: attempt.command_id,
        command: attempt.command,
      })),
    stop: vi
      .fn<WorkspaceProcessesProps['stop']>()
      .mockImplementation(async (id: string) => ({
        ...process,
        process_id: id,
        command_id: id,
        state: 'stopping',
      })),
    recover: vi
      .fn<WorkspaceProcessesProps['recover']>()
      .mockImplementation(async (id: string) => ({
        ...process,
        process_id: id,
        command_id: id,
        state: 'exited',
        exit_code: 130,
        quiesced: true,
      })),
  };
  return { props, session, view: render(<WorkspaceProcesses {...props} />) };
}
const pendingReview = async (
  attempt: Parameters<WorkspaceProcessesProps['review']>[0],
): Promise<WorkspaceProcessReview> => ({
  ...scope,
  resource_revision: attempt.snapshot.resource_revision,
  command: attempt.command,
  command_id: attempt.command_id,
  decision: 'pending',
  approval_id: null,
});
const commandBox = () =>
  screen.getByRole('textbox', { name: 'Process command' });
const runButton = () => screen.getByRole('button', { name: 'Run' });
async function type(command = 'python check.py') {
  await screen.findByText(/No commands have run here yet/);
  fireEvent.change(commandBox(), { target: { value: command } });
  await waitFor(() => expect(runButton()).toBeEnabled());
}
async function runCommand(command = 'python check.py') {
  await type(command);
  await userEvent.click(runButton());
}

it('loads passively, never dispatches on open, and starts only with exact server evidence', async () => {
  const { props } = fixture();
  await type();
  expect(props.review).not.toHaveBeenCalled();
  expect(props.start).not.toHaveBeenCalled();
  expect(props.stop).not.toHaveBeenCalled();
  await userEvent.click(runButton());
  await waitFor(() => expect(props.start).toHaveBeenCalledOnce());
  expect(props.review).toHaveBeenCalledOnce();
  const [attempt, evidence] = props.start.mock.calls[0];
  expect(attempt.command).toBe('python check.py');
  expect(attempt.command_id).toBe(props.review.mock.calls[0][0].command_id);
  expect(evidence.command_id).toBe(attempt.command_id);
  expect(evidence.approval_id).toBe('server-evidence');
  const processes = screen.getByRole('region', { name: 'Processes' });
  expect(within(processes).getAllByText('Running')).not.toHaveLength(0);
  // A running command holds the workspace: no second command meanwhile.
  expect(commandBox()).toBeDisabled();
  expect(runButton()).toBeDisabled();
});

it('checks a pending original approval without executing or changing its ID', async () => {
  const { props } = fixture();
  props.review.mockImplementationOnce(pendingReview);
  await runCommand();
  await screen.findByText(/Approval is pending/);
  expect(props.start).not.toHaveBeenCalled();
  expect(commandBox()).toBeDisabled();
  await userEvent.click(screen.getByRole('button', { name: 'Check approval' }));
  await waitFor(() => expect(props.start).toHaveBeenCalledOnce());
  expect(props.review).toHaveBeenCalledTimes(2);
  expect(props.review.mock.calls[0][0].command_id).toBe(
    props.review.mock.calls[1][0].command_id,
  );
  expect(props.start.mock.calls[0][0].command_id).toBe(
    props.review.mock.calls[0][0].command_id,
  );
});

it('cancels a pending approval without starting anything', async () => {
  const { props } = fixture();
  props.review.mockImplementation(pendingReview);
  await runCommand();
  await screen.findByText(/Approval is pending/);
  await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  await screen.findByText('Cancelled. Nothing was started.');
  expect(commandBox()).toBeEnabled();
  expect(runButton()).toBeEnabled();
  expect(props.start).not.toHaveBeenCalled();
});

it.each(['wrong-command', 'wrong-binding', 'missing-evidence', 'denied'])(
  'refuses mismatched, absent or denied approval authority: %s',
  async (variant) => {
    const { props } = fixture();
    props.review.mockImplementation(async (attempt) => ({
      ...scope,
      resource_revision: 'resource-1',
      command: variant === 'wrong-command' ? 'other command' : attempt.command,
      command_id: attempt.command_id,
      binding_id: variant === 'wrong-binding' ? 'other' : scope.binding_id,
      decision: variant === 'denied' ? 'denied' : 'approved',
      approval_id:
        variant === 'missing-evidence' || variant === 'denied'
          ? null
          : 'evidence',
    }));
    await runCommand();
    await waitFor(() => expect(props.review).toHaveBeenCalledOnce());
    await waitFor(() => expect(runButton()).toBeEnabled());
    expect(props.start).not.toHaveBeenCalled();
    if (variant === 'denied')
      expect(screen.getByText(/was not approved/)).toBeInTheDocument();
  },
);

it('reviews an edited command under a new identity', async () => {
  const { props } = fixture();
  props.review.mockImplementationOnce(pendingReview);
  await runCommand();
  await screen.findByText(/Approval is pending/);
  await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  fireEvent.change(commandBox(), { target: { value: 'python other.py' } });
  await userEvent.click(runButton());
  await waitFor(() => expect(props.start).toHaveBeenCalledOnce());
  const [first, second] = props.review.mock.calls.map((call) => call[0]);
  expect(second.command).toBe('python other.py');
  expect(second.command_id).not.toBe(first.command_id);
  expect(props.start.mock.calls[0][0].command).toBe('python other.py');
});

it('preserves draft and pending identity through a real unmount with the same injected session', async () => {
  const { props, view, session } = fixture();
  props.review.mockImplementationOnce(pendingReview);
  await runCommand();
  await screen.findByText(/Approval is pending/);
  const id = session.getSnapshot().attempt?.command_id;
  view.unmount();
  render(<WorkspaceProcesses {...props} />);
  await waitFor(() => expect(props.load).toHaveBeenCalledTimes(2));
  expect(commandBox()).toHaveValue('python check.py');
  expect(session.getSnapshot().attempt?.command_id).toBe(id);
  expect(screen.getByRole('button', { name: 'Check approval' })).toBeEnabled();
  expect(props.start).not.toHaveBeenCalled();
});

it('retains uncertain Start across remount and retries only the original approved command', async () => {
  const { props, view, session } = fixture();
  props.start.mockRejectedValueOnce(new TypeError('lost response'));
  await runCommand();
  await screen.findByText(/couldn't confirm the command started/);
  const original = session.getSnapshot().attempt;
  view.unmount();
  render(<WorkspaceProcesses {...props} />);
  await waitFor(() => expect(props.load).toHaveBeenCalledTimes(2));
  expect(commandBox()).toBeDisabled();
  await userEvent.click(
    screen.getByRole('button', { name: 'Retry original Start' }),
  );
  await waitFor(() => expect(props.start).toHaveBeenCalledTimes(2));
  expect(props.start.mock.calls[1][0].command_id).toBe(original?.command_id);
  expect(props.start.mock.calls[1][0].snapshot).toEqual(original?.snapshot);
  expect(props.start.mock.calls[1][1]).toEqual(original?.review);
  expect(props.review).toHaveBeenCalledOnce();
});

it('keeps Stop usable while Start is pending and rejects a late response over confirmed cleanup', async () => {
  const pending = deferred<WorkspaceProcessInfo>();
  const { props, session } = fixture();
  props.start.mockReturnValueOnce(pending.promise);
  props.stop.mockImplementation(async (id: string) => ({
    ...process,
    process_id: id,
    command_id: id,
    state: 'exited',
    quiesced: true,
  }));
  await runCommand();
  await waitFor(() => expect(props.start).toHaveBeenCalledOnce());
  const id = session.getSnapshot().attempt!.command_id;
  const stop = screen.getByRole('button', { name: `Stop ${id}` });
  expect(stop).toBeEnabled();
  await userEvent.click(stop);
  await screen.findByText(
    'Process stopped. Its workspace writer has been released.',
  );
  await act(async () => pending.reject(new Error('late network error')));
  expect(session.getSnapshot().processes[0].quiesced).toBe(true);
  expect(session.getSnapshot().attempt?.uncertain).toBe(false);
  expect(runButton()).toBeEnabled();
});

it('keeps a requested Stop disabled while asynchronous cleanup is pending', async () => {
  const { props } = fixture({
    load: vi.fn().mockResolvedValue({ ...snapshot, processes: [process] }),
  });
  const stop = await screen.findByRole('button', {
    name: `Stop ${process.process_id}`,
  });
  await userEvent.click(stop);
  await screen.findByText(
    'Stop requested. The workspace writer remains held until cleanup is confirmed.',
  );
  expect(stop).toBeDisabled();
  expect(
    screen.getByRole('button', {
      name: `Recover and stop ${process.process_id}`,
    }),
  ).toBeEnabled();
  expect(props.stop).toHaveBeenCalledOnce();
  expect(props.recover).not.toHaveBeenCalled();
});

it('recovers the same uncertain process ID after reopen without sending another Start', async () => {
  const { props, view, session } = fixture();
  props.start.mockRejectedValueOnce(new Error('unknown outcome'));
  await runCommand();
  await screen.findByText(/couldn't confirm the command started/);
  const id = session.getSnapshot().attempt!.command_id;
  view.unmount();
  render(<WorkspaceProcesses {...props} />);
  await userEvent.click(
    screen.getByRole('button', { name: `Recover and stop ${id}` }),
  );
  await screen.findByText(
    'Process stopped. Its workspace writer has been released.',
  );
  expect(props.recover).toHaveBeenCalledWith(id);
  expect(props.start).toHaveBeenCalledOnce();
  expect(runButton()).toBeEnabled();
});

it('runs a detected check through the same review and shows its state', async () => {
  const { props } = fixture({
    checks: [{ label: 'pytest', kind: 'test', command: 'python -m pytest' }],
  });
  const run = await screen.findByRole('button', { name: 'Run pytest' });
  await waitFor(() => expect(run).toBeEnabled());
  expect(props.review).not.toHaveBeenCalled();
  await userEvent.click(run);
  await waitFor(() => expect(props.start).toHaveBeenCalledOnce());
  expect(props.review.mock.calls[0][0].command).toBe('python -m pytest');
  expect(props.start.mock.calls[0][1].approval_id).toBe('server-evidence');
  const checks = screen.getByRole('region', { name: 'Detected checks' });
  expect(within(checks).getByRole('button', { name: 'Running' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Run pytest' })).toBeDisabled();
});

it('tails a running process into one console and reads its last lines once it stops', async () => {
  vi.useFakeTimers();
  try {
    let running = true;
    const { props } = fixture({
      load: async () => ({
        ...snapshot,
        processes: [
          running
            ? process
            : { ...process, state: 'exited', exit_code: 0, quiesced: true },
        ],
      }),
    });
    props.output.mockImplementation(async (id, cursor) => ({
      process_id: id,
      entries:
        cursor < 3
          ? [
              {
                sequence: cursor + 1,
                channel: cursor === 1 ? 'stderr' : 'stdout',
                text: `line-${cursor}`,
              },
            ]
          : [],
      next_cursor: Math.min(cursor + 1, 3),
      truncated: false,
      quiesced: !running,
    }));
    await act(async () => {});
    await act(async () => {
      fireEvent.click(
        screen.getByRole('button', { name: 'View output of python check.py' }),
      );
    });
    const console_ = screen.getByRole('region', { name: 'Process output' });
    expect(console_).toHaveTextContent('line-0');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(400);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(console_).toHaveTextContent('line-0line-1line-2');
    expect(console_.querySelector('[data-channel="stderr"]')).toHaveTextContent(
      'line-1',
    );
    running = false;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    const reads = props.output.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(props.output.mock.calls.length).toBe(reads);
    expect(
      props.output.mock.calls.map((call) => call[1]).every((c) => c <= 3),
    ).toBe(true);
    expect(console_.textContent?.match(/line-0/g)).toHaveLength(1);
    expect(screen.getAllByText('Passed')).not.toHaveLength(0);
    expect(props.start).not.toHaveBeenCalled();
  } finally {
    vi.useRealTimers();
  }
});

it('keeps only the latest console lines and says earlier ones were dropped', async () => {
  const { props } = fixture({
    load: vi.fn().mockResolvedValue({
      ...snapshot,
      processes: [{ ...process, state: 'exited', quiesced: true }],
    }),
  });
  props.output.mockResolvedValueOnce({
    process_id: process.process_id,
    entries: [{ sequence: 9, channel: 'stdout', text: 'latest' }],
    next_cursor: 9,
    truncated: true,
    quiesced: true,
  });
  await userEvent.click(
    await screen.findByRole('button', {
      name: 'View output of python check.py',
    }),
  );
  await screen.findByText('latest');
  expect(screen.getByText(/Earlier output is no longer kept/)).toBeVisible();
  expect(props.output).toHaveBeenCalledOnce();
});

it('aborts passive reads on hide and never automatically replays commands on reopen', async () => {
  const pending = deferred<WorkspaceProcessSnapshot>();
  const { props, view } = fixture({
    load: vi.fn().mockReturnValue(pending.promise),
  });
  await waitFor(() => expect(props.load).toHaveBeenCalledOnce());
  const signal = props.load.mock.calls[0][0];
  view.rerender(<WorkspaceProcesses {...props} visible={false} />);
  expect(signal.aborted).toBe(true);
  await act(async () => pending.resolve(snapshot));
  expect(
    screen.queryByRole('region', { name: 'Workspace processes' }),
  ).not.toBeInTheDocument();
  view.rerender(<WorkspaceProcesses {...props} />);
  await waitFor(() => expect(props.load).toHaveBeenCalledTimes(2));
  expect(props.start).not.toHaveBeenCalled();
});

it('keeps revoked drafts inert and never exposes them under another binding', async () => {
  const { props, view, session } = fixture();
  await type();
  act(() => session.revoke());
  expect(commandBox()).toHaveValue('python check.py');
  expect(runButton()).toBeDisabled();
  view.rerender(
    <WorkspaceProcesses
      {...props}
      scope={{ ...scope, binding_id: 'replacement' }}
    />,
  );
  expect(
    screen.getByText('Workspace process access changed'),
  ).toBeInTheDocument();
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  expect(props.review).not.toHaveBeenCalled();
  expect(props.start).not.toHaveBeenCalled();
});

it('requires re-review when the resource revision changes before execution', async () => {
  const { props, view } = fixture();
  props.review.mockImplementationOnce(pendingReview);
  await runCommand();
  await screen.findByText(/Approval is pending/);
  props.load.mockResolvedValue({
    ...snapshot,
    resource_revision: 'resource-2',
  });
  view.rerender(
    <WorkspaceProcesses {...props} resourceRevision="resource-2" />,
  );
  await waitFor(() => expect(props.load).toHaveBeenCalledTimes(2));
  expect(props.start).not.toHaveBeenCalled();
  const oldId = props.review.mock.calls[0][0].command_id;
  await userEvent.click(screen.getByRole('button', { name: 'Check approval' }));
  await waitFor(() => expect(props.start).toHaveBeenCalledOnce());
  expect(props.review.mock.calls[1][0].snapshot.resource_revision).toBe(
    'resource-2',
  );
  expect(props.review.mock.calls[1][0].command_id).not.toBe(oldId);
  expect(props.start.mock.calls[0][0].command_id).not.toBe(oldId);
});

it('ignores a late approval for a different binding', async () => {
  const pending = deferred<WorkspaceProcessReview>();
  const { props } = fixture();
  props.review.mockReturnValueOnce(pending.promise);
  await runCommand();
  const attempt = props.review.mock.calls[0][0];
  await act(async () =>
    pending.resolve({
      ...scope,
      binding_revision: 'replacement',
      resource_revision: 'resource-1',
      command: attempt.command,
      command_id: attempt.command_id,
      decision: 'approved',
      approval_id: 'foreign',
    }),
  );
  await screen.findByText(/Approval could not be confirmed/);
  expect(props.start).not.toHaveBeenCalled();
});

it('shows incomplete cleanup truthfully and keeps exact recovery available after an error', async () => {
  const { props } = fixture({
    load: vi.fn().mockResolvedValue({
      ...snapshot,
      processes: [
        {
          ...process,
          state: 'cleanup_incomplete',
          code: 'process_cleanup_incomplete',
        },
      ],
    }),
  });
  props.recover.mockRejectedValueOnce(new Error('private connection details'));
  const processes = await screen.findByRole('region', { name: 'Processes' });
  expect(within(processes).getAllByText('Cleanup incomplete')).not.toHaveLength(
    0,
  );
  await userEvent.click(
    screen.getByRole('button', {
      name: `Recover and stop ${process.process_id}`,
    }),
  );
  await screen.findByText(/Cleanup could not be confirmed/);
  expect(
    screen.queryByText(/private connection details/),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: `Stop ${process.process_id}` }),
  ).toBeEnabled();
  await userEvent.click(
    screen.getByRole('button', {
      name: `Recover and stop ${process.process_id}`,
    }),
  );
  await screen.findByText(
    'Process stopped. Its workspace writer has been released.',
  );
  expect(props.recover.mock.calls).toEqual([
    [process.process_id],
    [process.process_id],
  ]);
  expect(props.start).not.toHaveBeenCalled();
});

it('rejects an output response for another process and never renders its content', async () => {
  const { props } = fixture({
    load: vi.fn().mockResolvedValue({
      ...snapshot,
      processes: [{ ...process, state: 'exited', quiesced: true }],
    }),
  });
  props.output.mockResolvedValueOnce({
    process_id: 'foreign',
    entries: [
      { sequence: 1, channel: 'stdout', text: 'Foreign private content' },
    ],
    next_cursor: 1,
    quiesced: false,
    truncated: false,
  });
  await userEvent.click(
    await screen.findByRole('button', {
      name: 'View output of python check.py',
    }),
  );
  await screen.findByText(/Output is unavailable/);
  expect(screen.queryByText('Foreign private content')).not.toBeInTheDocument();
});

it('reaches every saved recovery page without accumulating historical owners or starting commands', async () => {
  const first = Array.from({ length: 32 }, (_, index) => ({
    ...process,
    process_id: `saved-${index}`,
    command_id: `saved-${index}`,
    command: '',
    state: 'cleanup_incomplete' as const,
  }));
  const last = {
    ...first[0],
    process_id: 'saved-last',
    command_id: 'saved-last',
  };
  const loadRecovery = vi
    .fn<NonNullable<WorkspaceProcessesProps['loadRecovery']>>()
    .mockImplementation(async (cursor) =>
      cursor
        ? { items: [last], next_cursor: null }
        : { items: first, next_cursor: 'page-two' },
    );
  const { props, session } = fixture({ loadRecovery });
  await userEvent.click(
    screen.getByRole('button', { name: 'Load saved process recovery' }),
  );
  await screen.findByRole('button', { name: 'Recover and stop saved-31' });
  expect(session.getSnapshot().processes).toHaveLength(0);
  expect(session.getSnapshot().recovery?.items).toHaveLength(32);
  await userEvent.click(
    screen.getByRole('button', { name: 'Next recovery page' }),
  );
  await screen.findByRole('button', { name: 'Recover and stop saved-last' });
  expect(
    screen.queryByRole('button', { name: 'Recover and stop saved-0' }),
  ).not.toBeInTheDocument();
  expect(session.getSnapshot().recovery?.items).toHaveLength(1);
  expect(
    screen.getByRole('button', { name: 'Next recovery page' }),
  ).toBeDisabled();
  await userEvent.click(
    screen.getByRole('button', { name: 'Recover and stop saved-last' }),
  );
  await screen.findByText('Cleanup confirmed', { exact: false });
  expect(props.recover).toHaveBeenCalledWith('saved-last');
  expect(session.getSnapshot().processes).toHaveLength(0);
  await userEvent.click(
    screen.getByRole('button', { name: 'First recovery page' }),
  );
  await screen.findByRole('button', { name: 'Recover and stop saved-0' });
  expect(loadRecovery.mock.calls.map((args) => args[0])).toEqual([
    undefined,
    'page-two',
    undefined,
  ]);
  expect(props.start).not.toHaveBeenCalled();
  expect(props.stop).not.toHaveBeenCalled();
});

it('keeps an in-flight recovery owner on the current bounded page and aborts hidden page reads', async () => {
  const page = deferred<{
    items: WorkspaceProcessInfo[];
    next_cursor: string | null;
  }>();
  const loadRecovery = vi
    .fn<NonNullable<WorkspaceProcessesProps['loadRecovery']>>()
    .mockReturnValueOnce(page.promise)
    .mockResolvedValue({ items: [process], next_cursor: 'next' });
  const { props, session, view } = fixture({ loadRecovery });
  await userEvent.click(
    screen.getByRole('button', { name: 'Load saved process recovery' }),
  );
  view.rerender(<WorkspaceProcesses {...props} visible={false} />);
  expect(loadRecovery.mock.calls[0][1].aborted).toBe(true);
  await act(async () =>
    page.resolve({
      items: [{ ...process, command: 'late private content' }],
      next_cursor: null,
    }),
  );
  expect(session.getSnapshot().recovery).toBeNull();
  view.rerender(<WorkspaceProcesses {...props} />);
  await userEvent.click(
    screen.getByRole('button', { name: 'Load saved process recovery' }),
  );
  await screen.findByRole('button', { name: 'Recover and stop owned-id' });
  const pending = deferred<WorkspaceProcessInfo>();
  props.recover.mockReturnValueOnce(pending.promise);
  await userEvent.click(
    screen.getByRole('button', { name: 'Recover and stop owned-id' }),
  );
  expect(
    screen.getByRole('button', { name: 'Next recovery page' }),
  ).toBeDisabled();
  expect(
    screen.getByRole('button', { name: 'First recovery page' }),
  ).toBeDisabled();
  await act(async () =>
    pending.resolve({ ...process, state: 'exited', quiesced: true }),
  );
  expect(
    screen.getByRole('button', { name: 'Next recovery page' }),
  ).toBeEnabled();
});

it('rejects oversized recovery pages without replacing the last bounded page', async () => {
  const loadRecovery = vi
    .fn<NonNullable<WorkspaceProcessesProps['loadRecovery']>>()
    .mockResolvedValueOnce({ items: [process], next_cursor: 'next' })
    .mockResolvedValueOnce({
      items: Array.from({ length: 33 }, (_, index) => ({
        ...process,
        process_id: `bad-${index}`,
      })),
      next_cursor: null,
    });
  const { session } = fixture({ loadRecovery });
  await userEvent.click(
    screen.getByRole('button', { name: 'Load saved process recovery' }),
  );
  await screen.findByRole('button', { name: 'Recover and stop owned-id' });
  await userEvent.click(
    screen.getByRole('button', { name: 'Next recovery page' }),
  );
  await screen.findByText(/Saved process recovery could not be loaded/);
  expect(session.getSnapshot().recovery?.items).toEqual([process]);
  expect(screen.queryByText('bad-32')).not.toBeInTheDocument();
});

it('does not replace the visible owner when a page response races an explicit recovery', async () => {
  const page = deferred<{
    items: WorkspaceProcessInfo[];
    next_cursor: string | null;
  }>();
  const loadRecovery = vi
    .fn<NonNullable<WorkspaceProcessesProps['loadRecovery']>>()
    .mockResolvedValueOnce({ items: [process], next_cursor: 'next' })
    .mockReturnValueOnce(page.promise);
  const { props, session } = fixture({ loadRecovery });
  await userEvent.click(
    screen.getByRole('button', { name: 'Load saved process recovery' }),
  );
  await screen.findByRole('button', { name: 'Recover and stop owned-id' });
  await userEvent.click(
    screen.getByRole('button', { name: 'Next recovery page' }),
  );
  const pending = deferred<WorkspaceProcessInfo>();
  props.recover.mockReturnValueOnce(pending.promise);
  await userEvent.click(
    screen.getByRole('button', { name: 'Recover and stop owned-id' }),
  );
  await act(async () =>
    page.resolve({
      items: [{ ...process, process_id: 'later-owner' }],
      next_cursor: null,
    }),
  );
  expect(session.getSnapshot().recovery?.items).toEqual([process]);
  await act(async () =>
    pending.resolve({ ...process, state: 'exited', quiesced: true }),
  );
  expect(session.getSnapshot().recovery?.items[0].quiesced).toBe(true);
  expect(screen.queryByText('later-owner')).not.toBeInTheDocument();
});

it('late actual component Start settlement cannot resurrect a disposed authentication session', async () => {
  const { props, session } = fixture();
  const pending = deferred<WorkspaceProcessInfo>();
  props.start.mockReturnValueOnce(pending.promise);
  await runCommand();
  await waitFor(() => expect(props.start).toHaveBeenCalledOnce());
  const attempt = props.start.mock.calls[0][0];
  act(() => session.dispose());
  await act(async () =>
    pending.resolve({
      ...process,
      process_id: attempt.command_id,
      command_id: attempt.command_id,
      command: attempt.command,
    }),
  );
  expect(session.getSnapshot()).toMatchObject({
    draft: '',
    attempt: null,
    processes: [],
    snapshot: null,
    log: null,
    revoked: true,
  });
  expect(commandBox()).toHaveValue('');
  expect(
    screen.queryByRole('region', { name: 'Processes' }),
  ).not.toBeInTheDocument();
});

it('observes asynchronous cleanup while visible and stops reads after confirmation or hiding', async () => {
  vi.useFakeTimers();
  try {
    const { props, session, view } = fixture({
      load: async () => ({
        ...snapshot,
        processes: [{ ...process, state: 'stopping' }],
      }),
    });
    await act(async () => {});
    expect(props.load).toHaveBeenCalledTimes(1);
    expect(
      screen.getByRole('button', { name: `Stop ${process.process_id}` }),
    ).toBeDisabled();
    const pending = deferred<WorkspaceProcessSnapshot>();
    props.load.mockReturnValueOnce(pending.promise);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(props.load).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(props.load).toHaveBeenCalledTimes(2);
    await act(async () =>
      pending.resolve({
        ...snapshot,
        processes: [{ ...process, state: 'exited', quiesced: true }],
      }),
    );
    expect(session.getSnapshot().processes[0].quiesced).toBe(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(props.load).toHaveBeenCalledTimes(2);
    act(() =>
      session.update((current) => ({ ...current, processes: [process] })),
    );
    view.rerender(<WorkspaceProcesses {...props} visible={false} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(props.load).toHaveBeenCalledTimes(2);
    expect(props.start).not.toHaveBeenCalled();
    expect(props.stop).not.toHaveBeenCalled();
    expect(props.recover).not.toHaveBeenCalled();
  } finally {
    vi.useRealTimers();
  }
});
