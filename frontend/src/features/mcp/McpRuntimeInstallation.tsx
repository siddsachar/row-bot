import { useEffect, useSyncExternalStore } from 'react';
import { RefreshCw, X } from 'lucide-react';
import { clientError } from '../../api/errors';
import {
  Button,
  EntityList,
  EntityRow,
  IconButton,
  type Tone,
} from '../../ui/primitives';

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
  version: string | null;
  system_available: boolean | null;
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
  /** One Install: the install follows its metadata resolution on its own. */
  chain: boolean;
  busy: boolean;
  cancelling: boolean;
  reading: boolean;
  message: string;
  refresh: number;
};
const FAILED =
  'The install didn’t finish. Nothing was changed; Retry starts it again.';
/** A final failure is "partial" with stage "failed"; the runtime is free again. */
const failed = (value: RuntimeInstallationReceipt | null) =>
  value?.installation.stage === 'failed';
const terminal = (value: RuntimeInstallationReceipt | null) =>
  value?.status === 'completed' ||
  value?.status === 'rejected' ||
  failed(value);

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
    chain: false,
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
      chain: state.chain && !failed(receipt),
      message: failed(receipt)
        ? FAILED
        : receipt.code === 'runtime_installation_owner_unavailable'
          ? 'Another window is running this install. Row-Bot keeps checking.'
          : '',
    });
  };
  const session = {
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
        state.chain ||
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
        chain: false,
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
          update({ message: 'Status unavailable. Check again in a moment.' });
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
      } catch (cause) {
        if (!abort.signal.aborted)
          update({
            chain: false,
            message: `Couldn’t start the install. ${clientError(cause).message}`,
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
              'No answer arrived. Row-Bot checks the install instead of starting another.',
          });
      } finally {
        aborters.delete(abort);
        update({ busy: false, refresh: state.refresh + 1 });
      }
    },
    async start(
      operation: 'resolve' | 'install',
      callbacks: RuntimeInstallationCallbacks,
    ) {
      await session.review(operation, callbacks);
      if (state.active && state.review?.operation === operation)
        await session.run(callbacks);
    },
    /** The single Install (or Retry): resolve the exact version, then install it. */
    async install(callbacks: RuntimeInstallationCallbacks) {
      if (!state.active || state.chain) return;
      const before = state.original;
      update({ chain: true });
      await session.start('resolve', callbacks);
      // Nothing started (the review was refused): there is nothing to follow.
      if (state.original === before) update({ chain: false });
      await session.advance(callbacks);
    },
    /** Once the chained resolution has finished, start its install. */
    async advance(callbacks: RuntimeInstallationCallbacks) {
      const result = state.result;
      if (!state.active || !state.chain || state.busy || !terminal(result))
        return;
      update({ chain: false });
      if (
        state.original?.type === 'mcp.runtime.resolve' &&
        result?.status === 'completed' &&
        result.installation.stage === 'resolved'
      )
        await session.start('install', callbacks);
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
      update({ cancel: command, cancelling: true, chain: false });
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
            message: 'No answer arrived for Cancel. Check again in a moment.',
          });
      } finally {
        aborters.delete(abort);
        update({ cancelling: false, refresh: state.refresh + 1 });
      }
    },
  };
  return session;
}
export type McpRuntimeInstallationSession = ReturnType<
  typeof createMcpRuntimeInstallationSession
>;

const NAMES: Record<string, string> = { node: 'Node.js', uv: 'uv' };
const STAGES: Record<string, string> = {
  admitted: 'Starting',
  resolving: 'Checking the latest version',
  resolved: 'Starting',
  downloading: 'Downloading',
  extracting: 'Unpacking',
  generation_prepared: 'Finishing',
  manifest_prepared: 'Finishing',
  cancellation_requested: 'Stopping',
};

/** Work that is still running and worth checking on again. */
function working(state: State) {
  return (
    state.chain ||
    (!!state.original && !terminal(state.result)) ||
    (!!state.cancel && !terminal(state.cancelResult)) ||
    state.snapshot?.quiesced === false ||
    state.snapshot?.availability === 'recovery_required'
  );
}

/** What the row says: status as a dot and plain words, and its one action. */
function describe(state: State): {
  tone: Tone;
  label: string;
  action: 'install' | 'retry' | null;
  installing: boolean;
} {
  const snapshot = state.snapshot;
  const running = state.chain || (!!state.original && !terminal(state.result));
  if (running || (snapshot?.active_command_id && !terminal(state.result))) {
    const stage = state.cancel
      ? 'Stopping'
      : (STAGES[state.result?.installation.stage ?? ''] ?? 'Starting');
    return {
      tone: 'info',
      label: `Installing · ${stage}`,
      action: null,
      installing: true,
    };
  }
  if (!snapshot)
    return {
      tone: 'neutral',
      label: 'Checking…',
      action: null,
      installing: false,
    };
  if (snapshot.availability === 'recovery_required')
    return {
      tone: 'info',
      label: 'Installing in another window',
      action: null,
      installing: false,
    };
  if (snapshot.installed)
    return {
      tone: 'success',
      label: snapshot.version
        ? `Installed v${snapshot.version.replace(/^v/, '')}`
        : 'Installed',
      action: null,
      installing: false,
    };
  if (failed(state.result))
    return {
      tone: 'danger',
      label: 'Failed',
      action: 'retry',
      installing: false,
    };
  if (snapshot.availability === 'unavailable')
    return {
      tone: 'warning',
      label: 'Needs reinstalling',
      action: 'install',
      installing: false,
    };
  return {
    tone: 'neutral',
    label: 'Not installed',
    action: 'install',
    installing: false,
  };
}

export function McpRuntimeInstallation({
  session,
  callbacks,
}: {
  session: McpRuntimeInstallationSession;
  callbacks: RuntimeInstallationCallbacks;
}) {
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  // Checks again every second while an install runs, however long it takes
  // (B262); pauses while the window is hidden and never overlaps reads.
  useEffect(() => {
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const visible = () => document.visibilityState !== 'hidden';
    const read = async () => {
      if (abort.signal.aborted || !visible()) return;
      await session.read(callbacks, abort.signal);
      if (abort.signal.aborted) return;
      await session.advance(callbacks);
      const current = session.getSnapshot();
      if (
        !abort.signal.aborted &&
        visible() &&
        current.active &&
        working(current)
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
  if (!state.active) return <p>Sign in again to manage runtimes.</p>;
  const name = NAMES[state.runtimeId] ?? state.runtimeId;
  const view = describe(state);
  const canCancel =
    view.installing &&
    !!(state.original?.command_id ?? state.snapshot?.active_command_id) &&
    state.result?.installation.quiesced !== true &&
    !state.cancel;
  const blocked =
    state.busy ||
    !state.snapshot?.resource_revision ||
    state.snapshot.availability === 'recovery_required';
  const system = state.snapshot?.system_available === true;
  return (
    <section
      className="settings-mcp-runtime"
      aria-label={`${state.runtimeId} managed runtime installation`}
    >
      <EntityList label={`${name} runtime`}>
        <EntityRow
          title={name}
          status={{ tone: view.tone, label: view.label }}
          meta={
            system && !state.snapshot?.installed
              ? `System ${name} found; local servers can use it`
              : undefined
          }
          action={
            <>
              {view.action && (
                <Button
                  variant={system ? 'secondary' : 'primary'}
                  disabled={blocked}
                  onClick={() => void session.install(callbacks)}
                >
                  {view.action === 'retry' ? 'Retry' : 'Install'}
                </Button>
              )}
              {canCancel && (
                <IconButton
                  size="sm"
                  label="Cancel install"
                  disabled={state.cancelling}
                  onClick={() => void session.cancel(callbacks)}
                >
                  <X size={15} aria-hidden />
                </IconButton>
              )}
              <IconButton
                size="sm"
                label={`Check ${name} again`}
                disabled={state.reading}
                onClick={() => session.refresh()}
              >
                <RefreshCw size={15} aria-hidden />
              </IconButton>
            </>
          }
        />
      </EntityList>
      {state.message && (
        <p role="status" className="settings-help">
          {state.message}
        </p>
      )}
    </section>
  );
}
