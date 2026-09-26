import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Brain,
  CalendarClock,
  ChevronRight,
  ClipboardCopy,
  Cpu,
  HardDrive,
  Plug,
  Radio,
  RefreshCw,
  Search,
  Stethoscope,
  type LucideIcon,
} from 'lucide-react';
import type {
  MonitorLogs,
  SystemDiagnosis,
  SystemDiagnosisCheck,
  TaskSummaryPage,
} from '../../api/types';
import type { ClientPlatform } from '../../platform';
import { writeClipboardText } from '../../platform/clipboard';
import { Drawer } from '../../ui/overlays';
import {
  Button,
  ErrorState,
  IconButton,
  InlineEmpty,
  Segmented,
  StatusDot,
  Toggle,
  type Tone,
} from '../../ui/primitives';
import { clientError } from '../../api/errors';
import { absoluteTime, parseTimestamp, relativeTime } from '../../ui/format';
import { FAILED_RUN_STATUSES, When, plural, runStatus } from './home-format';
import { Sparkline, Swimlane, type Lane } from './monitor-charts';

export type MonitorAvailability =
  'available' | 'missing' | 'unavailable' | 'corrupt';

export type ExtractionJournalEntry = {
  timestamp: string;
  summary: string;
  contradictions_blocked: number;
  low_confidence_skipped: number;
  islands_repaired: number;
  threads: Array<{ label: string; extracted: number; saved: number }>;
  errors: string[];
};

export type DreamJournalEntry = {
  timestamp: string;
  summary: string;
  merges: Array<{
    duplicate_subject: string;
    survivor_subject: string;
    score: number | null;
  }>;
  enrichments: Array<{
    subject: string;
    old_length: number;
    new_length: number;
    new_description: string;
  }>;
  inferred_relations: Array<{
    source_subject: string;
    target_subject: string;
    relation_type: string;
    confidence: number | null;
    evidence: string;
  }>;
  errors: string[];
};

export type MonitorLogEntry = {
  timestamp: string;
  level: string;
  logger: string;
  message: string;
  exception: string;
};

export type MonitorSnapshot = {
  extraction: {
    availability: MonitorAvailability;
    last_run: string | null;
    interval_hours: number;
    threads_scanned: number;
    entities_saved: number;
    islands_repaired: number;
  };
  extraction_journal: ExtractionJournalEntry[];
  dream: {
    availability: MonitorAvailability;
    enabled: boolean;
    window: string;
    last_run: string | null;
    last_summary: string | null;
    recent: Array<{ timestamp: string; summary: string }>;
  };
  dream_journal: DreamJournalEntry[];
  logs: {
    availability: MonitorAvailability;
    authorized: boolean;
    entries: MonitorLogEntry[];
    full_available: boolean;
  };
};

export type MonitorHomeProps = {
  snapshot: MonitorSnapshot | null;
  loading: boolean;
  error?: string | null;
  onRefresh: () => void;
  onRunDiagnosis: () => Promise<SystemDiagnosis>;
  /** Up to 200 redacted entries for the console (local owner only). */
  loadLogs?: (signal?: AbortSignal) => Promise<MonitorLogs>;
  loadTasks?: () => Promise<TaskSummaryPage>;
  writeClipboard?: ClientPlatform['writeClipboard'];
  now?: Date;
};

const MAX_HISTORY = 20;

/** Journals arrive newest first; charts and deltas read them oldest first. */
export function chronological<T extends { timestamp: string }>(
  entries: readonly T[] | undefined,
): T[] {
  return [...(entries ?? [])].sort(
    (left, right) =>
      (parseTimestamp(left.timestamp)?.getTime() ?? 0) -
      (parseTimestamp(right.timestamp)?.getTime() ?? 0),
  );
}

function availabilityCopy(subject: string, availability: MonitorAvailability) {
  if (availability === 'missing')
    return `${subject} has not produced status yet.`;
  if (availability === 'corrupt')
    return `${subject} status could not be read safely.`;
  return `${subject} status is unavailable.`;
}

// Health tiles --------------------------------------------------------------

type TileKey =
  'models' | 'channels' | 'mcp' | 'scheduler' | 'knowledge' | 'storage';
const TILES: {
  key: TileKey;
  label: string;
  icon: LucideIcon;
  match: (check: SystemDiagnosisCheck) => boolean;
}[] = [
  {
    key: 'models',
    label: 'Model runtime',
    icon: Cpu,
    match: (check) =>
      ['Ollama', 'Model', 'Cloud API', 'TTS'].includes(check.name) ||
      ['Models', 'Providers', 'Voice'].includes(check.settings_tab),
  },
  {
    key: 'channels',
    label: 'Channels',
    icon: Radio,
    match: (check) =>
      check.name === 'Tunnel' ||
      ['Channels', 'Accounts'].includes(check.settings_tab),
  },
  {
    key: 'mcp',
    label: 'MCP and tools',
    icon: Plug,
    match: (check) =>
      ['MCP', 'Plugins', 'Skills', 'Tools', 'Search'].includes(check.name) ||
      ['MCP', 'Plugins', 'Skills', 'Utilities', 'Search'].includes(
        check.settings_tab,
      ),
  },
  {
    key: 'scheduler',
    label: 'Scheduler',
    icon: CalendarClock,
    match: (check) =>
      ['Workflows', 'Dream Cycle', 'Tracker'].includes(check.name) ||
      check.settings_tab === 'Tracker',
  },
  {
    key: 'knowledge',
    label: 'Knowledge',
    icon: Brain,
    match: (check) =>
      ['Knowledge', 'FAISS Index', 'Wiki Vault', 'Documents'].includes(
        check.name,
      ) || ['Knowledge', 'Documents'].includes(check.settings_tab),
  },
  {
    key: 'storage',
    label: 'System',
    icon: HardDrive,
    match: () => true,
  },
];

const SETTINGS_ROUTES: Record<string, string> = {
  Models: 'models',
  Providers: 'providers',
  Voice: 'voice',
  Channels: 'channels',
  Accounts: 'accounts',
  MCP: 'mcp',
  Plugins: 'plugins',
  Skills: 'skills',
  Utilities: 'tools',
  Search: 'tools',
  Knowledge: 'knowledge',
  Documents: 'documents',
  Tracker: 'tracker',
  Buddy: 'buddy',
  Preferences: 'preferences',
  System: 'system',
};

const CHECK_STATUS: Record<
  SystemDiagnosisCheck['status'],
  { tone: Tone; label: string; rank: number }
> = {
  error: { tone: 'danger', label: 'Error', rank: 3 },
  warn: { tone: 'warning', label: 'Needs attention', rank: 2 },
  ok: { tone: 'success', label: 'OK', rank: 1 },
  inactive: { tone: 'neutral', label: 'Not in use', rank: 0 },
};

type TileView = {
  key: TileKey;
  label: string;
  icon: LucideIcon;
  tone: Tone;
  status: string;
  detail: string;
  checks: SystemDiagnosisCheck[];
};

function groupChecks(checks: readonly SystemDiagnosisCheck[]) {
  const groups = new Map<TileKey, SystemDiagnosisCheck[]>(
    TILES.map((tile) => [tile.key, []]),
  );
  for (const check of checks) {
    const tile = TILES.find((item) => item.match(check))!;
    groups.get(tile.key)!.push(check);
  }
  return groups;
}

function tileFromChecks(
  tile: (typeof TILES)[number],
  checks: SystemDiagnosisCheck[],
): TileView {
  if (!checks.length)
    return {
      ...tile,
      tone: 'neutral',
      status: 'Nothing to check',
      detail: 'No services in this area',
      checks,
    };
  const worst = [...checks].sort(
    (left, right) =>
      CHECK_STATUS[right.status].rank - CHECK_STATUS[left.status].rank,
  )[0];
  const view = CHECK_STATUS[worst.status];
  const ok = checks.filter((check) => check.status === 'ok').length;
  return {
    ...tile,
    tone: view.tone,
    status: view.label,
    detail:
      worst.status === 'error' || worst.status === 'warn'
        ? `${worst.name}: ${worst.detail}`
        : worst.status === 'inactive' && !ok
          ? 'Not set up'
          : `${ok} of ${checks.length} OK`,
    checks,
  };
}

// Log console ---------------------------------------------------------------

const LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR'] as const;
function levelOf(entry: MonitorLogEntry) {
  const level = entry.level.toUpperCase();
  if (level === 'CRITICAL') return 'ERROR';
  if (level === 'WARN') return 'WARNING';
  return (LEVELS as readonly string[]).includes(level) ? level : 'INFO';
}

function clock(timestamp: string) {
  const date = parseTimestamp(timestamp);
  if (!date) return timestamp.slice(11, 19) || timestamp;
  return new Intl.DateTimeFormat(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date);
}

function LogConsole({
  logs,
  loadLogs,
  writeClipboard,
}: {
  logs: MonitorSnapshot['logs'];
  loadLogs?: (signal?: AbortSignal) => Promise<MonitorLogs>;
  writeClipboard?: ClientPlatform['writeClipboard'];
}) {
  const [entries, setEntries] = useState<MonitorLogEntry[]>(logs.entries);
  const [level, setLevel] = useState<'ALL' | (typeof LEVELS)[number]>('ALL');
  const [query, setQuery] = useState('');
  const [follow, setFollow] = useState(true);
  const [collapse, setCollapse] = useState(true);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const body = useRef<HTMLOListElement>(null);
  const canLoad =
    Boolean(loadLogs) &&
    logs.authorized &&
    logs.availability === 'available' &&
    logs.full_available;

  // Once the full log is read, a snapshot refresh (a few recent lines) must
  // not replace it; the follow poll keeps it current.
  const fullLog = useRef(false);
  useEffect(() => {
    if (!fullLog.current) setEntries(logs.entries);
  }, [logs.entries]);
  useEffect(() => {
    if (!canLoad || !loadLogs) return;
    const abort = new AbortController();
    let timer = 0;
    const read = () => {
      loadLogs(abort.signal).then(
        (value) => {
          if (abort.signal.aborted) return;
          fullLog.current = true;
          setEntries(value.entries);
          setError('');
        },
        () => {
          if (!abort.signal.aborted)
            setError('The full log could not be read. Showing recent lines.');
        },
      );
    };
    read();
    // Follow the tail while the console is on screen.
    if (follow)
      timer = window.setInterval(() => {
        if (document.visibilityState === 'visible') read();
      }, 6000);
    return () => {
      abort.abort();
      window.clearInterval(timer);
    };
  }, [canLoad, follow, loadLogs]);

  const counts = useMemo(() => {
    const value: Record<string, number> = {};
    for (const entry of entries)
      value[levelOf(entry)] = (value[levelOf(entry)] ?? 0) + 1;
    return value;
  }, [entries]);
  const shown = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    const rows: { entry: MonitorLogEntry; repeats: number }[] = [];
    for (const entry of entries) {
      if (level !== 'ALL' && levelOf(entry) !== level) continue;
      if (
        needle &&
        !`${entry.logger} ${entry.message} ${entry.exception}`
          .toLocaleLowerCase()
          .includes(needle)
      )
        continue;
      const last = rows[rows.length - 1];
      if (
        collapse &&
        last &&
        last.entry.message === entry.message &&
        levelOf(last.entry) === levelOf(entry)
      ) {
        last.repeats += 1;
        last.entry = entry;
      } else rows.push({ entry, repeats: 1 });
    }
    return rows;
  }, [collapse, entries, level, query]);

  useEffect(() => {
    if (follow && body.current)
      body.current.scrollTop = body.current.scrollHeight;
  }, [follow, shown]);

  if (!logs.authorized || logs.availability === 'unavailable')
    return (
      <InlineEmpty>
        Logs are available only to the local owner in the native application. No
        log contents or private filesystem paths are exposed to this client.
      </InlineEmpty>
    );
  if (logs.availability === 'missing')
    return <InlineEmpty>No local log is available yet.</InlineEmpty>;
  if (logs.availability === 'corrupt')
    return (
      <ErrorState title="Logs unavailable">
        The local log could not be read safely.
      </ErrorState>
    );
  return (
    <div className="log-console">
      <div className="log-console-bar">
        <div role="group" aria-label="Log levels" className="log-levels">
          {(['ALL', ...LEVELS] as const).map((item) => (
            <button
              key={item}
              type="button"
              className="log-level-chip"
              data-level={item}
              aria-pressed={level === item}
              onClick={() => setLevel(item)}
            >
              {item === 'ALL'
                ? 'All'
                : item.charAt(0) + item.slice(1).toLowerCase()}
              <span>
                {item === 'ALL' ? entries.length : (counts[item] ?? 0)}
              </span>
            </button>
          ))}
        </div>
        <label className="log-search">
          <Search size={13} aria-hidden />
          <input
            type="search"
            aria-label="Search logs"
            placeholder="Search logs"
            value={query}
            onChange={(event) => setQuery(event.currentTarget.value)}
          />
        </label>
        <span className="home-page-header-spacer" />
        <label className="log-switch">
          <Toggle
            label="Follow new lines"
            checked={follow}
            onChange={(event) => setFollow(event.currentTarget.checked)}
          />
          <span aria-hidden>Follow</span>
        </label>
        <label className="log-switch">
          <Toggle
            label="Collapse repeated lines"
            checked={collapse}
            onChange={(event) => setCollapse(event.currentTarget.checked)}
          />
          <span aria-hidden>Collapse repeats</span>
        </label>
        <IconButton
          size="sm"
          label="Copy visible log lines"
          disabled={!shown.length}
          onClick={() =>
            void writeClipboardText(
              shown
                .map(
                  ({ entry, repeats }) =>
                    `${entry.timestamp} [${levelOf(entry)}] ${entry.logger ? `[${entry.logger}] ` : ''}${entry.message}${repeats > 1 ? ` (×${repeats})` : ''}${entry.exception ? `\n${entry.exception}` : ''}`,
                )
                .join('\n'),
              writeClipboard,
            ).then((ok) =>
              setNotice(ok ? 'Log lines copied.' : 'Clipboard unavailable.'),
            )
          }
        >
          <ClipboardCopy size={14} aria-hidden />
        </IconButton>
      </div>
      {error && <p className="home-caption">{error}</p>}
      {notice && (
        <p className="visually-hidden" role="status">
          {notice}
        </p>
      )}
      {shown.length ? (
        <ol
          ref={body}
          className="log-lines"
          aria-label="Log lines"
          tabIndex={0}
        >
          {shown.map(({ entry, repeats }, index) => (
            <li
              key={`${entry.timestamp}-${index}`}
              className="log-line"
              data-level={levelOf(entry)}
            >
              <time
                dateTime={entry.timestamp}
                title={absoluteTime(entry.timestamp)}
              >
                {clock(entry.timestamp)}
              </time>
              <span className="log-line-level">
                {levelOf(entry).slice(0, 4)}
              </span>
              {entry.logger && (
                <span className="log-line-logger">{entry.logger}</span>
              )}
              <span className="log-line-message">{entry.message}</span>
              {repeats > 1 && (
                <span
                  className="log-line-repeats"
                  title={`${repeats} identical lines`}
                >
                  ×{repeats}
                </span>
              )}
              {entry.exception && (
                <details className="log-line-exception">
                  <summary>Traceback</summary>
                  <pre>{entry.exception}</pre>
                </details>
              )}
            </li>
          ))}
        </ol>
      ) : (
        <InlineEmpty>
          {entries.length ? 'No lines match.' : 'No log entries yet.'}
        </InlineEmpty>
      )}
    </div>
  );
}

// History tables ------------------------------------------------------------

function DreamHistory({
  entries,
  now,
}: {
  entries: DreamJournalEntry[];
  now: Date;
}) {
  const [open, setOpen] = useState<number | null>(null);
  const rows = chronological(entries).slice(-MAX_HISTORY).reverse();
  if (!rows.length)
    return <InlineEmpty>Dream Cycle has not recorded a run yet.</InlineEmpty>;
  return (
    <table className="monitor-table" aria-label="Dream Cycle history">
      <thead>
        <tr>
          <th scope="col">When</th>
          <th scope="col" data-numeric="true">
            Merged
          </th>
          <th scope="col" data-numeric="true">
            Enriched
          </th>
          <th scope="col" data-numeric="true">
            Inferred
          </th>
          <th scope="col" data-numeric="true">
            Errors
          </th>
          <th scope="col" className="monitor-table-wide">
            Summary
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map((entry, index) => {
          const expanded = open === index;
          const changes =
            entry.merges.length +
            entry.enrichments.length +
            entry.inferred_relations.length;
          return (
            <Fragment key={`${entry.timestamp}-${index}`}>
              <tr data-expanded={expanded ? 'true' : undefined}>
                <td>
                  <button
                    type="button"
                    className="monitor-table-toggle"
                    aria-expanded={expanded}
                    onClick={() => setOpen(expanded ? null : index)}
                  >
                    <ChevronRight size={13} aria-hidden />
                    <When
                      value={entry.timestamp}
                      fallback="Unknown time"
                      now={now}
                    />
                  </button>
                </td>
                <td data-numeric="true">{entry.merges.length}</td>
                <td data-numeric="true">{entry.enrichments.length}</td>
                <td data-numeric="true">{entry.inferred_relations.length}</td>
                <td
                  data-numeric="true"
                  data-tone={entry.errors.length ? 'danger' : undefined}
                >
                  {entry.errors.length}
                </td>
                <td className="monitor-table-wide" title={entry.summary}>
                  {changes || entry.errors.length
                    ? entry.summary || 'No summary'
                    : 'No changes this cycle'}
                </td>
              </tr>
              {expanded && (
                <tr className="monitor-table-detail">
                  <td colSpan={6}>
                    {entry.merges.length > 0 && (
                      <section aria-label="Merges">
                        <strong>Merged duplicates</strong>
                        <ul>
                          {entry.merges.map((merge, mergeIndex) => (
                            <li
                              key={`${merge.duplicate_subject}-${mergeIndex}`}
                            >
                              {merge.duplicate_subject} →{' '}
                              {merge.survivor_subject}
                              {merge.score !== null &&
                                ` · ${Math.round(merge.score * 100)}% similar`}
                            </li>
                          ))}
                        </ul>
                      </section>
                    )}
                    {entry.enrichments.length > 0 && (
                      <section aria-label="Enrichments">
                        <strong>Enriched memories</strong>
                        <ul>
                          {entry.enrichments.map((item, itemIndex) => (
                            <li key={`${item.subject}-${itemIndex}`}>
                              {item.subject} · {item.old_length} →{' '}
                              {item.new_length} characters
                            </li>
                          ))}
                        </ul>
                      </section>
                    )}
                    {entry.inferred_relations.length > 0 && (
                      <section aria-label="Inferred relations">
                        <strong>Inferred links</strong>
                        <ul>
                          {entry.inferred_relations.map(
                            (relation, relationIndex) => (
                              <li
                                key={`${relation.source_subject}-${relation.target_subject}-${relationIndex}`}
                              >
                                {relation.source_subject} —{' '}
                                {relation.relation_type.replaceAll('_', ' ')} →{' '}
                                {relation.target_subject}
                                {relation.confidence !== null &&
                                  ` · ${Math.round(relation.confidence * 100)}% confident`}
                              </li>
                            ),
                          )}
                        </ul>
                      </section>
                    )}
                    {entry.errors.length > 0 && (
                      <section aria-label="Dream Cycle errors">
                        <strong>Errors</strong>
                        <ul>
                          {entry.errors.map((message, errorIndex) => (
                            <li key={`${message}-${errorIndex}`}>{message}</li>
                          ))}
                        </ul>
                      </section>
                    )}
                    {!changes && !entry.errors.length && (
                      <p className="home-caption">No changes this cycle.</p>
                    )}
                  </td>
                </tr>
              )}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}

function ExtractionHistory({
  entries,
  now,
}: {
  entries: ExtractionJournalEntry[];
  now: Date;
}) {
  const [open, setOpen] = useState<number | null>(null);
  const rows = chronological(entries).slice(-MAX_HISTORY).reverse();
  if (!rows.length)
    return (
      <InlineEmpty>
        Knowledge extraction has not recorded a run yet.
      </InlineEmpty>
    );
  return (
    <table className="monitor-table" aria-label="Extraction history">
      <thead>
        <tr>
          <th scope="col">When</th>
          <th scope="col" data-numeric="true">
            Conversations
          </th>
          <th scope="col" data-numeric="true">
            Saved
          </th>
          <th scope="col" data-numeric="true">
            Blocked
          </th>
          <th scope="col" data-numeric="true">
            Skipped
          </th>
          <th scope="col" data-numeric="true">
            Errors
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map((entry, index) => {
          const expanded = open === index;
          const saved = entry.threads.reduce(
            (sum, thread) => sum + thread.saved,
            0,
          );
          return (
            <Fragment key={`${entry.timestamp}-${index}`}>
              <tr data-expanded={expanded ? 'true' : undefined}>
                <td>
                  <button
                    type="button"
                    className="monitor-table-toggle"
                    aria-expanded={expanded}
                    onClick={() => setOpen(expanded ? null : index)}
                  >
                    <ChevronRight size={13} aria-hidden />
                    <When
                      value={entry.timestamp}
                      fallback="Unknown time"
                      now={now}
                    />
                  </button>
                </td>
                <td data-numeric="true">{entry.threads.length}</td>
                <td data-numeric="true">{saved}</td>
                <td data-numeric="true">{entry.contradictions_blocked}</td>
                <td data-numeric="true">{entry.low_confidence_skipped}</td>
                <td
                  data-numeric="true"
                  data-tone={entry.errors.length ? 'danger' : undefined}
                >
                  {entry.errors.length}
                </td>
              </tr>
              {expanded && (
                <tr className="monitor-table-detail">
                  <td colSpan={6}>
                    <p>{entry.summary || 'No summary.'}</p>
                    {entry.threads.length > 0 && (
                      <ul>
                        {entry.threads.map((thread, threadIndex) => (
                          <li key={`${thread.label}-${threadIndex}`}>
                            {thread.label}: extracted {thread.extracted}, saved{' '}
                            {thread.saved}
                          </li>
                        ))}
                      </ul>
                    )}
                    {entry.errors.length > 0 && (
                      <ul aria-label="Extraction errors">
                        {entry.errors.map((message, errorIndex) => (
                          <li key={`${message}-${errorIndex}`}>{message}</li>
                        ))}
                      </ul>
                    )}
                  </td>
                </tr>
              )}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}

// Metrics -------------------------------------------------------------------

type Metric = {
  key: string;
  label: string;
  values: number[];
  source: string;
};

function MetricTile({ metric }: { metric: Metric }) {
  const latest = metric.values[metric.values.length - 1];
  const previous = metric.values[metric.values.length - 2];
  const delta =
    previous === undefined
      ? ''
      : latest === previous
        ? 'same as last run'
        : `${latest > previous ? '+' : '−'}${Math.abs(latest - previous)} vs last run`;
  return (
    <div className="metric-tile">
      <span className="metric-label">{metric.label}</span>
      <span className="metric-value">
        {latest ?? '—'}
        <small>{metric.source}</small>
      </span>
      <span className="metric-foot">
        <span className="metric-delta">{delta || 'first run'}</span>
        <Sparkline values={metric.values.slice(-12)} label={metric.label} />
      </span>
    </div>
  );
}

// Page ----------------------------------------------------------------------

export default function MonitorHome({
  snapshot,
  loading,
  error,
  onRefresh,
  onRunDiagnosis,
  loadLogs,
  loadTasks,
  writeClipboard,
  now: suppliedNow,
}: MonitorHomeProps) {
  const [diagnosis, setDiagnosis] = useState<SystemDiagnosis | null>(null);
  const [diagnosisBusy, setDiagnosisBusy] = useState(false);
  const [diagnosisError, setDiagnosisError] = useState('');
  const [copyNotice, setCopyNotice] = useState('');
  const [drawer, setDrawer] = useState<TileKey | null>(null);
  const [range, setRange] = useState<'24h' | '7d'>('24h');
  const [history, setHistory] = useState<'dream' | 'extraction'>('dream');
  const [tasks, setTasks] = useState<TaskSummaryPage | null>(null);
  const [clock, setClock] = useState(() => new Date());
  const now = suppliedNow ?? clock;
  useEffect(() => {
    if (suppliedNow) return;
    setClock(new Date());
    const timer = window.setInterval(() => setClock(new Date()), 60_000);
    return () => window.clearInterval(timer);
  }, [suppliedNow, snapshot]);

  useEffect(() => {
    if (!loadTasks) return;
    let active = true;
    loadTasks().then(
      (page) => active && setTasks(page),
      () => {},
    );
    return () => {
      active = false;
    };
  }, [loadTasks]);

  async function runDiagnosis() {
    if (diagnosisBusy) return;
    setDiagnosisBusy(true);
    setDiagnosisError('');
    setCopyNotice('');
    try {
      setDiagnosis(await onRunDiagnosis());
    } catch (cause) {
      setDiagnosisError(clientError(cause).message);
    } finally {
      setDiagnosisBusy(false);
    }
  }

  async function copyDiagnosis() {
    if (!diagnosis) return;
    const report = [
      'Row-Bot System Diagnosis',
      '========================================',
      ...diagnosis.checks.map(
        (check) => `${check.name}: ${check.status} — ${check.detail}`,
      ),
    ].join('\n');
    setCopyNotice(
      (await writeClipboardText(report, writeClipboard))
        ? 'Diagnosis report copied.'
        : 'Could not copy the report. Check clipboard permission and retry.',
    );
  }

  const tiles = useMemo<TileView[]>(() => {
    const failing = (tasks?.items ?? []).filter((task) =>
      FAILED_RUN_STATUSES.has(String(task.last_status ?? '').toLowerCase()),
    ).length;
    if (diagnosis) {
      const groups = groupChecks(diagnosis.checks);
      return TILES.map((tile) => {
        const view = tileFromChecks(tile, groups.get(tile.key)!);
        // Diagnosis checks the scheduler itself; failed runs still need you.
        if (tile.key === 'scheduler' && failing && view.tone !== 'danger')
          return {
            ...view,
            tone: 'warning',
            status: 'Needs attention',
            detail: `${plural(failing, 'workflow')} failed last time · ${view.detail}`,
          };
        return view;
      });
    }
    return TILES.map((tile) => {
      const base: TileView = {
        ...tile,
        tone: 'neutral',
        status: 'Not checked',
        detail: 'Run diagnosis to check',
        checks: [],
      };
      if (tile.key === 'knowledge' && snapshot) {
        const { extraction } = snapshot;
        if (extraction.availability !== 'available')
          return {
            ...base,
            tone: 'warning',
            status: 'Needs attention',
            detail: availabilityCopy('Extraction', extraction.availability),
          };
        const last = parseTimestamp(extraction.last_run);
        const late =
          !last ||
          now.getTime() - last.getTime() >
            extraction.interval_hours * 2 * 3_600_000;
        return {
          ...base,
          tone: last && !late ? 'success' : 'neutral',
          status: last ? (late ? 'Idle' : 'OK') : 'Not run yet',
          detail: last
            ? `Extraction ran ${relativeTime(extraction.last_run, now)}`
            : 'Extraction starts automatically',
        };
      }
      if (tile.key === 'scheduler' && tasks) {
        const scheduled = tasks.items.filter(
          (task) => task.enabled && (task.schedule || task.at),
        );
        const next = scheduled
          .map((task) => parseTimestamp(task.next_run))
          .filter((date): date is Date => Boolean(date))
          .sort((left, right) => left.getTime() - right.getTime())[0];
        return {
          ...base,
          tone: failing ? 'warning' : scheduled.length ? 'success' : 'neutral',
          status: failing
            ? 'Needs attention'
            : scheduled.length
              ? 'OK'
              : 'Idle',
          detail: failing
            ? `${plural(failing, 'workflow')} failed last time`
            : scheduled.length
              ? `${scheduled.length} scheduled${next ? ` · next ${relativeTime(next.toISOString(), now)}` : ''}`
              : 'No scheduled workflows',
        };
      }
      return base;
    });
  }, [diagnosis, now, snapshot, tasks]);

  const lanes = useMemo<Lane[]>(() => {
    const dated = (value: string) => parseTimestamp(value);
    const extraction = (snapshot?.extraction_journal ?? []).flatMap((entry) => {
      const at = dated(entry.timestamp);
      return at
        ? [
            {
              at,
              failed: entry.errors.length > 0,
              label: `Extraction: ${entry.summary || 'ran'}`,
            },
          ]
        : [];
    });
    const dreams = (snapshot?.dream_journal ?? []).flatMap((entry) => {
      const at = dated(entry.timestamp);
      return at
        ? [
            {
              at,
              failed: entry.errors.length > 0,
              label: `Dream Cycle: ${entry.summary || 'ran'}`,
            },
          ]
        : [];
    });
    const runs = (tasks?.items ?? []).flatMap((task) =>
      (task.recent_runs ?? []).flatMap((run) => {
        const at = dated(run.started_at);
        return at
          ? [
              {
                at,
                failed: FAILED_RUN_STATUSES.has(run.status.toLowerCase()),
                label: `${task.name}: ${runStatus(run.status).label}`,
              },
            ]
          : [];
      }),
    );
    const channels = (snapshot?.logs.entries ?? []).flatMap((entry) => {
      const at = dated(entry.timestamp);
      return at &&
        /channel|telegram|whatsapp|slack|discord|sms/i.test(entry.logger)
        ? [
            {
              at,
              failed: levelOf(entry) === 'ERROR',
              label: `Channel event (${levelOf(entry).toLowerCase()})`,
            },
          ]
        : [];
    });
    return [
      { key: 'extraction', label: 'Extraction', events: extraction },
      { key: 'dream', label: 'Dream Cycle', events: dreams },
      { key: 'workflows', label: 'Workflow runs', events: runs },
      {
        key: 'channels',
        label: 'Channel events',
        events: channels,
        note: 'from recent log lines',
      },
    ];
  }, [snapshot, tasks]);

  const metrics = useMemo<Metric[]>(() => {
    const extraction = chronological(snapshot?.extraction_journal);
    const dreams = chronological(snapshot?.dream_journal);
    return [
      {
        key: 'saved',
        label: 'Memories saved',
        source: 'extraction',
        values: extraction.length
          ? extraction.map((entry) =>
              entry.threads.reduce((sum, thread) => sum + thread.saved, 0),
            )
          : snapshot
            ? [snapshot.extraction.entities_saved]
            : [],
      },
      {
        key: 'scanned',
        label: 'Conversations read',
        source: 'extraction',
        values: extraction.length
          ? extraction.map((entry) => entry.threads.length)
          : snapshot
            ? [snapshot.extraction.threads_scanned]
            : [],
      },
      {
        key: 'blocked',
        label: 'Contradictions blocked',
        source: 'extraction',
        values: extraction.map((entry) => entry.contradictions_blocked),
      },
      {
        key: 'merged',
        label: 'Duplicates merged',
        source: 'Dream Cycle',
        values: dreams.map((entry) => entry.merges.length),
      },
      {
        key: 'enriched',
        label: 'Memories enriched',
        source: 'Dream Cycle',
        values: dreams.map((entry) => entry.enrichments.length),
      },
      {
        key: 'inferred',
        label: 'Links inferred',
        source: 'Dream Cycle',
        values: dreams.map((entry) => entry.inferred_relations.length),
      },
    ].filter((metric) => metric.values.length > 0);
  }, [snapshot]);

  const open = tiles.find((tile) => tile.key === drawer) ?? null;
  const checkedAt = diagnosis?.checks.length
    ? Math.max(...diagnosis.checks.map((check) => check.checked_at))
    : null;
  return (
    <section
      className="monitor-home home-page"
      aria-labelledby="monitor-heading"
    >
      <header className="home-page-header">
        <h2 id="monitor-heading">System Monitor</h2>
        <p className="home-caption" role="status">
          {diagnosisBusy
            ? 'Checking local services…'
            : checkedAt
              ? `Checked ${relativeTime(new Date(checkedAt * 1000).toISOString(), now)}`
              : 'Passive status. Run diagnosis to check every service.'}
        </p>
        <span className="home-page-header-spacer" />
        {diagnosis && (
          <IconButton
            size="sm"
            label="Copy diagnosis report"
            onClick={() => void copyDiagnosis()}
          >
            <ClipboardCopy size={14} aria-hidden />
          </IconButton>
        )}
        <IconButton
          size="sm"
          label={loading ? 'Refreshing…' : 'Refresh monitor'}
          disabled={loading}
          onClick={onRefresh}
        >
          <RefreshCw size={15} aria-hidden />
        </IconButton>
        <Button
          variant="primary"
          className="small"
          disabled={diagnosisBusy}
          onClick={() => void runDiagnosis()}
        >
          <Stethoscope size={14} aria-hidden />
          {diagnosisBusy ? 'Checking…' : 'Run diagnosis'}
        </Button>
      </header>
      {copyNotice && (
        <p className="home-caption" role="status">
          {copyNotice}
        </p>
      )}
      {diagnosisError && (
        <ErrorState title="Diagnosis unavailable">{diagnosisError}</ErrorState>
      )}
      {error && (
        <ErrorState
          title="Monitor refresh failed"
          action={
            <Button className="small" onClick={onRefresh}>
              Try again
            </Button>
          }
        >
          {error}
        </ErrorState>
      )}
      <ul className="health-strip" aria-label="Health">
        {tiles.map((tile) => {
          const Icon = tile.icon;
          return (
            <li key={tile.key}>
              <button
                type="button"
                className="health-tile"
                data-tone={tile.tone}
                aria-busy={diagnosisBusy || undefined}
                onClick={() => setDrawer(tile.key)}
              >
                <span className="health-tile-head">
                  <Icon size={15} aria-hidden />
                  <span className="health-tile-name">{tile.label}</span>
                </span>
                <StatusDot
                  tone={tile.tone}
                  label={tile.status}
                  showLabel
                  pulse={diagnosisBusy}
                />
                <span className="health-tile-detail">{tile.detail}</span>
              </button>
            </li>
          );
        })}
      </ul>
      {!snapshot && loading && (
        <p className="home-caption" role="status">
          Loading System Monitor…
        </p>
      )}
      {snapshot && (
        <>
          <section
            className="monitor-section"
            aria-labelledby="activity-heading"
          >
            <header className="monitor-section-head">
              <h3 id="activity-heading">Activity</h3>
              <Segmented
                label="Activity range"
                size="sm"
                value={range}
                onChange={setRange}
                options={[
                  { value: '24h', label: '24 hours' },
                  { value: '7d', label: '7 days' },
                ]}
              />
            </header>
            <Swimlane
              lanes={lanes}
              hours={range === '24h' ? 24 : 24 * 7}
              now={now}
            />
          </section>
          <section
            className="monitor-section"
            aria-labelledby="maintenance-heading"
          >
            <header className="monitor-section-head">
              <h3 id="maintenance-heading">Maintenance</h3>
              <p className="home-caption">
                {snapshot.extraction.availability === 'available'
                  ? snapshot.extraction.last_run
                    ? `Extraction every ${snapshot.extraction.interval_hours}h · ran ${relativeTime(snapshot.extraction.last_run, now)}`
                    : 'Extraction has not run yet; it starts automatically.'
                  : availabilityCopy(
                      'Knowledge extraction',
                      snapshot.extraction.availability,
                    )}
                {' · '}
                {snapshot.dream.availability !== 'available'
                  ? availabilityCopy('Dream Cycle', snapshot.dream.availability)
                  : !snapshot.dream.enabled
                    ? 'Dream Cycle is off (Settings › Preferences).'
                    : `Dream Cycle ${snapshot.dream.window} · ${snapshot.dream.last_run ? `ran ${relativeTime(snapshot.dream.last_run, now)}` : 'not run yet'}`}
              </p>
            </header>
            {metrics.length ? (
              <div
                className="metric-grid"
                aria-label="Maintenance metrics"
                role="group"
              >
                {metrics.map((metric) => (
                  <MetricTile key={metric.key} metric={metric} />
                ))}
              </div>
            ) : (
              <InlineEmpty>No maintenance runs recorded yet.</InlineEmpty>
            )}
          </section>
          <section
            className="monitor-section"
            aria-labelledby="history-heading"
          >
            <header className="monitor-section-head">
              <h3 id="history-heading">History</h3>
              <Segmented
                label="History"
                size="sm"
                value={history}
                onChange={setHistory}
                options={[
                  { value: 'dream', label: 'Dream Cycle' },
                  { value: 'extraction', label: 'Extraction' },
                ]}
              />
            </header>
            {history === 'dream' ? (
              <DreamHistory entries={snapshot.dream_journal} now={now} />
            ) : (
              <ExtractionHistory
                entries={snapshot.extraction_journal}
                now={now}
              />
            )}
          </section>
          <section className="monitor-section" aria-labelledby="logs-heading">
            <header className="monitor-section-head">
              <h3 id="logs-heading">Logs</h3>
            </header>
            <LogConsole
              logs={snapshot.logs}
              loadLogs={loadLogs}
              writeClipboard={writeClipboard}
            />
          </section>
        </>
      )}
      <Drawer
        open={open !== null}
        onOpenChange={(next) => {
          if (!next) setDrawer(null);
        }}
        title={open?.label ?? ''}
        description={open ? `${open.status} · ${open.detail}` : undefined}
        closeLabel="Close health detail"
        className="health-drawer"
      >
        {open && (
          <div className="health-detail">
            {open.checks.length ? (
              <ul className="health-checks" aria-label={`${open.label} checks`}>
                {open.checks.map((check, index) => {
                  const view = CHECK_STATUS[check.status];
                  const route = SETTINGS_ROUTES[check.settings_tab];
                  return (
                    <li key={`${check.name}-${index}`}>
                      <div className="health-check-head">
                        <strong>{check.name}</strong>
                        <StatusDot
                          tone={view.tone}
                          label={view.label}
                          showLabel
                        />
                      </div>
                      <p>{check.detail}</p>
                      {route && (
                        <Link
                          className="overview-section-link"
                          to={`/settings/${route}`}
                        >
                          Open {check.settings_tab} settings
                        </Link>
                      )}
                    </li>
                  );
                })}
              </ul>
            ) : (
              <InlineEmpty>
                {diagnosis
                  ? 'No services were checked in this area.'
                  : 'Run diagnosis to check the services in this area. It may contact configured local services and test network reachability.'}
              </InlineEmpty>
            )}
            <Button
              className="small"
              disabled={diagnosisBusy}
              onClick={() => void runDiagnosis()}
            >
              <Stethoscope size={14} aria-hidden />
              {diagnosisBusy
                ? 'Checking…'
                : diagnosis
                  ? 'Run again'
                  : 'Run diagnosis'}
            </Button>
          </div>
        )}
      </Drawer>
    </section>
  );
}
