import {
  ChevronRight,
  FlaskConical,
  KeyRound,
  Link2Off,
  LogIn,
  Network,
  Plus,
  RefreshCw,
} from 'lucide-react';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import type {
  ProviderCatalogRefresh,
  ProviderLiveCard,
  ProviderLiveSnapshot,
  ProviderRuntimeProbe,
} from '../../api/types';
import { clientError } from '../../api/errors';
import {
  Button,
  Disclosure,
  ErrorState,
  IconButton,
  Skeleton,
  StatusDot,
  Tabs,
  type Tone,
} from '../../ui/primitives';
import { humanizeToken, maskedTail } from '../../ui/format';
import { SettingsGroup, SettingsRefresh } from './anatomy';
import { Drawer, ModalTask } from '../../ui/overlays';
import ProviderSettingsPanel from './ProviderSettingsPanel';
import type { createProviderSettingsSessions } from './provider-settings-sessions';
import { BillingTag, billingLabel } from '../shell/ModelList';
import ClaudePlanSetup, { claudePlanSummary } from './ClaudePlanSetup';

const groups = {
  local: 'On this device',
  subscription: 'Subscriptions',
  api: 'API providers',
  custom: 'Custom endpoints',
} as const;
const stateTones: Record<string, Tone> = {
  enabled: 'success',
  saved: 'info',
  warning: 'warning',
  unknown: 'neutral',
};
const sourceLabels: Record<string, string> = {
  keyring: 'Saved in keyring',
  encrypted_file: 'Saved in encrypted server storage',
  environment: 'Using environment variable',
  secret_file: 'Using read-only server secret',
  session: 'Using session key',
  api_keys: 'Saved API key',
  oauth_device: 'Signed in with ChatGPT',
  oauth_pkce: 'Signed in through Row-Bot',
  no_auth: 'No API key required',
  local_daemon: 'Running on this computer',
  not_running: 'Not running',
};
/** The app whose sign-in a subscription can borrow. */
const cliApps: Record<string, string> = {
  codex: 'Codex',
};
/** The account a subscription signs in with (no key involved). */
const subscriptionAccounts: Record<string, string> = {
  codex: 'ChatGPT',
};
function notConnectedDetail(card: ProviderLiveCard) {
  if (card.group !== 'subscription') return 'Needs an API key';
  const account = subscriptionAccounts[card.provider_id] ?? card.display_name;
  return `Sign in with your ${account} account`;
}
function sourceLabel(card: ProviderLiveCard) {
  const app = cliApps[card.provider_id] ?? 'its own app';
  if (card.source === 'external_cli') return `Uses your ${app} sign-in`;
  if (card.source === 'external_cli_detected')
    return `${app} is signed in on this computer; not used yet`;
  return sourceLabels[card.source];
}
/** One mark style for every provider: its initial on a tile. */
function providerMark(card: ProviderLiveCard) {
  const icon = card.icon.trim();
  return /^[\p{L}\p{N}]{1,2}$/u.test(icon)
    ? icon
    : (card.display_name.trim().charAt(0) || '?').toUpperCase();
}
/** Whether a row has a model list worth refreshing on its own. */
function hasModels(card: ProviderLiveCard) {
  return card.configured || card.runtime_enabled || card.group === 'local';
}
/** A saved subscription sign-in that can't be used any more. */
function needsReconnect(card: ProviderLiveCard) {
  return (
    card.configured && card.group === 'subscription' && !card.runtime_enabled
  );
}
function cardState(card: ProviderLiveCard) {
  // Claude runs the person's own Claude Code: its state is what Claude Code says.
  if (card.claude_plan) {
    const plan = claudePlanSummary(card.claude_plan);
    return {
      label: plan.label,
      tone: plan.tone === 'success' ? 'enabled' : plan.tone,
    };
  }
  if (needsReconnect(card)) return { label: 'Reconnect', tone: 'warning' };
  if (card.configured && card.source === 'external_cli')
    return { label: 'Linked', tone: 'saved' };
  if (card.configured) return { label: 'Connected', tone: 'enabled' };
  // Signed in on this computer but not in Row-Bot: its hint says so, and its
  // dialog says Not connected too (U4).
  if (card.source === 'external_cli_detected')
    return { label: 'Not connected', tone: 'unknown' };
  if (card.source === 'not_running')
    return { label: 'Not running', tone: 'warning' };
  return { label: 'Not connected', tone: 'unknown' };
}
function canConnect(card: ProviderLiveCard) {
  if (card.provider_id === 'codex')
    return (
      card.source !== 'oauth_device' || !card.configured || needsReconnect(card)
    );
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
    (card.provider_id === 'codex' && card.source === 'external_cli')
  );
}
function cardDetail(card: ProviderLiveCard) {
  if (card.claude_plan) {
    const details = [claudePlanSummary(card.claude_plan).detail];
    if (card.claude_plan.state === 'ready') {
      if (card.claude_plan.plan) details.push(card.claude_plan.plan);
      if (card.model_count) details.push(`${card.model_count} models`);
    }
    return details.join(' · ');
  }
  const source =
    card.configured && card.provider_id === 'codex' && !card.runtime_enabled
      ? 'Reconnect ChatGPT to use Codex models in chat'
      : card.configured &&
          card.provider_id === 'xai_oauth' &&
          !card.runtime_enabled
        ? 'Reconnect xAI Grok to use its models in chat'
        : card.provider_id === 'xai_oauth' && !card.configured
          ? card.oauth_client_id_configured
            ? 'Ready to sign in with your xAI account'
            : 'Needs an xAI client ID first: Set up xAI sign-in'
          : sourceLabel(card) ||
            (card.configured ? 'Connected' : notConnectedDetail(card));
  const details = [source];
  if (card.plan_type) details.push(`${humanizeToken(card.plan_type)} plan`);
  if (card.model_count !== null)
    details.push(
      `${card.model_count} ${card.model_count_source.includes('fallback') ? 'known models' : 'models'}`,
    );
  else if (card.configured || card.runtime_enabled)
    details.push('model count not known yet');
  if (card.last_runtime_probe_ok != null)
    details.push(
      card.last_runtime_probe_ok ? 'last test worked' : 'last test failed',
    );
  if (card.group === 'api' && card.configured && card.fingerprint)
    details.push(`key ${maskedTail(card.fingerprint)}`);
  return details.join(' · ');
}

type SheetAction = {
  label: string;
  icon: ReactNode;
  onSelect: () => void;
  disabled?: boolean;
  danger?: boolean;
};
/** What a tile says under its name: how it is paid and its models, or what's wrong. */
function tileDetail(card: ProviderLiveCard, plan = false) {
  const state = cardState(card);
  if (state.tone === 'warning' || !card.configured) return state.label;
  return [
    billingLabel(card.billing ?? null),
    plan && card.plan_type ? `${humanizeToken(card.plan_type)} plan` : '',
    card.model_count != null
      ? `${card.model_count} model${card.model_count === 1 ? '' : 's'}`
      : '',
  ]
    .filter(Boolean)
    .join(' · ');
}
/**
 * A sheet's line under its title: its state, then payment, plan and models,
 * or in full what's wrong (and what Claude Code says).
 */
function sheetSummary(card: ProviderLiveCard) {
  const state = cardState(card);
  const detail =
    card.claude_plan || state.tone === 'warning' || !card.configured
      ? cardDetail(card)
      : tileDetail(card, true);
  return [state.label, detail].filter(Boolean).join(' · ');
}
/** Connected providers are tiles; the rest wait in Add a provider. Local runtimes always show. */
function isTile(card: ProviderLiveCard) {
  return card.configured || card.group === 'local';
}
const galleryTabs: { id: keyof typeof groups | 'server'; label: string }[] = [
  { id: 'local', label: 'On this computer' },
  { id: 'subscription', label: 'Subscription' },
  { id: 'api', label: 'API key' },
  { id: 'server', label: 'Your server' },
];

/**
 * Settings › Models › Connected: a tile for each connected provider, which
 * opens its sheet (status, the one way to connect, its models with stars),
 * and Add a provider, a gallery of the rest by how they are paid for.
 */
export default function ProviderStatus({
  load,
  refresh,
  refreshState,
  testRuntime,
  owner,
  onSubscription,
  onSubscriptionOption,
  hideCustom = false,
  models,
  openProvider = '',
  onAddServer,
}: {
  load: (signal?: AbortSignal) => Promise<ProviderLiveSnapshot>;
  refresh: (provider: string) => Promise<ProviderCatalogRefresh>;
  refreshState: (signal?: AbortSignal) => Promise<ProviderCatalogRefresh>;
  testRuntime?: (provider: string) => Promise<ProviderRuntimeProbe>;
  owner?: ReturnType<typeof createProviderSettingsSessions>;
  onSubscription?: (
    provider: string,
    action: 'connect' | 'manage' | 'disconnect',
    expired: boolean,
  ) => void;
  onSubscriptionOption?: (provider: string) => void;
  /** Custom endpoints listed by their own editor below. */
  hideCustom?: boolean;
  /** A provider's models in its sheet, with their stars. */
  models?: (card: ProviderLiveCard) => ReactNode;
  /** The provider whose sheet opens first (`?provider=` in a link). */
  openProvider?: string;
  /** Add a provider › Your server: the custom endpoint dialog. */
  onAddServer?: () => void;
}) {
  const [snapshot, setSnapshot] = useState<ProviderLiveSnapshot | null>(null);
  const [open, setOpen] = useState(openProvider);
  const [adding, setAdding] = useState(false);
  const [tab, setTab] = useState('');
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
  const cards = (snapshot?.providers ?? []).filter(
    (card) => !(card.group === 'custom' && hideCustom),
  );
  const tiles = cards.filter(isTile);
  const waiting = (id: string) =>
    cards.filter((card) => card.group === id && !isTile(card));
  const tabs = galleryTabs.filter((item) =>
    item.id === 'server' ? !!onAddServer : waiting(item.id).length > 0,
  );
  const galleryTab = tabs.some((item) => item.id === tab)
    ? tab
    : (tabs[0]?.id ?? '');
  const current = cards.find((card) => card.provider_id === open);
  /** A subscription's sign-in steps open their own dialog, so the sheet closes first. */
  const handOff = (run: () => void) => () => {
    setOpen('');
    run();
  };
  const sheetActions = (card: ProviderLiveCard): SheetAction[] => {
    const actions: SheetAction[] = [];
    if (card.group !== 'subscription' || card.claude_plan) return actions;
    if (onSubscription && canConnect(card))
      actions.push({
        label: `${card.configured ? 'Reconnect' : 'Connect'} ${card.display_name}`,
        icon: <LogIn size={16} aria-hidden />,
        onSelect: handOff(() =>
          onSubscription(card.provider_id, 'connect', needsReconnect(card)),
        ),
      });
    if (card.provider_id === 'xai_oauth' && onSubscriptionOption)
      actions.push({
        label: 'Set up xAI sign-in',
        icon: <KeyRound size={16} aria-hidden />,
        onSelect: handOff(() => onSubscriptionOption(card.provider_id)),
      });
    if (
      card.provider_id === 'codex' &&
      card.external_reference_exists &&
      !card.configured &&
      onSubscriptionOption
    )
      actions.push({
        label: `Use your ${cliApps[card.provider_id] ?? card.display_name} sign-in`,
        icon: <KeyRound size={16} aria-hidden />,
        onSelect: handOff(() => onSubscriptionOption(card.provider_id)),
      });
    if (
      card.provider_id === 'xai_oauth' &&
      card.runtime_enabled &&
      card.configured &&
      testRuntime
    )
      actions.push({
        label: `Test ${card.display_name} runtime`,
        icon: <FlaskConical size={16} aria-hidden />,
        disabled: !!testing,
        onSelect: () => void runtimeTest(card),
      });
    if (canDisconnect(card) && onSubscription)
      actions.push({
        label: `Disconnect ${card.display_name}`,
        icon: <Link2Off size={16} aria-hidden />,
        danger: true,
        onSelect: handOff(() =>
          onSubscription(card.provider_id, 'disconnect', needsReconnect(card)),
        ),
      });
    return actions;
  };
  const mark = (card: ProviderLiveCard) => (
    <span className="settings-provider-mark" aria-hidden>
      {providerMark(card)}
    </span>
  );
  const tile = (card: ProviderLiveCard) => {
    const state = cardState(card);
    return (
      <li key={card.provider_id}>
        <button
          type="button"
          className="settings-provider-tile"
          aria-haspopup="dialog"
          onClick={() => setOpen(card.provider_id)}
        >
          {mark(card)}
          <span className="settings-provider-copy">
            <strong>{card.display_name}</strong>
            <small>{tileDetail(card)}</small>
          </span>
          <StatusDot
            tone={stateTones[state.tone] ?? 'neutral'}
            label={state.label}
          />
        </button>
      </li>
    );
  };
  return (
    <SettingsGroup
      title="Connected"
      anchor="connections"
      surface={false}
      className="settings-provider-status"
      meta={
        <SettingsRefresh
          label="Reload provider status"
          busy={loading}
          onRefresh={() => setReload((value) => value + 1)}
        />
      }
    >
      <div
        className="stack"
        id="provider-saved-status"
        aria-label="Provider connections"
        aria-busy={loading}
      >
        {notice && (
          <p className="settings-provider-notice" role="status">
            {notice}
          </p>
        )}
        {loading && !snapshot && (
          <Skeleton label="Checking provider connections" />
        )}
        {error && (
          <ErrorState title="Provider information unavailable">
            {error}
          </ErrorState>
        )}
        {snapshot && (
          <>
            {!tiles.length && (
              <p className="settings-provider-empty">
                Nothing is connected yet. Add a provider to choose its models.
              </p>
            )}
            {(Object.keys(groups) as Array<keyof typeof groups>).map(
              (group) => {
                const members = tiles.filter((card) => card.group === group);
                if (!members.length) return null;
                return (
                  <section
                    className="settings-provider-group"
                    aria-label={groups[group]}
                    key={group}
                    data-setting-anchor={`providers-${group}`}
                  >
                    <h4 className="settings-provider-group-heading">
                      {groups[group]}
                    </h4>
                    <ul className="settings-provider-tiles">
                      {members.map(tile)}
                    </ul>
                  </section>
                );
              },
            )}
            {tabs.length > 0 && (
              <button
                type="button"
                className="settings-provider-add"
                aria-haspopup="dialog"
                onClick={() => setAdding(true)}
              >
                <Plus size={16} aria-hidden />
                Add a provider
              </button>
            )}
          </>
        )}
      </div>
      {current && (
        <Drawer
          open
          modal
          title={current.display_name}
          description={sheetSummary(current)}
          className="settings-provider-sheet"
          onOpenChange={(value) => {
            if (!value) setOpen('');
          }}
          actions={
            hasModels(current) ? (
              <IconButton
                size="sm"
                label={`Refresh ${current.display_name} model list`}
                disabled={!!refreshing}
                onClick={() => void refreshProvider(current)}
              >
                <RefreshCw
                  size={16}
                  aria-hidden
                  data-spinning={
                    refreshing === current.provider_id || undefined
                  }
                />
              </IconButton>
            ) : undefined
          }
        >
          <div className="stack settings-provider-sheet-body">
            {notice && (
              <p className="settings-provider-notice" role="status">
                {notice}
              </p>
            )}
            {current.group === 'api' && owner && (
              <Disclosure
                className="settings-provider-key"
                summary="API key"
                meta={
                  current.configured && current.fingerprint
                    ? `key ${maskedTail(current.fingerprint)}`
                    : 'Not saved'
                }
                defaultOpen={!current.configured}
              >
                <ProviderSettingsPanel
                  compact
                  owner={owner}
                  providerId={current.provider_id}
                  onSaved={() => void refreshProvider(current)}
                  onCancel={() => setOpen('')}
                />
              </Disclosure>
            )}
            {current.claude_plan && (
              <ClaudePlanSetup
                plan={current.claude_plan}
                checking={loading}
                onCheck={() => setReload((value) => value + 1)}
                onDone={() => setOpen('')}
              />
            )}
            {sheetActions(current).length > 0 && (
              <div className="settings-provider-sheet-actions">
                {sheetActions(current).map((action) => (
                  <Button
                    key={action.label}
                    variant={action.danger ? 'danger' : undefined}
                    disabled={action.disabled}
                    onClick={() => action.onSelect()}
                  >
                    {action.icon}
                    {action.label}
                  </Button>
                ))}
              </div>
            )}
            {models && hasModels(current) && models(current)}
          </div>
        </Drawer>
      )}
      {adding && (
        <ModalTask
          open
          title="Add a provider"
          description="Grouped by how you pay. Pick one to connect it."
          ariaLabel="Add a provider"
          className="settings-provider-gallery"
          onOpenChange={(value) => {
            if (!value) setAdding(false);
          }}
        >
          <Tabs
            label="Kinds of provider"
            value={galleryTab}
            onChange={setTab}
            items={tabs.map((item) => ({
              id: item.id,
              label: item.label,
              content:
                item.id === 'server' ? (
                  <ul className="settings-provider-options">
                    <li>
                      <button
                        type="button"
                        className="settings-provider-option"
                        onClick={() => {
                          setAdding(false);
                          onAddServer?.();
                        }}
                      >
                        <span className="settings-provider-mark" aria-hidden>
                          <Network size={16} aria-hidden />
                        </span>
                        <span className="settings-provider-copy">
                          <strong>OpenAI-compatible server</strong>
                          <small>Its address, and a key if it needs one</small>
                        </span>
                        <ChevronRight size={16} aria-hidden />
                      </button>
                    </li>
                  </ul>
                ) : (
                  <ul className="settings-provider-options">
                    {waiting(item.id).map((card) => (
                      <li key={card.provider_id}>
                        <button
                          type="button"
                          className="settings-provider-option"
                          onClick={() => {
                            setAdding(false);
                            setOpen(card.provider_id);
                          }}
                        >
                          {mark(card)}
                          <span className="settings-provider-copy">
                            <strong>{card.display_name}</strong>
                            <small>{cardDetail(card)}</small>
                          </span>
                          <BillingTag billing={card.billing ?? null} />
                          <ChevronRight size={16} aria-hidden />
                        </button>
                      </li>
                    ))}
                  </ul>
                ),
            }))}
          />
        </ModalTask>
      )}
    </SettingsGroup>
  );
}
