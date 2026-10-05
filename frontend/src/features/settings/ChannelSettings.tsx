import { useEffect, useState, useSyncExternalStore } from 'react';
import {
  ChevronDown,
  Hash,
  MessageCircle,
  MessageSquare,
  MessagesSquare,
  Send,
  type LucideIcon,
} from 'lucide-react';
import { Button, Field, Input } from '../../ui/primitives';
import { ModalTask } from '../../ui/overlays';
import { QrCode } from '../../ui/QrCode';
import { AppLink } from '../../ui/app-link';
import { humanizeToken } from '../../ui/format';
import { SettingsSummary, SummaryChip } from './anatomy';
import { ConnectSheet, CopyValue, type ConnectStep } from './ConnectSheet';
import { CHANNEL_GUIDES, START_NOTES } from './connect-guides';

export type ChannelFieldStatus = {
  key: string;
  label: string;
  field_type: 'text' | 'password' | 'number' | 'slider';
  storage: 'env' | 'config';
  help_text: string;
  configured: boolean | null;
  source: string;
  fingerprint: string;
  externally_managed: boolean;
  writable: boolean;
};
export type PairedChannelIdentity = {
  identity_id: string;
  display_name: string;
  hint: string;
};
export type ChannelStatus = {
  schema_version: 1;
  channel_id: string;
  display_name: string;
  source: { kind: string; label: string };
  revision: string;
  configured: boolean | null;
  running: boolean | null;
  activity:
    'recent' | 'within_hour' | 'within_day' | 'older' | 'none' | 'unknown';
  activity_history: Array<{
    kind: 'last_inbound';
    recency: ChannelStatus['activity'];
  }>;
  fields: ChannelFieldStatus[];
  paired_identities: PairedChannelIdentity[];
  capabilities: string[];
  /** A channel linked by scanning a code (WhatsApp), while it runs. */
  link_state?: 'starting' | 'scan' | 'linked' | null;
  /** Where a service reaches a channel that needs a public address (SMS). */
  public_address?: string | null;
  reachability_problem?: string | null;
  /** A test message to the person's own account is possible. */
  can_test?: boolean;
  availability: {
    configuration: 'available' | 'limited';
    lifecycle: 'available' | 'configuration_required';
    pairing: 'available' | 'unsupported' | 'unavailable';
    monitor: 'available' | 'unavailable';
  };
};
export type ChannelPage = {
  schema_version: 1;
  total: number;
  items: ChannelStatus[];
  truncated: boolean;
};
export type ChannelOperation =
  'configure' | 'start' | 'stop' | 'pair' | 'revoke' | 'test' | 'reset';
export type ChannelLink = {
  state: 'starting' | 'scan' | 'linked' | null;
  code?: string | null;
};
export type ChannelCommandPayload = {
  channel_id: string;
  revision: string;
  operation: ChannelOperation;
  field_key: string | null;
  value: string | number | null;
  identity_id: string | null;
};
export type ChannelCommand = {
  command_id: string;
  type: 'channel.control';
  payload: ChannelCommandPayload;
};
export type ChannelReview = {
  channel_id: string;
  revision: string;
  operation: ChannelOperation;
  field_key: string | null;
  identity_id: string | null;
  action_digest: string;
  review_id: string;
};
export type ChannelReceipt = {
  command_id: string;
  status: string;
  operation?: ChannelOperation | null;
  code?: string | null;
  pairing_code?: string | null;
  channel?: ChannelStatus | null;
};
type Attempt = { command: ChannelCommand; review: ChannelReview };
type State = {
  page: ChannelPage | null;
  query: string;
  drafts: Record<string, string>;
  reviewed: Attempt | null;
  pending: Attempt | null;
  busy: '' | 'read' | 'review' | 'execute';
  message: string;
  pairingCode: string;
  refresh: number;
  active: boolean;
};

export function createChannelSettingsSession() {
  let state: State = {
    page: null,
    query: '',
    drafts: {},
    reviewed: null,
    pending: null,
    busy: '',
    message: '',
    pairingCode: '',
    refresh: 0,
    active: true,
  };
  const listeners = new Set<() => void>();
  const aborters = new Set<AbortController>();
  const update = (patch: Partial<State>) => {
    if (!state.active) return;
    state = { ...state, ...patch };
    listeners.forEach((listener) => listener());
  };
  return {
    getSnapshot: () => state,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    update,
    setDraft: (key: string, value: string) =>
      update({ drafts: { ...state.drafts, [key]: value }, reviewed: null }),
    beginRead: () => {
      const abort = new AbortController();
      if (!state.active) abort.abort();
      else aborters.add(abort);
      return abort;
    },
    endRead: (abort: AbortController) => aborters.delete(abort),
    refresh: () => update({ refresh: state.refresh + 1 }),
    hasRetained: () =>
      Boolean(state.active && (state.reviewed || state.pending)),
    dispose: () => {
      aborters.forEach((abort) => abort.abort());
      aborters.clear();
      state = {
        page: null,
        query: '',
        drafts: {},
        reviewed: null,
        pending: null,
        busy: '',
        message: 'Sign in again to manage channels.',
        pairingCode: '',
        refresh: 0,
        active: false,
      };
      listeners.forEach((listener) => listener());
    },
  };
}
export type ChannelSettingsSession = ReturnType<
  typeof createChannelSettingsSession
>;
export type ChannelSettingsProps = {
  session: ChannelSettingsSession;
  load: (query: string, signal: AbortSignal) => Promise<ChannelPage>;
  review: (
    payload: ChannelCommandPayload,
    signal: AbortSignal,
  ) => Promise<ChannelReview>;
  execute: (
    command: ChannelCommand,
    review: ChannelReview,
  ) => Promise<ChannelReceipt>;
  /** The link code of a channel linked by scanning (owner on this computer). */
  loadLink?: (channelId: string, signal: AbortSignal) => Promise<ChannelLink>;
  /** One channel only, open, as its app's settings (Apps › Telegram › Settings). */
  only?: string;
};

const activityLabels: Record<ChannelStatus['activity'], string> = {
  recent: 'Inbound activity just now',
  within_hour: 'Inbound activity within the last hour',
  within_day: 'Inbound activity within the last day',
  older: 'Earlier inbound activity',
  none: 'No inbound activity recorded this session',
  unknown: 'Activity status unavailable',
};

const ownerChannelOrder = new Map(
  ['telegram', 'slack', 'sms', 'discord', 'whatsapp'].map((id, index) => [
    id,
    index,
  ]),
);

const ownerChannelIcons: Record<string, LucideIcon> = {
  telegram: Send,
  slack: Hash,
  sms: MessageSquare,
  discord: MessagesSquare,
  whatsapp: MessageCircle,
};

function ownerOrderedChannels(channels: ChannelStatus[]): ChannelStatus[] {
  return [...channels].sort((left, right) => {
    const leftIndex = ownerChannelOrder.get(left.channel_id);
    const rightIndex = ownerChannelOrder.get(right.channel_id);
    if (leftIndex != null || rightIndex != null)
      return (
        (leftIndex ?? Number.MAX_SAFE_INTEGER) -
        (rightIndex ?? Number.MAX_SAFE_INTEGER)
      );
    return 0;
  });
}

function validPage(value: ChannelPage): boolean {
  return (
    value.schema_version === 1 &&
    Number.isInteger(value.total) &&
    value.total >= 0 &&
    Array.isArray(value.items) &&
    value.items.length <= 50 &&
    value.items.every(
      (item) =>
        item.schema_version === 1 &&
        /^[a-z0-9][a-z0-9_.-]{0,127}$/.test(item.channel_id) &&
        /^[0-9a-f]{64}$/.test(item.revision) &&
        item.fields.length <= 64 &&
        item.paired_identities.length <= 100 &&
        item.activity_history.length <= 1,
    )
  );
}

function isPassiveCatalogChannel(channel: ChannelStatus): boolean {
  return (
    channel.availability.lifecycle === 'configuration_required' &&
    channel.availability.monitor === 'unavailable' &&
    channel.fields.every((field) => !field.writable)
  );
}

function configuredLabel(channel: ChannelStatus): string {
  if (channel.configured === true) return 'Configured';
  if (channel.configured === false) return 'Not configured';
  return 'Saved state unavailable';
}

/** "Saved via …" in words; an environment value says so (parity row 44). */
function savedLine(field: ChannelFieldStatus): string {
  if (field.configured !== true)
    return field.configured === false
      ? 'Not saved.'
      : 'Saved state unavailable.';
  if (field.source === 'environment')
    return 'Supplied by the environment. Saving a value here overrides it.';
  if (field.source === 'legacy api_keys')
    return 'Saved in the older key store; Row-Bot moves it to the channel keyring at start.';
  return `Saved via ${field.source || 'channel storage'}${field.fingerprint ? ` (${field.fingerprint})` : ''}.`;
}

/**
 * WhatsApp's live link code (B139): the QR the bridge shows, read by the
 * owner on this computer only and refreshed while it waits for a scan.
 */
function ChannelLinkCode({
  channel,
  loadLink,
}: {
  channel: ChannelStatus;
  loadLink?: ChannelSettingsProps['loadLink'];
}) {
  const [code, setCode] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const waiting =
    channel.link_state === 'starting' || channel.link_state === 'scan';
  useEffect(() => {
    if (!waiting || !loadLink) return;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const read = async () => {
      try {
        const link = await loadLink(channel.channel_id, abort.signal);
        if (abort.signal.aborted) return;
        setCode(link.state === 'scan' ? (link.code ?? null) : null);
        setUnavailable(false);
      } catch {
        if (!abort.signal.aborted) setUnavailable(true);
        return;
      }
      if (!abort.signal.aborted) timer = setTimeout(() => void read(), 2000);
    };
    void read();
    return () => {
      abort.abort();
      clearTimeout(timer);
    };
  }, [channel.channel_id, loadLink, waiting]);
  if (channel.link_state === 'linked')
    return <p role="status">Linked to your phone.</p>;
  if (!waiting) return null;
  if (!loadLink || unavailable)
    return (
      <p className="settings-help">
        Open Settings on the computer running Row-Bot to see the code.
      </p>
    );
  return code ? (
    <div className="stack connect-link-code">
      <QrCode
        value={code}
        label={`${channel.display_name} link code`}
        size={200}
      />
      <small>
        On your phone: WhatsApp › Settings › Linked devices › Link a device,
        then scan this code. It refreshes by itself.
      </small>
    </div>
  ) : (
    <p role="status">Waiting for the code…</p>
  );
}

export default function ChannelSettings({
  session,
  load,
  review,
  execute,
  loadLink,
  only,
}: ChannelSettingsProps) {
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  // Outward or unlinking actions ask first: a test message to the person's
  // own account, and WhatsApp's Reset session.
  const [confirm, setConfirm] = useState<{
    channel: ChannelStatus;
    operation: 'test' | 'reset';
  } | null>(null);

  useEffect(() => {
    if (!session.getSnapshot().active) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let remaining = 30;
    const read = async () => {
      const current = session.getSnapshot();
      if (
        disposed ||
        current.busy === 'read' ||
        document.visibilityState === 'hidden' ||
        remaining <= 0
      )
        return;
      remaining--;
      const abort = session.beginRead();
      session.update({ busy: 'read' });
      let observe = false;
      try {
        const page = await load(current.query, abort.signal);
        if (abort.signal.aborted || disposed) return;
        if (!validPage(page)) throw Error('invalid channel page');
        session.update({ page, busy: '' });
        observe = Boolean(
          session.getSnapshot().pending ||
          page.items.some((item) => item.running),
        );
      } catch {
        if (!abort.signal.aborted && !disposed)
          session.update({
            busy: '',
            message: 'Channel status is unavailable. Refresh to try again.',
          });
      } finally {
        session.endRead(abort);
        if (observe && !disposed && remaining > 0)
          timer = setTimeout(() => void read(), 5000);
      }
    };
    const visible = () => {
      clearTimeout(timer);
      if (document.visibilityState !== 'hidden') void read();
    };
    document.addEventListener('visibilitychange', visible);
    void read();
    return () => {
      disposed = true;
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', visible);
    };
  }, [load, session, state.refresh, state.active]);

  const requestReview = async (
    channel: ChannelStatus,
    operation: ChannelOperation,
    field: ChannelFieldStatus | null = null,
    identityId: string | null = null,
  ) => {
    const current = session.getSnapshot();
    if (!current.active || current.busy || current.pending) return;
    let value: string | number | null = null;
    if (field) {
      const raw = current.drafts[`${channel.channel_id}:${field.key}`] ?? '';
      if (raw.length > 16384) {
        session.update({ message: 'The channel value is too large.' });
        return;
      }
      if (field.field_type === 'number' || field.field_type === 'slider') {
        const parsed = Number(raw);
        if (!raw || !Number.isFinite(parsed)) {
          session.update({ message: 'Enter a valid number.' });
          return;
        }
        value = parsed;
      } else {
        value = raw || null;
      }
    }
    const payload: ChannelCommandPayload = {
      channel_id: channel.channel_id,
      revision: channel.revision,
      operation,
      field_key: field?.key ?? null,
      value,
      identity_id: identityId,
    };
    const command: ChannelCommand = {
      command_id: crypto.randomUUID(),
      type: 'channel.control',
      payload,
    };
    const abort = session.beginRead();
    session.update({
      busy: 'review',
      reviewed: null,
      message: '',
      pairingCode: '',
    });
    try {
      const reviewed = await review(payload, abort.signal);
      if (abort.signal.aborted) return;
      if (
        reviewed.channel_id !== payload.channel_id ||
        reviewed.revision !== payload.revision ||
        reviewed.operation !== payload.operation ||
        reviewed.field_key !== payload.field_key ||
        reviewed.identity_id !== payload.identity_id
      )
        throw Error('review mismatch');
      const attempt = { command, review: reviewed };
      session.update({ reviewed: attempt, busy: '' });
      void submit(attempt);
    } catch {
      if (!abort.signal.aborted)
        session.update({
          busy: '',
          message:
            'The channel action could not be validated. Refresh and try again.',
        });
    } finally {
      session.endRead(abort);
    }
  };

  const submit = async (attempt: Attempt | null) => {
    const current = session.getSnapshot();
    if (!attempt || !current.active || current.busy) return;
    if (current.reviewed !== attempt && current.pending !== attempt) return;
    session.update({
      reviewed: null,
      pending: attempt,
      busy: 'execute',
      message: '',
      pairingCode: '',
    });
    try {
      const receipt = await execute(attempt.command, attempt.review);
      if (!session.getSnapshot().active) return;
      if (receipt.command_id !== attempt.command.command_id) throw Error();
      if (receipt.status === 'completed') {
        const fieldKey = attempt.command.payload.field_key;
        const drafts = { ...session.getSnapshot().drafts };
        if (fieldKey)
          delete drafts[`${attempt.command.payload.channel_id}:${fieldKey}`];
        session.update({
          pending: null,
          busy: '',
          drafts,
          pairingCode: receipt.pairing_code ?? '',
          message:
            receipt.code === 'channel_start_failed'
              ? 'The adapter reported that it did not start. Review its configuration.'
              : attempt.command.payload.operation === 'test'
                ? 'Test message sent. Check the channel on your phone.'
                : 'Channel action completed.',
        });
      } else if (receipt.status === 'rejected') {
        session.update({
          pending: null,
          busy: '',
          message: 'The channel action was rejected. Refresh and try again.',
        });
      } else {
        session.update({
          busy: '',
          message:
            'The outcome is uncertain. Check the original action; it will not be repeated.',
        });
      }
    } catch {
      if (session.getSnapshot().active)
        session.update({
          busy: '',
          message:
            'The outcome is uncertain. Check the original action before making another change.',
        });
    } finally {
      session.refresh();
    }
  };

  const locked = !state.active || Boolean(state.busy) || Boolean(state.pending);

  /** One saved field: a write-only input with Save and Clear. */
  const fieldEditor = (channel: ChannelStatus, field: ChannelFieldStatus) => {
    const draftKey = `${channel.channel_id}:${field.key}`;
    return (
      <div className="stack" key={field.key}>
        <Field label={`New ${field.label}`} hint={field.help_text || undefined}>
          <Input
            type={
              field.field_type === 'password'
                ? 'password'
                : field.field_type === 'number' || field.field_type === 'slider'
                  ? 'number'
                  : 'text'
            }
            autoComplete="off"
            value={state.drafts[draftKey] ?? ''}
            maxLength={16384}
            disabled={locked || !field.writable}
            onChange={(event) => session.setDraft(draftKey, event.target.value)}
          />
        </Field>
        <p>
          {savedLine(field)}
          {field.externally_managed ? ' Managed by the server operator.' : ''}
        </p>
        <div className="actions">
          <Button
            disabled={
              locked || !field.writable || !(state.drafts[draftKey] ?? '')
            }
            onClick={() => void requestReview(channel, 'configure', field)}
          >
            Save {field.label}
          </Button>
          {field.source !== 'environment' && (
            // An environment value can't be cleared from here.
            <Button
              disabled={
                locked ||
                !field.writable ||
                field.configured !== true ||
                Boolean(state.drafts[draftKey])
              }
              onClick={() => void requestReview(channel, 'configure', field)}
            >
              Clear {field.label}
            </Button>
          )}
        </div>
      </div>
    );
  };

  /** Fields no guide step fills (a channel without a guide has none). */
  const extraFields = (channel: ChannelStatus) => {
    const guide = CHANNEL_GUIDES[channel.channel_id];
    if (!guide) return [];
    const guided = new Set(guide.map((step) => step.field));
    return channel.fields.filter((field) => !guided.has(field.key));
  };

  /**
   * The channel's connect sheet (parity rows 42–44, B139): its own steps
   * with links and fields in place, Start, a way to pair, WhatsApp's code
   * and Reset session, and "Send a test message to me".
   */
  const channelSteps = (channel: ChannelStatus): ConnectStep[] => {
    const fields = new Map(channel.fields.map((field) => [field.key, field]));
    const linking =
      channel.link_state === 'starting' || channel.link_state === 'scan';
    const guide = CHANNEL_GUIDES[channel.channel_id];
    const steps: ConnectStep[] = (guide ?? []).map((step, index) => {
      const field = step.field ? fields.get(step.field) : undefined;
      return {
        id: `guide-${index}`,
        text: step.text,
        link: step.link,
        done: field ? field.configured === true : undefined,
        children: field ? fieldEditor(channel, field) : undefined,
      };
    });
    if (!guide && channel.fields.length)
      // A plugin channel: its settings in one step.
      steps.push({
        id: 'settings',
        text: 'Fill in its settings.',
        done: channel.fields.every((field) => field.configured === true),
        children: (
          <div className="stack">
            {channel.fields.map((field) => fieldEditor(channel, field))}
          </div>
        ),
      });
    steps.push({
      id: 'start',
      text:
        START_NOTES[channel.channel_id] ??
        `Start ${channel.display_name}. It starts again with Row-Bot until you stop it.`,
      done: channel.running === true,
      children: (
        <div
          className="actions"
          role="group"
          aria-label={`${channel.display_name} lifecycle`}
        >
          <Button
            variant={
              channel.running === true || linking ? 'secondary' : 'primary'
            }
            disabled={
              locked ||
              channel.availability.lifecycle !== 'available' ||
              channel.running === true ||
              linking
            }
            onClick={() => void requestReview(channel, 'start')}
          >
            Start {channel.display_name}
          </Button>
          <Button
            disabled={locked || (channel.running !== true && !linking)}
            onClick={() => void requestReview(channel, 'stop')}
          >
            Stop {channel.display_name}
          </Button>
        </div>
      ),
    });
    if (channel.link_state !== undefined && channel.link_state !== null)
      steps.push({
        id: 'link',
        text: 'Scan the code with your phone to link it.',
        done: channel.link_state === 'linked',
        children: (
          <div className="stack">
            <ChannelLinkCode channel={channel} loadLink={loadLink} />
            <div className="actions">
              <Button
                disabled={locked}
                onClick={() => setConfirm({ channel, operation: 'reset' })}
              >
                Reset session
              </Button>
            </div>
          </div>
        ),
      });
    if (channel.availability.pairing === 'available')
      steps.push({
        id: 'pair',
        text: 'Or approve your account from the chat app: get a code and send it to the bot.',
        children: (
          <div className="actions">
            <Button
              disabled={locked}
              onClick={() => void requestReview(channel, 'pair')}
            >
              Get pairing code for {channel.display_name}
            </Button>
          </div>
        ),
      });
    else if (channel.availability.pairing === 'unavailable')
      steps.push({
        id: 'pair',
        text: 'Pairing is not available for this channel; it approves people its own way.',
      });
    steps.push({
      id: 'test',
      text: channel.can_test
        ? 'Send yourself a test message to check it works.'
        : channel.running === true
          ? 'Add your user ID or pair your account, then send yourself a test message.'
          : 'Once it runs, send yourself a test message to check it works.',
      children: (
        <div className="actions">
          <Button
            disabled={locked || !channel.can_test}
            onClick={() => setConfirm({ channel, operation: 'test' })}
          >
            Send a test message to me
          </Button>
        </div>
      ),
    });
    return steps;
  };
  const channels = ownerOrderedChannels(state.page?.items ?? []).filter(
    (channel) => !only || channel.channel_id === only,
  );
  const passiveChannels = channels.filter(isPassiveCatalogChannel);
  const managedChannels = channels.filter(
    (channel) => !isPassiveCatalogChannel(channel),
  );
  const runtimeKnown = channels.every((channel) => channel.running !== null);
  return (
    <section aria-label="Channels" className="stack settings-channel-page">
      {state.page && !only && (
        <>
          <SettingsSummary>
            <span
              className="settings-summary-group"
              role="group"
              aria-label="Channel totals"
              aria-live="polite"
            >
              <SummaryChip>
                {
                  channels.filter((channel) => channel.configured === true)
                    .length
                }{' '}
                configured
              </SummaryChip>
              <SummaryChip
                tone={
                  channels.some((channel) => channel.running === true)
                    ? 'success'
                    : undefined
                }
              >
                {runtimeKnown
                  ? `${channels.filter((channel) => channel.running === true).length} running`
                  : 'Runtime status not loaded'}
              </SummaryChip>
            </span>
          </SettingsSummary>
          <p className="settings-help">
            Messages only go out through channels you start. A channel that
            needs a public address opens your tunnel by itself;{' '}
            <AppLink
              className="settings-inline-action"
              to="/settings/access#tunnel"
            >
              its setup is in Devices &amp; remote access
            </AppLink>
            .
          </p>
        </>
      )}
      {passiveChannels.length > 0 && (
        <div role="group" aria-label="Bundled channel summaries">
          {passiveChannels.map((channel) => (
            <details
              className="settings-account-panel"
              open={only ? true : undefined}
              key={channel.channel_id}
            >
              <summary>
                {(() => {
                  const ChannelIcon =
                    ownerChannelIcons[channel.channel_id] ?? MessageSquare;
                  return <ChannelIcon size={20} aria-hidden />;
                })()}
                <strong>{channel.display_name}</strong>
                <span
                  className={`status-chip ${
                    channel.configured === false ? 'warning' : ''
                  }`}
                >
                  {channel.configured === true
                    ? 'Stopped'
                    : configuredLabel(channel)}
                </span>
                <ChevronDown
                  className="settings-disclosure-chevron"
                  size={17}
                  aria-hidden
                />
              </summary>
              <div className="settings-account-content stack">
                <p>
                  The adapter is not loaded, so lifecycle and activity status
                  have not been inferred.
                </p>
                {channel.capabilities.length > 0 && (
                  <p>{capabilityWords(channel.capabilities)}</p>
                )}
                <ul
                  className="settings-compact-list"
                  aria-label={`${channel.display_name} saved fields`}
                >
                  {channel.fields.map((field) => (
                    <li key={field.key}>
                      <strong>{field.label}</strong>
                      <span>
                        {field.configured === true
                          ? `Saved via ${field.source || 'channel storage'}${field.fingerprint ? ` (${field.fingerprint})` : ''}`
                          : field.configured === false
                            ? 'Not saved'
                            : 'Saved state unavailable'}
                        {field.externally_managed
                          ? ' · Managed by the server operator'
                          : ''}
                      </span>
                      <span>{field.storage}</span>
                    </li>
                  ))}
                </ul>
                <p className="settings-help">
                  Configuration and lifecycle actions become available when the
                  adapter is loaded.
                </p>
              </div>
            </details>
          ))}
        </div>
      )}
      {managedChannels.map((channel) => (
        <details
          className="settings-account-panel"
          open={only ? true : undefined}
          aria-label={`${channel.display_name} channel`}
          data-setting-anchor={channel.channel_id}
          key={channel.channel_id}
        >
          <summary>
            {(() => {
              const ChannelIcon =
                ownerChannelIcons[channel.channel_id] ?? MessageSquare;
              return <ChannelIcon size={20} aria-hidden />;
            })()}
            <strong>{channel.display_name}</strong>
            <span
              className={`status-chip ${
                channel.configured === false || channel.reachability_problem
                  ? 'warning'
                  : ''
              }`}
            >
              {channel.running === true
                ? channel.reachability_problem
                  ? 'Running · not reachable'
                  : 'Running'
                : channel.link_state === 'starting' ||
                    channel.link_state === 'scan'
                  ? 'Waiting for a scan'
                  : channel.configured === true
                    ? 'Stopped'
                    : channel.configured === false
                      ? 'Not configured'
                      : 'Status unavailable'}
            </span>
            <ChevronDown
              className="settings-disclosure-chevron"
              size={17}
              aria-hidden
            />
          </summary>
          <div className="settings-account-content stack">
            <p>
              {activityLabels[channel.activity]}
              {channel.source.kind === 'plugin' ? ' · Plugin channel' : ''}
            </p>
            {channel.capabilities.length > 0 && (
              <p>{capabilityWords(channel.capabilities)}</p>
            )}
            {channel.public_address && (
              // A channel that needs a public address opened the tunnel
              // itself (parity row 41); no per-channel switch.
              <p className="connect-reachable">
                Reachable at{' '}
                <CopyValue
                  value={channel.public_address}
                  label={`${channel.display_name} address`}
                />
              </p>
            )}
            {channel.reachability_problem && (
              <p role="status" className="settings-help">
                {channel.reachability_problem}
              </p>
            )}
            <ConnectSheet
              title={`Connect ${channel.display_name}`}
              steps={channelSteps(channel)}
            />
            {extraFields(channel).length > 0 && (
              <details className="settings-plugin-details">
                <summary>More settings</summary>
                <div className="stack">
                  {extraFields(channel).map((field) =>
                    fieldEditor(channel, field),
                  )}
                </div>
              </details>
            )}
            {channel.paired_identities.length > 0 && (
              <div className="stack">
                <h4>Paired accounts</h4>
                {channel.paired_identities.map((identity) => (
                  <div className="actions" key={identity.identity_id}>
                    <span>
                      {identity.display_name || 'Paired account'} (
                      {identity.hint})
                    </span>
                    <Button
                      disabled={locked}
                      onClick={() =>
                        void requestReview(
                          channel,
                          'revoke',
                          null,
                          identity.identity_id,
                        )
                      }
                    >
                      Revoke {identity.display_name || identity.hint}
                    </Button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </details>
      ))}
      {confirm && (
        <ModalTask
          open
          onOpenChange={(open) => {
            if (!open) setConfirm(null);
          }}
          title={
            confirm.operation === 'test'
              ? `Send a test message to you on ${confirm.channel.display_name}?`
              : `Reset the ${confirm.channel.display_name} session?`
          }
          description={
            confirm.operation === 'test'
              ? 'Row-Bot sends one short message to your own account on this channel, to check it works.'
              : 'This unlinks Row-Bot from your phone and shows a new code to scan. Your phone lists the old link until you remove it there.'
          }
          ariaLabel={
            confirm.operation === 'test'
              ? `Send a test message on ${confirm.channel.display_name}`
              : `Reset ${confirm.channel.display_name} session`
          }
        >
          <div className="button-row">
            <Button onClick={() => setConfirm(null)}>Cancel</Button>
            <Button
              variant={confirm.operation === 'reset' ? 'danger' : 'primary'}
              onClick={() => {
                const current = confirm;
                setConfirm(null);
                void requestReview(current.channel, current.operation);
              }}
            >
              {confirm.operation === 'test'
                ? 'Send test message'
                : 'Reset session'}
            </Button>
          </div>
        </ModalTask>
      )}
      {state.pending && (
        <Button
          disabled={Boolean(state.busy) || !state.active}
          onClick={() => void submit(state.pending)}
        >
          Check original channel action
        </Button>
      )}
      {state.pairingCode && (
        <p role="status">
          Pairing code: <strong>{state.pairingCode}</strong>. Send it to the bot
          from the account you want to approve; it expires after one hour.
        </p>
      )}
      {state.message && <p role="status">{state.message}</p>}
    </section>
  );
}

// What a channel can carry, in words (U59): never "photo_in, voice_in".
const CAPABILITIES: Record<string, string> = {
  text_in: 'receives messages',
  text_out: 'sends messages',
  photo_in: 'receives photos',
  photo_out: 'sends photos',
  voice_in: 'receives voice notes',
  voice_out: 'sends voice notes',
  document_in: 'receives files',
  document_out: 'sends files',
  video_in: 'receives videos',
  video_out: 'sends videos',
  slash_commands: 'takes slash commands',
};

function capabilityWords(capabilities: readonly string[]) {
  const words = capabilities.map(
    (item) => CAPABILITIES[item] ?? humanizeToken(item).toLowerCase(),
  );
  if (!words.length) return 'Text messages only.';
  const text = words.join(', ');
  return `${text.charAt(0).toUpperCase()}${text.slice(1)}.`;
}
