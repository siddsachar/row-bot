import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { Hand, Monitor, Play, Square } from 'lucide-react';
import type {
  ComputerUseCommand,
  ComputerUsePreview,
  ComputerUseReceipt,
  ComputerUseSnapshot,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { Button, StatusDot } from '../../ui/primitives';

/**
 * Computer use in the conversation (NiceGUI parity row 37): the latest
 * picture, Stop, and Pause/Resume, where Pause hands the computer to you.
 * The picture lives only in memory: it is fetched when it changes, never
 * stored, and hidden while you have control or an approval is waiting.
 */
export type ComputerUseAction = ComputerUseCommand['type'];

/** How often the card checks the session while there is one to follow. */
export const COMPUTER_POLL_MS = 1000;

type Followed = {
  conversation: string;
  snapshot: ComputerUseSnapshot | null;
  checked: boolean;
  stopped: boolean;
};

const nothing = (conversation: string): Followed => ({
  conversation,
  snapshot: null,
  checked: false,
  stopped: false,
});

function pageVisible() {
  return (
    typeof document === 'undefined' || document.visibilityState !== 'hidden'
  );
}

/**
 * Follows the open conversation's computer-use session. It reads once when
 * the conversation opens and when its turn changes, then about once a second
 * while there is something to follow (`watch`: the turn is using the
 * computer or asks to) and the page is visible. An empty `conversationId`
 * turns it off (another device, or a past conversation page).
 */
export function useComputerUse({
  conversationId,
  watch,
  generationId,
  turnKey,
  load,
}: {
  conversationId: string;
  watch: boolean;
  generationId: string;
  turnKey: string;
  load: (
    conversation: string,
    signal: AbortSignal,
  ) => Promise<ComputerUseSnapshot>;
}) {
  const [followed, setFollowed] = useState<Followed>(() => nothing(''));
  const [visible, setVisible] = useState(pageVisible);
  const current =
    followed.conversation === conversationId
      ? followed
      : nothing(conversationId);

  const read = useCallback(
    async (signal: AbortSignal) => {
      try {
        const value = await load(conversationId, signal);
        if (signal.aborted || value.conversation_id !== conversationId) return;
        setFollowed((prior) => {
          const same = prior.conversation === conversationId;
          if (
            same &&
            prior.checked &&
            prior.snapshot?.revision === value.revision
          )
            return prior;
          return {
            conversation: conversationId,
            snapshot: value,
            checked: true,
            // A session that starts again replaces the "Stopped" note.
            stopped: same && prior.stopped && !value.active,
          };
        });
      } catch {
        if (signal.aborted) return;
        setFollowed((prior) =>
          prior.conversation === conversationId
            ? prior.checked
              ? prior
              : { ...prior, checked: true }
            : { ...nothing(conversationId), checked: true },
        );
      }
    },
    [conversationId, load],
  );

  useEffect(() => {
    const update = () => setVisible(pageVisible());
    document.addEventListener('visibilitychange', update);
    return () => document.removeEventListener('visibilitychange', update);
  }, []);

  useEffect(() => {
    if (!conversationId) return;
    const abort = new AbortController();
    void read(abort.signal);
    return () => abort.abort();
  }, [conversationId, read, turnKey]);

  const following =
    Boolean(conversationId) &&
    (watch ||
      Boolean(current.snapshot?.active) ||
      Boolean(current.snapshot?.approval_id));
  useEffect(() => {
    if (!following || !visible) return;
    const abort = new AbortController();
    let timer = 0;
    const next = () => {
      timer = window.setTimeout(() => {
        void read(abort.signal).then(() => {
          if (!abort.signal.aborted) next();
        });
      }, COMPUTER_POLL_MS);
    };
    next();
    return () => {
      abort.abort();
      window.clearTimeout(timer);
    };
  }, [following, visible, read]);

  // A new turn clears the "Stopped" note left by the last one.
  useEffect(() => {
    setFollowed((prior) =>
      prior.stopped ? { ...prior, stopped: false } : prior,
    );
  }, [generationId]);

  const apply = useCallback(
    (value: ComputerUseSnapshot) =>
      setFollowed((prior) =>
        value.conversation_id !== conversationId
          ? prior
          : {
              conversation: conversationId,
              snapshot: value,
              checked: true,
              stopped: prior.conversation === conversationId && prior.stopped,
            },
      ),
    [conversationId],
  );
  const markStopped = useCallback(
    () =>
      setFollowed((prior) =>
        prior.conversation === conversationId
          ? { ...prior, stopped: true }
          : { ...nothing(conversationId), checked: true, stopped: true },
      ),
    [conversationId],
  );
  return {
    snapshot: current.snapshot,
    /** False only until the first read of a followed conversation ends. */
    checked: !conversationId || current.checked,
    stopped: current.stopped,
    visible:
      Boolean(conversationId) &&
      (Boolean(current.snapshot?.active) ||
        Boolean(current.snapshot?.approval_id) ||
        current.stopped),
    apply,
    markStopped,
  };
}

const STATUS: Record<
  ComputerUseSnapshot['state'],
  {
    tone: 'accent' | 'warning' | 'info' | 'danger' | 'neutral';
    label: string;
  }
> = {
  working: { tone: 'accent', label: 'Working' },
  paused: { tone: 'warning', label: 'Paused' },
  waiting_approval: { tone: 'info', label: 'Waiting for approval' },
  needs_attention: { tone: 'danger', label: 'Needs attention' },
  stopped: { tone: 'neutral', label: 'Stopped' },
};

function note(snapshot: ComputerUseSnapshot | null, stopped: boolean) {
  if (!snapshot || (!snapshot.active && !snapshot.approval_id))
    return stopped ? 'Stopped. Row-Bot no longer controls your computer.' : '';
  switch (snapshot.state) {
    case 'paused':
      return 'Paused. You have control; the picture is hidden until you resume.';
    case 'waiting_approval':
      return 'The picture is hidden while Row-Bot waits for your approval.';
    case 'needs_attention':
      return 'Something went wrong while using your computer. Stop it, then ask again.';
    case 'stopped':
      return 'This computer session ended while paused. Stop to finish up.';
    default:
      return '';
  }
}

export function ComputerUseCard({
  conversationId,
  snapshot,
  stopped,
  loadPreview,
  send,
  onChange,
  onStopped,
}: {
  conversationId: string;
  snapshot: ComputerUseSnapshot | null;
  stopped: boolean;
  loadPreview: (
    conversation: string,
    revision: string,
    signal: AbortSignal,
  ) => Promise<ComputerUsePreview>;
  send: (
    conversation: string,
    action: ComputerUseAction,
  ) => Promise<ComputerUseReceipt>;
  onChange: (snapshot: ComputerUseSnapshot) => void;
  onStopped: () => void;
}) {
  const [picture, setPicture] = useState<{
    revision: string;
    src: string;
  } | null>(null);
  const [busy, setBusy] = useState<ComputerUseAction | null>(null);
  const [error, setError] = useState('');
  const cardRef = useRef<HTMLElement>(null);
  const keepFocus = useRef(false);
  const working = snapshot?.state === 'working' && snapshot.active;
  const revision = working && snapshot.has_picture ? snapshot.revision : '';

  useEffect(() => {
    // The picture is ephemeral: drop it as soon as it may not be shown.
    if (!working) setPicture(null);
  }, [working]);
  useEffect(() => {
    if (!revision) return;
    const abort = new AbortController();
    loadPreview(conversationId, revision, abort.signal)
      .then((value) => {
        if (
          abort.signal.aborted ||
          value.conversation_id !== conversationId ||
          value.revision !== revision
        )
          return;
        if (
          value.state === 'available' &&
          value.image_base64 &&
          value.mime_type
        )
          setPicture({
            revision,
            src: `data:${value.mime_type};base64,${value.image_base64}`,
          });
        else if (value.state !== 'waiting') setPicture(null);
      })
      // A changed picture or a lost request: the next check tries again.
      .catch(() => undefined);
    return () => abort.abort();
  }, [conversationId, loadPreview, revision]);

  // A finished action can remove the button it came from (Pause becomes
  // Resume): keep focus in the card instead of dropping it to the page.
  useLayoutEffect(() => {
    const card = cardRef.current;
    if (!keepFocus.current || busy || !card) return;
    keepFocus.current = false;
    if (!card.contains(document.activeElement))
      (
        card.querySelector<HTMLElement>('button:not(:disabled)') ?? card
      ).focus();
  });

  async function run(action: ComputerUseAction) {
    keepFocus.current = Boolean(
      cardRef.current?.contains(document.activeElement),
    );
    setBusy(action);
    setError('');
    try {
      const receipt = await send(conversationId, action);
      if (receipt.computer_use) onChange(receipt.computer_use);
      if (receipt.status === 'completed') {
        if (action === 'computer_use.stop') onStopped();
      } else
        setError(
          clientError({
            code: receipt.code ?? 'computer_use_outcome_uncertain',
          }).message,
        );
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy((current) => (current === action ? null : current));
    }
  }

  const ended = !snapshot || (!snapshot.active && !snapshot.approval_id);
  const state = ended ? 'stopped' : snapshot.state;
  const status = STATUS[state];
  const app = !ended ? snapshot.app : '';
  const text = note(snapshot, stopped);
  return (
    <section
      ref={cardRef}
      tabIndex={-1}
      className="computer-card"
      data-state={state}
      aria-label="Computer use"
      aria-busy={busy ? true : undefined}
    >
      <div className="computer-card-header">
        <Monitor className="computer-card-icon" aria-hidden />
        <span className="computer-card-title">
          {ended ? 'Computer use' : 'Using your computer'}
          {app && <span className="computer-card-app"> · {app}</span>}
        </span>
        <StatusDot
          tone={status.tone}
          label={status.label}
          showLabel
          pulse={state === 'working'}
        />
      </div>
      {working && picture && (
        <img
          className="computer-card-picture"
          src={picture.src}
          alt={`Latest picture of ${app || 'the app Row-Bot is using'}`}
        />
      )}
      {text && (
        <p className="computer-card-note" role="status">
          {text}
        </p>
      )}
      {!ended &&
        (snapshot.can_pause || snapshot.can_resume || snapshot.can_stop) && (
          <div className="computer-card-actions">
            {snapshot.can_pause && (
              <Button
                aria-label="Pause — you take over"
                title="Row-Bot stops and waits while you use the computer"
                disabled={busy !== null}
                onClick={() => void run('computer_use.pause')}
              >
                <Hand aria-hidden />
                Pause
              </Button>
            )}
            {snapshot.can_resume && (
              <Button
                variant="primary"
                disabled={busy !== null}
                onClick={() => void run('computer_use.resume')}
              >
                <Play aria-hidden />
                {busy === 'computer_use.resume' ? 'Resuming…' : 'Resume'}
              </Button>
            )}
            {snapshot.can_stop && (
              <Button
                variant="danger"
                disabled={busy === 'computer_use.stop'}
                onClick={() => void run('computer_use.stop')}
              >
                <Square aria-hidden />
                Stop
              </Button>
            )}
          </div>
        )}
      {error && (
        <p className="computer-card-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
