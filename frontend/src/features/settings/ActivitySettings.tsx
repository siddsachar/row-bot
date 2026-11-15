import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Bot,
  ChevronDown,
  Code2,
  Download,
  Lightbulb,
  MessageSquare,
  Radio,
  Search,
  Target,
  Trash2,
  Workflow,
  type LucideIcon,
} from 'lucide-react';
import type {
  ActivityCleared,
  ActivityEntry,
  ActivityExportRequest,
  ActivityFilters,
  ActivityPage,
  SaveOffer,
} from '../../api/types';
import { clientError } from '../../api/errors';
import type { CapabilityResult, SavedFile } from '../../platform/types';
import { unavailable } from '../../platform/types';
import { useLiveUpdate } from '../../live';
import { useOptionalRuntime } from '../../runtime';
import { parseTimestamp } from '../../ui/format';
import { useNotify, useOptionalOverlay } from '../../ui/overlays';
import {
  Button,
  EmptyState,
  ErrorState,
  IconButton,
  Input,
  Segmented,
  Select,
  Skeleton,
} from '../../ui/primitives';
import { announceSaved, saveProblem } from '../shell/save-file';
import { SettingsGroup } from './anatomy';
import './activity.css';

/** What the page reads and does; the app's controller and Save path by default. */
export type ActivityIO = {
  list(filters: ActivityFilters, signal?: AbortSignal): Promise<ActivityPage>;
  exportCsv(filters: ActivityExportRequest): Promise<SaveOffer>;
  save(reference: string, name: string): Promise<CapabilityResult<SavedFile>>;
  clear(): Promise<ActivityCleared>;
};

type Show = NonNullable<ActivityExportRequest['outcome']> | 'all';
type Where = NonNullable<ActivityExportRequest['surface']>;
type Tone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger';

const PAGE = 50;

/** Each outcome in plain words, and its tone. */
export const OUTCOMES: Record<
  ActivityEntry['outcome'],
  { label: string; tone: Tone }
> = {
  ran: { label: 'Ran', tone: 'neutral' },
  asked: { label: 'Waiting for you', tone: 'accent' },
  approved_by_you: { label: 'You approved', tone: 'success' },
  approved_by_reviewer: { label: 'Reviewer approved', tone: 'success' },
  hands_off: { label: 'Hands-off', tone: 'warning' },
  allowed_by_rule: { label: 'Your rule', tone: 'success' },
  allowed_in_chat: { label: 'Allowed in this chat', tone: 'success' },
  denied: { label: 'You said no', tone: 'danger' },
  changed: { label: 'Changed after you approved — didn’t run', tone: 'danger' },
  blocked: { label: 'Blocked', tone: 'danger' },
  failed: { label: 'Didn’t work', tone: 'danger' },
};

function outcomeOf(entry: ActivityEntry) {
  // A card the turn left unanswered isn't waiting any more.
  if (entry.outcome === 'asked' && !entry.pending)
    return { label: 'Not answered', tone: 'neutral' as Tone };
  return OUTCOMES[entry.outcome];
}

const DECIDED: Record<ActivityEntry['decided_by'], string> = {
  none: 'Needed no approval',
  person: 'You',
  reviewer: 'The reviewer (Auto)',
  hands_off: 'Hands-off',
  rule: 'Your rule',
  chat_grant: 'What you allowed in this chat',
  block: 'The Block level',
  policy: 'A safety policy',
};

const LEVELS: Record<string, string> = {
  block: 'Block',
  approve: 'Ask',
  auto: 'Auto',
  full: 'Hands-off',
};

const SURFACES: Record<
  ActivityEntry['surface'],
  { label: string; icon: LucideIcon }
> = {
  chat: { label: 'Chat', icon: MessageSquare },
  workflow: { label: 'Workflow', icon: Workflow },
  agent: { label: 'Helper', icon: Bot },
  goal: { label: 'Goal', icon: Target },
  channel: { label: 'Channel', icon: Radio },
  proactive: { label: 'Proactive', icon: Lightbulb },
  developer: { label: 'Developer', icon: Code2 },
};

const WHERE: { value: Where; label: string }[] = [
  { value: '', label: 'Everywhere' },
  { value: 'chat', label: 'Chats' },
  { value: 'workflow', label: 'Workflows' },
  { value: 'agent', label: 'Helpers' },
  { value: 'goal', label: 'Goals' },
  { value: 'channel', label: 'Channels' },
  { value: 'proactive', label: 'Proactive' },
  { value: 'developer', label: 'Developer' },
];

/** The day an entry belongs to, in words: Today, Yesterday, or the date. */
function dayWords(date: Date, now: Date) {
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (date.toDateString() === now.toDateString()) return 'Today';
  if (date.toDateString() === yesterday.toDateString()) return 'Yesterday';
  return date.toLocaleDateString(undefined, {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
  });
}

function useDefaultIO(): ActivityIO | null {
  const runtime = useOptionalRuntime();
  return useMemo(() => {
    if (!runtime) return null;
    const { controller, platform } = runtime;
    return {
      list: (filters, signal) => controller.activity(filters, signal),
      exportCsv: (filters) => controller.exportActivity(filters),
      save: (reference, name) =>
        platform
          .save(reference, name)
          .catch((): CapabilityResult<SavedFile> =>
            unavailable('operation_failed'),
          ),
      clear: () => controller.clearActivity(),
    };
  }, [runtime]);
}

/**
 * Settings › System › Activity (D1): every action Row-Bot took with a tool,
 * newest first, by day, with its outcome and who decided. Filters, a CSV
 * export and Clear (asks first). Kept 90 days on this computer.
 */
export default function ActivitySettings({ io: given }: { io?: ActivityIO }) {
  const fallback = useDefaultIO();
  const io = given ?? fallback;
  const notify = useNotify();
  const overlay = useOptionalOverlay();
  const [show, setShow] = useState<Show>('needed');
  const [where, setWhere] = useState<Where>('');
  const [typed, setTyped] = useState('');
  const [q, setQ] = useState('');
  const [items, setItems] = useState<ActivityEntry[] | null>(null);
  const [next, setNext] = useState<number | null>(null);
  const [anything, setAnything] = useState(false);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [open, setOpen] = useState<number | null>(null);
  const [busy, setBusy] = useState<'' | 'export' | 'clear' | 'more'>('');
  const filters = useMemo<ActivityFilters>(
    () => ({
      outcome: show === 'all' ? '' : show,
      surface: where,
      q: q.trim(),
    }),
    [show, where, q],
  );
  // An answered card, a run that ended: the newest page again.
  useLiveUpdate(['approvals', 'runs'], () => setReload((value) => value + 1));
  // Typing settles before it searches.
  useEffect(() => {
    const timer = window.setTimeout(() => setQ(typed), 250);
    return () => window.clearTimeout(timer);
  }, [typed]);
  useEffect(() => {
    if (!io) return;
    const request = new AbortController();
    const filtered = Boolean(filters.outcome || filters.surface || filters.q);
    io.list({ ...filters, limit: PAGE }, request.signal)
      .then(async (page) => {
        // Nothing under these filters: is there anything at all? (Nothing yet, or Nothing matches.)
        const any =
          page.items.length > 0 ||
          (filtered &&
            (await io.list({ limit: 1 }, request.signal)).items.length > 0);
        if (request.signal.aborted) return;
        setItems(page.items);
        setNext(page.next_before);
        setAnything(any);
        setError('');
      })
      .catch((cause) => {
        if (!request.signal.aborted) setError(clientError(cause).message);
      });
    return () => request.abort();
  }, [io, filters, reload]);

  async function earlier() {
    if (!io || next === null) return;
    setBusy('more');
    try {
      const page = await io.list({ ...filters, limit: PAGE, before: next });
      setItems((current) => [...(current ?? []), ...page.items]);
      setNext(page.next_before);
    } catch (cause) {
      notify(clientError(cause).message, 'warning');
    } finally {
      setBusy('');
    }
  }

  async function exportCsv() {
    if (!io) return;
    setBusy('export');
    try {
      const offer = await io.exportCsv({
        outcome: filters.outcome ?? '',
        surface: filters.surface ?? '',
        q: filters.q ?? '',
      });
      const result = await io.save(offer.reference, offer.file_name);
      if (result.status === 'ok')
        announceSaved(notify, result.value, offer.file_name);
      else {
        const problem = saveProblem(result, 'the activity');
        if (problem) notify(problem, 'warning');
      }
    } catch (cause) {
      notify(clientError(cause).message, 'warning');
    } finally {
      setBusy('');
    }
  }

  async function clearAll() {
    if (!io) return;
    setBusy('clear');
    try {
      const cleared = await io.clear();
      setItems([]);
      setNext(null);
      setAnything(false);
      notify(
        cleared.removed === 1
          ? 'Cleared 1 action'
          : `Cleared ${cleared.removed.toLocaleString()} actions`,
      );
    } catch (cause) {
      notify(clientError(cause).message, 'warning');
    } finally {
      setBusy('');
    }
  }

  function askToClear() {
    const confirm = () => {
      overlay?.close();
      void clearAll();
    };
    if (!overlay) return confirm();
    overlay.open({
      kind: 'alert',
      title: 'Clear all activity?',
      description:
        'Every action in the list is deleted from this computer. This can’t be undone.',
      confirmLabel: 'Clear activity',
      onConfirm: confirm,
    });
  }

  const now = new Date();
  const days: { day: string; entries: ActivityEntry[] }[] = [];
  for (const entry of items ?? []) {
    const date = parseTimestamp(entry.at);
    const day = date ? dayWords(date, now) : '';
    const last = days[days.length - 1];
    if (last?.day === day) last.entries.push(entry);
    else days.push({ day, entries: [entry] });
  }

  return (
    <SettingsGroup
      title="Every action"
      anchor="activity"
      className="alog-log"
      note="Kept 90 days on this computer. Only you can clear it."
      meta={
        <div className="alog-tools">
          <IconButton
            size="sm"
            label="Export as CSV"
            disabled={!io || busy === 'export'}
            onClick={() => void exportCsv()}
          >
            <Download size={15} aria-hidden />
          </IconButton>
          <IconButton
            size="sm"
            label="Clear activity"
            disabled={!io || busy === 'clear' || !anything}
            onClick={askToClear}
          >
            <Trash2 size={15} aria-hidden />
          </IconButton>
        </div>
      }
    >
      <div className="alog-filters" role="group" aria-label="Filters">
        <Segmented
          label="Show"
          size="sm"
          className="alog-show"
          value={show}
          onChange={setShow}
          options={[
            { value: 'needed', label: 'Needed approval' },
            { value: 'all', label: 'All' },
            { value: 'denied', label: 'You said no' },
            { value: 'blocked', label: 'Blocked' },
          ]}
        />
        <Select
          aria-label="Where"
          className="alog-where"
          value={where}
          onChange={(event) => setWhere(event.currentTarget.value as Where)}
        >
          {WHERE.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
        <label className="alog-search">
          <Search size={14} aria-hidden />
          <Input
            type="search"
            aria-label="Search activity"
            placeholder="Search"
            value={typed}
            maxLength={200}
            onChange={(event) => setTyped(event.currentTarget.value)}
          />
        </label>
      </div>
      {error && (
        <ErrorState
          title="Activity couldn’t be read"
          action={
            <Button
              className="small"
              onClick={() => {
                setError('');
                setReload((value) => value + 1);
              }}
            >
              Retry
            </Button>
          }
        >
          {error}
        </ErrorState>
      )}
      {items === null && !error && <Skeleton label="Reading activity" />}
      {items !== null &&
        !items.length &&
        !error &&
        (anything ? (
          <p className="alog-quiet">
            Nothing matches these filters.{' '}
            {(show !== 'all' || where !== '' || typed !== '') && (
              <Button
                variant="ghost"
                className="small"
                onClick={() => {
                  setShow('all');
                  setWhere('');
                  setTyped('');
                }}
              >
                Show all activity
              </Button>
            )}
          </p>
        ) : (
          <EmptyState title="Nothing yet">
            Every action Row-Bot takes will show here.
          </EmptyState>
        ))}
      {days.map(({ day, entries }) => (
        <div key={day} className="alog-day">
          <h4>{day}</h4>
          <ul className="alog-list">
            {entries.map((entry) => (
              <ActivityRow
                key={entry.entry_id}
                entry={entry}
                open={open === entry.entry_id}
                onToggle={() =>
                  setOpen((current) =>
                    current === entry.entry_id ? null : entry.entry_id,
                  )
                }
              />
            ))}
          </ul>
        </div>
      ))}
      {items !== null && items.length > 0 && next !== null && (
        <Button
          variant="ghost"
          className="small alog-more"
          disabled={busy === 'more'}
          onClick={() => void earlier()}
        >
          Show earlier
        </Button>
      )}
      {items !== null && items.length > 0 && next === null && (
        <p className="alog-end">That’s everything from the last 90 days.</p>
      )}
    </SettingsGroup>
  );
}

function ActivityRow({
  entry,
  open,
  onToggle,
}: {
  entry: ActivityEntry;
  open: boolean;
  onToggle: () => void;
}) {
  const outcome = outcomeOf(entry);
  const surface = SURFACES[entry.surface];
  const Icon = surface.icon;
  const time = parseTimestamp(entry.at)?.toLocaleTimeString(undefined, {
    hour: 'numeric',
    minute: '2-digit',
  });
  const where =
    entry.conversation_title ||
    (entry.conversation_id ? 'A chat' : surface.label);
  const detailId = `alog-detail-${entry.entry_id}`;
  return (
    <li className="alog-row" data-open={open || undefined}>
      <button
        type="button"
        className="alog-row-main"
        aria-expanded={open}
        aria-controls={detailId}
        onClick={onToggle}
      >
        <span className="alog-time">{time}</span>
        <span className="alog-icon" title={surface.label}>
          <Icon size={15} aria-hidden />
        </span>
        <span className="alog-what">
          <span className="alog-title">
            {entry.app_name ? `${entry.app_name} › ` : ''}
            {entry.title || entry.tool}
          </span>
          {entry.summary && (
            <span className="alog-summary">{entry.summary}</span>
          )}
          <span className="alog-where-line">
            {surface.label !== 'Chat' && `${surface.label} · `}
            {where}
          </span>
        </span>
        <span className="alog-outcome" data-tone={outcome.tone}>
          {outcome.label}
        </span>
        <ChevronDown size={14} aria-hidden className="alog-chevron" />
      </button>
      {open && (
        <div className="alog-detail" id={detailId}>
          <dl>
            <div>
              <dt>Decided by</dt>
              <dd>{DECIDED[entry.decided_by]}</dd>
            </div>
            {entry.reason && (
              <div>
                <dt>
                  {entry.decided_by === 'person' ? 'Why it asked you' : 'Why'}
                </dt>
                <dd>{entry.reason}</dd>
              </div>
            )}
            {entry.level && (
              <div>
                <dt>Approval level</dt>
                <dd>{LEVELS[entry.level] ?? entry.level}</dd>
              </div>
            )}
            <div>
              <dt>Tool</dt>
              <dd>
                <code>{entry.tool}</code>
              </dd>
            </div>
          </dl>
          {entry.conversation_id && (
            <p className="alog-open">
              <Link
                to={`/conversations/${encodeURIComponent(entry.conversation_id)}`}
              >
                Open in chat
              </Link>
            </p>
          )}
        </div>
      )}
    </li>
  );
}
