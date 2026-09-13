import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
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
    Pick<WorkspaceProcessesProps, 'load' | 'loadRecovery'>
  > = {},
) {
  const session = createWorkspaceProcessesSession(scope);
  const props = {
    scope,
    resourceRevision: 'resource-1',
    visible: true,
    session,
    loadRecovery: overrides.loadRecovery,
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
async function approve() {
  await screen.findByText('No owned processes reported.');
  fireEvent.change(screen.getByRole('textbox', { name: 'Process command' }), {
    target: { value: 'python check.py' },
  });
  await userEvent.click(screen.getByRole('button', { name: 'Review command' }));
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: 'Start reviewed command' }),
    ).toBeEnabled(),
  );
}

it('loads passively, never dispatches on open, and requires exact server evidence plus explicit Start', async () => {
  const { props } = fixture();
  await approve();
  expect(props.start).not.toHaveBeenCalled();
  expect(props.stop).not.toHaveBeenCalled();
  await userEvent.click(
    screen.getByRole('button', { name: 'Start reviewed command' }),
  );
  await waitFor(() => expect(props.start).toHaveBeenCalledOnce());
  const [attempt, evidence] = props.start.mock.calls[0];
  expect(attempt.command).toBe('python check.py');
  expect(evidence.command_id).toBe(attempt.command_id);
  expect(evidence.approval_id).toBe('server-evidence');
  expect(
    screen.getByText('Workspace writer held until cleanup completes.'),
  ).toBeInTheDocument();
});

it('checks a pending original approval without executing or changing its ID', async () => {
  const { props } = fixture();
  props.review.mockImplementationOnce(async (attempt) => ({
    ...scope,
    resource_revision: 'resource-1',
    command: attempt.command,
    command_id: attempt.command_id,
    decision: 'pending',
    approval_id: null,
  }));
  await screen.findByText('No owned processes reported.');
  fireEvent.change(screen.getByRole('textbox'), {
    target: { value: 'python check.py' },
  });
  await userEvent.click(screen.getByRole('button', { name: 'Review command' }));
  await screen.findByText(/Approval is pending/);
  expect(
    screen.getByRole('button', { name: 'Start reviewed command' }),
  ).toBeDisabled();
  await userEvent.click(
    screen.getByRole('button', { name: 'Check original approval' }),
  );
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: 'Start reviewed command' }),
    ).toBeEnabled(),
  );
  expect(props.review.mock.calls[0][0].command_id).toBe(
    props.review.mock.calls[1][0].command_id,
  );
  expect(props.start).not.toHaveBeenCalled();
});

it.each(['wrong-command', 'wrong-binding', 'missing-evidence'])(
  'refuses mismatched or absent approval authority: %s',
  async (variant) => {
    const { props } = fixture();
    props.review.mockImplementation(async (attempt) => ({
      ...scope,
      resource_revision: 'resource-1',
      command: variant === 'wrong-command' ? 'other command' : attempt.command,
      command_id: attempt.command_id,
      binding_id: variant === 'wrong-binding' ? 'other' : scope.binding_id,
      decision: 'approved',
      approval_id: variant === 'missing-evidence' ? null : 'evidence',
    }));
    await screen.findByText('No owned processes reported.');
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'python check.py' },
    });
    await userEvent.click(
      screen.getByRole('button', { name: 'Review command' }),
    );
    await waitFor(() => expect(props.review).toHaveBeenCalledOnce());
    expect(
      screen.getByRole('button', { name: 'Start reviewed command' }),
    ).toBeDisabled();
    expect(props.start).not.toHaveBeenCalled();
  },
);

it('invalidates approval when the draft changes', async () => {
  const { props } = fixture();
  await approve();
  fireEvent.change(screen.getByRole('textbox'), {
    target: { value: 'python other.py' },
  });
  expect(
    screen.getByRole('button', { name: 'Start reviewed command' }),
  ).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Review command' })).toBeEnabled();
  expect(props.start).not.toHaveBeenCalled();
});

it('preserves draft and reviewed identity through a real unmount with the same injected session', async () => {
  const { props, view, session } = fixture();
  await approve();
  const id = session.getSnapshot().attempt?.command_id;
  view.unmount();
  render(<WorkspaceProcesses {...props} />);
  await waitFor(() => expect(props.load).toHaveBeenCalledTimes(2));
  expect(screen.getByRole('textbox')).toHaveValue('python check.py');
  expect(session.getSnapshot().attempt?.command_id).toBe(id);
  expect(props.start).not.toHaveBeenCalled();
});

it('retains uncertain Start across remount and retries only the original approved command', async () => {
  const { props, view, session } = fixture();
  props.start.mockRejectedValueOnce(new TypeError('lost response'));
  await approve();
  await userEvent.click(
    screen.getByRole('button', { name: 'Start reviewed command' }),
  );
  await screen.findByText(/Original Start is unconfirmed/);
  const original = session.getSnapshot().attempt;
  view.unmount();
  render(<WorkspaceProcesses {...props} />);
  await waitFor(() => expect(props.load).toHaveBeenCalledTimes(2));
  expect(screen.getByRole('textbox')).toBeDisabled();
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
  await approve();
  await userEvent.click(
    screen.getByRole('button', { name: 'Start reviewed command' }),
  );
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
  expect(screen.getByRole('button', { name: 'New command' })).toBeEnabled();
});

it('keeps a requested Stop disabled while asynchronous cleanup is pending', async () => {
  const { props } = fixture({
    load: vi.fn().mockResolvedValue({ ...snapshot, processes: [process] }),
  });
  await screen.findByText('Workspace writer held until cleanup completes.');
  const stop = screen.getByRole('button', {
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
  await approve();
  await userEvent.click(
    screen.getByRole('button', { name: 'Start reviewed command' }),
  );
  await screen.findByText(/Original Start is unconfirmed/);
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
  expect(screen.getByRole('button', { name: 'New command' })).toBeEnabled();
});

it('makes every retained output page reachable and keeps only one rendered page', async () => {
  const { props } = fixture({
    load: vi.fn().mockResolvedValue({ ...snapshot, processes: [process] }),
  });
  props.output.mockImplementation(async (id, cursor) => ({
    process_id: id,
    entries:
      cursor < 3
        ? [
            {
              sequence: cursor + 1,
              channel: cursor === 1 ? 'stderr' : 'stdout',
              text: `section-${cursor}`,
            },
          ]
        : [],
    next_cursor: cursor < 3 ? cursor + 1 : cursor,
    truncated: cursor === 0,
    quiesced: true,
  }));
  await screen.findByText(/python check.py/);
  await userEvent.click(screen.getByRole('button', { name: 'View output' }));
  await screen.findByText('section-0');
  await screen.findByText(/Earlier output is no longer retained/);
  await userEvent.click(screen.getByRole('button', { name: 'Next output' }));
  await screen.findByText('[stderr] section-1');
  expect(screen.queryByText('section-0')).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Next output' }));
  await screen.findByText('section-2');
  await userEvent.click(screen.getByRole('button', { name: 'Next output' }));
  await screen.findByText('No output in this section.');
  expect(screen.getByRole('button', { name: 'Next output' })).toBeDisabled();
  await userEvent.click(
    screen.getByRole('button', { name: 'Previous output' }),
  );
  await screen.findByText('section-2');
  await userEvent.click(
    screen.getByRole('button', { name: 'First retained output' }),
  );
  await screen.findByText('section-0');
  expect(props.start).not.toHaveBeenCalled();
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
  await approve();
  act(() => session.revoke());
  expect(screen.getByRole('textbox')).toHaveValue('python check.py');
  expect(
    screen.getByRole('button', { name: 'Start reviewed command' }),
  ).toBeDisabled();
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
  expect(props.start).not.toHaveBeenCalled();
});

it('requires re-review when the resource revision changes before execution', async () => {
  const { props, view } = fixture();
  await approve();
  props.load.mockResolvedValue({
    ...snapshot,
    resource_revision: 'resource-2',
  });
  view.rerender(
    <WorkspaceProcesses {...props} resourceRevision="resource-2" />,
  );
  await waitFor(() => expect(props.load).toHaveBeenCalledTimes(2));
  expect(
    screen.getByRole('button', { name: 'Start reviewed command' }),
  ).toBeDisabled();
  expect(props.start).not.toHaveBeenCalled();
  const oldId = props.review.mock.calls[0][0].command_id;
  await userEvent.click(
    screen.getByRole('button', { name: 'Review current revision' }),
  );
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: 'Start reviewed command' }),
    ).toBeEnabled(),
  );
  expect(props.review.mock.calls[1][0].snapshot.resource_revision).toBe(
    'resource-2',
  );
  expect(props.review.mock.calls[1][0].command_id).not.toBe(oldId);
});

it('ignores a late approval for a different binding', async () => {
  const pending = deferred<WorkspaceProcessReview>();
  const { props } = fixture();
  props.review.mockReturnValueOnce(pending.promise);
  await screen.findByText('No owned processes reported.');
  fireEvent.change(screen.getByRole('textbox'), {
    target: { value: 'python check.py' },
  });
  await userEvent.click(screen.getByRole('button', { name: 'Review command' }));
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
  expect(
    screen.getByRole('button', { name: 'Start reviewed command' }),
  ).toBeDisabled();
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
  await screen.findByText('Cleanup incomplete · workspace writer retained.');
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
    load: vi.fn().mockResolvedValue({ ...snapshot, processes: [process] }),
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
  await screen.findByText(/python check.py/);
  await userEvent.click(screen.getByRole('button', { name: 'View output' }));
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
  await approve();
  const pending = deferred<WorkspaceProcessInfo>();
  props.start.mockReturnValueOnce(pending.promise);
  await userEvent.click(
    screen.getByRole('button', { name: 'Start reviewed command' }),
  );
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
    revoked: true,
  });
  expect(screen.getByRole('textbox', { name: 'Process command' })).toHaveValue(
    '',
  );
  expect(screen.queryByText(/Original process ID:/)).not.toBeInTheDocument();
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
