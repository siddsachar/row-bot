import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import type { ProviderStatusSnapshot } from '../../api/types';
import { clientError } from '../../api/errors';
import { Button, EmptyState, ErrorState, Skeleton } from '../../ui/primitives';
import CatalogStatus from './CatalogStatus';
import ProviderSettingsPanel from './ProviderSettingsPanel';
import CustomProviderCredentials from './CustomProviderCredentials';
import type { createProviderSettingsSessions } from './provider-settings-sessions';

const groups = {
  local: 'Local',
  subscription: 'Subscription accounts',
  api: 'API providers',
  custom: 'Custom endpoints',
} as const;

export default function ProviderStatus({
  load,
  owner,
}: {
  load: (signal?: AbortSignal) => Promise<ProviderStatusSnapshot>;
  owner?: ReturnType<typeof createProviderSettingsSessions>;
}) {
  const [selected, setSelected] = useState('');
  const [notice, setNotice] = useState('');
  const [snapshot, setSnapshot] = useState<ProviderStatusSnapshot | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);
  const Credentials = selected.startsWith('custom_openai_')
    ? CustomProviderCredentials
    : ProviderSettingsPanel;
  useEffect(() => {
    const abort = new AbortController();
    setLoading(true);
    setError('');
    load(abort.signal).then(
      (value) => {
        if (!abort.signal.aborted) {
          setSnapshot(value);
          setLoading(false);
        }
      },
      (cause: unknown) => {
        if (!abort.signal.aborted) {
          setSnapshot(null);
          setError(clientError(cause).message);
          setLoading(false);
        }
      },
    );
    return () => abort.abort();
  }, [load, reload]);
  return (
    <section
      className="stack settings-owner-section"
      aria-busy={loading}
      id="provider-saved-status"
      aria-labelledby="provider-connections-heading"
    >
      <div className="settings-owner-heading">
        <div>
          <h3 id="provider-connections-heading">Connection Status</h3>
          <p>Saved provider facts; checks run only when you request them.</p>
        </div>
        <Button
          disabled={loading}
          onClick={() => setReload((value) => value + 1)}
        >
          Reload saved status
        </Button>
      </div>
      {notice && <p role="status">{notice}</p>}
      {selected && owner ? (
        <Credentials
          owner={owner}
          providerId={selected}
          onSaved={() => {
            setNotice('Credential settings saved.');
            setSelected('');
            setReload((value) => value + 1);
          }}
          onCancel={() => setSelected('')}
        />
      ) : null}
      {loading && <Skeleton label="Loading saved providers" />}
      {error && (
        <ErrorState title="Provider information unavailable">
          {error}
        </ErrorState>
      )}
      {snapshot && (
        <>
          <CatalogStatus {...snapshot} />
          {snapshot.refresh_running && (
            <p role="status">A catalog refresh is already running.</p>
          )}
          {!snapshot.providers.length && (
            <EmptyState title="No saved providers">
              Set up a provider to populate this catalog.
            </EmptyState>
          )}
          {Object.entries(groups).map(([group, title]) => {
            const providers = snapshot.providers.filter(
              (item) => item.group === group,
            );
            return providers.length ? (
              <section
                className="stack settings-provider-group"
                aria-label={title}
                key={group}
              >
                <h3>{title}</h3>
                <ul className="settings-provider-list">
                  {providers.map((provider) => (
                    <li key={provider.provider_id}>
                      <Link
                        className="settings-provider-summary"
                        to={`/settings/models?provider=${encodeURIComponent(provider.provider_id)}`}
                      >
                        <span className="settings-provider-mark" aria-hidden>
                          {provider.display_name
                            .split(/\s+/)
                            .map((part) => part[0])
                            .join('')
                            .slice(0, 2)
                            .toUpperCase()}
                        </span>
                        <span className="settings-provider-copy">
                          <strong>{provider.display_name}</strong>
                          <small>
                            {provider.enabled === false
                              ? 'Disabled'
                              : provider.enabled === true
                                ? 'Enabled'
                                : 'Connection not checked'}
                            {' · '}
                            {provider.model_count === null
                              ? 'Model count unknown'
                              : `${provider.model_count} saved models`}
                          </small>
                        </span>
                        <span className="status-chip">
                          {provider.group === 'api'
                            ? 'API key'
                            : provider.group}
                        </span>
                      </Link>
                      {owner && ['api', 'custom'].includes(provider.group) && (
                        <Button
                          className="settings-row-action"
                          onClick={() => {
                            setNotice('');
                            setSelected(provider.provider_id);
                          }}
                        >
                          Edit {provider.display_name} credentials
                        </Button>
                      )}
                      <p className="muted settings-provider-state">
                        {provider.enabled === false ? 'Disabled. ' : ''}
                        {provider.catalog_state === 'error'
                          ? 'Last catalog read reported an error.'
                          : provider.catalog_state === 'unavailable'
                            ? 'No saved catalog details.'
                            : provider.catalog_state === 'verified_empty'
                              ? 'Last catalog contained no models.'
                              : 'Saved catalog available.'}
                      </p>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null;
          })}
        </>
      )}
    </section>
  );
}
