import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { EntitySummaryPage } from '../../api/types';
import { clientError } from '../../api/errors';
import {
  Button,
  EmptyState,
  ErrorState,
  Field,
  Input,
  Skeleton,
} from '../../ui/primitives';

type SavedPage = {
  revision: string;
  items: { id: string }[];
  next_cursor: string | null;
  total: number | null;
  availability: 'available' | 'missing' | 'unavailable';
};
export type SavedLoader<P> = (
  query?: string,
  selected?: string,
  cursor?: string,
  signal?: AbortSignal,
) => Promise<P>;

// Shared only by the two passive saved-library views; the server owns paging.
export function SavedCatalog<P extends SavedPage>({
  title,
  noun,
  load,
  filter,
  renderItems,
  description,
}: {
  title: string;
  noun: string;
  load: SavedLoader<P>;
  filter: (selected: string, change: (value: string) => void) => ReactNode;
  renderItems: (page: P) => ReactNode;
  description: string;
}) {
  const [draft, setDraft] = useState('');
  const [selected, setSelected] = useState('');
  const [applied, setApplied] = useState({ query: '', selected: '' });
  const [page, setPage] = useState<P | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');
  const [stale, setStale] = useState(false);
  const [earlierCount, setEarlierCount] = useState(0);
  const [reload, setReload] = useState(0);
  const epoch = useRef(0);
  const more = useRef<AbortController | null>(null);
  const changedMessage = `The saved ${noun} changed. Reload to continue.`;

  useEffect(() => {
    const abort = new AbortController();
    const ticket = ++epoch.current;
    more.current?.abort();
    more.current = null;
    setPage(null);
    setError('');
    setStale(false);
    setEarlierCount(0);
    setLoading(true);
    setLoadingMore(false);
    load(
      applied.query,
      applied.selected || undefined,
      undefined,
      abort.signal,
    ).then(
      (value) => {
        if (!abort.signal.aborted && ticket === epoch.current) {
          setPage(value);
          setLoading(false);
        }
      },
      (cause: unknown) => {
        if (!abort.signal.aborted && ticket === epoch.current) {
          setError(clientError(cause).message);
          setLoading(false);
        }
      },
    );
    return () => {
      abort.abort();
      more.current?.abort();
    };
  }, [load, applied, reload]);

  async function loadMore() {
    if (
      !page?.next_cursor ||
      stale ||
      more.current ||
      page.availability !== 'available'
    )
      return;
    const abort = new AbortController();
    const ticket = epoch.current;
    more.current = abort;
    setLoadingMore(true);
    setError('');
    try {
      const next = await load(
        applied.query,
        applied.selected || undefined,
        page.next_cursor,
        abort.signal,
      );
      if (abort.signal.aborted || ticket !== epoch.current) return;
      if (
        next.revision !== page.revision ||
        next.availability !== 'available'
      ) {
        setStale(true);
        setError(changedMessage);
        return;
      }
      const combined = [...page.items, ...next.items];
      const removed = Math.max(0, combined.length - 200);
      setEarlierCount((value) => value + removed);
      setPage({ ...next, items: combined.slice(-200) });
    } catch (cause) {
      if (!abort.signal.aborted && ticket === epoch.current) {
        const failure = clientError(cause);
        if (failure.code === 'cursor_expired') {
          setStale(true);
          setError(changedMessage);
        } else setError(failure.message);
      }
    } finally {
      if (more.current === abort) more.current = null;
      if (!abort.signal.aborted && ticket === epoch.current)
        setLoadingMore(false);
    }
  }

  return (
    <div className="stack" aria-busy={loading || loadingMore}>
      <h1>{title}</h1>
      <p>{description}</p>
      <form
        className="field-row"
        onSubmit={(event) => {
          event.preventDefault();
          setApplied({ query: draft.trim(), selected: selected.trim() });
        }}
      >
        <Field label={`Search ${noun}`}>
          <Input
            type="search"
            maxLength={256}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
        </Field>
        {filter(selected, setSelected)}
        <Button type="submit">Search</Button>
      </form>
      <div>
        <Button
          disabled={loading}
          onClick={() => setReload((value) => value + 1)}
        >
          Reload {noun}
        </Button>
      </div>
      {loading && <Skeleton label={`Loading saved ${noun}`} />}
      {error && (
        <ErrorState title={`${title} could not be loaded`}>{error}</ErrorState>
      )}
      {page?.availability === 'missing' && (
        <EmptyState title={`No saved ${noun} store`}>
          A saved library has not been created yet.
        </EmptyState>
      )}
      {page?.availability === 'unavailable' && (
        <ErrorState title={`Saved ${noun} unavailable`}>
          The saved library could not be read. Reload to try again.
        </ErrorState>
      )}
      {page?.availability === 'available' && (
        <>
          <p role="status">
            {page.total == null
              ? 'Matching count unknown'
              : `${page.total.toLocaleString()} matching saved entries`}
          </p>
          {earlierCount > 0 && (
            <p role="status">
              {earlierCount.toLocaleString()} earlier loaded entries are outside
              this view. Reload {noun} to return to the start.
            </p>
          )}
          {!page.items.length && (
            <EmptyState title={`No matching ${noun}`}>
              Try another search or filter.
            </EmptyState>
          )}
          {renderItems(page)}
          {page.next_cursor && (
            <Button
              disabled={loadingMore || stale}
              onClick={() => void loadMore()}
            >
              {loadingMore ? `Loading more ${noun}…` : `Load more ${noun}`}
            </Button>
          )}
        </>
      )}
    </div>
  );
}

export default function KnowledgeCatalog({
  load,
  onOpen,
}: {
  load: SavedLoader<EntitySummaryPage>;
  onOpen?: (id: string) => void;
}) {
  return (
    <SavedCatalog
      title="Knowledge"
      noun="knowledge"
      load={load}
      description="Browse saved knowledge. Semantic search readiness is unknown. Search matches saved text, including descriptions, aliases and tags."
      filter={(selected, change) => (
        <Field
          label="Entity type"
          hint="Exact type, or leave blank for all types"
        >
          <Input
            maxLength={64}
            value={selected}
            onChange={(event) => change(event.target.value)}
          />
        </Field>
      )}
      renderItems={(page) => (
        <ul className="settings-results">
          {page.items.map((item) => (
            <li className="surface" key={item.id}>
              <details>
                <summary>
                  {item.subject || 'Untitled knowledge'} · {item.entity_type}
                </summary>
                {item.truncated && (
                  <p className="muted">This saved summary is shortened.</p>
                )}
                <p>{item.description}</p>
                {onOpen && (
                  <Button onClick={() => onOpen(item.id)}>
                    Edit {item.subject || 'knowledge'}
                  </Button>
                )}
                <dl>
                  <dt>Saved identity</dt>
                  <dd>{item.id}</dd>
                  <dt>Saved status</dt>
                  <dd>Saved</dd>
                  <dt>Semantic search readiness</dt>
                  <dd>Unknown</dd>
                  <dt>Last saved update</dt>
                  <dd>{item.updated_at || 'Unknown'}</dd>
                </dl>
              </details>
            </li>
          ))}
        </ul>
      )}
    />
  );
}
