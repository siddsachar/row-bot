import { useEffect, useRef, useSyncExternalStore } from 'react';
import { Button, type Tone } from '../../ui/primitives';
import { Drawer } from '../../ui/overlays';
import { DangerAction, SettingsDangerZone } from './anatomy';
import McpCatalogAcceptance, {
  createMcpCatalogSession,
  type McpCatalogSession,
  type McpCatalogAcceptanceProps,
} from './McpCatalogAcceptance';
import McpPolicyControls, {
  createMcpPolicySession,
  type McpPolicySession,
  type McpPolicyControlsProps,
} from './McpPolicyControls';
import McpRuntimeControls, {
  RUNTIME_DONE,
  createMcpRuntimeSession,
  runtimeActions,
  type McpRuntimeIO,
  type McpRuntimeSession,
  type McpRuntimeControlsProps,
} from './McpRuntimeControls';

/** Bounded auth-lifetime originals; a retained launch or cleanup is never evicted. */
export function createMcpConnections(capacity = 8) {
  const sessions = new Map<
    string,
    { name: string; session: McpRuntimeSession; policy: McpPolicySession }
  >();
  const globalPolicy = createMcpPolicySession();
  const catalogs = new Map<string, McpCatalogSession>();
  const listeners = new Set<() => void>();
  let state = { selected: '', message: '', active: true, revision: 0 };
  const emit = (patch: Partial<typeof state>) => {
    state = { ...state, ...patch, revision: state.revision + 1 };
    listeners.forEach((notify) => notify());
  };
  /** The server's sessions, made (and a settled one evicted) when needed. */
  const ensure = (server: string, name: string) => {
    if (!state.active || !/^[a-f0-9]{64}$/.test(server)) return null;
    const existing = sessions.get(server);
    if (existing) return existing;
    if (sessions.size >= capacity) {
      const disposable = [...sessions.entries()].find(
        ([id, item]) =>
          id !== state.selected &&
          !item.session.hasRetained() &&
          !item.policy.hasRetained() &&
          ![...catalogs.values()].some(
            (catalog) => catalog.serverId === id && catalog.hasRetained(),
          ),
      );
      if (!disposable) {
        emit({
          message:
            'Finish or recover a retained connection command before opening another server.',
        });
        return null;
      }
      disposable[1].session.dispose();
      disposable[1].policy.dispose();
      sessions.delete(disposable[0]);
    }
    const item = {
      name,
      session: createMcpRuntimeSession(server),
      policy: createMcpPolicySession(server),
    };
    sessions.set(server, item);
    emit({ message: '' });
    return item;
  };
  return {
    globalPolicy,
    catalog(server: string, test: string) {
      if (!state.active) return null;
      const key = server + ':' + test;
      if (catalogs.has(key)) return catalogs.get(key)!;
      for (const [priorKey, prior] of catalogs) {
        if (prior.serverId === server && !prior.hasRetained()) {
          prior.dispose();
          catalogs.delete(priorKey);
        }
      }
      if (catalogs.size >= capacity) {
        const settled = [...catalogs].find(([, item]) => !item.hasRetained());
        if (!settled) return null;
        settled[1].dispose();
        catalogs.delete(settled[0]);
      }
      const item = createMcpCatalogSession(server, test);
      catalogs.set(key, item);
      return item;
    },
    catalogs: (server: string) =>
      [...catalogs.values()].filter((item) => item.serverId === server),
    getSnapshot: () => state,
    subscribe: (notify: () => void) => {
      listeners.add(notify);
      return () => {
        listeners.delete(notify);
      };
    },
    /** Opens the server's details. */
    select(server: string, name: string) {
      if (ensure(server, name)) emit({ selected: server, message: '' });
    },
    /** Closes the details; the server's retained commands stay. */
    close() {
      if (state.selected) emit({ selected: '' });
    },
    /** The server's connection session for its row, made on first use. */
    runtime: (server: string, name: string) =>
      ensure(server, name)?.session ?? null,
    /** The server's connection session, if one exists. */
    peek: (server: string) => sessions.get(server)?.session,
    entries: () => [...sessions.entries()],
    selected: () => sessions.get(state.selected),
    hasRetained: () =>
      [...catalogs.values()].some((item) => item.hasRetained()) ||
      globalPolicy.hasRetained() ||
      [...sessions.values()].some(
        (item) => item.session.hasRetained() || item.policy.hasRetained(),
      ),
    dispose() {
      globalPolicy.dispose();
      catalogs.forEach((item) => item.dispose());
      catalogs.clear();
      sessions.forEach((item) => {
        item.session.dispose();
        item.policy.dispose();
      });
      sessions.clear();
      emit({ active: false, selected: '', message: '' });
    },
  };
}
export type McpConnections = ReturnType<typeof createMcpConnections>;

/** What a saved server's row needs to run its connection commands. */
export type McpServerRuntime = McpRuntimeIO & { owner: McpConnections };
type RowServer = {
  server_id: string;
  name: string;
  enabled: boolean | null;
  runtime_status: string | null;
  connection_present: boolean | null;
};
const idle = {
  subscribe: () => () => undefined,
  getSnapshot: () => null,
};
const FAILED = new Set([
  'error',
  'failed',
  'dependency_missing',
  'sdk_missing',
]);

/**
 * A saved server's status in words and its one primary action, from its
 * state (B262): Disconnect, Retry, Turn on & connect or Connect. The action
 * is the same reviewed command as in the server's details; `onSettled` runs
 * whenever one of its commands settles, here or there, so the list can read
 * the new state.
 */
export function useServerAction(
  runtime: McpServerRuntime | undefined,
  server: RowServer,
  mcpEnabled: boolean | null,
  onSettled: () => void,
): {
  status: { tone: Tone; label: string; pulse?: boolean };
  primary: {
    label: string;
    tone: 'retry' | 'primary' | 'quiet';
    disabled: boolean;
    run: () => void;
  } | null;
  /** A problem from the last command, in plain words, for under the row. */
  message: string;
  test: (() => void) | null;
} {
  const owner = runtime?.owner;
  useSyncExternalStore(
    owner?.subscribe ?? idle.subscribe,
    owner?.getSnapshot ?? idle.getSnapshot,
  );
  const session = owner?.peek(server.server_id);
  const state = useSyncExternalStore(
    session?.subscribe ?? idle.subscribe,
    session?.getSnapshot ?? idle.getSnapshot,
  );
  // A command settles, from this row or the server's details: read the list.
  const settling = Boolean(
    state?.launch.busy ||
    state?.launch.reviewed ||
    state?.cleanup.busy ||
    state?.cleanup.reviewed,
  );
  const settled = useRef(onSettled);
  const wasSettling = useRef(settling);
  useEffect(() => {
    settled.current = onSettled;
  });
  useEffect(() => {
    if (wasSettling.current && !settling) settled.current();
    wasSettling.current = settling;
  }, [settling]);
  const started = useRef<'connect' | 'disconnect' | 'test' | null>(null);
  const start = (operation: 'connect' | 'disconnect' | 'test') => {
    if (!runtime) return;
    const target = runtime.owner.runtime(server.server_id, server.name);
    if (!target) return;
    started.current = operation;
    void runtimeActions(target, runtime).run(operation);
  };
  const recheck = (slot: 'launch' | 'cleanup') => {
    if (!runtime || !session) return;
    void runtimeActions(session, runtime).submit(
      slot,
      session.getSnapshot()[slot].pending,
    );
  };
  const busy = Boolean(state?.launch.busy || state?.cleanup.busy);
  const testing =
    state?.launch.busy &&
    (state.launch.pending?.command.payload.operation ?? started.current) ===
      'test';
  const slot = state?.cleanup.message ? state.cleanup : state?.launch;
  const message =
    slot?.message && (slot.pending || !RUNTIME_DONE.has(slot.message))
      ? slot.message
      : '';
  const connected = server.connection_present === true;
  const on = mcpEnabled !== false && server.enabled === true;
  const status: { tone: Tone; label: string; pulse?: boolean } = testing
    ? { tone: 'info', label: 'Testing', pulse: true }
    : state?.launch.busy || server.runtime_status === 'connecting'
      ? { tone: 'info', label: 'Connecting', pulse: true }
      : state?.cleanup.busy || server.runtime_status === 'stopping'
        ? { tone: 'info', label: 'Disconnecting', pulse: true }
        : connected
          ? { tone: 'success', label: 'Connected' }
          : FAILED.has(server.runtime_status ?? '')
            ? { tone: 'danger', label: 'Couldn’t connect' }
            : on
              ? { tone: 'neutral', label: 'On · not connected' }
              : server.enabled === null
                ? { tone: 'neutral', label: 'Status unknown' }
                : { tone: 'neutral', label: 'Off' };
  const primary = !runtime
    ? null
    : state?.launch.pending
      ? {
          label: 'Check original launch',
          tone: 'primary' as const,
          disabled: busy,
          run: () => recheck('launch'),
        }
      : state?.cleanup.pending
        ? {
            label: 'Check original disconnect',
            tone: 'primary' as const,
            disabled: busy,
            run: () => recheck('cleanup'),
          }
        : connected
          ? {
              label: 'Disconnect',
              tone: 'quiet' as const,
              disabled: busy,
              run: () => start('disconnect'),
            }
          : {
              label: FAILED.has(server.runtime_status ?? '')
                ? 'Retry'
                : on
                  ? 'Connect'
                  : 'Turn on & connect',
              tone: FAILED.has(server.runtime_status ?? '')
                ? ('retry' as const)
                : ('primary' as const),
              disabled: busy || server.runtime_status === 'connecting',
              run: () => start('connect'),
            };
  return {
    status,
    primary,
    message,
    test: runtime && !connected && !busy ? () => start('test') : null,
  };
}

function TestedCatalogs({
  owner,
  session,
  props,
}: {
  owner: McpConnections;
  session: McpRuntimeSession;
  props: Omit<McpCatalogAcceptanceProps, 'session'>;
}) {
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const current = state.testedCommandId
    ? owner.catalog(state.serverId, state.testedCommandId)
    : null;
  return (
    <>
      {state.testedCommandId && !current && (
        <p role="status">
          Finish or recover retained tool catalog reviews before opening this
          Test’s tools.
        </p>
      )}
      {owner.catalogs(state.serverId).map((catalog) => (
        <McpCatalogAcceptance
          key={catalog.testCommandId}
          {...props}
          session={catalog}
        />
      ))}
    </>
  );
}

/**
 * A saved server's details in a side drawer (a sheet on a phone, B262):
 * its connection, the tools a Test found, its saved permissions, and Remove
 * server in a Danger zone.
 */
export default function McpConnectionsPanel({
  owner,
  policy,
  catalog,
  onRemove,
  ...props
}: {
  owner: McpConnections;
  policy?: Omit<McpPolicyControlsProps, 'session'>;
  catalog?: Omit<McpCatalogAcceptanceProps, 'session'>;
  /** Asks to remove the server (the same confirmation as its row's ⋯). */
  onRemove?: (serverId: string, name: string) => void;
} & Omit<McpRuntimeControlsProps, 'session'>) {
  const state = useSyncExternalStore(owner.subscribe, owner.getSnapshot);
  const selected = owner.selected();
  return (
    <>
      {state.message && (
        <p role="status" className="settings-mcp-note">
          {state.message}
        </p>
      )}
      <Drawer
        open={Boolean(selected)}
        onOpenChange={(open) => {
          if (!open) owner.close();
        }}
        title={selected?.name ?? 'Server details'}
        closeLabel="Close server details"
        className="settings-mcp-drawer"
      >
        {selected && (
          <div className="settings-mcp-details">
            <McpRuntimeControls
              key={state.selected}
              {...props}
              session={selected.session}
            />
            {catalog && (
              <TestedCatalogs
                owner={owner}
                session={selected.session}
                props={catalog}
              />
            )}
            {policy && (
              <McpPolicyControls
                key={`policy:${state.selected}`}
                {...policy}
                session={selected.policy}
              />
            )}
            {onRemove && (
              <SettingsDangerZone anchor="mcp-remove-server">
                <DangerAction
                  title="Remove server"
                  description="Deletes its settings and saved keys, and stops its connection."
                >
                  <Button
                    variant="danger"
                    onClick={() => onRemove(state.selected, selected.name)}
                  >
                    Remove server…
                  </Button>
                </DangerAction>
              </SettingsDangerZone>
            )}
          </div>
        )}
      </Drawer>
    </>
  );
}
