import { useSyncExternalStore } from 'react';
import { Button } from '../../ui/primitives';
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
  createMcpRuntimeSession,
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
    select(server: string, name: string) {
      if (!state.active || !/^[a-f0-9]{64}$/.test(server)) return;
      if (!sessions.has(server)) {
        if (sessions.size >= capacity) {
          const disposable = [...sessions.entries()].find(
            ([id, item]) =>
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
            return;
          }
          disposable[1].session.dispose();
          disposable[1].policy.dispose();
          sessions.delete(disposable[0]);
        }
        sessions.set(server, {
          name,
          session: createMcpRuntimeSession(server),
          policy: createMcpPolicySession(server),
        });
      }
      emit({ selected: server, message: '' });
    },
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
export default function McpConnectionsPanel({
  owner,
  policy,
  catalog,
  ...props
}: {
  owner: McpConnections;
  policy?: Omit<McpPolicyControlsProps, 'session'>;
  catalog?: Omit<McpCatalogAcceptanceProps, 'session'>;
} & Omit<McpRuntimeControlsProps, 'session'>) {
  const state = useSyncExternalStore(owner.subscribe, owner.getSnapshot);
  const selected = owner.selected();
  return (
    <section className="stack" aria-label="MCP connections">
      {policy && <McpPolicyControls {...policy} session={owner.globalPolicy} />}
      {state.message && <p role="status">{state.message}</p>}
      {selected && (
        <>
          <h2>Connection: {selected.name}</h2>
          <div className="actions" aria-label="Retained connection controls">
            {owner.entries().map(([id, item]) => (
              <Button
                key={id}
                aria-pressed={id === state.selected}
                onClick={() => owner.select(id, item.name)}
              >
                {item.name}
              </Button>
            ))}
          </div>
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
        </>
      )}
    </section>
  );
}
