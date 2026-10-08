import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import type {
  ConversationView,
  GenerationState,
  MonitorSnapshot,
  OnboardingSnapshot,
  PendingApproval,
  PendingApprovalPage,
  ProblemFix,
  SystemDiagnosisCheck,
  TaskSummary,
  TaskSummaryPage,
} from '../../api/types';
import OverviewHome, { type OverviewHomeProps } from './OverviewHome';
import { WorkspaceActionsContext } from '../shell/workspace-actions';
import { clockTime, scheduleWords } from './home-format';

const runtime = vi.hoisted(() => ({
  approval: vi.fn(),
  intent: vi.fn(),
  // A failed workflow's Run again: the reviewed run Workflows uses.
  getSnapshot: () => ({ handshake: { client_session_id: 'session-1' } }),
  taskRunReview: vi.fn(),
  command: vi.fn(),
  taskRun: vi.fn(),
}));
vi.mock('../../runtime', () => ({
  useRuntime: () => ({ controller: runtime }),
}));

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

function approval(patch: Partial<PendingApproval> = {}): PendingApproval {
  return {
    id: 'approval-mail',
    source: 'conversation',
    title: 'Send an email to Riverside Flour',
    what: 'Reorder for next week: 40 kg rye',
    requested_at: at(26, 9),
    expires_at: null,
    conversation_id: 'chat-mail',
    task_id: null,
    ...patch,
  };
}

function approvals(...items: PendingApproval[]): PendingApprovalPage {
  return { schema_version: 1, items, total: items.length };
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

function check(patch: Partial<SystemDiagnosisCheck>): SystemDiagnosisCheck {
  return {
    id: 'disk',
    name: 'Disk',
    status: 'ok',
    detail: '120 GB free',
    checked_at: new Date(2026, 8, 26, 9, 20, 0).getTime() / 1000,
    settings_tab: 'System',
    network: false,
    stale: false,
    ...patch,
  };
}

function health(...checks: SystemDiagnosisCheck[]) {
  return vi.fn().mockResolvedValue({
    schema_version: 1,
    hourly_network_checks: true,
    checks,
  });
}

const graphNode = (id: string, type: string, relations: number) => ({
  id,
  revision: `r-${id}`,
  subject: `Memory ${id}`,
  description: '',
  entity_type: type,
  source: 'manual',
  updated_at: at(25, 9),
  relation_count: relations,
  orphan: relations === 0,
  is_user: false,
  status: 'active',
  tier: 'semantic',
});

function memory(total: number) {
  return vi.fn().mockResolvedValue({
    schema_version: 1,
    availability: 'available',
    revision: 'g'.repeat(64),
    nodes: [
      graphNode('a', 'person', 3),
      graphNode('b', 'project', 2),
      graphNode('c', 'place', 1),
    ],
    edges: [
      {
        id: 'e1',
        source_id: 'a',
        target_id: 'b',
        relation_type: 'works_on',
        updated_at: at(25, 9),
      },
    ],
    total_entities: total,
    total_relations: 2,
    shown_entities: 3,
    shown_relations: 1,
    truncated: total > 3,
    center_id: null,
    entity_types: ['person', 'project', 'place'],
    sources: ['manual'],
    status_counts: { active: total },
  });
}

const handlers = {
  onOpenConversation: vi.fn(),
  onOpenWorkflows: vi.fn(),
  onOpenTab: vi.fn(),
  onHideSetup: vi.fn(),
  onAsk: vi.fn(),
  onNewResource: vi.fn(),
  onNewWorkflow: vi.fn(),
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

function summary() {
  return screen.getByRole('heading', { level: 2 }).nextElementSibling!;
}

function card(name: RegExp) {
  return within(list('Live status')).getByRole('button', { name });
}

beforeEach(() => {
  for (const handler of Object.values(handlers)) handler.mockReset();
  runtime.approval.mockReset().mockResolvedValue({
    id: 'approval-mail',
    revision: '0',
    nonce: 'nonce-mail',
  });
  runtime.intent.mockReset().mockResolvedValue({ status: 'completed' });
  runtime.taskRunReview.mockReset().mockResolvedValue({
    task_id: 'wf-brief',
    task_revision: 't'.repeat(64),
    policy_revision: 'p'.repeat(64),
    agent_profile_id: 'builtin:row_bot_default',
    approval_mode: 'block',
    notify_only: false,
    steps_total: 2,
    conversation_id: null,
  });
  runtime.command
    .mockReset()
    .mockImplementation(async (_id: null, command: { command_id: string }) => ({
      command_id: command.command_id,
      status: 'completed',
      task_run_id: 'run-again',
      task_run_reserved: true,
    }));
  runtime.taskRun.mockReset().mockResolvedValue({ id: 'run-again' });
});

afterEach(() => {
  vi.mocked(window.matchMedia).mockImplementation(
    (query: string) =>
      ({
        matches: false,
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }) as unknown as MediaQueryList,
  );
});

// Hero ---------------------------------------------------------------------

it('greets by the supplied clock, shows Buddy beside it and is all caught up once reads finish', async () => {
  show({ buddy: <img alt="Buddy" src="data:," /> });
  const heading = screen.getByRole('heading', {
    level: 2,
    name: 'Good morning',
  });
  expect(heading).toBeVisible();
  expect(
    within(heading.closest('header')!).getByRole('img', { name: 'Buddy' }),
  ).toBeVisible();
  expect(screen.getByRole('status')).toHaveTextContent(
    'Checking what needs you…',
  );
  const needs = screen.getByRole('region', { name: 'Needs you' });
  expect(await within(needs).findByText('All caught up')).toBeVisible();
  expect(needs).toHaveTextContent(
    'Nothing is waiting for you. Approvals and failed runs show up here.',
  );
  expect(summary()).toHaveTextContent("You're all caught up.");
  expect(screen.getByText('No conversations yet.')).toBeVisible();
  expect(
    screen.getByText('Quiet. Nothing ran since 6 PM yesterday.'),
  ).toBeVisible();
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

it('sums up in one sentence what needs you, who is working and what runs next', async () => {
  show({
    conversations: [
      conversation('chat-wait', 'Deploy plan', {
        generation_state: [generation('waiting_approval')],
      }),
      conversation('chat-fail', 'Other', { activity_state: 'attention' }),
      conversation('chat-a', 'Research sprint', { activity_state: 'active' }),
      conversation('chat-b', 'Quick question', {
        generation_state: [generation('running')],
      }),
    ],
    loadTasks: vi
      .fn()
      .mockResolvedValue(
        page([task('digest', 'Morning digest', { next_run: at(26, 10) })]),
      ),
  });
  await screen.findByRole('button', { name: /^Workflows: in 30 minutes/ });
  expect(summary()).toHaveTextContent(
    '2 things need you. 2 agents are working, and Morning digest runs in 30 minutes.',
  );
});

it('says what Row-Bot learned this week when nothing needs you', async () => {
  show({
    monitor: monitorSnapshot({
      extraction_journal: [
        {
          timestamp: at(24, 10),
          summary: '',
          contradictions_blocked: 0,
          low_confidence_skipped: 0,
          islands_repaired: 0,
          threads: [{ label: 'Trip plan', extracted: 5, saved: 12 }],
          errors: [],
        },
      ],
    }),
  });
  await screen.findByText('All caught up');
  expect(summary()).toHaveTextContent(
    "You're all caught up. Row-Bot learned 12 new things this week.",
  );
});

it('starts a new chat from the Ask box on Enter with the text as its first message', async () => {
  const user = userEvent.setup();
  show();
  const ask = screen.getByRole('textbox', { name: 'Ask Row-Bot' });
  await user.type(ask, '{Enter}');
  expect(handlers.onAsk).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Start chat' })).toBeDisabled();
  await user.type(ask, '  Plan my week  {Enter}');
  expect(handlers.onAsk).toHaveBeenCalledTimes(1);
  expect(handlers.onAsk).toHaveBeenCalledWith('Plan my week');
  // Files, dictation and tools live in the new chat's composer.
  await user.click(
    screen.getByRole('button', { name: 'Add files in a new chat' }),
  );
  expect(handlers.onAsk).toHaveBeenLastCalledWith('  Plan my week  ', {
    send: false,
  });
});

it('keeps the Ask box from starting a second chat while one is being made', async () => {
  const user = userEvent.setup();
  show({ asking: true });
  const ask = screen.getByRole('textbox', { name: 'Ask Row-Bot' });
  await user.type(ask, 'Plan my week{Enter}');
  expect(handlers.onAsk).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Start chat' })).toBeDisabled();
});

it('offers quick starts that reuse New design, New code folder, New workflow and the last chat', () => {
  show({
    conversations: [
      conversation('pinned', 'Pinned plan', {
        pinned: true,
        updated_at: at(20, 8),
      }),
      conversation('latest', 'Research brief', { updated_at: at(26, 9) }),
      conversation('child', 'Delegated', {
        parent_conversation_id: 'latest',
        updated_at: at(26, 9, 20),
      }),
    ],
  });
  const quick = screen.getByRole('group', { name: 'Quick starts' });
  fireEvent.click(within(quick).getByRole('button', { name: 'New design' }));
  expect(handlers.onNewResource).toHaveBeenLastCalledWith('artifact');
  fireEvent.click(
    within(quick).getByRole('button', { name: 'New code folder' }),
  );
  expect(handlers.onNewResource).toHaveBeenLastCalledWith('workspace');
  fireEvent.click(within(quick).getByRole('button', { name: 'New workflow' }));
  expect(handlers.onNewWorkflow).toHaveBeenCalledTimes(1);
  fireEvent.click(
    within(quick).getByRole('button', { name: 'Continue “Research brief”' }),
  );
  expect(handlers.onOpenConversation).toHaveBeenCalledWith('latest');
});

it('leaves out the Ask box and quick starts Home cannot run', () => {
  show({
    onAsk: undefined,
    onNewResource: undefined,
    onNewWorkflow: undefined,
  });
  expect(screen.queryByRole('textbox', { name: 'Ask Row-Bot' })).toBeNull();
  expect(screen.queryByRole('group', { name: 'Quick starts' })).toBeNull();
});

// Needs you ----------------------------------------------------------------

it('answers waiting approvals in place and re-reads them after a decision', async () => {
  const loadApprovals = vi
    .fn()
    .mockResolvedValueOnce(
      approvals(
        approval(),
        approval({
          id: 'approval-post',
          source: 'workflow',
          title: 'Post the specials',
          what: 'Weekly specials · step 3 of 4',
          conversation_id: null,
          task_id: 'task-specials',
        }),
      ),
    )
    .mockResolvedValue(approvals());
  show({
    loadApprovals,
    conversations: [
      // Its approval is answered in place: not listed a second time.
      conversation('chat-mail', 'Supplier price check', {
        generation_state: [generation('waiting_approval')],
      }),
    ],
  });
  const needs = await screen.findByRole('list', { name: 'Needs you' });
  const mail = (
    await within(needs).findByText('Send an email to Riverside Flour')
  ).closest('li')!;
  expect(mail).toHaveTextContent('Reorder for next week: 40 kg rye');
  expect(needs).not.toHaveTextContent('Supplier price check');
  expect(
    within(mail).getByRole('link', { name: 'Open the conversation' }),
  ).toHaveAttribute('href', '/conversations/chat-mail');
  const post = within(needs).getByText('Post the specials').closest('li')!;
  expect(
    within(post).getByRole('link', { name: 'Open the workflow' }),
  ).toHaveAttribute('href', '/?tab=workflows&workflow=task-specials');
  expect(screen.getByRole('heading', { name: /^Needs you/ })).toHaveTextContent(
    'Needs you2',
  );
  await act(async () =>
    fireEvent.click(
      within(
        within(mail).getByRole('group', {
          name: 'Answer: Send an email to Riverside Flour',
        }),
      ).getByRole('button', { name: 'Approve' }),
    ),
  );
  expect(runtime.approval).toHaveBeenCalledWith('approval-mail');
  expect(runtime.intent).toHaveBeenCalledWith(
    'approval-mail',
    'approval.resolve',
    { decision: 'approve', nonce: 'nonce-mail' },
    '0',
  );
  expect(loadApprovals).toHaveBeenCalledTimes(2);
  expect(
    await within(needs).findByRole('button', {
      name: 'Review approval in Supplier price check',
    }),
  ).toBeVisible();
  expect(needs).not.toHaveTextContent('Send an email to Riverside Flour');
});

it('shows two waiting items at first and the rest behind "more waiting"', async () => {
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
  await within(needs).findByText('Deploy plan');
  expect(buttonNames('Needs you')).toEqual([
    'Review approval in Deploy plan',
    'Review approval in Delegated review',
  ]);
  const more = screen.getByRole('button', { name: '+5 more waiting' });
  expect(more).toHaveAttribute('aria-expanded', 'false');
  fireEvent.click(more);
  expect(more).toHaveAttribute('aria-expanded', 'true');
  expect(more).toHaveTextContent('Show fewer');
  expect(buttonNames('Needs you')).toEqual([
    'Review approval in Deploy plan',
    'Review approval in Delegated review',
    'Review approval in Agent approval',
    'Review workflow approval: Nightly backup',
    'Review workflow approval: Invoice sync',
    // A failed run's one fix sits beside it (Phase 18).
    'Open failed workflow: Morning digest',
    'Run Morning digest again',
    'Open failed workflow: Weekly report',
    'Run Weekly report again',
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
  expect(summary()).toHaveTextContent('7 things need you.');

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

it('lists a Monitor check whose kept result is red under Needs you (B252)', async () => {
  const loadHealth = health(
    check({
      id: 'disk',
      name: 'Disk',
      status: 'error',
      detail: '1.2 GB free (97% used)',
    }),
    check({
      id: 'documents',
      name: 'Documents',
      status: 'warn',
      detail: 'rebuild recommended',
      settings_tab: 'Documents',
    }),
  );
  show({ loadHealth });
  const needs = await screen.findByRole('list', { name: 'Needs you' });
  await within(needs).findByText('Disk needs attention');
  expect(buttonNames('Needs you')).toEqual([
    'Open Monitor: Disk needs attention',
  ]);
  expect(needs).toHaveTextContent('1.2 GB free (97% used)');
  expect(needs).not.toHaveTextContent('Documents');
  fireEvent.click(
    within(needs).getByRole('button', {
      name: 'Open Monitor: Disk needs attention',
    }),
  );
  expect(handlers.onOpenTab).toHaveBeenCalledWith('monitor');
  expect(loadHealth).toHaveBeenCalledOnce();
});

const fix = (patch: Partial<ProblemFix> & Pick<ProblemFix, 'kind'>) =>
  ({ href: null, target: null, name: '', ...patch }) as ProblemFix;

it('offers each red check its one fix beside it and checks again in place (Phase 18)', async () => {
  const loadHealth = health(
    check({
      id: 'disk',
      status: 'error',
      detail: '1.2 GB free (97% used)',
      fix: fix({ kind: 'check_again', name: 'Disk' }),
    }),
    check({
      id: 'tunnel',
      name: 'Tunnel',
      status: 'error',
      detail: 'Not running: agent failed',
      settings_tab: 'Access',
      fix: fix({
        kind: 'open',
        href: '/settings/access#tunnel',
        name: 'Public link',
      }),
    }),
  );
  const onRunDiagnosis = vi.fn().mockResolvedValue({
    schema_version: 1,
    hourly_network_checks: true,
    checks: [
      check({ id: 'disk', status: 'ok', detail: '40 GB free' }),
      check({
        id: 'tunnel',
        name: 'Tunnel',
        status: 'error',
        detail: 'Not running: agent failed',
        settings_tab: 'Access',
        fix: fix({
          kind: 'open',
          href: '/settings/access#tunnel',
          name: 'Public link',
        }),
      }),
    ],
  });
  show({ loadHealth, onRunDiagnosis });
  const needs = await screen.findByRole('list', { name: 'Needs you' });
  await within(needs).findByText('Disk needs attention');
  // The exact setting, never a page name, as an icon with its name.
  expect(
    within(needs).getByRole('link', { name: 'Open Public link settings' }),
  ).toHaveAttribute('href', '/settings/access#tunnel');

  fireEvent.click(
    within(needs).getByRole('button', { name: 'Check Disk again' }),
  );
  expect(onRunDiagnosis).toHaveBeenCalledOnce();
  await vi.waitFor(() =>
    expect(within(needs).queryByText('Disk needs attention')).toBeNull(),
  );
  expect(within(needs).getByText('Tunnel needs attention')).toBeVisible();
});

it('runs a failed workflow again in place with the reviewed run (Phase 18)', async () => {
  const loadTasks = vi
    .fn()
    .mockResolvedValue(
      page([task('wf-brief', 'Morning brief', { last_status: 'failed' })]),
    );
  show({ loadTasks });
  const needs = await screen.findByRole('list', { name: 'Needs you' });
  fireEvent.click(
    await within(needs).findByRole('button', {
      name: 'Run Morning brief again',
    }),
  );
  expect(
    await within(needs).findByText('Morning brief is running again.'),
  ).toBeVisible();
  expect(runtime.taskRunReview).toHaveBeenCalledWith('wf-brief');
  expect(runtime.command).toHaveBeenCalledWith(
    null,
    expect.objectContaining({
      type: 'task.run',
      payload: {
        task_id: 'wf-brief',
        task_revision: 't'.repeat(64),
        policy_revision: 'p'.repeat(64),
      },
    }),
    expect.any(String),
  );
  // Workflows are read again to show the new run.
  expect(loadTasks).toHaveBeenCalledTimes(2);
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
  expect(screen.queryByText('All caught up')).toBeNull();
  expect(summary()).toHaveTextContent('1 thing needs you.');
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

// Live status strip -------------------------------------------------------

it('shows who is working in the Agents card and opens the first of them', async () => {
  show({
    conversations: [
      conversation('chat-active', 'Research brief', {
        activity_state: 'active',
        activity_phase: 'tool_call',
      }),
      conversation('child-agent', 'Summarize sources', {
        parent_conversation_id: 'chat-active',
        generation_state: [generation('stopping')],
      }),
      conversation('chat-reply', 'Quick question', {
        generation_state: [generation('running')],
      }),
      // Waiting for approval is not counted as working.
      conversation('chat-both', 'Both states', {
        activity_state: 'active',
        generation_state: [generation('waiting_approval')],
      }),
      conversation('chat-done', 'Finished', {
        generation_state: [generation('completed')],
      }),
    ],
  });
  const agents = card(/^Agents/);
  expect(agents).toHaveTextContent('3 working');
  expect(agents).toHaveTextContent('Research brief and 2 more');
  fireEvent.click(agents);
  expect(handlers.onOpenConversation).toHaveBeenCalledWith('chat-active');
});

it('rests the Agents card when nothing works and counts agents that finished today', () => {
  show({
    conversations: [
      conversation('child-a', 'Researcher', {
        parent_conversation_id: 'chat',
        activity_state: 'terminal',
        updated_at: at(26, 8),
      }),
      conversation('child-old', 'Writer', {
        parent_conversation_id: 'chat',
        activity_state: 'terminal',
        updated_at: at(24, 8),
      }),
    ],
  });
  const agents = card(/^Agents/);
  expect(agents).toHaveTextContent('Resting');
  expect(agents).toHaveTextContent('1 agent finished today');
  fireEvent.click(agents);
  expect(handlers.onOpenConversation).toHaveBeenCalledWith('child-a');
});

it('opens the Agents library from the Agents card when no agent is at work', () => {
  const openAgentProfiles = vi.fn();
  render(
    <MemoryRouter>
      <WorkspaceActionsContext.Provider
        value={{ resetLayout: vi.fn(), openAgentProfiles }}
      >
        <OverviewHome
          conversations={[]}
          setup={null}
          monitor={null}
          now={now}
          {...handlers}
        />
      </WorkspaceActionsContext.Provider>
    </MemoryRouter>,
  );
  const agents = card(/^Agents/);
  expect(agents).toHaveTextContent('No agents at work');
  fireEvent.click(agents);
  // Focus comes back to the card when the library closes.
  expect(openAgentProfiles).toHaveBeenCalledWith(agents);
  expect(handlers.onOpenConversation).not.toHaveBeenCalled();
});

it('counts down to the next workflow run and draws today’s runs in the Workflows card', async () => {
  const tasks = [
    task('later', 'Later', { next_run: at(27, 9) }),
    task('soonest', 'Soonest', {
      next_run: at(26, 10),
      recent_runs: [
        { status: 'completed', started_at: at(26, 7) },
        { status: 'failed', started_at: at(26, 8) },
        // Yesterday: not one of today's runs.
        { status: 'completed', started_at: at(25, 20) },
      ],
    }),
    task('disabled', 'Disabled', { enabled: false, next_run: at(26, 9, 45) }),
    task('b', 'B', {
      recent_runs: [{ status: 'completed', started_at: at(26, 9) }],
    }),
  ];
  show({ loadTasks: vi.fn().mockResolvedValue(page(tasks)) });
  const workflows = await screen.findByRole('button', {
    name: /^Workflows: in 30 minutes/,
  });
  expect(workflows).toHaveTextContent('Next: Soonest');
  expect(workflows).toHaveTextContent('3 today · 1 failed');
  fireEvent.click(workflows);
  expect(handlers.onOpenWorkflows).toHaveBeenLastCalledWith();
});

it('says when no workflow is scheduled and when workflows could not be read', async () => {
  const { unmount } = show({
    loadTasks: vi.fn().mockResolvedValue(page([task('a', 'Manual one')])),
  });
  expect(
    await screen.findByRole('button', {
      name: /^Workflows: Nothing scheduled/,
    }),
  ).toHaveTextContent('No runs today');
  unmount();
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
  expect(card(/^Workflows/)).toHaveTextContent('Unavailable');
  expect(
    within(list('Continue where you left off')).getByRole('button', {
      name: 'Open Design review',
    }),
  ).toBeVisible();
});

it('shows how many memories there are and how many were saved this week', async () => {
  const loadMemory = memory(660);
  show({
    loadMemory,
    monitor: monitorSnapshot({
      extraction_journal: [
        {
          timestamp: at(25, 10),
          summary: '',
          contradictions_blocked: 0,
          low_confidence_skipped: 0,
          islands_repaired: 0,
          threads: [
            { label: 'Trip plan', extracted: 9, saved: 8 },
            { label: 'Menu', extracted: 4, saved: 4 },
          ],
          errors: [],
        },
        {
          // More than a week ago.
          timestamp: at(18, 10),
          summary: '',
          contradictions_blocked: 0,
          low_confidence_skipped: 0,
          islands_repaired: 0,
          threads: [{ label: 'Old', extracted: 5, saved: 5 }],
          errors: [],
        },
      ],
    }),
  });
  const memoryCard = await screen.findByRole('button', {
    name: /^Memory: 660 memories/,
  });
  expect(memoryCard).toHaveTextContent('12 updates this week'); // Saves include updates: never "+12" beside a smaller total.
  fireEvent.click(memoryCard);
  expect(handlers.onOpenTab).toHaveBeenCalledWith('knowledge');
  expect(loadMemory).toHaveBeenCalledOnce();
});

it('says when memory could not be read', async () => {
  show({ loadMemory: vi.fn().mockRejectedValue(new Error('locked')) });
  expect(
    await screen.findByRole('button', { name: /^Memory: Unavailable/ }),
  ).toBeVisible();
});

it('reports Monitor’s kept checks in the Health card', async () => {
  const { unmount } = show({
    loadHealth: health(check({}), check({ id: 'net', name: 'Network' })),
  });
  const good = await screen.findByRole('button', { name: /^Health: All good/ });
  expect(good).toHaveTextContent('Checked 10 minutes ago');
  expect(good).toHaveTextContent('2 checks');
  fireEvent.click(good);
  expect(handlers.onOpenTab).toHaveBeenCalledWith('monitor');
  unmount();
  show({
    loadHealth: health(
      check({}),
      check({
        id: 'invoice',
        name: 'Invoice server',
        status: 'warn',
        detail: 'unreachable',
      }),
    ),
  });
  expect(
    await screen.findByRole('button', { name: /^Health: 1 warning/ }),
  ).toHaveTextContent('Invoice server: unreachable');
});

// Continue where you left off -----------------------------------------------

it('continues from four top-level conversations as cards in list order', () => {
  const conversations = [
    conversation('pinned', 'Solstice poster', {
      pinned: true,
      category: 'designer',
    }),
    conversation('working', 'Quarterly brief', {
      activity_state: 'active',
      activity_phase: 'tool_call',
    }),
    conversation('agent-1', 'Researcher', {
      parent_conversation_id: 'working',
      generation_state: [generation('running')],
    }),
    conversation('code', 'Bakery website', { category: 'code' }),
    conversation('flow', 'Morning digest', { category: 'workflow' }),
    conversation('fifth', 'Fifth one'),
  ];
  show({ conversations });
  const cards = list('Continue where you left off');
  expect(
    within(cards)
      .getAllByRole('button')
      .map((button) => button.getAttribute('aria-label')),
  ).toEqual([
    'Open Solstice poster',
    'Open Quarterly brief',
    'Open Bakery website',
    'Open Morning digest',
  ]);
  const button = (name: string) => within(cards).getByRole('button', { name });
  expect(button('Open Solstice poster')).toHaveTextContent('Design');
  expect(button('Open Solstice poster')).toHaveTextContent('Pinned');
  expect(button('Open Quarterly brief')).toHaveTextContent(
    'Agents working · Tool call',
  );
  expect(button('Open Quarterly brief')).toHaveTextContent('1 agent');
  expect(button('Open Bakery website')).toHaveTextContent('Code folder');
  expect(button('Open Morning digest')).toHaveTextContent('Workflow');
  fireEvent.click(button('Open Bakery website'));
  expect(handlers.onOpenConversation).toHaveBeenCalledWith('code');
  expect(screen.getByRole('link', { name: 'Library' })).toHaveAttribute(
    'href',
    '/library',
  );
});

// Since yesterday evening --------------------------------------------------

it('draws since yesterday evening as a timeline that links to where each thing happened', async () => {
  const tasks = [
    task('a', 'Morning digest', {
      recent_runs: [
        { status: 'completed', started_at: at(26, 7) },
        // Before 6 PM yesterday: outside the window.
        { status: 'failed', started_at: at(25, 17, 59) },
        // After the supplied clock: not "since yesterday evening" yet.
        { status: 'failed', started_at: at(26, 11) },
      ],
    }),
    task('b', 'Invoice sync', {
      recent_runs: [{ status: 'failed', started_at: at(25, 22) }],
    }),
  ];
  const monitor = monitorSnapshot({
    extraction_journal: [
      {
        timestamp: at(26, 2),
        summary: '',
        contradictions_blocked: 0,
        low_confidence_skipped: 0,
        islands_repaired: 0,
        threads: [
          { label: 'Trip plan', extracted: 3, saved: 3 },
          { label: 'Menu', extracted: 1, saved: 1 },
        ],
        errors: [],
      },
    ],
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
    ],
  });
  const conversations = [
    conversation('agent-done', 'Designer', {
      parent_conversation_id: 'poster',
      activity_state: 'terminal',
      updated_at: at(26, 9, 12),
    }),
  ];
  show({
    conversations,
    monitor,
    loadTasks: vi.fn().mockResolvedValue(page(tasks)),
  });
  const timeline = await screen.findByRole('list', {
    name: 'Since yesterday evening',
  });
  await within(timeline).findByText('Invoice sync failed');
  expect(
    within(timeline)
      .getAllByRole('listitem')
      .map((item) => item.textContent),
  ).toEqual([
    `${clockTime(22, 0)}Invoice sync failedFailed`,
    `${clockTime(2, 0)}Learned 4 new thingsFrom Trip plan and Menu`,
    `${clockTime(3, 0)}Dream Cycle tidied memoryMerged 1 duplicate, enriched 2 memories, inferred 1 connection`,
    `${clockTime(7, 0)}Morning digest ranCompleted`,
    `${clockTime(9, 12)}Designer finishedAgent`,
  ]);
  const open = (name: RegExp) =>
    fireEvent.click(within(timeline).getByRole('button', { name }));
  open(/Invoice sync failed/);
  expect(handlers.onOpenWorkflows).toHaveBeenLastCalledWith('b');
  open(/Learned 4 new things/);
  expect(handlers.onOpenTab).toHaveBeenLastCalledWith('knowledge');
  open(/Dream Cycle tidied memory/);
  expect(handlers.onOpenTab).toHaveBeenLastCalledWith('monitor');
  open(/Designer finished/);
  expect(handlers.onOpenConversation).toHaveBeenLastCalledWith('agent-done');
  fireEvent.click(screen.getByRole('button', { name: 'Monitor' }));
  expect(handlers.onOpenTab).toHaveBeenLastCalledWith('monitor');
});

it('reports a quiet Dream Cycle and a journal-less Dream run honestly', async () => {
  const { unmount } = show({
    monitor: monitorSnapshot({
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
    }),
  });
  expect(
    await screen.findByRole('list', { name: 'Since yesterday evening' }),
  ).toHaveTextContent('Dream Cycle ranFound nothing to change');
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

// Learned this week --------------------------------------------------------

it('lists what Row-Bot learned this week as chips and the top insight as a card', async () => {
  const dream = (day: number, subjects: string[]) => ({
    timestamp: at(day, 3),
    summary: '',
    merges: [],
    enrichments: subjects.map((subject) => ({
      subject,
      old_length: 1,
      new_length: 2,
      new_description: '',
    })),
    inferred_relations: [],
    errors: [],
  });
  show({
    monitor: monitorSnapshot({
      dream_journal: [
        dream(26, ['Priya prefers calls before 10', 'Lisbon trip']),
        dream(24, [
          'lisbon trip',
          'Sourdough at 75% water',
          'Menu update ships Friday',
          'Riverside Flour',
          'Bakery hours',
        ]),
        // Last month: not this week.
        dream(1, ['Old news']),
      ],
      extraction_journal: [
        {
          timestamp: at(25, 10),
          summary: '',
          contradictions_blocked: 0,
          low_confidence_skipped: 0,
          islands_repaired: 0,
          threads: [
            { label: 'Trip plan', extracted: 9, saved: 8 },
            { label: 'Menu', extracted: 4, saved: 4 },
            { label: 'Nothing new', extracted: 1, saved: 0 },
          ],
          errors: [],
        },
      ],
    }),
    loadInsights: vi.fn().mockResolvedValue({
      schema_version: 1,
      revision: 'i',
      curator_report: null,
      items: [
        {
          id: 'i-1',
          title: 'Move Invoice sync to 2 PM?',
          body: 'It failed three times this week at 12:30.',
          suggestion: 'Run it at 2 PM while the server is idle.',
          category: 'workflow',
          severity: 'suggestion',
          status: 'new',
          proposals: [],
        },
      ],
    }),
  });
  const learned = screen.getByRole('region', { name: 'Learned this week' });
  expect(learned).toHaveTextContent('12 memory updates from 2 conversations');
  const chips = within(learned).getByRole('list', {
    name: 'Memories added to this week',
  });
  expect(
    within(chips)
      .getAllByRole('button')
      .map((chip) => chip.textContent),
  ).toEqual([
    'Priya prefers calls before 10',
    'Lisbon trip',
    'Sourdough at 75% water',
    'Menu update ships Friday',
    'Riverside Flour',
    '+1 more',
  ]);
  fireEvent.click(within(chips).getByRole('button', { name: 'Lisbon trip' }));
  expect(handlers.onOpenTab).toHaveBeenLastCalledWith('knowledge');
  expect(
    await within(learned).findByText('Move Invoice sync to 2 PM?'),
  ).toBeVisible();
  expect(learned).toHaveTextContent('Run it at 2 PM while the server is idle.');
  fireEvent.click(
    within(learned).getByRole('button', { name: 'Open Insights' }),
  );
  expect(handlers.onOpenTab).toHaveBeenLastCalledWith('insights');
});

it('says so when nothing was learned this week and leaves out unreadable Insights', async () => {
  const loadInsights = vi.fn().mockRejectedValue(new Error('owner only'));
  show({ loadInsights });
  const learned = screen.getByRole('region', { name: 'Learned this week' });
  expect(learned).toHaveTextContent(
    'Nothing new this week. Row-Bot learns from your conversations as you go.',
  );
  await act(async () => {});
  expect(loadInsights).toHaveBeenCalledOnce();
  expect(
    within(learned).queryByRole('button', { name: 'Open Insights' }),
  ).toBeNull();
});

// Motion -------------------------------------------------------------------

it('fades cards in and pulses live work only when motion is allowed', () => {
  const working = [
    conversation('chat-a', 'Research brief', { activity_state: 'active' }),
  ];
  const { container, unmount } = show({ conversations: working });
  expect(container.querySelector('[data-entrance="stagger"]')).not.toBeNull();
  expect(container.querySelector('[data-pulse="true"]')).not.toBeNull();
  unmount();
  vi.mocked(window.matchMedia).mockImplementation(
    (query: string) =>
      ({
        matches: query === '(prefers-reduced-motion: reduce)',
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }) as unknown as MediaQueryList,
  );
  const reduced = show({ conversations: working });
  expect(
    reduced.container.querySelector('[data-entrance="stagger"]'),
  ).toBeNull();
  expect(reduced.container.querySelector('[data-pulse="true"]')).toBeNull();
});

// Reads --------------------------------------------------------------------

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
    await screen.findByRole('button', { name: /Next: Fresh/ }),
  ).toBeVisible();
  await act(async () => {
    first.resolve(page([task('stale', 'Stale', { next_run: at(26, 10) })]));
  });
  expect(screen.queryByRole('button', { name: /Next: Stale/ })).toBeNull();
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

it('lists an app that is on but signed out or broken under Needs you, with its fix one click away', async () => {
  const entry = (fields: Record<string, unknown>) => ({
    kind: 'mcp',
    parent_id: null,
    description: '',
    app: null,
    icon: 'letter:A',
    verified: false,
    source: 'recommended',
    publisher: '',
    version: '',
    installed: true,
    enabled: true,
    account_label: '',
    compatibility: 'supported',
    evidence: 'inspected',
    tested_with_row_bot: false,
    attributions: [],
    children: [],
    lifecycle: 'installed',
    readiness: 'ready',
    blockers: [],
    next_action: { kind: 'try', label: 'Try it' },
    ...fields,
  });
  const loadApps = vi.fn(async () => ({
    schema_version: 1,
    revision: 'r',
    total: 3,
    next_cursor: null,
    sources: [],
    items: [
      entry({
        id: 'mcp:notion',
        name: 'Notion',
        readiness: 'needs_sign_in',
        blockers: [
          {
            code: 'expired',
            severity: 'blocking',
            message: 'The saved sign-in no longer works. Sign in again.',
            subject: '',
          },
        ],
        next_action: { kind: 'sign_in', label: 'Sign in again' },
      }),
      entry({ id: 'mcp:linear', name: 'Linear' }),
      entry({
        id: 'mcp:off',
        name: 'Figma',
        lifecycle: 'off',
        readiness: 'attention',
      }),
    ],
  }));
  const onOpenApp = vi.fn();
  show({ loadApps, onOpenApp } as unknown as Partial<OverviewHomeProps>);
  const needs = await screen.findByRole('list', { name: 'Needs you' });
  await within(needs).findByText('Sign in to Notion');
  expect(needs).toHaveTextContent('The saved sign-in no longer works.');
  expect(needs).not.toHaveTextContent('Linear'); // Ready apps and ones turned off don't need you.
  expect(needs).not.toHaveTextContent('Figma');
  fireEvent.click(within(needs).getByRole('button', { name: 'Sign in again' }));
  expect(onOpenApp).toHaveBeenCalledWith('mcp:notion', true);
});

it('lists a goal waiting for your answer in Needs you', async () => {
  show({
    conversations: [
      conversation('chat-goal', 'Autumn launch', {
        activity_state: 'attention',
        activity_phase: 'goal_needs_you',
      }),
    ],
    loadTasks: vi.fn().mockResolvedValue(page([])),
  });
  const needs = await screen.findByRole('list', { name: 'Needs you' });
  await within(needs).findByText('Autumn launch');
  expect(within(needs).getByText('Goal waiting for your answer')).toBeVisible();
  expect(buttonNames('Needs you')).toContain(
    'Answer the goal in Autumn launch',
  );
});
