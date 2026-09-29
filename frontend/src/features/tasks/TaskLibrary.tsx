import {
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type Ref,
} from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import * as Popover from '@radix-ui/react-popover';
import {
  ArrowLeft,
  Bell,
  Braces,
  CalendarClock,
  CornerDownLeft,
  Database,
  FileDown,
  FileUp,
  Lock,
  Mail,
  MoreHorizontal,
  Pencil,
  Play,
  RefreshCw,
  Search,
  Send,
  Square,
  Trash2,
  Zap,
} from 'lucide-react';
import type {
  TaskDeliverySnapshot,
  TaskRunReview,
  TaskSummary,
  TaskSummaryPage,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useClientState, useRuntime } from '../../runtime';
import TaskEditor from './TaskEditor';
import TaskRun from './TaskRun';
import { taskRuns } from './task-runs';
import { taskEdits, taskMutation, type TaskCommandOwner } from './task-edits';
import TaskGraphEditor from './TaskGraphEditor';
import TaskSettingsEditor from './TaskSettingsEditor';
import { RunSparkline } from './RunSparkline';
import {
  Button,
  EmptyState,
  ErrorState,
  IconButton,
  Input,
  Menu,
  Segmented,
  Skeleton,
  Toggle,
  type MenuAction,
} from '../../ui/primitives';
import { Drawer, ModalTask, useOverlay } from '../../ui/overlays';
import {
  FAILED_RUN_STATUSES,
  When,
  runStatus,
  scheduleWords,
} from '../home/home-format';
import { humanizeToken, parseTimestamp } from '../../ui/format';

const workflowIcons = {
  notifications: Bell,
  notifications_active: Bell,
  alarm: Bell,
  schedule: CalendarClock,
  event: CalendarClock,
  code: Braces,
  terminal: Braces,
  email: Mail,
  mail: Mail,
  database: Database,
  storage: Database,
  upload: FileUp,
  download: FileDown,
  sync: RefreshCw,
  refresh: RefreshCw,
} as const;

function workflowIcon(icon: string, reminder: boolean) {
  const key = icon.trim().toLowerCase() as keyof typeof workflowIcons;
  return workflowIcons[key] ?? (reminder ? Bell : Zap);
}

/** Emoji icons are shown as they are; named icons map to glyphs. */
function WorkflowGlyph({ task }: { task: TaskSummary }) {
  const icon = task.icon.trim();
  if (icon && !/^[a-z_]+$/i.test(icon))
    return <span className="workflow-emoji">{icon}</span>;
  const Icon = workflowIcon(icon, task.notify_only);
  return <Icon size={16} />;
}

type Filter = 'all' | 'enabled' | 'scheduled' | 'failed';
const FILTERS: { value: Filter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'enabled', label: 'Enabled' },
  { value: 'scheduled', label: 'Scheduled' },
  { value: 'failed', label: 'Failed' },
];
/** Scheduled and Failed filter every saved workflow, read page by page. */
const CLIENT_FILTER_PAGES = 20;

function matchesFilter(task: TaskSummary, filter: Filter) {
  if (filter === 'scheduled')
    return task.enabled && Boolean(task.schedule || task.at);
  if (filter === 'failed')
    return FAILED_RUN_STATUSES.has(
      String(task.last_status ?? '').toLowerCase(),
    );
  return true;
}

function isRunning(task: TaskSummary) {
  return Boolean(
    task.active_run || task.last_status?.toLowerCase() === 'running',
  );
}

function DeliveryDefaults({
  defaults,
  options,
  busy,
  onSave,
}: {
  defaults: ReadonlyArray<{ id: string; label: string }>;
  options: ReadonlyArray<{ id: string; label: string }>;
  busy: boolean;
  onSave: (ids: readonly string[]) => Promise<boolean>;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Set<string>>(new Set());
  const saved = defaults.map((item) => item.id);
  const changed =
    draft.size !== saved.length || saved.some((id) => !draft.has(id));
  const summary = ['Web app', ...defaults.map((item) => item.label)].join(', ');
  return (
    <Popover.Root
      open={open}
      onOpenChange={(next) => {
        if (next) setDraft(new Set(saved));
        setOpen(next);
      }}
    >
      <Popover.Trigger asChild>
        <IconButton
          size="sm"
          label="Delivery defaults"
          title={`Delivery defaults: ${summary}`}
          className="workflow-delivery-trigger"
        >
          <Send size={15} aria-hidden />
          {defaults.length > 0 && (
            <span className="workflow-delivery-count" aria-hidden>
              {defaults.length}
            </span>
          )}
        </IconButton>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          className="popover workflow-delivery-popover"
          aria-label="Default delivery channels"
          align="end"
          sideOffset={6}
          collisionPadding={12}
        >
          <div className="workflow-delivery-head">
            <strong>Delivery defaults</strong>
            <p>
              Workflows that use the defaults deliver here. A workflow can
              choose its own channels.
            </p>
          </div>
          <ul className="workflow-delivery-options">
            <li>
              <span className="workflow-delivery-locked">
                <Lock size={13} aria-hidden /> Web app
              </span>
              <span className="home-caption">Always on</span>
            </li>
            {options.map((channel) => (
              <li key={channel.id}>
                <label>
                  <input
                    type="checkbox"
                    checked={draft.has(channel.id)}
                    onChange={(event) => {
                      const checked = event.currentTarget.checked;
                      setDraft((previous) => {
                        const next = new Set(previous);
                        if (checked) next.add(channel.id);
                        else next.delete(channel.id);
                        return next;
                      });
                    }}
                  />
                  {channel.label}
                </label>
              </li>
            ))}
          </ul>
          {!options.length && (
            <p className="home-caption">No external channels are set up.</p>
          )}
          <div className="workflow-delivery-actions">
            <Popover.Close asChild>
              <Button className="small">Cancel</Button>
            </Popover.Close>
            <Button
              variant="primary"
              className="small"
              disabled={!changed || busy}
              onClick={() =>
                void onSave([...draft]).then((ok) => ok && setOpen(false))
              }
            >
              Save defaults
            </Button>
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

export function SavedTasks({
  load,
  onEdit,
  onCreate,
  onRuns,
  onGraph,
  onSettings,
  onOpenConversation,
  deliveryDefaults = [],
  deliveryOptions = [],
  onToggleEnabled,
  onDuplicate,
  onReview,
  onRun,
  onDelete,
  onBulkDelete,
  onStop,
  onSetDeliveryDefaults,
  refreshToken = 0,
  createButtonRef,
}: {
  onEdit?: (id: string, name: string) => void;
  onCreate?: () => void;
  /** Open the run drawer: review, Run now, history and approvals. */
  onRuns?: (id: string, name: string) => void;
  onGraph?: (id: string, name: string) => void;
  onSettings?: (id: string, name: string) => void;
  onOpenConversation?: (id: string) => void;
  deliveryDefaults?: ReadonlyArray<{ id: string; label: string }>;
  deliveryOptions?: ReadonlyArray<{ id: string; label: string }>;
  onToggleEnabled?: (id: string, enabled: boolean) => void | Promise<void>;
  /** Copy a workflow (no schedule or trigger); resolves to the copy's name. */
  onDuplicate?: (id: string) => Promise<string>;
  /** ▶ reviews in the row (U40), then Run starts the reviewed request. */
  onReview?: (id: string) => Promise<TaskRunReview>;
  onRun?: (review: TaskRunReview) => Promise<void>;
  onDelete?: (id: string) => void | Promise<void>;
  onBulkDelete?: (ids: readonly string[]) => void | Promise<void>;
  onStop?: (id: string) => void | Promise<void>;
  onSetDeliveryDefaults?: (ids: readonly string[]) => void | Promise<void>;
  refreshToken?: number;
  createButtonRef?: Ref<HTMLButtonElement>;
  load: (
    query?: string,
    enabled?: boolean,
    cursor?: string,
    signal?: AbortSignal,
  ) => Promise<TaskSummaryPage>;
}) {
  const navigate = useNavigate();
  const overlay = useOverlay();
  const [draft, setDraft] = useState('');
  const [filter, setFilter] = useState<{ query: string; state: Filter }>({
    query: '',
    state: 'all',
  });
  const [page, setPage] = useState<TaskSummaryPage | null>(null);
  // Scheduled and Failed stop reading after CLIENT_FILTER_PAGES; the count
  // of workflows checked is shown when more remained unread.
  const [checkedOnly, setCheckedOnly] = useState(0);
  const [earlierRows, setEarlierRows] = useState(0);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [reload, setReload] = useState(0);
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [action, setAction] = useState('');
  const [actionError, setActionError] = useState('');
  const [reviewing, setReviewing] = useState<{
    id: string;
    name: string;
    review?: TaskRunReview;
  } | null>(null);
  const actionRef = useRef('');
  const epoch = useRef(0);
  const more = useRef<AbortController | null>(null);
  const clientFilter =
    filter.state === 'scheduled' || filter.state === 'failed';
  const enabled =
    filter.state === 'enabled' || filter.state === 'scheduled'
      ? true
      : undefined;
  // Search as you type, after a short pause; Enter searches at once.
  useEffect(() => {
    const query = draft.trim();
    if (query === filter.query) return;
    const timer = window.setTimeout(
      () => setFilter((value) => ({ ...value, query })),
      350,
    );
    return () => window.clearTimeout(timer);
  }, [draft, filter.query]);
  useEffect(() => {
    const abort = new AbortController();
    const ticket = ++epoch.current;
    more.current?.abort();
    more.current = null;
    setLoading(true);
    setLoadingMore(false);
    setError('');
    setPage(null);
    setCheckedOnly(0);
    setEarlierRows(0);
    setSelected(new Set());
    let checked = 0;
    const read = async () => {
      const first = await load(filter.query, enabled, undefined, abort.signal);
      if (!clientFilter) return first;
      const items = [...first.items];
      let cursor = first.next_cursor;
      for (let count = 1; cursor && count < CLIENT_FILTER_PAGES; count += 1) {
        const next = await load(filter.query, enabled, cursor, abort.signal);
        if (next.revision !== first.revision)
          throw clientError({ code: 'cursor_expired' });
        items.push(...next.items);
        cursor = next.next_cursor;
      }
      if (cursor) checked = items.length;
      const matching = items.filter((task) =>
        matchesFilter(task, filter.state),
      );
      return {
        ...first,
        items: matching,
        total: matching.length,
        next_cursor: null,
      };
    };
    read().then(
      (next) => {
        if (!abort.signal.aborted && ticket === epoch.current) {
          setPage(next);
          setCheckedOnly(checked);
          setLoading(false);
        }
      },
      (cause: unknown) => {
        if (!abort.signal.aborted && ticket === epoch.current) {
          setError(clientError(cause).message);
          setLoading(false);
        }
      },
    );
    return () => {
      abort.abort();
      more.current?.abort();
    };
  }, [
    load,
    filter.query,
    filter.state,
    enabled,
    clientFilter,
    reload,
    refreshToken,
  ]);
  async function invoke(
    key: string,
    callback: () => void | Promise<void>,
    success: string | (() => string),
  ) {
    if (actionRef.current) return false;
    actionRef.current = key;
    setAction(key);
    setActionError('');
    try {
      await callback();
      overlay.notify(typeof success === 'function' ? success() : success);
      setReload((value) => value + 1);
      return true;
    } catch (cause) {
      setActionError(clientError(cause).message);
      return false;
    } finally {
      actionRef.current = '';
      setAction('');
    }
  }
  function toggleSelected(id: string, checked: boolean) {
    setSelected((previous) => {
      const next = new Set(previous);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }
  function leaveSelectionMode() {
    setSelecting(false);
    setSelected(new Set());
  }
  async function nextPage() {
    if (!page?.next_cursor || more.current) return;
    const ticket = epoch.current;
    const abort = new AbortController();
    more.current = abort;
    setLoadingMore(true);
    setError('');
    try {
      const next = await load(
        filter.query,
        enabled,
        page.next_cursor,
        abort.signal,
      );
      if (abort.signal.aborted || ticket !== epoch.current) return;
      if (next.revision !== page.revision) {
        setError('Saved workflows changed. Refresh the list to continue.');
        return;
      }
      const items = [...page.items, ...next.items];
      const visibleItems = items.slice(-200);
      setEarlierRows((value) => value + Math.max(0, items.length - 200));
      setPage({ ...next, items: visibleItems });
      setSelected((previous) => {
        const visible = new Set(visibleItems.map((item) => item.id));
        return new Set([...previous].filter((id) => visible.has(id)));
      });
    } catch (cause) {
      if (!abort.signal.aborted && ticket === epoch.current)
        setError(clientError(cause).message);
    } finally {
      if (more.current === abort) more.current = null;
      if (!abort.signal.aborted && ticket === epoch.current)
        setLoadingMore(false);
    }
  }
  const filtered = Boolean(filter.query) || filter.state !== 'all';
  const caption = page
    ? `${page.total.toLocaleString()} ${filtered ? 'matching' : page.total === 1 ? 'workflow' : 'workflows'}${checkedOnly ? ` in the first ${checkedOnly.toLocaleString()}` : ''}`
    : '';
  const headerMenu: MenuAction[] = [
    ...(onBulkDelete && page?.items.length
      ? [
          {
            label: selecting ? 'Done selecting' : 'Select workflows',
            onSelect: () =>
              selecting ? leaveSelectionMode() : setSelecting(true),
          },
        ]
      : []),
  ];
  return (
    <section
      className="route-surface workflow-library home-page"
      aria-label="Workflows"
      aria-busy={loading}
    >
      <header className="home-page-header workflow-header">
        <h1>Workflows</h1>
        <form
          className="home-search"
          role="search"
          aria-label="Filter workflows"
          onSubmit={(event) => {
            event.preventDefault();
            setFilter((value) => ({ ...value, query: draft.trim() }));
          }}
        >
          <Search className="home-search-icon" size={14} aria-hidden />
          <Input
            type="search"
            aria-label="Search workflows"
            placeholder="Search workflows"
            maxLength={256}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
          <IconButton
            size="sm"
            type="submit"
            label="Search workflows"
            tooltip={false}
            className="home-search-submit"
          >
            <CornerDownLeft size={13} aria-hidden />
          </IconButton>
        </form>
        <Segmented
          label="Show workflows"
          size="sm"
          value={filter.state}
          onChange={(state) => setFilter((value) => ({ ...value, state }))}
          options={FILTERS}
        />
        {page && (
          <p className="home-caption" role="status">
            {caption}
          </p>
        )}
        <span className="home-page-header-spacer" />
        <IconButton
          size="sm"
          label="Refresh workflows"
          disabled={loading}
          onClick={() => setReload((value) => value + 1)}
        >
          <RefreshCw size={15} aria-hidden />
        </IconButton>
        {onSetDeliveryDefaults && (
          <DeliveryDefaults
            defaults={deliveryDefaults}
            options={deliveryOptions}
            busy={Boolean(action)}
            onSave={(ids) =>
              invoke(
                'delivery:save',
                () => onSetDeliveryDefaults(ids),
                'Workflow default delivery saved.',
              )
            }
          />
        )}
        {headerMenu.length > 0 && (
          <Menu
            label="More workflow actions"
            iconOnly
            variant="ghost"
            className="icon-action icon-action-sm"
            actions={headerMenu}
          >
            <MoreHorizontal size={16} aria-hidden />
          </Menu>
        )}
        {onCreate && (
          <Button
            ref={createButtonRef}
            variant="primary"
            className="small workflow-new"
            onClick={onCreate}
          >
            New workflow
          </Button>
        )}
      </header>
      {loading && <Skeleton label="Loading saved workflows" />}
      {error && (
        <ErrorState title="Workflow list unavailable">{error}</ErrorState>
      )}
      {actionError && (
        <ErrorState title="Workflow action failed">{actionError}</ErrorState>
      )}
      {page && (
        <>
          {selecting && (
            <div
              className="workflow-bulk-actions"
              role="group"
              aria-label="Selected workflow actions"
            >
              <span role="status">
                {selected.size} {selected.size === 1 ? 'workflow' : 'workflows'}{' '}
                selected
              </span>
              <Button
                variant="ghost"
                className="small"
                disabled={selected.size === 0 || Boolean(action)}
                onClick={() => setSelected(new Set())}
              >
                Clear
              </Button>
              {onBulkDelete && (
                <Button
                  variant="danger"
                  className="small"
                  disabled={selected.size === 0 || Boolean(action)}
                  onClick={(event) => {
                    const ids = [...selected].slice(0, 200);
                    const count = ids.length;
                    overlay.open({
                      kind: 'alert',
                      title: `Delete ${count} ${count === 1 ? 'workflow' : 'workflows'}?`,
                      description:
                        'This cannot be undone. Schedules and live state will be removed. Completed run records remain in audit history.',
                      confirmLabel: `Delete ${count} ${count === 1 ? 'workflow' : 'workflows'}`,
                      returnFocusTo: event.currentTarget,
                      onConfirm: () => {
                        void invoke(
                          'bulk-delete',
                          () => onBulkDelete(ids),
                          `${count} ${count === 1 ? 'workflow' : 'workflows'} deleted.`,
                        ).then((deleted) => {
                          if (deleted) leaveSelectionMode();
                        });
                      },
                    });
                  }}
                >
                  <Trash2 size={15} aria-hidden /> Delete selected
                </Button>
              )}
              <Button
                variant="ghost"
                className="small"
                onClick={leaveSelectionMode}
              >
                Done
              </Button>
            </div>
          )}
          {earlierRows > 0 && (
            <p className="home-caption" role="status">
              Showing entries {earlierRows + 1}–
              {earlierRows + page.items.length}. Refresh to return to the
              beginning.
            </p>
          )}
          {!page.items.length && (
            <EmptyState
              title={filtered ? 'No matching workflows' : 'No workflows yet'}
            >
              {filtered
                ? 'Try another search or filter.'
                : 'Create a workflow to run prompts on a schedule or on demand.'}
            </EmptyState>
          )}
          {page.items.length > 0 && (
            <ul className="workflow-grid workflow-rows">
              {page.items.map((task) => {
                const running = isRunning(task);
                const run = task.active_run;
                const next = parseTimestamp(task.next_run);
                const schedule = scheduleWords(task.schedule, task.at);
                const lastRun = task.last_run ? (
                  <>
                    Ran <When value={task.last_run} />
                  </>
                ) : (
                  'Never run'
                );
                const actions: MenuAction[] = [
                  ...(onRuns
                    ? [
                        {
                          label: 'Run history',
                          onSelect: () => onRuns(task.id, task.name),
                        },
                      ]
                    : []),
                  ...(onGraph
                    ? [
                        {
                          label: 'Edit workflow steps',
                          onSelect: () => onGraph(task.id, task.name),
                        },
                      ]
                    : []),
                  ...(onSettings
                    ? [
                        {
                          label: 'Workflow settings',
                          onSelect: () => onSettings(task.id, task.name),
                        },
                      ]
                    : []),
                  ...(task.conversation_id
                    ? [
                        {
                          label: 'Open conversation',
                          onSelect: () =>
                            onOpenConversation
                              ? onOpenConversation(task.conversation_id!)
                              : navigate(
                                  `/conversations/${encodeURIComponent(task.conversation_id!)}`,
                                ),
                        },
                      ]
                    : []),
                  ...(onDuplicate
                    ? [
                        {
                          label: 'Duplicate workflow',
                          disabled: Boolean(action),
                          onSelect: () => {
                            let copy = '';
                            void invoke(
                              `duplicate:${task.id}`,
                              async () => {
                                copy = await onDuplicate(task.id);
                              },
                              () => `Duplicated as “${copy}”.`,
                            );
                          },
                        } satisfies MenuAction,
                      ]
                    : []),
                  ...(onDelete
                    ? [
                        {
                          label: 'Delete workflow',
                          danger: true,
                          disabled: Boolean(action),
                          onSelect: (opener) =>
                            overlay.open({
                              kind: 'alert',
                              returnFocusTo: opener,
                              title: `Delete '${task.name}'?`,
                              description:
                                'This removes the workflow, its schedule and live state. Completed run records remain in audit history.',
                              confirmLabel: 'Delete workflow',
                              onConfirm: () =>
                                void invoke(
                                  `delete:${task.id}`,
                                  () => onDelete(task.id),
                                  `${task.name} deleted.`,
                                ),
                            }),
                        } satisfies MenuAction,
                      ]
                    : []),
                ];
                const failed = FAILED_RUN_STATUSES.has(
                  String(task.last_status ?? '').toLowerCase(),
                );
                return (
                  <li
                    className="workflow-row"
                    data-enabled={task.enabled ? 'true' : 'false'}
                    data-running={running ? 'true' : undefined}
                    data-failed={failed ? 'true' : undefined}
                    key={task.id}
                  >
                    {selecting && (
                      <label className="workflow-select-control">
                        <span className="sr-only">
                          Select workflow: {task.name}
                        </span>
                        <input
                          type="checkbox"
                          checked={selected.has(task.id)}
                          onChange={(event) =>
                            toggleSelected(task.id, event.target.checked)
                          }
                        />
                      </label>
                    )}
                    <span className="workflow-icon" aria-hidden>
                      <WorkflowGlyph task={task} />
                    </span>
                    <div className="workflow-row-main">
                      <span className="workflow-row-title">
                        <span className="workflow-row-name" title={task.name}>
                          {task.name}
                        </span>
                        {task.description && (
                          <span
                            className="workflow-row-description"
                            title={task.description}
                          >
                            {task.description}
                          </span>
                        )}
                      </span>
                      <p className="workflow-metadata">
                        <span>
                          {task.notify_only
                            ? 'Reminder'
                            : `${task.step_count} ${task.step_count === 1 ? 'step' : 'steps'}`}
                        </span>
                        <span aria-hidden>·</span>
                        <span>{lastRun}</span>
                        <span aria-hidden>·</span>
                        <span>
                          {schedule === 'Manual' ? 'Run manually' : schedule}
                        </span>
                      </p>
                    </div>
                    <span className="workflow-next">
                      {next && task.enabled ? (
                        <>
                          <span className="visually-hidden">Next run </span>
                          <When value={task.next_run} />
                        </>
                      ) : (
                        <span className="workflow-next-none">
                          {task.schedule || task.at
                            ? task.enabled
                              ? 'Not scheduled'
                              : 'Paused'
                            : ''}
                        </span>
                      )}
                    </span>
                    <span className="workflow-status">
                      {running ? (
                        <button
                          type="button"
                          className="workflow-progress"
                          onClick={() => onRuns?.(task.id, task.name)}
                        >
                          <span className="workflow-progress-dot" aria-hidden />
                          <span className="visually-hidden">
                            Open run of {task.name}:{' '}
                          </span>
                          {run && run.steps_total > 0
                            ? `Step ${Math.min(run.steps_done + 1, run.steps_total)}/${run.steps_total}`
                            : runStatus(run?.status ?? 'running').label}
                        </button>
                      ) : (
                        <RunSparkline
                          runs={task.recent_runs}
                          name={task.name}
                        />
                      )}
                    </span>
                    <Toggle
                      label={`${task.enabled ? 'Disable' : 'Enable'} workflow: ${task.name}`}
                      checked={task.enabled}
                      disabled={!onToggleEnabled || Boolean(action)}
                      onChange={(event) =>
                        void invoke(
                          `toggle:${task.id}`,
                          () => onToggleEnabled!(task.id, event.target.checked),
                          `${task.name} ${event.target.checked ? 'enabled' : 'disabled'}.`,
                        )
                      }
                    />
                    <div className="workflow-row-actions">
                      {running
                        ? onStop && (
                            <IconButton
                              size="sm"
                              label={`Stop running workflow: ${task.name}`}
                              variant="danger"
                              disabled={Boolean(action)}
                              onClick={() =>
                                void invoke(
                                  `stop:${task.id}`,
                                  () => onStop(task.id),
                                  `Stopping ${task.name}.`,
                                )
                              }
                            >
                              <Square size={14} aria-hidden />
                            </IconButton>
                          )
                        : (onRuns || onReview) && (
                            <IconButton
                              size="sm"
                              label={`Run workflow: ${task.name}`}
                              disabled={Boolean(action)}
                              onClick={() => {
                                if (!onReview || !onRun) {
                                  onRuns?.(task.id, task.name);
                                  return;
                                }
                                setReviewing({ id: task.id, name: task.name });
                                setActionError('');
                                onReview(task.id).then(
                                  (review) =>
                                    setReviewing((current) =>
                                      current?.id === task.id
                                        ? { ...current, review }
                                        : current,
                                    ),
                                  (cause: unknown) => {
                                    setReviewing(null);
                                    setActionError(clientError(cause).message);
                                  },
                                );
                              }}
                            >
                              <Play size={15} aria-hidden />
                            </IconButton>
                          )}
                      {onEdit && (
                        <IconButton
                          size="sm"
                          label={`Edit workflow: ${task.name}`}
                          onClick={() => onEdit(task.id, task.name)}
                        >
                          <Pencil size={14} aria-hidden />
                        </IconButton>
                      )}
                      {actions.length > 0 && (
                        <Menu
                          label={`More actions for ${task.name}`}
                          hint="More workflow actions"
                          iconOnly
                          variant="ghost"
                          className="icon-action icon-action-sm"
                          actions={actions}
                        >
                          <MoreHorizontal size={16} aria-hidden />
                        </Menu>
                      )}
                    </div>
                    {reviewing?.id === task.id && (
                      <div
                        role="group"
                        aria-label={`Run ${task.name} now?`}
                        className="workflow-run-review"
                      >
                        {reviewing.review ? (
                          <span>{reviewLine(reviewing.review)}</span>
                        ) : (
                          <span className="home-caption">Checking…</span>
                        )}
                        <Button
                          variant="primary"
                          className="small"
                          disabled={!reviewing.review || Boolean(action)}
                          onClick={() => {
                            const { review, id, name } = reviewing;
                            if (!review || !onRun) return;
                            void invoke(
                              `run:${id}`,
                              async () => {
                                await onRun(review);
                                setReviewing(null);
                                onRuns?.(id, name);
                              },
                              'Run started.',
                            );
                          }}
                        >
                          <Play size={14} aria-hidden /> Run
                        </Button>
                        <Button
                          variant="ghost"
                          className="small"
                          onClick={() => setReviewing(null)}
                        >
                          Cancel
                        </Button>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
          {page.next_cursor && (
            <Button
              className="workflow-more-rows"
              disabled={loadingMore}
              onClick={() => void nextPage()}
            >
              {loadingMore ? 'Loading more workflows…' : 'Load more workflows'}
            </Button>
          )}
        </>
      )}
    </section>
  );
}

// One line of what a run will use: steps, profile and approvals (U40).
function reviewLine(review: TaskRunReview) {
  const steps = review.notify_only
    ? 'Reminder'
    : `${review.steps_total} ${review.steps_total === 1 ? 'step' : 'steps'}`;
  const profile =
    humanizeToken(review.agent_profile_id.replace(/^builtin:/, '')) ||
    'Default profile';
  const approvals =
    review.approval_mode === 'approve'
      ? 'Asks before actions'
      : review.approval_mode === 'allow_all'
        ? 'Auto approvals'
        : 'Blocks actions';
  return `${steps} · ${profile} · ${approvals}`;
}

const noTaskSubscription = () => () => {};
const emptyTaskSessions = { selected: null, drafts: [], capacity: false };
const noTaskSnapshot = () => emptyTaskSessions;

function EditorFrame({
  title,
  label,
  onBack,
  children,
  headingRef,
}: {
  title: string;
  label: string;
  onBack: () => void;
  children: React.ReactNode;
  headingRef?: Ref<HTMLHeadingElement>;
}) {
  return (
    <section
      className="route-surface workflow-editor-view home-page"
      aria-label={label}
    >
      <header className="home-page-header">
        <IconButton size="sm" label="Back to workflows" onClick={onBack}>
          <ArrowLeft size={16} aria-hidden />
        </IconButton>
        <h1 ref={headingRef} tabIndex={-1}>
          {title}
        </h1>
      </header>
      {children}
    </section>
  );
}

export default function TaskLibrary() {
  const { controller, taskEditSessions } = useRuntime();
  const state = useClientState();
  const navigate = useNavigate();
  const overlay = useOverlay();
  const [search, setSearch] = useSearchParams();
  const [runsFor, setRunsFor] = useState<{ id: string; name: string } | null>(
    null,
  );
  const [reload, setReload] = useState(0);
  const [delivery, setDelivery] = useState<TaskDeliverySnapshot | null>(null);
  const [profileOptions, setProfileOptions] = useState<
    ReadonlyArray<{ id: string; label: string }>
  >([]);
  const execution = useMemo(() => taskRuns(controller), [controller]);
  const quickEdits = useMemo(() => taskEdits(controller), [controller]);
  const deleteOwner = useRef<TaskCommandOwner<void>>({ pending: null });
  const duplicateOwner = useRef<TaskCommandOwner<string>>({ pending: null });
  const deliveryOwner = useRef<TaskCommandOwner<void>>({ pending: null });
  const editorHeading = useRef<HTMLHeadingElement>(null);
  const createButton = useRef<HTMLButtonElement>(null);
  const deleteMutation = useMemo(
    () => taskMutation(controller, deleteOwner.current),
    [controller],
  );
  const deliveryMutation = useMemo(
    () => taskMutation(controller, deliveryOwner.current),
    [controller],
  );
  const duplicateMutation = useMemo(
    () => taskMutation(controller, duplicateOwner.current),
    [controller],
  );
  const sessions = useSyncExternalStore(
    taskEditSessions?.subscribe ?? noTaskSubscription,
    taskEditSessions?.getSnapshot ?? noTaskSnapshot,
  );
  // Overview links open one workflow's runs: /?tab=workflows&workflow=<id>.
  const requestedRuns = search.get('workflow');
  useEffect(() => {
    if (!requestedRuns) return;
    setRunsFor({ id: requestedRuns, name: '' });
    setSearch(
      (current) => {
        const next = new URLSearchParams(current);
        next.delete('workflow');
        return next;
      },
      { replace: true },
    );
    // The link carries only the id; name the drawer once the task is read.
    controller.taskEditor(requestedRuns).then(
      (task) =>
        setRunsFor((current) =>
          current?.id === requestedRuns && !current.name
            ? { id: requestedRuns, name: task.fields.name }
            : current,
        ),
      () => {},
    );
  }, [controller, requestedRuns, setSearch]);
  useEffect(() => {
    if (!state.handshake) return;
    const abort = new AbortController();
    controller.taskDeliveryDefaults(abort.signal).then(setDelivery, () => {
      if (!abort.signal.aborted) setDelivery(null);
    });
    return () => abort.abort();
  }, [controller, reload, state.handshake]);
  const selected = sessions.selected;
  const settingsOpen = selected?.session.kind === 'settings';
  // Workflow settings pick the agent profile from a list (U41).
  useEffect(() => {
    if (!settingsOpen || !state.handshake) return;
    const abort = new AbortController();
    controller.profiles('', undefined, undefined, abort.signal).then(
      (page) =>
        setProfileOptions(
          page.items
            .filter((profile) => profile.enabled)
            .map((profile) => ({
              id: profile.id,
              label: profile.display_name,
            })),
        ),
      () => {},
    );
    return () => abort.abort();
  }, [controller, settingsOpen, state.handshake]);
  const modelOptions = (state.handshake?.models ?? [])
    .filter((model) => model.available)
    .map((model) => ({ id: model.model_ref, label: model.label }));
  // The draft in the editor is not waiting to be continued. Its row stays
  // mounted (hidden) so closing the editor returns focus to it.
  const waitingDrafts = sessions.drafts.filter((draft) => !draft.open);
  const selectedTaskId =
    selected?.session.kind === 'task' ? selected.session.taskId : null;
  useEffect(() => {
    if (selectedTaskId === null) return;
    editorHeading.current?.scrollIntoView?.({ block: 'start' });
    editorHeading.current?.focus({ preventScroll: true });
  }, [selectedTaskId]);
  const close = () => taskEditSessions?.close();
  const saved = () => {
    taskEditSessions?.discard();
    taskEditSessions?.close();
    setReload((value) => value + 1);
  };
  const toggleEnabled = async (id: string, enabled: boolean) => {
    const task = await controller.taskEditor(id);
    await quickEdits.save(id, task.revision, { ...task.fields, enabled });
  };
  const deleteTask = async (id: string) => {
    const task = await controller.taskEditor(id);
    await deleteMutation(
      'task.delete',
      { task_id: id, task_revision: task.revision },
      id,
      async () => undefined,
    );
  };
  // A copy without schedule or trigger; the receipt names the new workflow.
  const duplicateTask = async (id: string) => {
    const task = await controller.taskEditor(id);
    return duplicateMutation(
      'task.duplicate',
      { task_id: id, task_revision: task.revision },
      undefined,
      async (copyId) => (await controller.taskEditor(copyId)).fields.name,
    );
  };
  const deleteTasks = async (ids: readonly string[]) => {
    for (const id of ids.slice(0, 200)) await deleteTask(id);
  };
  const saveDeliveryDefaults = async (ids: readonly string[]) => {
    if (!delivery) throw clientError({ code: 'task_metadata_unavailable' });
    await deliveryMutation(
      'task.delivery.update',
      { delivery_revision: delivery.revision, channels: [...ids] },
      'delivery-defaults',
      async () => {
        setDelivery(await controller.taskDeliveryDefaults());
      },
    );
  };
  const stopTask = async (id: string) => {
    const page = await controller.taskRuns(id);
    const active = page.items.find((item) =>
      [
        'running',
        'starting',
        'resuming',
        'paused',
        'waiting_approval',
      ].includes(item.status.toLowerCase()),
    );
    if (!active) throw clientError({ code: 'task_run_not_found' });
    await execution.stop(id, active.id);
  };
  const openConversation = (id: string) => {
    void controller.selectConversation(id);
    navigate(`/conversations/${encodeURIComponent(id)}`);
  };
  if (!state.handshake) return <Skeleton label="Connecting to workflows" />;
  if (!taskEditSessions)
    return (
      <EmptyState title="Workflow editing is unavailable">
        Reload Row-Bot to reopen workflow editing.
      </EmptyState>
    );
  if (selected?.session.kind === 'settings')
    return (
      <EditorFrame
        title="Workflow settings"
        label="Workflow configuration"
        onBack={close}
      >
        <TaskSettingsEditor
          key={selected.session.taskId}
          session={selected.session}
          taskId={selected.session.taskId}
          taskName={selected.label}
          load={controller.taskSettings}
          review={controller.reviewTaskSettings}
          save={selected.settings.save}
          rotate={selected.settings.rotate}
          download={selected.settings.download}
          onSaved={saved}
          onCancel={close}
          profileOptions={profileOptions}
          modelOptions={modelOptions}
        />
      </EditorFrame>
    );
  if (selected?.session.kind === 'graph')
    return (
      <EditorFrame
        title="Edit workflow steps"
        label="Workflow step editor"
        onBack={close}
      >
        <TaskGraphEditor
          key={selected.session.taskId}
          session={selected.session}
          taskId={selected.session.taskId}
          load={controller.taskGraph}
          save={selected.graph}
          onSaved={saved}
          onCancel={close}
          onBuilder={() =>
            taskEditSessions.open(
              'task',
              selected.session.taskId,
              selected.label,
            )
          }
          onTaskSettings={() =>
            taskEditSessions.open(
              'settings',
              selected.session.taskId,
              selected.label,
            )
          }
        />
      </EditorFrame>
    );
  if (selected?.session.kind === 'task' && selected.session.taskId)
    return (
      <EditorFrame
        title="Edit workflow"
        label="Workflow editor"
        onBack={close}
        headingRef={editorHeading}
      >
        <TaskEditor
          key={selected.session.taskId}
          session={selected.session}
          taskId={selected.session.taskId || undefined}
          load={controller.taskEditor}
          create={selected.edits.create}
          save={selected.edits.save}
          onSaved={saved}
          onCancel={close}
          deliveryChannels={delivery?.channels ?? []}
          onAdvancedSteps={() =>
            taskEditSessions.open(
              'graph',
              selected.session.taskId,
              selected.label,
            )
          }
          onTaskSettings={() =>
            taskEditSessions.open(
              'settings',
              selected.session.taskId,
              selected.label,
            )
          }
        />
      </EditorFrame>
    );
  return (
    <div className="workflow-home">
      <ModalTask
        open={selected?.session.kind === 'task' && !selected.session.taskId}
        title="New task/workflow"
        description="Name it, add steps, and choose when it runs. Saving never starts a run."
        ariaLabel="New task/workflow"
        className="workflow-builder-dialog"
        dismissible={!selected?.session.getMeta().busy}
        fallbackFocusTo={createButton.current}
        onOpenChange={(open) => {
          if (!open) close();
        }}
      >
        {selected?.session.kind === 'task' && !selected.session.taskId && (
          <TaskEditor
            session={selected.session}
            load={controller.taskEditor}
            create={selected.edits.create}
            save={selected.edits.save}
            onSaved={saved}
            onCancel={close}
            deliveryChannels={delivery?.channels ?? []}
          />
        )}
      </ModalTask>
      {sessions.capacity && (
        <p role="alert" className="workflow-capacity">
          Eight workflows already have unsaved or unresolved changes. Continue
          one of them before opening another.
        </p>
      )}
      {sessions.drafts.length > 0 && (
        <section
          aria-label="Continue editing workflows"
          className="workflow-recovery"
          hidden={waitingDrafts.length === 0}
        >
          <h2>Continue editing</h2>
          <ul className="workflow-recovery-list">
            {sessions.drafts.map((draft) => {
              const area =
                draft.kind === 'graph'
                  ? 'Steps'
                  : draft.kind === 'settings'
                    ? 'Settings'
                    : 'Details';
              const stateLabel = draft.uncertain
                ? 'save outcome needs review'
                : draft.busy
                  ? 'saving'
                  : draft.dirty
                    ? 'unsaved changes'
                    : 'waiting for the save to be confirmed';
              return (
                <li
                  className="workflow-recovery-row"
                  key={draft.key}
                  hidden={draft.open}
                >
                  <Pencil size={14} aria-hidden />
                  <div>
                    <strong>{draft.label}</strong>
                    <small>
                      {area} · {stateLabel}
                    </small>
                  </div>
                  <Button
                    className="small"
                    onClick={() =>
                      taskEditSessions.open(
                        draft.kind,
                        draft.taskId,
                        draft.label,
                      )
                    }
                  >
                    Continue editing
                  </Button>
                  <Button
                    variant="ghost"
                    className="small"
                    disabled={!draft.canDiscard}
                    onClick={(event) =>
                      overlay.open({
                        kind: 'alert',
                        title: `Discard changes to ${draft.label}?`,
                        description:
                          'These unsaved workflow changes will be removed from this device. The last saved workflow is not deleted.',
                        confirmLabel: 'Discard changes',
                        returnFocusTo: event.currentTarget,
                        onConfirm: () => taskEditSessions.discard(draft.key),
                      })
                    }
                  >
                    Discard changes
                  </Button>
                </li>
              );
            })}
          </ul>
          <p className="home-caption">
            Unsaved changes stay on this device while Row-Bot is open. A save
            whose outcome is unknown must be checked before it can be discarded.
          </p>
        </section>
      )}
      <SavedTasks
        key={state.handshake?.client_session_id ?? ''}
        refreshToken={reload}
        createButtonRef={createButton}
        load={controller.savedTasks}
        onCreate={() => taskEditSessions.open('task')}
        onEdit={(id, name) => taskEditSessions.open('task', id, name)}
        onRuns={(id, name) => setRunsFor({ id, name })}
        onOpenConversation={openConversation}
        onToggleEnabled={toggleEnabled}
        onDuplicate={duplicateTask}
        onReview={controller.taskRunReview}
        onRun={async (review) => {
          await execution.run(review);
        }}
        onDelete={deleteTask}
        onBulkDelete={deleteTasks}
        onStop={stopTask}
        deliveryDefaults={
          delivery?.channels.filter((channel) => channel.selected) ?? []
        }
        deliveryOptions={delivery?.channels ?? []}
        onSetDeliveryDefaults={saveDeliveryDefaults}
        onGraph={(id, name) => taskEditSessions.open('graph', id, name)}
        onSettings={(id, name) => taskEditSessions.open('settings', id, name)}
      />
      <Drawer
        open={runsFor !== null}
        onOpenChange={(open) => {
          if (!open) {
            setRunsFor(null);
            setReload((value) => value + 1);
          }
        }}
        title={runsFor?.name || 'Workflow runs'}
        description="Run it, follow its progress and answer approvals"
        closeLabel="Close workflow runs"
        className="workflow-run-drawer"
      >
        {runsFor && (
          <TaskRun
            key={runsFor.id}
            taskId={runsFor.id}
            loadReview={controller.taskRunReview}
            loadHistory={controller.taskRuns}
            loadApprovals={controller.taskApprovals}
            loadRun={controller.taskRun}
            run={execution.run}
            stop={execution.stop}
            respondApproval={execution.respondApproval}
            openConversation={(id) => {
              setRunsFor(null);
              openConversation(id);
            }}
          />
        )}
      </Drawer>
    </div>
  );
}
