import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import { Play, Square } from 'lucide-react';
import {
  Button,
  Disclosure,
  IconButton,
  Input,
  Skeleton,
  StatusDot,
} from '../../ui/primitives';
import { clientError } from '../../api/errors';

/** Output lines kept for the live console of one process. */
const LOG_LIMIT = 2000;
type LogEntry = WorkspaceProcessOutput['entries'][number];

/** The operators the server refuses (`developer/runtime.py`), outside quotes. */
const SHELL_OPERATORS = ['&&', '||', '|', '>', '<'] as const;

/**
 * Commands run as one program with its arguments, never through a shell, so
 * the server refuses shell operators. Say so before sending (B142), with the
 * same unquoting rule the server applies.
 */
export function shellOperators(command: string): string[] {
  let text = '',
    single = false,
    double = false,
    escaped = false;
  for (const char of command) {
    if (escaped) {
      escaped = false;
      if (!single && !double) text += char;
    } else if (char === '\\' && double) escaped = true;
    else if (char === "'" && !double) single = !single;
    else if (char === '"' && !single) double = !double;
    else if (!single && !double) text += char;
  }
  const found: string[] = [];
  let rest = text;
  for (const operator of SHELL_OPERATORS)
    if (rest.includes(operator)) {
      found.push(operator);
      rest = rest.split(operator).join(' ');
    }
  return found;
}

function operatorNote(operators: string[]) {
  const names = operators.join(' ');
  return `Row-Bot runs one command without a shell, so ${names} can't be used here. Run the commands one at a time, or put them in a script and run that.`;
}

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
  /** The live console: lines read so far for the selected process. */
  log: {
    processId: string;
    entries: LogEntry[];
    next: number;
    truncated: boolean;
  } | null;
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
    log: null,
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
        log: null,
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
  /** Checks the inspector detected, each run through the same review. */
  checks?: { label: string; kind: string; command: string }[];
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
            ? 'Command approved.'
            : evidence.decision === 'pending'
              ? 'Approval is pending. Review its approval card, then check approval here.'
              : 'This command was not approved. No process was started.',
      }));
    } catch (cause) {
      update((value) => ({
        ...value,
        error:
          clientError(cause).code === 'process_command_invalid'
            ? "That command can't run here. Row-Bot runs one program with its arguments, without a shell; check it and run it again."
            : 'Approval could not be confirmed. Check the original review again; no process was started.',
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
          'Too many commands are running. Stop one before starting another.',
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
            : "Row-Bot couldn't confirm the command started. Retry Start to check it, or stop the command. Nothing new is sent automatically.",
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

  // ------------------------------------------------ live log tail
  async function tail(processId: string, reset = false) {
    if (!active()) return;
    const known = session.getSnapshot().log;
    const log =
      !reset && known?.processId === processId
        ? known
        : { processId, entries: [], next: 0, truncated: false };
    reads.current.output?.abort();
    const abort = new AbortController();
    reads.current.output = abort;
    update((current) => ({ ...current, selected: processId, log }));
    try {
      const result = await callbacks.current.output(
        processId,
        log.next,
        abort.signal,
      );
      if (
        abort.signal.aborted ||
        !active() ||
        session.getSnapshot().log?.processId !== processId
      )
        return;
      if (result.process_id !== processId || result.next_cursor < log.next)
        throw new Error('scope');
      update((current) => {
        const base = current.log?.processId === processId ? current.log : log;
        const seen = new Set(base.entries.map((entry) => entry.sequence));
        const entries = [
          ...base.entries,
          ...result.entries.filter((entry) => !seen.has(entry.sequence)),
        ];
        const overflow = Math.max(0, entries.length - LOG_LIMIT);
        return {
          ...current,
          log: {
            processId,
            entries: overflow ? entries.slice(overflow) : entries,
            next: result.next_cursor,
            truncated: base.truncated || result.truncated || overflow > 0,
          },
        };
      });
    } catch {
      if (!abort.signal.aborted && active())
        update((current) => ({
          ...current,
          error:
            'Output is unavailable right now. Nothing was started or stopped.',
        }));
    }
  }

  // Follow the selected process while it runs (only while this tab shows);
  // one last read after it stops collects its final lines.
  const selectedProcess = state.processes.find(
    (item) => item.process_id === state.selected,
  );
  const following =
    props.visible && !!selectedProcess && !selectedProcess.quiesced;
  const followed = useRef('');
  useEffect(() => {
    const was = followed.current;
    followed.current = following ? state.selected : '';
    if (!props.visible || !state.selected || state.revoked) return;
    if (!following) {
      if (was === state.selected) void tail(state.selected);
      return;
    }
    let ended = false;
    let timer: ReturnType<typeof setTimeout>;
    const step = async () => {
      await tail(session.getSnapshot().selected);
      if (!ended) timer = setTimeout(() => void step(), 1000);
    };
    timer = setTimeout(() => void step(), 400);
    return () => {
      ended = true;
      clearTimeout(timer);
    };
    // The session owns callbacks; only the selection and its state matter.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.visible, state.selected, following, state.revoked]);
  const consoleRef = useRef<HTMLPreElement>(null);
  const [pinned, setPinned] = useState(true);
  useLayoutEffect(() => {
    const element = consoleRef.current;
    if (element && pinned) element.scrollTop = element.scrollHeight;
  }, [state.log?.entries.length, pinned]);

  async function run(command: string) {
    const current = session.getSnapshot();
    const owned = current.processes.find(
      (item) => item.process_id === current.attempt?.command_id,
    );
    if (
      !command.trim() ||
      current.activity ||
      current.revoked ||
      current.attempt?.uncertain ||
      (current.attempt?.started && !owned?.quiesced)
    )
      return;
    const operators = shellOperators(command);
    update((value) => ({
      ...value,
      draft: command,
      attempt: null,
      notice: operators.length ? operatorNote(operators) : '',
      error: '',
    }));
    if (!operators.length) await continueRun();
  }
  async function continueRun() {
    await review();
    const attempt = session.getSnapshot().attempt;
    if (
      attempt?.review?.decision === 'approved' &&
      attempt.review.approval_id &&
      !attempt.started
    ) {
      await start();
      const started = session.getSnapshot().attempt;
      if (started?.started && !started.uncertain)
        void tail(started.command_id, true);
    }
  }

  if (!props.visible) return null;
  if (!sameScope(session.scope, props.scope))
    return (
      <div className="dev-error-card" role="alert">
        <strong>Workspace process access changed</strong>
        <p>Open the matching workspace to review its retained command.</p>
      </div>
    );
  const attempt = state.attempt;
  const owned = state.processes.find(
    (item) => item.process_id === attempt?.command_id,
  );
  const running = !!attempt?.started && !owned?.quiesced;
  const pending = attempt?.review?.decision === 'pending';
  const blocked =
    !!state.activity ||
    state.revoked ||
    !state.snapshot ||
    state.snapshot.resource_revision !== props.resourceRevision ||
    !!attempt?.uncertain ||
    running;
  const checks = props.checks ?? [];
  const tone = (item: WorkspaceProcessInfo) =>
    !item.quiesced
      ? item.state === 'cleanup_incomplete'
        ? 'warning'
        : 'info'
      : item.state === 'failed' ||
          (item.exit_code !== null && item.exit_code !== 0)
        ? 'danger'
        : item.exit_code === 0
          ? 'success'
          : 'neutral';
  const stateWord = (item: WorkspaceProcessInfo) =>
    item.state === 'running'
      ? 'Running'
      : item.state === 'starting'
        ? 'Starting'
        : item.state === 'stopping'
          ? 'Stopping'
          : item.state === 'cleanup_incomplete'
            ? 'Cleanup incomplete'
            : item.state === 'failed'
              ? 'Failed to start'
              : item.exit_code === 0
                ? 'Passed'
                : item.exit_code !== null
                  ? `Exited ${item.exit_code}`
                  : 'Stopped';
  const log = state.log;
  const stoppable = state.processes.filter(
    (item) =>
      !item.quiesced &&
      item.state !== 'stopping' &&
      !state.controls.has(item.process_id),
  );
  return (
    <section className="dev-run" aria-label="Workspace processes">
      {!state.snapshot && !state.error && (
        <Skeleton label="Loading process status" />
      )}
      {state.error && (
        <div className="dev-error-card" role="alert">
          <strong>Process needs attention</strong>
          <p>{state.error}</p>
          <Button onClick={() => void load()}>Retry</Button>
        </div>
      )}
      {checks.length > 0 && (
        <section className="dev-run-section" aria-label="Detected checks">
          <h4>Checks</h4>
          <ul className="dev-checks">
            {checks.map((check) => {
              const last = [...state.processes]
                .reverse()
                .find((item) => item.command === check.command);
              return (
                <li key={`${check.kind}:${check.label}`}>
                  <StatusDot
                    tone={last ? tone(last) : 'neutral'}
                    pulse={!!last && !last.quiesced}
                    label={last ? stateWord(last) : 'Not run'}
                  />
                  <span className="dev-check-name">{check.label}</span>
                  <code className="dev-check-command">{check.command}</code>
                  {last && (
                    <button
                      type="button"
                      className="dev-link"
                      onClick={() => void tail(last.process_id, true)}
                    >
                      {stateWord(last)}
                    </button>
                  )}
                  <IconButton
                    size="sm"
                    label={`Run ${check.label}`}
                    disabled={blocked}
                    onClick={() => void run(check.command)}
                  >
                    <Play size={14} aria-hidden />
                  </IconButton>
                </li>
              );
            })}
          </ul>
        </section>
      )}
      <form
        className="dev-run-command"
        onSubmit={(event) => {
          event.preventDefault();
          if (pending) void continueRun();
          else if (attempt?.uncertain) void start();
          else void run(state.draft);
        }}
      >
        <Input
          aria-label="Process command"
          placeholder="Run a command, such as npm test"
          value={state.draft}
          maxLength={4096}
          disabled={!!state.activity || running || state.revoked || pending}
          onChange={(event) =>
            update((value) => ({
              ...value,
              draft: event.target.value,
              attempt: value.attempt?.started ? value.attempt : null,
              notice: '',
            }))
          }
        />
        {attempt?.uncertain ? (
          <Button type="submit" disabled={!!state.activity || state.revoked}>
            Retry original Start
          </Button>
        ) : pending ? (
          <>
            <Button
              disabled={!!state.activity || state.revoked}
              onClick={() =>
                update((value) => ({
                  ...value,
                  attempt: null,
                  notice: 'Cancelled. Nothing was started.',
                }))
              }
            >
              Cancel
            </Button>
            <Button type="submit" disabled={!!state.activity || state.revoked}>
              Check approval
            </Button>
          </>
        ) : (
          <Button
            type="submit"
            variant="primary"
            disabled={blocked || !state.draft.trim()}
          >
            <Play size={14} aria-hidden />
            Run
          </Button>
        )}
      </form>
      <p className="dev-muted-line">
        Commands are reviewed before they start and may ask for your approval in
        the chat. A running command holds the workspace until it stops.
      </p>
      {attempt?.uncertain && (
        <p className="dev-git-status" role="status">
          The start was not confirmed. Retry the same start or stop its process;
          a new command is never sent automatically.
        </p>
      )}
      {state.notice && (
        <p className="dev-git-status" role="status">
          {state.notice}
        </p>
      )}
      {state.processes.length > 0 && (
        <section className="dev-run-section" aria-label="Processes">
          <header className="dev-run-section-header">
            <h4>Processes</h4>
            {stoppable.length > 1 && (
              <Button
                variant="ghost"
                aria-label="Stop all processes"
                onClick={() =>
                  stoppable.forEach(
                    (item) => void control(item.process_id, false),
                  )
                }
              >
                <Square size={12} aria-hidden />
                Stop all
              </Button>
            )}
          </header>
          <ul className="dev-processes">
            {[...state.processes].reverse().map((item) => (
              <li
                key={item.process_id}
                data-selected={
                  state.selected === item.process_id ? 'true' : undefined
                }
              >
                <button
                  type="button"
                  className="dev-process-main"
                  aria-label={`View output of ${item.command || item.process_id}`}
                  aria-current={
                    state.selected === item.process_id ? 'true' : undefined
                  }
                  onClick={() => void tail(item.process_id, true)}
                >
                  <StatusDot
                    tone={tone(item)}
                    pulse={!item.quiesced}
                    label={stateWord(item)}
                  />
                  <code>{item.command || item.process_id}</code>
                  <span className="dev-process-state">{stateWord(item)}</span>
                </button>
                {!item.quiesced && (
                  <Button
                    variant="ghost"
                    disabled={
                      item.state === 'stopping' ||
                      state.controls.has(item.process_id)
                    }
                    aria-label={`Stop ${item.process_id}`}
                    onClick={() => void control(item.process_id, false)}
                  >
                    <Square size={12} aria-hidden />
                    Stop
                  </Button>
                )}
                {!item.quiesced &&
                  (item.state === 'cleanup_incomplete' ||
                    item.state === 'stopping' ||
                    !item.run_id) && (
                    <Button
                      variant="ghost"
                      disabled={state.controls.has(item.process_id)}
                      aria-label={`Recover and stop ${item.process_id}`}
                      onClick={() => void control(item.process_id, true)}
                    >
                      Recover and stop
                    </Button>
                  )}
              </li>
            ))}
          </ul>
        </section>
      )}
      {state.snapshot && !state.processes.length && !checks.length && (
        <p className="dev-empty">
          No commands have run here yet. Run one above; its output streams here.
        </p>
      )}
      {log && (
        <section className="dev-console" aria-label="Process output reader">
          <header className="dev-console-header">
            <span className="dev-console-title">
              {selectedProcess?.command || log.processId}
            </span>
            {selectedProcess && (
              <span className="dev-process-state">
                {stateWord(selectedProcess)}
              </span>
            )}
            <label className="dev-toggle-row">
              <input
                type="checkbox"
                checked={pinned}
                onChange={(event) => setPinned(event.target.checked)}
              />
              <span>Follow</span>
            </label>
          </header>
          {log.truncated && (
            <p className="dev-muted-line" role="status">
              Earlier output is no longer kept; this is the latest part.
            </p>
          )}
          <pre
            ref={consoleRef}
            className="dev-console-lines"
            tabIndex={0}
            role="region"
            aria-label="Process output"
            onScroll={(event) => {
              const element = event.currentTarget;
              const atEnd =
                element.scrollHeight -
                  element.scrollTop -
                  element.clientHeight <
                24;
              if (atEnd !== pinned) setPinned(atEnd);
            }}
          >
            {log.entries.length
              ? log.entries.map((entry) => (
                  <span
                    key={entry.sequence}
                    className="dev-console-line"
                    data-channel={entry.channel}
                  >
                    {entry.text}
                  </span>
                ))
              : following
                ? 'Waiting for output…'
                : 'No output.'}
          </pre>
        </section>
      )}
      {props.loadRecovery && (
        <Disclosure summary="Saved process recovery" className="dev-disclosure">
          <p className="muted">
            Commands started before a restart need an explicit recovery to
            confirm cleanup. Loading this page does not start or probe anything.
          </p>
          <div className="action-cluster">
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
            <p className="muted">No saved commands on this page.</p>
          )}
          <ul className="dev-processes">
            {state.recovery?.items
              .filter(
                (item) =>
                  !state.processes.some(
                    (live) => live.process_id === item.process_id,
                  ),
              )
              .map((item) => (
                <li key={item.process_id}>
                  <span className="dev-process-main">
                    <code>{item.process_id}</code>
                    <span className="dev-process-state">
                      {item.quiesced
                        ? 'Cleanup confirmed'
                        : 'Cleanup not confirmed'}
                    </span>
                  </span>
                  <Button
                    variant="ghost"
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
        </Disclosure>
      )}
    </section>
  );
}
