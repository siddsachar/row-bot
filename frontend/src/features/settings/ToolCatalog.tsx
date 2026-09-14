import { useEffect, useRef, useState } from 'react';
import type { ToolCatalogPage } from '../../api/types';
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

type Source = ToolCatalogPage['items'][number]['source'];
const sourceLabels: Record<Source, string> = {
  core: 'Core',
  mcp: 'MCP',
  plugin: 'Plugins',
  custom: 'Custom tools',
};
const changedMessage =
  'The tool catalog changed. Reload cached tools to continue.';

export default function ToolCatalog({
  load,
}: {
  load: (
    source?: Source,
    query?: string,
    cursor?: string,
    signal?: AbortSignal,
  ) => Promise<ToolCatalogPage>;
}) {
  const [draft, setDraft] = useState('');
  const [filter, setFilter] = useState<{ source: Source | ''; query: string }>({
    source: '',
    query: '',
  });
  const [page, setPage] = useState<ToolCatalogPage | null>(null);
  const [earlierRows, setEarlierRows] = useState(0);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [stale, setStale] = useState(false);
  const [reload, setReload] = useState(0);
  const epoch = useRef(0);
  const more = useRef<AbortController | null>(null);

  useEffect(() => {
    const abort = new AbortController();
    const ticket = ++epoch.current;
    more.current?.abort();
    more.current = null;
    setLoading(true);
    setLoadingMore(false);
    setStale(false);
    setError('');
    setPage(null);
    setEarlierRows(0);
    load(
      filter.source || undefined,
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
  }, [load, filter, reload]);

  async function loadMore() {
    if (!page?.next_cursor || stale || more.current) return;
    const ticket = epoch.current;
    const abort = new AbortController();
    more.current = abort;
    setLoadingMore(true);
    setError('');
    try {
      const next = await load(
        filter.source || undefined,
        filter.query,
        page.next_cursor,
        abort.signal,
      );
      if (abort.signal.aborted || ticket !== epoch.current) return;
      if (next.revision !== page.revision) {
        setStale(true);
        setError(changedMessage);
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
      <h2>Tool catalogue</h2>
      <p>
        Browse cached tool information by source. Runtime readiness and account
        access have not been checked.
      </p>
      <form
        className="field-row"
        onSubmit={(event) => {
          event.preventDefault();
          setFilter((value) => ({ ...value, query: draft.trim() }));
        }}
      >
        <Field label="Search tools">
          <Input
            type="search"
            maxLength={256}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
        </Field>
        <Field label="Tool source">
          <Select
            value={filter.source}
            onChange={(event) =>
              setFilter((value) => ({
                ...value,
                source: event.target.value as Source | '',
              }))
            }
          >
            <option value="">All sources</option>
            {Object.entries(sourceLabels).map(([source, label]) => (
              <option key={source} value={source}>
                {label}
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
          Reload cached tools
        </Button>
      </div>
      {loading && <Skeleton label="Loading cached tools" />}
      {error && (
        <ErrorState title="Tool information unavailable">{error}</ErrorState>
      )}
      {page && (
        <>
          <p className="muted" role="status">
            {page.freshness === 'unavailable'
              ? 'Cached tool information is unavailable.'
              : 'Showing cached tool information.'}{' '}
            Collection time is unknown.
          </p>
          <details>
            <summary>Catalog sources and coverage</summary>
            <dl aria-label="Catalog sources">
              {page.sources.map((source) => (
                <div key={source.source}>
                  <dt>{sourceLabels[source.source]}</dt>
                  <dd>
                    {source.state === 'cached' ? 'Cached' : 'Unavailable'} ·{' '}
                    {source.total == null
                      ? 'Count unknown'
                      : `${source.total.toLocaleString()} recorded entries`}
                  </dd>
                </div>
              ))}
            </dl>
            <p className="muted">
              Counts cover recorded tools and discovered entries. Individual
              plugin commands may not appear.
            </p>
          </details>
          {page.truncated && (
            <p role="status">
              Only part of the cached catalog is available. These results and
              counts may be incomplete.
            </p>
          )}
          <p role="status">
            {page.total.toLocaleString()} matching recorded entries
          </p>
          {!page.items.length && (
            <EmptyState title="No matching cached tools">
              Try another search or source, or reload the cached information.
            </EmptyState>
          )}
          {earlierRows > 0 && (
            <p role="status">
              Showing entries {earlierRows + 1}–
              {earlierRows + page.items.length}. Reload cached tools to return
              to the beginning.
            </p>
          )}
          <ul className="settings-results">
            {page.items.map((tool) => (
              <li className="surface" key={`${tool.source}:${tool.id}`}>
                <details>
                  <summary>
                    {tool.label} · {sourceLabels[tool.source]}
                  </summary>
                  <p>{tool.id}</p>
                  <dl>
                    <dt>Enabled setting</dt>
                    <dd>
                      {tool.enabled == null
                        ? 'Unknown'
                        : tool.enabled
                          ? 'Enabled'
                          : 'Disabled'}
                    </dd>
                    <dt>Configuration record</dt>
                    <dd>
                      {tool.configured == null
                        ? 'Unknown'
                        : tool.configured
                          ? 'Recorded'
                          : 'Not recorded'}
                    </dd>
                    <dt>Destructive action declaration</dt>
                    <dd>
                      {tool.destructive == null
                        ? 'Unknown'
                        : tool.destructive
                          ? 'Declared'
                          : 'Not declared'}
                    </dd>
                    <dt>Approval declaration</dt>
                    <dd>
                      {tool.requires_approval == null
                        ? 'Unknown'
                        : tool.requires_approval
                          ? 'Declared'
                          : 'Not declared'}
                    </dd>
                    <dt>Runtime readiness</dt>
                    <dd>Unknown</dd>
                    {tool.parent_id && (
                      <>
                        <dt>Parent tool</dt>
                        <dd>{tool.parent_id}</dd>
                      </>
                    )}
                    {tool.plugin_id && (
                      <>
                        <dt>Plugin</dt>
                        <dd>{tool.plugin_id}</dd>
                      </>
                    )}
                    {tool.server_name && (
                      <>
                        <dt>MCP server</dt>
                        <dd>{tool.server_name}</dd>
                      </>
                    )}
                  </dl>
                </details>
              </li>
            ))}
          </ul>
          {page.next_cursor && (
            <Button
              disabled={loadingMore || stale}
              onClick={() => void loadMore()}
            >
              {loadingMore ? 'Loading more tools…' : 'Load more tools'}
            </Button>
          )}
        </>
      )}
    </div>
  );
}
