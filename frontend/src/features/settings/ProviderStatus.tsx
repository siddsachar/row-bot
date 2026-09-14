import { KeyRound, RefreshCw } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import type { ProviderStatusSnapshot } from '../../api/types';
import { clientError } from '../../api/errors';
import {
  Button,
  CompactAction,
  EmptyState,
  ErrorState,
  Skeleton,
} from '../../ui/primitives';
import CatalogStatus from './CatalogStatus';
import ProviderSettingsPanel from './ProviderSettingsPanel';
import CustomProviderCredentials from './CustomProviderCredentials';
import type { createProviderSettingsSessions } from './provider-settings-sessions';

const groups = {
  local: 'Local',
  subscription: 'Subscription Accounts',
  api: 'API Providers',
  custom: 'Custom Endpoints',
} as const;

function providerState(provider: ProviderStatusSnapshot['providers'][number]) {
  if (provider.enabled === false)
    return {
      label: 'Disabled',
      detail: 'Disabled in saved configuration',
      tone: 'disabled',
    } as const;
  if (provider.enabled === true)
    return {
      label: 'Saved enabled',
      detail: 'Enabled in saved configuration; readiness not checked',
      tone: 'saved',
    } as const;
  return {
    label: 'Not checked',
    detail:
      provider.catalog_state === 'error'
        ? 'Last saved catalog read reported an error'
        : provider.catalog_state === 'unavailable'
          ? 'No saved catalog; connection readiness not checked'
          : provider.catalog_state === 'verified_empty'
            ? 'Saved catalog is empty; connection readiness not checked'
            : 'Saved catalog; connection readiness not checked',
    tone: 'unknown',
  } as const;
}

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
          <RefreshCw size={15} aria-hidden />
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
          <div
            className="settings-summary-strip"
            role="group"
            aria-label="Provider summary"
          >
            {snapshot.providers.some((item) => item.enabled === true) && (
              <span className="status-chip success">
                {
                  snapshot.providers.filter((item) => item.enabled === true)
                    .length
                }{' '}
                saved enabled
              </span>
            )}
            {(['local', 'api', 'subscription', 'custom'] as const).map(
              (group) => (
                <span className="status-chip" key={group}>
                  {
                    snapshot.providers.filter((item) => item.group === group)
                      .length
                  }{' '}
                  {
                    {
                      local: 'local',
                      api: 'API',
                      subscription: 'subscription',
                      custom: 'custom',
                    }[group]
                  }
                </span>
              ),
            )}
            <span className="status-chip">
              {snapshot.total_models} saved models
            </span>
          </div>
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
                          <span className="settings-provider-title">
                            <span
                              className={`settings-provider-readiness-dot is-${providerState(provider).tone}`}
                              aria-hidden
                            />
                            <strong>{provider.display_name}</strong>
                            <span>{providerState(provider).label}</span>
                          </span>
                          <small>
                            {providerState(provider).detail}
                            {' · '}
                            {provider.model_count === null
                              ? 'Model count unknown'
                              : `${provider.model_count} saved models`}
                          </small>
                        </span>
                        <span className="status-chip">
                          {
                            {
                              local: 'Local',
                              subscription: 'Subscription',
                              api: 'API key',
                              custom: 'Custom',
                            }[provider.group]
                          }
                        </span>
                      </Link>
                      {owner && ['api', 'custom'].includes(provider.group) && (
                        <CompactAction
                          label={`Edit ${provider.display_name} credentials`}
                          className="settings-row-action"
                          onClick={() => {
                            setNotice('');
                            setSelected(provider.provider_id);
                          }}
                        >
                          <KeyRound size={17} aria-hidden />
                        </CompactAction>
                      )}
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
