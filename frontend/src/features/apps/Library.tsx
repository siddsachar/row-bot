import { useEffect, useId, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Search } from 'lucide-react';
import { useRuntime } from '../../runtime';
import { clientError } from '../../api/errors';
import type { IntegrationEntry, IntegrationEntryPage } from '../../api/types';
import {
  Button,
  EmptyState,
  ErrorState,
  Input,
  Skeleton,
} from '../../ui/primitives';
import AddFromLink from './AddFromLink';
import { attentionOrder, categories, ItemCard, useAppCatalog } from './parts';

const NOUN = {
  app: ['app', 'apps', 'Your apps'],
  skill: ['skill', 'skills', 'Your skills'],
} as const;

function Cards({
  items,
  revision,
}: {
  items: IntegrationEntry[];
  revision?: string;
}) {
  const catalog = useAppCatalog();
  return (
    <ul className="app-grid">
      {items.map((entry) => (
        <ItemCard
          key={entry.id}
          entry={entry}
          revision={revision}
          app={entry.app ? catalog.get(entry.app.id) : undefined}
        />
      ))}
    </ul>
  );
}

/** Apps home and the Skills library: yours first, then featured, then everything, all searched locally. */
export default function Library({ kind }: { kind: 'app' | 'skill' }) {
  const { controller } = useRuntime();
  const [params, setParams] = useSearchParams();
  const query = params.get('q') ?? '';
  const category = params.get('category') ?? '';
  const [draft, setDraft] = useState(query);
  const [installed, setInstalled] = useState<IntegrationEntry[] | null>(null);
  const [page, setPage] = useState<IntegrationEntryPage | null>(null);
  const [online, setOnline] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState(false);
  const [retry, setRetry] = useState(0);
  const headingId = useId();
  const [one, many, yours] = NOUN[kind];
  useEffect(() => {
    const timer = setTimeout(() => {
      if (draft.trim() === query) return;
      const next = new URLSearchParams(params);
      if (draft.trim()) next.set('q', draft.trim());
      else next.delete('q');
      setParams(next, { replace: true });
    }, 200);
    return () => clearTimeout(timer);
  }, [draft, query, params, setParams]);
  useEffect(() => {
    const abort = new AbortController();
    controller
      .integrationItems({ scope: 'installed', kind }, abort.signal)
      .then(
        (value) => {
          // Items included in a package appear under it; skills from packages appear here too.
          const own = value.items.flatMap((item) =>
            kind === 'skill'
              ? [item, ...item.children].filter((row) => row.kind === 'skill')
              : [item],
          );
          setInstalled(
            own.sort((a, b) => attentionOrder(a) - attentionOrder(b)),
          );
        },
        (cause) =>
          !abort.signal.aborted && setError(clientError(cause).message),
      );
    return () => abort.abort();
  }, [controller, kind, retry]);
  const preparing = page?.sources.some((source) => source.status === 'pending');
  useEffect(() => {
    const abort = new AbortController();
    setOnline(false);
    controller
      .integrationItems({ scope: 'catalog', kind, query }, abort.signal)
      .then(
        (value) => {
          setPage(value);
          setError('');
        },
        (cause) =>
          !abort.signal.aborted && setError(clientError(cause).message),
      );
    return () => abort.abort();
  }, [controller, kind, query, retry]);
  useEffect(() => {
    if (!preparing) return;
    // While the Registry index is being prepared, look again shortly.
    const timer = setTimeout(() => setRetry((n) => n + 1), 2000);
    return () => clearTimeout(timer);
  }, [preparing, page]);
  const searchOnline = async () => {
    setBusy(true);
    try {
      setPage(
        await controller.searchIntegrationItems({
          query,
          kind,
          refresh: true,
          limit: 50,
        }),
      );
      setOnline(true);
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  };
  const more = async () => {
    if (!page?.next_cursor) return;
    setBusy(true);
    try {
      const next = await controller.integrationItems({
        scope: 'catalog',
        kind,
        query,
        cursor: page.next_cursor,
      });
      setPage({ ...next, items: [...page.items, ...next.items] });
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  };
  const shown = (page?.items ?? []).filter(
    (entry) => !category || entry.app?.category === category,
  );
  const isFeatured = (entry: IntegrationEntry) =>
    kind === 'skill'
      ? entry.source === 'featured_skills'
      : Boolean(entry.app?.featured_rank);
  const featured =
    query || category ? [] : shown.filter(isFeatured).slice(0, 8);
  const rest = shown.filter((entry) => !featured.includes(entry));
  const mine = (installed ?? []).filter(
    (entry) =>
      !query ||
      `${entry.name} ${entry.app?.name ?? ''}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  const actions = (
    <div className="button-row">
      {query && (
        <Button disabled={busy} onClick={() => void searchOnline()}>
          Search online catalogs
        </Button>
      )}
      <Button onClick={() => setAdding(true)}>Add from link or file</Button>
    </div>
  );
  return (
    <div className="apps-library stack">
      <div className="apps-toolbar">
        <label className="apps-search">
          <Search size={16} aria-hidden />
          <span className="visually-hidden">Search {many}</span>
          <Input
            type="search"
            value={draft}
            maxLength={256}
            placeholder={
              kind === 'app'
                ? 'Find an app or a job, like “send email”'
                : 'Find a skill, like “pdf”'
            }
            onChange={(event) => setDraft(event.target.value)}
          />
        </label>
        {actions}
        {kind === 'app' ? (
          <Link className="button ghost" to="?view=advanced">
            Advanced
          </Link>
        ) : (
          <Link className="button ghost" to="/settings/skills/new">
            Create a skill
          </Link>
        )}
      </div>
      {preparing && (
        <p className="settings-help" role="status">
          Preparing the app catalog… More apps appear in a moment.
        </p>
      )}
      {error && (
        <ErrorState
          title={`Couldn't load ${many}`}
          action={<Button onClick={() => setRetry((n) => n + 1)}>Retry</Button>}
        >
          {error}
        </ErrorState>
      )}
      {mine.length > 0 && (
        <section aria-labelledby={`${headingId}-yours`}>
          <h3 id={`${headingId}-yours`}>{yours}</h3>
          <Cards items={mine} />
        </section>
      )}
      {kind === 'app' && !query && (
        <div className="apps-categories" role="group" aria-label="Categories">
          {categories.map(([id, label]) => (
            <Button
              key={id}
              className="app-chip-button"
              aria-pressed={category === id}
              onClick={() => {
                const next = new URLSearchParams(params);
                if (category === id) next.delete('category');
                else next.set('category', id);
                setParams(next, { replace: true });
              }}
            >
              {label}
            </Button>
          ))}
        </div>
      )}
      {featured.length > 0 && (
        <section aria-labelledby={`${headingId}-featured`}>
          <h3 id={`${headingId}-featured`}>Featured</h3>
          <Cards items={featured} revision={page?.revision} />
        </section>
      )}
      <section aria-labelledby={`${headingId}-all`} aria-busy={!page}>
        <h3 id={`${headingId}-all`}>
          {query
            ? `Results for “${query}”${online ? ' · includes online catalogs' : ''}`
            : `All ${many}`}
        </h3>
        {!page && !error && <Skeleton label={`Loading ${many}`} />}
        {page && !rest.length && !featured.length && (
          <EmptyState
            title={
              query ? `No ${many} match “${query}”` : `No ${many} here yet`
            }
            action={actions}
          >
            {query
              ? `Try another word, search online catalogs, or add a ${one} from a link.`
              : `Add a ${one} from a link or a file.`}
          </EmptyState>
        )}
        <Cards items={rest} revision={page?.revision} />
        {page?.next_cursor && (
          <Button disabled={busy} onClick={() => void more()}>
            Show more
          </Button>
        )}
      </section>
      <AddFromLink open={adding} kind={kind} onClose={() => setAdding(false)} />
    </div>
  );
}
