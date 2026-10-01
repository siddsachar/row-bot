import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useClientState, useRuntime } from '../../runtime';
import { useOverlay } from '../../ui/overlays';
import { Button, Field, Input } from '../../ui/primitives';
import { clientError } from '../../api/errors';

type Hit = NonNullable<
  ReturnType<typeof useClientState>['search']
>['items'][number];

/**
 * The hits of the current search, grouped by conversation (its title once,
 * then each matching message). A hit opens its conversation at the message.
 * Shared by Find in conversation and the Library's search (B270).
 */
export function SearchResults({
  onContinue,
}: {
  onContinue: (cursor: string) => void;
}) {
  const { controller } = useRuntime();
  const state = useClientState();
  const overlay = useOverlay();
  const navigate = useNavigate();
  const [error, setError] = useState('');
  const alive = useRef(true);
  const openSequence = useRef(0);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const groups: { id: string; title: string; hits: Hit[] }[] = [];
  for (const hit of state.search?.items ?? []) {
    const last = groups.at(-1);
    if (last?.id === hit.conversation_id) last.hits.push(hit);
    else
      groups.push({ id: hit.conversation_id, title: hit.title, hits: [hit] });
  }
  function open(hit: Hit) {
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
        if (controller.getSnapshot().conversation?.id !== hit.conversation_id)
          throw { code: 'not_found' };
        const selection = controller.getSelectionVersion();
        if (hit.message_id) await controller.showHistory(hit.message_id);
        if (
          !alive.current ||
          ticket !== openSequence.current ||
          controller.getSelectionVersion() !== selection
        )
          return;
        navigate(`/conversations/${hit.conversation_id}`);
        const target = Array.from(
          document.querySelectorAll<HTMLElement>('[data-message-id]'),
        ).find((element) => element.dataset.messageId === hit.message_id);
        overlay.close(target);
      })
      .catch((e) => setError(clientError(e).message));
  }
  return (
    <>
      <div className="search-results" aria-live="polite">
        {groups.length > 0 && (
          <ol aria-label="Conversation search results">
            {groups.map((group, index) => (
              <li key={`${group.id}:${index}`} className="search-result-group">
                <span className="search-result-title" aria-hidden>
                  {group.title}
                </span>
                <ul>
                  {group.hits.map((hit) => (
                    <li key={hit.message_id ?? 'title'}>
                      <Button
                        variant="ghost"
                        aria-label={`${hit.title} ${hit.excerpt}`}
                        onClick={() => open(hit)}
                      >
                        <span className="search-result-copy">
                          {hit.excerpt}
                        </span>
                      </Button>
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ol>
        )}
      </div>
      {state.search?.has_more && (
        <Button
          disabled={state.searching}
          onClick={() => onContinue(state.search!.next_cursor!)}
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
    </>
  );
}

export default function SearchConversations({
  conversationId,
}: {
  conversationId?: string;
}) {
  const { controller } = useRuntime();
  const state = useClientState();
  const [query, setQuery] = useState('');
  const [error, setError] = useState('');
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
      className="stack conversation-search"
      aria-label={
        conversationId
          ? 'Find in conversation history'
          : 'Search conversations and history'
      }
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
      <SearchResults onContinue={(cursor) => void search(cursor)} />
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
