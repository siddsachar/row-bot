import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  Archive,
  ChevronDown,
  Edit3,
  History,
  Network,
  ScrollText,
  Search,
  Trash2,
} from 'lucide-react';
import type {
  EntitySummaryPage,
  KnowledgeEntityDetail,
  KnowledgeMaintenanceReceipt,
  KnowledgeMaintenanceReview,
  KnowledgeMemoryChangePage,
  KnowledgeRecallPage,
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
  Toggle,
} from '../../ui/primitives';
import WikiSettings, {
  type WikiSettingsSession,
} from '../knowledge/WikiSettings';
import type { SettingsMutationIO } from './SettingsSnapshotPanels';

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

type KnowledgeFilters = {
  query: string;
  entityType: string;
  status: string;
  source: string;
  tier: string;
};
export type KnowledgePageLoader = (
  filters: KnowledgeFilters,
  cursor?: string,
  signal?: AbortSignal,
) => Promise<EntitySummaryPage>;
export type KnowledgeMaintenanceIO = {
  review(
    action:
      'knowledge.delete' | 'knowledge.delete.bulk' | 'knowledge.delete_all',
    catalogRevision: string,
    targets: { entity_id: string; revision: string }[],
    signal?: AbortSignal,
  ): Promise<KnowledgeMaintenanceReview>;
  execute(
    review: KnowledgeMaintenanceReview,
    commandId: string,
  ): Promise<KnowledgeMaintenanceReceipt>;
  receipt(
    commandId: string,
    signal?: AbortSignal,
  ): Promise<KnowledgeMaintenanceReceipt>;
};

export default function KnowledgeCatalog({
  load,
  loadFiltered,
  loadDetail,
  loadRecalls,
  loadChangeLog,
  maintenance,
  settingsMutation,
  wikiSession,
  wikiSnapshot,
  onOpen,
  onLifecycle,
  onMutation,
  snapshot,
  refreshToken = 0,
}: {
  load?: SavedLoader<EntitySummaryPage>;
  loadFiltered?: KnowledgePageLoader;
  loadDetail?: (
    id: string,
    signal?: AbortSignal,
  ) => Promise<KnowledgeEntityDetail>;
  loadRecalls?: (signal?: AbortSignal) => Promise<KnowledgeRecallPage>;
  loadChangeLog?: (signal?: AbortSignal) => Promise<KnowledgeMemoryChangePage>;
  maintenance?: KnowledgeMaintenanceIO;
  settingsMutation?: SettingsMutationIO | null;
  wikiSession?: WikiSettingsSession;
  wikiSnapshot?: import('../../api/types').WikiSettingsSnapshot;
  onOpen?: (id: string) => void;
  onLifecycle?: (
    id: string,
    revision: string,
    action: 'knowledge.archive' | 'knowledge.restore' | 'knowledge.resolve',
  ) => void | Promise<void>;
  onMutation?: () => void;
  snapshot?: KnowledgeSettingsSnapshot;
  refreshToken?: number;
}) {
  const loader = useMemo<KnowledgePageLoader | null>(() => {
    if (loadFiltered) return loadFiltered;
    if (!load) return null;
    return (filters, cursor, signal) =>
      load(filters.query, filters.entityType || undefined, cursor, signal);
  }, [load, loadFiltered]);
  const [draftQuery, setDraftQuery] = useState('');
  const [filters, setFilters] = useState<KnowledgeFilters>({
    query: '',
    entityType: '',
    status: '',
    source: '',
    tier: '',
  });
  const [page, setPage] = useState<EntitySummaryPage | null>(null);
  const [catalogRevision, setCatalogRevision] = useState('');
  const [reviewItems, setReviewItems] = useState<EntitySummaryPage['items']>(
    [],
  );
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [details, setDetails] = useState<Record<string, KnowledgeEntityDetail>>(
    {},
  );
  const [detailLoading, setDetailLoading] = useState<Record<string, boolean>>(
    {},
  );
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [lifecyclePending, setLifecyclePending] = useState(false);
  const lifecycleBusy = useRef(false);
  const [maintenanceReview, setMaintenanceReview] =
    useState<KnowledgeMaintenanceReview | null>(null);
  const [maintenancePending, setMaintenancePending] = useState<{
    commandId: string;
  } | null>(null);
  const [maintenanceResult, setMaintenanceResult] =
    useState<KnowledgeMaintenanceReceipt | null>(null);
  const [memoryCommand, setMemoryCommand] = useState<{
    commandId: string;
    request: Parameters<SettingsMutationIO['execute']>[0];
    review: Parameters<SettingsMutationIO['execute']>[1];
  } | null>(null);
  const [memoryPending, setMemoryPending] = useState(false);
  const memoryBusy = useRef(false);
  const epoch = useRef(0);

  useEffect(() => {
    const timer = window.setTimeout(
      () => setFilters((value) => ({ ...value, query: draftQuery.trim() })),
      300,
    );
    return () => window.clearTimeout(timer);
  }, [draftQuery]);

  useEffect(() => {
    if (!loader) return;
    const abort = new AbortController();
    const ticket = ++epoch.current;
    setLoading(true);
    setError('');
    setSelected({});
    setDetails({});
    const unfiltered: KnowledgeFilters = {
      query: '',
      entityType: '',
      status: '',
      source: '',
      tier: '',
    };
    const isUnfiltered = Object.values(filters).every((value) => !value);
    const reviewLoad = loadFiltered
      ? loader(
          { ...unfiltered, status: 'needs_review' },
          undefined,
          abort.signal,
        )
      : Promise.resolve(null);
    const catalogLoad = !isUnfiltered
      ? loader(unfiltered, undefined, abort.signal)
      : Promise.resolve(null);
    void Promise.all([
      loader(filters, undefined, abort.signal),
      reviewLoad,
      catalogLoad,
    ]).then(
      ([value, review, catalog]) => {
        if (abort.signal.aborted || ticket !== epoch.current) return;
        setPage(value);
        setCatalogRevision((catalog ?? value).revision);
        setReviewItems(review?.items.slice(0, 5) ?? []);
        setLoading(false);
      },
      (cause) => {
        if (!abort.signal.aborted && ticket === epoch.current) {
          setError(clientError(cause).message);
          setLoading(false);
        }
      },
    );
    return () => abort.abort();
  }, [filters, loadFiltered, loader, reload, refreshToken]);

  async function ensureDetail(id: string) {
    if (details[id]) return details[id];
    if (!loadDetail) return null;
    setDetailLoading((value) => ({ ...value, [id]: true }));
    try {
      const detail = await loadDetail(id);
      setDetails((value) => ({ ...value, [id]: detail }));
      return detail;
    } catch (cause) {
      setError(clientError(cause).message);
      return null;
    } finally {
      setDetailLoading((value) => ({ ...value, [id]: false }));
    }
  }

  async function applyLifecycle(
    id: string,
    revision: string,
    action: 'knowledge.archive' | 'knowledge.restore' | 'knowledge.resolve',
  ) {
    if (!onLifecycle || lifecycleBusy.current) return;
    lifecycleBusy.current = true;
    setLifecyclePending(true);
    try {
      await onLifecycle(id, revision, action);
      setDetails({});
      setReload((value) => value + 1);
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      lifecycleBusy.current = false;
      setLifecyclePending(false);
    }
  }

  async function more() {
    if (!loader || !page?.next_cursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const next = await loader(filters, page.next_cursor);
      if (next.revision !== page.revision) throw { code: 'cursor_expired' };
      setPage({ ...next, items: [...page.items, ...next.items].slice(-200) });
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setLoadingMore(false);
    }
  }

  async function beginDelete(
    action: KnowledgeMaintenanceReview['action'],
    targets: { entity_id: string; revision: string }[],
  ) {
    if (!maintenance || !catalogRevision || maintenancePending) return;
    setError('');
    try {
      setMaintenanceReview(
        await maintenance.review(action, catalogRevision, targets),
      );
      setMaintenanceResult(null);
    } catch (cause) {
      setError(clientError(cause).message);
    }
  }

  async function applyDelete() {
    if (!maintenance || !maintenanceReview || maintenancePending) return;
    const commandId = crypto.randomUUID();
    setMaintenancePending({ commandId });
    try {
      const result = await maintenance.execute(maintenanceReview, commandId);
      setMaintenanceResult(result);
      setMaintenancePending(null);
      if (result.status === 'completed') {
        setMaintenanceReview(null);
        setSelected({});
        setReload((value) => value + 1);
        onMutation?.();
      }
    } catch {
      setError(
        'The deletion outcome is unconfirmed. Check the original receipt before trying again.',
      );
    }
  }

  async function checkDeleteReceipt() {
    if (!maintenance || !maintenancePending) return;
    try {
      const result = await maintenance.receipt(maintenancePending.commandId);
      setMaintenanceResult(result);
      setMaintenancePending(null);
      if (result.status === 'completed') {
        setMaintenanceReview(null);
        setSelected({});
        setReload((value) => value + 1);
        onMutation?.();
      }
    } catch {
      setError(
        'The deletion outcome is still unconfirmed. No action was repeated.',
      );
    }
  }

  async function reviewMemory(enabled: boolean) {
    if (
      !settingsMutation ||
      !snapshot ||
      memoryPending ||
      memoryCommand ||
      memoryBusy.current
    )
      return;
    memoryBusy.current = true;
    const request = {
      settings_revision: settingsMutation.revision,
      page: 'knowledge' as const,
      field: 'memory_enabled',
      value: enabled,
    };
    let admitted = false;
    setMemoryPending(true);
    try {
      const review = await settingsMutation.review(request);
      const captured = { commandId: crypto.randomUUID(), request, review };
      setMemoryCommand(captured);
      admitted = true;
      const receipt = await settingsMutation.execute(
        captured.request,
        captured.review,
        captured.commandId,
      );
      if (receipt.status === 'completed' && receipt.snapshot) {
        settingsMutation.onSnapshot(receipt.snapshot);
        setMemoryCommand(null);
      } else
        setError(
          'The memory setting outcome is uncertain. Check its original receipt before retrying.',
        );
    } catch (cause) {
      setError(
        admitted
          ? 'The memory setting outcome is uncertain. Check its original receipt before retrying.'
          : clientError(cause).message,
      );
    } finally {
      memoryBusy.current = false;
      setMemoryPending(false);
    }
  }

  async function checkMemoryReceipt() {
    if (!settingsMutation || !memoryCommand || memoryPending) return;
    setMemoryPending(true);
    try {
      const receipt = await settingsMutation.receipt(memoryCommand.commandId);
      if (receipt.status === 'completed' && receipt.snapshot) {
        settingsMutation.onSnapshot(receipt.snapshot);
        setMemoryCommand(null);
        setError('');
      } else
        setError(
          'The memory setting outcome is still uncertain. No action was repeated.',
        );
    } catch {
      setError(
        'The memory setting outcome is still uncertain. No action was repeated.',
      );
    } finally {
      setMemoryPending(false);
    }
  }

  const selectedTargets = Object.entries(selected).map(
    ([entity_id, revision]) => ({ entity_id, revision }),
  );
  return (
    <div
      className="stack settings-knowledge-page"
      aria-busy={loading || loadingMore || lifecyclePending}
    >
      {snapshot && (
        <KnowledgeGraphSummary
          snapshot={snapshot}
          disabled={
            !settingsMutation || memoryPending || Boolean(memoryCommand)
          }
          onToggle={(enabled) => void reviewMemory(enabled)}
        />
      )}
      {memoryCommand && (
        <Button
          disabled={memoryPending}
          onClick={() => void checkMemoryReceipt()}
        >
          Check original memory setting receipt
        </Button>
      )}
      {wikiSession && (
        <WikiSettings compact session={wikiSession} snapshot={wikiSnapshot} />
      )}
      {snapshot && <KnowledgeLifecycleSummary snapshot={snapshot} />}

      <section
        className="stack settings-knowledge-catalog"
        aria-labelledby="stored-knowledge-heading"
      >
        <header className="settings-owner-heading">
          <div>
            <h2 id="stored-knowledge-heading">Stored Knowledge</h2>
            <p>
              Browse, review, filter, and manage saved memory without provider
              or network work.
            </p>
          </div>
          <Button
            onClick={() => {
              setSelecting((value) => !value);
              setSelected({});
            }}
          >
            {selecting ? 'Done' : 'Select'}
          </Button>
        </header>
        <div className="settings-knowledge-filters">
          <Field label="Category">
            <select
              className="input select"
              value={filters.entityType}
              onChange={(event) =>
                setFilters((value) => ({
                  ...value,
                  entityType: event.target.value,
                }))
              }
            >
              <option value="">All categories</option>
              {snapshot?.entity_types.map((item) => (
                <option key={item.kind} value={item.kind}>
                  {item.kind}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Status">
            <select
              className="input select"
              value={filters.status}
              onChange={(event) =>
                setFilters((value) => ({
                  ...value,
                  status: event.target.value,
                }))
              }
            >
              <option value="">All statuses</option>
              <option value="active">Active</option>
              <option value="needs_review">Needs review</option>
              <option value="superseded">Superseded</option>
              <option value="archived">Archived</option>
            </select>
          </Field>
          <Field label="Source">
            <select
              className="input select"
              value={filters.source}
              onChange={(event) =>
                setFilters((value) => ({
                  ...value,
                  source: event.target.value,
                }))
              }
            >
              <option value="">All sources</option>
              <option value="manual">Manual/live</option>
              <option value="extraction">Extraction</option>
              <option value="document">Document</option>
              <option value="wiki">Wiki/dream</option>
              <option value="other">Other</option>
            </select>
          </Field>
          <Field label="Tier">
            <select
              className="input select"
              value={filters.tier}
              onChange={(event) =>
                setFilters((value) => ({ ...value, tier: event.target.value }))
              }
            >
              <option value="">All tiers</option>
              <option value="core">Core</option>
              <option value="semantic">Semantic</option>
              <option value="episodic">Episodic</option>
              <option value="resource">Resource</option>
            </select>
          </Field>
        </div>
        <Field label="Search knowledge">
          <div className="settings-search-control">
            <Search size={17} aria-hidden />
            <Input
              type="search"
              maxLength={256}
              value={draftQuery}
              onChange={(event) => setDraftQuery(event.target.value)}
            />
          </div>
        </Field>
        {reviewItems.length > 0 && (
          <section
            className="settings-knowledge-review-queue stack"
            aria-label="Needs Review"
          >
            <h3>Needs Review</h3>
            {reviewItems.map((item) => (
              <div className="settings-review-row" key={item.id}>
                <strong>{item.subject}</strong>
                <div className="actions">
                  <Button onClick={() => onOpen?.(item.id)}>Edit</Button>
                  <Button
                    disabled={lifecyclePending}
                    onClick={async () => {
                      const detail = await ensureDetail(item.id);
                      if (detail)
                        await applyLifecycle(
                          item.id,
                          detail.revision,
                          'knowledge.resolve',
                        );
                    }}
                  >
                    Resolve
                  </Button>
                  <Button
                    disabled={lifecyclePending}
                    onClick={async () => {
                      const detail = await ensureDetail(item.id);
                      if (detail)
                        await applyLifecycle(
                          item.id,
                          detail.revision,
                          'knowledge.archive',
                        );
                    }}
                  >
                    Archive
                  </Button>
                </div>
              </div>
            ))}
          </section>
        )}
        {selecting && (
          <div
            className="settings-bulk-bar"
            role="group"
            aria-label="Knowledge selection actions"
          >
            <span>{selectedTargets.length} selected · maximum 100</span>
            <Button
              variant="danger"
              disabled={!selectedTargets.length || !maintenance}
              onClick={() =>
                void beginDelete('knowledge.delete.bulk', selectedTargets)
              }
            >
              Delete selected
            </Button>
          </div>
        )}
        {loading && <Skeleton label="Loading saved knowledge" />}
        {error && (
          <ErrorState
            title="Knowledge action needs attention"
            action={
              <Button onClick={() => setReload((value) => value + 1)}>
                Retry
              </Button>
            }
          >
            {error}
          </ErrorState>
        )}
        {page?.availability === 'missing' && (
          <EmptyState title="No saved knowledge store">
            A saved knowledge graph has not been created yet.
          </EmptyState>
        )}
        {page?.availability === 'unavailable' && (
          <ErrorState title="Saved knowledge unavailable">
            The local saved graph could not be read.
          </ErrorState>
        )}
        {page?.availability === 'available' && (
          <>
            <p role="status">
              Showing {page.items.length.toLocaleString()} of{' '}
              {(page.total ?? page.items.length).toLocaleString()} matching
              entries.
            </p>
            {!page.items.length && (
              <EmptyState title="No matching knowledge">
                Try another filter or search.
              </EmptyState>
            )}
            <ul className="settings-results settings-knowledge-results">
              {page.items.map((item) => {
                const detail = details[item.id];
                return (
                  <li
                    className="settings-knowledge-result"
                    key={`${item.id}:${page.revision}`}
                  >
                    <div className="settings-knowledge-row">
                      {selecting && (
                        <input
                          type="checkbox"
                          aria-label={`Select ${item.subject}`}
                          checked={Boolean(selected[item.id])}
                          disabled={
                            !selected[item.id] && selectedTargets.length >= 100
                          }
                          onChange={async (event) => {
                            if (!event.target.checked) {
                              setSelected((value) => {
                                const next = { ...value };
                                delete next[item.id];
                                return next;
                              });
                              return;
                            }
                            const value = await ensureDetail(item.id);
                            if (value?.availability === 'available')
                              setSelected((current) => ({
                                ...current,
                                [item.id]: value.revision,
                              }));
                          }}
                        />
                      )}
                      <details
                        onToggle={(event) => {
                          if (event.currentTarget.open)
                            void ensureDetail(item.id);
                        }}
                      >
                        <summary
                          aria-label={`${item.subject || 'Untitled knowledge'} · ${item.entity_type}`}
                        >
                          <strong>
                            {item.subject || 'Untitled knowledge'}
                          </strong>
                          <span className="status-chip">
                            {item.entity_type}
                          </span>
                          <ChevronDown
                            className="settings-disclosure-chevron"
                            size={17}
                            aria-hidden
                          />
                        </summary>
                        <div className="settings-knowledge-result-detail">
                          {detailLoading[item.id] && (
                            <Skeleton
                              label={`Loading details for ${item.subject}`}
                            />
                          )}
                          {detail && detail.availability === 'available' && (
                            <KnowledgeDetail
                              detail={detail}
                              pending={lifecyclePending}
                              onOpen={onOpen}
                              onLifecycle={(id, revision, action) =>
                                applyLifecycle(id, revision, action)
                              }
                              onDelete={() =>
                                void beginDelete('knowledge.delete', [
                                  {
                                    entity_id: detail.id,
                                    revision: detail.revision,
                                  },
                                ])
                              }
                            />
                          )}
                          {detail && detail.availability !== 'available' && (
                            <p role="status">
                              Details are {detail.availability}.
                            </p>
                          )}
                        </div>
                      </details>
                    </div>
                  </li>
                );
              })}
            </ul>
            {page.next_cursor && (
              <Button disabled={loadingMore} onClick={() => void more()}>
                {loadingMore ? 'Loading more…' : 'Load more'}
              </Button>
            )}
          </>
        )}
      </section>
      {maintenanceReview && (
        <section
          className="settings-reviewed-action stack"
          aria-label="Reviewed knowledge deletion"
        >
          <h3>Reviewed deletion</h3>
          <p>
            {maintenanceReview.entity_count.toLocaleString()}{' '}
            {maintenanceReview.entity_count === 1 ? 'entry' : 'entries'} will be
            removed. Related graph links, local search indexes, and
            Row-Bot-managed Wiki files will be cleaned up; external files are
            preserved.
          </p>
          <div className="actions">
            <Button
              disabled={Boolean(maintenancePending)}
              onClick={() => setMaintenanceReview(null)}
            >
              Cancel
            </Button>
            <Button
              variant="danger"
              disabled={Boolean(maintenancePending)}
              onClick={() => void applyDelete()}
            >
              Confirm permanent deletion
            </Button>
          </div>
        </section>
      )}
      {maintenanceResult?.status !== 'completed' && maintenanceResult && (
        <p role="alert">
          Deletion was {maintenanceResult.status}. Deleted{' '}
          {maintenanceResult.deleted.length}; stale{' '}
          {maintenanceResult.stale.length}; missing{' '}
          {maintenanceResult.missing.length}. Cleanup:{' '}
          {JSON.stringify(maintenanceResult.cleanup)}.
        </p>
      )}
      {maintenancePending && (
        <Button onClick={() => void checkDeleteReceipt()}>
          Check original deletion receipt
        </Button>
      )}
      {snapshot && (
        <KnowledgeAuditAndDanger
          key={reload}
          loadRecalls={loadRecalls}
          loadChangeLog={loadChangeLog}
          canDelete={Boolean(
            maintenance &&
            catalogRevision &&
            page?.availability === 'available' &&
            page.total != null,
          )}
          pending={Boolean(maintenancePending)}
          count={snapshot.entities}
          onDeleteAll={() => void beginDelete('knowledge.delete_all', [])}
        />
      )}
    </div>
  );
}

function KnowledgeDetail({
  detail,
  pending,
  onOpen,
  onLifecycle,
  onDelete,
}: {
  detail: KnowledgeEntityDetail;
  pending: boolean;
  onOpen?: (id: string) => void;
  onLifecycle?: (
    id: string,
    revision: string,
    action: 'knowledge.archive' | 'knowledge.restore' | 'knowledge.resolve',
  ) => void | Promise<void>;
  onDelete(): void;
}) {
  return (
    <div className="stack">
      <div className="settings-summary-strip">
        <span
          className={`status-chip ${detail.status === 'active' ? 'success' : detail.status === 'needs_review' ? 'warning' : ''}`}
        >
          {detail.status.replaceAll('_', ' ')}
        </span>
        <span className="status-chip">{detail.tier}</span>
        <span className="status-chip">{detail.source_bucket}</span>
        {detail.confidence != null && (
          <span className="status-chip">
            {Math.round(detail.confidence * 100)}% confidence
          </span>
        )}
      </div>
      <p className="settings-knowledge-description">{detail.description}</p>
      {detail.aliases.length > 0 && (
        <p>
          <strong>Aliases:</strong> {detail.aliases.join(', ')}
          {detail.alias_count > detail.aliases.length
            ? ` +${detail.alias_count - detail.aliases.length} more`
            : ''}
        </p>
      )}
      {detail.tags.length > 0 && (
        <p>
          <strong>Tags:</strong> {detail.tags.join(', ')}
          {detail.tag_count > detail.tags.length
            ? ` +${detail.tag_count - detail.tags.length} more`
            : ''}
        </p>
      )}
      {detail.relations.length > 0 && (
        <div className="settings-knowledge-relations">
          <strong>Relations</strong>
          {detail.relations.map((relation, index) => {
            const label = `${relation.direction === 'outgoing' ? '→' : '←'} ${relation.relation_type}: ${relation.peer_subject}`;
            return onOpen ? (
              <button
                className="settings-knowledge-relation-link"
                key={`${relation.peer_id}:${index}`}
                type="button"
                aria-label={`Open ${relation.peer_subject}`}
                onClick={() => onOpen(relation.peer_id)}
              >
                {label}
              </button>
            ) : (
              <span key={`${relation.peer_id}:${index}`}>{label}</span>
            );
          })}
          {detail.relation_count > detail.relations.length && (
            <span>+{detail.relation_count - detail.relations.length} more</span>
          )}
        </div>
      )}
      <dl className="settings-facts">
        <div className="settings-fact">
          <dt>ID</dt>
          <dd>{detail.id}</dd>
        </div>
        <div className="settings-fact">
          <dt>Created</dt>
          <dd>{detail.created_at || 'Unknown'}</dd>
        </div>
        <div className="settings-fact">
          <dt>Updated</dt>
          <dd>{detail.updated_at || 'Unknown'}</dd>
        </div>
        {detail.last_user_modified_at && (
          <div className="settings-fact">
            <dt>User modified</dt>
            <dd>{detail.last_user_modified_at}</dd>
          </div>
        )}
        {detail.last_evolved_at && (
          <div className="settings-fact">
            <dt>Evolved</dt>
            <dd>{detail.last_evolved_at}</dd>
          </div>
        )}
        {detail.last_recalled_at && (
          <div className="settings-fact">
            <dt>Recalled</dt>
            <dd>{detail.last_recalled_at}</dd>
          </div>
        )}
      </dl>
      {detail.review_reason && (
        <p className="warning-text">
          <strong>Review:</strong> {detail.review_reason}
        </p>
      )}
      {detail.superseded_by && <p>Superseded by: {detail.superseded_by}</p>}
      <details className="settings-knowledge-provenance">
        <summary>
          Provenance <ChevronDown size={16} aria-hidden />
        </summary>
        <p>Source: {detail.source || detail.source_bucket}</p>
        {detail.source_context.map((line) => (
          <p key={line}>{line}</p>
        ))}
        {detail.evidence.map((line) => (
          <p key={line}>Evidence: {line}</p>
        ))}
      </details>
      <div className="actions">
        <Button variant="danger" onClick={onDelete}>
          <Trash2 size={16} aria-hidden /> Delete
        </Button>
        <Button onClick={() => onOpen?.(detail.id)}>
          <Edit3 size={16} aria-hidden /> Edit
        </Button>
        {detail.can_archive && (
          <Button
            disabled={pending}
            onClick={() =>
              void onLifecycle?.(
                detail.id,
                detail.revision,
                'knowledge.archive',
              )
            }
          >
            <Archive size={16} aria-hidden /> Archive
          </Button>
        )}
        {detail.can_restore && (
          <Button
            disabled={pending}
            onClick={() =>
              void onLifecycle?.(
                detail.id,
                detail.revision,
                'knowledge.restore',
              )
            }
          >
            Restore
          </Button>
        )}
        {detail.can_resolve && (
          <Button
            disabled={pending}
            onClick={() =>
              void onLifecycle?.(
                detail.id,
                detail.revision,
                'knowledge.resolve',
              )
            }
          >
            Resolve
          </Button>
        )}
      </div>
    </div>
  );
}

function KnowledgeGraphSummary({
  snapshot,
  disabled,
  onToggle,
}: {
  snapshot: KnowledgeSettingsSnapshot;
  disabled: boolean;
  onToggle(enabled: boolean): void;
}) {
  const memoryStatus = !snapshot.memory_available
    ? 'Memory unavailable'
    : snapshot.memory_enabled === true
      ? 'Memory enabled'
      : snapshot.memory_enabled === false
        ? 'Memory disabled'
        : 'Memory state unavailable';
  return (
    <section
      className="settings-snapshot-section stack settings-knowledge-graph"
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
        <label className="settings-knowledge-switch">
          <span>{memoryStatus}</span>
          <Toggle
            label="Enable Memory"
            checked={snapshot.memory_enabled === true}
            disabled={disabled || !snapshot.memory_available}
            onChange={(event) => onToggle(event.target.checked)}
          />
        </label>
      </div>
      {snapshot.availability === 'missing' ? (
        <p>No saved memory graph has been created yet.</p>
      ) : snapshot.availability === 'unavailable' ? (
        <p role="status">Saved memory graph statistics are unavailable.</p>
      ) : (
        <>
          <Network size={34} aria-hidden />
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

function KnowledgeAuditAndDanger({
  loadRecalls,
  loadChangeLog,
  canDelete,
  pending,
  count,
  onDeleteAll,
}: {
  loadRecalls?: (signal?: AbortSignal) => Promise<KnowledgeRecallPage>;
  loadChangeLog?: (signal?: AbortSignal) => Promise<KnowledgeMemoryChangePage>;
  canDelete: boolean;
  pending: boolean;
  count: number;
  onDeleteAll(): void;
}) {
  return (
    <div className="stack settings-knowledge-audit">
      <LazyAuditDisclosure
        title="Recent recall decisions"
        icon={<History size={17} aria-hidden />}
        load={loadRecalls}
        render={(page) =>
          page.items.length ? (
            page.items.map((item, index) => (
              <article
                className="settings-audit-row"
                key={`${item.timestamp}:${index}`}
              >
                <strong>
                  {item.outcome === 'used' ? 'Memory used' : 'Memory skipped'}
                </strong>
                <span>
                  {item.timestamp || 'Unknown time'} · {item.selected_count}/
                  {item.candidate_count} selected ·{' '}
                  {item.context_characters.toLocaleString()} context characters
                </span>
                {item.reason && <p>{item.reason}</p>}
                {item.candidates.length > 0 && (
                  <p>
                    Candidates:{' '}
                    {item.candidates
                      .map(
                        (candidate) =>
                          `${candidate.subject}${candidate.score == null ? '' : ` (${candidate.score.toFixed(2)})`}`,
                      )
                      .join(', ')}
                  </p>
                )}
                {item.rejection_reasons.length > 0 && (
                  <p>Skipped because: {item.rejection_reasons.join(', ')}</p>
                )}
              </article>
            ))
          ) : (
            <p className="settings-help">No recent recall decisions.</p>
          )
        }
      />
      <LazyAuditDisclosure
        title="Memory change log"
        icon={<ScrollText size={17} aria-hidden />}
        load={loadChangeLog}
        render={(page) =>
          page.items.length ? (
            page.items.map((item, index) => (
              <article
                className="settings-audit-row"
                key={`${item.timestamp}:${index}`}
              >
                <strong>{item.action.replaceAll('_', ' ')}</strong>
                <span>
                  {item.timestamp || 'Unknown time'} · {item.actor || 'Row-Bot'}
                </span>
                {item.subjects.length > 0 && (
                  <p>
                    {item.subjects.join(', ')}
                    {item.additional_subjects
                      ? ` +${item.additional_subjects} more`
                      : ''}
                  </p>
                )}
                {(item.old_status || item.new_status) && (
                  <p>
                    {item.old_status || 'unknown'} →{' '}
                    {item.new_status || 'unknown'}
                  </p>
                )}
                {item.reason && <p>{item.reason}</p>}
              </article>
            ))
          ) : (
            <p className="settings-help">No recent memory changes.</p>
          )
        }
      />
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
          <Button
            variant="danger"
            disabled={!canDelete || pending || count === 0}
            onClick={onDeleteAll}
          >
            Delete all knowledge ({count.toLocaleString()})
          </Button>
        </div>
        <p className="settings-help">
          This permanently removes the current saved graph after an exact
          revision review. Row-Bot-managed Wiki files and local indexes are
          cleaned up; files outside Row-Bot's managed Wiki scope are preserved.
        </p>
      </section>
    </div>
  );
}

function LazyAuditDisclosure<P extends { availability: string }>({
  title,
  icon,
  load,
  render,
}: {
  title: string;
  icon: ReactNode;
  load?: (signal?: AbortSignal) => Promise<P>;
  render(page: P): ReactNode;
}) {
  const [page, setPage] = useState<P | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const abort = useRef<AbortController | null>(null);
  useEffect(() => () => abort.current?.abort(), []);
  function open(event: React.SyntheticEvent<HTMLDetailsElement>) {
    if (!event.currentTarget.open || page || loading || !load) return;
    abort.current?.abort();
    abort.current = new AbortController();
    setLoading(true);
    setError('');
    void load(abort.current.signal)
      .then(setPage, (cause) => {
        if (!abort.current?.signal.aborted)
          setError(clientError(cause).message);
      })
      .finally(() => {
        if (!abort.current?.signal.aborted) setLoading(false);
      });
  }
  return (
    <details className="settings-knowledge-audit-disclosure" onToggle={open}>
      <summary>
        {icon} {title}
        <ChevronDown size={17} aria-hidden />
      </summary>
      <div className="stack">
        {loading && <Skeleton label={`Loading ${title}`} />}
        {error && (
          <ErrorState title={`${title} unavailable`}>{error}</ErrorState>
        )}
        {!load && (
          <p className="settings-help">
            This bounded audit view is unavailable.
          </p>
        )}
        {page && page.availability === 'available' && render(page)}
        {page && page.availability !== 'available' && (
          <p className="settings-help">Audit data is {page.availability}.</p>
        )}
      </div>
    </details>
  );
}
