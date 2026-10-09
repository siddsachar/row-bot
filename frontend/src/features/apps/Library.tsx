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
import {
  attentionOrder,
  categories,
  fromApp,
  ItemCard,
  useAppCatalog,
  yourItems,
} from './parts';

const NOUN = {
  app: ['an app', 'apps', 'Your apps'],
  skill: ['a skill', 'skills', 'Your skills'],
} as const;

/**
 * What each page is for, in one line, and where the other one is. Plain text:
 * both pages sit side by side in Settings, and a link inside a sentence is too
 * small a target on a phone.
 */
const ABOUT = {
  app: 'Apps let Row-Bot use a service for you, like GitHub or Gmail. To teach it how to do a task, add a skill in Skills.',
  skill:
    'Skills teach Row-Bot how to do a task. Some work with an app, like GitHub, which you connect in Apps.',
} as const;

/** One card per app: the ways you set up for one app (an account and a key, say) share its card. */
function byApp(items: IntegrationEntry[]) {
  const groups = new Map<string, IntegrationEntry[]>();
  for (const entry of items) {
    const key = entry.app ? `app:${entry.app.id}` : entry.id;
    groups.set(key, [...(groups.get(key) ?? []), entry]);
  }
  return [...groups.values()];
}

function Cards({
  groups,
  revision,
}: {
  groups: IntegrationEntry[][];
  revision?: string;
}) {
  const catalog = useAppCatalog();
  return (
    <ul className="app-grid">
      {groups.map(([entry, ...others]) => (
        <ItemCard
          key={entry.id}
          entry={entry}
          ways={[entry, ...others]}
          revision={revision}
          app={entry.app ? catalog.get(entry.app.id) : undefined}
        />
      ))}
    </ul>
  );
}

const single = (items: IntegrationEntry[]) => items.map((entry) => [entry]);

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
  const [look, setLook] = useState(0); // Only the catalog waits on "Preparing…".
  const [everything, setEverything] = useState(false);
  const headingId = useId();
  const [one, many, yours] = NOUN[kind];
  useEffect(() => {
    const at = window.location.href;
    const timer = setTimeout(() => {
      // A result opened meanwhile has moved the page on before this page is gone: writing the
      // search now would replace that page with this list again.
      if (draft.trim() === query || window.location.href !== at) return;
      const next = new URLSearchParams(params);
      if (draft.trim()) next.set('q', draft.trim());
      else next.delete('q');
      setParams(next, { replace: true });
    }, 200);
    return () => clearTimeout(timer);
  }, [draft, query, params, setParams]);
  useEffect(() => {
    const abort = new AbortController();
    // Every installed item, page by page, so attention items are never cut off.
    yourItems(controller, kind, abort.signal).then(
      (items) => {
        // Items included in a package appear under it; skills from packages appear here too.
        const own = items.flatMap((item) =>
          kind === 'skill'
            ? [item, ...item.children].filter((row) => row.kind === 'skill')
            : [item],
        );
        setInstalled(own.sort((a, b) => attentionOrder(a) - attentionOrder(b)));
      },
      (cause) => !abort.signal.aborted && setError(clientError(cause).message),
    );
    return () => abort.abort();
  }, [controller, kind, retry]);
  const preparing = page?.sources.some((source) => source.status === 'pending');
  useEffect(() => {
    const abort = new AbortController();
    setOnline(false);
    controller
      .integrationItems(
        {
          scope: 'catalog',
          kind,
          query,
          ...(everything ? { all: 'true' as const } : {}),
        },
        abort.signal,
      )
      .then(
        (value) => {
          setPage(value);
          setError('');
        },
        (cause) =>
          !abort.signal.aborted && setError(clientError(cause).message),
      );
    return () => abort.abort();
  }, [controller, kind, query, retry, look, everything]);
  useEffect(() => setEverything(false), [query]);
  useEffect(() => {
    if (!preparing) return;
    // While the Registry index is being prepared, look again shortly.
    const timer = setTimeout(() => setLook((n) => n + 1), 2000);
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
          include_incompatible: everything,
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
        ...(everything ? { all: 'true' as const } : {}),
      });
      setPage({ ...next, items: [...page.items, ...next.items] });
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  };
  const lowered = query.toLowerCase();
  // Yours, one card per app in Apps: found by any of its ways' names or the app's.
  const mine = (kind === 'app' ? byApp : single)(installed ?? []).filter(
    (group) =>
      !query ||
      group.some((entry) =>
        `${entry.name} ${entry.app?.name ?? ''}`
          .toLowerCase()
          .includes(lowered),
      ),
  );
  // An app already among yours is not listed again below: its page has its other ways to connect.
  const have = new Set(
    kind === 'app' ? mine.flat().map((entry) => entry.app?.id) : [],
  );
  const shown = (page?.items ?? []).filter(
    (entry) =>
      (!category || entry.app?.category === category) &&
      !(entry.app && have.has(entry.app.id)),
  );
  const isFeatured = (entry: IntegrationEntry) =>
    kind === 'skill'
      ? entry.source === 'featured_skills'
      : Boolean(entry.app?.featured_rank);
  const featured =
    query || category ? [] : shown.filter(isFeatured).slice(0, 8);
  const rest = shown.filter((entry) => !featured.includes(entry));
  // Apps from their vendors (or featured) first; everything else is the community's.
  const community =
    kind === 'app' ? rest.filter((entry) => !fromApp(entry)) : [];
  const leading = rest.filter((entry) => !community.includes(entry));
  // Found only in the community: one heading for them, not an empty one above.
  const onlyCommunity =
    Boolean(page) && !leading.length && community.length > 0;
  // Everything found is already among yours: no "nothing matches" below it.
  const onlyYours =
    Boolean(page) && !rest.length && !featured.length && mine.length > 0;
  const about = ABOUT[kind];
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
      <p className="settings-help">{about}</p>
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
          <Cards groups={mine} />
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
          <Cards groups={single(featured)} revision={page?.revision} />
        </section>
      )}
      {!onlyCommunity && !onlyYours && (
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
                ? `Try another word, search online catalogs, or add ${one} from a link.`
                : `Add ${one} from a link or a file.`}
            </EmptyState>
          )}
          <Cards groups={single(leading)} revision={page?.revision} />
        </section>
      )}
      {community.length > 0 && (
        <section aria-labelledby={`${headingId}-community`}>
          <h3 id={`${headingId}-community`}>
            {onlyCommunity ? 'From the community' : 'More from the community'}
          </h3>
          <Cards groups={single(community)} revision={page?.revision} />
        </section>
      )}
      {(page?.next_cursor || (Boolean(page?.hidden) && !everything)) && (
        <div className="button-row">
          {page?.next_cursor && (
            <Button disabled={busy} onClick={() => void more()}>
              Show more
            </Button>
          )}
          {Boolean(page?.hidden) && !everything && (
            <Button
              className="settings-link"
              onClick={() => setEverything(true)}
            >
              Show all results
            </Button>
          )}
        </div>
      )}
      <AddFromLink open={adding} kind={kind} onClose={() => setAdding(false)} />
    </div>
  );
}
