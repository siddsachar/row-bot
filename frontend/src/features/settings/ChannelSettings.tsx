import { useEffect, useSyncExternalStore } from 'react';
import { Button, Field, Input } from '../../ui/primitives';

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
  'configure' | 'start' | 'stop' | 'pair' | 'revoke';
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
};

const activityLabels: Record<ChannelStatus['activity'], string> = {
  recent: 'Inbound activity just now',
  within_hour: 'Inbound activity within the last hour',
  within_day: 'Inbound activity within the last day',
  older: 'Earlier inbound activity',
  none: 'No inbound activity recorded this session',
  unknown: 'Activity status unavailable',
};

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

export default function ChannelSettings({
  session,
  load,
  review,
  execute,
}: ChannelSettingsProps) {
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);

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
          session.update({ message: 'Enter a valid number before review.' });
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
      session.update({ reviewed: { command, review: reviewed }, busy: '' });
    } catch {
      if (!abort.signal.aborted)
        session.update({
          busy: '',
          message:
            'The channel action could not be reviewed. Refresh and try again.',
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
              : 'Channel action completed.',
        });
      } else if (receipt.status === 'rejected') {
        session.update({
          pending: null,
          busy: '',
          message: 'The channel action was rejected. Refresh and review again.',
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
  return (
    <section aria-label="Channels" className="settings-section stack">
      <h2>Channels</h2>
      <p>
        Connect messaging accounts and monitor adapter status. Saved values,
        recipient IDs, message content, and delivery targets stay hidden.
      </p>
      <Field label="Search channels">
        <Input
          value={state.query}
          maxLength={128}
          disabled={!state.active || Boolean(state.busy)}
          onChange={(event) => session.update({ query: event.target.value })}
        />
      </Field>
      <div className="actions">
        <Button
          disabled={!state.active || Boolean(state.busy)}
          onClick={() => session.refresh()}
        >
          Refresh channels
        </Button>
      </div>
      {state.page && (
        <p role="status">
          {state.page.total} registered channel
          {state.page.total === 1 ? '' : 's'}
          {state.page.truncated
            ? '; refine the search to see omitted channels'
            : ''}
          .
        </p>
      )}
      {state.page?.items.map((channel) => (
        <section
          className="settings-section stack"
          aria-label={`${channel.display_name} channel`}
          key={channel.channel_id}
        >
          <h3>{channel.display_name}</h3>
          <p>
            {channel.running === true
              ? 'Running'
              : channel.configured === true
                ? 'Stopped'
                : channel.configured === false
                  ? 'Configuration required'
                  : 'Status unavailable'}
            {' · '}
            {activityLabels[channel.activity]}
            {channel.source.kind === 'plugin' ? ' · Plugin channel' : ''}
          </p>
          {channel.capabilities.length > 0 && (
            <p>Capabilities: {channel.capabilities.join(', ')}.</p>
          )}
          <div
            className="actions"
            role="group"
            aria-label={`${channel.display_name} lifecycle`}
          >
            <Button
              disabled={
                locked ||
                channel.availability.lifecycle !== 'available' ||
                channel.running === true
              }
              onClick={() => void requestReview(channel, 'start')}
            >
              Review start {channel.display_name}
            </Button>
            <Button
              disabled={locked || channel.running !== true}
              onClick={() => void requestReview(channel, 'stop')}
            >
              Review stop {channel.display_name}
            </Button>
            <Button
              disabled={locked || channel.availability.pairing !== 'available'}
              onClick={() => void requestReview(channel, 'pair')}
            >
              Review pairing code for {channel.display_name}
            </Button>
          </div>
          {channel.availability.pairing !== 'available' && (
            <p>
              Pairing controls are {channel.availability.pairing}; use the
              account method provided by this adapter.
            </p>
          )}
          {channel.fields.map((field) => {
            const draftKey = `${channel.channel_id}:${field.key}`;
            return (
              <div className="stack" key={field.key}>
                <Field
                  label={`New ${field.label}`}
                  hint={field.help_text || undefined}
                >
                  <Input
                    type={
                      field.field_type === 'password'
                        ? 'password'
                        : field.field_type === 'number' ||
                            field.field_type === 'slider'
                          ? 'number'
                          : 'text'
                    }
                    autoComplete="off"
                    value={state.drafts[draftKey] ?? ''}
                    maxLength={16384}
                    disabled={locked || !field.writable}
                    onChange={(event) =>
                      session.setDraft(draftKey, event.target.value)
                    }
                  />
                </Field>
                <p>
                  {field.configured === true
                    ? `Saved via ${field.source || 'channel storage'}${field.fingerprint ? ` (${field.fingerprint})` : ''}.`
                    : field.configured === false
                      ? 'Not saved.'
                      : 'Saved state unavailable.'}
                  {field.externally_managed
                    ? ' Managed by the server operator.'
                    : ''}
                </p>
                <div className="actions">
                  <Button
                    disabled={
                      locked ||
                      !field.writable ||
                      !(state.drafts[draftKey] ?? '')
                    }
                    onClick={() =>
                      void requestReview(channel, 'configure', field)
                    }
                  >
                    Review save {field.label}
                  </Button>
                  <Button
                    disabled={
                      locked ||
                      !field.writable ||
                      field.configured !== true ||
                      Boolean(state.drafts[draftKey])
                    }
                    onClick={() =>
                      void requestReview(channel, 'configure', field)
                    }
                  >
                    Review clear {field.label}
                  </Button>
                </div>
              </div>
            );
          })}
          {channel.paired_identities.length > 0 && (
            <div className="stack">
              <h4>Paired accounts</h4>
              {channel.paired_identities.map((identity) => (
                <div className="actions" key={identity.identity_id}>
                  <span>
                    {identity.display_name || 'Paired account'} ({identity.hint}
                    )
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
                    Review revoke {identity.display_name || identity.hint}
                  </Button>
                </div>
              ))}
            </div>
          )}
        </section>
      ))}
      {state.reviewed && (
        <section
          role="region"
          aria-label="Review channel action"
          className="settings-section stack"
        >
          <p>
            Confirm {state.reviewed.command.payload.operation} for{' '}
            {state.reviewed.command.payload.channel_id}. Starting or pairing may
            contact the channel provider; configuration changes saved channel
            data.
          </p>
          <div className="actions">
            <Button
              disabled={Boolean(state.busy)}
              onClick={() => void submit(state.reviewed)}
            >
              Confirm channel action
            </Button>
            <Button
              disabled={Boolean(state.busy)}
              onClick={() => session.update({ reviewed: null })}
            >
              Cancel review
            </Button>
          </div>
        </section>
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
