import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import type {
  ConversationView,
  GenerationState,
  MonitorSnapshot,
  OnboardingSnapshot,
  TaskSummary,
  TaskSummaryPage,
} from '../../api/types';
import OverviewHome, { type OverviewHomeProps } from './OverviewHome';
import { scheduleWords } from './home-format';

/** Friday 26 September 2026, 9:30 local time. */
const now = new Date(2026, 8, 26, 9, 30, 0);
/** A local wall-clock time on 26 September (day 26) or earlier/later days. */
const at = (day: number, hour: number, minute = 0) =>
  new Date(2026, 8, day, hour, minute, 0).toISOString();

function conversation(
  id: string,
  title: string,
  extra: Partial<ConversationView> = {},
): ConversationView {
  return {
    id,
    revision: `r-${id}`,
    title,
    pinned: false,
    updated_at: at(26, 8),
    ...extra,
  };
}

function generation(status: GenerationState['status']): GenerationState {
  return {
    execution_id: 'e',
    conversation_id: 'c',
    generation_id: 'g',
    pass_id: 'p',
    status,
    revision: 'r',
    cancel_requested: false,
    quiesced: false,
    cleanup_complete: false,
    external_outcome: 'not_applicable',
    approval_id: status === 'waiting_approval' ? 'approval-1' : null,
    can_stop: true,
  };
}

function task(
  id: string,
  name: string,
  extra: Partial<TaskSummary> = {},
): TaskSummary {
  return {
    id,
    name,
    description: '',
    icon: '',
    enabled: true,
    notify_only: false,
    step_count: 2,
    schedule: null,
    at: null,
    last_run: null,
    last_status: null,
    conversation_id: null,
    agent_profile_id: 'builtin:row_bot_default',
    approval_mode: 'block',
    ...extra,
  };
}

function page(items: TaskSummary[]): TaskSummaryPage {
  return {
    schema_version: 1,
    revision: 'tasks-r',
    items,
    total: items.length,
    next_cursor: null,
  };
}

function setupSnapshot(
  extra: Partial<OnboardingSnapshot> = {},
): OnboardingSnapshot {
  return {
    schema_version: 1,
    revision: 'c'.repeat(64),
    setup_complete: true,
    starter_workflows_missing: 0,
    profile: [],
    completed_steps: ['models'],
    skipped_steps: [],
    dismissed_home_card: false,
    steps: [
      { id: 'models', title: 'Models', description: 'Connect a model.' },
      { id: 'voice', title: 'Voice', description: 'Configure voice.' },
    ],
    intents: [],
    ...extra,
  };
}

function monitorSnapshot(
  extra: Partial<MonitorSnapshot> = {},
): MonitorSnapshot {
  return {
    schema_version: 1,
    dream_revision: 'b'.repeat(64),
    extraction: {
      availability: 'available',
      last_run: null,
      interval_hours: 2,
      threads_scanned: 0,
      entities_saved: 0,
      islands_repaired: 0,
    },
    extraction_journal: [],
    extraction_journal_availability: 'missing',
    dream: {
      availability: 'available',
      enabled: true,
      window: '1:00 – 5:00',
      last_run: null,
      last_summary: null,
      recent: [],
    },
    dream_journal: [],
    dream_journal_availability: 'missing',
    logs: {
      availability: 'unavailable',
      authorized: false,
      entries: [],
      full_available: false,
    },
    ...extra,
  };
}

const handlers = {
  onOpenConversation: vi.fn(),
  onOpenWorkflows: vi.fn(),
  onOpenTab: vi.fn(),
  onHideSetup: vi.fn(),
};

function show(props: Partial<OverviewHomeProps> = {}) {
  const loadTasks =
    props.loadTasks ?? vi.fn().mockResolvedValue(page([] as TaskSummary[]));
  const view = render(
    <MemoryRouter>
      <OverviewHome
        conversations={[]}
        setup={null}
        monitor={null}
        loadTasks={loadTasks}
        now={now}
        {...handlers}
        {...props}
      />
    </MemoryRouter>,
  );
  return { ...view, loadTasks };
}

function list(name: string) {
  return screen.getByRole('list', { name });
}

function buttonNames(name: string) {
  return within(list(name))
    .getAllByRole('button')
    .map((button) => button.getAttribute('aria-label') ?? button.textContent);
}

beforeEach(() => {
  for (const handler of Object.values(handlers)) handler.mockReset();
});

it('greets by the supplied clock and summarizes a quiet workspace', async () => {
  show();
  expect(
    screen.getByRole('heading', { level: 2, name: 'Good morning' }),
  ).toBeVisible();
  expect(screen.getByRole('status')).toHaveTextContent('Reading workflows…');
  expect(await screen.findByText('No scheduled workflows.')).toBeVisible();
  expect(screen.queryByText('Reading workflows…')).toBeNull();
  const header = screen.getByRole('heading', { level: 2 }).closest('header')!;
  expect(header).toHaveTextContent('Nothing needs you');
  expect(
    screen.getByText(
      'Nothing needs you. Approvals and failed runs show up here.',
    ),
  ).toBeVisible();
  expect(screen.getByText('No agents or workflows are running.')).toBeVisible();
  expect(screen.getByText('No conversations yet.')).toBeVisible();
  expect(
    screen.getByText('Quiet. Nothing ran since 6 PM yesterday.'),
  ).toBeVisible();
  expect(screen.getByRole('region', { name: 'Needs you' })).toBeInTheDocument();
  expect(screen.queryByRole('region', { name: 'Continue setup' })).toBeNull();
});

it('greets in the afternoon and evening by the local hour', () => {
  const { unmount } = show({ now: new Date(2026, 8, 26, 14, 0, 0) });
  expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent(
    'Good afternoon',
  );
  unmount();
  show({ now: new Date(2026, 8, 26, 21, 0, 0) });
  expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent(
    'Good evening',
  );
});

it('lists conversation and workflow approvals and failed workflows under Needs you', async () => {
  const conversations = [
    conversation('chat-wait', 'Deploy plan', {
      generation_state: [generation('waiting_approval')],
    }),
    conversation('chat-attention', 'Delegated review', {
      activity_state: 'attention',
    }),
    conversation('chat-phase', 'Agent approval', {
      activity_state: 'attention',
      activity_phase: 'waiting_approval',
    }),
    conversation('chat-idle', 'Idle notes'),
  ];
  const tasks = [
    task('wf-paused', 'Nightly backup', {
      active_run: {
        id: 'run-1',
        status: 'paused',
        started_at: at(26, 9),
        steps_done: 1,
        steps_total: 3,
      },
    }),
    task('wf-waiting', 'Invoice sync', {
      active_run: {
        id: 'run-2',
        status: 'waiting_approval',
        started_at: at(26, 9),
        steps_done: 0,
        steps_total: 2,
      },
    }),
    task('wf-failed', 'Morning digest', {
      last_status: 'FAILED',
      schedule: 'daily:08:00',
      last_run: at(26, 8),
    }),
    task('wf-timeout', 'Weekly report', {
      last_status: 'timed_out',
      schedule: null,
    }),
    // A failed workflow that is running again is not reported as failed.
    task('wf-retrying', 'Retrying digest', {
      last_status: 'failed',
      active_run: {
        id: 'run-3',
        status: 'running',
        started_at: at(26, 9),
        steps_done: 0,
        steps_total: 1,
      },
    }),
    task('wf-ok', 'Healthy', { last_status: 'completed' }),
  ];
  show({
    conversations,
    loadTasks: vi.fn().mockResolvedValue(page(tasks)),
  });
  const needs = await screen.findByRole('list', { name: 'Needs you' });
  expect(buttonNames('Needs you')).toEqual([
    'Review approval in Deploy plan',
    'Review approval in Delegated review',
    'Review approval in Agent approval',
    'Review workflow approval: Nightly backup',
    'Review workflow approval: Invoice sync',
    'Open failed workflow: Morning digest',
    'Open failed workflow: Weekly report',
  ]);
  const meta = (name: string) =>
    within(needs).getByRole('button', { name }).textContent;
  expect(meta('Review approval in Deploy plan')).toContain(
    'Waiting for your approval',
  );
  // Attention without an approval phase is not described as an approval.
  expect(meta('Review approval in Delegated review')).toContain(
    'Agent work needs your attention',
  );
  expect(meta('Review approval in Delegated review')).not.toContain(
    'Waiting for your approval',
  );
  expect(meta('Review approval in Agent approval')).toContain(
    'Waiting for your approval',
  );
  expect(
    within(needs).getAllByText('Workflow waiting for your approval'),
  ).toHaveLength(2);
  expect(
    within(needs).getByText(`Failed · ${scheduleWords('daily:08:00', null)}`),
  ).toBeVisible();
  expect(within(needs).getByText('Timed out · Manual')).toBeVisible();
  expect(screen.getByRole('heading', { name: /^Needs you/ })).toHaveTextContent(
    'Needs you7',
  );
  const header = screen.getByRole('heading', { level: 2 }).closest('header')!;
  expect(header).toHaveTextContent('7 things need you');

  fireEvent.click(
    within(needs).getByRole('button', {
      name: 'Review approval in Deploy plan',
    }),
  );
  expect(handlers.onOpenConversation).toHaveBeenCalledWith('chat-wait');
  fireEvent.click(
    within(needs).getByRole('button', {
      name: 'Review workflow approval: Nightly backup',
    }),
  );
  expect(handlers.onOpenWorkflows).toHaveBeenLastCalledWith('wf-paused');
  fireEvent.click(
    within(needs).getByRole('button', {
      name: 'Open failed workflow: Morning digest',
    }),
  );
  expect(handlers.onOpenWorkflows).toHaveBeenLastCalledWith('wf-failed');
});

it('shows running conversations, delegated agents, and workflow step progress', async () => {
  const conversations = [
    conversation('chat-active', 'Research sprint', {
      activity_state: 'active',
      activity_phase: 'tool_call',
    }),
    conversation('chat-reply', 'Quick question', {
      generation_state: [generation('running')],
    }),
    conversation('child-agent', 'Summarize sources', {
      parent_conversation_id: 'chat-active',
      generation_state: [generation('stopping')],
    }),
    // Waiting for approval wins over working; it is listed only once.
    conversation('chat-both', 'Both states', {
      activity_state: 'active',
      generation_state: [generation('waiting_approval')],
    }),
    conversation('chat-done', 'Finished', {
      generation_state: [generation('completed')],
    }),
  ];
  const tasks = [
    task('wf-run', 'Import leads', {
      active_run: {
        id: 'run-1',
        status: 'running',
        started_at: at(26, 9, 10),
        steps_done: 1,
        steps_total: 3,
      },
    }),
    task('wf-last', 'Final step', {
      active_run: {
        id: 'run-2',
        status: 'running',
        started_at: at(26, 9, 10),
        steps_done: 3,
        steps_total: 3,
      },
    }),
    task('wf-start', 'Starting up', {
      active_run: {
        id: 'run-3',
        status: 'starting',
        started_at: at(26, 9, 10),
        steps_done: 0,
        steps_total: 0,
      },
    }),
  ];
  show({
    conversations,
    loadTasks: vi.fn().mockResolvedValue(page(tasks)),
  });
  const running = await screen.findByRole('list', { name: 'Running now' });
  await within(running).findByRole('button', {
    name: 'Open running workflow: Import leads',
  });
  expect(buttonNames('Running now')).toEqual([
    'Open running conversation: Research sprint',
    'Open running conversation: Quick question',
    'Open running conversation: Summarize sources',
    'Open running workflow: Import leads',
    'Open running workflow: Final step',
    'Open running workflow: Starting up',
  ]);
  const text = (name: string) =>
    within(running).getByRole('button', { name }).textContent;
  expect(text('Open running conversation: Research sprint')).toContain(
    'Agents working · Tool call',
  );
  expect(text('Open running conversation: Quick question')).toContain(
    'Replying',
  );
  expect(text('Open running conversation: Summarize sources')).toContain(
    'Agent · Working',
  );
  expect(text('Open running workflow: Import leads')).toContain(
    'Workflow · step 2/3',
  );
  expect(text('Open running workflow: Final step')).toContain(
    'Workflow · step 3/3',
  );
  expect(text('Open running workflow: Starting up')).toContain(
    'Workflow · Starting',
  );
  expect(buttonNames('Needs you')).toEqual(['Review approval in Both states']);
  const header = screen.getByRole('heading', { level: 2 }).closest('header')!;
  expect(header).toHaveTextContent('1 thing needs you · 6 running');

  fireEvent.click(
    within(running).getByRole('button', {
      name: 'Open running conversation: Summarize sources',
    }),
  );
  expect(handlers.onOpenConversation).toHaveBeenCalledWith('child-agent');
  fireEvent.click(
    within(running).getByRole('button', {
      name: 'Open running workflow: Import leads',
    }),
  );
  expect(handlers.onOpenWorkflows).toHaveBeenCalledWith('wf-run');
});

it('lists at most six top-level recent threads named by title', () => {
  const conversations = [
    ...Array.from({ length: 7 }, (_, index) =>
      conversation(`chat-${index + 1}`, `Thread ${index + 1}`),
    ),
    conversation('child', 'Delegated child', {
      parent_conversation_id: 'chat-1',
    }),
  ];
  conversations.splice(
    1,
    0,
    conversation('untitled', '', { updated_at: undefined }),
  );
  show({ conversations });
  const recent = list('Recent threads');
  expect(
    within(recent)
      .getAllByRole('button')
      .map((button) => button.textContent),
  ).toEqual([
    expect.stringContaining('Thread 1'),
    'Untitled conversation',
    expect.stringContaining('Thread 2'),
    expect.stringContaining('Thread 3'),
    expect.stringContaining('Thread 4'),
    expect.stringContaining('Thread 5'),
  ]);
  expect(
    within(recent).queryByRole('button', { name: /Delegated child/ }),
  ).toBeNull();
  expect(within(recent).queryByRole('button', { name: /Thread 6/ })).toBeNull();
  fireEvent.click(within(recent).getByRole('button', { name: /Thread 3/ }));
  expect(handlers.onOpenConversation).toHaveBeenCalledWith('chat-3');
  expect(screen.getByRole('link', { name: 'Library' })).toHaveAttribute(
    'href',
    '/library',
  );
});

it('lists the next five enabled scheduled workflows in fire order', async () => {
  const tasks = [
    task('later', 'Later', { next_run: at(27, 9), schedule: 'daily:09:00' }),
    task('soonest', 'Soonest', {
      next_run: at(26, 10),
      schedule: 'interval:2',
    }),
    task('disabled', 'Disabled', { enabled: false, next_run: at(26, 9, 45) }),
    task('no-next', 'No next run', { next_run: null }),
    task('bad-next', 'Bad next run', { next_run: 'not a date' }),
    task('third', 'Third', { next_run: at(26, 18) }),
    task('fourth', 'Fourth', { next_run: at(28, 7) }),
    task('fifth', 'Fifth', { next_run: at(29, 7) }),
    task('sixth', 'Sixth', { next_run: at(30, 7) }),
    task('second', 'Second', { next_run: at(26, 12) }),
  ];
  show({ loadTasks: vi.fn().mockResolvedValue(page(tasks)) });
  const upcoming = await screen.findByRole('list', { name: 'Upcoming' });
  expect(buttonNames('Upcoming')).toEqual([
    'Open scheduled workflow: Soonest',
    'Open scheduled workflow: Second',
    'Open scheduled workflow: Third',
    'Open scheduled workflow: Later',
    'Open scheduled workflow: Fourth',
  ]);
  const soonest = within(upcoming).getByRole('button', {
    name: 'Open scheduled workflow: Soonest',
  });
  expect(soonest).toHaveTextContent('Every 2 hours');
  expect(soonest).toHaveTextContent('in 30 minutes');
  expect(within(soonest).getByText('in 30 minutes')).toHaveAttribute(
    'datetime',
    at(26, 10),
  );
  expect(screen.getByRole('heading', { name: /^Upcoming/ })).toHaveTextContent(
    'Upcoming5',
  );
  const header = screen.getByRole('heading', { level: 2 }).closest('header')!;
  expect(header).toHaveTextContent('Nothing needs you · next: Soonest');

  fireEvent.click(soonest);
  expect(handlers.onOpenWorkflows).toHaveBeenLastCalledWith('soonest');
  fireEvent.click(screen.getByRole('button', { name: 'Workflows' }));
  expect(handlers.onOpenWorkflows).toHaveBeenLastCalledWith();
});

it('digests workflow runs, extraction, Dream Cycle and active threads since 6 PM yesterday', async () => {
  const tasks = [
    task('a', 'A', {
      recent_runs: [
        { status: 'completed', started_at: at(26, 7) },
        { status: 'failed', started_at: at(25, 22) },
        // Before 6 PM yesterday: outside the window.
        { status: 'failed', started_at: at(25, 17, 59) },
      ],
    }),
    task('b', 'B', {
      recent_runs: [
        { status: 'completed_delivery_failed', started_at: at(25, 18) },
        // After the supplied clock: not "since yesterday evening" yet.
        { status: 'failed', started_at: at(26, 11) },
      ],
    }),
  ];
  const conversations = [
    conversation('recent-1', 'Recent one', { updated_at: at(26, 8) }),
    conversation('recent-2', 'Recent two', { updated_at: at(25, 19) }),
    conversation('old', 'Old one', { updated_at: at(25, 12) }),
    conversation('child', 'Child', {
      parent_conversation_id: 'recent-1',
      updated_at: at(26, 8),
    }),
  ];
  const monitor = monitorSnapshot({
    extraction: {
      availability: 'available',
      last_run: at(26, 2),
      interval_hours: 2,
      threads_scanned: 4,
      entities_saved: 1,
      islands_repaired: 0,
    },
    dream_journal: [
      {
        timestamp: at(26, 3),
        summary: '',
        merges: [{ duplicate_subject: 'x', survivor_subject: 'y', score: 0.9 }],
        enrichments: [
          {
            subject: 'a',
            old_length: 1,
            new_length: 2,
            new_description: 'b',
          },
          {
            subject: 'c',
            old_length: 1,
            new_length: 2,
            new_description: 'd',
          },
        ],
        inferred_relations: [
          {
            source_subject: 'a',
            target_subject: 'c',
            relation_type: 'knows',
            confidence: 0.8,
            evidence: '',
          },
        ],
        errors: [],
      },
      {
        timestamp: at(24, 3),
        summary: '',
        merges: [
          { duplicate_subject: 'old', survivor_subject: 'y', score: 0.9 },
        ],
        enrichments: [],
        inferred_relations: [],
        errors: [],
      },
    ],
  });
  show({
    conversations,
    monitor,
    loadTasks: vi.fn().mockResolvedValue(page(tasks)),
  });
  const digest = await screen.findByRole('list', {
    name: 'Since yesterday evening',
  });
  await within(digest).findByText('3 workflow runs, 2 failed');
  expect(
    within(digest)
      .getAllByRole('listitem')
      .map((item) => item.textContent),
  ).toEqual([
    '3 workflow runs, 2 failed',
    'Knowledge extraction read 4 conversations and saved 1 memory',
    'Dream Cycle merged 1 duplicate, enriched 2 memories, inferred 1 connection',
    '2 conversations active',
  ]);
  fireEvent.click(screen.getByRole('button', { name: 'Monitor' }));
  expect(handlers.onOpenTab).toHaveBeenCalledWith('monitor');
});

it('reports all-completed runs, a quiet Dream Cycle, and a journal-less Dream run honestly', async () => {
  const tasks = [
    task('a', 'A', {
      recent_runs: [{ status: 'completed', started_at: at(26, 1) }],
    }),
  ];
  const quietDream = monitorSnapshot({
    extraction: {
      availability: 'unavailable',
      last_run: at(26, 2),
      interval_hours: 2,
      threads_scanned: 9,
      entities_saved: 9,
      islands_repaired: 0,
    },
    dream_journal: [
      {
        timestamp: at(26, 3),
        summary: '',
        merges: [],
        enrichments: [],
        inferred_relations: [],
        errors: [],
      },
    ],
  });
  const { unmount } = show({
    monitor: quietDream,
    loadTasks: vi.fn().mockResolvedValue(page(tasks)),
  });
  const digest = await screen.findByRole('list', {
    name: 'Since yesterday evening',
  });
  await within(digest).findByText('1 workflow run, all completed');
  expect(
    within(digest)
      .getAllByRole('listitem')
      .map((item) => item.textContent),
  ).toEqual([
    '1 workflow run, all completed',
    'Dream Cycle ran and found nothing to change',
  ]);
  unmount();

  show({
    monitor: monitorSnapshot({
      dream: {
        availability: 'available',
        enabled: true,
        window: '1:00 – 5:00',
        last_run: at(26, 4),
        last_summary: null,
        recent: [],
      },
    }),
  });
  expect(
    await screen.findByRole('list', { name: 'Since yesterday evening' }),
  ).toHaveTextContent('Dream Cycle ran');
});

it('asks to choose how Row-Bot thinks while no model exists, without a hide control', () => {
  show({
    setup: setupSnapshot({
      setup_complete: false,
      needs_model: true,
      completed_steps: [],
      dismissed_home_card: false,
    }),
  });
  const region = screen.getByRole('region', { name: 'Continue setup' });
  expect(region).toHaveTextContent('Choose how Row-Bot thinks');
  expect(
    within(region).getByRole('link', { name: 'Choose a model' }),
  ).toHaveAttribute('href', '/setup');
  expect(
    within(region).queryByRole('button', { name: 'Hide setup reminder' }),
  ).toBeNull();
  // Even a dismissed card stays until setup is complete.
  expect(
    screen.queryByText(
      'Nothing needs you. Approvals and failed runs show up here.',
    ),
  ).toBeNull();
  const header = screen.getByRole('heading', { level: 2 }).closest('header')!;
  expect(header).toHaveTextContent('1 thing needs you');
});

it('keeps the first-run route even if the home card was dismissed', () => {
  show({
    setup: setupSnapshot({
      setup_complete: false,
      needs_model: true,
      dismissed_home_card: true,
    }),
  });
  expect(screen.getByRole('link', { name: 'Choose a model' })).toBeVisible();
});

it('reminds about optional setup until hidden and reports a hide failure', () => {
  const { rerender } = show({ setup: setupSnapshot() });
  const region = screen.getByRole('region', { name: 'Continue setup' });
  expect(region).toHaveTextContent('Finish setup');
  expect(region).toHaveTextContent('1 of 2 done');
  expect(
    within(region).getByRole('link', { name: 'Continue setup' }),
  ).toHaveAttribute('href', '/setup');
  fireEvent.click(
    within(region).getByRole('button', { name: 'Hide setup reminder' }),
  );
  expect(handlers.onHideSetup).toHaveBeenCalledTimes(1);

  rerender(
    <MemoryRouter>
      <OverviewHome
        conversations={[]}
        setup={setupSnapshot()}
        monitor={null}
        loadTasks={vi.fn().mockResolvedValue(page([]))}
        now={now}
        {...handlers}
        setupError="Could not hide the setup reminder. Refresh this page and try again."
      />
    </MemoryRouter>,
  );
  expect(
    within(screen.getByRole('region', { name: 'Continue setup' })).getByRole(
      'status',
    ),
  ).toHaveTextContent(
    'Could not hide the setup reminder. Refresh this page and try again.',
  );
});

it('counts skipped areas apart from done ones', () => {
  show({
    setup: setupSnapshot({
      steps: [
        { id: 'models', title: 'Models', description: 'Connect a model.' },
        { id: 'voice', title: 'Voice', description: 'Configure voice.' },
        { id: 'channels', title: 'Channels', description: 'Channels.' },
      ],
      completed_steps: ['models'],
      skipped_steps: ['voice'],
    }),
  });
  expect(
    screen.getByRole('region', { name: 'Continue setup' }),
  ).toHaveTextContent('1 of 3 done · 1 skipped');
});

it.each([
  ['dismissed', { dismissed_home_card: true }],
  ['finished', { completed_steps: ['models'], skipped_steps: ['voice'] }],
])('does not remind about optional setup once %s', (_label, extra) => {
  show({ setup: setupSnapshot(extra) });
  expect(screen.queryByRole('region', { name: 'Continue setup' })).toBeNull();
});

it('omits the hide control when Home cannot hide the reminder', () => {
  show({ setup: setupSnapshot(), onHideSetup: undefined });
  expect(
    screen.queryByRole('button', { name: 'Hide setup reminder' }),
  ).toBeNull();
});

it('redacts a workflow read failure and still shows conversation sections', async () => {
  show({
    conversations: [conversation('chat-a', 'Design review')],
    loadTasks: vi
      .fn()
      .mockRejectedValue(new Error('C:\\Users\\private\\tasks.db locked')),
  });
  expect(
    await screen.findByText(
      'Workflows could not be read: Something went wrong. Try again.',
    ),
  ).toBeVisible();
  expect(document.body.textContent).not.toContain('private');
  expect(screen.queryByText('Reading workflows…')).toBeNull();
  expect(screen.getByText('No scheduled workflows.')).toBeVisible();
  expect(
    within(list('Recent threads')).getByRole('button', {
      name: /Design review/,
    }),
  ).toBeVisible();
});

it('aborts the workflow read on unmount and ignores its late result', async () => {
  let resolve!: (value: TaskSummaryPage) => void;
  let signal: AbortSignal | undefined;
  const loadTasks = vi.fn((next: AbortSignal) => {
    signal = next;
    return new Promise<TaskSummaryPage>((done) => {
      resolve = done;
    });
  });
  const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
  const { unmount } = show({ loadTasks });
  expect(loadTasks).toHaveBeenCalledTimes(1);
  expect(signal?.aborted).toBe(false);
  unmount();
  expect(signal?.aborted).toBe(true);
  await act(async () => {
    resolve(page([task('late', 'Late', { next_run: at(26, 10) })]));
  });
  expect(screen.queryByText('Late')).toBeNull();
  expect(errors).not.toHaveBeenCalled();
});

it('re-reads workflows when the refresh key changes and drops the stale read', async () => {
  const first = { resolve: (_value: TaskSummaryPage) => {} };
  const signals: AbortSignal[] = [];
  const loadTasks = vi
    .fn()
    .mockImplementationOnce((signal: AbortSignal) => {
      signals.push(signal);
      return new Promise<TaskSummaryPage>((done) => {
        first.resolve = done;
      });
    })
    .mockImplementationOnce((signal: AbortSignal) => {
      signals.push(signal);
      return Promise.resolve(
        page([task('fresh', 'Fresh', { next_run: at(26, 11) })]),
      );
    });
  const view = (refreshKey: string) => (
    <MemoryRouter>
      <OverviewHome
        conversations={[]}
        setup={null}
        monitor={null}
        loadTasks={loadTasks}
        now={now}
        refreshKey={refreshKey}
        {...handlers}
      />
    </MemoryRouter>
  );
  const { rerender } = render(view('session-a'));
  rerender(view('session-a'));
  expect(loadTasks).toHaveBeenCalledTimes(1);
  rerender(view('session-b'));
  expect(loadTasks).toHaveBeenCalledTimes(2);
  expect(signals[0].aborted).toBe(true);
  expect(
    await screen.findByRole('button', {
      name: 'Open scheduled workflow: Fresh',
    }),
  ).toBeVisible();
  await act(async () => {
    first.resolve(page([task('stale', 'Stale', { next_run: at(26, 10) })]));
  });
  expect(
    screen.queryByRole('button', { name: 'Open scheduled workflow: Stale' }),
  ).toBeNull();
});

it('does not start a clock timer when the caller supplies the time', () => {
  const setInterval = vi.spyOn(window, 'setInterval');
  show();
  expect(setInterval).not.toHaveBeenCalledWith(expect.any(Function), 60_000);
});

it('advances its own clock every minute when no time is supplied', () => {
  vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] });
  try {
    vi.setSystemTime(new Date(2026, 8, 26, 11, 59, 30));
    render(
      <MemoryRouter>
        <OverviewHome
          conversations={[]}
          setup={null}
          monitor={null}
          loadTasks={() => new Promise<TaskSummaryPage>(() => {})}
          {...handlers}
        />
      </MemoryRouter>,
    );
    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent(
      'Good morning',
    );
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent(
      'Good afternoon',
    );
  } finally {
    vi.useRealTimers();
  }
});

it('lets interrupted agent work be resumed or dismissed from Needs you (B220)', async () => {
  const resumable = conversation('chat-cut', 'Phase 5 developer', {
    activity_state: 'attention',
    activity_phase: 'resume_required',
  });
  const finished = conversation('chat-done', 'Secure storage', {
    activity_state: 'attention',
    activity_phase: 'interrupted',
  });
  const onResumeAgentWork = vi.fn().mockResolvedValue(undefined);
  const onDismissAgentWork = vi.fn().mockResolvedValue(undefined);
  show({
    conversations: [resumable, finished],
    onResumeAgentWork,
    onDismissAgentWork,
  });
  const needs = await screen.findByRole('list', { name: 'Needs you' });
  const row = (title: string) =>
    within(needs)
      .getByRole('button', { name: `Review agent work in ${title}` })
      .closest('li')!;
  const cut = row('Phase 5 developer');
  expect(cut).toHaveTextContent('Agent work was interrupted');
  await act(async () => {
    fireEvent.click(
      within(cut).getByRole('button', { name: 'Resume agent work' }),
    );
  });
  expect(onResumeAgentWork).toHaveBeenCalledWith(resumable);

  // Nothing left to run there: Dismiss only.
  const done = row('Secure storage');
  expect(
    within(done).queryByRole('button', { name: 'Resume agent work' }),
  ).not.toBeInTheDocument();
  await act(async () => {
    fireEvent.click(
      within(done).getByRole('button', { name: 'Dismiss agent work' }),
    );
  });
  expect(onDismissAgentWork).toHaveBeenCalledWith(finished);
  expect(handlers.onOpenConversation).not.toHaveBeenCalled();

  fireEvent.click(
    within(needs).getByRole('button', {
      name: 'Review agent work in Phase 5 developer',
    }),
  );
  expect(handlers.onOpenConversation).toHaveBeenCalledWith('chat-cut');
});
