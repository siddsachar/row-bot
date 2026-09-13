import { useEffect, useRef, useState } from 'react';
import type { CachedModelPage, ProviderStatusSnapshot } from '../../api/types';
import { clientError } from '../../api/errors';
import {
  Button,
  EmptyState,
  ErrorState,
  Field,
  Input,
  Select,
  Skeleton,
} from '../../ui/primitives';
import CatalogStatus from './CatalogStatus';

export default function ModelCatalog({
  load,
  loadProviders,
  initialProvider = '',
  onChooseDefault,
}: {
  load: (
    providerId?: string,
    query?: string,
    cursor?: string,
    signal?: AbortSignal,
  ) => Promise<CachedModelPage>;
  loadProviders: (signal?: AbortSignal) => Promise<ProviderStatusSnapshot>;
  initialProvider?: string;
  onChooseDefault?: (model: CachedModelPage['items'][number]) => Promise<void>;
}) {
  const [providers, setProviders] = useState<
    ProviderStatusSnapshot['providers']
  >([]);
  const [draft, setDraft] = useState('');
  const [filter, setFilter] = useState({
    provider: initialProvider,
    query: '',
  });
  const [page, setPage] = useState<CachedModelPage | null>(null);
  const [earlierRows, setEarlierRows] = useState(0);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [choosing, setChoosing] = useState(false);
  const [stale, setStale] = useState(false);
  const [reload, setReload] = useState(0);
  const epoch = useRef(0);
  const more = useRef<AbortController | null>(null);
  useEffect(() => {
    const abort = new AbortController();
    loadProviders(abort.signal).then(
      (value) => {
        if (!abort.signal.aborted) setProviders(value.providers);
      },
      () => {
        /* The model query retains its independent outcome. */
      },
    );
    return () => abort.abort();
  }, [loadProviders, reload]);
  useEffect(() => {
    const abort = new AbortController();
    const ticket = ++epoch.current;
    more.current?.abort();
    more.current = null;
    setLoadingMore(false);
    setStale(false);
    setLoading(true);
    setError('');
    setPage(null);
    setEarlierRows(0);
    load(
      filter.provider || undefined,
      filter.query,
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
  }, [filter, load, reload]);
  async function loadMore() {
    if (!page?.next_cursor || stale || more.current) return;
    const ticket = epoch.current;
    const abort = new AbortController();
    more.current = abort;
    setLoadingMore(true);
    setError('');
    try {
      const next = await load(
        filter.provider || undefined,
        filter.query,
        page.next_cursor,
        abort.signal,
      );
      if (abort.signal.aborted || ticket !== epoch.current) return;
      if (next.revision !== page.revision) {
        setStale(true);
        setError('The catalog changed. Reload saved models to continue.');
        return;
      }
      const items = [...page.items, ...next.items];
      setEarlierRows((value) => value + Math.max(0, items.length - 200));
      setPage({ ...next, items: items.slice(-200) });
    } catch (cause) {
      if (!abort.signal.aborted && ticket === epoch.current) {
        const failure = clientError(cause);
        if (failure.code === 'cursor_expired') {
          setStale(true);
          setError('The catalog changed. Reload saved models to continue.');
        } else setError(failure.message);
      }
    } finally {
      if (more.current === abort) more.current = null;
      if (!abort.signal.aborted && ticket === epoch.current)
        setLoadingMore(false);
    }
  }
  return (
    <div className="stack" aria-busy={loading}>
      <h1>Models</h1>
      <p>
        Search the locally saved catalog. Choose a model for a conversation in
        that conversation’s controls.
      </p>
      <form
        className="field-row"
        onSubmit={(event) => {
          event.preventDefault();
          setFilter((value) => ({ ...value, query: draft.trim() }));
        }}
      >
        <Field label="Search models">
          <Input
            type="search"
            maxLength={256}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
        </Field>
        <Field label="Provider">
          <Select
            value={filter.provider}
            onChange={(event) =>
              setFilter((value) => ({ ...value, provider: event.target.value }))
            }
          >
            <option value="">All providers</option>
            {filter.provider &&
              !providers.some(
                (provider) => provider.provider_id === filter.provider,
              ) && <option value={filter.provider}>{filter.provider}</option>}
            {providers.map((provider) => (
              <option key={provider.provider_id} value={provider.provider_id}>
                {provider.display_name}
              </option>
            ))}
          </Select>
        </Field>
        <Button type="submit">Search</Button>
      </form>
      <div>
        <Button
          disabled={loading}
          onClick={() => setReload((value) => value + 1)}
        >
          Reload saved models
        </Button>
      </div>
      {loading && <Skeleton label="Loading saved models" />}
      {error && (
        <ErrorState title="Model information unavailable">{error}</ErrorState>
      )}
      {page && (
        <>
          <CatalogStatus {...page} />
          <p role="status">{page.total} matching models</p>
          {earlierRows > 0 && (
            <p role="status">
              Showing entries {earlierRows + 1}–
              {earlierRows + page.items.length}. Reload saved models to return
              to the beginning.
            </p>
          )}
          {!page.items.length && (
            <EmptyState title="No matching saved models">
              Try another search or provider.
            </EmptyState>
          )}
          <ul className="settings-results">
            {page.items.map((model) => (
              <li className="surface" key={model.selection_ref}>
                <details>
                  <summary>
                    {model.display_name} · {model.provider_display_name}
                  </summary>
                  <p>{model.selection_ref}</p>
                  <dl>
                    <dt>Context window</dt>
                    <dd>
                      {model.context_window == null
                        ? 'Unknown'
                        : `${model.context_window.toLocaleString()} tokens`}
                    </dd>
                    <dt>Tool calling</dt>
                    <dd>
                      {model.tool_calling == null
                        ? 'Unknown'
                        : model.tool_calling
                          ? 'Supported'
                          : 'Unsupported'}
                    </dd>
                    <dt>Input</dt>
                    <dd>{model.input_modalities.join(', ') || 'Unknown'}</dd>
                    <dt>Output</dt>
                    <dd>{model.output_modalities.join(', ') || 'Unknown'}</dd>
                    <dt>Installed</dt>
                    <dd>
                      {model.installed == null
                        ? 'Unknown'
                        : model.installed
                          ? 'Yes'
                          : 'No'}
                    </dd>
                    <dt>Thinking</dt>
                    <dd>
                      {model.reasoning?.supported_efforts.join(', ') ||
                        (model.reasoning
                          ? model.reasoning.thinking_mode
                          : 'Unknown')}
                    </dd>
                  </dl>
                </details>
                {onChooseDefault && model.categories.includes('chat') && (
                  <Button
                    disabled={choosing || stale}
                    onClick={() => {
                      setChoosing(true);
                      setError('');
                      void onChooseDefault(model)
                        .catch((cause) => setError(clientError(cause).message))
                        .finally(() => setChoosing(false));
                    }}
                  >
                    Review {model.display_name} as default
                  </Button>
                )}
              </li>
            ))}
          </ul>
          {page.next_cursor && (
            <Button
              disabled={loadingMore || stale}
              onClick={() => void loadMore()}
            >
              {loadingMore ? 'Loading more models…' : 'Load more models'}
            </Button>
          )}
        </>
      )}
    </div>
  );
}
