import {
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Bell,
  Braces,
  CalendarClock,
  Database,
  FileDown,
  FileUp,
  GitBranch,
  History,
  Mail,
  Pencil,
  Play,
  RefreshCw,
  Search,
  Square,
  Zap,
} from 'lucide-react';
import type { TaskSummaryPage } from '../../api/types';
import { clientError } from '../../api/errors';
import { useClientState, useRuntime } from '../../runtime';
import TaskEditor from './TaskEditor';
import TaskRun from './TaskRun';
import { taskRuns } from './task-runs';
import TaskGraphEditor from './TaskGraphEditor';
import TaskSettingsEditor from './TaskSettingsEditor';
import {
  Button,
  CompactAction,
  EmptyState,
  ErrorState,
  Field,
  Input,
  Menu,
  Select,
  Skeleton,
} from '../../ui/primitives';
import { useOverlay } from '../../ui/overlays';

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

export function SavedTasks({
  load,
  onEdit,
  onCreate,
  onRuns,
  onGraph,
  onSettings,
}: {
  onEdit?: (id: string, name: string) => void;
  onCreate?: () => void;
  onRuns?: (id: string) => void;
  onGraph?: (id: string, name: string) => void;
  onSettings?: (id: string, name: string) => void;
  load: (
    query?: string,
    enabled?: boolean,
    cursor?: string,
    signal?: AbortSignal,
  ) => Promise<TaskSummaryPage>;
}) {
  const navigate = useNavigate();
  const [draft, setDraft] = useState('');
  const [filter, setFilter] = useState({ query: '', state: 'all' });
  const [page, setPage] = useState<TaskSummaryPage | null>(null);
  const [earlierRows, setEarlierRows] = useState(0);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [reload, setReload] = useState(0);
  const epoch = useRef(0);
  const more = useRef<AbortController | null>(null);
  const enabled =
    filter.state === 'all' ? undefined : filter.state === 'enabled';
  useEffect(() => {
    const abort = new AbortController();
    const ticket = ++epoch.current;
    more.current?.abort();
    more.current = null;
    setLoading(true);
    setLoadingMore(false);
    setError('');
    setPage(null);
    setEarlierRows(0);
    load(filter.query, enabled, undefined, abort.signal).then(
      (next) => {
        if (!abort.signal.aborted && ticket === epoch.current) {
          setPage(next);
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
  }, [load, filter.query, enabled, reload]);
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
        setError('Saved tasks changed. Reload the list to continue.');
        return;
      }
      const items = [...page.items, ...next.items];
      setEarlierRows((value) => value + Math.max(0, items.length - 200));
      setPage({ ...next, items: items.slice(-200) });
    } catch (cause) {
      if (!abort.signal.aborted && ticket === epoch.current)
        setError(clientError(cause).message);
    } finally {
      if (more.current === abort) more.current = null;
      if (!abort.signal.aborted && ticket === epoch.current)
        setLoadingMore(false);
    }
  }
  return (
    <section
      className="route-surface stack capability-page workflow-library"
      aria-label="Workflows"
      aria-busy={loading}
    >
      <header className="capability-header">
        <div className="workflow-heading">
          <span className="workflow-heading-icon" aria-hidden>
            <GitBranch size={22} />
          </span>
          <div>
            <p className="eyebrow">Automation library</p>
            <h1>Workflows</h1>
            <p>Browse saved tasks, reminders and schedules.</p>
          </div>
        </div>
        {onCreate && (
          <div className="action-cluster">
            <Button variant="primary" onClick={onCreate}>
              New workflow
            </Button>
          </div>
        )}
      </header>
      <form
        className="workflow-toolbar panel-toolbar"
        aria-label="Filter workflows"
        onSubmit={(event) => {
          event.preventDefault();
          setFilter((value) => ({ ...value, query: draft.trim() }));
        }}
      >
        <Field label="Search workflows">
          <Input
            type="search"
            maxLength={256}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
        </Field>
        <Field label="Workflow status">
          <Select
            value={filter.state}
            onChange={(event) =>
              setFilter((value) => ({ ...value, state: event.target.value }))
            }
          >
            <option value="all">All saved tasks</option>
            <option value="enabled">Enabled</option>
            <option value="disabled">Disabled</option>
          </Select>
        </Field>
        <CompactAction label="Search workflows" type="submit">
          <Search size={17} aria-hidden />
        </CompactAction>
        <CompactAction
          label="Refresh workflows"
          disabled={loading}
          onClick={() => setReload((value) => value + 1)}
        >
          <RefreshCw size={17} aria-hidden />
        </CompactAction>
      </form>
      {loading && <Skeleton label="Loading saved tasks" />}
      {error && <ErrorState title="Task list unavailable">{error}</ErrorState>}
      {page && (
        <>
          <p role="status">{page.total} matching tasks</p>
          {earlierRows > 0 && (
            <p role="status">
              Showing entries {earlierRows + 1}–
              {earlierRows + page.items.length}. Reload saved tasks to return to
              the beginning.
            </p>
          )}
          {!page.items.length && (
            <EmptyState title="No matching saved tasks">
              Try another search or status.
            </EmptyState>
          )}
          <ul className="workflow-grid">
            {page.items.map((task) => {
              const Icon = workflowIcon(task.icon, task.notify_only);
              const active = task.last_status?.toLowerCase() === 'running';
              const schedule = task.schedule
                ? task.schedule
                : task.at
                  ? `Once · ${task.at}`
                  : 'Run manually';
              const actions = [
                ...(onRuns
                  ? [
                      {
                        label: 'Run history',
                        onSelect: () => onRuns(task.id),
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
                          navigate(
                            `/conversations/${encodeURIComponent(task.conversation_id!)}`,
                          ),
                      },
                    ]
                  : []),
              ];
              return (
                <li className="workflow-card" key={task.id}>
                  <header className="workflow-card-header">
                    <span className="workflow-icon" aria-hidden>
                      <Icon size={22} />
                    </span>
                    <div className="workflow-card-title">
                      <h2 title={task.name}>{task.name}</h2>
                      <div className="workflow-chips">
                        <span
                          className={`status-chip ${task.enabled ? 'success' : ''}`}
                        >
                          {task.enabled ? 'Enabled' : 'Disabled'}
                        </span>
                        <span className="status-chip">
                          {task.notify_only ? 'Reminder' : 'Workflow'}
                        </span>
                      </div>
                    </div>
                  </header>
                  <p className="workflow-description">
                    {task.description || 'No description.'}
                  </p>
                  <div className="workflow-metadata">
                    <p title={schedule}>
                      <CalendarClock size={15} aria-hidden />
                      <span>{schedule}</span>
                    </p>
                    <p>
                      <History size={15} aria-hidden />
                      <span>{task.last_run || 'Never run'}</span>
                    </p>
                  </div>
                  <footer className="workflow-card-footer">
                    <span
                      className={`workflow-last-status ${active ? 'active' : ''}`}
                    >
                      {task.last_status || 'No recorded status'}
                    </span>
                    <div className="workflow-card-actions">
                      {onRuns && (
                        <CompactAction
                          label={
                            active
                              ? `Open running workflow: ${task.name}`
                              : `Run workflow: ${task.name}`
                          }
                          onClick={() => onRuns(task.id)}
                        >
                          {active ? (
                            <Square size={16} aria-hidden />
                          ) : (
                            <Play size={17} aria-hidden />
                          )}
                        </CompactAction>
                      )}
                      {onEdit && (
                        <CompactAction
                          label={`Edit workflow: ${task.name}`}
                          onClick={() => onEdit(task.id, task.name)}
                        >
                          <Pencil size={16} aria-hidden />
                        </CompactAction>
                      )}
                      {actions.length > 0 && (
                        <Menu
                          label={`More actions for ${task.name}`}
                          hint="More workflow actions"
                          iconOnly
                          variant="ghost"
                          actions={actions}
                        >
                          <span className="workflow-more" aria-hidden>
                            •••
                          </span>
                        </Menu>
                      )}
                    </div>
                  </footer>
                </li>
              );
            })}
          </ul>
          {page.next_cursor && (
            <Button disabled={loadingMore} onClick={() => void nextPage()}>
              {loadingMore ? 'Loading more tasks…' : 'Load more tasks'}
            </Button>
          )}
        </>
      )}
    </section>
  );
}

const noTaskSubscription = () => () => {};
const emptyTaskSessions = { selected: null, drafts: [], capacity: false };
const noTaskSnapshot = () => emptyTaskSessions;

export default function TaskLibrary() {
  const { controller, taskEditSessions } = useRuntime();
  const state = useClientState();
  const navigate = useNavigate();
  const overlay = useOverlay();
  const [running, setRunning] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const execution = useMemo(() => taskRuns(controller), [controller]);
  const sessions = useSyncExternalStore(
    taskEditSessions?.subscribe ?? noTaskSubscription,
    taskEditSessions?.getSnapshot ?? noTaskSnapshot,
  );
  const selected = sessions.selected;
  const close = () => taskEditSessions?.close();
  const saved = () => {
    taskEditSessions?.discard();
    taskEditSessions?.close();
    setReload((value) => value + 1);
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
      <section
        className="route-surface stack"
        aria-label="Workflow configuration"
      >
        <h1>Workflow settings</h1>
        <TaskSettingsEditor
          key={selected.session.taskId}
          session={selected.session}
          taskId={selected.session.taskId}
          load={controller.taskSettings}
          review={controller.reviewTaskSettings}
          save={selected.settings.save}
          rotate={selected.settings.rotate}
          download={selected.settings.download}
          onSaved={saved}
          onCancel={close}
        />
      </section>
    );
  if (selected?.session.kind === 'graph')
    return (
      <section
        className="route-surface stack"
        aria-label="Workflow step editor"
      >
        <h1>Edit workflow steps</h1>
        <TaskGraphEditor
          key={selected.session.taskId}
          session={selected.session}
          taskId={selected.session.taskId}
          load={controller.taskGraph}
          save={selected.graph}
          onSaved={saved}
          onCancel={close}
        />
      </section>
    );
  if (selected?.session.kind === 'task')
    return (
      <section className="route-surface stack" aria-label="Workflow editor">
        <h1>{selected.session.taskId ? 'Edit workflow' : 'New workflow'}</h1>
        <TaskEditor
          key={selected.session.taskId}
          session={selected.session}
          taskId={selected.session.taskId || undefined}
          load={controller.taskEditor}
          create={selected.edits.create}
          save={selected.edits.save}
          onSaved={saved}
          onCancel={close}
        />
      </section>
    );
  if (running !== null)
    return (
      <section className="route-surface stack" aria-label="Workflow runs">
        <h1>Workflow runs</h1>
        <Button onClick={() => setRunning(null)}>Back to workflows</Button>
        <TaskRun
          taskId={running}
          loadReview={controller.taskRunReview}
          loadHistory={controller.taskRuns}
          loadApprovals={controller.taskApprovals}
          run={execution.run}
          stop={execution.stop}
          respondApproval={execution.respondApproval}
          openConversation={(id) =>
            navigate(`/conversations/${encodeURIComponent(id)}`)
          }
        />
      </section>
    );
  return (
    <div className="stack">
      {sessions.capacity && (
        <p role="alert">
          Eight workflows already have unsaved or unresolved changes. Continue
          one of them before opening another.
        </p>
      )}
      {sessions.drafts.length > 0 && (
        <section
          aria-label="Continue editing workflows"
          className="workflow-recovery stack"
        >
          <h2>Continue editing</h2>
          <p>
            Unsaved workflow changes stay on this device while Row-Bot remains
            open. A save whose outcome is not yet known must be checked before
            it can be discarded.
          </p>
          <ul className="workflow-recovery-list">
            {sessions.drafts.map((draft) => {
              const area =
                draft.kind === 'graph'
                  ? 'steps'
                  : draft.kind === 'settings'
                    ? 'settings'
                    : 'details';
              const stateLabel = draft.uncertain
                ? 'Save outcome needs review'
                : draft.busy
                  ? 'Save in progress'
                  : draft.dirty
                    ? 'Unsaved changes'
                    : 'Pending save receipt';
              return (
                <li className="workflow-recovery-row" key={draft.key}>
                  <div>
                    <strong>{draft.label}</strong>
                    <small>
                      {area} · {stateLabel}
                    </small>
                  </div>
                  <div className="action-cluster">
                    <Button
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
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      )}
      <SavedTasks
        key={`${state.handshake?.client_session_id ?? ''}:${reload}`}
        load={controller.savedTasks}
        onCreate={() => taskEditSessions.open('task')}
        onEdit={(id, name) => taskEditSessions.open('task', id, name)}
        onRuns={setRunning}
        onGraph={(id, name) => taskEditSessions.open('graph', id, name)}
        onSettings={(id, name) => taskEditSessions.open('settings', id, name)}
      />
    </div>
  );
}
