import { useEffect, useState, useSyncExternalStore } from 'react';
import {
  ArrowLeft,
  Eye,
  EyeOff,
  Hand,
  RotateCcw,
  Square,
  ScanEye,
} from 'lucide-react';
import type { BrowserPreview } from '../../api/types';
import {
  Button,
  CompactAction,
  Field,
  Input,
  Surface,
} from '../../ui/primitives';

export type BrowserAction =
  | 'browser.navigate'
  | 'browser.take_over'
  | 'browser.check'
  | 'browser.back'
  | 'browser.end';

export type BrowserAvailability = {
  state: 'available' | 'check_on_use' | 'unavailable';
  code: string | null;
};

export type BrowserControlState = {
  schema_version: 1;
  conversation_id: string;
  revision: string;
  active: boolean;
  paused: boolean;
  state: string;
  site: string;
  url: string;
  last_action: string;
  availability: Record<string, BrowserAvailability>;
};

export type BrowserCommandPayload = {
  revision: string;
  url?: string;
  nonce?: string;
};

export type BrowserCommand = {
  command_id: string;
  type: BrowserAction;
  payload: BrowserCommandPayload & { nonce: string };
};

export type BrowserReview = {
  schema_version: 1;
  action: BrowserAction;
  conversation_id: string;
  revision: string;
  policy_action: string;
  policy_decision: 'allow' | 'ask';
  policy_reason: string;
  approval_required: true;
  origin_and_path: string;
  query_present: boolean;
  disclosures: string[];
  action_digest: string;
  nonce: string;
};

export type BrowserReceipt = {
  schema_version: 1;
  command_id: string;
  action: BrowserAction;
  conversation_id: string;
  status: 'completed' | 'partial' | 'rejected' | string;
  code: string | null;
  revision: string | null;
  browser_control: BrowserControlState | null;
};

type RetainedAction = {
  phase: 'reviewed' | 'pending';
  command: BrowserCommand;
  review: BrowserReview;
};

type SessionState = {
  conversationId: string;
  snapshot: BrowserControlState | null;
  retained: RetainedAction | null;
  active: boolean;
  busy: boolean;
  refresh: number;
  message: string;
  readMessage: string;
};

/** Authenticated-controller memory only; never persisted in browser storage. */
export function createBrowserControlSession(conversationId: string) {
  let state: SessionState = {
    conversationId,
    snapshot: null,
    retained: null,
    active: true,
    busy: false,
    refresh: 0,
    message: '',
    readMessage: '',
  };
  const listeners = new Set<() => void>();
  const aborters = new Set<AbortController>();
  const update = (patch: Partial<SessionState>) => {
    if (!state.active) return;
    state = { ...state, ...patch };
    listeners.forEach((notify) => notify());
  };
  return {
    getSnapshot: () => state,
    subscribe: (notify: () => void) => {
      listeners.add(notify);
      return () => listeners.delete(notify);
    },
    update,
    refresh: () => update({ refresh: state.refresh + 1 }),
    beginRead: () => {
      const abort = new AbortController();
      if (!state.active) abort.abort();
      else aborters.add(abort);
      return abort;
    },
    endRead: (abort: AbortController) => aborters.delete(abort),
    hasRetained: () => state.active && state.retained !== null,
    dispose: () => {
      aborters.forEach((abort) => abort.abort());
      aborters.clear();
      state = {
        conversationId,
        snapshot: null,
        retained: null,
        active: false,
        busy: false,
        refresh: 0,
        message: '',
        readMessage: 'Sign in again to manage the browser.',
      };
      listeners.forEach((notify) => notify());
    },
  };
}

export type BrowserControlSession = ReturnType<
  typeof createBrowserControlSession
>;

export type BrowserLiveControlsProps = {
  session: BrowserControlSession;
  load: (
    conversationId: string,
    signal: AbortSignal,
  ) => Promise<BrowserControlState>;
  loadPreview: (
    conversationId: string,
    revision: string,
    signal: AbortSignal,
  ) => Promise<BrowserPreview>;
  review: (
    action: BrowserAction,
    payload: BrowserCommandPayload,
    signal: AbortSignal,
  ) => Promise<BrowserReview>;
  execute: (
    command: BrowserCommand,
    review: BrowserReview,
  ) => Promise<BrowserReceipt>;
};

const actionLabels: Record<BrowserAction, string> = {
  'browser.navigate': 'Open address',
  'browser.take_over': 'Take over browser',
  'browser.check': 'Check current page',
  'browser.back': 'Go back',
  'browser.end': 'End browser activity',
};

const stateLabels: Record<string, string> = {
  idle: 'No managed-browser activity for this conversation.',
  acting: 'The managed browser is carrying out an action.',
  observing: 'The managed browser is ready.',
  waiting_user: 'You have control of the managed browser.',
  waiting_approval: 'The managed browser is waiting for an approval.',
  needs_attention: 'The managed browser needs attention.',
};

const unavailableLabels: Record<string, string> = {
  'browser.click': 'Page clicks need an exact page-target contract.',
  'browser.type': 'Text entry needs exact target and hidden-text handling.',
  'browser.scroll': 'Scrolling needs a client-safe page observation contract.',
  'browser.tab': 'Tab controls need stable owned-tab identities.',
  'browser.screenshot': 'Private browser previews cannot be exported here.',
  'browser.external.attach':
    'Use Computer Use for an existing external browser.',
};

function validSnapshot(
  value: BrowserControlState,
  conversationId: string,
): boolean {
  return (
    Boolean(value) &&
    value.schema_version === 1 &&
    value.conversation_id === conversationId &&
    /^[0-9a-f]{64}$/.test(value.revision) &&
    typeof value.active === 'boolean' &&
    typeof value.paused === 'boolean' &&
    typeof value.state === 'string' &&
    value.state.length <= 64 &&
    typeof value.site === 'string' &&
    value.site.length <= 120 &&
    typeof value.url === 'string' &&
    value.url.length <= 2048 &&
    typeof value.last_action === 'string' &&
    value.last_action.length <= 160 &&
    Boolean(value.availability) &&
    typeof value.availability === 'object' &&
    !Array.isArray(value.availability)
  );
}

function available(
  snapshot: BrowserControlState | null,
  action: BrowserAction,
): boolean {
  return Boolean(
    snapshot && snapshot.availability[action]?.state !== 'unavailable',
  );
}

function validReview(
  value: BrowserReview,
  action: BrowserAction,
  payload: BrowserCommandPayload,
  conversationId: string,
): boolean {
  return (
    value.schema_version === 1 &&
    value.action === action &&
    value.conversation_id === conversationId &&
    value.revision === payload.revision &&
    value.approval_required === true &&
    (value.policy_decision === 'allow' || value.policy_decision === 'ask') &&
    typeof value.policy_action === 'string' &&
    value.policy_action.length <= 64 &&
    typeof value.policy_reason === 'string' &&
    value.policy_reason.length <= 512 &&
    typeof value.origin_and_path === 'string' &&
    value.origin_and_path.length <= 2048 &&
    typeof value.query_present === 'boolean' &&
    /^[0-9a-f]{64}$/.test(value.action_digest) &&
    typeof value.nonce === 'string' &&
    value.nonce.length > 0 &&
    value.nonce.length <= 512 &&
    Array.isArray(value.disclosures) &&
    value.disclosures.length > 0 &&
    value.disclosures.length <= 8 &&
    value.disclosures.every(
      (item) => typeof item === 'string' && item.length <= 1024,
    )
  );
}

export default function BrowserLiveControls({
  session,
  load,
  loadPreview,
  review,
  execute,
}: BrowserLiveControlsProps) {
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const [url, setUrl] = useState('');
  const [pictureHidden, setPictureHidden] = useState(false);
  const [picture, setPicture] = useState<BrowserPreview | null>(null);
  const [pictureError, setPictureError] = useState('');
  const { conversationId, snapshot, retained } = state;

  useEffect(() => {
    if (!session.getSnapshot().active) return;
    const abort = session.beginRead();
    void load(conversationId, abort.signal)
      .then((value) => {
        if (abort.signal.aborted) return;
        if (!validSnapshot(value, conversationId)) throw Error();
        session.update({ snapshot: value, readMessage: '' });
      })
      .catch(() => {
        if (!abort.signal.aborted)
          session.update({
            readMessage: 'Browser status is unavailable. Refresh to try again.',
          });
      })
      .finally(() => session.endRead(abort));
    return () => abort.abort();
  }, [conversationId, load, session, state.active, state.refresh]);

  useEffect(() => {
    setPicture(null);
    setPictureError('');
    if (!snapshot?.active || pictureHidden || !state.active) return;
    const abort = session.beginRead();
    void loadPreview(conversationId, snapshot.revision, abort.signal)
      .then((value) => {
        if (abort.signal.aborted) return;
        if (
          value.conversation_id !== conversationId ||
          value.revision !== snapshot.revision
        )
          throw new Error('Picture changed');
        setPicture(value);
      })
      .catch(() => {
        if (!abort.signal.aborted)
          setPictureError(
            'Picture unavailable. Refresh browser status to retry.',
          );
      })
      .finally(() => session.endRead(abort));
    return () => abort.abort();
  }, [
    conversationId,
    loadPreview,
    pictureHidden,
    session,
    snapshot?.active,
    snapshot?.revision,
    state.active,
  ]);

  const requestReview = async (action: BrowserAction) => {
    const current = session.getSnapshot();
    const saved = current.snapshot;
    if (
      !current.active ||
      current.busy ||
      current.retained ||
      !saved ||
      !available(saved, action)
    )
      return;
    let selectedUrl: string | undefined;
    if (action === 'browser.navigate') {
      try {
        const parsed = new URL(url);
        if (!['http:', 'https:'].includes(parsed.protocol)) throw Error();
        selectedUrl = url.trim();
      } catch {
        session.update({
          message: 'Enter a complete http:// or https:// address.',
        });
        return;
      }
    }
    const payload: BrowserCommandPayload = {
      revision: saved.revision,
      ...(selectedUrl ? { url: selectedUrl } : {}),
    };
    const commandId = crypto.randomUUID();
    const abort = session.beginRead();
    session.update({ busy: true, message: '' });
    try {
      const reviewed = await review(action, payload, abort.signal);
      if (abort.signal.aborted) return;
      if (!validReview(reviewed, action, payload, conversationId))
        throw Error();
      const command: BrowserCommand = {
        command_id: commandId,
        type: action,
        payload: { ...payload, nonce: reviewed.nonce },
      };
      const attempt: RetainedAction = {
        phase: 'reviewed',
        command,
        review: reviewed,
      };
      session.update({ retained: attempt, busy: false, message: '' });
      await submit(attempt);
    } catch {
      if (!abort.signal.aborted)
        session.update({
          busy: false,
          message:
            'The browser action could not be reviewed. Refresh and try again.',
        });
    } finally {
      session.endRead(abort);
    }
  };

  const submit = async (attempt: RetainedAction | null) => {
    const current = session.getSnapshot();
    if (
      !attempt ||
      !current.active ||
      current.busy ||
      current.retained !== attempt
    )
      return;
    const pending = { ...attempt, phase: 'pending' as const };
    session.update({ retained: pending, busy: true, message: '' });
    try {
      const receipt = await execute(pending.command, pending.review);
      if (!session.getSnapshot().active) return;
      if (
        receipt.command_id !== pending.command.command_id ||
        receipt.action !== pending.command.type ||
        receipt.conversation_id !== conversationId
      )
        throw Error();
      if (receipt.status === 'rejected') {
        session.update({
          retained: null,
          busy: false,
          message: 'The browser action was rejected. Refresh and review again.',
        });
      } else if (
        receipt.status === 'completed' &&
        receipt.browser_control &&
        validSnapshot(receipt.browser_control, conversationId)
      ) {
        session.update({
          snapshot: receipt.browser_control,
          retained: null,
          busy: false,
          message: `${actionLabels[pending.command.type]} completed.`,
        });
      } else {
        session.update({
          retained: pending,
          busy: false,
          message:
            'The outcome is not confirmed. Check this original browser action; no replacement will be sent.',
        });
      }
    } catch {
      if (session.getSnapshot().active)
        session.update({
          retained: pending,
          busy: false,
          message:
            'The outcome is uncertain. Check this original browser action before starting another.',
        });
    } finally {
      session.refresh();
    }
  };

  const locked = !state.active || state.busy || retained !== null;
  const unavailable = snapshot
    ? Object.entries(unavailableLabels).filter(
        ([action]) => snapshot.availability[action]?.state === 'unavailable',
      )
    : Object.entries(unavailableLabels);

  return (
    <section
      aria-label="Managed browser"
      className="settings-section capability-page"
    >
      <header className="capability-header">
        <div>
          <p className="eyebrow">Local managed profile</p>
          <h3>Managed browser</h3>
          <p>
            Open ordinary websites in Row-Bot&apos;s local managed profile and
            take over its window when you need direct control.
          </p>
        </div>
      </header>
      <p role="status">
        {snapshot
          ? (stateLabels[snapshot.state] ?? 'Browser state is unknown.')
          : 'Browser status has not been read.'}
      </p>
      {snapshot?.site && (
        <p>
          <strong>Site:</strong> {snapshot.site}
          {snapshot.url && (
            <>
              {' · '}
              <span>{snapshot.url}</span>
            </>
          )}
        </p>
      )}
      {snapshot?.last_action && (
        <p>
          <strong>Last action:</strong> {snapshot.last_action}
        </p>
      )}
      <CompactAction
        label="Refresh browser status"
        disabled={!state.active || state.busy}
        onClick={session.refresh}
      >
        <RotateCcw aria-hidden="true" />
      </CompactAction>

      {snapshot?.active && (
        <section
          className="capability-section stack"
          aria-label="Browser picture"
        >
          <div className="section-heading">
            <h4>Live picture</h4>
            <CompactAction
              label={
                pictureHidden ? 'Show browser picture' : 'Hide browser picture'
              }
              onClick={() => {
                setPictureHidden((value) => !value);
                setPicture(null);
              }}
            >
              {pictureHidden ? (
                <Eye size={17} aria-hidden />
              ) : (
                <EyeOff size={17} aria-hidden />
              )}
            </CompactAction>
          </div>
          {!pictureHidden &&
            (snapshot.state === 'waiting_user' ||
              snapshot.state === 'waiting_approval' ||
              picture?.state === 'shielded') && (
              <p role="status">
                Picture hidden while you control the target or while a protected
                surface is visible.
              </p>
            )}
          {!pictureHidden &&
            snapshot.state !== 'waiting_user' &&
            snapshot.state !== 'waiting_approval' &&
            picture?.state === 'available' &&
            picture.image_base64 && (
              <img
                className="browser-live-picture"
                src={`data:image/png;base64,${picture.image_base64}`}
                alt="Ephemeral managed browser picture"
              />
            )}
          {!pictureHidden &&
            snapshot.state !== 'waiting_user' &&
            snapshot.state !== 'waiting_approval' &&
            picture?.state === 'waiting' && (
              <p>Waiting for the first safe target picture…</p>
            )}
          {!pictureHidden && pictureError && (
            <p role="status">{pictureError}</p>
          )}
        </section>
      )}

      <div role="group" aria-label="Browser navigation">
        <Field
          label="Address"
          hint="Enter a complete http:// or https:// address. Query values are omitted from status and history."
        >
          <Input
            aria-label="Address"
            type="url"
            value={url}
            placeholder="https://example.com"
            disabled={locked}
            onChange={(event) => setUrl(event.target.value)}
          />
        </Field>
        <Button
          disabled={
            locked || !available(snapshot, 'browser.navigate') || !url.trim()
          }
          onClick={() => void requestReview('browser.navigate')}
        >
          Open address
        </Button>
      </div>

      <div
        className="action-cluster capability-section"
        role="group"
        aria-label="Browser live control"
      >
        <CompactAction
          label="Take over browser"
          disabled={locked || !available(snapshot, 'browser.take_over')}
          onClick={() => void requestReview('browser.take_over')}
        >
          <Hand aria-hidden="true" />
        </CompactAction>
        <CompactAction
          label="Check current page"
          disabled={locked || !available(snapshot, 'browser.check')}
          onClick={() => void requestReview('browser.check')}
        >
          <ScanEye aria-hidden="true" />
        </CompactAction>
        <CompactAction
          label="Go back"
          disabled={locked || !available(snapshot, 'browser.back')}
          onClick={() => void requestReview('browser.back')}
        >
          <ArrowLeft aria-hidden="true" />
        </CompactAction>
        <CompactAction
          label="End browser activity"
          variant="danger"
          disabled={locked || !available(snapshot, 'browser.end')}
          onClick={() => void requestReview('browser.end')}
        >
          <Square aria-hidden="true" />
        </CompactAction>
      </div>

      {retained?.phase === 'pending' && (
        <Surface elevated>
          <h4>Original browser action</h4>
          <p>
            Keep this exact command while its outcome is checked. Starting a
            replacement could repeat the action.
          </p>
          <Button disabled={state.busy} onClick={() => void submit(retained)}>
            Check original browser action
          </Button>
        </Surface>
      )}

      {state.message && <p role="status">{state.message}</p>}
      {state.readMessage && <p role="status">{state.readMessage}</p>}

      <details>
        <summary>Unavailable browser controls</summary>
        <ul>
          {unavailable.map(([action, label]) => (
            <li key={action}>{label}</li>
          ))}
        </ul>
      </details>
    </section>
  );
}
