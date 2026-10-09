import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { BadgeCheck } from 'lucide-react';
import { useRuntime } from '../../runtime';
import type { AppView, IntegrationEntry } from '../../api/types';
import { StatusDot, type Tone } from '../../ui/primitives';
import './apps.css';

type Controller = ReturnType<typeof useRuntime>['controller'];

type Icon = { src: string; mono: boolean };

/** Icons come only from Row-Bot's local icon route: every icon a screen asks for in one tick
 * travels in one request, and each is kept for the session. Letter avatars need no request. */
const icons = new Map<string, Promise<Icon>>();
let waiting: {
  controller: Controller;
  ids: Map<string, (icon: Icon | null) => void>;
} | null = null;

function loadIcon(controller: Controller, icon: string) {
  let found = icons.get(icon);
  if (found) return found;
  found = new Promise<Icon>((resolve, reject) => {
    if (!waiting) {
      const batch = (waiting = { controller, ids: new Map() });
      setTimeout(() => {
        waiting = null;
        const ids = [...batch.ids.keys()];
        for (let start = 0; start < ids.length; start += 64) {
          const part = ids.slice(start, start + 64);
          batch.controller.integrationIcons(part).then(
            (value) => {
              const byId = new Map(value.items.map((item) => [item.id, item]));
              for (const id of part) {
                const item = byId.get(id);
                batch.ids.get(id)?.(
                  item ? { src: item.data, mono: item.mono } : null,
                );
              }
            },
            () => part.forEach((id) => batch.ids.get(id)?.(null)),
          );
        }
      }, 0);
    }
    waiting.ids.set(icon, (value) => {
      if (value) resolve(value);
      else {
        icons.delete(icon);
        reject(new Error('icon_unavailable'));
      }
    });
  });
  icons.set(icon, found);
  return found;
}

const palette = [
  '#4F46E5',
  '#0E7490',
  '#B45309',
  '#047857',
  '#BE185D',
  '#6D28D9',
  '#1D4ED8',
  '#B91C1C',
];

export function AppIcon({ icon, size = 40 }: { icon: string; size?: number }) {
  const { controller } = useRuntime();
  const letter = /^letter:([A-Z0-9])$/.exec(icon)?.[1];
  const [loaded, setLoaded] = useState<Icon | null>(null);
  useEffect(() => {
    if (letter) return;
    let alive = true;
    loadIcon(controller, icon).then(
      (value) => alive && setLoaded(value),
      () => undefined,
    );
    return () => {
      alive = false;
    };
  }, [controller, icon, letter]);
  if (letter)
    return (
      <span
        className="app-icon app-letter"
        aria-hidden
        style={{
          width: size,
          height: size,
          fontSize: size * 0.45,
          background: palette[letter.charCodeAt(0) % palette.length],
        }}
      >
        {letter}
      </span>
    );
  return (
    <span className="app-icon" style={{ width: size, height: size }}>
      {loaded && (
        <img
          src={loaded.src}
          alt=""
          width={size}
          height={size}
          data-mono={loaded.mono || undefined}
        />
      )}
    </span>
  );
}

/** App identities (summary, prompts, links), read once: local data only. */
let apps: Promise<Map<string, AppView>> | null = null;
export function appCatalog(controller: Controller) {
  apps ??= controller.integrationApps('').then(
    (list) => new Map(list.items.map((app) => [app.id, app])),
    (error) => {
      apps = null;
      throw error;
    },
  );
  return apps;
}

/** Everything of one kind you have set up, every page of it: local data only. */
export async function yourItems(
  controller: Controller,
  kind: 'app' | 'skill',
  signal?: AbortSignal,
) {
  const items: IntegrationEntry[] = [];
  let cursor: string | undefined;
  do {
    const value = await controller.integrationItems(
      { scope: 'installed', kind, cursor },
      signal,
    );
    items.push(...value.items);
    cursor = value.next_cursor ?? undefined;
  } while (cursor && !signal?.aborted);
  return items;
}

/** Your apps, for a page that says whether an app is connected; null until read (or when not wanted). */
export function useYourApps(wanted = true) {
  const { controller } = useRuntime();
  const [items, setItems] = useState<IntegrationEntry[] | null>(null);
  useEffect(() => {
    if (!wanted) return;
    const abort = new AbortController();
    yourItems(controller, 'app', abort.signal).then(
      (found) => !abort.signal.aborted && setItems(found),
      () => !abort.signal.aborted && setItems([]),
    );
    return () => abort.abort();
  }, [controller, wanted]);
  return items;
}

export function useAppCatalog() {
  const { controller } = useRuntime();
  const [value, setValue] = useState<Map<string, AppView>>(new Map());
  useEffect(() => {
    let alive = true;
    appCatalog(controller).then(
      (found) => alive && setValue(found),
      () => undefined,
    );
    return () => {
      alive = false;
    };
  }, [controller]);
  return value;
}

export const categories: [AppView['category'], string][] = [
  ['productivity', 'Productivity'],
  ['developer', 'Developer'],
  ['data', 'Data'],
  ['design', 'Design'],
  ['communication', 'Communication'],
  ['finance', 'Finance'],
  ['local_tools', 'Local tools'],
];

const readiness: Record<string, [Tone, string]> = {
  ready: ['success', 'Ready'],
  needs_sign_in: ['warning', 'Sign in needed'],
  needs_key: ['warning', 'Key needed'],
  needs_setup: ['warning', 'Finish setup'],
  needs_runtime: ['warning', 'Finish setup'],
  needs_app: ['warning', 'Open the app'],
  working: ['info', 'Working…'],
  attention: ['danger', 'Needs attention'],
};

/** One plain status for an installed item, or null when it is only listed. */
export function statusOf(entry: IntegrationEntry): [Tone, string] | null {
  if (entry.lifecycle === 'available') return null;
  if (entry.lifecycle === 'data_retained') return ['neutral', 'Removed'];
  if (entry.lifecycle === 'off' && entry.readiness === 'ready')
    return ['neutral', 'Off'];
  return readiness[entry.readiness ?? 'ready'] ?? ['neutral', 'Off'];
}

/** Attention first, then setup, then ready, then off. */
export function attentionOrder(entry: IntegrationEntry) {
  const tone = statusOf(entry)?.[0];
  return tone === 'danger'
    ? 0
    : tone === 'warning' || tone === 'info'
      ? 1
      : tone === 'success'
        ? 2
        : 3;
}

const methods: Record<string, string> = {
  hosted_sign_in: 'Hosted · Sign-in',
  api_key: 'API key',
  hosted: 'Hosted',
  local: 'Runs on this computer',
};

/** "by" only for a vendor verified by rule; otherwise who actually published it, or Community. */
export function Publisher({ entry }: { entry: IntegrationEntry }) {
  if (entry.verified)
    return (
      <span className="app-publisher" data-verified="true">
        <BadgeCheck size={14} aria-hidden />
        by {entry.app?.publisher || entry.publisher}
      </span>
    );
  // Not verified as the service's own: always says so, whatever name it gives itself.
  return (
    <span className="app-publisher">
      {['bundled', 'builtin'].includes(entry.source)
        ? 'Built in'
        : entry.source === 'user'
          ? 'Made by you'
          : entry.publisher
            ? `Community · ${entry.publisher}`
            : 'Community'}
    </span>
  );
}

/**
 * Whether an entry's page goes by its app (name, summary, examples). A skill or a built-in way is one thing that
 * works with an app (the "Fix failing CI" skill, the GitHub account), so its page goes by its own name and job.
 */
export function asApp(entry: IntegrationEntry) {
  return entry.kind !== 'skill' && entry.kind !== 'builtin';
}

/**
 * The way to connect an app's card stands for, as a chip: a built-in way by its own name ("GitHub account"),
 * the others by how they connect ("API key"). Nothing for a skill, or for a card already named by the way.
 */
export function wayLabel(entry: IntegrationEntry) {
  if (entry.kind === 'skill') return '';
  if (entry.kind === 'builtin') return entry.app ? entry.name : '';
  return methods[entry.method ?? ''] ?? '';
}

/** A featured app, or one whose publisher is verified; the rest is "More from the community". */
export function fromApp(entry: IntegrationEntry) {
  return Boolean(entry.verified || entry.app?.featured_rank);
}

/** A link to one item: an installed skill by name, otherwise `item?id=` (ids carry `:` and `/`, which paths refuse). */
export function idPath(kind: 'app' | 'skill', id: string, revision = '') {
  if (kind === 'skill' && /^skill:[a-z0-9_-]+$/i.test(id))
    return `/settings/skills/${id.slice(6)}`;
  const query = new URLSearchParams({ id });
  if (revision) query.set('r', revision);
  return `/settings/${kind === 'skill' ? 'skills' : 'apps'}/item?${query}`;
}

export function itemHref(entry: IntegrationEntry, revision = '') {
  return idPath(
    entry.kind === 'skill' ? 'skill' : 'app',
    entry.id,
    entry.installed ? '' : revision,
  );
}

/**
 * One card. In Apps a card is one app: it goes by the app, and its chips say which ways to connect it
 * stands for (`ways`: every way of that app you set up, the first opening). A skill goes by its own name
 * and says which app it works with.
 */
export function ItemCard({
  entry,
  revision = '',
  app,
  ways = [entry],
}: {
  entry: IntegrationEntry;
  revision?: string;
  app?: AppView;
  ways?: IntegrationEntry[];
}) {
  const status = statusOf(entry);
  const skill = entry.kind === 'skill';
  const name = (!skill && entry.app?.name) || entry.name;
  const chips = [...new Set(ways.map(wayLabel))].filter(
    (label) => label && label !== name,
  );
  return (
    <li>
      <Link className="app-card" to={itemHref(entry, revision)}>
        <AppIcon icon={entry.icon} />
        <span className="app-card-text">
          <strong>{name}</strong>
          <span className="app-card-job">
            {(!skill && app?.summary) || entry.description || 'No description.'}
          </span>
          <span className="app-card-meta">
            {ways.length === 1 && <Publisher entry={entry} />}
            {chips.map((label) => (
              <span key={label} className="app-chip">
                {label}
              </span>
            ))}
            {skill && entry.app && (
              <span className="app-chip">Works with {entry.app.name}</span>
            )}
            {entry.kind === 'plugin' &&
              entry.compatibility === 'not_inspected' &&
              !entry.installed && (
                <span className="app-chip">Checked when you add</span>
              )}
          </span>
        </span>
        {status && <StatusDot tone={status[0]} label={status[1]} showLabel />}
      </Link>
    </li>
  );
}
