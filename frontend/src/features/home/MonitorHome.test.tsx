import { useState } from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import MonitorHome, {
  type MonitorHomeProps,
  type MonitorLogEntry,
  type MonitorSnapshot,
} from './MonitorHome';

const recentLog: MonitorLogEntry = {
  timestamp: '2026-09-20T09:15:00Z',
  level: 'INFO',
  logger: 'row_bot.fixture',
  message: 'Monitor fixture ready',
  exception: '',
};

const snapshot: MonitorSnapshot = {
  extraction: {
    availability: 'available',
    last_run: '2026-09-20T08:30:00Z',
    interval_hours: 6,
    threads_scanned: 4,
    entities_saved: 7,
    islands_repaired: 2,
  },
  extraction_journal: [
    {
      timestamp: '2026-09-20T08:30:00Z',
      summary: 'Saved useful knowledge',
      contradictions_blocked: 1,
      low_confidence_skipped: 2,
      islands_repaired: 2,
      threads: [{ label: 'Fixture thread', extracted: 8, saved: 7 }],
      errors: ['One bounded fixture error'],
    },
  ],
  dream: {
    availability: 'available',
    enabled: true,
    window: '1:00 – 5:00',
    last_run: '2026-09-20T02:00:00Z',
    last_summary: 'Knowledge connected',
    recent: [
      {
        timestamp: '2026-09-19T02:00:00Z',
        summary: 'Earlier cycle',
      },
    ],
  },
  dream_journal: [
    {
      timestamp: '2026-09-20T02:00:00Z',
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
    },
  ],
  logs: {
    availability: 'available',
    authorized: true,
    entries: [recentLog],
    full_available: true,
  },
};

function renderMonitor(overrides: Partial<MonitorHomeProps> = {}) {
  const props: MonitorHomeProps = {
    snapshot,
    loading: false,
    error: null,
    onRefresh: vi.fn(),
    onRunDiagnosis: vi.fn(async () => ({
      schema_version: 1 as const,
      checks: [],
    })),
    onLoadFullLogs: vi.fn(),
    fullLogsOpen: false,
    fullLogsLoading: false,
    fullLogsError: null,
    fullLogEntries: [],
    onCloseFullLogs: vi.fn(),
    ...overrides,
  };
  return { ...render(<MonitorHome {...props} />), props };
}

it('runs diagnosis only on click and presents bounded results with a retry', async () => {
  const run = vi.fn(async () => ({
    schema_version: 1 as const,
    checks: [
      {
        name: 'Ollama',
        status: 'warn' as const,
        detail: 'Server offline',
        checked_at: 1,
        settings_tab: 'Models',
      },
    ],
  }));
  renderMonitor({ onRunDiagnosis: run });
  expect(run).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Run diagnosis' }));
  expect(await screen.findByText('Checked 1 services.')).toBeVisible();
  expect(screen.getByText('Ollama · warn')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Run again' }));
  expect(run).toHaveBeenCalledTimes(2);
});

it('shows diagnosis failure and allows an explicit retry', async () => {
  const run = vi
    .fn()
    .mockRejectedValueOnce(new Error('Local owner required'))
    .mockResolvedValueOnce({ schema_version: 1, checks: [] });
  renderMonitor({ onRunDiagnosis: run });
  fireEvent.click(screen.getByRole('button', { name: 'Run diagnosis' }));
  expect(await screen.findByText('Local owner required')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Run diagnosis' }));
  expect(await screen.findByText('Checked 0 services.')).toBeVisible();
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
    onRunDiagnosis: vi.fn(async () => ({
      schema_version: 1 as const,
      checks: [
        {
          name: 'Disk',
          status: 'ok' as const,
          detail: 'Ready',
          checked_at: 1,
          settings_tab: 'System',
        },
      ],
    })),
  });
  fireEvent.click(screen.getByRole('button', { name: 'Run diagnosis' }));
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

it('announces loading without reading or mutating monitor state on mount', () => {
  const onRefresh = vi.fn();
  const onLoadFullLogs = vi.fn();
  renderMonitor({
    snapshot: null,
    loading: true,
    onRefresh,
    onLoadFullLogs,
  });

  expect(screen.getByRole('status')).toHaveTextContent(
    'Loading System Monitor',
  );
  expect(screen.getByRole('button', { name: 'Refreshing…' })).toBeDisabled();
  expect(onRefresh).not.toHaveBeenCalled();
  expect(onLoadFullLogs).not.toHaveBeenCalled();
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

it('shows extraction never-run and Dream Cycle disabled states independently', () => {
  renderMonitor({
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

  expect(screen.getByText('Not yet run — starts automatically.')).toBeVisible();
  expect(screen.getByText(/Disabled — enable Dream Cycle/)).toBeVisible();
  expect(
    within(screen.getByRole('region', { name: 'Dream Cycle' })).queryByRole(
      'button',
      { name: 'View Journal' },
    ),
  ).toBeNull();
});

it('renders populated summaries and bounded journal details', async () => {
  const user = userEvent.setup();
  renderMonitor();

  expect(screen.getByLabelText('Extraction summary')).toHaveTextContent(
    '4 threads scanned',
  );
  expect(screen.getByLabelText('Extraction summary')).toHaveTextContent(
    '7 entities saved',
  );
  expect(screen.getByText(/Knowledge connected/)).toBeVisible();
  expect(screen.getByText(/Earlier cycle/)).toBeVisible();

  await user.click(
    within(
      screen.getByRole('region', { name: 'Knowledge Extraction' }),
    ).getByRole('button', { name: 'View Journal' }),
  );
  expect(screen.getByRole('dialog')).toHaveAccessibleName('Extraction Journal');
  await user.click(screen.getByText(/Saved useful knowledge/));
  expect(
    screen.getByText(/Fixture thread: extracted 8, saved 7/),
  ).toBeVisible();
  expect(screen.getByRole('alert')).toHaveTextContent(
    'One bounded fixture error',
  );
  await user.click(screen.getByRole('button', { name: 'Close' }));

  await user.click(
    within(screen.getByRole('region', { name: 'Dream Cycle' })).getByRole(
      'button',
      { name: 'View Journal' },
    ),
  );
  expect(screen.getByRole('dialog')).toHaveAccessibleName(
    'Dream Cycle Journal',
  );
  await user.click(
    within(screen.getByRole('dialog')).getByText(/Knowledge connected/),
  );
  expect(screen.getByRole('region', { name: 'Merges' })).toHaveTextContent(
    'Row Bot duplicate → Row Bot',
  );
  expect(
    screen.getByRole('region', { name: 'Inferred relations' }),
  ).toHaveTextContent('confidence 0.91');
});

it('preserves independent partial-unavailable and corrupt section states', () => {
  renderMonitor({
    snapshot: {
      ...snapshot,
      extraction: { ...snapshot.extraction, availability: 'corrupt' },
      dream: { ...snapshot.dream, availability: 'missing' },
      logs: { ...snapshot.logs, availability: 'corrupt' },
    },
  });

  expect(
    screen.getByText('Knowledge extraction status could not be read safely.'),
  ).toBeVisible();
  expect(
    screen.getByText('Dream Cycle has not produced status yet.'),
  ).toBeVisible();
  expect(
    screen.getByText('The local log could not be read safely.'),
  ).toBeVisible();
});

function FullLogHarness() {
  const [open, setOpen] = useState(false);
  return (
    <MonitorHome
      snapshot={snapshot}
      loading={false}
      onRefresh={vi.fn()}
      onRunDiagnosis={vi.fn(async () => ({
        schema_version: 1 as const,
        checks: [],
      }))}
      onLoadFullLogs={() => setOpen(true)}
      fullLogsOpen={open}
      fullLogEntries={[
        {
          ...recentLog,
          logger: 'row_bot.full',
          message: 'Complete redacted message',
          exception: 'Redacted exception detail',
        },
      ]}
      onCloseFullLogs={() => setOpen(false)}
    />
  );
}

it('shows authorized local logs and opens the bounded full-log dialog', async () => {
  const user = userEvent.setup();
  render(<FullLogHarness />);

  expect(screen.getByLabelText('Recent logs')).toHaveTextContent(
    'Monitor fixture ready',
  );
  expect(screen.getByLabelText('Recent logs')).not.toHaveTextContent(
    'row_bot.fixture',
  );
  await user.click(screen.getByRole('button', { name: 'View Full Log' }));
  expect(screen.getByRole('dialog')).toHaveAccessibleName('Log Viewer');
  expect(screen.getByLabelText('Full log entries')).toHaveTextContent(
    '[row_bot.full] Complete redacted message',
  );
  expect(screen.getByLabelText('Full log entries')).toHaveTextContent(
    'Redacted exception detail',
  );
  await user.click(screen.getByRole('button', { name: 'Close' }));
  expect(screen.queryByRole('dialog')).toBeNull();
});

it('explains remote log authority without exposing entries or full-log controls', () => {
  renderMonitor({
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

  expect(screen.getByText(/available only to the local owner/)).toBeVisible();
  expect(
    screen.getByText(/No log contents or private filesystem paths/),
  ).toBeVisible();
  expect(screen.queryByText('Monitor fixture ready')).toBeNull();
  expect(screen.queryByRole('button', { name: 'View Full Log' })).toBeNull();
});

it('uses explicit Refresh and Refresh logs controls only when activated', () => {
  const onRefresh = vi.fn();
  renderMonitor({ onRefresh });

  expect(onRefresh).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
  fireEvent.click(screen.getByRole('button', { name: 'Refresh logs' }));
  expect(onRefresh).toHaveBeenCalledTimes(2);
});
