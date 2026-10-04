import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { BadgeCheck } from 'lucide-react';
import { useRuntime } from '../../runtime';
import type { AppView, IntegrationEntry } from '../../api/types';
import { StatusDot, type Tone } from '../../ui/primitives';
import './apps.css';

type Controller = ReturnType<typeof useRuntime>['controller'];

/** Icons come only from Row-Bot's local icon route, loaded once per session as blobs. */
const icons = new Map<string, Promise<string>>();

export function AppIcon({ icon, size = 40 }: { icon: string; size?: number }) {
  const { controller } = useRuntime();
  const [url, setUrl] = useState('');
  useEffect(() => {
    let alive = true;
    if (!icons.has(icon))
      icons.set(
        icon,
        controller.integrationIcon(icon).then(
          (blob) => URL.createObjectURL(blob),
          (error) => {
            icons.delete(icon);
            throw error;
          },
        ),
      );
    icons.get(icon)!.then(
      (value) => alive && setUrl(value),
      () => undefined,
    );
    return () => {
      alive = false;
    };
  }, [controller, icon]);
  return (
    <span className="app-icon" style={{ width: size, height: size }}>
      {url && <img src={url} alt="" width={size} height={size} />}
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

export function Publisher({ entry }: { entry: IntegrationEntry }) {
  const name = entry.app?.publisher || entry.publisher;
  if (entry.verified)
    return (
      <span className="app-publisher" data-verified="true">
        <BadgeCheck size={14} aria-hidden />
        by {name}
      </span>
    );
  return (
    <span className="app-publisher">
      {entry.kind === 'skill' && name ? `by ${name}` : 'Community'}
    </span>
  );
}

/** The one line a card shows about what it does. */
export function jobOf(entry: IntegrationEntry, app?: AppView) {
  return app?.summary || entry.description || 'No description.';
}

export function itemHref(entry: IntegrationEntry, revision = '') {
  const base = entry.kind === 'skill' ? '/settings/skills/' : '/settings/apps/';
  return (
    base +
    encodeURIComponent(entry.id) +
    (revision && !entry.installed ? `?r=${revision}` : '')
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
          <span className="app-card-job">{jobOf(entry, app)}</span>
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
