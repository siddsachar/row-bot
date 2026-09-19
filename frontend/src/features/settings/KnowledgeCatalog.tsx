import { useEffect, useRef, useState, type ReactNode } from 'react';
import {
  BookOpen,
  ChevronDown,
  History,
  ScrollText,
  Trash2,
} from 'lucide-react';
import type {
  EntitySummaryPage,
  KnowledgeSettingsSnapshot,
} from '../../api/types';
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
  initialPageSize,
}: {
  title: string;
  noun: string;
  load: SavedLoader<P>;
  filter: (selected: string, change: (value: string) => void) => ReactNode;
  renderItems: (page: P) => ReactNode;
  description: string;
  initialPageSize?: number;
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
    <div className="stack" aria-busy={loading || loadingMore}>
      <h2>{title}</h2>
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
          {!page.items.length && (
            <EmptyState title={`No matching ${noun}`}>
              Try another search or filter.
            </EmptyState>
          )}
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

export default function KnowledgeCatalog({
  load,
  onOpen,
  snapshot,
}: {
  load: SavedLoader<EntitySummaryPage>;
  onOpen?: (id: string) => void;
  snapshot?: KnowledgeSettingsSnapshot;
}) {
  return (
    <div className="stack settings-knowledge-page">
      {snapshot && <KnowledgeGraphSummary snapshot={snapshot} />}
      {snapshot && (
        <>
          <KnowledgeWikiIntent />
          <KnowledgeLifecycleSummary snapshot={snapshot} />
        </>
      )}
      <SavedCatalog
        title="Stored Knowledge"
        noun="knowledge"
        load={load}
        description="Browse saved knowledge. Semantic search readiness is unknown. Search matches saved text, including descriptions, aliases and tags."
        filter={(selected, change) =>
          snapshot?.entity_types.length ? (
            <Field label="Category" hint="Exact saved entity type">
              <select
                className="input select"
                value={selected}
                onChange={(event) => change(event.target.value)}
              >
                <option value="">All categories</option>
                {snapshot.entity_types.map((item) => (
                  <option key={item.kind} value={item.kind}>
                    {item.kind}
                  </option>
                ))}
              </select>
            </Field>
          ) : (
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
          )
        }
        initialPageSize={snapshot ? 25 : undefined}
        renderItems={(page) => (
          <ul className="settings-results settings-knowledge-results">
            {page.items.map((item) => (
              <li className="settings-knowledge-result" key={item.id}>
                <details>
                  <summary
                    aria-label={`${item.subject || 'Untitled knowledge'} · ${item.entity_type}`}
                  >
                    <strong>{item.subject || 'Untitled knowledge'}</strong>
                    <span className="status-chip">{item.entity_type}</span>
                    <ChevronDown
                      className="settings-disclosure-chevron"
                      size={17}
                      aria-hidden
                    />
                  </summary>
                  <div className="settings-knowledge-result-detail">
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
                  </div>
                </details>
              </li>
            ))}
          </ul>
        )}
      />
      {snapshot && <KnowledgeAuditAndDanger />}
    </div>
  );
}

function KnowledgeGraphSummary({
  snapshot,
}: {
  snapshot: KnowledgeSettingsSnapshot;
}) {
  const memoryStatus = !snapshot.memory_available
    ? 'Memory unavailable'
    : snapshot.memory_enabled === true
      ? 'Memory enabled'
      : snapshot.memory_enabled === false
        ? 'Memory disabled'
        : 'Memory state unavailable';
  const statusClass =
    snapshot.availability === 'available' && snapshot.memory_available
      ? snapshot.memory_enabled === false
        ? 'status-chip warning'
        : 'status-chip success'
      : 'status-chip warning';

  return (
    <section
      className="settings-snapshot-section stack"
      aria-label="Memory graph summary"
    >
      <div className="settings-owner-heading">
        <div>
          <h2>Memory graph</h2>
          <p>
            Conversation and document entities used for recall and relationship
            browsing.
          </p>
        </div>
        <span className={statusClass}>{memoryStatus}</span>
      </div>
      {snapshot.availability === 'missing' ? (
        <p>No saved memory graph has been created yet.</p>
      ) : snapshot.availability === 'unavailable' ? (
        <p role="status">Saved memory graph statistics are unavailable.</p>
      ) : (
        <>
          <div
            className="settings-summary-strip"
            role="group"
            aria-label="Graph totals"
          >
            <span className="status-chip">
              {snapshot.entities.toLocaleString()} entities
            </span>
            <span className="status-chip">
              {snapshot.relations.toLocaleString()} relations
            </span>
          </div>
          {snapshot.entity_types.length > 0 && (
            <p className="settings-help">
              Types:{' '}
              {snapshot.entity_types
                .map((item) => `${item.kind}: ${item.count.toLocaleString()}`)
                .join(', ')}
            </p>
          )}
          <dl className="settings-facts">
            <div className="settings-fact">
              <dt>Connected components</dt>
              <dd>{snapshot.connected_components.toLocaleString()}</dd>
            </div>
            <div className="settings-fact">
              <dt>Largest component</dt>
              <dd>{snapshot.largest_component.toLocaleString()} entities</dd>
            </div>
            <div className="settings-fact">
              <dt>Isolated entities</dt>
              <dd>{snapshot.isolated_entities.toLocaleString()}</dd>
            </div>
          </dl>
        </>
      )}
    </section>
  );
}

function KnowledgeWikiIntent() {
  return (
    <section
      className="settings-snapshot-section stack settings-knowledge-wiki"
      aria-labelledby="settings-knowledge-wiki"
    >
      <header className="settings-snapshot-heading">
        <BookOpen size={18} aria-hidden />
        <div>
          <h3 id="settings-knowledge-wiki">Wiki vault</h3>
          <p>
            Publish saved knowledge into the configured local Markdown vault.
          </p>
        </div>
      </header>
      <div className="settings-control-actions">
        <a className="button secondary" href="/settings/wiki">
          Open Wiki settings
        </a>
      </div>
      <p className="settings-help">
        Vault configuration, saved counts, sync review, and rebuild controls
        remain on the dedicated Wiki route.
      </p>
    </section>
  );
}

function KnowledgeLifecycleSummary({
  snapshot,
}: {
  snapshot: KnowledgeSettingsSnapshot;
}) {
  const counts = snapshot.status_counts;
  if (!counts) return null;
  return (
    <div
      className="settings-summary-strip settings-knowledge-lifecycle"
      role="group"
      aria-label="Knowledge lifecycle totals"
    >
      <span className="status-chip success">
        {(counts.active ?? 0).toLocaleString()} active
      </span>
      <span className="status-chip warning">
        {(counts.needs_review ?? 0).toLocaleString()} needs review
      </span>
      <span className="status-chip">
        {(counts.superseded ?? 0).toLocaleString()} superseded
      </span>
      <span className="status-chip">
        {(counts.archived ?? 0).toLocaleString()} archived
      </span>
    </div>
  );
}

function KnowledgeAuditAndDanger() {
  const unavailable =
    'This Settings client has no bounded reviewed owner for this operation.';
  return (
    <div className="stack settings-knowledge-audit">
      <details>
        <summary>
          <History size={17} aria-hidden /> Recent recall decisions
        </summary>
        <p className="settings-help">
          Recall decision history is not exposed by the bounded Settings read
          owner.
        </p>
      </details>
      <details>
        <summary>
          <ScrollText size={17} aria-hidden /> Memory change log
        </summary>
        <p className="settings-help">
          Memory audit history is not exposed by the bounded Settings read
          owner.
        </p>
      </details>
      <section
        className="settings-snapshot-section stack is-danger settings-knowledge-danger"
        aria-labelledby="settings-knowledge-danger"
      >
        <header className="settings-snapshot-heading">
          <Trash2 size={18} aria-hidden />
          <div>
            <h3 id="settings-knowledge-danger">Danger Zone</h3>
            <p>Permanent actions affecting the complete knowledge store.</p>
          </div>
        </header>
        <div className="settings-control-actions">
          <Button variant="danger" disabled title={unavailable}>
            Delete all knowledge
          </Button>
        </div>
        <p className="settings-help">
          Delete all knowledge is unavailable here because a reviewed,
          receipt-backed knowledge-wide command owner has not been migrated.
        </p>
      </section>
    </div>
  );
}
