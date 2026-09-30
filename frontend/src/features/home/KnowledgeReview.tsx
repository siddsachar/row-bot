import { useEffect, useState } from 'react';
import { Archive, Check, Pencil } from 'lucide-react';
import type { EntitySummary, EntitySummaryPage } from '../../api/types';
import { clientError } from '../../api/errors';
import { Button, IconButton } from '../../ui/primitives';
import { humanizeToken } from '../../ui/format';
import { typeSlot } from './knowledge-palette';
import { When, plural } from './home-format';

export type KnowledgeLifecycleAction =
  'knowledge.archive' | 'knowledge.restore' | 'knowledge.resolve';

/**
 * Memories Row-Bot was unsure about, from the whole saved library (not only
 * the part the map shows): open, edit, keep or archive each one (B264).
 */
export default function KnowledgeReview({
  load,
  loadRevision,
  refreshKey,
  onOpen,
  onEdit,
  onLifecycle,
}: {
  /** One page of the memories that need review. */
  load: (cursor?: string, signal?: AbortSignal) => Promise<EntitySummaryPage>;
  /** The memory's current revision, which a change is reviewed against. */
  loadRevision: (id: string) => Promise<string>;
  /** Changes when saved knowledge changes elsewhere; the queue reads again. */
  refreshKey: string;
  onOpen: (memory: EntitySummary) => void;
  onEdit: (id: string) => void;
  onLifecycle: (
    id: string,
    revision: string,
    action: KnowledgeLifecycleAction,
    subject: string,
  ) => Promise<boolean>;
}) {
  const [page, setPage] = useState<EntitySummaryPage | null>(null);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [busy, setBusy] = useState('');
  const [loadingMore, setLoadingMore] = useState(false);

  useEffect(() => {
    const abort = new AbortController();
    setError('');
    load(undefined, abort.signal).then(
      (value) => {
        if (!abort.signal.aborted) setPage(value);
      },
      (cause: unknown) => {
        if (!abort.signal.aborted) setError(clientError(cause).message);
      },
    );
    return () => abort.abort();
  }, [load, refreshKey, reload]);

  async function more() {
    if (!page?.next_cursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const next = await load(page.next_cursor);
      // The queue changed underneath: start again from the top.
      if (next.revision !== page.revision) setReload((value) => value + 1);
      else setPage({ ...next, items: [...page.items, ...next.items] });
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setLoadingMore(false);
    }
  }

  async function change(item: EntitySummary, action: KnowledgeLifecycleAction) {
    setBusy(item.id);
    try {
      const revision = await loadRevision(item.id);
      if (await onLifecycle(item.id, revision, action, item.subject))
        setReload((value) => value + 1);
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy('');
    }
  }

  const items = page?.availability === 'available' ? page.items : [];
  return (
    <section
      className="knowledge-review"
      aria-labelledby="knowledge-review-title"
      aria-busy={!page && !error}
    >
      <header className="knowledge-view-head">
        <h3 id="knowledge-review-title">Needs review</h3>
        <p>
          {page?.availability === 'available' && page.total
            ? `${plural(page.total, 'memory', 'memories')} Row-Bot was unsure about. Mark each one as reviewed, edit it or archive it.`
            : 'Memories Row-Bot was unsure about appear here.'}
        </p>
      </header>
      {!page && !error && (
        <p className="home-caption" role="status">
          Loading memories that need review…
        </p>
      )}
      {error && (
        <div className="task-builder-alert" role="alert">
          <strong>The review queue could not be read</strong>
          <p>{error}</p>
          <Button
            className="small"
            onClick={() => setReload((value) => value + 1)}
          >
            Try again
          </Button>
        </div>
      )}
      {page && page.availability !== 'available' && (
        <p className="home-caption" role="status">
          The saved knowledge could not be read.
        </p>
      )}
      {page?.availability === 'available' && items.length === 0 && (
        <p className="knowledge-view-empty" role="status">
          <strong>Nothing needs review</strong>
          <span>Every saved memory is settled.</span>
        </p>
      )}
      {items.length > 0 && (
        <ul className="knowledge-review-list">
          {items.map((item) => (
            <li key={item.id}>
              <div
                className="knowledge-review-row"
                role="group"
                aria-label={item.subject}
              >
                <button
                  type="button"
                  className="knowledge-review-open"
                  onClick={() => onOpen(item)}
                >
                  <span
                    className="knowledge-type-dot"
                    data-slot={typeSlot(item.entity_type)}
                    aria-hidden
                  />
                  <span className="knowledge-review-subject">
                    {item.subject}
                  </span>
                  <span className="knowledge-review-meta">
                    {humanizeToken(item.entity_type)} ·{' '}
                    <When value={item.updated_at} fallback="" />
                  </span>
                </button>
                <span className="knowledge-review-actions">
                  <IconButton
                    size="sm"
                    label="Mark as reviewed"
                    disabled={Boolean(busy)}
                    onClick={() => void change(item, 'knowledge.resolve')}
                  >
                    <Check size={14} aria-hidden />
                  </IconButton>
                  <IconButton
                    size="sm"
                    label="Edit memory"
                    disabled={Boolean(busy)}
                    onClick={() => onEdit(item.id)}
                  >
                    <Pencil size={14} aria-hidden />
                  </IconButton>
                  <IconButton
                    size="sm"
                    label="Archive memory"
                    disabled={Boolean(busy)}
                    onClick={() => void change(item, 'knowledge.archive')}
                  >
                    <Archive size={14} aria-hidden />
                  </IconButton>
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}
      {page?.next_cursor && (
        <Button
          className="small knowledge-view-more"
          disabled={loadingMore}
          onClick={() => void more()}
        >
          {loadingMore ? 'Loading more…' : 'Load more'}
        </Button>
      )}
    </section>
  );
}
