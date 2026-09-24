import { useEffect, useRef, useSyncExternalStore } from 'react';
import {
  Button,
  ErrorState,
  Field,
  Input,
  Skeleton,
} from '../../ui/primitives';

// Structural domain DTOs; shared generated aliases are integrated by the owner.
export type WorkspaceProcessInfo = {
  process_id: string;
  command_id: string;
  run_id: string;
  command: string;
  state:
    | 'starting'
    | 'running'
    | 'stopping'
    | 'exited'
    | 'failed'
    | 'cleanup_incomplete';
  exit_code: number | null;
  quiesced: boolean;
  code?: string;
};
export type WorkspaceProcessScope = {
  resource_id: string;
  conversation_id: string;
  binding_id: string;
  binding_revision: string;
};
export type WorkspaceProcessSnapshot = WorkspaceProcessScope & {
  resource_revision: string;
  processes: WorkspaceProcessInfo[];
  schema_version?: 1;
};
export type WorkspaceProcessOutput = {
  process_id: string;
  entries: { sequence: number; channel: 'stdout' | 'stderr'; text: string }[];
  next_cursor: number;
  truncated: boolean;
  quiesced: boolean;
  schema_version?: 1;
};
export type WorkspaceProcessRecoveryPage = {
  items: WorkspaceProcessInfo[];
  next_cursor: string | null;
};
export type WorkspaceProcessReview = WorkspaceProcessScope & {
  resource_revision: string;
  command_id: string;
  command: string;
  decision: 'approved' | 'pending' | 'denied';
  approval_id: string | null;
};
export type WorkspaceProcessAttempt = {
  command_id: string;
  command: string;
  snapshot: WorkspaceProcessSnapshot;
  review: WorkspaceProcessReview | null;
  started: boolean;
  uncertain: boolean;
};
type SessionState = {
  draft: string;
  snapshot: WorkspaceProcessSnapshot | null;
  attempt: WorkspaceProcessAttempt | null;
  activity: 'review' | 'start' | null;
  processes: WorkspaceProcessInfo[];
  selected: string;
  output: WorkspaceProcessOutput | null;
  cursor: number;
  previous: number[];
  controls: ReadonlySet<string>;
  error: string;
  notice: string;
  revoked: boolean;
  recovery: WorkspaceProcessRecoveryPage | null;
  recoveryCursor: string | undefined;
  recoveryLoading: boolean;
};

function sameScope(a: WorkspaceProcessScope, b: WorkspaceProcessScope) {
  return (
    a.resource_id === b.resource_id &&
    a.conversation_id === b.conversation_id &&
    a.binding_id === b.binding_id &&
    a.binding_revision === b.binding_revision
  );
}

/** Retain with the controller's exact binding session; never allocate per render.
 * No storage, command replay, polling or eviction occurs inside this session.
 */
export function createWorkspaceProcessesSession(scope: WorkspaceProcessScope) {
  let disposed = false;
  let state: SessionState = {
    draft: '',
    snapshot: null,
    attempt: null,
    activity: null,
    processes: [],
    selected: '',
    output: null,
    cursor: 0,
    previous: [],
    controls: new Set(),
    error: '',
    notice: '',
    revoked: false,
    recovery: null,
    recoveryCursor: undefined,
    recoveryLoading: false,
  };
  const listeners = new Set<() => void>();
  const session = {
    scope: Object.freeze({ ...scope }),
    getSnapshot: () => state,
    subscribe: (listener: () => void) => {
      if (disposed) return () => {};
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    update: (change: (current: SessionState) => SessionState) => {
      if (disposed) return;
      state = change(state);
      listeners.forEach((listener) => listener());
    },
    revoke: () => {
      if (state.revoked || disposed) return;
      session.update((current) => ({
        ...current,
        revoked: true,
        error:
          'Workspace access changed. The original command and recovery identity are retained.',
      }));
    },
    dispose: () => {
      if (disposed) return;
      disposed = true;
      state = {
        ...state,
        draft: '',
        snapshot: null,
        attempt: null,
        activity: null,
        processes: [],
        selected: '',
        output: null,
        cursor: 0,
        previous: [],
        controls: new Set(),
        error: '',
        notice: '',
        revoked: true,
        recovery: null,
        recoveryCursor: undefined,
        recoveryLoading: false,
      };
      listeners.forEach((listener) => listener());
      listeners.clear();
    },
  };
  return session;
}
export type WorkspaceProcessesSession = ReturnType<
  typeof createWorkspaceProcessesSession
>;
export type WorkspaceProcessesProps = {
  scope: WorkspaceProcessScope;
  resourceRevision: string;
  visible: boolean;
  session: WorkspaceProcessesSession;
  load: (signal: AbortSignal) => Promise<WorkspaceProcessSnapshot>;
  loadRecovery?: (
    cursor: string | undefined,
    signal: AbortSignal,
  ) => Promise<WorkspaceProcessRecoveryPage>;
  output: (
    processId: string,
    cursor: number,
    signal: AbortSignal,
  ) => Promise<WorkspaceProcessOutput>;
  review: (attempt: WorkspaceProcessAttempt) => Promise<WorkspaceProcessReview>;
  start: (
    attempt: WorkspaceProcessAttempt,
    evidence: WorkspaceProcessReview,
  ) => Promise<WorkspaceProcessInfo>;
  stop: (processId: string) => Promise<WorkspaceProcessInfo>;
  recover: (processId: string) => Promise<WorkspaceProcessInfo>;
};

function mergeProcess(
  items: WorkspaceProcessInfo[],
  value: WorkspaceProcessInfo,
) {
  const existing = items.find((item) => item.process_id === value.process_id);
  // A delayed Start/list response cannot undo a later confirmed stop.
  const accepted =
    (existing?.quiesced && !value.quiesced) ||
    (existing?.state === 'stopping' && value.state === 'running')
      ? existing
      : value;
  const retained = items.filter((item) => item.process_id !== value.process_id);
  if (retained.length >= 32) {
    const removable = retained.findIndex((item) => item.quiesced);
    if (removable < 0) return items;
    retained.splice(removable, 1);
  }
  return [...retained, accepted];
}
function attention(result: WorkspaceProcessInfo) {
  if (!result.quiesced && result.state === 'cleanup_incomplete')
    return 'Cleanup is incomplete. The workspace writer remains held. Recover this exact process before starting another writer.';
  if (result.state === 'failed')
    return `Process unavailable (${result.code || 'process_start_failed'}).`;
  return '';
}

export default function WorkspaceProcesses(props: WorkspaceProcessesProps) {
  const { session } = props;
  const state = useSyncExternalStore(
    session.subscribe,
    session.getSnapshot,
    session.getSnapshot,
  );
  const callbacks = useRef(props);
  callbacks.current = props;
  const reads = useRef<{
    list?: AbortController;
    output?: AbortController;
    recovery?: AbortController;
  }>({});
  const active = () =>
    callbacks.current.visible &&
    sameScope(session.scope, callbacks.current.scope);
  const update = session.update;

  async function loadRecovery(cursor?: string) {
    if (
      !active() ||
      session.getSnapshot().revoked ||
      !callbacks.current.loadRecovery ||
      session.getSnapshot().controls.size
    )
      return;
    reads.current.recovery?.abort();
    const abort = new AbortController();
    reads.current.recovery = abort;
    update((current) => ({ ...current, recoveryLoading: true }));
    try {
      const page = await callbacks.current.loadRecovery(cursor, abort.signal);
      if (
        abort.signal.aborted ||
        !active() ||
        session.getSnapshot().revoked ||
        session.getSnapshot().controls.size > 0
      )
        return;
      if (
        page.items.length > 32 ||
        new Set(page.items.map((item) => item.process_id)).size !==
          page.items.length ||
        (page.next_cursor !== null &&
          (typeof page.next_cursor !== 'string' ||
            page.next_cursor.length > 64 ||
            page.next_cursor === cursor))
      )
        throw new Error('recovery page');
      update((current) => ({
        ...current,
        recovery: page,
        recoveryCursor: cursor,
        error: '',
      }));
    } catch {
      if (!abort.signal.aborted && active())
        update((current) => ({
          ...current,
          error:
            'Saved process recovery could not be loaded. Return to the first page to refresh its cursor; no process was started.',
        }));
    } finally {
      if (reads.current.recovery === abort)
        update((current) => ({ ...current, recoveryLoading: false }));
    }
  }

  async function load() {
    if (!active() || session.getSnapshot().revoked) return;
    reads.current.list?.abort();
    const abort = new AbortController();
    reads.current.list = abort;
    try {
      const result = await callbacks.current.load(abort.signal);
      if (abort.signal.aborted || !active()) return;
      if (!sameScope(result, session.scope) || result.processes.length > 32)
        throw new Error('scope');
      update((current) => ({
        ...current,
        snapshot: result,
        processes: result.processes.reduce(mergeProcess, current.processes),
        error: '',
      }));
    } catch (cause) {
      if (
        typeof cause === 'object' &&
        cause !== null &&
        'code' in cause &&
        [
          'resource_binding_revoked',
          'capability_revoked',
          'resource_unavailable',
        ].includes(String(cause.code))
      ) {
        if (!abort.signal.aborted) session.revoke();
        return;
      }
      if (!abort.signal.aborted && active())
        update((current) => ({
          ...current,
          error:
            'Processes could not be refreshed. The last confirmed state and original command are retained.',
        }));
    }
  }

  async function output(
    processId: string,
    cursor = 0,
    direction: 'next' | 'previous' | 'first' | 'refresh' = 'first',
  ) {
    if (!active()) return;
    reads.current.output?.abort();
    const abort = new AbortController();
    reads.current.output = abort;
    update((current) => ({
      ...current,
      selected: processId,
      ...(current.selected !== processId
        ? { output: null, cursor: 0, previous: [] }
        : {}),
    }));
    try {
      const result = await callbacks.current.output(
        processId,
        cursor,
        abort.signal,
      );
      if (
        abort.signal.aborted ||
        !active() ||
        session.getSnapshot().selected !== processId
      )
        return;
      if (result.process_id !== processId || result.next_cursor < cursor)
        throw new Error('scope');
      update((current) => ({
        ...current,
        output: result,
        cursor,
        previous:
          direction === 'first'
            ? []
            : direction === 'previous'
              ? current.previous.slice(0, -1)
              : direction === 'next'
                ? [...current.previous, current.cursor].slice(-32)
                : current.previous,
      }));
    } catch {
      if (!abort.signal.aborted && active())
        update((current) => ({
          ...current,
          error:
            'Output is unavailable. Refresh or return to the first retained output; no process was started.',
        }));
    }
  }

  useEffect(() => {
    const requests = reads.current;
    if (props.visible && sameScope(props.scope, session.scope)) void load();
    return () => {
      requests.list?.abort();
      requests.output?.abort();
      requests.recovery?.abort();
    };
    // Session identity owns reads; callback changes never dispatch commands.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.visible, props.resourceRevision, session]);

  const observedProcesses = state.processes
    .filter((item) => !item.quiesced)
    .map((item) => item.process_id)
    .join(':');
  useEffect(() => {
    if (!props.visible || !observedProcesses || state.revoked) return;
    let ended = false;
    let timer: ReturnType<typeof setTimeout>;
    // Observe completion only while this panel is visible. The next read starts
    // after the previous one settles; this never retries a Start or Stop effect.
    const observe = async () => {
      await load();
      if (!ended) timer = setTimeout(() => void observe(), 1000);
    };
    timer = setTimeout(() => void observe(), 1000);
    return () => {
      ended = true;
      clearTimeout(timer);
    };
    // The session owns current callbacks and scope; token rendering does not poll.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.visible, observedProcesses, session, state.revoked]);

  function validReview(
    review: WorkspaceProcessReview,
    attempt: WorkspaceProcessAttempt,
  ) {
    return (
      sameScope(review, session.scope) &&
      review.resource_revision === attempt.snapshot.resource_revision &&
      review.command_id === attempt.command_id &&
      review.command === attempt.command
    );
  }
  async function review() {
    const current = session.getSnapshot();
    if (
      !active() ||
      current.revoked ||
      current.activity ||
      current.attempt?.started ||
      !current.snapshot ||
      current.snapshot.resource_revision !==
        callbacks.current.resourceRevision ||
      !current.draft.trim()
    )
      return;
    const attempt = (current.attempt?.snapshot.resource_revision ===
    current.snapshot.resource_revision
      ? current.attempt
      : null) ?? {
      command_id: crypto.randomUUID(),
      command: current.draft,
      snapshot: current.snapshot,
      review: null,
      started: false,
      uncertain: false,
    };
    update((value) => ({
      ...value,
      attempt,
      activity: 'review',
      error: '',
      notice: '',
    }));
    try {
      const evidence = await callbacks.current.review(attempt);
      if (!validReview(evidence, attempt)) throw new Error('approval scope');
      update((value) => ({
        ...value,
        attempt: { ...attempt, review: evidence },
        notice:
          evidence.decision === 'approved'
            ? 'Command approved. Choose Start command to run this exact command.'
            : evidence.decision === 'pending'
              ? 'Approval is pending. Review its approval card, then check approval here.'
              : 'This command was not approved. No process was started.',
      }));
    } catch {
      update((value) => ({
        ...value,
        error:
          'Approval could not be confirmed. Check the original review again; no process was started.',
      }));
    } finally {
      update((value) => ({ ...value, activity: null }));
    }
  }
  async function start() {
    const current = session.getSnapshot(),
      attempt = current.attempt;
    if (
      !active() ||
      current.revoked ||
      current.activity ||
      !attempt?.review ||
      attempt.review.decision !== 'approved' ||
      !attempt.review.approval_id ||
      !validReview(attempt.review, attempt) ||
      (!attempt.started &&
        attempt.snapshot.resource_revision !==
          callbacks.current.resourceRevision)
    )
      return;
    if (
      !attempt.started &&
      current.processes.length >= 32 &&
      !current.processes.some((item) => item.quiesced)
    ) {
      update((value) => ({
        ...value,
        error:
          'The process session is full. Stop an owned process before starting another.',
      }));
      return;
    }
    const owned = { ...attempt, started: true };
    update((value) => ({
      ...value,
      attempt: owned,
      activity: 'start',
      error: '',
      notice: '',
      processes: mergeProcess(value.processes, {
        process_id: owned.command_id,
        command_id: owned.command_id,
        run_id: '',
        command: owned.command,
        state: 'starting',
        exit_code: null,
        quiesced: false,
      }),
    }));
    try {
      const result = await callbacks.current.start(owned, attempt.review);
      if (
        result.process_id !== owned.command_id ||
        result.command_id !== owned.command_id
      )
        throw new Error('owner');
      update((value) => ({
        ...value,
        attempt: { ...owned, uncertain: false },
        processes: mergeProcess(value.processes, result),
        error: attention(result),
      }));
    } catch {
      update((value) => {
        const stopped = value.processes.some(
          (item) => item.process_id === owned.command_id && item.quiesced,
        );
        return {
          ...value,
          attempt: { ...owned, uncertain: !stopped },
          error: stopped
            ? ''
            : 'Start is unconfirmed. Keep this command identity; retry the original Start or stop its owned process. A new command will not be sent automatically.',
        };
      });
    } finally {
      update((value) => ({ ...value, activity: null }));
    }
  }
  async function control(processId: string, recover: boolean) {
    if (!active() || session.getSnapshot().controls.has(processId)) return;
    update((value) => ({
      ...value,
      controls: new Set([...value.controls, processId]),
    }));
    try {
      const result = await (recover
        ? callbacks.current.recover(processId)
        : callbacks.current.stop(processId));
      if (result.process_id !== processId) throw new Error('owner');
      update((value) => ({
        ...value,
        processes:
          value.recovery?.items.some((item) => item.process_id === processId) &&
          !value.processes.some((item) => item.process_id === processId)
            ? value.processes
            : mergeProcess(value.processes, result),
        recovery: value.recovery
          ? {
              ...value.recovery,
              items: value.recovery.items.map((item) =>
                item.process_id === processId ? result : item,
              ),
            }
          : null,
        error: attention(result),
        attempt:
          result.quiesced && value.attempt?.command_id === processId
            ? { ...value.attempt, uncertain: false }
            : value.attempt,
        notice: result.quiesced
          ? 'Process stopped. Its workspace writer has been released.'
          : 'Stop requested. The workspace writer remains held until cleanup is confirmed.',
      }));
    } catch {
      update((value) => ({
        ...value,
        error:
          'Cleanup could not be confirmed. The original process identity is retained; retry Stop or Recover and stop.',
      }));
    } finally {
      update((value) => ({
        ...value,
        controls: new Set([...value.controls].filter((id) => id !== processId)),
      }));
    }
  }

  if (!props.visible) return null;
  if (!sameScope(session.scope, props.scope))
    return (
      <ErrorState title="Workspace process access changed">
        Open the matching workspace session to review its retained command.
      </ErrorState>
    );
  const attempt = state.attempt;
  const owned = state.processes.find(
    (item) => item.process_id === attempt?.command_id,
  );
  const approved =
    attempt?.review?.decision === 'approved' && !!attempt.review.approval_id;
  return (
    <section className="stack studio-section" aria-label="Workspace processes">
      <header className="capability-header">
        <div>
          <h3>Processes</h3>
          <p>
            Review a command before starting it. Active processes hold the
            workspace writer until cleanup is confirmed.
          </p>
        </div>
        <div className="action-cluster">
          <Button onClick={() => void load()}>Refresh processes</Button>
        </div>
      </header>
      {!state.snapshot && !state.error && (
        <Skeleton label="Loading process status" />
      )}
      {state.error && (
        <ErrorState title="Process requires attention">
          {state.error}
        </ErrorState>
      )}
      <Field label="Process command">
        <Input
          value={state.draft}
          maxLength={4096}
          disabled={!!state.activity || !!attempt?.started || state.revoked}
          onChange={(event) =>
            update((value) => ({
              ...value,
              draft: event.target.value,
              attempt: null,
              notice: '',
            }))
          }
        />
      </Field>
      <div className="actions">
        <Button
          disabled={
            !!state.activity ||
            !!attempt?.started ||
            state.revoked ||
            !state.snapshot ||
            state.snapshot.resource_revision !== props.resourceRevision ||
            !state.draft.trim()
          }
          onClick={() => void review()}
        >
          {attempt &&
          attempt.snapshot.resource_revision !== props.resourceRevision
            ? 'Check current revision'
            : attempt
              ? 'Check original approval'
              : 'Check command'}
        </Button>
        <Button
          disabled={
            !!state.activity ||
            state.revoked ||
            !approved ||
            (!!attempt?.started && !attempt.uncertain) ||
            (!attempt?.started &&
              attempt?.snapshot.resource_revision !== props.resourceRevision)
          }
          onClick={() => void start()}
        >
          {attempt?.uncertain ? 'Retry original Start' : 'Start command'}
        </Button>
        <Button
          disabled={
            !!state.activity ||
            !!attempt?.uncertain ||
            (!!attempt?.started && !owned?.quiesced)
          }
          onClick={() =>
            update((value) => ({
              ...value,
              draft: '',
              attempt: null,
              notice: '',
              error: '',
            }))
          }
        >
          New command
        </Button>
      </div>
      {attempt && (
        <p className="muted">
          Original process ID: <code>{attempt.command_id}</code>
        </p>
      )}
      {attempt?.uncertain && (
        <p role="status">
          Original Start is unconfirmed. Its command and recovery identity are
          retained.
        </p>
      )}
      {state.notice && <p role="status">{state.notice}</p>}
      {state.processes.length === 0 && state.snapshot && (
        <p>No owned processes reported.</p>
      )}
      <ul>
        {state.processes.map((item) => (
          <li key={item.process_id}>
            <p>
              <code>{item.command || item.process_id}</code> ·{' '}
              {item.state.replaceAll('_', ' ')}
              {item.exit_code !== null && ` · Exit ${item.exit_code}`}
            </p>
            {!item.quiesced && (
              <p>
                {!item.run_id
                  ? 'Start has not been confirmed. Stop uses its original process identity.'
                  : item.state === 'cleanup_incomplete'
                    ? 'Cleanup incomplete · workspace writer retained.'
                    : 'Workspace writer held until cleanup completes.'}
              </p>
            )}
            <div className="actions">
              <Button onClick={() => void output(item.process_id)}>
                View output
              </Button>
              <Button
                disabled={
                  item.quiesced ||
                  item.state === 'stopping' ||
                  state.controls.has(item.process_id)
                }
                aria-label={`Stop ${item.process_id}`}
                onClick={() => void control(item.process_id, false)}
              >
                Stop
              </Button>
              <Button
                disabled={item.quiesced || state.controls.has(item.process_id)}
                aria-label={`Recover and stop ${item.process_id}`}
                onClick={() => void control(item.process_id, true)}
              >
                Recover and stop
              </Button>
            </div>
          </li>
        ))}
      </ul>
      {props.loadRecovery && (
        <section
          className="stack capability-section"
          aria-label="Saved process recovery"
        >
          <h4>Saved process recovery</h4>
          <p>
            Historical owners need explicit recovery to confirm cleanup. Each
            page shows at most 32 saved owners; loading it does not probe or
            start a process.
          </p>
          <div className="actions">
            <Button
              disabled={
                state.revoked || state.recoveryLoading || !!state.controls.size
              }
              onClick={() => void loadRecovery()}
            >
              {state.recovery
                ? 'First recovery page'
                : 'Load saved process recovery'}
            </Button>
            <Button
              disabled={
                state.revoked ||
                state.recoveryLoading ||
                !!state.controls.size ||
                !state.recovery?.next_cursor
              }
              onClick={() =>
                void loadRecovery(state.recovery?.next_cursor ?? undefined)
              }
            >
              Next recovery page
            </Button>
          </div>
          {state.recoveryLoading && (
            <Skeleton label="Loading saved process recovery" />
          )}
          {state.recovery?.items.length === 0 && (
            <p>No saved owners on this recovery page.</p>
          )}
          <ul>
            {state.recovery?.items
              .filter(
                (item) =>
                  !state.processes.some(
                    (live) => live.process_id === item.process_id,
                  ),
              )
              .map((item) => (
                <li key={item.process_id}>
                  <p>
                    <code>{item.process_id}</code> ·{' '}
                    {item.quiesced
                      ? 'Cleanup confirmed'
                      : 'Cleanup unconfirmed · writer release not confirmed'}
                  </p>
                  <Button
                    disabled={
                      item.quiesced || state.controls.has(item.process_id)
                    }
                    aria-label={`Recover and stop ${item.process_id}`}
                    onClick={() => void control(item.process_id, true)}
                  >
                    Recover and stop
                  </Button>
                </li>
              ))}
          </ul>
        </section>
      )}
      {state.selected && (
        <section
          className="stack capability-section"
          aria-label="Process output reader"
        >
          <h4>Output</h4>
          <p className="muted">
            Read-only retained output. This panel does not send interactive
            terminal input.
          </p>
          {state.output?.truncated && (
            <p role="status">
              Earlier output is no longer retained, or its drain was incomplete.
              This is the available bounded tail.
            </p>
          )}
          <pre
            className="code-sample"
            tabIndex={0}
            role="region"
            aria-label="Process output"
            style={{
              minWidth: 0,
              maxWidth: '100%',
              maxHeight: 320,
              overflow: 'auto',
              whiteSpace: 'pre-wrap',
            }}
          >
            {state.output?.entries
              .map(
                (entry) =>
                  `${entry.channel === 'stderr' ? '[stderr] ' : ''}${entry.text}`,
              )
              .join('') || 'No output in this section.'}
          </pre>
          <div className="actions">
            <Button onClick={() => void output(state.selected, 0)}>
              First retained output
            </Button>
            <Button
              disabled={!state.previous.length}
              onClick={() =>
                void output(
                  state.selected,
                  state.previous.at(-1) ?? 0,
                  'previous',
                )
              }
            >
              Previous output
            </Button>
            <Button
              disabled={
                !state.output || state.output.next_cursor <= state.cursor
              }
              onClick={() =>
                void output(
                  state.selected,
                  state.output?.next_cursor ?? 0,
                  'next',
                )
              }
            >
              Next output
            </Button>
            <Button
              onClick={() =>
                void output(state.selected, state.cursor, 'refresh')
              }
            >
              Refresh output
            </Button>
          </div>
        </section>
      )}
    </section>
  );
}
