import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useClientState, useRuntime } from '../../runtime';
import { useOverlay } from '../../ui/overlays';
import { Button, Field, Input } from '../../ui/primitives';
import { clientError } from '../../api/errors';

export default function SearchConversations({
  conversationId,
}: {
  conversationId?: string;
}) {
  const { controller } = useRuntime();
  const state = useClientState();
  const overlay = useOverlay();
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [error, setError] = useState('');
  const alive = useRef(true);
  const openSequence = useRef(0);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  async function search(cursor?: string) {
    setError('');
    try {
      await controller.searchLibrary(query, conversationId, cursor);
    } catch (cause) {
      setError(clientError(cause).message);
    }
  }
  return (
    <form
      className="stack"
      onSubmit={(e) => {
        e.preventDefault();
        void search();
      }}
    >
      <Field
        label={
          conversationId
            ? 'Find in history'
            : 'Search conversations and history'
        }
      >
        <Input
          data-initial-focus
          value={query}
          maxLength={200}
          onChange={(e) => setQuery(e.target.value)}
        />
      </Field>
      <Button type="submit" disabled={!query.trim() || state.searching}>
        Search
      </Button>
      <div className="search-results" aria-live="polite">
        {state.search?.items.map((hit) => (
          <Button
            key={`${hit.conversation_id}:${hit.message_id ?? 'title'}`}
            onClick={() => {
              const ticket = ++openSequence.current;
              void controller
                .selectConversation(hit.conversation_id)
                .then(async () => {
                  if (
                    !alive.current ||
                    ticket !== openSequence.current ||
                    controller.getSnapshot().selectedConversationId !==
                      hit.conversation_id
                  )
                    return;
                  if (
                    controller.getSnapshot().conversation?.id !==
                    hit.conversation_id
                  )
                    throw { code: 'not_found' };
                  const selection = controller.getSelectionVersion();
                  if (hit.message_id)
                    await controller.showHistory(hit.message_id);
                  if (
                    !alive.current ||
                    ticket !== openSequence.current ||
                    controller.getSelectionVersion() !== selection
                  )
                    return;
                  navigate(`/conversations/${hit.conversation_id}`);
                  const target = Array.from(
                    document.querySelectorAll<HTMLElement>('[data-message-id]'),
                  ).find(
                    (element) => element.dataset.messageId === hit.message_id,
                  );
                  overlay.close(target);
                })
                .catch((e) => setError(clientError(e).message));
            }}
          >
            <strong>{hit.title}</strong> <span>{hit.excerpt}</span>
          </Button>
        ))}
      </div>
      {state.search?.has_more && (
        <Button
          disabled={state.searching}
          onClick={() => void search(state.search!.next_cursor!)}
        >
          Continue search
        </Button>
      )}
      {state.search && !state.search.items.length && (
        <p>
          {state.search.has_more
            ? 'No matches in this page. Continue searching the remaining history.'
            : 'No matching conversations or messages.'}
        </p>
      )}
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
