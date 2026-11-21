import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import type {
  TaskRunPage,
  TaskRunReview,
  TaskSummary,
  TaskSummaryPage,
} from '../../api/types';
import { OverlayProvider } from '../../ui/overlays';
import { WorkspaceActionsContext } from '../shell/workspace-actions';
import PhoneWorkflows, { NEW_WORKFLOW_DRAFT } from './PhoneWorkflows';

const HOUR = 3_600_000;
const at = (offset: number) => new Date(Date.now() + offset).toISOString();

function workflow(extra: Partial<TaskSummary>): TaskSummary {
  return {
    id: 'wf',
    name: 'Workflow',
    description: '',
    icon: '',
    enabled: true,
    notify_only: false,
    step_count: 1,
    schedule: null,
    at: null,
    last_run: null,
    last_status: null,
    conversation_id: null,
    agent_profile_id: '',
    approval_mode: 'approve',
    ...extra,
  };
}

const WORKFLOWS = [
  workflow({
    id: 'brief',
    name: 'Morning Brief',
    schedule: 'daily:08:00',
    next_run: at(20 * HOUR),
    conversation_id: 'chat-brief',
  }),
  workflow({
    id: 'launch',
    name: 'Launch Summary',
    last_status: 'running',
    active_run: {
      id: 'run-launch',
      status: 'running',
      started_at: at(-2 * 60_000),
      steps_done: 1,
      steps_total: 3,
    },
  }),
  workflow({
    id: 'digest',
    name: 'Research Digest',
    schedule: 'weekly:fri:16:00',
    last_run: at(-2 * HOUR),
    last_status: 'failed',
  }),
  workflow({
    id: 'tidy',
    name: 'Weekly Tidy-up',
    schedule: 'weekly:sun:10:00',
    enabled: false,
  }),
  workflow({ id: 'garden', name: 'Garden Reminder', notify_only: true }),
];

function page(items: TaskSummary[]): TaskSummaryPage {
  return {
    schema_version: 1,
    revision: 'a'.repeat(64),
    items,
    total: items.length,
    next_cursor: null,
  };
}

function setup(items = WORKFLOWS, openAt: string | null = null) {
  // The saved workflows: a switch saves, and the next read says so.
  const saved = items.map((item) => ({ ...item }));
  const load = vi.fn(async () => page(saved));
  const onToggleEnabled = vi.fn(async (id: string, enabled: boolean) => {
    saved.find((item) => item.id === id)!.enabled = enabled;
  });
  const newChat = vi.fn();
  const review: TaskRunReview = {
    task_id: 'brief',
    task_revision: 'c'.repeat(64),
    policy_revision: 'd'.repeat(64),
    agent_profile_id: '',
    approval_mode: 'approve',
    notify_only: false,
    steps_total: 1,
    conversation_id: 'chat-brief',
  };
  const runs: TaskRunPage = {
    task_id: 'brief',
    revision: 'e'.repeat(64),
    items: [
      {
        id: 'run-1',
        task_id: 'brief',
        conversation_id: 'chat-brief',
        status: 'completed',
        started_at: at(-4 * HOUR),
        finished_at: at(-4 * HOUR + 60_000),
        steps_total: 1,
        steps_done: 1,
        notice: '3 items for today',
      },
    ],
    next_cursor: null,
    total: 1,
  };
  const io = {
    loadReview: vi.fn(async () => review),
    loadHistory: vi.fn(async () => runs),
    loadRun: vi.fn(),
    run: vi.fn(async () => ({
      run: { ...runs.items[0], id: 'run-2', status: 'running' },
      replayed: false,
    })),
    stop: vi.fn(),
    openConversation: vi.fn(),
  };
  function Page() {
    const [slot, setSlot] = useState<HTMLElement | null>(null);
    const [open, setOpen] = useState<{ id: string; name: string } | null>(
      openAt ? { id: openAt, name: '' } : null,
    );
    return (
      <MemoryRouter>
        <OverlayProvider>
          <header ref={setSlot} aria-label="Phone header" />
          <WorkspaceActionsContext.Provider
            value={{ resetLayout: vi.fn(), newChat, phoneHeaderSlot: slot }}
          >
            <PhoneWorkflows
              load={load}
              onToggleEnabled={onToggleEnabled}
              io={io}
              openId={open?.id ?? null}
              openName={open?.name ?? ''}
              onOpen={(id, name) => setOpen({ id, name })}
              onClose={() => setOpen(null)}
            />
          </WorkspaceActionsContext.Provider>
        </OverlayProvider>
      </MemoryRouter>
    );
  }
  render(<Page />);
  return { load, onToggleEnabled, newChat, io };
}

const rows = () =>
  within(screen.getByRole('list', { name: 'Workflows' })).getAllByRole(
    'listitem',
  );

it('lists each workflow with its status line and switch, under chips that count them', async () => {
  setup();
  await screen.findByRole('list', { name: 'Workflows' });
  expect(rows().map((row) => row.textContent)).toEqual([
    expect.stringMatching(/^Morning BriefEvery day at .* · next in 20 hours$/),
    'Launch SummaryRunning · message 2 of 3',
    expect.stringMatching(/^Research DigestFailed 2 hours ago · Every Friday/),
    expect.stringMatching(/^Weekly Tidy-upOff · Every Sunday/),
    'Garden ReminderRun when asked',
  ]);
  expect(
    screen.getByRole('switch', { name: 'Turn on workflow: Weekly Tidy-up' }),
  ).not.toBeChecked();
  expect(screen.getAllByRole('radio').map((chip) => chip.textContent)).toEqual([
    'All 5',
    'Running 1',
    'Failed 1',
    'Off 1',
  ]);
  // The page title is for screen readers; the header names the place.
  expect(
    screen.getByRole('heading', { level: 1, name: 'Workflows' }),
  ).toBeInTheDocument();

  await userEvent.click(screen.getByRole('radio', { name: 'Failed 1' }));
  expect(rows().map((row) => row.textContent)).toEqual([
    expect.stringContaining('Research Digest'),
  ]);
  await userEvent.click(screen.getByRole('radio', { name: 'Off 1' }));
  expect(rows().map((row) => row.textContent)).toEqual([
    expect.stringContaining('Weekly Tidy-up'),
  ]);
  await userEvent.click(screen.getByRole('radio', { name: 'Running 1' }));
  expect(rows().map((row) => row.textContent)).toEqual([
    expect.stringContaining('Launch Summary'),
  ]);
});

it('says what a chip has when it has nothing', async () => {
  setup([WORKFLOWS[0]]);
  await screen.findByRole('list', { name: 'Workflows' });
  await userEvent.click(screen.getByRole('radio', { name: 'Failed 0' }));
  expect(screen.getByText('Nothing has failed')).toBeVisible();
});

it('turns a workflow off from its row through the saved-workflow edit', async () => {
  const { onToggleEnabled } = setup();
  await screen.findByRole('list', { name: 'Workflows' });
  await userEvent.click(
    screen.getByRole('switch', { name: 'Turn off workflow: Morning Brief' }),
  );
  expect(onToggleEnabled).toHaveBeenCalledWith('brief', false);
  expect(
    await screen.findByRole('switch', {
      name: 'Turn on workflow: Morning Brief',
    }),
  ).not.toBeChecked();
  // The switch never opens the sheet.
  expect(screen.queryByRole('dialog')).toBeNull();
});

it('opens a workflow’s sheet from its row: Run now, Open chat and its runs’ answers', async () => {
  const { io } = setup();
  await screen.findByRole('list', { name: 'Workflows' });
  await userEvent.click(screen.getByRole('button', { name: /^Morning Brief/ }));
  const sheet = await screen.findByRole('dialog', { name: 'Morning Brief' });
  expect(within(sheet).getByText(/Asks before actions/)).toBeVisible();
  const runs = await within(sheet).findByRole('list', { name: 'Recent runs' });
  expect(within(runs).getByText('3 items for today')).toBeVisible();
  expect(
    within(sheet).getByRole('switch', {
      name: 'Turn off workflow: Morning Brief',
    }),
  ).toBeChecked();
  expect(
    within(sheet).getByText(/Change its messages or schedule on your computer/),
  ).toBeVisible();

  await userEvent.click(within(sheet).getByRole('button', { name: 'Run now' }));
  expect(io.run).toHaveBeenCalledTimes(1);
  await userEvent.click(
    within(sheet).getByRole('button', { name: 'Open chat' }),
  );
  expect(io.openConversation).toHaveBeenCalledWith('chat-brief');
});

it('opens the sheet of a workflow a link names', async () => {
  setup(WORKFLOWS, 'digest');
  expect(
    await screen.findByRole('dialog', { name: 'Research Digest' }),
  ).toBeVisible();
});

it('starts a chat that sets one up from + in the header', async () => {
  const { newChat } = setup();
  await screen.findByRole('list', { name: 'Workflows' });
  const header = screen.getByRole('banner', { name: 'Phone header' });
  await userEvent.click(
    within(header).getByRole('button', { name: 'New workflow' }),
  );
  expect(newChat).toHaveBeenCalledWith(NEW_WORKFLOW_DRAFT);
});

it('offers + when there are no workflows yet', async () => {
  setup([]);
  expect(await screen.findByText('No workflows yet')).toBeVisible();
  expect(screen.getByText(/Tap \+ to set one up in a chat/)).toBeVisible();
  await waitFor(() =>
    expect(
      screen.getAllByRole('radio').map((chip) => chip.textContent),
    ).toEqual(['All 0', 'Running 0', 'Failed 0', 'Off 0']),
  );
});
