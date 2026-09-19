import { useSyncExternalStore } from 'react';
import { Button } from '../../ui/primitives';
import DocumentRemoval, {
  createDocumentRemovalSession,
  type DocumentRemovalSession,
  type DocumentRemovalTransport,
} from './DocumentRemoval';

export function createDocumentRemovals(
  transport: DocumentRemovalTransport,
  capacity = 8,
) {
  const sessions = new Map<
    string,
    { label: string; session: DocumentRemovalSession }
  >();
  const listeners = new Set<() => void>();
  let state = { selected: '', error: '', revision: 0 },
    disposed = false;
  const emit = (patch: Partial<typeof state> = {}) => {
    state = { ...state, ...patch, revision: state.revision + 1 };
    listeners.forEach((notify) => notify());
  };
  const retained = (session: DocumentRemovalSession) => {
    const current = session.getSnapshot();
    return (
      current.busy ||
      current.pending ||
      !!current.review ||
      (!!current.receipt?.removal &&
        current.receipt.removal.status !== 'complete')
    );
  };
  return {
    getSnapshot: () => state,
    subscribe(notify: () => void) {
      listeners.add(notify);
      return () => {
        listeners.delete(notify);
      };
    },
    entries: () => [...sessions.entries()],
    select(id: string | null, label: string) {
      if (disposed) return;
      const key = id ?? '*';
      if (!sessions.has(key)) {
        if (sessions.size >= capacity) {
          const settled = [...sessions].find(
            ([, row]) => !retained(row.session),
          );
          if (!settled) {
            emit({
              error:
                'Finish or cancel a retained document review before opening another. Unconfirmed removals remain available below.',
            });
            return;
          }
          settled[1].session.purge();
          sessions.delete(settled[0]);
        }
        const session = createDocumentRemovalSession(id, transport, () => {
          if (disposed) throw new Error('authentication_required');
        });
        session.subscribe(() => {
          if (!disposed) emit();
        });
        sessions.set(key, { label, session });
      }
      emit({ selected: key, error: '' });
    },
    hasRetained: () =>
      !disposed && [...sessions.values()].some((row) => retained(row.session)),
    dispose() {
      if (disposed) return;
      disposed = true;
      sessions.forEach((row) => row.session.purge());
      sessions.clear();
      emit({ selected: '', error: '' });
      listeners.clear();
    },
  };
}
export type DocumentRemovals = ReturnType<typeof createDocumentRemovals>;

export default function DocumentRemovalsPanel({
  owner,
}: {
  owner: DocumentRemovals;
}) {
  const state = useSyncExternalStore(owner.subscribe, owner.getSnapshot);
  const entries = owner.entries();
  const current = entries.find(([id]) => id === state.selected)?.[1];
  return (
    <section className="stack" aria-label="Document cleanup controls">
      <Button onClick={() => owner.select(null, 'all saved documents')}>
        Clear documents
      </Button>
      {state.error && <p role="alert">{state.error}</p>}
      {!!entries.length && (
        <div
          className="actions"
          role="group"
          aria-label="Retained document removals"
        >
          {entries.map(([key, row]) => (
            <Button
              key={key}
              onClick={() => owner.select(key === '*' ? null : key, row.label)}
            >
              {row.label}
            </Button>
          ))}
        </div>
      )}
      {current && (
        <DocumentRemoval
          key={state.selected}
          session={current.session}
          label={current.label}
        />
      )}
    </section>
  );
}
