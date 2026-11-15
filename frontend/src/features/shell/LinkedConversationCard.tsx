import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { MessageSquareDashed, MessageSquareShare } from 'lucide-react';
import type { ConversationLinkView } from '../../api/types';
import { useClientSelector, useRuntime } from '../../runtime';
import { Button } from '../../ui/primitives';

const STATE_WORDS: Record<ConversationLinkView['state'], string> = {
  proposed: 'proposed',
  working: 'working',
  waiting: 'waiting for you',
  done: 'done',
  dismissed: 'not started',
  expired: 'proposal expired',
};

/**
 * A conversation this one started or proposed (5.3.1 C), in the chat:
 * "Make the demo video · working" with Open, or a proposal with Start and
 * Not now. Its state is read again by link as the sidebar's list changes.
 */
export function LinkedConversationCard({
  linkId,
  title,
  conversationId,
}: {
  linkId: string;
  title: string;
  conversationId: string | null | undefined;
}) {
  const { controller } = useRuntime();
  const navigate = useNavigate();
  const [link, setLink] = useState<ConversationLinkView | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  // The list moves when the linked conversation starts, works or finishes.
  const conversations = useClientSelector((value) => value.conversations);

  useEffect(() => {
    if (!conversationId) return;
    const abort = new AbortController();
    controller.conversationLinks(conversationId, abort.signal).then(
      (page) => {
        if (abort.signal.aborted || !page) return;
        setLink(page.items.find((item) => item.link_id === linkId) ?? null);
      },
      () => undefined,
    );
    return () => abort.abort();
  }, [controller, conversationId, linkId, conversations]);

  async function act(
    kind: 'conversation.link.start' | 'conversation.link.dismiss',
  ) {
    if (!conversationId) return;
    setBusy(true);
    setError('');
    try {
      const receipt = await controller.intent(
        conversationId,
        kind,
        { link_id: linkId },
        controller.getSnapshot().conversation?.revision ?? '0',
      );
      const starting = kind === 'conversation.link.start';
      const started = receipt.linked_conversation_id;
      setLink((current) =>
        current
          ? {
              ...current,
              state: starting ? 'working' : 'dismissed',
              proposed: !starting,
              conversation_id: starting
                ? (started ?? current.conversation_id)
                : current.conversation_id,
            }
          : current,
      );
    } catch {
      setError(
        kind === 'conversation.link.start'
          ? "It couldn't start. Try again."
          : "That didn't go through. Try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  const state = link?.state ?? 'working';
  const proposed = state === 'proposed';
  const target = link?.conversation_id;
  const name = link?.title || title;
  return (
    <div
      className="transcript-card linked-conversation-card"
      data-kind="linked_conversation"
      data-state={state}
      role="group"
      aria-label={`${name} · ${STATE_WORDS[state]}`}
      aria-busy={!link}
    >
      {proposed || state === 'dismissed' || state === 'expired' ? (
        <MessageSquareDashed className="transcript-card-icon" aria-hidden />
      ) : (
        <MessageSquareShare className="transcript-card-icon" aria-hidden />
      )}
      <div className="transcript-card-text">
        <span>
          <strong>{name}</strong> ·{' '}
          {proposed ? 'proposed conversation' : STATE_WORDS[state]}
        </span>
        {error && <small role="alert">{error}</small>}
      </div>
      <div className="transcript-card-actions">
        {proposed ? (
          <>
            <Button
              variant="ghost"
              disabled={busy}
              onClick={() => void act('conversation.link.dismiss')}
            >
              Not now
            </Button>
            <Button
              variant="primary"
              disabled={busy}
              onClick={() => void act('conversation.link.start')}
            >
              Start
            </Button>
          </>
        ) : target ? (
          <Button
            onClick={() =>
              navigate(`/conversations/${encodeURIComponent(target)}`)
            }
          >
            Open
          </Button>
        ) : null}
      </div>
    </div>
  );
}
