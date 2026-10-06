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

export function ItemCard({
  entry,
  revision = '',
  app,
}: {
  entry: IntegrationEntry;
  revision?: string;
  app?: AppView;
}) {
  const status = statusOf(entry);
  const method = methods[entry.method ?? ''];
  return (
    <li>
      <Link className="app-card" to={itemHref(entry, revision)}>
        <AppIcon icon={entry.icon} />
        <span className="app-card-text">
          <strong>{entry.app?.name || entry.name}</strong>
          <span className="app-card-job">
            {app?.summary || entry.description || 'No description.'}
          </span>
          <span className="app-card-meta">
            <Publisher entry={entry} />
            {method && <span className="app-chip">{method}</span>}
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
