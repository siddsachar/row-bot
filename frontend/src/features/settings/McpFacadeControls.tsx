import { useEffect, useSyncExternalStore } from 'react';
import { Button, Toggle } from '../../ui/primitives';

export type NativeMcpState = {
  schema_version: 1;
  resource_revision: string | null;
  availability: string;
  saved_enabled: boolean | null;
  effective_enabled: boolean | null;
  registered: boolean;
};
export type NativeMcpCommand = {
  command_id: string;
  type: 'mcp.facade.control';
  payload: { resource_revision: string; enabled: boolean };
};
export type NativeMcpReview = {
  resource_revision: string;
  enabled: boolean;
  action_digest: string;
  nonce: string;
};
export type NativeMcpReceipt = {
  command_id: string;
  status: string;
  native_mcp?: {
    schema_version: 1;
    status: string;
    resource_revision: string | null;
    saved_enabled: boolean | null;
    effective_enabled: boolean | null;
    code: string | null;
  };
};
type Attempt = { command: NativeMcpCommand; review: NativeMcpReview };
type State = {
  active: boolean;
  snapshot: NativeMcpState | null;
  draft: boolean | null;
  reviewed: Attempt | null;
  pending: Attempt | null;
  busy: string;
  message: string;
};

/** One authenticated lifetime owner; unmounting never discards uncertain intent. */
export function createMcpFacadeSession() {
  let state: State = {
    active: true,
    snapshot: null,
    draft: null,
    reviewed: null,
    pending: null,
    busy: '',
    message: '',
  };
  const listeners = new Set<() => void>();
  const reads = new Set<AbortController>();
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
    beginRead: () => {
      const abort = new AbortController();
      if (state.active) reads.add(abort);
      else abort.abort();
      return abort;
    },
    endRead: (abort: AbortController) => {
      reads.delete(abort);
    },
    hasRetained: () =>
      state.active &&
      (state.draft !== null || Boolean(state.reviewed || state.pending)),
    dispose: () => {
      reads.forEach((abort) => abort.abort());
      reads.clear();
      state = {
        active: false,
        snapshot: null,
        draft: null,
        reviewed: null,
        pending: null,
        busy: '',
        message: 'Sign in again to manage chat tool access.',
      };
      listeners.forEach((notify) => notify());
    },
  };
}
export type McpFacadeSession = ReturnType<typeof createMcpFacadeSession>;
export type McpFacadeControlsProps = {
  session: McpFacadeSession;
  load: (signal: AbortSignal) => Promise<NativeMcpState>;
  review: (
    payload: NativeMcpCommand['payload'],
    signal: AbortSignal,
  ) => Promise<NativeMcpReview>;
  execute: (
    command: NativeMcpCommand,
    review: NativeMcpReview,
  ) => Promise<NativeMcpReceipt>;
};
const label = (enabled: boolean | null) =>
  enabled === null ? 'Unknown' : enabled ? 'Enabled' : 'Disabled';

export default function McpFacadeControls({
  session,
  load,
  review,
  execute,
}: McpFacadeControlsProps) {
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const read = async () => {
    if (!session.getSnapshot().active || session.getSnapshot().busy) return;
    const abort = session.beginRead();
    session.update({ busy: 'load', reviewed: null });
    try {
      const snapshot = await load(abort.signal);
      if (abort.signal.aborted) return;
      if (snapshot.schema_version !== 1) throw Error();
      session.update({ snapshot, busy: '', message: '' });
    } catch {
      if (!abort.signal.aborted)
        session.update({
          busy: '',
          message: 'Chat tool access is unavailable. Refresh to try again.',
        });
    } finally {
      session.endRead(abort);
    }
  };
  useEffect(() => {
    const current = session.getSnapshot();
    if (!current.active || current.snapshot || current.busy) return;
    const abort = session.beginRead();
    session.update({ busy: 'load' });
    void load(abort.signal)
      .then((snapshot) => {
        if (abort.signal.aborted) return;
        if (snapshot.schema_version !== 1) throw Error();
        session.update({ snapshot, busy: '' });
      })
      .catch(() => {
        if (!abort.signal.aborted)
          session.update({
            busy: '',
            message: 'Chat tool access is unavailable. Refresh to try again.',
          });
      })
      .finally(() => session.endRead(abort));
  }, [session, load]);
  const available = Boolean(
    state.snapshot?.registered &&
    state.snapshot.resource_revision &&
    ['available', 'missing'].includes(state.snapshot.availability),
  );
  const locked = !state.active || Boolean(state.busy || state.pending);
  const requestReview = async () => {
    const current = session.getSnapshot();
    if (
      !current.active ||
      current.busy ||
      current.pending ||
      current.draft === null ||
      !current.snapshot?.resource_revision ||
      !available
    )
      return;
    const command: NativeMcpCommand = {
      command_id: crypto.randomUUID(),
      type: 'mcp.facade.control',
      payload: {
        resource_revision: current.snapshot.resource_revision,
        enabled: current.draft,
      },
    };
    const abort = session.beginRead();
    session.update({ busy: 'review', reviewed: null, message: '' });
    try {
      const result = await review(command.payload, abort.signal);
      if (abort.signal.aborted) return;
      if (
        result.resource_revision !== command.payload.resource_revision ||
        result.enabled !== command.payload.enabled
      )
        throw Error();
      const attempt = { command, review: result };
      session.update({ reviewed: attempt, busy: '', message: '' });
      void save(attempt);
    } catch {
      if (!abort.signal.aborted)
        session.update({
          busy: '',
          message: 'This change could not be validated. Refresh and try again.',
        });
    } finally {
      session.endRead(abort);
    }
  };
  const save = async (attempt: Attempt | null) => {
    const current = session.getSnapshot();
    if (
      !attempt ||
      !current.active ||
      current.busy ||
      (current.pending !== attempt && current.reviewed !== attempt)
    )
      return;
    session.update({
      pending: attempt,
      reviewed: null,
      busy: 'save',
      message: '',
    });
    try {
      const receipt = await execute(attempt.command, attempt.review);
      if (receipt.command_id !== attempt.command.command_id) throw Error();
      const outcome = receipt.native_mcp;
      if (
        receipt.status === 'completed' &&
        outcome?.schema_version === 1 &&
        outcome.status === 'saved' &&
        outcome.saved_enabled === attempt.command.payload.enabled &&
        outcome.effective_enabled === attempt.command.payload.enabled
      ) {
        session.update({
          pending: null,
          draft: null,
          busy: '',
          snapshot: current.snapshot
            ? {
                ...current.snapshot,
                resource_revision: outcome.resource_revision,
                saved_enabled: outcome.saved_enabled,
                effective_enabled: outcome.effective_enabled,
              }
            : null,
          message: 'Chat access saved. Server connections were not changed.',
        });
      } else if (receipt.status === 'rejected') {
        session.update({
          pending: null,
          busy: '',
          message: 'The change was rejected. Refresh and review a new change.',
        });
      } else {
        session.update({
          busy: '',
          message:
            'The original save is unconfirmed. Check its receipt; do not create another save.',
        });
      }
    } catch {
      session.update({
        busy: '',
        message:
          'The original save is unconfirmed. Check its receipt; do not create another save.',
      });
    }
  };
  return (
    <section aria-label="MCP chat access" className="settings-section">
      <h3>External MCP tools in chat</h3>
      <p>
        This switch controls chat tool exposure. Server connections and saved
        server permissions are managed separately.
      </p>
      <p>
        Saved access: {label(state.snapshot?.saved_enabled ?? null)}. Current
        chat access: {label(state.snapshot?.effective_enabled ?? null)}.
      </p>
      {state.snapshot && !available && (
        <p role="status">
          Chat access cannot be changed:{' '}
          {state.snapshot.availability.replaceAll('_', ' ')}.
        </p>
      )}
      <div className="button-row">
        <Button disabled={locked} onClick={() => void read()}>
          Refresh chat access
        </Button>
        <label className="settings-knowledge-switch">
          <span>Enable in chat</span>
          <Toggle
            label="Enable in chat"
            checked={state.draft ?? state.snapshot?.saved_enabled ?? false}
            disabled={locked || !available}
            onChange={(event) => {
              session.update({
                draft: event.target.checked,
                reviewed: null,
                message: '',
              });
              void requestReview();
            }}
          />
        </label>
      </div>
      {state.draft !== null && (
        <p>Proposed chat access: {label(state.draft)}.</p>
      )}
      <div className="button-row">
        {state.pending && (
          <Button
            disabled={!state.active || Boolean(state.busy)}
            onClick={() => void save(state.pending)}
          >
            Check original save
          </Button>
        )}
      </div>
      {state.message && <p role="status">{state.message}</p>}
    </section>
  );
}
