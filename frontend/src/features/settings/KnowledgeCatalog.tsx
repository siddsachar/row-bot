import { useEffect, useRef, useState, type ReactNode } from 'react';
import { RefreshCw, Search } from 'lucide-react';
import { clientError } from '../../api/errors';
import {
  Button,
  CompactAction,
  EmptyState,
  ErrorState,
  IconButton,
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

// The passive saved-library list (Settings › Documents); the server owns
// paging. Saved memories are browsed in Home › Knowledge (B264).
export function SavedCatalog<P extends SavedPage>({
  title,
  noun,
  load,
  filter,
  renderItems,
  description,
  initialPageSize,
  headless = false,
}: {
  title: string;
  noun: string;
  load: SavedLoader<P>;
  filter: (selected: string, change: (value: string) => void) => ReactNode;
  renderItems: (page: P) => ReactNode;
  description: string;
  initialPageSize?: number;
  /**
   * Inside a settings group (B258): no heading of its own; the reload sits
   * in the search row.
   */
  headless?: boolean;
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
  const [visibleLimit, setVisibleLimit] = useState(
    initialPageSize ?? Number.MAX_SAFE_INTEGER,
  );
  const [reload, setReload] = useState(0);
  const epoch = useRef(0);
  const more = useRef<AbortController | null>(null);
  const typing = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(typing.current), []);
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
    setVisibleLimit(initialPageSize ?? Number.MAX_SAFE_INTEGER);
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
  }, [load, applied, reload, initialPageSize]);

  async function loadMore() {
    if (!page || stale || more.current || page.availability !== 'available')
      return;
    if (initialPageSize && visibleLimit < page.items.length) {
      setVisibleLimit((value) =>
        Math.min(page.items.length, value + initialPageSize),
      );
      return;
    }
    if (!page.next_cursor) return;
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
      if (initialPageSize)
        setVisibleLimit((value) =>
          Math.min(combined.length, value + initialPageSize),
        );
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

  const displayedPage =
    page && initialPageSize
      ? ({ ...page, items: page.items.slice(0, visibleLimit) } as P)
      : page;
  const bufferedItems = Boolean(
    page && initialPageSize && visibleLimit < page.items.length,
  );

  return (
    <div
      className="stack settings-saved-catalog"
      aria-busy={loading || loadingMore}
    >
      {!headless && (
        <header className="settings-owner-heading">
          <div>
            <h3>{title}</h3>
            <p>{description}</p>
          </div>
          <CompactAction
            label={`Reload ${noun}`}
            disabled={loading}
            onClick={() => setReload((value) => value + 1)}
          >
            <RefreshCw size={16} aria-hidden />
          </CompactAction>
        </header>
      )}
      <form
        className="settings-list-toolbar"
        role="search"
        aria-label={`Search ${noun}`}
        onSubmit={(event) => {
          event.preventDefault();
          clearTimeout(typing.current);
          setApplied({ query: draft.trim(), selected: selected.trim() });
        }}
      >
        <label className="settings-inline-search">
          <span className="visually-hidden">Search {noun}</span>
          <Search size={14} aria-hidden />
          <Input
            type="search"
            maxLength={256}
            placeholder={`Search ${noun}`}
            value={draft}
            onChange={(event) => {
              const value = event.target.value;
              setDraft(value);
              clearTimeout(typing.current);
              typing.current = setTimeout(
                () =>
                  setApplied({
                    query: value.trim(),
                    selected: selected.trim(),
                  }),
                350,
              );
            }}
          />
        </label>
        {filter(selected, (value) => {
          setSelected(value);
          clearTimeout(typing.current);
          setApplied({ query: draft.trim(), selected: value.trim() });
        })}
        {headless && (
          <IconButton
            size="sm"
            label={`Reload ${noun}`}
            disabled={loading}
            onClick={() => setReload((value) => value + 1)}
          >
            <RefreshCw size={14} aria-hidden />
          </IconButton>
        )}
      </form>
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
              : initialPageSize
                ? `Showing ${Math.min(page.items.length, visibleLimit).toLocaleString()} of ${page.total.toLocaleString()}`
                : `${page.total.toLocaleString()} matching saved entries`}
          </p>
          {earlierCount > 0 && (
            <p role="status">
              {earlierCount.toLocaleString()} earlier loaded entries are outside
              this view. Reload {noun} to return to the start.
            </p>
          )}
          {!page.items.length &&
            (applied.query || applied.selected ? (
              <EmptyState title={`No matching ${noun}`}>
                Try another search or filter.
              </EmptyState>
            ) : (
              // Nothing saved yet: not a search that missed.
              <EmptyState title={`No ${noun} yet`}>
                What you add shows here.
              </EmptyState>
            ))}
          {displayedPage && renderItems(displayedPage)}
          {(page.next_cursor || bufferedItems) && (
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
