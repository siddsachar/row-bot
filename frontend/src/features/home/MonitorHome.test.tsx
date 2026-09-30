import type { ReactElement } from 'react';
import { MemoryRouter } from 'react-router-dom';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { clientError } from '../../api/errors';
import type {
  MonitorLogs,
  SystemDiagnosis,
  SystemDiagnosisCheck,
  TaskSummary,
  TaskSummaryPage,
} from '../../api/types';
import MonitorHome, {
  type DreamJournalEntry,
  type ExtractionJournalEntry,
  type MonitorHomeProps,
  type MonitorLogEntry,
  type MonitorSnapshot,
} from './MonitorHome';

const NOW = new Date('2026-09-20T12:00:00Z');
const NOW_SECONDS = NOW.getTime() / 1000;
const hoursAgo = (hours: number) =>
  new Date(NOW.getTime() - hours * 3_600_000).toISOString();

const recentLog: MonitorLogEntry = {
  timestamp: '2026-09-20T09:15:00Z',
  level: 'INFO',
  logger: 'row_bot.fixture',
  message: 'Monitor fixture ready',
  exception: '',
};

const extractionEntry: ExtractionJournalEntry = {
  timestamp: hoursAgo(3),
  summary: 'Saved useful knowledge',
  contradictions_blocked: 1,
  low_confidence_skipped: 2,
  islands_repaired: 2,
  threads: [{ label: 'Fixture thread', extracted: 8, saved: 7 }],
  errors: ['One bounded fixture error'],
};

const dreamEntry: DreamJournalEntry = {
  timestamp: hoursAgo(10),
  summary: 'Knowledge connected',
  merges: [
    {
      duplicate_subject: 'Row Bot duplicate',
      survivor_subject: 'Row Bot',
      score: 0.94,
    },
  ],
  enrichments: [
    {
      subject: 'Local assistant',
      old_length: 20,
      new_length: 45,
      new_description: 'A local-first assistant with bounded diagnostics.',
    },
  ],
  inferred_relations: [
    {
      source_subject: 'Row Bot',
      target_subject: 'Local assistant',
      relation_type: 'is_a',
      confidence: 0.91,
      evidence: 'Deterministic fixture evidence',
    },
  ],
  errors: ['One Dream fixture error'],
};

const snapshot: MonitorSnapshot = {
  extraction: {
    availability: 'available',
    last_run: hoursAgo(3),
    interval_hours: 6,
    threads_scanned: 4,
    entities_saved: 7,
    islands_repaired: 2,
  },
  extraction_journal: [extractionEntry],
  dream: {
    availability: 'available',
    enabled: true,
    window: '1:00 – 5:00',
    last_run: hoursAgo(10),
    last_summary: 'Knowledge connected',
    recent: [{ timestamp: hoursAgo(34), summary: 'Earlier cycle' }],
  },
  dream_journal: [dreamEntry],
  logs: {
    availability: 'available',
    authorized: true,
    entries: [recentLog],
    full_available: true,
  },
};

function check(
  name: string,
  status: SystemDiagnosisCheck['status'],
  detail: string,
  settings_tab: string,
  checked_at = NOW_SECONDS,
  kept: Partial<Pick<SystemDiagnosisCheck, 'network' | 'stale'>> = {},
): SystemDiagnosisCheck {
  return {
    id: name.toLowerCase().replace(/[^a-z0-9]+/g, '-'),
    name,
    status,
    detail,
    checked_at,
    settings_tab,
    network: false,
    stale: false,
    ...kept,
  };
}

function diagnosis(...checks: SystemDiagnosisCheck[]): SystemDiagnosis {
  return { schema_version: 1, hourly_network_checks: true, checks };
}

function task(overrides: Partial<TaskSummary> & { id: string }): TaskSummary {
  return {
    name: overrides.id,
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
    agent_profile_id: 'builtin:row_bot_default',
    approval_mode: 'block',
    ...overrides,
  };
}

function taskPage(items: TaskSummary[]): TaskSummaryPage {
  return {
    schema_version: 1,
    revision: 'r'.repeat(64),
    items,
    total: items.length,
    next_cursor: null,
  };
}

function logsResponse(entries: MonitorLogEntry[]): MonitorLogs {
  return {
    availability: 'available',
    authorized: true,
    entries,
    full_available: true,
  };
}

function withRouter(element: ReactElement) {
  return <MemoryRouter>{element}</MemoryRouter>;
}

function renderMonitor(overrides: Partial<MonitorHomeProps> = {}) {
  const props: MonitorHomeProps = {
    snapshot,
    loading: false,
    error: null,
    onRefresh: vi.fn(),
    onRunDiagnosis: vi.fn(async () => diagnosis()),
    now: NOW,
    ...overrides,
  };
  const view = render(withRouter(<MonitorHome {...props} />));
  return {
    ...view,
    props,
    rerenderWith: (next: Partial<MonitorHomeProps>) =>
      view.rerender(withRouter(<MonitorHome {...props} {...next} />)),
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

const healthTile = (name: string) =>
  within(screen.getByRole('list', { name: 'Health' })).getByRole('button', {
    name: new RegExp(`^${name}`),
  });

const maintenanceCaption = () =>
  screen.getByRole('heading', { name: 'Maintenance' }).nextElementSibling;

const metricTile = (label: string) =>
  within(screen.getByRole('group', { name: 'Maintenance metrics' }))
    .getByText(label)
    .closest('.metric-tile') as HTMLElement;

const logLines = () => screen.getByRole('list', { name: 'Log lines' });

const levelChip = (level: string) =>
  within(screen.getByRole('group', { name: 'Log levels' })).getByRole(
    'button',
    { name: new RegExp(`^${level}`) },
  );

beforeEach(() => {
  // Relative times ("3 hours ago") read the system clock.
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
});

it('shows the kept results after leaving the tab, without running anything', async () => {
  const kept = diagnosis(
    check('Ollama', 'warn', 'Server offline', 'Models', NOW_SECONDS - 240, {
      network: true,
    }),
    check('Cloud API', 'ok', 'Keys configured', 'Providers', NOW_SECONDS - 240),
    check('Disk', 'ok', '40.0 GB free', 'System', NOW_SECONDS - 240),
  );
  const loadHealth = vi.fn(async () => kept);
  const run = vi.fn(async () => diagnosis());
  const view = renderMonitor({ loadHealth, onRunDiagnosis: run });

  await waitFor(() =>
    expect(healthTile('System')).toHaveTextContent('1 of 1 OK'),
  );
  expect(healthTile('System')).toHaveTextContent('checked 4 minutes ago');
  expect(healthTile('Model runtime')).toHaveTextContent('Needs attention');
  expect(healthTile('Model runtime')).toHaveTextContent(
    'Ollama: Server offline',
  );
  expect(screen.getByText('Checked 4 minutes ago')).toBeVisible();

  // Another tab and back: the page mounts again and reads the kept results.
  view.unmount();
  renderMonitor({ loadHealth, onRunDiagnosis: run });
  await waitFor(() =>
    expect(healthTile('System')).toHaveTextContent('checked 4 minutes ago'),
  );
  expect(screen.queryByText(/Not checked/)).toBeNull();
  expect(loadHealth).toHaveBeenCalledTimes(2);
  expect(run).not.toHaveBeenCalled();

  fireEvent.click(healthTile('Model runtime'));
  const drawer = screen.getByRole('dialog', { name: 'Model runtime' });
  expect(drawer).toHaveAccessibleDescription(
    'Needs attention · Ollama: Server offline',
  );
  const checks = within(drawer).getByRole('list', {
    name: 'Model runtime checks',
  });
  const [ollama, cloud] = within(checks).getAllByRole('listitem');
  expect(within(checks).getAllByRole('listitem')).toHaveLength(2);
  expect(ollama).toHaveTextContent('Needs attention');
  expect(ollama).toHaveTextContent('Server offline');
  expect(ollama).toHaveTextContent('checked 4 minutes ago');
  expect(
    within(ollama).getByRole('link', { name: 'Open Models settings' }),
  ).toHaveAttribute('href', '/settings/models');
  expect(cloud).toHaveTextContent('OK');
  expect(
    within(cloud).getByRole('link', { name: 'Open Providers settings' }),
  ).toHaveAttribute('href', '/settings/providers');
  // Checks from other areas stay in their own tile.
  expect(checks).not.toHaveTextContent('Disk');
});

it('says "Checking…" until the first checks are kept, then shows them', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(NOW);
  const loadHealth = vi
    .fn<() => Promise<SystemDiagnosis>>()
    .mockResolvedValueOnce(diagnosis())
    .mockResolvedValue(
      diagnosis(check('Disk', 'ok', '40.0 GB free', 'System')),
    );
  renderMonitor({ loadHealth });
  await act(async () => {});

  expect(healthTile('System')).toHaveTextContent('Checking…');
  expect(healthTile('Knowledge')).toHaveTextContent('Checking…');
  expect(screen.queryByText(/Not checked/)).toBeNull();

  await act(async () => {
    await vi.advanceTimersByTimeAsync(5_000);
  });
  expect(healthTile('System')).toHaveTextContent('1 of 1 OK');
  expect(healthTile('System')).toHaveTextContent('checked just now');
});

it('marks a stale connection result and checks again on request', async () => {
  const run = vi.fn(async () =>
    diagnosis(
      check('GitHub', 'ok', 'Connected as fixture', 'Accounts', NOW_SECONDS, {
        network: true,
      }),
      check('Tunnel', 'inactive', 'Ready (no active tunnels)', 'Access'),
    ),
  );
  renderMonitor({
    onRunDiagnosis: run,
    loadHealth: vi.fn(async () =>
      diagnosis(
        check(
          'GitHub',
          'ok',
          'Connected as fixture',
          'Accounts',
          NOW_SECONDS - 26 * 3_600,
          { network: true, stale: true },
        ),
        check('Tunnel', 'inactive', 'Ready (no active tunnels)', 'Access'),
      ),
    ),
  });

  await waitFor(() =>
    expect(healthTile('Channels')).toHaveTextContent(
      'checked yesterday · Check again',
    ),
  );
  fireEvent.click(healthTile('Channels'));
  const drawer = screen.getByRole('dialog', { name: 'Channels' });
  const github = within(drawer).getByText('GitHub').closest('li')!;
  expect(github).toHaveTextContent('checked yesterday');
  fireEvent.click(within(github).getByRole('button', { name: 'Check again' }));
  expect(run).toHaveBeenCalledOnce();
  await waitFor(() =>
    expect(healthTile('Channels')).toHaveTextContent('checked just now'),
  );
  expect(healthTile('Channels')).not.toHaveTextContent('Check again');
  expect(
    within(drawer).queryByRole('button', { name: 'Check again' }),
  ).toBeNull();
});

it('runs diagnosis on click and presents the new results', async () => {
  const first = deferred<SystemDiagnosis>();
  const run = vi
    .fn<() => Promise<SystemDiagnosis>>()
    .mockReturnValueOnce(first.promise)
    .mockResolvedValue(
      diagnosis(check('Ollama', 'ok', 'Server ready', 'Models')),
    );
  renderMonitor({ onRunDiagnosis: run });

  expect(run).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Run diagnosis' }));
  expect(screen.getByText('Checking every service…')).toBeVisible();
  const busy = screen.getByRole('button', { name: 'Checking…' });
  expect(busy).toBeDisabled();
  fireEvent.click(busy);
  expect(run).toHaveBeenCalledTimes(1);

  await act(async () =>
    first.resolve(
      diagnosis(
        check('Ollama', 'warn', 'Server offline', 'Models'),
        check('Disk', 'ok', 'Ready', 'System', NOW_SECONDS - 10),
      ),
    ),
  );
  expect(screen.getByText('Checked just now')).toBeVisible();
  expect(healthTile('Model runtime')).toHaveTextContent('Needs attention');
  expect(healthTile('System')).toHaveTextContent('1 of 1 OK');

  fireEvent.click(healthTile('Model runtime'));
  const drawer = screen.getByRole('dialog', { name: 'Model runtime' });
  fireEvent.click(within(drawer).getByRole('button', { name: 'Run again' }));
  expect(run).toHaveBeenCalledTimes(2);
  expect(await within(drawer).findByText('Server ready')).toBeVisible();
  expect(healthTile('Model runtime')).toHaveTextContent('1 of 1 OK');
});

it('sends each check to the settings page that fixes it, by its name', async () => {
  renderMonitor({
    loadHealth: vi.fn(async () =>
      diagnosis(
        check('Tunnel', 'warn', 'Not running', 'Access'),
        check('Tools', 'ok', '12 / 14 enabled', 'Tools'),
      ),
    ),
  });
  await screen.findByText('Checked just now');

  fireEvent.click(healthTile('Channels'));
  const channels = screen.getByRole('dialog', { name: 'Channels' });
  expect(
    within(channels).getByRole('link', {
      name: 'Open Devices & remote access settings',
    }),
  ).toHaveAttribute('href', '/settings/access');
  fireEvent.click(
    within(channels).getByRole('button', { name: 'Close health detail' }),
  );

  fireEvent.click(healthTile('MCP and tools'));
  expect(
    within(screen.getByRole('dialog', { name: 'MCP and tools' })).getByRole(
      'link',
      { name: 'Open Tools settings' },
    ),
  ).toHaveAttribute('href', '/settings/tools');
});

it('shows a safe diagnosis failure, keeps the last results and allows a retry', async () => {
  const run = vi
    .fn()
    .mockRejectedValueOnce(new Error('secret stack detail'))
    .mockRejectedValueOnce(
      Object.assign(new Error('owner check traceback'), { status: 403 }),
    )
    .mockResolvedValueOnce(diagnosis(check('Disk', 'ok', 'Ready', 'System')));
  renderMonitor({
    onRunDiagnosis: run,
    loadHealth: vi.fn(async () =>
      diagnosis(
        check('Disk', 'warn', '3.0 GB free (90% used)', 'System', NOW_SECONDS),
      ),
    ),
  });
  await waitFor(() =>
    expect(healthTile('System')).toHaveTextContent('Needs attention'),
  );

  fireEvent.click(screen.getByRole('button', { name: 'Run diagnosis' }));
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('Diagnosis unavailable');
  // Only the mapped client message is shown, never the raw error text.
  expect(alert).toHaveTextContent(clientError(new Error()).message);
  expect(screen.queryByText(/secret stack detail/)).toBeNull();
  expect(healthTile('System')).toHaveTextContent('Disk: 3.0 GB free');

  const denied = clientError({ status: 403 }).message;
  expect(denied).not.toBe(clientError(new Error()).message);
  fireEvent.click(screen.getByRole('button', { name: 'Run diagnosis' }));
  await waitFor(() =>
    expect(screen.getByRole('alert')).toHaveTextContent(denied),
  );
  expect(screen.queryByText(/owner check traceback/)).toBeNull();

  fireEvent.click(screen.getByRole('button', { name: 'Run diagnosis' }));
  await waitFor(() =>
    expect(healthTile('System')).toHaveTextContent('1 of 1 OK'),
  );
  expect(run).toHaveBeenCalledTimes(3);
  expect(screen.queryByRole('alert')).toBeNull();
});

it('copies the diagnosis report and reports clipboard failure', async () => {
  const writeText = vi
    .fn()
    .mockRejectedValueOnce(new Error('blocked'))
    .mockResolvedValueOnce(undefined);
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText },
  });
  renderMonitor({
    loadHealth: vi.fn(async () =>
      diagnosis(check('Disk', 'ok', 'Ready', 'System', 1)),
    ),
  });
  fireEvent.click(
    await screen.findByRole('button', { name: 'Copy diagnosis report' }),
  );
  expect(await screen.findByText(/Could not copy the report/)).toBeVisible();
  fireEvent.click(
    screen.getByRole('button', { name: 'Copy diagnosis report' }),
  );
  expect(await screen.findByText('Diagnosis report copied.')).toBeVisible();
  expect(writeText).toHaveBeenCalledWith(
    expect.stringContaining('Disk: ok — Ready'),
  );
});

it('saves "Check connections every hour" and says what those checks contact', async () => {
  const loadHealth = vi.fn(async () =>
    diagnosis(check('Disk', 'ok', 'Ready', 'System')),
  );
  const save = vi.fn(async (enabled: boolean) => ({
    ...diagnosis(check('Disk', 'ok', 'Ready', 'System')),
    hourly_network_checks: enabled,
  }));
  renderMonitor({ loadHealth, onSetHourlyChecks: save });

  const hourly = await screen.findByRole('switch', {
    name: 'Check connections every hour',
  });
  expect(hourly).toBeChecked();
  expect(
    screen.getByRole('group', { name: 'Check connections every hour' }),
  ).toHaveAccessibleDescription(
    'These checks contact your providers, accounts and the internet; turn this off to run them only when you choose Run diagnosis.',
  );
  fireEvent.click(hourly);
  expect(save).toHaveBeenCalledWith(false);
  await waitFor(() => expect(hourly).not.toBeChecked());
});

it('keeps the switch as it was when saving it fails', async () => {
  renderMonitor({
    loadHealth: vi.fn(async () => diagnosis()),
    onSetHourlyChecks: vi.fn(async () => {
      throw new Error('offline');
    }),
  });
  const hourly = await screen.findByRole('switch', {
    name: 'Check connections every hour',
  });
  fireEvent.click(hourly);
  await waitFor(() => expect(hourly).toBeChecked());
});

it('announces loading and only reads what it shows on mount', async () => {
  const onRefresh = vi.fn();
  const onRunDiagnosis = vi.fn(async () => diagnosis());
  const loadLogs = vi.fn(async () => logsResponse([]));
  const loadTasks = vi.fn(async () => taskPage([]));
  const loadHealth = vi.fn(async () => diagnosis());
  renderMonitor({
    snapshot: null,
    loading: true,
    onRefresh,
    onRunDiagnosis,
    loadLogs,
    loadTasks,
    loadHealth,
  });
  await act(async () => {});

  expect(screen.getByText('Loading System Monitor…')).toHaveAttribute(
    'role',
    'status',
  );
  expect(screen.getByRole('button', { name: 'Refreshing…' })).toBeDisabled();
  expect(onRefresh).not.toHaveBeenCalled();
  expect(onRunDiagnosis).not.toHaveBeenCalled();
  expect(loadLogs).not.toHaveBeenCalled();
  // The mount reads are the kept check results and the workflow summary for
  // the Scheduler tile and the workflow activity lane.
  expect(loadHealth).toHaveBeenCalledOnce();
  expect(loadTasks).toHaveBeenCalledOnce();
  expect(loadTasks).toHaveBeenCalledWith();
});

it('keeps a refresh error actionable', () => {
  const onRefresh = vi.fn();
  renderMonitor({
    snapshot: null,
    error: 'Snapshot request failed',
    onRefresh,
  });

  expect(screen.getByRole('alert')).toHaveTextContent(
    'Snapshot request failed',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
  expect(onRefresh).toHaveBeenCalledOnce();
});

it('uses the explicit Refresh monitor control only when activated', async () => {
  const onRefresh = vi.fn();
  const loadHealth = vi.fn(async () => diagnosis());
  const { rerenderWith } = renderMonitor({ onRefresh, loadHealth });
  await act(async () => {});

  expect(onRefresh).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Refresh monitor' }));
  expect(onRefresh).toHaveBeenCalledOnce();
  await waitFor(() => expect(loadHealth).toHaveBeenCalledTimes(2));

  rerenderWith({ loading: true });
  const refreshing = screen.getByRole('button', { name: 'Refreshing…' });
  expect(refreshing).toBeDisabled();
  fireEvent.click(refreshing);
  expect(onRefresh).toHaveBeenCalledOnce();
});

it('adds failed workflows to the kept scheduler result', async () => {
  const loadTasks = vi.fn(async () =>
    taskPage([
      task({
        id: 'morning-brief',
        schedule: 'daily:09:00',
        next_run: new Date(NOW.getTime() + 2 * 3_600_000).toISOString(),
        last_status: 'completed',
      }),
      task({ id: 'weekly-report', last_status: 'failed' }),
    ]),
  );
  renderMonitor({
    loadTasks,
    loadHealth: vi.fn(async () =>
      diagnosis(check('Workflows', 'ok', '1 scheduled · 0 running', 'System')),
    ),
  });

  await waitFor(() =>
    expect(healthTile('Scheduler')).toHaveTextContent(
      '1 workflow failed last time · 1 of 1 OK',
    ),
  );
  expect(healthTile('Scheduler')).toHaveTextContent('Needs attention');
});

it('shows extraction never-run and Dream Cycle disabled states independently', () => {
  const { rerenderWith } = renderMonitor({
    snapshot: {
      ...snapshot,
      extraction: { ...snapshot.extraction, last_run: null },
      dream: {
        ...snapshot.dream,
        enabled: false,
        last_run: null,
        recent: [],
      },
    },
  });

  expect(maintenanceCaption()).toHaveTextContent(
    'Extraction has not run yet; it starts automatically. · Dream Cycle is off (Settings › Preferences).',
  );

  rerenderWith({
    snapshot: {
      ...snapshot,
      dream: { ...snapshot.dream, last_run: null, recent: [] },
    },
  });
  expect(maintenanceCaption()).toHaveTextContent(
    'Extraction every 6h · ran 3 hours ago · Dream Cycle 1:00 – 5:00 · not run yet',
  );
});

it('renders populated summaries and bounded journal details', () => {
  const { rerenderWith } = renderMonitor();

  expect(maintenanceCaption()).toHaveTextContent(
    'Extraction every 6h · ran 3 hours ago · Dream Cycle 1:00 – 5:00 · ran 10 hours ago',
  );
  expect(
    within(metricTile('Memories saved')).getByText('first run'),
  ).toBeVisible();

  // Dream Cycle history is shown first; each date toggles its details.
  const dreamTable = screen.getByRole('table', { name: 'Dream Cycle history' });
  const [, dreamRow] = within(dreamTable).getAllByRole('row');
  expect(dreamRow).toHaveTextContent('Knowledge connected');
  const dreamToggle = within(dreamRow).getByRole('button', {
    name: '10 hours ago',
  });
  expect(dreamToggle).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByRole('region', { name: 'Merges' })).toBeNull();
  fireEvent.click(dreamToggle);
  expect(dreamToggle).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByRole('region', { name: 'Merges' })).toHaveTextContent(
    'Row Bot duplicate → Row Bot · 94% similar',
  );
  expect(screen.getByRole('region', { name: 'Enrichments' })).toHaveTextContent(
    'Local assistant · 20 → 45 characters',
  );
  expect(
    screen.getByRole('region', { name: 'Inferred relations' }),
  ).toHaveTextContent('Row Bot — is a → Local assistant · 91% confident');
  expect(
    screen.getByRole('region', { name: 'Dream Cycle errors' }),
  ).toHaveTextContent('One Dream fixture error');
  // Long enrichment text and evidence stay out of the bounded summary.
  expect(dreamTable).not.toHaveTextContent('bounded diagnostics');
  expect(dreamTable).not.toHaveTextContent('Deterministic fixture evidence');
  fireEvent.click(dreamToggle);
  expect(screen.queryByRole('region', { name: 'Merges' })).toBeNull();

  fireEvent.click(screen.getByRole('radio', { name: 'Extraction' }));
  expect(
    screen.queryByRole('table', { name: 'Dream Cycle history' }),
  ).toBeNull();
  const extractionTable = screen.getByRole('table', {
    name: 'Extraction history',
  });
  const [, extractionRow] = within(extractionTable).getAllByRole('row');
  expect(
    within(extractionRow)
      .getAllByRole('cell')
      .map((cell) => cell.textContent),
  ).toEqual(['3 hours ago', '1', '7', '1', '2', '1']);
  fireEvent.click(within(extractionRow).getByRole('button'));
  expect(extractionTable).toHaveTextContent('Saved useful knowledge');
  expect(extractionTable).toHaveTextContent(
    'Fixture thread: extracted 8, saved 7',
  );
  expect(
    within(extractionTable).getByRole('list', { name: 'Extraction errors' }),
  ).toHaveTextContent('One bounded fixture error');

  // Without a journal, the metrics fall back to the latest extraction totals.
  rerenderWith({
    snapshot: { ...snapshot, extraction_journal: [], dream_journal: [] },
  });
  expect(metricTile('Memories saved')).toHaveTextContent(
    'Memories saved7extraction',
  );
  expect(metricTile('Conversations read')).toHaveTextContent(
    'Conversations read4extraction',
  );
  expect(screen.queryByText('Duplicates merged')).toBeNull();
  expect(
    screen.getByText('Knowledge extraction has not recorded a run yet.'),
  ).toBeVisible();
  fireEvent.click(screen.getByRole('radio', { name: 'Dream Cycle' }));
  expect(
    screen.getByText('Dream Cycle has not recorded a run yet.'),
  ).toBeVisible();
});

it('bounds history to the 20 newest runs, newest first', () => {
  const cycles = Array.from({ length: 25 }, (_, index) => ({
    ...dreamEntry,
    timestamp: hoursAgo(50 - index),
    summary: `Cycle ${String(index + 1).padStart(2, '0')}`,
  }));
  renderMonitor({ snapshot: { ...snapshot, dream_journal: cycles } });

  const table = screen.getByRole('table', { name: 'Dream Cycle history' });
  const [, ...rows] = within(table).getAllByRole('row');
  expect(rows).toHaveLength(20);
  expect(rows[0]).toHaveTextContent('Cycle 25');
  expect(rows[19]).toHaveTextContent('Cycle 06');
  expect(table).not.toHaveTextContent('Cycle 05');
});

it('dates history rows and maintenance runs from the supplied now', () => {
  // The system clock stays at NOW; the page is told it is an hour later.
  const hoursLater = (hours: number) =>
    new Date(NOW.getTime() + hours * 3_600_000);
  const { rerenderWith } = renderMonitor({ now: hoursLater(1) });
  expect(maintenanceCaption()).toHaveTextContent(
    'Extraction every 6h · ran 4 hours ago · Dream Cycle 1:00 – 5:00 · ran 11 hours ago',
  );
  const dreamWhen = () =>
    within(
      screen.getByRole('table', { name: 'Dream Cycle history' }),
    ).getAllByRole('row')[1];
  expect(
    within(dreamWhen()).getByRole('button', { name: '11 hours ago' }),
  ).toBeVisible();

  rerenderWith({ now: hoursLater(2) });
  expect(maintenanceCaption()).toHaveTextContent(
    'Extraction every 6h · ran 5 hours ago · Dream Cycle 1:00 – 5:00 · ran 12 hours ago',
  );
  expect(
    within(dreamWhen()).getByRole('button', { name: '12 hours ago' }),
  ).toBeVisible();
  fireEvent.click(screen.getByRole('radio', { name: 'Extraction' }));
  const [, extractionRow] = within(
    screen.getByRole('table', { name: 'Extraction history' }),
  ).getAllByRole('row');
  expect(
    within(extractionRow).getByRole('button', { name: '5 hours ago' }),
  ).toBeVisible();
});

it('shows run-over-run changes and trends for maintenance metrics', () => {
  const extraction = [5, 5, 7].map((saved, index) => ({
    ...extractionEntry,
    timestamp: hoursAgo(30 - index * 6),
    contradictions_blocked: index === 2 ? 1 : 0,
    threads: [{ label: `Thread ${index}`, extracted: saved, saved }],
    errors: [],
  }));
  const dreams = [2, 1].map((merged, index) => ({
    ...dreamEntry,
    timestamp: hoursAgo(34 - index * 24),
    merges: Array.from({ length: merged }, () => dreamEntry.merges[0]),
  }));
  renderMonitor({
    snapshot: {
      ...snapshot,
      extraction_journal: extraction,
      dream_journal: dreams,
    },
  });

  const saved = metricTile('Memories saved');
  expect(saved).toHaveTextContent('7extraction');
  expect(within(saved).getByText('+2 vs last run')).toBeVisible();
  expect(
    within(saved).getByRole('img', {
      name: 'Memories saved: last 3 values from 5 to 7, highest 7',
    }),
  ).toBeVisible();
  expect(
    within(metricTile('Conversations read')).getByText('same as last run'),
  ).toBeVisible();
  expect(
    within(metricTile('Contradictions blocked')).getByText('+1 vs last run'),
  ).toBeVisible();
  const merged = metricTile('Duplicates merged');
  expect(merged).toHaveTextContent('1Dream Cycle');
  expect(within(merged).getByText('−1 vs last run')).toBeVisible();
  expect(
    within(merged).getByRole('img', {
      name: 'Duplicates merged: last 2 values from 2 to 1, highest 2',
    }),
  ).toBeVisible();
});

it('counts recent activity per lane for the selected range', async () => {
  const loadTasks = vi.fn(async () =>
    taskPage([
      task({
        id: 'morning-brief',
        name: 'Morning brief',
        recent_runs: [
          { status: 'completed', started_at: hoursAgo(1) },
          { status: 'failed', started_at: hoursAgo(48) },
        ],
      }),
    ]),
  );
  renderMonitor({
    loadTasks,
    snapshot: {
      ...snapshot,
      extraction_journal: [
        { ...extractionEntry, timestamp: hoursAgo(72), errors: [] },
        { ...extractionEntry, timestamp: hoursAgo(3) },
      ],
      logs: {
        ...snapshot.logs,
        entries: [
          recentLog,
          {
            ...recentLog,
            timestamp: hoursAgo(0.5),
            level: 'ERROR',
            logger: 'row_bot.channels.telegram',
            message: 'Delivery failed',
          },
        ],
      },
    },
  });

  expect(
    await screen.findByRole('img', {
      name: 'Workflow runs: 1 in the last 24 hours',
    }),
  ).toBeVisible();
  expect(
    screen.getByRole('img', {
      name: 'Extraction: 1 in the last 24 hours, 1 failed',
    }),
  ).toBeVisible();
  expect(
    screen.getByRole('img', {
      name: 'Dream Cycle: 1 in the last 24 hours, 1 failed',
    }),
  ).toBeVisible();
  expect(
    screen.getByRole('img', {
      name: 'Channel events: 1 in the last 24 hours, 1 failed',
    }),
  ).toBeVisible();

  const range = screen.getByRole('radiogroup', { name: 'Activity range' });
  expect(within(range).getByRole('radio', { name: '24 hours' })).toBeChecked();
  fireEvent.click(within(range).getByRole('radio', { name: '7 days' }));
  expect(within(range).getByRole('radio', { name: '7 days' })).toBeChecked();
  expect(
    screen.getByRole('img', {
      name: 'Extraction: 2 in the last 7 days, 1 failed',
    }),
  ).toBeVisible();
  expect(
    screen.getByRole('img', {
      name: 'Workflow runs: 2 in the last 7 days, 1 failed',
    }),
  ).toBeVisible();
});

it('preserves independent partial-unavailable and corrupt section states', () => {
  const loadLogs = vi.fn(async () => logsResponse([recentLog]));
  const { rerenderWith } = renderMonitor({
    loadLogs,
    snapshot: {
      ...snapshot,
      extraction: { ...snapshot.extraction, availability: 'corrupt' },
      dream: { ...snapshot.dream, availability: 'missing' },
      logs: { ...snapshot.logs, availability: 'corrupt' },
    },
  });

  expect(maintenanceCaption()).toHaveTextContent(
    'Knowledge extraction status could not be read safely. · Dream Cycle has not produced status yet.',
  );
  const alert = screen.getByRole('alert');
  expect(alert).toHaveTextContent('Logs unavailable');
  expect(alert).toHaveTextContent('The local log could not be read safely.');
  expect(screen.queryByRole('list', { name: 'Log lines' })).toBeNull();
  expect(screen.queryByRole('searchbox', { name: 'Search logs' })).toBeNull();
  expect(screen.queryByText('Monitor fixture ready')).toBeNull();
  // The other sections keep rendering their own data.
  expect(
    screen.getByRole('table', { name: 'Dream Cycle history' }),
  ).toBeVisible();
  expect(
    screen.getByRole('group', { name: 'Maintenance metrics' }),
  ).toBeVisible();

  rerenderWith({
    snapshot: {
      ...snapshot,
      extraction: { ...snapshot.extraction, availability: 'unavailable' },
      logs: { ...snapshot.logs, availability: 'missing', entries: [] },
    },
  });
  expect(maintenanceCaption()).toHaveTextContent(
    'Knowledge extraction status is unavailable. · Dream Cycle 1:00 – 5:00 · ran 10 hours ago',
  );
  expect(screen.getByText('No local log is available yet.')).toBeVisible();
  expect(screen.queryByRole('alert')).toBeNull();
  expect(loadLogs).not.toHaveBeenCalled();
});

const consoleEntries: MonitorLogEntry[] = [
  recentLog,
  {
    timestamp: '2026-09-20T09:16:00Z',
    level: 'DEBUG',
    logger: 'row_bot.cache',
    message: 'Cache warm',
    exception: '',
  },
  ...['09:17:00', '09:17:10', '09:17:20'].map((time) => ({
    timestamp: `2026-09-20T${time}Z`,
    level: 'INFO',
    logger: 'row_bot.heartbeat',
    message: 'Heartbeat',
    exception: '',
  })),
  {
    timestamp: '2026-09-20T09:18:00Z',
    level: 'WARN',
    logger: 'row_bot.providers',
    message: 'Slow provider response',
    exception: '',
  },
  {
    timestamp: '2026-09-20T09:19:00Z',
    level: 'ERROR',
    logger: 'row_bot.providers',
    message: 'Provider request failed',
    exception: 'Traceback: redacted detail',
  },
  {
    timestamp: '2026-09-20T09:20:00Z',
    level: 'CRITICAL',
    logger: 'row_bot.scheduler',
    message: 'Scheduler stopped',
    exception: '',
  },
];

function renderConsole(overrides: Partial<MonitorHomeProps> = {}) {
  return renderMonitor({
    snapshot: {
      ...snapshot,
      logs: { ...snapshot.logs, entries: consoleEntries },
    },
    ...overrides,
  });
}

const lineTexts = () =>
  within(logLines())
    .getAllByRole('listitem')
    .map(
      (line) =>
        line.querySelector('.log-line-message')?.textContent ??
        line.textContent,
    );

it('filters authorized local logs by level and search, collapsing repeated lines', () => {
  renderConsole();

  expect(levelChip('All')).toHaveTextContent('All8');
  expect(levelChip('All')).toHaveAttribute('aria-pressed', 'true');
  expect(levelChip('Debug')).toHaveTextContent('Debug1');
  expect(levelChip('Info')).toHaveTextContent('Info4');
  expect(levelChip('Warning')).toHaveTextContent('Warning1');
  expect(levelChip('Error')).toHaveTextContent('Error2');

  // Repeated lines collapse by default into one counted line.
  expect(lineTexts()).toEqual([
    'Monitor fixture ready',
    'Cache warm',
    'Heartbeat',
    'Slow provider response',
    'Provider request failed',
    'Scheduler stopped',
  ]);
  const heartbeat = within(logLines()).getAllByRole('listitem')[2];
  expect(heartbeat).toHaveTextContent('×3');
  expect(within(heartbeat).getByTitle('3 identical lines')).toBeVisible();
  expect(logLines()).toHaveTextContent('row_bot.fixture');

  // Exceptions stay behind a Traceback disclosure.
  const failure = within(logLines()).getAllByRole('listitem')[4];
  expect(
    within(failure).getByText('Traceback: redacted detail'),
  ).not.toBeVisible();
  fireEvent.click(within(failure).getByText('Traceback'));
  expect(within(failure).getByText('Traceback: redacted detail')).toBeVisible();

  fireEvent.click(levelChip('Error'));
  expect(levelChip('Error')).toHaveAttribute('aria-pressed', 'true');
  expect(levelChip('All')).toHaveAttribute('aria-pressed', 'false');
  expect(lineTexts()).toEqual(['Provider request failed', 'Scheduler stopped']);

  fireEvent.click(levelChip('All'));
  const search = screen.getByRole('searchbox', { name: 'Search logs' });
  fireEvent.change(search, { target: { value: 'PROVIDER' } });
  expect(lineTexts()).toEqual([
    'Slow provider response',
    'Provider request failed',
  ]);
  fireEvent.change(search, { target: { value: 'row_bot.cache' } });
  expect(lineTexts()).toEqual(['Cache warm']);
  fireEvent.change(search, { target: { value: 'redacted detail' } });
  expect(lineTexts()).toEqual(['Provider request failed']);
  fireEvent.change(search, { target: { value: 'no such line' } });
  expect(screen.getByText('No lines match.')).toBeVisible();
  expect(screen.queryByRole('list', { name: 'Log lines' })).toBeNull();
  expect(
    screen.getByRole('button', { name: 'Copy visible log lines' }),
  ).toBeDisabled();
  fireEvent.change(search, { target: { value: '' } });

  const collapse = screen.getByRole('switch', {
    name: 'Collapse repeated lines',
  });
  expect(collapse).toBeChecked();
  fireEvent.click(collapse);
  expect(collapse).not.toBeChecked();
  expect(lineTexts()).toHaveLength(8);
  expect(logLines()).not.toHaveTextContent('×3');
});

it('copies the visible log lines and reports clipboard failure', async () => {
  const writeClipboard = vi
    .fn()
    .mockResolvedValueOnce({ status: 'unavailable', reason: 'blocked' })
    .mockResolvedValue({ status: 'ok', value: null });
  renderConsole({ writeClipboard });

  fireEvent.click(levelChip('Error'));
  fireEvent.click(
    screen.getByRole('button', { name: 'Copy visible log lines' }),
  );
  expect(await screen.findByText('Clipboard unavailable.')).toBeInTheDocument();
  fireEvent.click(
    screen.getByRole('button', { name: 'Copy visible log lines' }),
  );
  expect(await screen.findByText('Log lines copied.')).toBeInTheDocument();
  expect(writeClipboard).toHaveBeenLastCalledWith(
    [
      '2026-09-20T09:19:00Z [ERROR] [row_bot.providers] Provider request failed',
      'Traceback: redacted detail',
      '2026-09-20T09:20:00Z [ERROR] [row_bot.scheduler] Scheduler stopped',
    ].join('\n'),
  );

  fireEvent.click(levelChip('Info'));
  fireEvent.click(
    screen.getByRole('button', { name: 'Copy visible log lines' }),
  );
  await act(async () => {});
  expect(writeClipboard).toHaveBeenLastCalledWith(
    [
      '2026-09-20T09:15:00Z [INFO] [row_bot.fixture] Monitor fixture ready',
      '2026-09-20T09:17:20Z [INFO] [row_bot.heartbeat] Heartbeat (×3)',
    ].join('\n'),
  );
});

it('reads the full redacted log for the local owner and follows new lines every 6 seconds', async () => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  const full = {
    ...recentLog,
    timestamp: '2026-09-20T09:30:00Z',
    logger: 'row_bot.full',
    message: 'Complete redacted message',
    exception: 'Redacted exception detail',
  };
  const newer = {
    ...recentLog,
    timestamp: '2026-09-20T09:31:00Z',
    message: 'Newest followed line',
  };
  const loadLogs = vi
    .fn<(signal?: AbortSignal) => Promise<MonitorLogs>>()
    .mockResolvedValueOnce(logsResponse([recentLog, full]))
    .mockResolvedValue(logsResponse([recentLog, full, newer]));
  const { unmount } = renderMonitor({ loadLogs });
  await act(async () => {});

  expect(loadLogs).toHaveBeenCalledOnce();
  const firstSignal = loadLogs.mock.calls[0][0];
  expect(firstSignal).toBeInstanceOf(AbortSignal);
  expect(logLines()).toHaveTextContent('row_bot.full');
  expect(logLines()).toHaveTextContent('Complete redacted message');
  expect(logLines()).toHaveTextContent('Redacted exception detail');
  expect(logLines()).not.toHaveTextContent('Newest followed line');

  await act(async () => {
    await vi.advanceTimersByTimeAsync(5_999);
  });
  expect(loadLogs).toHaveBeenCalledOnce();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(loadLogs).toHaveBeenCalledTimes(2);
  expect(logLines()).toHaveTextContent('Newest followed line');

  // A hidden window does not poll.
  const visibility = vi
    .spyOn(document, 'visibilityState', 'get')
    .mockReturnValue('hidden');
  await act(async () => {
    await vi.advanceTimersByTimeAsync(12_000);
  });
  expect(loadLogs).toHaveBeenCalledTimes(2);
  visibility.mockRestore();

  // Turning Follow off stops polling.
  const follow = screen.getByRole('switch', { name: 'Follow new lines' });
  expect(follow).toBeChecked();
  fireEvent.click(follow);
  expect(follow).not.toBeChecked();
  expect(firstSignal?.aborted).toBe(true);
  await act(async () => {});
  const calls = loadLogs.mock.calls.length;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60_000);
  });
  expect(loadLogs).toHaveBeenCalledTimes(calls);

  const lastSignal = loadLogs.mock.lastCall?.[0];
  unmount();
  expect(lastSignal?.aborted).toBe(true);
});

it('keeps the recent lines when the full log cannot be read', async () => {
  const loadLogs = vi.fn(async () => {
    throw new Error('Local owner required');
  });
  renderMonitor({ loadLogs });

  expect(
    await screen.findByText(
      'The full log could not be read. Showing recent lines.',
    ),
  ).toBeVisible();
  expect(logLines()).toHaveTextContent('Monitor fixture ready');
  expect(screen.queryByText(/Local owner required/)).toBeNull();
});

const refreshedLog: MonitorLogEntry = {
  ...recentLog,
  timestamp: '2026-09-20T09:40:00Z',
  message: 'Refreshed recent line',
};

function withRecentLines(entries: MonitorLogEntry[]): MonitorSnapshot {
  return { ...snapshot, logs: { ...snapshot.logs, entries } };
}

it('keeps the full log it read when a monitor refresh brings recent lines', async () => {
  const full = {
    ...recentLog,
    timestamp: '2026-09-20T09:30:00Z',
    message: 'Complete redacted message',
  };
  const loadLogs = vi.fn(async () => logsResponse([recentLog, full]));
  const { rerenderWith } = renderMonitor({ loadLogs });
  await act(async () => {});
  expect(lineTexts()).toEqual([
    'Monitor fixture ready',
    'Complete redacted message',
  ]);

  // A refresh brings a new snapshot holding only the few newest lines.
  rerenderWith({ snapshot: withRecentLines([refreshedLog]) });
  await act(async () => {});
  expect(lineTexts()).toEqual([
    'Monitor fixture ready',
    'Complete redacted message',
  ]);
  expect(levelChip('All')).toHaveTextContent('All2');
  expect(loadLogs).toHaveBeenCalledOnce();
});

it('shows refreshed recent lines until the full log has been read', async () => {
  const loadLogs = vi.fn(async () => {
    throw new Error('Local owner required');
  });
  const { rerenderWith } = renderMonitor({ loadLogs });
  expect(
    await screen.findByText(
      'The full log could not be read. Showing recent lines.',
    ),
  ).toBeVisible();
  expect(lineTexts()).toEqual(['Monitor fixture ready']);

  rerenderWith({ snapshot: withRecentLines([recentLog, refreshedLog]) });
  expect(lineTexts()).toEqual([
    'Monitor fixture ready',
    'Refreshed recent line',
  ]);
  expect(loadLogs).toHaveBeenCalledOnce();
});

it('explains remote log authority without exposing entries or log controls', async () => {
  const loadLogs = vi.fn(async () => logsResponse([recentLog]));
  renderMonitor({
    loadLogs,
    snapshot: {
      ...snapshot,
      logs: {
        availability: 'unavailable',
        authorized: false,
        entries: [recentLog],
        full_available: false,
      },
    },
  });
  await act(async () => {});

  expect(screen.getByText(/available only to the local owner/)).toBeVisible();
  expect(
    screen.getByText(/No log contents or private filesystem paths/),
  ).toBeVisible();
  expect(screen.queryByText('Monitor fixture ready')).toBeNull();
  expect(screen.queryByRole('list', { name: 'Log lines' })).toBeNull();
  expect(screen.queryByRole('group', { name: 'Log levels' })).toBeNull();
  expect(screen.queryByRole('searchbox', { name: 'Search logs' })).toBeNull();
  expect(
    screen.queryByRole('button', { name: 'Copy visible log lines' }),
  ).toBeNull();
  expect(loadLogs).not.toHaveBeenCalled();
});

it('shows recent lines without reading the full log when it is not offered', async () => {
  const loadLogs = vi.fn(async () => logsResponse([]));
  const { rerenderWith } = renderMonitor({
    loadLogs,
    snapshot: {
      ...snapshot,
      logs: { ...snapshot.logs, full_available: false },
    },
  });
  await act(async () => {});
  expect(logLines()).toHaveTextContent('Monitor fixture ready');

  rerenderWith({
    snapshot: {
      ...snapshot,
      logs: {
        ...snapshot.logs,
        authorized: false,
        availability: 'available',
      },
    },
  });
  await act(async () => {});
  expect(screen.getByText(/available only to the local owner/)).toBeVisible();
  expect(screen.queryByText('Monitor fixture ready')).toBeNull();
  expect(loadLogs).not.toHaveBeenCalled();
});

it('lists a red check it found in the background without a link away', () => {
  renderMonitor({
    attention: [
      {
        id: 'health:disk',
        title: 'Disk needs attention',
        detail: '1.2 GB free (97% used)',
        place: 'health',
      },
      {
        id: 'channel:telegram',
        title: 'Telegram stopped',
        detail: 'It is set to start with Row-Bot but isn’t running.',
        place: 'channels',
      },
    ],
  });
  const list = screen
    .getByRole('heading', { name: 'Needs attention' })
    .closest('section')!;
  const [disk, telegram] = within(list).getAllByRole('listitem');
  expect(disk).toHaveTextContent('Disk needs attention 1.2 GB free (97% used)');
  expect(within(disk).queryByRole('link')).toBeNull();
  expect(
    within(telegram).getByRole('link', { name: 'Open Channels' }),
  ).toHaveAttribute('href', '/settings/channels');
});
