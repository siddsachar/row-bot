import { useEffect, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { useRuntime } from '../../runtime';
import { clientError } from '../../api/errors';
import type { CatalogSchedule, IntegrationSourceView } from '../../api/types';
import { relativeTime } from '../../ui/format';
import { Button, Field, Select, Toggle } from '../../ui/primitives';
import { SettingsGroup, StatusLine } from '../settings/anatomy';
import RuntimeInstallations from '../mcp/RuntimeInstallations';

/** Where a catalog stands, in plain words. */
function standing(source: IntegrationSourceView) {
  if (!['eligible', 'explicit_only'].includes(source.eligibility))
    return 'Not available yet';
  const update = source.catalog;
  if (!update)
    return source.network === 'none'
      ? 'Included with Row-Bot'
      : 'Searched when you ask';
  if (update.state === 'updating') return 'Updating…';
  if (update.state === 'failed')
    return "Couldn't update. The last copy is still used.";
  if (update.state === 'never' || !update.updated_at)
    return 'Included with Row-Bot';
  const entries = update.entries
    ? ` · ${update.entries.toLocaleString()} entries`
    : '';
  return `Updated ${relativeTime(update.updated_at)}${entries}`;
}

/** Apps › Advanced: catalogs and their optional schedule, plus app-wide controls. */
export default function Advanced({ chat }: { chat: ReactNode }) {
  const { controller } = useRuntime();
  const [sources, setSources] = useState<IntegrationSourceView[]>([]);
  const [schedule, setSchedule] = useState<CatalogSchedule | null>(null);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    controller.integrationSources(abort.signal).then(
      (list) => {
        setSources(list.items);
        // An update runs in the background; look again until it settles.
        if (list.items.some((source) => source.catalog?.state === 'updating'))
          timer = setTimeout(() => setReload((n) => n + 1), 1500);
      },
      (cause) => !abort.signal.aborted && setError(clientError(cause).message),
    );
    return () => {
      abort.abort();
      clearTimeout(timer);
    };
  }, [controller, reload]);
  const updating = sources.some(
    (source) => source.catalog?.state === 'updating',
  );
  useEffect(() => {
    controller.catalogSchedule().then(setSchedule, () => undefined);
  }, [controller]);
  const updatable = sources.filter(
    (source) =>
      source.network === 'explicit' &&
      source.catalog &&
      source.eligibility === 'eligible',
  );
  const update = async (ids: string[]) => {
    setError('');
    try {
      for (const id of ids) await controller.updateIntegrationSource(id);
      setReload((n) => n + 1);
    } catch (cause) {
      setError(clientError(cause).message);
    }
  };
  const save = async (next: CatalogSchedule) => {
    try {
      setSchedule(await controller.setCatalogSchedule(next));
    } catch (cause) {
      setError(clientError(cause).message);
    }
  };
  return (
    <div className="stack">
      <Link className="settings-link app-back" to="/settings/apps">
        <ArrowLeft size={14} aria-hidden /> Apps
      </Link>
      <SettingsGroup title="Catalogs" anchor="catalogs">
        <div className="app-section">
          <p className="settings-help">
            Row-Bot searches copies kept on this computer. Updating fetches the
            latest copy; searching never sends what you type.
          </p>
          <ul className="catalog-list">
            {sources.map((source) => (
              <li key={source.id}>
                <span>
                  <strong>{source.label}</strong>
                  <small>
                    {source.kinds.includes('skill') ? 'Skills' : 'Apps'} ·{' '}
                    {standing(source)}
                  </small>
                </span>
                {updatable.includes(source) && (
                  <Button
                    disabled={source.catalog?.state === 'updating'}
                    onClick={() => void update([source.id])}
                    aria-label={`Update ${source.label}`}
                  >
                    Update
                  </Button>
                )}
              </li>
            ))}
          </ul>
          <div className="button-row">
            <Button
              disabled={updating || !updatable.length}
              onClick={() => void update(updatable.map((s) => s.id))}
            >
              Update all
            </Button>
          </div>
          {schedule && (
            <>
              <Field label="Update catalogs automatically" layout="row">
                <Toggle
                  label="Update catalogs automatically"
                  checked={schedule.enabled}
                  onChange={(event) =>
                    void save({ ...schedule, enabled: event.target.checked })
                  }
                />
              </Field>
              {schedule.enabled && (
                <Field label="How often">
                  <Select
                    value={String(schedule.interval_days)}
                    onChange={(event) =>
                      void save({
                        ...schedule,
                        interval_days: Number(event.target.value) as 1 | 7 | 30,
                      })
                    }
                  >
                    <option value="1">Every day</option>
                    <option value="7">Every week</option>
                    <option value="30">Every month</option>
                  </Select>
                </Field>
              )}
            </>
          )}
          {error && <StatusLine tone="danger">{error}</StatusLine>}
        </div>
      </SettingsGroup>
      <SettingsGroup title="Apps in chats" anchor="chats">
        {chat}
        <RuntimeInstallations />
      </SettingsGroup>
      <SettingsGroup title="Your own connection" anchor="custom">
        <div className="app-section">
          <p className="settings-help">
            Add a connection by its settings, for servers that aren't in a
            catalog.
          </p>
          <Link className="button" to="/settings/apps/custom?edit=1">
            Add a custom connection
          </Link>
        </div>
      </SettingsGroup>
    </div>
  );
}
