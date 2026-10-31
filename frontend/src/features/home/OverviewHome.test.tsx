import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { LiveUpdates } from '../../api/live-updates';
import type {
  AttentionProblem,
  ConversationView,
  GenerationState,
  LiveFrame,
  MonitorSnapshot,
  OnboardingSnapshot,
  PendingApproval,
  PendingApprovalPage,
  ProblemFix,
  SystemDiagnosisCheck,
  TaskSummary,
  TaskSummaryPage,
} from '../../api/types';
import { provideLive } from '../../live';
import { OverlayProvider } from '../../ui/overlays';
import OverviewHome, { type OverviewHomeProps } from './OverviewHome';
import AttentionIndicator from '../shell/AttentionIndicator';

const runtime = vi.hoisted(() => ({
  approval: vi.fn(),
  intent: vi.fn(),
  // A failed workflow's Run again: the reviewed run Workflows uses.
  getSnapshot: () => ({ handshake: { client_session_id: 'session-1' } }),
  taskRunReview: vi.fn(),
  command: vi.fn(),
  taskRun: vi.fn(),
  waits: vi.fn(async () => ({ schema_version: 1, items: [] as unknown[] })),
  stopWait: vi.fn(async () => ({ schema_version: 1, items: [] })),
  dismissMemoryNotice: vi.fn(async () => ({ dismissed: true })),
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

const fix = (patch: Partial<ProblemFix> & Pick<ProblemFix, 'kind'>) =>
  ({ href: null, target: null, name: '', ...patch }) as ProblemFix;

function problem(patch: Partial<AttentionProblem> = {}): AttentionProblem {
  return {
    id: 'health:disk',
    title: 'Disk needs attention',
    detail: '1.2 GB free (97% used)',
    place: 'health',
    fix: fix({ kind: 'check_again', name: 'Disk' }),
    ...patch,
  };
}

/** The shared attention read: what needs you, as the sidebar reads it. */
function attention(...problems: AttentionProblem[]) {
  return vi.fn().mockResolvedValue({
    schema_version: 1,
    problems,
    update: null,
  });
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

function learned(saved: number, day = 24) {
  return monitorSnapshot({
    extraction_journal: [
      {
        timestamp: at(day, 10),
        summary: '',
        contradictions_blocked: 0,
        low_confidence_skipped: 0,
        islands_repaired: 0,
        threads: [{ label: 'Trip plan', extracted: 5, saved }],
        errors: [],
      },
    ],
  });
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

const handlers = {
  onOpenConversation: vi.fn(),
  onOpenWorkflows: vi.fn(),
  onOpenTab: vi.fn(),
  onHideSetup: vi.fn(),
  onAsk: vi.fn(),
  onNewResource: vi.fn(),
  onNewWorkflow: vi.fn(),
};

/** Where a row or fix took the person. */
function Where() {
  const location = useLocation();
  return (
    <output aria-label="Location">
      {location.pathname + location.search + location.hash}
    </output>
  );
}

function show(props: Partial<OverviewHomeProps> = {}) {
  const loadTasks =
    props.loadTasks ?? vi.fn().mockResolvedValue(page([] as TaskSummary[]));
  const view = render(
    <MemoryRouter>
      <OverlayProvider>
        <OverviewHome
          conversations={[]}
          setup={null}
          monitor={null}
          loadTasks={loadTasks}
          now={now}
          {...handlers}
          {...props}
        />
        <Where />
      </OverlayProvider>
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

/** The one sentence under the greeting, or "" when there is none. */
function summary() {
  return (
    screen.getByRole('heading', { level: 2 }).nextElementSibling?.textContent ??
    ''
  );
}

/** The sections under the greeting, in order. */
function sections() {
  const home = screen.getByRole('region', { name: /^Good / });
  return within(home)
    .queryAllByRole('region')
    .map((region) => region.getAttribute('aria-labelledby'))
    .filter(Boolean)
    .map((id) => document.getElementById(id!)!.textContent);
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

// States -------------------------------------------------------------------

it('first run: the "Choose a model" step is the only thing under the greeting', async () => {
  show({
    setup: setupSnapshot({
      setup_complete: false,
      needs_model: true,
      completed_steps: [],
      dismissed_home_card: true,
    }),
    conversations: [conversation('chat-a', 'Old chat')],
    loadApprovals: vi.fn().mockResolvedValue(approvals(approval())),
    loadAttention: attention(problem()),
    monitor: learned(4),
  });
  expect(
    screen.getByRole('heading', { level: 2, name: 'Good morning' }),
  ).toBeVisible();
  const step = screen.getByRole('region', { name: 'Continue setup' });
  expect(step).toHaveTextContent('Choose how Row-Bot thinks');
  expect(step).toHaveTextContent('Row-Bot needs a model before it can answer.');
  expect(
    within(step).getByRole('link', { name: 'Choose a model' }),
  ).toHaveAttribute('href', '/setup');
  // It can't be hidden, and nothing else competes with it.
  expect(
    within(step).queryByRole('button', { name: 'Hide setup reminder' }),
  ).toBeNull();
  await act(async () => {});
  expect(summary()).toBe('');
  expect(screen.queryByRole('textbox', { name: 'Ask Row-Bot' })).toBeNull();
  expect(screen.queryByRole('group', { name: 'Quick starts' })).toBeNull();
  expect(sections()).toEqual([]);
});

it('empty profile: the greeting, "All quiet", the Ask box and three quick starts, and nothing empty', async () => {
  show({
    buddy: <img alt="Buddy" src="data:," />,
    loadApprovals: vi.fn().mockResolvedValue(approvals()),
    loadAttention: attention(),
    loadHealth: health(check({})),
  });
  const heading = screen.getByRole('heading', {
    level: 2,
    name: 'Good morning',
  });
  expect(
    within(heading.closest('header')!).getByRole('img', { name: 'Buddy' }),
  ).toBeVisible();
  // Nothing is said until what needs you has been read.
  expect(summary()).toBe('');
  await vi.waitFor(() => expect(summary()).toBe('All quiet.'));
  expect(screen.getByRole('textbox', { name: 'Ask Row-Bot' })).toBeVisible();
  const quick = screen.getByRole('group', { name: 'Quick starts' });
  expect(
    within(quick)
      .getAllByRole('button')
      .map((button) => button.getAttribute('aria-label')),
  ).toEqual(['New design', 'New code folder', 'New workflow']);
  // No "All caught up" box, no empty Recent or Today.
  expect(sections()).toEqual([]);
  expect(screen.queryByText(/caught up/i)).toBeNull();
});

it('busy profile: Needs you right under the Ask box, then Recent and Today', async () => {
  show({
    conversations: [
      conversation('chat-a', 'Research sprint', {
        activity_state: 'active',
        updated_at: at(26, 9, 20),
      }),
      conversation('chat-b', 'Menu ideas', { updated_at: at(25, 18) }),
    ],
    loadApprovals: vi.fn().mockResolvedValue(approvals(approval())),
    loadAttention: attention(),
    loadTasks: vi
      .fn()
      .mockResolvedValue(
        page([task('digest', 'Morning digest', { next_run: at(26, 10) })]),
      ),
    monitor: learned(12),
  });
  await screen.findByText('Send an email to Riverside Flour');
  expect(summary()).toBe('1 thing needs you.');
  expect(sections()).toEqual(['Needs you1', 'Recent', 'Today']);
  expect(buttonNames('Recent')).toEqual([
    'Open Research sprint',
    'Open Menu ideas',
  ]);
  expect(buttonNames('Today')).toEqual([
    'Open Monitor: agents at work',
    'Open Workflows: Morning digest',
    'Open Knowledge',
  ]);
});

it('all quiet: names the next workflow run instead', async () => {
  show({
    loadApprovals: vi.fn().mockResolvedValue(approvals()),
    loadAttention: attention(),
    loadTasks: vi.fn().mockResolvedValue(
      page([
        task('later', 'Weekly report', { next_run: at(28, 9) }),
        task('digest', 'Morning digest', { next_run: at(26, 10) }),
        task('off', 'Paused one', {
          enabled: false,
          next_run: at(26, 9, 45),
        }),
      ]),
    ),
  });
  await vi.waitFor(() =>
    expect(summary()).toBe('Next: Morning digest in 30 minutes.'),
  );
  expect(screen.queryByRole('region', { name: /^Needs you/ })).toBeNull();
});

it('a run about to start is "in under a minute", never "just now"', async () => {
  show({
    loadApprovals: vi.fn().mockResolvedValue(approvals()),
    loadAttention: attention(),
    loadTasks: vi.fn().mockResolvedValue(
      page([
        task('digest', 'Morning digest', {
          next_run: new Date(now.getTime() + 30_000).toISOString(),
        }),
      ]),
    ),
  });
  await vi.waitFor(() =>
    expect(summary()).toBe('Next: Morning digest in under a minute.'),
  );
});

it('error: an unreadable source is one quiet line under Today, not a box', async () => {
  show({
    loadTasks: vi.fn().mockRejectedValue(new Error('offline')),
    loadHealth: vi.fn().mockRejectedValue(new Error('offline')),
  });
  const today = await screen.findByRole('region', { name: 'Today' });
  expect(within(today).getByRole('status')).toHaveTextContent(
    'Couldn’t read workflows and health checks just now. Their tabs may say more.',
  );
  expect(within(today).queryByRole('list')).toBeNull();
  expect(summary()).toBe('All quiet.');
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

// One count (B383) -----------------------------------------------------------

it('shows the same number on Home and in the sidebar for the same items, without the setup reminder', async () => {
  const conversations = [
    // Its approval is in the list: counted once.
    conversation('chat-mail', 'Supplier price check', {
      generation_state: [generation('waiting_approval')],
    }),
    conversation('chat-goal', 'Autumn launch', {
      activity_state: 'attention',
      activity_phase: 'goal_needs_you',
    }),
  ];
  const loadApprovals = vi.fn().mockResolvedValue(approvals(approval()));
  const loadAttention = attention(
    problem(),
    problem({
      id: 'workflow:wf-brief',
      title: 'Morning brief failed',
      detail: 'Run it again, or open it to see why.',
      place: 'workflows',
      fix: fix({
        kind: 'open',
        href: '/?tab=workflows&workflow=wf-brief',
        name: 'Morning brief',
      }),
    }),
  );
  render(
    <MemoryRouter>
      <OverlayProvider>
        <AttentionIndicator
          load={loadAttention}
          loadApprovals={loadApprovals}
          conversations={conversations}
        />
        <OverviewHome
          conversations={conversations}
          setup={setupSnapshot()}
          monitor={null}
          loadApprovals={loadApprovals}
          loadAttention={loadAttention}
          now={now}
          {...handlers}
        />
      </OverlayProvider>
    </MemoryRouter>,
  );
  const sidebar = await screen.findByRole('button', {
    name: '4 things need you. Review',
  });
  expect(sidebar).toBeVisible();
  await vi.waitFor(() => expect(summary()).toBe('4 things need you.'));
  expect(screen.getByRole('heading', { name: /^Needs you/ })).toHaveTextContent(
    'Needs you4',
  );
  // The setup reminder shows as a row but is not one of the four.
  expect(
    screen.getByRole('region', { name: 'Continue setup' }),
  ).toHaveTextContent('Finish setup');
  // One read of each, shared by both.
  expect(loadAttention).toHaveBeenCalledOnce();
  expect(loadApprovals).toHaveBeenCalledOnce();
});

// Needs you ------------------------------------------------------------------

it('lists waiting approvals and opens each on its card', async () => {
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
  expect(summary()).toBe('2 things need you.');
  // Each opens on its own card, with what exactly will happen; nothing is approved from the row (F31).
  expect(within(mail).queryByRole('button', { name: 'Approve' })).toBeNull();
  await act(async () =>
    fireEvent.click(
      within(mail).getByRole('button', {
        name: 'Review: Send an email to Riverside Flour',
      }),
    ),
  );
  expect(
    screen.getByRole('dialog', { name: 'Review and approve' }),
  ).toBeInTheDocument();
  expect(runtime.intent).not.toHaveBeenCalled();
});

it('shows three waiting items at first and the rest behind "more waiting"', async () => {
  show({
    conversations: [
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
    ],
    loadAttention: attention(problem()),
  });
  const needs = await screen.findByRole('list', { name: 'Needs you' });
  await within(needs).findByText('Deploy plan');
  expect(buttonNames('Needs you')).toEqual([
    'Review approval in Deploy plan',
    'Review approval in Delegated review',
    'Review approval in Agent approval',
  ]);
  const more = screen.getByRole('button', { name: '+1 more waiting' });
  expect(more).toHaveAttribute('aria-expanded', 'false');
  fireEvent.click(more);
  expect(more).toHaveAttribute('aria-expanded', 'true');
  expect(more).toHaveTextContent('Show fewer');
  const meta = (name: string) =>
    within(needs).getByRole('button', { name }).textContent;
  expect(meta('Review approval in Deploy plan')).toContain(
    'Waiting for your approval',
  );
  // Attention without an approval phase is not described as an approval.
  expect(meta('Review approval in Delegated review')).toContain(
    'Agent work needs your attention',
  );
  expect(within(needs).getByText('Disk needs attention')).toBeVisible();
  fireEvent.click(
    within(needs).getByRole('button', {
      name: 'Review approval in Deploy plan',
    }),
  );
  expect(handlers.onOpenConversation).toHaveBeenCalledWith('chat-wait');
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
});

it('lists a goal waiting for your answer in Needs you', async () => {
  show({
    conversations: [
      conversation('chat-goal', 'Autumn launch', {
        activity_state: 'attention',
        activity_phase: 'goal_needs_you',
      }),
    ],
  });
  const needs = await screen.findByRole('list', { name: 'Needs you' });
  expect(within(needs).getByText('Goal waiting for your answer')).toBeVisible();
  expect(buttonNames('Needs you')).toEqual([
    'Answer the goal in Autumn launch',
  ]);
});

it('offers each problem its one fix beside it and reads again after it (Phase 18)', async () => {
  const loadAttention = vi
    .fn()
    .mockResolvedValueOnce({
      schema_version: 1,
      update: null,
      problems: [
        problem(),
        problem({
          id: 'tunnel',
          title: "Your public tunnel isn't running",
          detail: 'agent failed',
          place: 'access',
          fix: fix({
            kind: 'open',
            href: '/settings/access#tunnel',
            name: 'Public link',
          }),
        }),
        problem({
          id: 'app:mcp:notion',
          title: 'Sign in to Notion',
          detail: 'The saved sign-in no longer works. Sign in again.',
          place: 'mcp',
          fix: fix({
            kind: 'open',
            href: '/settings/apps/item?id=mcp:notion',
            name: 'Notion',
          }),
        }),
      ],
    })
    .mockResolvedValue({ schema_version: 1, update: null, problems: [] });
  const onRunDiagnosis = vi.fn().mockResolvedValue({
    schema_version: 1,
    hourly_network_checks: true,
    checks: [],
  });
  show({ loadAttention, onRunDiagnosis });
  const needs = await screen.findByRole('list', { name: 'Needs you' });
  await within(needs).findByText('Disk needs attention');
  expect(needs).toHaveTextContent('The saved sign-in no longer works.');
  // The exact setting, as an icon with its name.
  expect(
    within(needs).getByRole('link', { name: 'Open Public link settings' }),
  ).toHaveAttribute('href', '/settings/access#tunnel');
  fireEvent.click(
    within(needs).getByRole('button', { name: 'Open Sign in to Notion' }),
  );
  expect(screen.getByRole('status', { name: 'Location' })).toHaveTextContent(
    '/settings/apps/item?id=mcp:notion',
  );
  fireEvent.click(
    within(needs).getByRole('button', { name: 'Open Disk needs attention' }),
  );
  expect(handlers.onOpenTab).toHaveBeenCalledWith('monitor');

  fireEvent.click(
    within(needs).getByRole('button', { name: 'Check Disk again' }),
  );
  expect(onRunDiagnosis).toHaveBeenCalledOnce();
  await vi.waitFor(() =>
    expect(screen.queryByText('Disk needs attention')).toBeNull(),
  );
  expect(loadAttention).toHaveBeenCalledTimes(2);
  expect(summary()).toBe('All quiet.');
});

it('runs a failed workflow again in place with the reviewed run (Phase 18)', async () => {
  const loadTasks = vi.fn().mockResolvedValue(page([]));
  const loadAttention = attention(
    problem({
      id: 'workflow:wf-brief',
      title: 'Morning brief failed',
      detail: 'Run it again, or open it to see why.',
      place: 'workflows',
      fix: fix({
        kind: 'open',
        href: '/?tab=workflows&workflow=wf-brief',
        name: 'Morning brief',
      }),
    }),
  );
  show({ loadTasks, loadAttention });
  const needs = await screen.findByRole('list', { name: 'Needs you' });
  fireEvent.click(
    await within(needs).findByRole('button', {
      name: 'Open Morning brief failed',
    }),
  );
  expect(handlers.onOpenWorkflows).toHaveBeenCalledWith('wf-brief');
  fireEvent.click(
    within(needs).getByRole('button', { name: 'Run Morning brief again' }),
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
  // What needs you and the workflows are read again.
  expect(loadAttention).toHaveBeenCalledTimes(2);
  expect(loadTasks).toHaveBeenCalledTimes(2);
});

it('reminds about optional setup as a row until hidden and reports a hide failure', () => {
  const { rerender } = show({ setup: setupSnapshot() });
  const region = screen.getByRole('region', { name: 'Continue setup' });
  expect(region).toHaveTextContent('Finish setup');
  expect(region).toHaveTextContent('1 of 2 done');
  expect(
    within(region).getByRole('link', { name: 'Continue setup' }),
  ).toHaveAttribute('href', '/setup');
  // A reminder, not a need: no count.
  expect(screen.getByRole('heading', { name: 'Needs you' })).toBeVisible();
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
  expect(screen.queryByRole('region', { name: /^Needs you/ })).toBeNull();
});

it('omits the hide control when Home cannot hide the reminder', () => {
  show({ setup: setupSnapshot(), onHideSetup: undefined });
  expect(
    screen.queryByRole('button', { name: 'Hide setup reminder' }),
  ).toBeNull();
});

// Waiting (5.3.1) -----------------------------------------------------------------

it('lists what conversations wait for under Waiting, not Needs you, each with Stop', async () => {
  const user = userEvent.setup();
  runtime.waits.mockResolvedValueOnce({
    schema_version: 1,
    items: [
      {
        wait_id: 'a1b2c3d4e5f6',
        conversation_id: 'ship',
        conversation_title: 'Ship 5.3.1',
        kind: 'check',
        label: 'CI on PR 590 is green',
        how: 'checks every 2 min',
        until: '2026-09-26T11:30',
        status: 'waiting',
      },
    ],
  });
  show();

  const waiting = await screen.findByRole('list', { name: 'Waiting' });
  expect(waiting).toHaveTextContent(
    'CI on PR 590 is greenShip 5.3.1 · checks every 2 min',
  );
  expect(screen.queryByRole('list', { name: 'Needs you' })).toBeNull();
  // Not "All quiet" while something is waited for.
  expect(summary()).toBe('Waiting for 1 thing.');
  await user.click(
    within(waiting).getByRole('button', { name: 'Open Ship 5.3.1' }),
  );
  expect(handlers.onOpenConversation).toHaveBeenCalledWith('ship');
  await user.click(
    within(waiting).getByRole('button', { name: 'Stop waiting' }),
  );
  expect(runtime.stopWait).toHaveBeenCalledWith('a1b2c3d4e5f6');
  await waitFor(() =>
    expect(screen.queryByRole('list', { name: 'Waiting' })).toBeNull(),
  );
});

// Ask box and quick starts -------------------------------------------------------

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

it('starts a new design, code folder or workflow from the quick-start icons', () => {
  show();
  const quick = screen.getByRole('group', { name: 'Quick starts' });
  fireEvent.click(within(quick).getByRole('button', { name: 'New design' }));
  expect(handlers.onNewResource).toHaveBeenLastCalledWith('artifact');
  fireEvent.click(
    within(quick).getByRole('button', { name: 'New code folder' }),
  );
  expect(handlers.onNewResource).toHaveBeenLastCalledWith('workspace');
  fireEvent.click(within(quick).getByRole('button', { name: 'New workflow' }));
  expect(handlers.onNewWorkflow).toHaveBeenCalledTimes(1);
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

// Recent -------------------------------------------------------------------

it('lists the last three chats, newest first, with an "All" link to the Library', () => {
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
      conversation('older', 'Menu ideas', { updated_at: at(25, 18) }),
      conversation('mid', 'Shop rota', { updated_at: at(26, 7) }),
    ],
  });
  const recent = screen.getByRole('region', { name: 'Recent' });
  expect(buttonNames('Recent')).toEqual([
    'Open Research brief',
    'Open Shop rota',
    'Open Menu ideas',
  ]);
  expect(within(recent).getByRole('link', { name: 'All' })).toHaveAttribute(
    'href',
    '/library',
  );
  fireEvent.click(
    within(recent).getByRole('button', { name: 'Open Shop rota' }),
  );
  expect(handlers.onOpenConversation).toHaveBeenCalledWith('mid');
});

// Today --------------------------------------------------------------------

it('shows only the facts that exist under Today, each opening its tab', async () => {
  show({
    conversations: [
      conversation('chat-a', 'Research sprint', { activity_state: 'active' }),
      conversation('chat-b', 'Quick question', {
        generation_state: [generation('running')],
      }),
      // Waiting for approval is not working.
      conversation('chat-c', 'Both states', {
        activity_state: 'active',
        generation_state: [generation('waiting_approval')],
      }),
    ],
    loadTasks: vi
      .fn()
      .mockResolvedValue(
        page([task('digest', 'Morning digest', { next_run: at(26, 10) })]),
      ),
    loadHealth: health(
      check({
        id: 'documents',
        name: 'Documents',
        status: 'warn',
        detail: 'rebuild recommended',
        fix: fix({
          kind: 'open',
          href: '/settings/documents#embedding',
          name: 'Documents',
        }),
      }),
      check({ id: 'search', name: 'Search', status: 'warn', detail: 'Slow' }),
    ),
    monitor: learned(12),
  });
  const today = await screen.findByRole('list', { name: 'Today' });
  await within(today).findByText('Documents: rebuild recommended');
  expect(
    within(today)
      .getAllByRole('button')
      .map((button) => button.textContent),
  ).toEqual([
    '2 agents are working',
    'Morning digest runs in 30 minutes',
    'Documents: rebuild recommendedAnd 1 more warning',
    '12 memories saved this week',
  ]);
  expect(
    within(today).getByRole('link', { name: 'Open Documents settings' }),
  ).toHaveAttribute('href', '/settings/documents#embedding');
  fireEvent.click(
    within(today).getByRole('button', {
      name: 'Open Workflows: Morning digest',
    }),
  );
  expect(handlers.onOpenWorkflows).toHaveBeenCalledWith('digest');
  fireEvent.click(
    within(today).getByRole('button', { name: 'Open Knowledge' }),
  );
  expect(handlers.onOpenTab).toHaveBeenLastCalledWith('knowledge');
  fireEvent.click(
    within(today).getByRole('button', { name: 'Open Monitor: Documents' }),
  );
  expect(handlers.onOpenTab).toHaveBeenLastCalledWith('monitor');
});

it('names the one agent at work and leaves out memories saved before this week', async () => {
  show({
    conversations: [
      conversation('chat-a', 'Research sprint', { activity_state: 'active' }),
    ],
    monitor: learned(9, 10),
  });
  const today = await screen.findByRole('list', { name: 'Today' });
  expect(
    within(today)
      .getAllByRole('button')
      .map((button) => button.textContent),
  ).toEqual(['Research sprint is working']);
});

it('leaves a stale health warning out of Today (B384)', async () => {
  // Kept before the channel was turned off (or by an older Row-Bot): not current.
  const loadHealth = health(
    check({
      id: 'channel:whatsapp',
      name: 'WhatsApp',
      status: 'warn',
      detail: 'Stopped',
      stale: true,
    }),
  );
  show({ loadHealth });
  await act(async () => {});
  expect(loadHealth).toHaveBeenCalledOnce();
  expect(screen.queryByText(/WhatsApp/)).toBeNull();
  expect(screen.queryByRole('region', { name: 'Today' })).toBeNull();
});

// Motion and reads ---------------------------------------------------------

it('fades the column in only when motion is allowed', () => {
  const { container, unmount } = show();
  expect(container.querySelector('[data-entrance="stagger"]')).not.toBeNull();
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
  const reduced = show();
  expect(
    reduced.container.querySelector('[data-entrance="stagger"]'),
  ).toBeNull();
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
  expect(screen.queryByText(/Late/)).toBeNull();
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
  await vi.waitFor(() => expect(summary()).toBe('Next: Fresh in 2 hours.'));
  await act(async () => {
    first.resolve(page([task('stale', 'Stale', { next_run: at(26, 10) })]));
  });
  expect(summary()).toBe('Next: Fresh in 2 hours.');
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

it('offers choosing a folder for a workflow its removed code folder stopped (N9)', async () => {
  render(
    <MemoryRouter>
      <OverviewHome
        conversations={[]}
        setup={setupSnapshot()}
        monitor={null}
        loadApprovals={vi.fn().mockResolvedValue(approvals())}
        loadAttention={attention(
          problem({
            id: 'workflow:wf-landing',
            title: 'Landing checks needs a code folder',
            detail:
              "Its code folder isn't a code folder any more, so it didn't run. Choose another.",
            place: 'workflows',
            fix: fix({
              kind: 'choose_folder',
              href: '/?tab=workflows&workflow=wf-landing&fix=folder',
              name: 'Landing checks',
            }),
          }),
        )}
        now={now}
        {...handlers}
      />
    </MemoryRouter>,
  );
  expect(
    await screen.findByText('Landing checks needs a code folder'),
  ).toBeVisible();
  expect(screen.getByRole('link', { name: 'Choose a folder' })).toHaveAttribute(
    'href',
    '/?tab=workflows&workflow=wf-landing&fix=folder',
  );
});

// Last in this file: it gives the window live updates.
it('reads the checks again once an approval is answered, so the Workflows warning leaves Today at once (B414)', async () => {
  const frames: ((frame: LiveFrame) => void)[] = [];
  const live = new LiveUpdates(
    {
      async *observeLive(signal: AbortSignal) {
        while (!signal.aborted)
          yield await new Promise<LiveFrame>((resolve) => frames.push(resolve));
      },
    },
    () => Promise.resolve(),
  );
  const send = (revision: number, approvals: number) =>
    frames.shift()?.({
      epoch: 'server-a',
      revision,
      topics: {
        conversations: 0,
        runs: 0,
        approvals,
        helpers: 0,
        processes: 0,
        monitor: 0,
        models: 0,
        learning: 0,
      },
    });
  const workflows = (status: 'ok' | 'warn', waiting: string) =>
    check({
      id: 'workflows',
      name: 'Workflows',
      status,
      detail: `0 scheduled · 0 running · ${waiting} waiting`,
      settings_tab: '',
    });
  const loadHealth = vi
    .fn()
    .mockResolvedValueOnce({
      schema_version: 1,
      hourly_network_checks: true,
      checks: [workflows('warn', '1 approval')],
    })
    .mockResolvedValue({
      schema_version: 1,
      hourly_network_checks: true,
      checks: [workflows('ok', '0 approvals')],
    });
  provideLive(live);
  live.start();
  try {
    show({ loadHealth });
    const today = await screen.findByRole('list', { name: 'Today' });
    expect(
      within(today).getByText(
        'Workflows: 0 scheduled · 0 running · 1 approval waiting',
      ),
    ).toBeVisible();
    await waitFor(() => expect(frames).toHaveLength(1));
    act(() => void send(1, 0));
    await waitFor(() => expect(live.connected).toBe(true));

    // The workflow's approval was answered, here or anywhere else.
    await waitFor(() => expect(frames).toHaveLength(1));
    act(() => void send(2, 1));

    await waitFor(() =>
      expect(screen.queryByRole('list', { name: 'Today' })).toBeNull(),
    );
    expect(loadHealth).toHaveBeenCalledTimes(2);
  } finally {
    live.stop();
  }
});

it('Review shows each post or email waiting to go out in full, sets one aside, and cancels a scheduled post (G3)', async () => {
  runtime.approval.mockImplementation(async (id: string) => ({
    id,
    status: 'pending',
    revision: '0',
    nonce: `nonce-${id}`,
    policy_revision: '1',
    action_label: id === 'approval-post' ? 'x_post' : 'send_gmail_message',
    question:
      id === 'approval-post'
        ? 'Post to X as @rowbot_ai?'
        : 'Send an email to Riverside Flour?',
    reason: 'To orders@riverside.example: Reorder for next week: 40 kg rye',
    x_post:
      id === 'approval-post'
        ? {
            account_id: '111',
            username: 'rowbot_ai',
            action: 'post',
            text: 'Row-Bot 5.3.1 is out.',
            post_at: '2026-09-26T13:00',
          }
        : null,
  }));
  const cancel = vi.fn().mockResolvedValue({ cancelled: true });
  show({
    loadApprovals: vi
      .fn()
      .mockResolvedValue(
        approvals(
          approval({ id: 'approval-post', goes_out: 'post', title: 'Daily X' }),
          approval({ goes_out: 'email' }),
          approval({ id: 'approval-file', title: 'Delete notes.txt' }),
        ),
      ),
    loadScheduledPosts: vi.fn().mockResolvedValue({
      schema_version: 1,
      items: [
        {
          id: 'xs-0123456789ab',
          account_id: '111',
          username: 'rowbot_ai',
          action: 'post',
          text: 'Morning post about local AI.',
          post_at: '2026-09-26T18:00',
          media: [],
        },
      ],
    }),
    onCancelScheduledPost: cancel,
  });
  const review = await screen.findByRole('list', {
    name: 'Waiting to go out',
  });
  // Needs you keeps one row for them, and the approval that isn't going out.
  expect(
    await screen.findByRole('button', { name: 'Go to Review' }),
  ).toHaveTextContent('1 post and 1 email to review');
  expect(within(list('Needs you')).getByText('Delete notes.txt')).toBeVisible();
  const post = await within(review).findByRole('complementary', {
    name: 'Approval required: Post to X as @rowbot_ai?',
  });
  expect(within(post).getByText('Row-Bot 5.3.1 is out.')).toBeVisible();
  expect(within(post).getByText(/^Goes out at/)).toBeVisible();
  expect(
    within(post).getByRole('button', { name: 'Approve for then' }),
  ).not.toHaveAttribute('aria-keyshortcuts');
  await within(review).findByRole('complementary', {
    name: 'Approval required: Send an email to Riverside Flour?',
  });

  fireEvent.click(within(post).getByRole('button', { name: 'Not now' }));
  expect(within(review).getAllByRole('complementary')).toHaveLength(1);
  expect(
    screen.getByRole('button', { name: /1 set aside · Show/ }),
  ).toBeVisible();

  const scheduled = list('Scheduled posts');
  expect(within(scheduled).getByText(/^Today, .* · @rowbot_ai$/)).toBeVisible();
  await act(async () => {
    fireEvent.click(
      within(scheduled).getByRole('button', { name: 'Cancel this post' }),
    );
  });
  expect(cancel).toHaveBeenCalledWith('xs-0123456789ab');
  expect(screen.queryByRole('list', { name: 'Scheduled posts' })).toBeNull();
});

it('edits a scheduled post in place: its text and which pictures go, at the same time', async () => {
  const edit = vi.fn().mockResolvedValue({ edited: true });
  const waiting = {
    id: 'xs-0123456789ab',
    account_id: '111',
    username: 'rowbot_ai',
    name: 'Row-Bot',
    limit: 280,
    action: 'post' as const,
    text: 'Tonight’s chart',
    post_at: '2026-09-26T21:00',
    media: [
      {
        name: 'chart.png',
        size: 4096,
        sha256: 'c89670e22e36',
        kind: 'image' as const,
      },
      {
        name: 'photo.png',
        size: 2048,
        sha256: 'ab12cd34ef56',
        kind: 'image' as const,
      },
    ],
    status: 'scheduled' as const,
  };
  show({
    loadScheduledPosts: vi.fn().mockResolvedValue({
      schema_version: 1,
      items: [waiting],
    }),
    onEditScheduledPost: edit,
  });
  const scheduled = await screen.findByRole('list', {
    name: 'Scheduled posts',
  });
  fireEvent.click(
    within(scheduled).getByRole('button', { name: 'Edit this post' }),
  );
  const box = within(scheduled).getByRole('textbox', {
    name: 'Edit the post as @rowbot_ai',
  });
  expect(within(scheduled).getByText('15 of 280')).toBeVisible();
  fireEvent.change(box, { target: { value: 'x'.repeat(281) } });
  expect(
    within(scheduled).getByRole('button', { name: 'Save edits' }),
  ).toBeDisabled();
  fireEvent.change(box, {
    target: { value: 'Tonight’s chart, with the photo' },
  });
  fireEvent.click(
    within(scheduled).getByRole('button', { name: 'Remove chart.png' }),
  );
  await act(async () => {
    fireEvent.click(
      within(scheduled).getByRole('button', { name: 'Save edits' }),
    );
  });

  expect(edit).toHaveBeenCalledWith('xs-0123456789ab', {
    text: 'Tonight’s chart, with the photo',
    media: ['ab12cd34ef56'],
  });
  const [row] = within(scheduled).getAllByRole('listitem');
  expect(row).toHaveTextContent('Tonight’s chart, with the photo');
  expect(
    within(scheduled).queryByRole('textbox', { name: /Edit the post/ }),
  ).toBeNull();
});

it('keeps a scheduled post that didn’t go out in Review, with why, Try again, without the picture and Dismiss', async () => {
  const retry = vi.fn().mockResolvedValue({ retried: true });
  const dismiss = vi.fn().mockResolvedValue({ cancelled: true });
  const failed = {
    id: 'xs-0123456789ab',
    account_id: '111',
    username: 'rowbot_ai',
    action: 'post' as const,
    text: 'Row-Bot can keep a project lead’s update format.',
    post_at: '2026-09-26T12:00',
    media: [
      {
        name: 'memory.png',
        size: 57246,
        sha256: 'c89670e22e36',
        kind: 'image' as const,
      },
    ],
    status: 'failed' as const,
    reason:
      'Uploading memory.png to X failed (start), so nothing was posted. X refused it (403): Missing required scope.',
  };
  show({
    loadScheduledPosts: vi.fn().mockResolvedValue({
      schema_version: 1,
      items: [failed, { ...failed, id: 'xs-ba9876543210', media: [] }],
    }),
    onRetryScheduledPost: retry,
    onCancelScheduledPost: dismiss,
  });
  const rows = await screen.findByRole('list', {
    name: "Posts that didn't go out",
  });
  const [withPicture, textOnly] = within(rows).getAllByRole('listitem');
  expect(withPicture).toHaveTextContent(/· @rowbot_ai/);
  expect(withPicture).toHaveTextContent(
    'X refused it (403): Missing required scope.',
  );
  expect(screen.queryByRole('list', { name: 'Scheduled posts' })).toBeNull();
  // Without the picture is offered only where there is one.
  expect(
    within(textOnly).queryByRole('button', {
      name: 'Post it now without the picture',
    }),
  ).toBeNull();
  await act(async () => {
    fireEvent.click(
      within(withPicture).getByRole('button', {
        name: 'Post it now without the picture',
      }),
    );
  });
  expect(retry).toHaveBeenCalledWith('xs-0123456789ab', true);
  await act(async () => {
    fireEvent.click(
      within(textOnly).getByRole('button', { name: 'Try again now' }),
    );
  });
  expect(retry).toHaveBeenCalledWith('xs-ba9876543210', false);
  expect(
    screen.queryByRole('list', { name: "Posts that didn't go out" }),
  ).toBeNull();
  expect(dismiss).not.toHaveBeenCalled();
});

const GUIDE = 'https://row-bot.ai/docs/operations/hosting#memory';
/** U8: what the server says after Row-Bot restarted for lack of memory on a 2 GB pod. */
function outOfMemory(host: boolean): AttentionProblem {
  return {
    id: 'memory:restarted',
    title: 'Row-Bot restarted because the server ran out of memory',
    detail: `Your server has 2 GB. Local speech-to-text together with the search model and browser needs about 3.2 GB. ${host ? 'Make the pod 4 GB in PikaPods' : 'Give the server 4 GB'}, or use cloud speech-to-text.`,
    place: 'memory',
    fix: null,
    links: [
      { label: 'How to resize', url: GUIDE },
      ...(host
        ? [
            {
              label: 'Manage your pod in PikaPods',
              url: 'https://www.pikapods.com/pods',
            },
          ]
        : []),
    ],
  };
}

it('says Row-Bot restarted for lack of memory, how to resize and how to dismiss it (U8)', async () => {
  const loadAttention = vi
    .fn()
    .mockResolvedValueOnce({
      schema_version: 1,
      update: null,
      problems: [outOfMemory(true)],
    })
    .mockResolvedValue({ schema_version: 1, update: null, problems: [] });
  show({ loadAttention });
  const needs = await screen.findByRole('list', { name: 'Needs you' });
  await within(needs).findByText(
    'Row-Bot restarted because the server ran out of memory',
  );
  expect(needs).toHaveTextContent(
    'Your server has 2 GB. Local speech-to-text together with the search model and browser needs about 3.2 GB. Make the pod 4 GB in PikaPods, or use cloud speech-to-text.',
  );
  // How to resize: the guide and the host's own page, each in a new tab.
  for (const [name, href] of [
    ['How to resize', GUIDE],
    ['Manage your pod in PikaPods', 'https://www.pikapods.com/pods'],
  ]) {
    const link = within(needs).getByRole('link', { name });
    expect(link).toHaveAttribute('href', href);
    expect(link).toHaveAttribute('target', '_blank');
  }
  expect(
    within(needs).getByRole('link', { name: 'Use cloud speech-to-text' }),
  ).toHaveAttribute('href', '/settings/voice#talk');
  fireEvent.click(
    within(needs).getByRole('button', {
      name: 'Open Row-Bot restarted because the server ran out of memory',
    }),
  );
  expect(handlers.onOpenTab).toHaveBeenCalledWith('monitor');

  fireEvent.click(within(needs).getByRole('button', { name: 'Dismiss' }));
  expect(runtime.dismissMemoryNotice).toHaveBeenCalledOnce();
  await vi.waitFor(() =>
    expect(
      screen.queryByText(
        'Row-Bot restarted because the server ran out of memory',
      ),
    ).toBeNull(),
  );
  expect(loadAttention).toHaveBeenCalledTimes(2);
  expect(summary()).toBe('All quiet.');
});

it('without a known host the memory notice links only the guide, and only to web pages (U8)', async () => {
  const notice = outOfMemory(false);
  show({
    loadAttention: attention({
      ...notice,
      links: [
        ...(notice.links ?? []),
        { label: 'Open a script', url: 'javascript:alert(1)' },
      ],
    }),
  });
  const needs = await screen.findByRole('list', { name: 'Needs you' });
  await within(needs).findByText(/Give the server 4 GB/);
  expect(
    within(needs).getByRole('link', { name: 'How to resize' }),
  ).toBeVisible();
  expect(
    within(needs).queryByRole('link', { name: /Manage your pod/ }),
  ).toBeNull();
  expect(
    within(needs).queryByRole('link', { name: 'Open a script' }),
  ).toBeNull();
  expect(summary()).toBe('1 thing needs you.');
});
