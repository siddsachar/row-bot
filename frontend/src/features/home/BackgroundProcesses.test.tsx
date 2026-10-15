import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import type { BackgroundProcessView } from '../../api/types';
import { OverlayProvider } from '../../ui/overlays';
import BackgroundProcesses, {
  type BackgroundProcessesIo,
} from './BackgroundProcesses';

function view(patch: Partial<BackgroundProcessView>): BackgroundProcessView {
  return {
    process_id: 'proc-00000001',
    conversation_id: 'conversation-a',
    conversation_title: 'Fix the login page',
    command: 'npm run dev',
    name: 'Dev server',
    folder: '/code/site',
    kind: 'process',
    status: 'running',
    exit_code: null,
    started_at: '2026-10-14T10:00:00+00:00',
    ended_at: null,
    runtime_seconds: 192,
    local_urls: ['http://localhost:5173'],
    pid: 4321,
    stopped_by: '',
    env_names: [],
    output_size: 0,
    ...patch,
  };
}

function setup(items: BackgroundProcessView[]) {
  const current = [...items];
  const io = {
    load: vi.fn(async () => ({
      items: [...current],
      running: current.filter((item) => item.status === 'running').length,
      limit_per_conversation: 5,
      limit_total: 12,
    })),
    output: vi.fn(async () => ({
      process_id: 'proc-00000001',
      text: '',
      start: 0,
      end: 0,
      size: 0,
      earlier_dropped: false,
      running: true,
    })),
    stop: vi.fn(async (id: string) => {
      const index = current.findIndex((item) => item.process_id === id);
      current[index] = {
        ...current[index],
        status: 'stopped',
        stopped_by: 'you',
      };
      return current[index];
    }),
    openConversation: vi.fn(),
  } satisfies BackgroundProcessesIo;
  render(
    <OverlayProvider>
      <BackgroundProcesses io={io} />
    </OverlayProvider>,
  );
  return io;
}

it('lists every running process with its conversation and a Stop for each', async () => {
  const io = setup([
    view({}),
    view({
      process_id: 'proc-00000002',
      conversation_id: 'conversation-b',
      conversation_title: 'Nightly build',
      name: '',
      command: 'npm run build',
      kind: 'command',
      local_urls: [],
      runtime_seconds: 30,
    }),
    view({
      process_id: 'proc-00000003',
      name: '',
      command: 'pytest -q',
      status: 'failed',
      exit_code: 1,
      runtime_seconds: 12,
    }),
  ]);

  const running = await screen.findByRole('list', {
    name: 'Running background processes',
  });
  const rows = within(running).getAllByRole('listitem');
  expect(rows).toHaveLength(2);
  expect(rows[0]).toHaveTextContent('Dev server');
  expect(rows[0]).toHaveTextContent('Running · 3m 12s');
  expect(rows[0]).toHaveTextContent('Fix the login page · localhost:5173');
  expect(rows[1]).toHaveTextContent('npm run build');
  expect(rows[1]).toHaveTextContent('Nightly build');
  expect(screen.getByText('2 running')).toBeInTheDocument();
  // Finished ones fold under Recently finished; no exit code as the main text.
  const finished = screen.getByRole('list', {
    name: 'Recently finished background processes',
  });
  expect(finished).toHaveTextContent('Failed after 12s');
  expect(
    within(finished).queryByRole('button', { name: /^Stop/ }),
  ).not.toBeInTheDocument();

  fireEvent.click(
    within(rows[0]).getByRole('button', { name: 'Stop Dev server' }),
  );

  expect(await screen.findByText(/Stopped by you after/)).toBeInTheDocument();
  expect(io.stop).toHaveBeenCalledWith('proc-00000001');
});

it('stops them all after one confirmation', async () => {
  const io = setup([
    view({}),
    view({ process_id: 'proc-00000002', name: 'Watcher' }),
  ]);
  fireEvent.click(await screen.findByRole('button', { name: 'Stop all' }));
  const dialog = await screen.findByRole('alertdialog', {
    name: 'Stop 2 background processes?',
  });

  fireEvent.click(within(dialog).getByRole('button', { name: 'Stop all' }));

  await vi.waitFor(() => expect(io.stop).toHaveBeenCalledTimes(2));
});

it('says what shows here while nothing runs', async () => {
  setup([]);
  expect(
    await screen.findByText(/Nothing is running in the background/),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Stop all' }),
  ).not.toBeInTheDocument();
});

it('opens the output and the conversation from a row', async () => {
  const io = setup([view({})]);
  const user = userEvent.setup();
  const running = await screen.findByRole('list', {
    name: 'Running background processes',
  });
  await user.click(
    within(running).getByRole('button', { name: 'More for Dev server' }),
  );
  await user.click(
    await screen.findByRole('menuitem', { name: 'Open conversation' }),
  );
  expect(io.openConversation).toHaveBeenCalledWith('conversation-a');

  await user.click(
    within(running).getByRole('button', { name: 'More for Dev server' }),
  );
  await user.click(
    await screen.findByRole('menuitem', { name: 'View output' }),
  );
  const drawer = await screen.findByRole('dialog', { name: 'Dev server' });
  expect(await within(drawer).findByRole('log')).toHaveTextContent(
    'Waiting for output…',
  );
  expect(within(drawer).getByText('Fix the login page')).toBeInTheDocument();
});
