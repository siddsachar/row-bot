import { useEffect, useSyncExternalStore } from 'react';
import { Button } from '../../ui/primitives';

export type RuntimeArchive = {
  version: string;
  url: string;
  sha256: string;
  size_bytes: number;
  system: string;
  arch: string;
  asset_name: string;
};
export type RuntimeInstallationSnapshot = {
  schema_version: 1;
  runtime_id: string;
  resource_revision: string | null;
  availability: string;
  installed: boolean | null;
  active_command_id: string | null;
  quiesced: boolean | null;
};
export type RuntimeInstallationReview = {
  schema_version: 1;
  runtime_id: string;
  operation: 'resolve' | 'install';
  resource_revision: string;
  source_command_id: string | null;
  action_digest: string;
  plan: RuntimeArchive | null;
  disclosures: string[];
  network_required: boolean;
  executes_runtime: boolean;
  nonce?: string;
};
export type RuntimeInstallationCommand = {
  command_id: string;
  type:
    | 'mcp.runtime.resolve'
    | 'mcp.runtime.install'
    | 'mcp.runtime.install.cancel';
  payload: {
    runtime_id: string;
    source_command_id: string | null;
    resource_revision?: string;
    action_digest?: string;
  };
};
export type RuntimeInstallationReceipt = {
  command_id: string;
  status: string;
  code?: string;
  installation: {
    runtime_id: string;
    operation: string;
    stage: string;
    cancel_requested: boolean;
    quiesced: boolean | null;
    installed: boolean | null;
    plan: RuntimeArchive | null;
  };
};
export type RuntimeInstallationCallbacks = {
  load: (signal: AbortSignal) => Promise<RuntimeInstallationSnapshot>;
  review: (
    operation: 'resolve' | 'install',
    sourceCommandId: string | null,
    revision: string,
    signal: AbortSignal,
  ) => Promise<RuntimeInstallationReview>;
  execute: (
    command: RuntimeInstallationCommand,
    review: RuntimeInstallationReview | null,
    signal: AbortSignal,
  ) => Promise<RuntimeInstallationReceipt>;
  receipt: (
    commandId: string,
    signal: AbortSignal,
  ) => Promise<RuntimeInstallationReceipt>;
};
type State = {
  runtimeId: string;
  active: boolean;
  snapshot: RuntimeInstallationSnapshot | null;
  review: RuntimeInstallationReview | null;
  original: RuntimeInstallationCommand | null;
  result: RuntimeInstallationReceipt | null;
  cancel: RuntimeInstallationCommand | null;
  cancelResult: RuntimeInstallationReceipt | null;
  sourceCommandId: string | null;
  busy: boolean;
  cancelling: boolean;
  reading: boolean;
  message: string;
  refresh: number;
};
const terminal = (value: RuntimeInstallationReceipt | null) =>
  value?.status === 'completed' || value?.status === 'rejected';

/** Inject from the authenticated runtime lifetime; one current intent per runtime. */
export function createMcpRuntimeInstallationSession(runtimeId: 'node' | 'uv') {
  let state: State = {
    runtimeId,
    active: true,
    snapshot: null,
    review: null,
    original: null,
    result: null,
    cancel: null,
    cancelResult: null,
    sourceCommandId: null,
    busy: false,
    cancelling: false,
    reading: false,
    message: '',
    refresh: 0,
  };
  const listeners = new Set<() => void>();
  const aborters = new Set<AbortController>();
  const update = (patch: Partial<State>) => {
    if (!state.active) return;
    state = { ...state, ...patch };
    listeners.forEach((notify) => notify());
  };
  const begin = () => {
    const abort = new AbortController();
    if (!state.active) abort.abort();
    else aborters.add(abort);
    return abort;
  };
  const apply = (receipt: RuntimeInstallationReceipt) => {
    if (!state.active || receipt.installation.runtime_id !== state.runtimeId)
      return;
    const id = state.original?.command_id ?? state.snapshot?.active_command_id;
    if (receipt.command_id !== id) return;
    update({
      result: receipt,
      sourceCommandId:
        receipt.status === 'completed' &&
        receipt.installation.stage === 'resolved'
          ? receipt.command_id
          : state.sourceCommandId,
      message: terminal(receipt)
        ? receipt.installation.stage
        : 'Original operation retained. Refresh its receipt; do not start another installation.',
    });
  };
  return {
    getSnapshot: () => state,
    subscribe: (notify: () => void) => {
      listeners.add(notify);
      return () => {
        listeners.delete(notify);
      };
    },
    hasRetained: () =>
      state.active &&
      (state.busy ||
        state.cancelling ||
        !!state.review ||
        (!!(state.original || state.snapshot?.active_command_id) &&
          !terminal(state.result)) ||
        (!!state.cancel && !terminal(state.cancelResult))),
    dispose: () => {
      aborters.forEach((abort) => abort.abort());
      aborters.clear();
      state = {
        ...state,
        active: false,
        review: null,
        original: null,
        result: null,
        cancel: null,
        cancelResult: null,
        sourceCommandId: null,
        snapshot: null,
        busy: false,
        cancelling: false,
        reading: false,
        message: '',
      };
      listeners.forEach((notify) => notify());
    },
    refresh: () => update({ refresh: state.refresh + 1 }),
    async read(
      callbacks: RuntimeInstallationCallbacks,
      external?: AbortSignal,
    ) {
      if (!state.active || state.reading || external?.aborted) return;
      update({ reading: true });
      const abort = begin();
      const stop = () => abort.abort();
      external?.addEventListener('abort', stop, { once: true });
      try {
        const snapshot = await callbacks.load(abort.signal);
        if (
          abort.signal.aborted ||
          !state.active ||
          snapshot.runtime_id !== state.runtimeId
        )
          return;
        update({ snapshot });
        const id = state.original?.command_id ?? snapshot.active_command_id;
        if (id) {
          const receipt = await callbacks.receipt(id, abort.signal);
          if (!abort.signal.aborted) apply(receipt);
          if (
            !abort.signal.aborted &&
            terminal(receipt) &&
            snapshot.availability === 'recovery_required'
          ) {
            const latest = await callbacks.load(abort.signal);
            if (!abort.signal.aborted && latest.runtime_id === state.runtimeId)
              update({ snapshot: latest });
          }
        }
        if (state.cancel && !terminal(state.cancelResult)) {
          const receipt = await callbacks.receipt(
            state.cancel.command_id,
            abort.signal,
          );
          if (
            !abort.signal.aborted &&
            receipt.command_id === state.cancel?.command_id &&
            receipt.installation.runtime_id === state.runtimeId
          )
            update({ cancelResult: receipt });
        }
      } catch {
        if (!abort.signal.aborted)
          update({
            message:
              'Status unavailable. The original operation remains retained.',
          });
      } finally {
        external?.removeEventListener('abort', stop);
        aborters.delete(abort);
        update({ reading: false });
      }
    },
    async review(
      operation: 'resolve' | 'install',
      callbacks: RuntimeInstallationCallbacks,
    ) {
      if (
        !state.active ||
        state.busy ||
        !state.snapshot?.resource_revision ||
        (!!state.original && !terminal(state.result)) ||
        (!!state.cancel && !terminal(state.cancelResult)) ||
        state.snapshot.availability === 'recovery_required'
      )
        return;
      const source = operation === 'install' ? state.sourceCommandId : null;
      if (operation === 'install' && !source) return;
      update({ busy: true, review: null, message: '' });
      const abort = begin();
      try {
        const review = await callbacks.review(
          operation,
          source,
          state.snapshot.resource_revision,
          abort.signal,
        );
        if (
          !abort.signal.aborted &&
          review.runtime_id === state.runtimeId &&
          review.operation === operation &&
          review.source_command_id === source
        )
          update({ review });
      } catch {
        if (!abort.signal.aborted)
          update({
            message: 'Review unavailable. No installation was started.',
          });
      } finally {
        aborters.delete(abort);
        update({ busy: false });
      }
    },
    async run(callbacks: RuntimeInstallationCallbacks) {
      if (!state.active || state.busy || !state.review) return;
      const review = state.review;
      const command: RuntimeInstallationCommand = {
        command_id: crypto.randomUUID(),
        type:
          review.operation === 'resolve'
            ? 'mcp.runtime.resolve'
            : 'mcp.runtime.install',
        payload: {
          runtime_id: state.runtimeId,
          source_command_id: review.source_command_id,
          resource_revision: review.resource_revision,
          action_digest: review.action_digest,
        },
      };
      update({
        original: command,
        result: null,
        cancel: null,
        cancelResult: null,
        review: null,
        busy: true,
        message: '',
      });
      const abort = begin();
      try {
        const receipt = await callbacks.execute(command, review, abort.signal);
        if (!abort.signal.aborted) apply(receipt);
      } catch {
        if (!abort.signal.aborted)
          update({
            message:
              'Response unavailable. Recover the original receipt; do not repeat the action.',
          });
      } finally {
        aborters.delete(abort);
        update({ busy: false, refresh: state.refresh + 1 });
      }
    },
    async cancel(callbacks: RuntimeInstallationCallbacks) {
      if (!state.active || state.cancelling || state.cancel) return;
      const source =
        state.original?.command_id ?? state.snapshot?.active_command_id;
      if (!source || state.result?.installation.quiesced === true) return;
      const command: RuntimeInstallationCommand = {
        command_id: crypto.randomUUID(),
        type: 'mcp.runtime.install.cancel',
        payload: { runtime_id: state.runtimeId, source_command_id: source },
      };
      update({ cancel: command, cancelling: true });
      const abort = begin();
      try {
        const receipt = await callbacks.execute(command, null, abort.signal);
        if (
          !abort.signal.aborted &&
          receipt.command_id === command.command_id &&
          receipt.installation.runtime_id === state.runtimeId
        )
          update({ cancelResult: receipt });
      } catch {
        if (!abort.signal.aborted)
          update({
            message:
              'Cancellation response unavailable. Refresh the original receipts.',
          });
      } finally {
        aborters.delete(abort);
        update({ cancelling: false, refresh: state.refresh + 1 });
      }
    },
  };
}
export type McpRuntimeInstallationSession = ReturnType<
  typeof createMcpRuntimeInstallationSession
>;

export function McpRuntimeInstallation({
  session,
  callbacks,
}: {
  session: McpRuntimeInstallationSession;
  callbacks: RuntimeInstallationCallbacks;
}) {
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  useEffect(() => {
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let reads = 0;
    const visible = () => document.visibilityState !== 'hidden';
    const read = async () => {
      if (abort.signal.aborted || !visible() || reads >= 30) return;
      reads += 1;
      await session.read(callbacks, abort.signal);
      const current = session.getSnapshot();
      if (
        !abort.signal.aborted &&
        visible() &&
        reads < 30 &&
        current.active &&
        ((!!current.original && !terminal(current.result)) ||
          current.snapshot?.quiesced === false)
      )
        timer = setTimeout(() => {
          void read();
        }, 1000);
    };
    const changed = () => {
      clearTimeout(timer);
      if (visible()) void read();
    };
    document.addEventListener('visibilitychange', changed);
    void read();
    return () => {
      abort.abort();
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', changed);
    };
  }, [session, callbacks, state.refresh]);
  if (!state.active) return <p>Runtime installation access is unavailable.</p>;
  const unresolved =
    (!!state.original && !terminal(state.result)) ||
    (!!state.cancel && !terminal(state.cancelResult));
  const canCancel =
    !!(state.original?.command_id ?? state.snapshot?.active_command_id) &&
    state.result?.installation.quiesced !== true &&
    !state.cancel;
  return (
    <section
      className="settings-section"
      aria-label={`${state.runtimeId} managed runtime installation`}
    >
      <h3>{state.runtimeId === 'node' ? 'Node.js' : 'uv'} managed runtime</h3>
      <p>
        Resolve publisher metadata first, then separately review and install the
        pinned archive. No server is connected.
      </p>
      <p role="status">
        {state.message ||
          state.snapshot?.availability ||
          'Reading saved runtime status…'}
      </p>
      <div className="button-row">
        <Button disabled={state.reading} onClick={() => session.refresh()}>
          Refresh installation status
        </Button>
        <Button
          disabled={
            state.busy ||
            unresolved ||
            !state.snapshot?.resource_revision ||
            state.snapshot.availability === 'recovery_required'
          }
          onClick={() => void session.review('resolve', callbacks)}
        >
          Review metadata resolution
        </Button>
        <Button
          disabled={
            state.busy ||
            unresolved ||
            !state.sourceCommandId ||
            state.snapshot?.availability === 'recovery_required'
          }
          onClick={() => void session.review('install', callbacks)}
        >
          Review pinned installation
        </Button>
        <Button
          disabled={!canCancel || state.cancelling}
          onClick={() => void session.cancel(callbacks)}
        >
          Cancel original operation
        </Button>
      </div>
      {state.review && (
        <div>
          <ul>
            {state.review.disclosures.map((text) => (
              <li key={text}>{text}</li>
            ))}
          </ul>
          {state.review.plan && (
            <dl>
              <dt>Version</dt>
              <dd>{state.review.plan.version}</dd>
              <dt>Archive</dt>
              <dd style={{ overflowWrap: 'anywhere' }}>
                {state.review.plan.url}
              </dd>
              <dt>SHA-256</dt>
              <dd style={{ overflowWrap: 'anywhere' }}>
                {state.review.plan.sha256}
              </dd>
              <dt>Bytes</dt>
              <dd>{state.review.plan.size_bytes}</dd>
              <dt>Platform</dt>
              <dd>
                {state.review.plan.system} / {state.review.plan.arch}
              </dd>
            </dl>
          )}
          <Button
            disabled={state.busy}
            onClick={() => void session.run(callbacks)}
          >
            {state.review.operation === 'resolve'
              ? 'Approve and resolve metadata'
              : 'Approve and install pinned runtime'}
          </Button>
        </div>
      )}
      {state.result && (
        <p>
          Original operation: {state.result.installation.stage}. Worker cleanup:{' '}
          {state.result.installation.quiesced === true
            ? 'confirmed'
            : state.result.installation.quiesced === false
              ? 'still owned'
              : 'unconfirmed'}
          .
        </p>
      )}
      {state.cancel && (
        <p>
          Cancellation requested. Ownership remains until the original worker is
          confirmed stopped.
        </p>
      )}
    </section>
  );
}
