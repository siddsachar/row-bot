import { useEffect, useSyncExternalStore } from 'react';
import { RefreshCw } from 'lucide-react';
import { Button, IconButton, Toggle } from '../../ui/primitives';
import { SettingsItem, StatusLine } from './anatomy';

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
            "Row-Bot couldn't confirm the save. Check again rather than saving twice.",
        });
      }
    } catch {
      session.update({
        busy: '',
        message:
          "Row-Bot couldn't confirm the save. Check again rather than saving twice.",
      });
    }
  };
  const saved = state.snapshot?.saved_enabled ?? null;
  const effective = state.snapshot?.effective_enabled ?? null;
  // "Use apps in chats" (B262): the page's second switch. It only
  // changes which tools chats see, never a server's connection.
  return (
    <SettingsItem
      label="Use apps in chats"
      help="Chats can use your connected apps' tools. Tools that change things ask first."
      layout="inline"
      anchor="mcp-chat-tools"
      bind={false}
      status={
        state.snapshot && !available ? (
          <StatusLine
            tone="warning"
            more={[saved === null ? 'status unknown' : '']}
          >
            Can’t be changed right now:{' '}
            {state.snapshot.availability.replaceAll('_', ' ')}
          </StatusLine>
        ) : saved !== null && effective !== null && saved !== effective ? (
          <StatusLine tone="warning">
            Saved {label(saved).toLowerCase()}, but chats have it{' '}
            {label(effective).toLowerCase()} right now
          </StatusLine>
        ) : undefined
      }
      control={
        <Toggle
          label="Use apps in chats"
          checked={state.draft ?? saved ?? false}
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
      }
      trailing={
        !state.snapshot || !available ? (
          <IconButton
            size="sm"
            label="Refresh chat access"
            disabled={locked}
            onClick={() => void read()}
          >
            <RefreshCw size={15} aria-hidden />
          </IconButton>
        ) : undefined
      }
    >
      {state.pending && (
        <Button
          disabled={!state.active || Boolean(state.busy)}
          onClick={() => void save(state.pending)}
        >
          Check original save
        </Button>
      )}
      {state.message && <p role="status">{state.message}</p>}
    </SettingsItem>
  );
}
