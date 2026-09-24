import {
  FlaskConical,
  KeyRound,
  Link2Off,
  LogIn,
  RefreshCw,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type {
  ProviderCatalogRefresh,
  ProviderLiveCard,
  ProviderLiveSnapshot,
  ProviderRuntimeProbe,
} from '../../api/types';
import { clientError } from '../../api/errors';
import {
  CompactAction,
  EmptyState,
  ErrorState,
  Skeleton,
} from '../../ui/primitives';
import { ModalTask } from '../../ui/overlays';
import ProviderSettingsPanel from './ProviderSettingsPanel';
import type { createProviderSettingsSessions } from './provider-settings-sessions';

const groups = {
  local: 'Local',
  subscription: 'Subscription Accounts',
  api: 'API Providers',
  custom: 'Custom Endpoints',
} as const;
const sourceLabels: Record<string, string> = {
  keyring: 'Saved in keyring',
  encrypted_file: 'Saved in encrypted server storage',
  environment: 'Using environment variable',
  secret_file: 'Using read-only server secret',
  session: 'Using session key',
  api_keys: 'Saved API key',
  oauth_device: 'Signed in with ChatGPT',
  oauth_pkce: 'Connected with Row-Bot OAuth',
  external_cli: 'Using external CLI login',
  external_cli_detected: 'External CLI login detected',
  no_auth: 'No API key required',
  local_daemon: 'Local daemon running',
  not_running: 'Not running',
};
function cardState(card: ProviderLiveCard) {
  if (card.configured && card.group === 'subscription' && !card.runtime_enabled)
    return { label: 'Reconnect', tone: 'warning' };
  if (card.configured && card.source === 'external_cli')
    return { label: 'Referenced', tone: 'saved' };
  if (card.configured) return { label: 'Connected', tone: 'enabled' };
  if (card.source === 'external_cli_detected')
    return { label: 'Detected', tone: 'saved' };
  if (card.source === 'not_running')
    return { label: 'Not running', tone: 'warning' };
  return { label: 'Not connected', tone: 'unknown' };
}
function canConnect(card: ProviderLiveCard) {
  if (card.provider_id === 'codex')
    return card.source !== 'oauth_device' || !card.configured;
  if (card.provider_id === 'claude_subscription')
    return card.source !== 'oauth_pkce' || !card.configured;
  if (card.provider_id === 'xai_oauth')
    return (
      card.oauth_client_id_configured &&
      (!card.configured || !card.runtime_enabled || !!card.reconnect_required)
    );
  return false;
}
function canDisconnect(card: ProviderLiveCard) {
  return (
    card.configured ||
    (['codex', 'claude_subscription'].includes(card.provider_id) &&
      card.source === 'external_cli')
  );
}
function cardDetail(card: ProviderLiveCard) {
  const source =
    card.configured && card.provider_id === 'codex' && !card.runtime_enabled
      ? 'Reconnect ChatGPT to use Codex models in chat'
      : card.configured &&
          card.provider_id === 'claude_subscription' &&
          !card.runtime_enabled
        ? 'Claude Code login found, but Row-Bot runtime is not connected'
        : card.configured &&
            card.provider_id === 'xai_oauth' &&
            !card.runtime_enabled
          ? 'Reconnect xAI Grok to use OAuth models in chat'
          : card.provider_id === 'xai_oauth' && !card.configured
            ? card.oauth_client_id_configured
              ? 'OAuth client ID available; connect xAI Grok'
              : 'Set an OAuth client ID override to connect xAI Grok'
            : sourceLabels[card.source] ||
              (card.configured
                ? 'Connected'
                : 'Add credentials to enable this provider');
  const details = [source];
  if (card.plan_type) details.push(`${card.plan_type} plan`);
  if (card.model_count !== null)
    details.push(
      `${card.model_count} ${card.model_count_source.includes('fallback') ? 'known models' : 'models'}`,
    );
  else if (card.configured || card.runtime_enabled)
    details.push('catalog count unknown');
  if (card.chat_count) details.push(`${card.chat_count} chat`);
  if (card.media_count) details.push(`${card.media_count} media`);
  return details.join(' · ');
}

export default function ProviderStatus({
  load,
  refresh,
  refreshState,
  testRuntime,
  owner,
  onSubscription,
  onSubscriptionOption,
}: {
  load: (signal?: AbortSignal) => Promise<ProviderLiveSnapshot>;
  refresh: (provider: string) => Promise<ProviderCatalogRefresh>;
  refreshState: (signal?: AbortSignal) => Promise<ProviderCatalogRefresh>;
  testRuntime?: (provider: string) => Promise<ProviderRuntimeProbe>;
  owner?: ReturnType<typeof createProviderSettingsSessions>;
  onSubscription?: (
    provider: string,
    action: 'connect' | 'manage' | 'disconnect',
  ) => void;
  onSubscriptionOption?: (provider: string) => void;
}) {
  const [snapshot, setSnapshot] = useState<ProviderLiveSnapshot | null>(null);
  const [selected, setSelected] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState('');
  const [testing, setTesting] = useState('');
  const [reload, setReload] = useState(0);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const statusAbort = useRef<AbortController | null>(null);
  useEffect(() => () => statusAbort.current?.abort(), []);
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
      (cause) => {
        if (!abort.signal.aborted) {
          setError(clientError(cause).message);
          setLoading(false);
        }
      },
    );
    return () => abort.abort();
  }, [load, reload]);
  async function refreshProvider(card: ProviderLiveCard) {
    if (refreshing || statusAbort.current) return;
    const abort = new AbortController();
    statusAbort.current = abort;
    setRefreshing(card.provider_id);
    setNotice(`Refreshing ${card.display_name}...`);
    try {
      const start = await refresh(card.provider_id);
      if (abort.signal.aborted) return;
      if (!start.started) {
        setNotice('Model catalog refresh is already running.');
        setReload((value) => value + 1);
        return;
      }
      let result: ProviderCatalogRefresh = { running: true, started: false };
      for (
        let count = 0;
        count < 120 && result.running && !abort.signal.aborted;
        count++
      ) {
        await new Promise((resolve) => setTimeout(resolve, 500));
        if (abort.signal.aborted) return;
        result = await refreshState(abort.signal);
      }
      if (abort.signal.aborted) return;
      setNotice(
        result.running
          ? `${card.display_name} refresh is still running.`
          : result.ok === false
            ? `${card.display_name} refresh failed.`
            : result.provider_id !== card.provider_id
              ? `${card.display_name} refresh result is unavailable.`
              : result.message ||
                `${card.display_name} catalog refreshed${result.model_count != null ? `: ${result.model_count} models` : ''}.`,
      );
      setReload((value) => value + 1);
    } catch (cause) {
      if (!abort.signal.aborted) setError(clientError(cause).message);
    } finally {
      if (statusAbort.current === abort) statusAbort.current = null;
      if (!abort.signal.aborted) setRefreshing('');
    }
  }
  async function runtimeTest(card: ProviderLiveCard) {
    if (!testRuntime || testing) return;
    setTesting(card.provider_id);
    setNotice(`Testing ${card.display_name} runtime...`);
    try {
      const result = await testRuntime(card.provider_id);
      setNotice(`${card.display_name}: ${result.detail}.`);
      setReload((value) => value + 1);
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setTesting('');
    }
  }
  return (
    <section
      className="stack settings-provider-status"
      id="provider-saved-status"
      aria-labelledby="provider-connections-heading"
      aria-busy={loading}
    >
      <h3 id="provider-connections-heading">Connection Status</h3>
      {notice && (
        <p className="settings-provider-notice" role="status">
          {notice}
        </p>
      )}
      {loading && <Skeleton label="Checking provider connections" />}
      {error && (
        <ErrorState title="Provider information unavailable">
          {error}
        </ErrorState>
      )}
      {snapshot && (
        <>
          <div
            className="settings-summary-strip"
            role="group"
            aria-label="Provider summary"
          >
            <span className="status-chip">
              {snapshot.providers.filter((card) => card.configured).length}{' '}
              connected
            </span>
            <span className="status-chip">
              {
                snapshot.providers.filter((card) => card.group === 'local')
                  .length
              }{' '}
              local
            </span>
            <span className="status-chip">
              {snapshot.providers.filter((card) => card.group === 'api').length}{' '}
              API
            </span>
            <span className="status-chip">
              {
                snapshot.providers.filter(
                  (card) => card.group === 'subscription',
                ).length
              }{' '}
              subscription
            </span>
            <span className="status-chip">
              {snapshot.providers.filter((card) => card.media_count > 0).length}{' '}
              media-capable
            </span>
          </div>
          {!snapshot.providers.length && (
            <EmptyState title="No providers">
              No provider definitions are available.
            </EmptyState>
          )}
          {(Object.keys(groups) as Array<keyof typeof groups>).map((group) => {
            const cards = snapshot.providers.filter(
              (card) => card.group === group,
            );
            return cards.length ? (
              <section
                className="settings-provider-group"
                aria-label={groups[group]}
                key={group}
              >
                <h4>{groups[group]}</h4>
                <ul className="settings-provider-list">
                  {cards.map((card) => (
                    <li key={card.provider_id}>
                      <span className="settings-provider-mark" aria-hidden>
                        {card.icon}
                      </span>
                      <span className="settings-provider-copy">
                        <span className="settings-provider-title">
                          <span
                            className={`settings-provider-readiness-dot is-${cardState(card).tone}`}
                            aria-hidden
                          />
                          <strong>{card.display_name}</strong>
                          <span>{cardState(card).label}</span>
                        </span>
                        <small title={cardDetail(card)}>
                          {cardDetail(card)}
                        </small>
                      </span>
                      <span className="settings-provider-row-meta">
                        {card.fingerprint && (
                          <span
                            className="status-chip"
                            title="Credential fingerprint"
                          >
                            {card.fingerprint}
                          </span>
                        )}
                        {card.oauth_client_id_fingerprint && (
                          <span
                            className="status-chip"
                            title="OAuth client ID fingerprint"
                          >
                            {card.oauth_client_id_fingerprint}
                          </span>
                        )}
                        {(card.account_id_hash || card.user_hash) && (
                          <span
                            className="status-chip"
                            title="Account fingerprint"
                          >
                            {card.account_id_hash || card.user_hash}
                          </span>
                        )}
                        <span className="status-chip">{card.risk_label}</span>
                        {card.last_runtime_probe_ok !== null && (
                          <span className="status-chip">
                            runtime{' '}
                            {card.last_runtime_probe_ok ? 'ok' : 'failed'}
                          </span>
                        )}
                      </span>
                      {card.group === 'api' && owner && (
                        <CompactAction
                          label={`Manage ${card.display_name} API key`}
                          onClick={() => setSelected(card.provider_id)}
                        >
                          <KeyRound size={17} aria-hidden />
                        </CompactAction>
                      )}
                      {card.group === 'subscription' &&
                        onSubscription &&
                        canConnect(card) && (
                          <CompactAction
                            label={`${card.configured ? 'Reconnect' : 'Connect'} ${card.display_name}`}
                            onClick={() =>
                              onSubscription(card.provider_id, 'connect')
                            }
                          >
                            <LogIn size={17} aria-hidden />
                          </CompactAction>
                        )}
                      {card.group === 'subscription' &&
                        card.provider_id === 'claude_subscription' &&
                        onSubscription && (
                          <CompactAction
                            label="Import Claude setup token"
                            onClick={() =>
                              onSubscription(card.provider_id, 'manage')
                            }
                          >
                            <KeyRound size={17} aria-hidden />
                          </CompactAction>
                        )}
                      {card.group === 'subscription' &&
                        card.provider_id === 'xai_oauth' &&
                        onSubscriptionOption && (
                          <CompactAction
                            label="Configure xAI OAuth client ID"
                            onClick={() =>
                              onSubscriptionOption(card.provider_id)
                            }
                          >
                            <KeyRound size={17} aria-hidden />
                          </CompactAction>
                        )}
                      {card.group === 'subscription' &&
                        ['codex', 'claude_subscription'].includes(
                          card.provider_id,
                        ) &&
                        card.external_reference_exists &&
                        !card.configured &&
                        onSubscriptionOption && (
                          <CompactAction
                            label={`Reference ${card.display_name} CLI login`}
                            onClick={() =>
                              onSubscriptionOption(card.provider_id)
                            }
                          >
                            <KeyRound size={17} aria-hidden />
                          </CompactAction>
                        )}
                      {card.group === 'subscription' &&
                        card.runtime_enabled &&
                        card.configured &&
                        testRuntime &&
                        ['claude_subscription', 'xai_oauth'].includes(
                          card.provider_id,
                        ) && (
                          <CompactAction
                            label={`Test ${card.display_name} runtime`}
                            disabled={!!testing}
                            onClick={() => void runtimeTest(card)}
                          >
                            <FlaskConical size={17} aria-hidden />
                          </CompactAction>
                        )}
                      {card.group === 'subscription' &&
                        canDisconnect(card) &&
                        onSubscription && (
                          <CompactAction
                            label={`Disconnect ${card.display_name}`}
                            onClick={() =>
                              onSubscription(card.provider_id, 'disconnect')
                            }
                          >
                            <Link2Off size={17} aria-hidden />
                          </CompactAction>
                        )}
                      <CompactAction
                        label={`Refresh ${card.display_name} provider status and catalog`}
                        disabled={!!refreshing}
                        onClick={() => void refreshProvider(card)}
                      >
                        <RefreshCw size={17} aria-hidden />
                      </CompactAction>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null;
          })}
        </>
      )}
      {selected && owner && (
        <ModalTask
          open
          title="Manage API key"
          description="Update this provider credential without leaving Settings."
          ariaLabel="Manage API key"
          onOpenChange={(open) => {
            if (!open) setSelected('');
          }}
        >
          <div className="settings-provider-dialog-content">
            <ProviderSettingsPanel
              compact
              owner={owner}
              providerId={selected}
              onSaved={() => {
                const card = snapshot?.providers.find(
                  (item) => item.provider_id === selected,
                );
                setSelected('');
                if (card) void refreshProvider(card);
                else setReload((value) => value + 1);
              }}
              onCancel={() => setSelected('')}
            />
          </div>
        </ModalTask>
      )}
    </section>
  );
}
