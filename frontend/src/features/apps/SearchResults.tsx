import { useId, useState } from 'react';
import { Link } from 'react-router-dom';
import { ChevronRight, SlidersHorizontal } from 'lucide-react';
import type { IntegrationEntry, IntegrationEntryPage } from '../../api/types';
import {
  Button,
  Disclosure,
  Segmented,
  Select,
  StatusDot,
  Toggle,
} from '../../ui/primitives';
import { Drawer } from '../../ui/overlays';
import {
  AppIcon,
  itemHref,
  Publisher,
  signalsLine,
  statusOf,
  wayLabel,
} from './parts';

export type Kind = 'app' | 'skill' | 'plugin';
export type Runs = 'anywhere' | 'hosted' | 'local';
export type Filters = {
  kind: Kind;
  backed: boolean; // Official & reviewed only (built in too).
  runs: Runs;
  noSignIn: boolean;
};

const BACKED_KEY = 'row-bot:apps:backed-only';

/** "Official & reviewed only" is remembered for this person on this device; nothing else is. */
export function rememberedBacked() {
  try {
    return window.localStorage.getItem(BACKED_KEY) === 'true';
  } catch {
    return false;
  }
}

export function rememberBacked(on: boolean) {
  try {
    if (on) window.localStorage.setItem(BACKED_KEY, 'true');
    else window.localStorage.removeItem(BACKED_KEY);
  } catch {
    // Storage refused: the switch still works for this visit.
  }
}

/** The filters as the catalog read's query: they run on the server, so counts stay true. */
export function filterQuery(filters: Filters) {
  return {
    kind: filters.kind,
    ...(filters.backed ? { labels: 'official,reviewed,built_in' } : {}),
    ...(filters.runs !== 'anywhere' ? { runs: filters.runs } : {}),
    ...(filters.noSignIn ? { no_sign_in: 'true' as const } : {}),
  };
}

export function activeFilters(filters: Filters, pageKind: Kind) {
  return (
    Number(filters.backed) +
    Number(filters.runs !== 'anywhere') +
    Number(filters.noSignIn) +
    Number(filters.kind !== pageKind)
  );
}

const KINDS: { value: Kind; label: string }[] = [
  { value: 'app', label: 'Apps' },
  { value: 'skill', label: 'Skills' },
  { value: 'plugin', label: 'Plugins' },
];
const RUNS: { value: Runs; label: string }[] = [
  { value: 'anywhere', label: 'Anywhere' },
  { value: 'hosted', label: 'Hosted' },
  { value: 'local', label: 'On this computer' },
];

/**
 * The search filters: a row of chips on a wide screen, one Filters sheet (with how many are on) on a phone.
 */
export function FilterBar({
  filters,
  pageKind,
  total,
  onChange,
}: {
  filters: Filters;
  pageKind: Kind;
  total: number | null;
  onChange: (next: Filters) => void;
}) {
  const [open, setOpen] = useState(false);
  const active = activeFilters(filters, pageKind);
  const set = (change: Partial<Filters>) => onChange({ ...filters, ...change });
  return (
    <>
      <div className="apps-filters" role="group" aria-label="Filters">
        {KINDS.map(({ value, label }) => (
          <Button
            key={value}
            className="app-chip-button"
            aria-pressed={filters.kind === value}
            onClick={() => set({ kind: value })}
          >
            {label}
          </Button>
        ))}
        <span className="apps-filters-gap" aria-hidden />
        <Button
          className="app-chip-button"
          aria-pressed={filters.backed}
          onClick={() => set({ backed: !filters.backed })}
        >
          Official &amp; reviewed only
        </Button>
        <Select
          className="apps-filters-runs"
          aria-label="Where it runs"
          value={filters.runs}
          onChange={(event) => set({ runs: event.target.value as Runs })}
        >
          {RUNS.map(({ value, label }) => (
            <option key={value} value={value}>
              {value === 'anywhere' ? 'Runs anywhere' : label}
            </option>
          ))}
        </Select>
        <Button
          className="app-chip-button"
          aria-pressed={filters.noSignIn}
          onClick={() => set({ noSignIn: !filters.noSignIn })}
        >
          No sign-in
        </Button>
      </div>
      <Button
        className="apps-filters-open"
        aria-haspopup="dialog"
        onClick={() => setOpen(true)}
      >
        <SlidersHorizontal size={16} aria-hidden />
        {active ? `Filters · ${active}` : 'Filters'}
      </Button>
      <Drawer open={open} onOpenChange={setOpen} modal title="Filters">
        <div className="apps-filter-sheet">
          <label className="apps-filter-row">
            <span>Official &amp; reviewed only</span>
            <Toggle
              label="Official & reviewed only"
              checked={filters.backed}
              onChange={(event) => set({ backed: event.target.checked })}
            />
          </label>
          <label className="apps-filter-row">
            <span>No sign-in needed</span>
            <Toggle
              label="No sign-in needed"
              checked={filters.noSignIn}
              onChange={(event) => set({ noSignIn: event.target.checked })}
            />
          </label>
          <div className="apps-filter-row apps-filter-stack">
            <span>Where it runs</span>
            <Segmented
              label="Where it runs"
              size="sm"
              value={filters.runs}
              options={RUNS}
              onChange={(runs) => set({ runs })}
            />
          </div>
          <div className="apps-filter-row apps-filter-stack">
            <span>Kind</span>
            <Segmented
              label="Kind"
              size="sm"
              value={filters.kind}
              options={KINDS}
              onChange={(kind) => set({ kind })}
            />
          </div>
          <Button className="primary" onClick={() => setOpen(false)}>
            {total === null
              ? 'Show results'
              : `Show ${total} result${total === 1 ? '' : 's'}`}
          </Button>
        </div>
      </Drawer>
    </>
  );
}

function nameOf(entry: IntegrationEntry) {
  return (entry.kind !== 'skill' && entry.app?.name) || entry.name;
}

/** One quiet result: icon, name, who stands behind it, and how it connects. */
export function ResultRow({
  entry,
  revision,
}: {
  entry: IntegrationEntry;
  revision?: string;
}) {
  const status = statusOf(entry);
  const how = wayLabel(entry);
  return (
    <li>
      <Link className="app-row" to={itemHref(entry, revision)}>
        <AppIcon icon={entry.icon} size={32} />
        <span className="app-row-text">
          <span className="app-row-name">
            <strong>{nameOf(entry)}</strong>
            <Publisher entry={entry} />
          </span>
          <span className="app-row-meta">
            {[
              how,
              entry.kind === 'skill' && entry.app
                ? `Works with ${entry.app.name}`
                : '',
              entry.description,
            ]
              .filter(Boolean)
              .join(' · ')}
          </span>
        </span>
        {status ? (
          <StatusDot tone={status[0]} label={status[1]} showLabel />
        ) : (
          <ChevronRight size={16} aria-hidden className="app-row-go" />
        )}
      </Link>
    </li>
  );
}

/** The result a search leads with: larger, with what it does, how it connects, and its stars. */
function BestMatch({
  entry,
  summary,
  revision,
}: {
  entry: IntegrationEntry;
  summary?: string;
  revision?: string;
}) {
  const how = wayLabel(entry);
  const signals = signalsLine(entry);
  return (
    <Link className="app-hero" to={itemHref(entry, revision)}>
      <AppIcon icon={entry.icon} size={48} />
      <span className="app-hero-text">
        <strong>{nameOf(entry)}</strong>
        <span className="app-hero-job">
          {summary || entry.description || 'No description.'}
        </span>
        <span className="app-card-meta">
          <Publisher entry={entry} />
          {how && <span className="app-chip">{how}</span>}
          {signals && <span className="app-signals">{signals}</span>}
        </span>
      </span>
      {entry.next_action.label && (
        <span className="button primary app-hero-action" aria-hidden>
          {entry.next_action.label}
        </span>
      )}
    </Link>
  );
}

const GROUPS: [NonNullable<IntegrationEntry['label']>, string][] = [
  ['official', 'Official'],
  ['reviewed', 'Reviewed by Row-Bot'],
  ['built_in', 'Built in'],
  ['community', 'Community'],
];

/**
 * A search's results: the best match (when one is good enough to lead) and four more name matches,
 * then everything else folded under "More results (n)", grouped by who stands behind it.
 */
export function SearchResults({
  page,
  items,
  query,
  summaries,
  busy,
  everything,
  onMore,
  onEverything,
}: {
  page: IntegrationEntryPage;
  items: IntegrationEntry[];
  query: string;
  summaries: (entry: IntegrationEntry) => string | undefined;
  busy: boolean;
  everything: boolean;
  onMore: () => void;
  onEverything: () => void;
}) {
  const id = useId();
  const best = items.find((entry) => entry.id === page.best_match_id);
  const named = items.filter(
    (entry) => entry !== best && entry.match !== 'description',
  );
  const top = named.slice(0, best ? 4 : 5);
  const rest = items.filter((entry) => entry !== best && !top.includes(entry));
  const unread = Math.max(0, page.total - page.items.length);
  const more = rest.length + unread;
  // Counted on the server across every page, less what is already shown above or among yours.
  const shown = new Set([best, ...top].filter(Boolean));
  const left = (label: string) =>
    (page.label_counts?.[label as keyof typeof page.label_counts] ?? 0) -
    page.items.filter(
      (entry) =>
        entry.label === label && (shown.has(entry) || !items.includes(entry)),
    ).length;
  const onlyCommunity =
    !best &&
    top.length > 0 &&
    top.every((entry) => entry.label === 'community');
  const revision = page.revision;
  return (
    <div className="apps-results">
      {onlyCommunity && (
        <p className="apps-results-note" role="note">
          No official or reviewed app matches “{query}”. These are made by the
          community.
        </p>
      )}
      {best && (
        <section aria-labelledby={`${id}-best`}>
          <h3 id={`${id}-best`}>Best match</h3>
          <BestMatch
            entry={best}
            summary={summaries(best)}
            revision={revision}
          />
        </section>
      )}
      {top.length > 0 && (
        <section aria-labelledby={`${id}-top`}>
          <h3 id={`${id}-top`}>{best ? 'Also good' : 'Top results'}</h3>
          <ul className="app-rows">
            {top.map((entry) => (
              <ResultRow key={entry.id} entry={entry} revision={revision} />
            ))}
          </ul>
        </section>
      )}
      {(more > 0 || (Boolean(page.hidden) && !everything)) && (
        <Disclosure
          className="apps-more"
          summary={`More results (${more})`}
          meta={
            page.hidden && !everything
              ? `${page.hidden} copies and placeholders hidden`
              : undefined
          }
          defaultOpen={!best && top.length === 0}
        >
          {GROUPS.map(([label, heading]) => {
            const group = rest.filter((entry) => entry.label === label);
            if (!group.length) return null;
            return (
              <section key={label} aria-labelledby={`${id}-${label}`}>
                <h4 id={`${id}-${label}`}>
                  {heading} <span>{Math.max(left(label), group.length)}</span>
                </h4>
                <ul className="app-rows">
                  {group.map((entry) => (
                    <ResultRow
                      key={entry.id}
                      entry={entry}
                      revision={revision}
                    />
                  ))}
                </ul>
              </section>
            );
          })}
          <div className="button-row">
            {page.next_cursor && (
              <Button disabled={busy} onClick={onMore}>
                Show more
              </Button>
            )}
            {Boolean(page.hidden) && !everything && (
              <Button className="settings-link" onClick={onEverything}>
                Show all results
              </Button>
            )}
          </div>
        </Disclosure>
      )}
    </div>
  );
}
