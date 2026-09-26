import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { MemoryRouter, useLocation } from 'react-router-dom';
import Home from './Home';

const onboarding = (extra: Record<string, unknown> = {}) => ({
  schema_version: 1,
  revision: 'c'.repeat(64),
  setup_complete: true,
  starter_workflows_missing: 0,
  profile: [],
  completed_steps: ['models'],
  skipped_steps: [],
  dismissed_home_card: true,
  steps: [{ id: 'models', title: 'Models', description: 'Connect a model.' }],
  intents: [],
  ...extra,
});

const emptyGraph = {
  schema_version: 1,
  availability: 'available',
  revision: 'a'.repeat(64),
  nodes: [],
  edges: [],
  total_entities: 0,
  total_relations: 0,
  shown_entities: 0,
  shown_relations: 0,
  truncated: false,
  center_id: null,
  entity_types: [],
  sources: [],
};

const monitorSnapshot = {
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
};

const taskPage = (items: unknown[] = []) => ({
  schema_version: 1,
  revision: 't'.repeat(64),
  items,
  total: items.length,
  next_cursor: null,
});

const mock = vi.hoisted(() => ({
  overlayOpen: vi.fn(),
  state: {
    status: 'ready',
    handshake: { instance_id: 'server-a', client_session_id: 'session-a' } as {
      instance_id: string;
      client_session_id: string;
    } | null,
    conversations: [] as {
      id: string;
      title: string;
      revision?: string;
      pinned?: boolean;
    }[],
  },
  controller: {
    selectConversation: vi.fn(),
    onboardingCommand: vi.fn(),
    onboarding: vi.fn(),
    knowledgeGraph: vi.fn(),
    monitorSnapshot: vi.fn(),
    savedTasks: vi.fn(),
    knowledgeEntityDetail: vi.fn(),
    reviewDreamRun: vi.fn(),
    executeDreamRun: vi.fn(),
    monitorLogs: vi.fn(),
    systemDiagnosis: vi.fn(),
  },
}));

vi.mock('../../runtime', () => ({
  useClientState: () => mock.state,
  useRuntime: () => ({
    controller: mock.controller,
    platform: { writeClipboard: vi.fn() },
  }),
}));

vi.mock('../../ui/overlays', async (load) => {
  const actual = await load<typeof import('../../ui/overlays')>();
  return { ...actual, useOverlay: () => ({ open: mock.overlayOpen }) };
});

vi.mock('../tasks/TaskLibrary', () => ({
  default: () => (
    <section aria-label="Workflow library">Workflow owner</section>
  ),
}));

function Location() {
  const location = useLocation();
  return (
    <output aria-label="Current location">
      {location.pathname}
      {location.search}
    </output>
  );
}

function show(path = '/') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Home />
      <Location />
    </MemoryRouter>,
  );
}

function chooseTab(name: string) {
  fireEvent.mouseDown(screen.getByRole('tab', { name }), {
    button: 0,
    ctrlKey: false,
  });
}

function location() {
  return screen.getByLabelText('Current location').textContent;
}

beforeEach(() => {
  mock.overlayOpen.mockReset();
  for (const fn of Object.values(mock.controller)) fn.mockReset();
  mock.controller.onboarding.mockResolvedValue(onboarding());
  mock.controller.knowledgeGraph.mockResolvedValue(emptyGraph);
  mock.controller.monitorSnapshot.mockResolvedValue(monitorSnapshot);
  mock.controller.savedTasks.mockResolvedValue(taskPage());
  mock.controller.monitorLogs.mockResolvedValue(monitorSnapshot.logs);
  mock.state.status = 'ready';
  mock.state.handshake = {
    instance_id: 'server-a',
    client_session_id: 'session-a',
  };
  mock.state.conversations = [];
});

it('opens on Overview with the five Home capability tabs and no pane-backed resources', async () => {
  show();
  expect(
    screen.getByRole('tablist', { name: 'Home capabilities' }),
  ).toBeVisible();
  expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
    'Overview',
    'Workflows',
    'Knowledge',
    'Monitor',
    'Insights',
  ]);
  expect(screen.getByRole('tab', { name: 'Overview' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  expect(
    await screen.findByRole('heading', { level: 2, name: /^Good / }),
  ).toBeVisible();
  expect(screen.queryByRole('region', { name: 'Workflow library' })).toBeNull();
  expect(screen.queryByRole('tab', { name: 'Designer' })).toBeNull();
  expect(screen.queryByRole('tab', { name: 'Developer' })).toBeNull();
  expect(
    screen.queryByRole('button', { name: /Search all conversations/ }),
  ).toBeNull();
  expect(location()).toBe('/');
  await waitFor(() =>
    expect(mock.controller.savedTasks).toHaveBeenCalledTimes(1),
  );
});

it('keeps the old welcome, examples, and connection chrome out of Overview while listing recent threads', async () => {
  mock.state.conversations = [
    { id: 'chat-a', title: 'Design review', revision: 'r', pinned: false },
  ];
  show();
  expect(screen.queryByRole('region', { name: 'Start working' })).toBeNull();
  expect(
    screen.queryByRole('region', { name: 'Recent conversations' }),
  ).toBeNull();
  expect(
    screen.queryByRole('region', { name: 'Start with an example' }),
  ).toBeNull();
  expect(screen.queryByText('Connected · local workspace')).toBeNull();
  const recent = screen.getByRole('list', { name: 'Recent threads' });
  fireEvent.click(
    within(recent).getByRole('button', { name: /Design review/ }),
  );
  expect(mock.controller.selectConversation).toHaveBeenCalledWith('chat-a');
  expect(location()).toBe('/conversations/chat-a');
  await waitFor(() =>
    expect(mock.controller.savedTasks).toHaveBeenCalledTimes(1),
  );
});

it('uses the explicit one-shot workflow deep-link intent without persistent last-tab state', () => {
  const { unmount } = show('/?tab=workflows');
  expect(screen.getByRole('tab', { name: 'Workflows' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  expect(
    screen.getByRole('region', { name: 'Workflow library' }),
  ).toBeVisible();
  // The Workflows tab owns its own reads.
  expect(mock.controller.savedTasks).not.toHaveBeenCalled();
  unmount();
  show('/?tab=WORKFLOWS');
  expect(screen.getByRole('tab', { name: 'Workflows' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
});

it('falls back to Overview for an unknown tab', () => {
  show('/?tab=designer');
  expect(screen.getByRole('tab', { name: 'Overview' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
});

it('opens a workflow from Overview with a one-shot workflow intent', async () => {
  mock.controller.savedTasks.mockResolvedValue(
    taskPage([
      {
        id: 'task-7',
        name: 'Morning digest',
        description: '',
        icon: '',
        enabled: true,
        notify_only: false,
        step_count: 1,
        schedule: 'daily:08:00',
        at: null,
        last_run: null,
        last_status: null,
        conversation_id: null,
        next_run: '2099-01-01T08:00:00',
      },
    ]),
  );
  show();
  fireEvent.click(
    await screen.findByRole('button', {
      name: 'Open scheduled workflow: Morning digest',
    }),
  );
  expect(location()).toBe('/?tab=workflows&workflow=task-7');
  expect(screen.getByRole('tab', { name: 'Workflows' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  chooseTab('Overview');
  expect(location()).toBe('/?tab=overview');
  fireEvent.click(await screen.findByRole('button', { name: 'Workflows' }));
  expect(location()).toBe('/?tab=workflows');
  chooseTab('Overview');
  fireEvent.click(await screen.findByRole('button', { name: 'Monitor' }));
  expect(location()).toBe('/?tab=monitor');
  expect(
    await screen.findByRole('region', { name: 'System Monitor' }),
  ).toBeVisible();
});

it('checks Dream Cycle from one click and opens the irreversible-change confirmation', async () => {
  mock.controller.reviewDreamRun.mockResolvedValueOnce({
    review_id: 'dream-review',
    snapshot_revision: 'b'.repeat(64),
    action_digest: 'd'.repeat(64),
  });
  show();
  chooseTab('Knowledge');
  fireEvent.click(
    await screen.findByRole('button', { name: 'Run Dream Cycle' }),
  );
  expect(mock.controller.reviewDreamRun).toHaveBeenCalledWith({
    snapshot_revision: 'b'.repeat(64),
  });
  await waitFor(() =>
    expect(mock.overlayOpen).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'Run Dream Cycle now?',
        confirmLabel: 'Run Dream Cycle',
      }),
    ),
  );
  expect(screen.getByRole('button', { name: 'Run Dream Cycle' })).toBeEnabled();
  expect(mock.controller.executeDreamRun).not.toHaveBeenCalled();
});

it('runs the reviewed Dream Cycle only on confirmation and then re-reads knowledge and monitor', async () => {
  mock.controller.reviewDreamRun.mockResolvedValueOnce({
    review_id: 'dream-review',
    snapshot_revision: 'b'.repeat(64),
    action_digest: 'd'.repeat(64),
  });
  mock.controller.executeDreamRun.mockResolvedValueOnce({
    status: 'completed',
    summary: 'Merged 2 duplicates.',
  });
  show('/?tab=knowledge');
  fireEvent.click(
    await screen.findByRole('button', { name: 'Run Dream Cycle' }),
  );
  await waitFor(() => expect(mock.overlayOpen).toHaveBeenCalledTimes(1));
  expect(mock.controller.knowledgeGraph).toHaveBeenCalledTimes(1);
  expect(mock.controller.monitorSnapshot).toHaveBeenCalledTimes(1);
  const { onConfirm } = mock.overlayOpen.mock.calls[0][0] as {
    onConfirm: () => void;
  };
  await act(async () => onConfirm());
  expect(mock.controller.executeDreamRun).toHaveBeenCalledTimes(1);
  expect(mock.controller.executeDreamRun).toHaveBeenCalledWith(
    expect.objectContaining({
      client_session_id: 'session-a',
      type: 'dream.run',
      payload: {
        snapshot_revision: 'b'.repeat(64),
        action_digest: 'd'.repeat(64),
        review_id: 'dream-review',
      },
    }),
  );
  await waitFor(() =>
    expect(mock.controller.knowledgeGraph).toHaveBeenCalledTimes(2),
  );
  await waitFor(() =>
    expect(mock.controller.monitorSnapshot).toHaveBeenCalledTimes(2),
  );
  expect(mock.controller.executeDreamRun).toHaveBeenCalledTimes(1);
  expect(mock.controller.reviewDreamRun).toHaveBeenCalledTimes(1);
});

// Suspected product bug: Home.tsx's monitor refresh (triggered by the Dream
// completion itself) resets any non-running Dream state to idle, erasing the
// "completed with errors" alert and the "Retry Dream" label as soon as the
// refreshed snapshot arrives. Flip to `it` once the outcome survives.
it('keeps a Dream Cycle outcome visible after the refresh it triggers', async () => {
  mock.controller.reviewDreamRun.mockResolvedValueOnce({
    review_id: 'dream-review',
    snapshot_revision: 'b'.repeat(64),
    action_digest: 'd'.repeat(64),
  });
  mock.controller.executeDreamRun.mockResolvedValueOnce({
    status: 'failed',
    summary: '',
  });
  show('/?tab=knowledge');
  fireEvent.click(
    await screen.findByRole('button', { name: 'Run Dream Cycle' }),
  );
  await waitFor(() => expect(mock.overlayOpen).toHaveBeenCalledTimes(1));
  const { onConfirm } = mock.overlayOpen.mock.calls[0][0] as {
    onConfirm: () => void;
  };
  await act(async () => onConfirm());
  await waitFor(() =>
    expect(mock.controller.monitorSnapshot).toHaveBeenCalledTimes(2),
  );
  await act(async () => {});
  expect(screen.getByRole('alert')).toHaveTextContent(
    'Dream Cycle completed with errors.',
  );
  expect(screen.getByRole('button', { name: 'Retry Dream' })).toBeEnabled();
});

it('keeps Knowledge and Monitor as passive, truthful boundaries', async () => {
  show();
  chooseTab('Knowledge');
  expect(screen.getByRole('region', { name: 'Knowledge' })).toBeVisible();
  chooseTab('Monitor');
  expect(screen.getByRole('region', { name: 'System Monitor' })).toBeVisible();
  await waitFor(() =>
    expect(mock.controller.monitorSnapshot).toHaveBeenCalled(),
  );
  expect(mock.controller.reviewDreamRun).not.toHaveBeenCalled();
  expect(mock.controller.executeDreamRun).not.toHaveBeenCalled();
  expect(mock.controller.systemDiagnosis).not.toHaveBeenCalled();
  expect(mock.controller.onboardingCommand).not.toHaveBeenCalled();
});

it('reuses monitor, knowledge, and workflow reads across tab switches for 20 seconds', async () => {
  let clock = 1_000_000;
  vi.spyOn(Date, 'now').mockImplementation(() => clock);
  show();
  await waitFor(() =>
    expect(mock.controller.monitorSnapshot).toHaveBeenCalledTimes(1),
  );
  await waitFor(() =>
    expect(mock.controller.savedTasks).toHaveBeenCalledTimes(1),
  );
  chooseTab('Knowledge');
  await waitFor(() =>
    expect(mock.controller.knowledgeGraph).toHaveBeenCalledTimes(1),
  );
  expect(mock.controller.knowledgeGraph).toHaveBeenCalledWith(
    250,
    expect.any(AbortSignal),
  );
  await screen.findByRole('button', { name: 'Run Dream Cycle' });
  chooseTab('Monitor');
  await screen.findByRole('region', { name: 'System Monitor' });
  chooseTab('Overview');
  chooseTab('Knowledge');
  await screen.findByRole('button', { name: 'Run Dream Cycle' });
  expect(mock.controller.monitorSnapshot).toHaveBeenCalledTimes(1);
  expect(mock.controller.knowledgeGraph).toHaveBeenCalledTimes(1);
  expect(mock.controller.savedTasks).toHaveBeenCalledTimes(1);

  clock += 20_001;
  chooseTab('Overview');
  await waitFor(() =>
    expect(mock.controller.monitorSnapshot).toHaveBeenCalledTimes(2),
  );
  await waitFor(() =>
    expect(mock.controller.savedTasks).toHaveBeenCalledTimes(2),
  );
  chooseTab('Knowledge');
  await waitFor(() =>
    expect(mock.controller.knowledgeGraph).toHaveBeenCalledTimes(2),
  );
});

it('does not cache a failed workflow read', async () => {
  mock.controller.savedTasks
    .mockRejectedValueOnce(new Error('offline'))
    .mockResolvedValue(taskPage());
  show();
  expect(
    await screen.findByText(/^Workflows could not be read:/),
  ).toBeVisible();
  chooseTab('Monitor');
  await waitFor(() =>
    expect(mock.controller.savedTasks).toHaveBeenCalledTimes(2),
  );
});

it('reads a bounded knowledge graph and raises the limit only when Show all is chosen', async () => {
  const node = (id: string) => ({
    id,
    revision: `r-${id}`,
    subject: `Memory ${id}`,
    description: '',
    entity_type: 'fact',
    source: 'manual',
    updated_at: '2026-09-25T10:00:00',
    relation_count: 0,
    orphan: true,
    is_user: false,
  });
  mock.controller.knowledgeGraph
    .mockResolvedValueOnce({
      ...emptyGraph,
      nodes: [node('a'), node('b')],
      total_entities: 3,
      shown_entities: 2,
      truncated: true,
      entity_types: ['fact'],
      sources: ['manual'],
    })
    .mockResolvedValueOnce({
      ...emptyGraph,
      revision: 'e'.repeat(64),
      nodes: [node('a'), node('b'), node('c')],
      total_entities: 3,
      shown_entities: 3,
      truncated: false,
      entity_types: ['fact'],
      sources: ['manual'],
    });
  show('/?tab=knowledge');
  const stats = await screen.findByLabelText('Knowledge statistics');
  expect(mock.controller.knowledgeGraph).toHaveBeenCalledTimes(1);
  expect(mock.controller.knowledgeGraph).toHaveBeenLastCalledWith(
    250,
    expect.any(AbortSignal),
  );
  fireEvent.click(
    within(stats).getByRole('button', { name: 'Show all memories' }),
  );
  await waitFor(() =>
    expect(mock.controller.knowledgeGraph).toHaveBeenCalledTimes(2),
  );
  expect(mock.controller.knowledgeGraph).toHaveBeenLastCalledWith(
    1000,
    expect.any(AbortSignal),
  );
  await waitFor(() =>
    expect(screen.getByLabelText('Knowledge statistics')).not.toHaveTextContent(
      'showing',
    ),
  );
  expect(
    screen.queryByRole('button', { name: 'Show all memories' }),
  ).toBeNull();
});

// Suspected product bug: Home.tsx passes loadLogs as a new inline function on
// every render and MonitorHome's log console re-reads whenever it changes, so
// any Home re-render (for example a conversation update) aborts the current
// log read, sends another monitorLogs request and restarts the follow timer.
it('does not re-read the full log when Home re-renders', async () => {
  const logs = {
    availability: 'available',
    authorized: true,
    entries: [],
    full_available: true,
  };
  mock.controller.monitorSnapshot.mockResolvedValue({
    ...monitorSnapshot,
    logs,
  });
  mock.controller.monitorLogs.mockResolvedValue(logs);
  const tree = () => (
    <MemoryRouter initialEntries={['/?tab=monitor']}>
      <Home />
    </MemoryRouter>
  );
  const { rerender } = render(tree());
  await waitFor(() =>
    expect(mock.controller.monitorLogs).toHaveBeenCalledTimes(1),
  );
  rerender(tree());
  rerender(tree());
  await act(async () => {});
  expect(mock.controller.monitorLogs).toHaveBeenCalledTimes(1);
});

it('reports reconnecting state without exposing client identity', () => {
  mock.state.status = 'reconnecting';
  mock.state.handshake = null;
  show();
  expect(screen.getByText('Connecting to your workspace…')).toHaveAttribute(
    'role',
    'status',
  );
  expect(mock.controller.onboarding).not.toHaveBeenCalled();
  expect(mock.controller.monitorSnapshot).not.toHaveBeenCalled();
  expect(document.body.textContent).not.toContain('server-a');
  expect(document.body.textContent).not.toContain('session-a');
});

// Suspected product bug: Home.tsx's shared loadTasks is not gated on the
// connection identity like the onboarding, monitor and knowledge reads, so
// Overview calls savedTasks while still connecting and can show a
// "Workflows could not be read" error under the connecting status.
it('does not read workflows before the workspace is connected', () => {
  mock.state.status = 'reconnecting';
  mock.state.handshake = null;
  show();
  expect(mock.controller.savedTasks).not.toHaveBeenCalled();
});

it('asks to connect when the workspace is disconnected', () => {
  mock.state.status = 'disconnected';
  mock.state.handshake = null;
  show();
  expect(screen.getByText('Connect to open your workflows.')).toHaveAttribute(
    'role',
    'status',
  );
});

it('shows a first-run setup route in Overview without changing setup on mount', async () => {
  mock.controller.onboarding.mockResolvedValueOnce(
    onboarding({
      setup_complete: false,
      completed_steps: [],
      dismissed_home_card: false,
      steps: [],
    }),
  );
  show();
  const region = await screen.findByRole('region', { name: 'Continue setup' });
  expect(region).toBeVisible();
  expect(
    within(region).getByRole('link', { name: 'Open Setup Center' }),
  ).toHaveAttribute('href', '/setup');
  expect(
    within(region).queryByRole('button', { name: 'Hide setup reminder' }),
  ).toBeNull();
  expect(mock.controller.onboarding).toHaveBeenCalled();
  expect(mock.controller.onboardingCommand).not.toHaveBeenCalled();
  // The reminder lives in Overview, not above the tabs.
  chooseTab('Workflows');
  expect(screen.queryByRole('region', { name: 'Continue setup' })).toBeNull();
});

const optionalSetup = onboarding({
  dismissed_home_card: false,
  steps: [
    { id: 'models', title: 'Models', description: 'Connect a model.' },
    { id: 'voice', title: 'Voice', description: 'Configure voice.' },
  ],
});

it('hides a saved optional setup reminder on one click', async () => {
  mock.controller.onboarding.mockResolvedValueOnce(optionalSetup);
  mock.controller.onboardingCommand.mockResolvedValueOnce({
    schema_version: 1,
    status: 'completed',
    snapshot: { ...optionalSetup, dismissed_home_card: true },
  });
  show();
  const region = await screen.findByRole('region', { name: 'Continue setup' });
  expect(
    within(region).getByRole('link', { name: 'Continue setup' }),
  ).toHaveAttribute('href', '/setup');
  fireEvent.click(
    within(region).getByRole('button', { name: 'Hide setup reminder' }),
  );
  expect(mock.controller.onboardingCommand).toHaveBeenCalledTimes(1);
  expect(mock.controller.onboardingCommand).toHaveBeenCalledWith(
    expect.objectContaining({
      action: 'dismiss_home',
      expected_revision: optionalSetup.revision,
    }),
  );
  await waitFor(() =>
    expect(screen.queryByRole('region', { name: 'Continue setup' })).toBeNull(),
  );
});

it('keeps the reminder and explains a failed hide', async () => {
  mock.controller.onboarding.mockResolvedValueOnce(optionalSetup);
  mock.controller.onboardingCommand.mockRejectedValueOnce(
    new Error('C:\\Users\\private\\settings.json'),
  );
  show();
  fireEvent.click(
    await screen.findByRole('button', { name: 'Hide setup reminder' }),
  );
  const region = screen.getByRole('region', { name: 'Continue setup' });
  expect(await within(region).findByRole('status')).toHaveTextContent(
    'Could not hide the setup reminder. Refresh this page and try again.',
  );
  expect(document.body.textContent).not.toContain('private');
});
