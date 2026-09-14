import {
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import { Link, useNavigate } from 'react-router-dom';
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
  EmptyState,
  ErrorState,
  Field,
  Input,
  Select,
  Skeleton,
} from '../../ui/primitives';

export function SavedTasks({
  load,
  onEdit,
  onCreate,
  onRuns,
  onGraph,
  onSettings,
}: {
  onEdit?: (id: string) => void;
  onCreate?: () => void;
  onRuns?: (id: string) => void;
  onGraph?: (id: string) => void;
  onSettings?: (id: string) => void;
  load: (
    query?: string,
    enabled?: boolean,
    cursor?: string,
    signal?: AbortSignal,
  ) => Promise<TaskSummaryPage>;
}) {
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
      className="route-surface stack capability-page"
      aria-label="Workflows"
      aria-busy={loading}
    >
      <header className="capability-header">
        <div>
          <p className="eyebrow">Automation library</p>
          <h1>Workflows</h1>
          <p>Browse saved tasks, reminders and schedules.</p>
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
        className="field-row capability-section"
        aria-label="Filter workflows"
        onSubmit={(event) => {
          event.preventDefault();
          setFilter((value) => ({ ...value, query: draft.trim() }));
        }}
      >
        <Field label="Search tasks">
          <Input
            type="search"
            maxLength={256}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
        </Field>
        <Field label="Task status">
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
        <Button type="submit">Search</Button>
      </form>
      <div className="action-cluster">
        <Button
          disabled={loading}
          onClick={() => setReload((value) => value + 1)}
        >
          Reload saved tasks
        </Button>
      </div>
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
          <ul className="settings-results">
            {page.items.map((task) => (
              <li className="surface" key={task.id}>
                <details className="stack">
                  <summary>{task.name}</summary>
                  {task.description && <p>{task.description}</p>}
                  <div className="action-cluster">
                    {onEdit && (
                      <Button onClick={() => onEdit(task.id)}>
                        Edit workflow
                      </Button>
                    )}
                    {onRuns && (
                      <Button onClick={() => onRuns(task.id)}>
                        Run and history
                      </Button>
                    )}
                    {onGraph && (
                      <Button onClick={() => onGraph(task.id)}>
                        Edit workflow steps
                      </Button>
                    )}
                    {onSettings && (
                      <Button onClick={() => onSettings(task.id)}>
                        Workflow settings
                      </Button>
                    )}
                  </div>
                  <dl className="capability-summary">
                    <dt>Type</dt>
                    <dd>{task.notify_only ? 'Reminder' : 'Task'}</dd>
                    <dt>Enabled</dt>
                    <dd>{task.enabled ? 'Yes' : 'No'}</dd>
                    <dt>Schedule</dt>
                    <dd>{task.schedule || 'No recurring schedule'}</dd>
                    <dt>One-time schedule</dt>
                    <dd>{task.at || 'None'}</dd>
                    <dt>Last run</dt>
                    <dd>{task.last_run || 'No recorded run time'}</dd>
                    <dt>Last recorded status</dt>
                    <dd>{task.last_status || 'No recorded status'}</dd>
                  </dl>
                  {task.conversation_id && (
                    <div className="action-cluster">
                      <Link
                        className="button"
                        to={`/conversations/${encodeURIComponent(task.conversation_id)}`}
                      >
                        Open conversation
                      </Link>
                    </div>
                  )}
                </details>
              </li>
            ))}
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
        Reload the application to restore the workflow session.
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
          Eight workflow drafts are retained. Resume or discard a resolved draft
          before opening another.
        </p>
      )}
      {sessions.drafts.length > 0 && (
        <section
          aria-label="Retained workflow drafts"
          className="route-surface stack"
        >
          <h2>Retained workflow drafts</h2>
          <p>
            Closing an editor keeps its draft and original pending action in
            this application session.
          </p>
          {sessions.drafts.map((draft) => (
            <div className="actions" key={draft.key}>
              <Button
                onClick={() => taskEditSessions.open(draft.kind, draft.taskId)}
              >
                Resume{' '}
                {draft.kind === 'task'
                  ? 'workflow'
                  : draft.kind === 'graph'
                    ? 'workflow steps'
                    : 'workflow settings'}
                : {draft.taskId || 'New workflow'}
              </Button>
              <Button onClick={() => taskEditSessions.discard(draft.key)}>
                Discard resolved draft: {draft.taskId || 'New workflow'} (
                {draft.kind})
              </Button>
            </div>
          ))}
        </section>
      )}
      <SavedTasks
        key={`${state.handshake?.client_session_id ?? ''}:${reload}`}
        load={controller.savedTasks}
        onCreate={() => taskEditSessions.open('task')}
        onEdit={(id) => taskEditSessions.open('task', id)}
        onRuns={setRunning}
        onGraph={(id) => taskEditSessions.open('graph', id)}
        onSettings={(id) => taskEditSessions.open('settings', id)}
      />
    </div>
  );
}
