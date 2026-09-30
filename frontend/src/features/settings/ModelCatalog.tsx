import { useEffect, useRef, useState, type ComponentType } from 'react';
import {
  AudioLines,
  Check,
  ChevronLeft,
  Clapperboard,
  Eye,
  Image,
  MessageSquare,
  Pin,
  Search,
} from 'lucide-react';
import type { ClientController } from '../../api/controller';
import type { CachedModelPage, ModelCatalogSummary } from '../../api/types';
import { clientError } from '../../api/errors';
import {
  Button,
  IconButton,
  Input,
  Select,
  Skeleton,
} from '../../ui/primitives';
import { humanizeToken } from '../../ui/format';

type Surface = 'chat' | 'vision' | 'image' | 'video' | 'voice';
type Icon = ComponentType<{ size?: number; 'aria-hidden'?: boolean }>;
const surfaces: { id: Surface; label: string; icon: Icon }[] = [
  { id: 'chat', label: 'Chat', icon: MessageSquare },
  { id: 'vision', label: 'Vision', icon: Eye },
  { id: 'image', label: 'Image', icon: Image },
  { id: 'video', label: 'Video', icon: Clapperboard },
  { id: 'voice', label: 'Voice', icon: AudioLines },
];
const capability: Record<string, Icon> = {
  chat: MessageSquare,
  vision: Eye,
  image: Image,
  video: Clapperboard,
  voice: AudioLines,
};

/** "272K" for a context window of 272,000 tokens. */
function reads(tokens: number) {
  return tokens >= 1_000_000
    ? `${Math.round(tokens / 100_000) / 10}M`
    : `${Math.round(tokens / 1000)}K`;
}

/**
 * Settings › Models › Catalog (B229): search, the job as filter chips, then
 * the providers; a provider (or a search) lists its models with a pin and
 * "use for this job". Rows load only once a provider is opened or a search
 * runs, and then page by page.
 */
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
  const [summaryReload, setSummaryReload] = useState(0);
  const ticket = useRef(0);
  const more = useRef<AbortController | null>(null);
  const browseRows = !!provider || !!query;
  const surfaceLabel =
    surfaces.find((item) => item.id === surface)?.label ?? surface;

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
  }, [controller, surface, reload, summaryReload, refreshSignal]);

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
      setSummaryReload((value) => value + 1);
      // Keep the loaded rows (and the reader's place): refetching would reset
      // to the first page and drop a row reached through "Show more models".
      // Pin toggles this surface; choosing a default also pins it.
      const pinned =
        kind === 'default' || !model.pinned_surfaces.includes(surface);
      setPage((current) =>
        current
          ? {
              ...current,
              items: current.items.map((item) =>
                item.selection_ref !== model.selection_ref
                  ? item
                  : {
                      ...item,
                      pinned_surfaces: pinned
                        ? [...new Set([...item.pinned_surfaces, surface])]
                        : item.pinned_surfaces.filter(
                            (value) => value !== surface,
                          ),
                    },
              ),
            }
          : current,
      );
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusyRef('');
    }
  }

  const providerName = (id: string) =>
    summary?.providers.find((item) => item.provider_id === id)?.display_name ??
    humanizeToken(id);
  return (
    <div className="settings-model-catalog">
      <form
        className="settings-divided settings-catalog-head"
        role="search"
        aria-label="Search the model catalog"
        onSubmit={(event) => {
          event.preventDefault();
          setQuery(searchDraft.trim());
        }}
      >
        <label className="settings-inline-search">
          <span className="visually-hidden">Search models</span>
          <Search size={14} aria-hidden />
          <Input
            type="search"
            maxLength={256}
            placeholder="Search models"
            value={searchDraft}
            onChange={(event) => setSearchDraft(event.target.value)}
          />
        </label>
        <Select
          aria-label="Provider"
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
      </form>
      <div
        className="settings-catalog-chips"
        role="group"
        aria-label="Model category"
      >
        {surfaces.map(({ id, label, icon: Glyph }) => (
          <button
            key={id}
            type="button"
            className="settings-chip"
            aria-pressed={surface === id}
            onClick={() => {
              setSurface(id);
              setProvider('');
              setQuery('');
              setSearchDraft('');
            }}
          >
            <Glyph size={12} aria-hidden />
            {label}
          </button>
        ))}
      </div>
      {error && (
        <p role="alert" className="settings-divided settings-catalog-note">
          {error}
        </p>
      )}
      {cursorExpired && (
        <div className="settings-divided settings-catalog-note">
          <Button
            onClick={() => {
              setError('');
              setCursorExpired(false);
              setReload((value) => value + 1);
            }}
          >
            Reload catalog results
          </Button>
        </div>
      )}
      {!browseRows && !summary && !error && (
        <div className="settings-divided settings-catalog-note">
          <Skeleton label="Loading the catalog" />
        </div>
      )}
      {!browseRows && summary && (
        <ul className="settings-model-provider-summaries">
          {summary.providers.length ? (
            summary.providers.map((item) => (
              <li className="settings-divided" key={item.provider_id}>
                <div className="settings-catalog-row-text">
                  <strong>{item.display_name}</strong>
                  <small>
                    {item.total} {surfaceLabel.toLowerCase()} model
                    {item.total === 1 ? '' : 's'} · {item.ready} ready
                    {item.pinned > 0 ? ` · ${item.pinned} pinned` : ''}
                  </small>
                </div>
                <Button
                  variant="ghost"
                  aria-label={`Open ${item.display_name}`}
                  onClick={() => setProvider(item.provider_id)}
                >
                  Open
                </Button>
              </li>
            ))
          ) : (
            <li className="settings-divided settings-catalog-note">
              No {surfaceLabel.toLowerCase()} models saved yet. Refresh after
              connecting a provider.
            </li>
          )}
        </ul>
      )}
      {loading && (
        <div className="settings-divided settings-catalog-note">
          <Skeleton label="Loading model rows" />
        </div>
      )}
      {page && (
        <section
          className="settings-model-provider-results"
          aria-label={provider ? providerName(provider) : 'Search results'}
        >
          <div className="settings-divided settings-catalog-results-head">
            <IconButton
              size="sm"
              label="All providers"
              onClick={() => {
                setProvider('');
                setQuery('');
                setSearchDraft('');
              }}
            >
              <ChevronLeft size={15} aria-hidden />
            </IconButton>
            <strong>
              {provider ? providerName(provider) : 'Search results'}
            </strong>
            <span role="status">
              Showing {page.items.length} of {page.total} models
            </span>
          </div>
          {!page.items.length && (
            <p className="settings-divided settings-catalog-note">
              No matching models. Try another provider or search.
            </p>
          )}
          <ul className="settings-model-row-list">
            {page.items.map((model) => {
              const usable =
                model.configured &&
                model.runtime_ready &&
                model.installed === true;
              const pinned = model.pinned_surfaces.includes(surface);
              const isDefault = defaults[surface] === model.selection_ref;
              const meta = [
                model.provider_display_name || humanizeToken(model.provider_id),
                model.context_window
                  ? `reads ${reads(model.context_window)}`
                  : '',
                usable && model.runtime_mode === 'agent' ? 'Agent-ready' : '',
                usable && model.runtime_mode === 'chat_only' ? 'Chat only' : '',
              ].filter(Boolean);
              return (
                <li className="settings-divided" key={model.selection_ref}>
                  <div className="settings-catalog-row-text">
                    <strong title={model.model_id}>
                      {model.display_name}
                      {isDefault && (
                        <span className="settings-catalog-default">
                          Default
                        </span>
                      )}
                    </strong>
                    <small>
                      {meta.join(' · ')}
                      {!model.configured && (
                        <span className="settings-catalog-warning">
                          {' · '}Connect first
                        </span>
                      )}
                      {model.configured && !model.runtime_ready && (
                        <span className="settings-catalog-warning">
                          {' · '}Unavailable
                        </span>
                      )}
                    </small>
                  </div>
                  <span
                    className="settings-catalog-caps"
                    title={model.categories.map(humanizeToken).join(', ')}
                  >
                    {model.categories.map((category) => {
                      const Glyph = capability[category];
                      return Glyph ? (
                        <Glyph key={category} size={14} aria-hidden />
                      ) : null;
                    })}
                    <span className="visually-hidden">
                      {model.categories.map(humanizeToken).join(', ')}
                    </span>
                  </span>
                  <div className="settings-model-row-actions">
                    <IconButton
                      size="sm"
                      label={`${pinned ? 'Unpin' : 'Pin'} ${model.display_name} for ${surface}`}
                      pressed={pinned}
                      className="settings-catalog-pin"
                      disabled={!usable || !!busyRef}
                      onClick={() => void act('pin', model)}
                    >
                      <Pin
                        size={14}
                        fill={pinned ? 'currentColor' : 'none'}
                        aria-hidden
                      />
                    </IconButton>
                    {surface !== 'voice' && (
                      <IconButton
                        size="sm"
                        label={`Set ${model.display_name} as ${surface} default`}
                        disabled={!usable || isDefault || !!busyRef}
                        onClick={() => void act('default', model)}
                      >
                        <Check size={14} aria-hidden />
                      </IconButton>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
          {page.next_cursor && (
            <div className="settings-divided settings-catalog-more">
              <Button
                variant="ghost"
                disabled={loadingMore || cursorExpired}
                onClick={() => void showMore()}
              >
                {loadingMore ? 'Loading more models…' : 'Show more models'}
              </Button>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
