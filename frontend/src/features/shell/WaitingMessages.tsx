import { useEffect, useRef, useState } from 'react';
import type {
  ClientQueueItem,
  ClientQueueView,
  ParentSteeringView,
} from '../../api/types';
import { useOverlay } from '../../ui/overlays';
import { Button } from '../../ui/primitives';

/**
 * Messages typed while Row-Bot was answering, until they are sent or
 * discarded (B107, B108, U21). The list reads the server's waiting messages
 * and never counts events, so it cannot show a message that is not there.
 * Follow-ups for running agents are listed too; they cannot be changed.
 */
export type WaitingMessage = {
  id: string;
  text: string;
  revision: string;
  state: ClientQueueItem['state'];
  editable: boolean;
  /** Guidance already handed to running agents: listed, never changed. */
  forAgents?: boolean;
};

const WAITING = new Set(['queued', 'paused', 'dispatching']);

export function waitingFrom(
  queue: ClientQueueView | null | undefined,
  steering: ParentSteeringView | null | undefined,
): WaitingMessage[] {
  const messages: WaitingMessage[] = (queue?.items ?? [])
    .filter((item) => WAITING.has(item.state))
    .map((item) => ({
      id: item.submission_id,
      text: item.text,
      revision: item.revision,
      state: item.state,
      editable: item.editable,
    }));
  const agents: WaitingMessage[] = (steering?.items ?? [])
    .filter((item) => item.state === 'queued')
    .map((item) => ({
      id: `steering:${item.id}`,
      text: item.text,
      revision: '0',
      state: 'queued',
      editable: false,
      forAgents: true,
    }));
  return [...messages, ...agents];
}

/**
 * Reads the waiting messages for a conversation whenever `refreshKey`
 * changes (a queue event, a run starting or ending, a new server). Keeps the
 * last list it read while a read is in flight or fails.
 */
export function useWaitingMessages({
  conversationId,
  refreshKey,
  steeringGeneration,
  readWaiting,
  readSteering,
}: {
  conversationId: string;
  refreshKey: string;
  /** The running generation whose agents take follow-ups, if any. */
  steeringGeneration: string;
  readWaiting: (
    conversation: string,
    signal: AbortSignal,
  ) => Promise<ClientQueueView>;
  readSteering: (
    conversation: string,
    generation: string,
    signal: AbortSignal,
  ) => Promise<ParentSteeringView>;
}): { items: WaitingMessage[]; reload: () => void } {
  const [loaded, setLoaded] = useState<{
    conversation: string;
    items: WaitingMessage[];
  }>({ conversation: conversationId, items: [] });
  const [reloads, setReloads] = useState(0);
  const readers = useRef({ readWaiting, readSteering });
  useEffect(() => {
    readers.current = { readWaiting, readSteering };
  });
  useEffect(() => {
    if (!conversationId) return;
    const abort = new AbortController();
    const timer = setTimeout(() => {
      const { readWaiting: waiting, readSteering: steering } = readers.current;
      void Promise.all([
        Promise.resolve().then(() => waiting(conversationId, abort.signal)),
        steeringGeneration
          ? Promise.resolve()
              .then(() =>
                steering(conversationId, steeringGeneration, abort.signal),
              )
              .catch(() => null)
          : Promise.resolve(null),
      ]).then(
        ([queue, agents]) => {
          if (abort.signal.aborted || queue.conversation_id !== conversationId)
            return;
          setLoaded({
            conversation: conversationId,
            items: waitingFrom(queue, agents),
          });
        },
        () => undefined,
      );
    }, 120);
    return () => {
      clearTimeout(timer);
      abort.abort();
    };
  }, [conversationId, refreshKey, steeringGeneration, reloads]);
  return {
    items: loaded.conversation === conversationId ? loaded.items : [],
    reload: () => setReloads((value) => value + 1),
  };
}

export type WaitingAction = 'dispatch' | 'edit' | 'remove';

/** The first waiting message, when nothing is running, can be sent now. */
export function sendable(items: WaitingMessage[], running: boolean) {
  const first = items.find((item) => !item.forAgents);
  return !running &&
    first &&
    first.editable &&
    (first.state === 'paused' || first.state === 'queued')
    ? first
    : null;
}

export default function WaitingMessages({
  items,
  running,
  busy,
  onAction,
}: {
  items: WaitingMessage[];
  /** A run is active: waiting messages go on their own when it finishes. */
  running: boolean;
  busy: boolean;
  onAction: (
    action: WaitingAction,
    item: WaitingMessage,
    text?: string,
  ) => Promise<void>;
}) {
  const overlay = useOverlay();
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(
    null,
  );
  const [acting, setActing] = useState(false);
  if (!items.length) return null;
  const next = sendable(items, running);
  const disabled = busy || acting;
  async function act(
    action: WaitingAction,
    item: WaitingMessage,
    text?: string,
  ) {
    setActing(true);
    try {
      await onAction(action, item, text);
      if (action === 'edit') setEditing(null);
    } catch {
      // The conversation shows the error and its fix; an edit stays open.
    } finally {
      setActing(false);
    }
  }
  function discard(item: WaitingMessage) {
    overlay.open({
      kind: 'alert',
      title: 'Discard this message?',
      description: 'It was not sent. Row-Bot will not see it.',
      confirmLabel: 'Discard message',
      onConfirm: () => void act('remove', item),
    });
  }
  const count = items.length;
  return (
    <section
      className="waiting-messages"
      aria-label="Waiting messages"
      aria-busy={disabled}
    >
      <p className="waiting-messages-title">
        <strong>
          {count === 1 ? '1 message waiting' : `${count} messages waiting`}
        </strong>
        {running && <span> · sends when Row-Bot finishes</span>}
      </p>
      <ol className="waiting-message-list">
        {items.map((item) => {
          const edit = editing?.id === item.id ? editing : null;
          return (
            <li key={item.id} className="waiting-message">
              {edit ? (
                <div className="waiting-message-edit">
                  <textarea
                    className="input"
                    aria-label="Edit waiting message"
                    maxLength={16000}
                    value={edit.text}
                    disabled={disabled}
                    rows={2}
                    onChange={(event) =>
                      setEditing({ id: item.id, text: event.target.value })
                    }
                  />
                  <div className="waiting-message-actions">
                    <Button
                      variant="primary"
                      disabled={disabled || !edit.text.trim()}
                      onClick={() => void act('edit', item, edit.text)}
                    >
                      Save
                    </Button>
                    <Button
                      variant="ghost"
                      disabled={disabled}
                      onClick={() => setEditing(null)}
                    >
                      Cancel
                    </Button>
                  </div>
                </div>
              ) : (
                <>
                  <p className="waiting-message-text">
                    {item.text || 'This message could not be read.'}
                  </p>
                  {item.forAgents ? (
                    <span className="waiting-message-note">
                      For the running agents
                    </span>
                  ) : item.state === 'dispatching' ? (
                    <span className="waiting-message-note">Sending…</span>
                  ) : !item.editable ? (
                    <span className="waiting-message-note">
                      Continues when you resume
                    </span>
                  ) : (
                    <div className="waiting-message-actions">
                      {next?.id === item.id && (
                        <Button
                          disabled={disabled}
                          onClick={() => void act('dispatch', item)}
                        >
                          Send now
                        </Button>
                      )}
                      <Button
                        variant="ghost"
                        disabled={disabled}
                        onClick={() =>
                          setEditing({ id: item.id, text: item.text })
                        }
                      >
                        Edit
                      </Button>
                      <Button
                        variant="ghost"
                        disabled={disabled}
                        onClick={() => discard(item)}
                      >
                        Discard
                      </Button>
                    </div>
                  )}
                </>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
