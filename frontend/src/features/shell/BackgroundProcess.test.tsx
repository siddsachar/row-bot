import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type {
  BackgroundProcessOutput,
  BackgroundProcessView,
} from '../../api/types';
import { ProcessCard } from './BackgroundProcess';
import {
  ProcessIoContext,
  durationWords,
  processStatus,
  type ProcessIo,
} from './background-processes';
import { cardOf } from './TranscriptCards';
import { approvalQuestion, stepVerb } from './tool-activity';

function view(
  patch: Partial<BackgroundProcessView> = {},
): BackgroundProcessView {
  return {
    process_id: 'proc-1a2b3c4d',
    conversation_id: 'conversation-a',
    conversation_title: 'Fix the login page',
    command: 'npm run dev',
    name: '',
    folder: 'C:\\code\\site',
    kind: 'process',
    status: 'running',
    exit_code: null,
    started_at: '2026-10-14T10:00:00+00:00',
    ended_at: null,
    runtime_seconds: 72,
    local_urls: ['http://localhost:5173'],
    pid: 4321,
    stopped_by: '',
    env_names: ['PORT'],
    output_size: 40,
    ...patch,
  };
}

function io(
  current: BackgroundProcessView[],
  output = 'VITE ready\n  Local: http://localhost:5173/\n',
) {
  const page = (): BackgroundProcessOutput => ({
    process_id: 'proc-1a2b3c4d',
    text: output,
    start: 0,
    end: output.length,
    size: output.length,
    earlier_dropped: false,
    running: false,
  });
  return {
    load: vi.fn(async () => ({
      items: [...current],
      running: current.filter((item) => item.status === 'running').length,
      limit_per_conversation: 5,
      limit_total: 12,
    })),
    output: vi.fn(async () => page()),
    stop: vi.fn(async (id: string) => {
      const stopped = view({
        ...current[0],
        process_id: id,
        status: 'stopped',
        stopped_by: 'you',
        exit_code: 130,
        runtime_seconds: 80,
      });
      current.splice(0, current.length, stopped);
      return stopped;
    }),
  } satisfies ProcessIo;
}

function renderCard(processes: ProcessIo) {
  return render(
    <ProcessIoContext.Provider value={processes}>
      <ProcessCard processId="proc-1a2b3c4d" conversationId="conversation-a" />
    </ProcessIoContext.Provider>,
  );
}

afterEach(() => vi.useRealTimers());

it('follows a running process: what runs, for how long, where it serves', async () => {
  const processes = io([view()]);
  renderCard(processes);

  const card = await screen.findByRole('group', {
    name: 'Background process npm run dev: Running · 1m 12s',
  });
  expect(within(card).getByText('npm run dev')).toBeInTheDocument();
  expect(within(card).getByText('localhost:5173')).toBeInTheDocument();
  expect(
    within(card).getByRole('button', { name: 'View output' }),
  ).toBeInTheDocument();
  expect(processes.load).toHaveBeenCalledWith(
    'conversation-a',
    expect.anything(),
  );
  // No process number or exit code as the main text.
  expect(card).not.toHaveTextContent('4321');
});

it('opens the output and stops it with everything it started', async () => {
  const processes = io([view({ name: 'Dev server' })]);
  renderCard(processes);
  const card = await screen.findByRole('group', {
    name: /Background process Dev server/,
  });

  fireEvent.click(within(card).getByRole('button', { name: 'View output' }));
  const drawer = await screen.findByRole('dialog', { name: 'Dev server' });
  expect(
    await within(drawer).findByRole('log', { name: 'Output' }),
  ).toHaveTextContent('VITE ready');
  expect(processes.output).toHaveBeenCalledWith(
    'proc-1a2b3c4d',
    0,
    65536,
    expect.anything(),
  );
  fireEvent.click(within(drawer).getByRole('button', { name: 'Close output' }));

  fireEvent.click(within(card).getByRole('button', { name: 'Stop' }));

  expect(
    await screen.findByRole('group', {
      name: 'Background process Dev server: Stopped by you after 1m 20s',
    }),
  ).toBeInTheDocument();
  expect(processes.stop).toHaveBeenCalledWith('proc-1a2b3c4d');
  expect(
    screen.queryByRole('button', { name: 'Stop' }),
  ).not.toBeInTheDocument();
});

it('settles on how it ended, with the exit code only in Details', async () => {
  renderCard(
    io(
      [
        view({
          status: 'failed',
          exit_code: 1,
          runtime_seconds: 12,
          local_urls: [],
        }),
      ],
      'Error: port in use\n',
    ),
  );

  const card = await screen.findByRole('group', {
    name: 'Background process npm run dev: Failed after 12s',
  });
  expect(
    within(card).queryByRole('button', { name: 'Stop' }),
  ).not.toBeInTheDocument();
  expect(card).not.toHaveTextContent('exit');
  fireEvent.click(within(card).getByRole('button', { name: 'View output' }));
  const drawer = await screen.findByRole('dialog', { name: 'npm run dev' });
  expect(await within(drawer).findByRole('log')).toHaveTextContent(
    'Error: port in use',
  );
  expect(within(drawer).getByText('Exit code')).toBeInTheDocument();
});

it('says so when the process is no longer listed', async () => {
  renderCard(io([]));
  expect(
    await screen.findByText('This background process is no longer listed.'),
  ).toBeInTheDocument();
});

it('keeps counting while it runs, between reads too', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const started = Date.now() - 5000;
  const processes = io([view()]);
  // The server counts from when it started; the card counts on between reads.
  processes.load.mockImplementation(async () => ({
    items: [view({ runtime_seconds: (Date.now() - started) / 1000 })],
    running: 1,
    limit_per_conversation: 5,
    limit_total: 12,
  }));
  renderCard(processes);
  await screen.findByRole('group', { name: /Running · [5-6]s/ });

  await act(async () => {
    vi.advanceTimersByTime(3000);
  });

  await waitFor(() =>
    expect(
      screen.getByRole('group', { name: /Running · [8-9]s/ }),
    ).toBeInTheDocument(),
  );
});

it('names its states in plain words', () => {
  expect(durationWords(5)).toBe('5s');
  expect(durationWords(192)).toBe('3m 12s');
  expect(durationWords(3840)).toBe('1h 4m');
  expect(processStatus(view({ status: 'finished' }), 123).label).toBe(
    'Finished in 2m 3s',
  );
  expect(
    processStatus(view({ status: 'failed', exit_code: null }), 1).label,
  ).toBe("Couldn't start");
  expect(
    processStatus(view({ status: 'stopped', stopped_by: 'quit' }), 60).label,
  ).toBe('Stopped when Row-Bot closed after 1m');
  expect(
    processStatus(view({ status: 'stopped', stopped_by: 'agent' }), 60).label,
  ).toBe('Stopped by Row-Bot after 1m');
});

it('reads the step that left a process running as its card', () => {
  expect(
    cardOf({
      kind: 'background_process',
      process_id: 'proc-1a2b3c4d',
    } as Parameters<typeof cardOf>[0]),
  ).toEqual({ kind: 'process', processId: 'proc-1a2b3c4d' });
  expect(approvalQuestion('start_process')).toBe('Start a background process?');
  expect(stepVerb('check_process', 'succeeded')).toBe(
    'Checked a background process',
  );
});

it('keeps the output open when the settled card replaces the live one', async () => {
  const processes = io([view({ name: 'Dev server' })]);
  const first = renderCard(processes);
  const card = await screen.findByRole('group', {
    name: /Background process Dev server/,
  });
  fireEvent.click(within(card).getByRole('button', { name: 'View output' }));
  expect(
    await screen.findByRole('dialog', { name: 'Dev server' }),
  ).toBeInTheDocument();

  first.unmount();
  renderCard(processes);

  expect(
    await screen.findByRole('dialog', { name: 'Dev server' }),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Close output' }));
  await waitFor(() =>
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument(),
  );
});
