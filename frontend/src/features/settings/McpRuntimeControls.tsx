import { useEffect, useMemo, useSyncExternalStore } from 'react';
import { RefreshCw } from 'lucide-react';
import { clientError } from '../../api/errors';
import { Button, IconButton } from '../../ui/primitives';
import { SettingsGroup, SettingsItem } from './anatomy';
import { mcpRevision, reviewFresh } from './mcp-revision';

export type McpRuntimeState = {
  schema_version: 1;
  server_id: string;
  configuration_revision: string | null;
  cleanup_revision: string | null;
  availability: string;
  runtime_id: string | null;
  state: string;
  session_quiesced: boolean | null;
  /** MCP and this server are both turned on; Connect needs both. */
  enabled: boolean | null;
};
export type McpRuntimeCommand = {
  command_id: string;
  type: 'mcp.runtime.control';
  payload: {
    resource_revision: string;
    server_id: string;
    operation: 'connect' | 'test' | 'disconnect';
    expected_runtime_id: string | null;
  };
};
export type McpRuntimeReview = {
  resource_revision: string;
  server_id: string;
  operation: McpRuntimeCommand['payload']['operation'];
  runtime_id: string | null;
  action_digest: string;
  nonce?: string;
};
export type McpRuntimeReceipt = {
  command_id: string;
  status: string;
  mcp_runtime?: {
    schema_version: 1;
    server_id: string;
    operation: McpRuntimeCommand['payload']['operation'];
    runtime_id: string | null;
    state: string;
    session_quiesced: boolean | null;
    code: string | null;
  };
};
type Attempt = {
  command: McpRuntimeCommand;
  review: McpRuntimeReview;
  runtimeId: string | null;
};
type Slot = {
  reviewed: Attempt | null;
  pending: Attempt | null;
  busy: boolean;
  message: string;
};
type SlotName = 'launch' | 'cleanup';
type State = {
  serverId: string;
  snapshot: McpRuntimeState | null;
  launch: Slot;
  cleanup: Slot;
  active: boolean;
  refresh: number;
  readMessage: string;
  testedCommandId: string | null;
};
const emptySlot = (): Slot => ({
  reviewed: null,
  pending: null,
  busy: false,
  message: '',
});

/** One authenticated runtime owns one bounded session per saved server identity. */
export function createMcpRuntimeSession(serverId: string) {
  let state: State = {
    serverId,
    snapshot: null,
    launch: emptySlot(),
    cleanup: emptySlot(),
    active: true,
    refresh: 0,
    readMessage: '',
    testedCommandId: null,
  };
  const listeners = new Set<() => void>();
  const aborters = new Set<AbortController>();
  let observation: AbortController | null = null;
  let observationWaiter: (() => void) | null = null;
  const update = (patch: Partial<State>) => {
    if (!state.active) return;
    state = { ...state, ...patch };
    listeners.forEach((notify) => notify());
  };
  return {
    getSnapshot: () => state,
    subscribe: (notify: () => void) => {
      listeners.add(notify);
      return () => {
        listeners.delete(notify);
      };
    },
    update,
    updateSlot: (name: SlotName, patch: Partial<Slot>) =>
      update({ [name]: { ...state[name], ...patch } }),
    refresh: () => update({ refresh: state.refresh + 1 }),
    beginRead: () => {
      const abort = new AbortController();
      if (!state.active) abort.abort();
      else aborters.add(abort);
      return abort;
    },
    endRead: (abort: AbortController) => {
      aborters.delete(abort);
    },
    beginObservation: () => {
      if (!state.active || observation) return null;
      observation = new AbortController();
      aborters.add(observation);
      return observation;
    },
    endObservation: (abort: AbortController) => {
      aborters.delete(abort);
      abort.abort();
      if (observation !== abort) return;
      observation = null;
      observationWaiter?.();
    },
    observeAvailability: (notify: () => void) => {
      observationWaiter = notify;
      return () => {
        if (observationWaiter === notify) observationWaiter = null;
      };
    },
    hasRetained: () =>
      state.active &&
      Boolean(
        state.launch.pending ||
        state.launch.reviewed ||
        state.cleanup.pending ||
        state.cleanup.reviewed,
      ),
    dispose: () => {
      aborters.forEach((abort) => abort.abort());
      aborters.clear();
      state = {
        serverId,
        snapshot: null,
        launch: emptySlot(),
        cleanup: emptySlot(),
        active: false,
        refresh: 0,
        readMessage: 'Sign in again to manage MCP connections.',
        testedCommandId: null,
      };
      listeners.forEach((notify) => notify());
    },
  };
}
export type McpRuntimeSession = ReturnType<typeof createMcpRuntimeSession>;
export type McpRuntimeControlsProps = {
  session: McpRuntimeSession;
  load: (serverId: string, signal: AbortSignal) => Promise<McpRuntimeState>;
  review: (
    payload: McpRuntimeCommand['payload'],
    signal: AbortSignal,
  ) => Promise<McpRuntimeReview>;
  execute: (
    command: McpRuntimeCommand,
    review: McpRuntimeReview,
  ) => Promise<McpRuntimeReceipt>;
  /**
   * Turns MCP and this server on, each a reviewed change, so a turned-off
   * server offers one "Turn on & connect" (B262).
   */
  turnOn?: (serverId: string) => Promise<void>;
};

export type McpRuntimeIO = Omit<McpRuntimeControlsProps, 'session'>;

/** A connection state in plain words (B262). */
export const runtimeStateLabels: Record<string, string> = {
  missing: 'Not connected',
  not_started: 'Starting',
  connecting: 'Connecting',
  connected: 'Connected',
  stopping: 'Disconnecting',
  stopped: 'Not connected',
  failed: 'Couldn’t connect',
  dependency_missing: 'A runtime it needs isn’t installed',
  cleanup_incomplete: 'Cleanup didn’t finish; check again to finish it',
};

const CONNECTED = 'Connected. Tools that change things still ask first.';
const DISCONNECTED = 'Disconnected.';
/** A finished Connect or Disconnect: a server's row already shows it. */
export const RUNTIME_DONE = new Set([CONNECTED, DISCONNECTED]);

/**
 * The reviewed connection commands for one saved server: Test, Connect
 * (turning MCP and the server on first when they are off) and Disconnect.
 * The server's details and its row in the list (B262) both run these, so a
 * command started in one shows in the other.
 */
export function runtimeActions(
  session: McpRuntimeSession,
  { load, review, execute, turnOn }: McpRuntimeIO,
) {
  /** One passive read of the connection state. */
  const read = async () => {
    const current = session.getSnapshot();
    if (!current.active) return;
    const abort = session.beginRead();
    try {
      const value = await load(current.serverId, abort.signal);
      if (abort.signal.aborted) return;
      if (value.server_id !== current.serverId || value.schema_version !== 1)
        throw Error();
      session.update({ snapshot: value, readMessage: '' });
    } catch {
      if (!abort.signal.aborted)
        session.update({
          readMessage:
            'Connection state is unavailable. Refresh to check again.',
        });
    } finally {
      session.endRead(abort);
    }
  };
  const submit = async (name: SlotName, attempt: Attempt | null) => {
    const current = session.getSnapshot();
    if (!attempt || !current.active || current[name].busy) return;
    // Only a retained reviewed/original intent can be dispatched from this session.
    if (current[name].pending !== attempt && current[name].reviewed !== attempt)
      return;
    session.updateSlot(name, {
      pending: attempt,
      reviewed: null,
      busy: true,
      message: '',
    });
    session.refresh();
    try {
      const receipt = await execute(attempt.command, attempt.review);
      if (!session.getSnapshot().active) return;
      if (receipt.command_id !== attempt.command.command_id) throw Error();
      if (receipt.status === 'rejected') {
        session.updateSlot(name, {
          pending: null,
          busy: false,
          message: 'The action was rejected. Refresh and review again.',
        });
      } else {
        const value = receipt.mcp_runtime;
        if (
          !value ||
          value.schema_version !== 1 ||
          value.server_id !== current.serverId ||
          value.operation !== attempt.command.payload.operation ||
          (attempt.runtimeId !== null && value.runtime_id !== attempt.runtimeId)
        )
          throw Error();
        const allowedStates =
          value.operation === 'test'
            ? ['tested', 'failed']
            : value.operation === 'disconnect'
              ? ['stopped']
              : ['connected', 'stopped', 'failed', 'dependency_missing'];
        const completed =
          receipt.status === 'completed' &&
          (value.operation === 'connect' && value.state === 'connected'
            ? value.session_quiesced === false
            : value.session_quiesced === true) &&
          allowedStates.includes(value.state);
        if (completed) {
          if (value.operation === 'test' && value.state === 'tested')
            session.update({ testedCommandId: attempt.command.command_id });
          session.updateSlot(name, {
            pending: null,
            busy: false,
            message:
              value.state === 'tested'
                ? 'Test completed and the temporary connection closed.'
                : value.state === 'connected'
                  ? CONNECTED
                  : value.state === 'dependency_missing'
                    ? 'Couldn’t connect: a runtime it needs isn’t installed. Install it under Runtimes, then try again.'
                    : value.state === 'failed'
                      ? value.operation === 'test'
                        ? 'The test didn’t pass: the server didn’t start or answer. Check its settings, then test again.'
                        : 'Couldn’t connect: the server didn’t start or answer. Check its settings, then try again.'
                      : DISCONNECTED,
          });
        } else {
          session.updateSlot(name, {
            pending: {
              ...attempt,
              runtimeId: value.runtime_id ?? attempt.runtimeId,
            },
            busy: false,
            message:
              'The outcome is not confirmed. Check the original command; no replacement will be launched.',
          });
        }
      }
    } catch {
      session.updateSlot(name, {
        busy: false,
        message:
          'The outcome is uncertain. Check the original command before another launch.',
      });
    } finally {
      session.refresh();
    }
  };
  const requestReview = async (
    operation: McpRuntimeCommand['payload']['operation'],
  ) => {
    const name: SlotName = operation === 'disconnect' ? 'cleanup' : 'launch';
    const current = session.getSnapshot();
    if (!current.active || current[name].busy || current[name].pending) return;
    let saved = current.snapshot;
    const revisionOf = (value: McpRuntimeState | null) =>
      operation === 'disconnect'
        ? value?.cleanup_revision
        : value?.configuration_revision;
    if (
      !revisionOf(saved) ||
      (operation === 'disconnect' && !saved?.runtime_id)
    )
      return;
    if (
      name === 'launch' &&
      (current.cleanup.pending || current.cleanup.busy || saved?.runtime_id)
    )
      return;
    const abort = session.beginRead();
    session.updateSlot(name, { busy: true, reviewed: null, message: '' });
    const reread = async () => {
      const value = await load(current.serverId, abort.signal);
      if (value.server_id !== current.serverId || value.schema_version !== 1)
        throw Error();
      session.update({ snapshot: value });
      saved = value;
      return revisionOf(value) ?? null;
    };
    let attempt: Attempt | null = null;
    try {
      // "Turn on & connect": turn MCP and the server on, then connect.
      if (operation === 'connect' && saved?.enabled === false && turnOn) {
        await turnOn(current.serverId);
        mcpRevision.saved(session);
        await reread();
        if (session.getSnapshot().snapshot?.enabled !== true)
          throw Error('The server is still turned off. Try again.');
      }
      const sent: { command?: McpRuntimeCommand } = {};
      const result = await reviewFresh(
        (revision) => {
          sent.command = {
            command_id: crypto.randomUUID(),
            type: 'mcp.runtime.control',
            payload: {
              resource_revision: revision,
              server_id: current.serverId,
              operation,
              expected_runtime_id:
                operation === 'disconnect' ? saved!.runtime_id : null,
            },
          };
          return review(sent.command.payload, abort.signal);
        },
        revisionOf(saved)!,
        reread,
      );
      const command = sent.command;
      if (abort.signal.aborted || !command) return;
      if (
        result.resource_revision !== command.payload.resource_revision ||
        result.server_id !== current.serverId ||
        result.operation !== operation ||
        result.runtime_id !== command.payload.expected_runtime_id
      )
        throw Error();
      attempt = { command, review: result, runtimeId: result.runtime_id };
      session.updateSlot(name, {
        reviewed: attempt,
        busy: false,
        message: '',
      });
    } catch (cause) {
      if (!abort.signal.aborted)
        session.updateSlot(name, {
          busy: false,
          message:
            cause instanceof Error && cause.message
              ? cause.message
              : (cause as { code?: string } | null)?.code
                ? clientError(cause).message
                : 'The action could not be validated. Refresh and try again.',
        });
    } finally {
      session.endRead(abort);
    }
    if (attempt) await submit(name, attempt);
  };
  /** A row's action: reads the state first when nothing was read yet. */
  // The server row's one action follows the server list: read the runtime
  // first, so a Disconnect right after Connect sees the runtime it made
  // (it silently did nothing with the older read, B262).
  const run = async (operation: McpRuntimeCommand['payload']['operation']) => {
    await read();
    await requestReview(operation);
  };
  return { read, requestReview, submit, run };
}

export default function McpRuntimeControls({
  session,
  load,
  review,
  execute,
  turnOn,
}: McpRuntimeControlsProps) {
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const { snapshot, launch, cleanup } = state;
  const actions = useMemo(
    () => runtimeActions(session, { load, review, execute, turnOn }),
    [session, load, review, execute, turnOn],
  );
  // A save in another MCP panel changes the revision this one reviews against.
  useEffect(
    () =>
      mcpRevision.subscribe((source) => {
        if (source !== session) session.refresh();
      }),
    [session],
  );
  // Only passive reads repeat, with one request at a time and a finite visible window.
  // Unmount stops observation; the runtime session retains effect receipts.
  useEffect(() => {
    if (!session.getSnapshot().active) return;
    let abort: AbortController | null = null;
    let disposed = false;
    let waiting = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let remaining = 30;
    const isVisible = () => document.visibilityState !== 'hidden';
    const read = async () => {
      if (disposed || !isVisible() || remaining <= 0) return;
      const currentAbort = session.beginObservation();
      if (!currentAbort) {
        waiting = true;
        return;
      }
      waiting = false;
      abort = currentAbort;
      remaining--;
      let observe = false;
      try {
        const value = await load(state.serverId, currentAbort.signal);
        if (currentAbort.signal.aborted || disposed) return;
        if (value.server_id !== state.serverId || value.schema_version !== 1)
          throw Error();
        session.update({ snapshot: value, readMessage: '' });
        const current = session.getSnapshot();
        observe = Boolean(
          current.launch.pending ||
          current.cleanup.pending ||
          (value.runtime_id !== null && value.session_quiesced !== true),
        );
        if (remaining === 0) {
          session.update({
            readMessage:
              'Automatic observation paused. Refresh to check current connection state.',
          });
        }
      } catch {
        if (!currentAbort.signal.aborted && !disposed)
          session.update({
            readMessage:
              'Connection state is unavailable. Refresh to check again.',
          });
      } finally {
        abort = null;
        const continueObserving =
          observe &&
          remaining > 0 &&
          !disposed &&
          !currentAbort.signal.aborted &&
          isVisible();
        session.endObservation(currentAbort);
        if (continueObserving) timer = setTimeout(() => void read(), 2000);
      }
    };
    const unsubscribe = session.observeAvailability(() => {
      if (waiting) void read();
    });
    const visibility = () => {
      clearTimeout(timer);
      if (!isVisible()) abort?.abort();
      else void read();
    };
    document.addEventListener('visibilitychange', visibility);
    void read();
    return () => {
      disposed = true;
      abort?.abort();
      clearTimeout(timer);
      unsubscribe();
      document.removeEventListener('visibilitychange', visibility);
    };
  }, [session, load, state.serverId, state.refresh, state.active]);

  const launchLocked =
    !state.active ||
    launch.busy ||
    Boolean(
      launch.pending || cleanup.pending || cleanup.busy || snapshot?.runtime_id,
    ) ||
    !snapshot?.configuration_revision ||
    snapshot.availability !== 'available';
  const cleanupLocked =
    !state.active ||
    cleanup.busy ||
    Boolean(cleanup.pending) ||
    !snapshot?.runtime_id ||
    !snapshot.cleanup_revision;
  // Connect is refused for a turned-off server; offer to turn it on first.
  const turnedOff = snapshot?.enabled === false;
  // A running connection offers Disconnect; otherwise Connect and Test.
  const running = Boolean(snapshot?.runtime_id || cleanup.pending);
  return (
    <SettingsGroup
      title="Connection"
      className="settings-mcp-connection"
      meta={
        <IconButton
          size="sm"
          label="Refresh connection"
          disabled={!state.active}
          onClick={() => session.refresh()}
        >
          <RefreshCw size={15} aria-hidden />
        </IconButton>
      }
    >
      <SettingsItem
        label={
          snapshot
            ? (runtimeStateLabels[snapshot.state] ?? 'Connection state unknown')
            : 'Not read yet'
        }
        help={
          turnedOff && !snapshot?.runtime_id
            ? turnOn
              ? 'This server is turned off. Turn on & connect turns MCP and this server on, then connects.'
              : 'This server is turned off. Turn it on in its permissions to connect.'
            : 'Connecting or testing may run a local command or contact the server. Tool approvals stay separate.'
        }
        bind={false}
        control={
          running ? (
            <Button
              disabled={cleanupLocked}
              onClick={() => void actions.requestReview('disconnect')}
            >
              Disconnect
            </Button>
          ) : (
            <>
              <Button
                variant="primary"
                disabled={launchLocked || (turnedOff && !turnOn)}
                onClick={() => void actions.requestReview('connect')}
              >
                {turnedOff && turnOn ? 'Turn on & connect' : 'Connect'}
              </Button>
              <Button
                disabled={launchLocked}
                onClick={() => void actions.requestReview('test')}
              >
                Test
              </Button>
            </>
          )
        }
      >
        {snapshot?.session_quiesced === true &&
          snapshot.state === 'cleanup_incomplete' && (
            <p>
              Session cleanup finished, but its record still needs checking.
            </p>
          )}
        {(launch.pending || cleanup.pending) && (
          <div className="action-cluster">
            {launch.pending && (
              <Button
                disabled={launch.busy || !state.active}
                onClick={() => void actions.submit('launch', launch.pending)}
              >
                Check original launch
              </Button>
            )}
            {cleanup.pending && (
              <Button
                disabled={cleanup.busy || !state.active}
                onClick={() => void actions.submit('cleanup', cleanup.pending)}
              >
                Check original disconnect
              </Button>
            )}
          </div>
        )}
        {launch.message && <p role="status">{launch.message}</p>}
        {cleanup.message && <p role="status">{cleanup.message}</p>}
        {state.readMessage && <p role="status">{state.readMessage}</p>}
      </SettingsItem>
    </SettingsGroup>
  );
}
