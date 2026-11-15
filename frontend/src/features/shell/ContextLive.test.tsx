import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type {
  BackgroundProcessView,
  TaskSummary,
  WaitView,
} from '../../api/types';
import type { ClientPlatform } from '../../platform';
import { RuntimeContext } from '../../runtime';
import { ProcessIoContext, type ProcessIo } from './background-processes';
import { ContextLive } from './ContextLive';

const WAIT: WaitView = {
  wait_id: 'a1b2c3d4e5f6',
  conversation_id: 'here',
  conversation_title: 'Release',
  kind: 'check',
  label: 'CI on PR 590 is green',
  how: 'checks every 2 min',
  until: '2026-10-28T15:00',
  status: 'waiting',
};

const SERVER: BackgroundProcessView = {
  process_id: 'p1',
  conversation_id: 'here',
  conversation_title: 'Release',
  command: 'npm run dev',
  name: 'Dev server',
  folder: '',
  kind: 'process',
  status: 'running',
  exit_code: null,
  started_at: '2026-10-28T14:00:00Z',
  ended_at: null,
  runtime_seconds: 30,
  local_urls: [],
  pid: 4242,
  stopped_by: '',
  env_names: [],
  output_size: 0,
};

const TASK = {
  id: 'task1',
  name: 'Daily digest',
  conversation_id: 'here',
  schedule: null,
  at: null,
  enabled: true,
  last_status: 'completed',
  active_run: null,
  next_run: null,
} as unknown as TaskSummary;

function Where() {
  const location = useLocation();
  return <p data-testid="at">{location.pathname + location.search}</p>;
}

function setup({
  category = 'chat',
  startedFrom,
}: { category?: string; startedFrom?: string } = {}) {
  const snapshot = {
    conversations: [
      {
        id: 'here',
        title: 'Release',
        category,
        started_from: startedFrom ?? null,
      },
      { id: 'parent', title: 'Planning', category: 'chat' },
      {
        id: 'child',
        title: 'Draft the notes',
        category: 'chat',
        started_from: 'here',
      },
    ],
  };
  const controller = {
    getSnapshot: () => snapshot,
    subscribe: () => () => undefined,
    waits: vi.fn(async () => ({ schema_version: 1, items: [WAIT] })),
    stopWait: vi.fn(async () => ({ schema_version: 1, items: [] })),
    savedTasks: vi.fn(async () => ({ items: [TASK], next_cursor: null })),
  } as unknown as ClientController;
  const processes: ProcessIo = {
    load: vi.fn(async () => ({
      items: [SERVER],
      running: 1,
      limit_per_conversation: 5,
      limit_total: 20,
    })),
    output: vi.fn(),
    stop: vi.fn(async () => ({ ...SERVER, status: 'stopped' as const })),
  };
  render(
    <RuntimeContext.Provider
      value={{ controller, platform: {} as ClientPlatform }}
    >
      <ProcessIoContext.Provider value={processes}>
        <MemoryRouter initialEntries={['/conversations/here']}>
          <Routes>
            <Route
              path="*"
              element={
                <>
                  <ContextLive conversationId="here" />
                  <Where />
                </>
              }
            />
          </Routes>
        </MemoryRouter>
      </ProcessIoContext.Provider>
    </RuntimeContext.Provider>,
  );
  return { controller, processes };
}

it('lists what the conversation waits for and what runs for it, each with its Stop (5.3.1)', async () => {
  const { controller, processes } = setup();

  const waiting = await screen.findByRole('region', { name: 'Waiting' });
  expect(waiting).toHaveTextContent('CI on PR 590 is greenchecks every 2 min');
  const running = await screen.findByRole('region', { name: 'Running' });
  expect(running).toHaveTextContent('Dev serverRunning');

  await userEvent.click(
    within(running).getByRole('button', { name: 'Stop Dev server' }),
  );
  expect(processes.stop).toHaveBeenCalledWith('p1');
  await waitFor(() =>
    expect(screen.queryByRole('region', { name: 'Running' })).toBeNull(),
  );

  await userEvent.click(
    within(waiting).getByRole('button', { name: 'Stop waiting' }),
  );
  expect(controller.stopWait).toHaveBeenCalledWith('a1b2c3d4e5f6');
  // A chat is no workflow's: its workflows are never read.
  expect(controller.savedTasks).not.toHaveBeenCalled();
});

it("shows a workflow's chat its workflow, and opens its runs", async () => {
  setup({ category: 'workflow' });

  const workflow = await screen.findByRole('region', { name: 'Workflow' });
  expect(workflow).toHaveTextContent('Daily digest');
  expect(workflow).toHaveTextContent('Last run: Completed');
  await userEvent.click(
    within(workflow).getByRole('button', { name: 'Open its runs' }),
  );
  expect(screen.getByTestId('at')).toHaveTextContent(
    '/?tab=workflows&workflow=task1',
  );
});

it('links the conversation that started this one and the ones it started', async () => {
  setup({ startedFrom: 'parent' });

  const linked = await screen.findByRole('region', {
    name: 'Linked conversations',
  });
  expect(linked).toHaveTextContent('PlanningStarted this one');
  expect(linked).toHaveTextContent('Draft the notesStarted from here');
  await userEvent.click(
    within(linked).getByRole('button', { name: /Draft the notes/ }),
  );
  expect(screen.getByTestId('at')).toHaveTextContent('/conversations/child');
});
