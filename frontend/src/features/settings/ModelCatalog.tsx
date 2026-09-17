import { useEffect, useRef, useState } from 'react';
import { Check, Pin, Search } from 'lucide-react';
import type { ClientController } from '../../api/controller';
import type { CachedModelPage, ModelCatalogSummary } from '../../api/types';
import { clientError } from '../../api/errors';
import {
  Button,
  EmptyState,
  Field,
  Input,
  Select,
  Skeleton,
} from '../../ui/primitives';

type Surface = 'chat' | 'vision' | 'image' | 'video' | 'voice';
const tabs: Surface[] = ['chat', 'vision', 'image', 'video', 'voice'];

export default function ModelCatalog({
  controller,
  initialProvider = '',
  defaults,
  onDefault,
  onPin,
  onChanged,
  refreshSignal = 0,
}: {
  controller: ClientController;
  initialProvider?: string;
  defaults: Partial<Record<Surface, string>>;
  onDefault: (
    surface: Surface,
    model: CachedModelPage['items'][number],
  ) => Promise<void>;
  onPin: (
    surface: Surface,
    model: CachedModelPage['items'][number],
  ) => Promise<void>;
  onChanged: () => Promise<void>;
  refreshSignal?: number;
}) {
  const [surface, setSurface] = useState<Surface>('chat');
  const [provider, setProvider] = useState(initialProvider);
  const [searchDraft, setSearchDraft] = useState('');
  const [query, setQuery] = useState('');
  const [summary, setSummary] = useState<ModelCatalogSummary | null>(null);
  const [page, setPage] = useState<CachedModelPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [busyRef, setBusyRef] = useState('');
  const [error, setError] = useState('');
  const [cursorExpired, setCursorExpired] = useState(false);
  const [reload, setReload] = useState(0);
  const ticket = useRef(0);
  const more = useRef<AbortController | null>(null);
  const browseRows = !!provider || !!query;

  useEffect(() => {
    const abort = new AbortController();
    setSummary(null);
    void controller.modelCatalogSummary(surface, abort.signal).then(
      (result) => {
        if (!abort.signal.aborted) setSummary(result);
      },
      (cause) => {
        if (!abort.signal.aborted) setError(clientError(cause).message);
      },
    );
    return () => abort.abort();
  }, [controller, surface, reload, refreshSignal]);

  useEffect(() => {
    const abort = new AbortController();
    const current = ++ticket.current;
    more.current?.abort();
    more.current = null;
    setPage(null);
    setCursorExpired(false);
    if (!browseRows) return () => abort.abort();
    setLoading(true);
    setError('');
    void controller
      .modelCatalogPage(
        surface,
        provider || undefined,
        query,
        undefined,
        abort.signal,
      )
      .then(
        (result) => {
          if (!abort.signal.aborted && current === ticket.current) {
            setPage(result);
            setLoading(false);
          }
        },
        (cause) => {
          if (!abort.signal.aborted && current === ticket.current) {
            setError(clientError(cause).message);
            setLoading(false);
          }
        },
      );
    return () => abort.abort();
  }, [controller, surface, provider, query, reload, browseRows, refreshSignal]);

  async function showMore() {
    if (!page?.next_cursor || more.current || cursorExpired) return;
    const abort = new AbortController();
    more.current = abort;
    const current = ticket.current;
    setLoadingMore(true);
    try {
      const next = await controller.modelCatalogPage(
        surface,
        provider || undefined,
        query,
        page.next_cursor,
        abort.signal,
      );
      if (abort.signal.aborted || current !== ticket.current) return;
      if (next.revision !== page.revision) throw { code: 'cursor_expired' };
      setPage({ ...next, items: [...page.items, ...next.items] });
    } catch (cause) {
      if (!abort.signal.aborted) {
        const failure = clientError(cause);
        setError(failure.message);
        if (failure.code === 'cursor_expired') setCursorExpired(true);
      }
    } finally {
      more.current = null;
      if (!abort.signal.aborted) setLoadingMore(false);
    }
  }

  async function act(
    kind: 'default' | 'pin',
    model: CachedModelPage['items'][number],
  ) {
    if (busyRef) return;
    setBusyRef(model.selection_ref);
    setError('');
    try {
      if (kind === 'pin') await onPin(surface, model);
      else await onDefault(surface, model);
      await onChanged();
      setReload((value) => value + 1);
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusyRef('');
    }
  }

  return (
    <div className="stack settings-model-catalog" aria-label="Model catalog">
      <p>
        Browse discovered models by provider. Open one provider or search before
        showing model rows.
      </p>
      <div className="settings-model-catalog-filters">
        <div
          className="settings-model-tabs"
          role="tablist"
          aria-label="Model category"
        >
          {tabs.map((tab) => (
            <button
              key={tab}
              type="button"
              role="tab"
              aria-selected={surface === tab}
              onClick={() => {
                setSurface(tab);
                setProvider('');
                setQuery('');
                setSearchDraft('');
              }}
            >
              {tab.toUpperCase()}
            </button>
          ))}
        </div>
        <Field label="Provider">
          <Select
            value={provider}
            onChange={(event) => setProvider(event.target.value)}
          >
            <option value="">All providers</option>
            {summary?.providers.map((item) => (
              <option key={item.provider_id} value={item.provider_id}>
                {item.display_name}
              </option>
            ))}
          </Select>
        </Field>
        <form
          className="settings-model-search"
          onSubmit={(event) => {
            event.preventDefault();
            setQuery(searchDraft.trim());
          }}
        >
          <Field label="Search models">
            <Input
              type="search"
              maxLength={256}
              value={searchDraft}
              onChange={(event) => setSearchDraft(event.target.value)}
            />
          </Field>
          <Button type="submit" aria-label="Search models">
            <Search size={16} aria-hidden />
          </Button>
        </form>
      </div>
      {error && <p role="alert">{error}</p>}
      {cursorExpired && (
        <Button
          onClick={() => {
            setError('');
            setCursorExpired(false);
            setReload((value) => value + 1);
          }}
        >
          Reload catalog results
        </Button>
      )}
      {!browseRows && summary && (
        <div className="settings-model-provider-summaries">
          <h4>Providers</h4>
          {summary.providers.length ? (
            <ul>
              {summary.providers.map((item) => (
                <li key={item.provider_id}>
                  <div>
                    <strong>{item.display_name}</strong>
                    <small>
                      {item.total} {surface} model(s) · {item.ready} ready ·{' '}
                      {item.pinned} pinned
                    </small>
                  </div>
                  {item.pinned > 0 && (
                    <span className="status-chip">{item.pinned} pinned</span>
                  )}
                  <Button
                    variant="ghost"
                    onClick={() => setProvider(item.provider_id)}
                  >
                    Open
                  </Button>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState title="No cached models">
              Refresh after connecting a provider.
            </EmptyState>
          )}
        </div>
      )}
      {loading && <Skeleton label="Loading model rows" />}
      {page && (
        <section className="settings-model-provider-results">
          <h4>
            {provider
              ? (summary?.providers.find(
                  (item) => item.provider_id === provider,
                )?.display_name ?? provider)
              : 'Search results'}{' '}
            ({page.total})
          </h4>
          <p>
            Showing {page.items.length} of {page.total} models
          </p>
          {!page.items.length && (
            <EmptyState title="No matching models">
              Try another provider or search.
            </EmptyState>
          )}
          <ul className="settings-model-row-list">
            {page.items.map((model) => {
              const usable =
                model.configured &&
                model.runtime_ready &&
                model.installed === true;
              const pinned = model.pinned_surfaces.includes(surface);
              const isDefault = defaults[surface] === model.selection_ref;
              return (
                <li key={model.selection_ref}>
                  <div className="settings-model-row-name">
                    <strong>{model.display_name}</strong>
                    <small title={model.model_id}>{model.model_id}</small>
                  </div>
                  <div className="settings-model-row-badges">
                    <span className="status-chip">{model.provider_id}</span>
                    {model.context_window && (
                      <span className="status-chip">
                        {Math.round(model.context_window / 1000)}K ctx
                      </span>
                    )}
                    {model.categories.map((category) => (
                      <span className="status-chip" key={category}>
                        {category}
                      </span>
                    ))}
                    {usable && model.runtime_mode === 'agent' && (
                      <span className="status-chip success">Agent-ready</span>
                    )}
                    {usable && model.runtime_mode === 'chat_only' && (
                      <span className="status-chip">Chat only</span>
                    )}
                    {!model.configured && (
                      <span className="status-chip warning">connect</span>
                    )}
                    {model.configured && !model.runtime_ready && (
                      <span className="status-chip warning">unavailable</span>
                    )}
                    {pinned && <span className="status-chip">pinned</span>}
                    {isDefault && <span className="status-chip">default</span>}
                  </div>
                  <div className="settings-model-row-actions">
                    <Button
                      variant="ghost"
                      aria-label={`${pinned ? 'Unpin' : 'Pin'} ${model.display_name} for ${surface}`}
                      title={
                        model.status_reason ||
                        (pinned
                          ? 'Remove from this picker'
                          : 'Pin to this picker')
                      }
                      disabled={!usable || !!busyRef}
                      onClick={() => void act('pin', model)}
                    >
                      <Pin
                        size={16}
                        fill={pinned ? 'currentColor' : 'none'}
                        aria-hidden
                      />
                    </Button>
                    {surface !== 'voice' && (
                      <Button
                        variant="ghost"
                        aria-label={`Set ${model.display_name} as ${surface} default`}
                        title={model.status_reason || 'Set default'}
                        disabled={!usable || isDefault || !!busyRef}
                        onClick={() => void act('default', model)}
                      >
                        <Check size={16} aria-hidden />
                      </Button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
          {page.next_cursor && (
            <Button
              disabled={loadingMore || cursorExpired}
              onClick={() => void showMore()}
            >
              {loadingMore ? 'Loading more models…' : 'Show more models'}
            </Button>
          )}
        </section>
      )}
    </div>
  );
}
