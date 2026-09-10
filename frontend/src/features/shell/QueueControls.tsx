import { useEffect, useRef, useState } from 'react';
import type { ClientQueueItem, ClientQueueView } from '../../api/types';
import { useOverlay } from '../../ui/overlays';
import {
  Button,
  EmptyState,
  ErrorState,
  Field,
  Skeleton,
} from '../../ui/primitives';

export type QueueControlsProps = {
  conversationId: string;
  generationId: string;
  refreshKey: string;
  loadPage: (
    generationId: string,
    cursor: string | null,
    signal: AbortSignal,
  ) => Promise<ClientQueueView>;
  onAction: (
    type: 'edit' | 'remove' | 'dispatch',
    id: string,
    revision: string,
    text?: string,
  ) => Promise<void>;
};

export default function QueueControls({
  conversationId,
  generationId,
  refreshKey,
  loadPage,
  onAction,
}: QueueControlsProps) {
  const owner = JSON.stringify([conversationId, generationId]);
  const overlay = useOverlay();
  const [selection, setSelection] = useState<{
    owner: string;
    cursor: string | null;
  }>({ owner, cursor: null });
  const cursor = selection.owner === owner ? selection.cursor : null;
  const [loaded, setLoaded] = useState<{
    owner: string;
    cursor: string | null;
    page: ClientQueueView;
  } | null>(null);
  const [editing, setEditing] = useState<{
    owner: string;
    item: ClientQueueItem;
    text: string;
  } | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [reload, setReload] = useState(0);
  const loader = useRef(loadPage);
  const currentOwner = useRef(owner);
  const mounted = useRef(false);
  const lifetime = useRef(0);
  const sequence = useRef(0);
  const actionInFlight = useRef(false);
  useEffect(() => {
    loader.current = loadPage;
  }, [loadPage]);
  useEffect(() => {
    mounted.current = true;
    lifetime.current += 1;
    currentOwner.current = owner;
    actionInFlight.current = false;
    setBusy(false);
    setEditing(null);
    return () => {
      mounted.current = false;
    };
  }, [owner]);
  useEffect(() => {
    const epoch = ++sequence.current;
    const abort = new AbortController();
    setLoading(true);
    setError('');
    loader.current(generationId, cursor, abort.signal).then(
      (page) => {
        if (abort.signal.aborted || epoch !== sequence.current) return;
        if (
          page.conversation_id !== conversationId ||
          page.generation_id !== generationId ||
          page.items.length > 256 ||
          new Set(page.items.map((item) => item.id)).size !==
            page.items.length ||
          (page.has_more && (!page.next_cursor || page.next_cursor === cursor))
        ) {
          setLoaded(null);
          setError('The queue changed while loading. Reload its first page.');
        } else setLoaded({ owner, cursor, page });
        setLoading(false);
      },
      () => {
        if (abort.signal.aborted || epoch !== sequence.current) return;
        setLoaded(null);
        setError(
          'Queued messages could not load. Your conversation is preserved.',
        );
        setLoading(false);
      },
    );
    return () => abort.abort();
  }, [conversationId, generationId, owner, cursor, refreshKey, reload]);
  const current =
    loaded?.owner === owner && loaded.cursor === cursor ? loaded.page : null;
  const draft = editing?.owner === owner ? editing : null;
  const validOwner = () => mounted.current && currentOwner.current === owner;
  async function act(
    type: 'edit' | 'remove' | 'dispatch',
    item: ClientQueueItem,
    text?: string,
  ) {
    if (!validOwner() || actionInFlight.current) return;
    const actionLifetime = lifetime.current;
    const stillCurrent = () =>
      validOwner() && lifetime.current === actionLifetime;
    actionInFlight.current = true;
    setBusy(true);
    setError('');
    try {
      await onAction(type, item.submission_id, item.revision, text);
      if (!stillCurrent()) return;
      setEditing(null);
      setReload((value) => value + 1);
    } catch {
      if (stillCurrent())
        setError(
          'The queued message could not change. Refresh the queue to check its current state; your edit remains here.',
        );
    } finally {
      if (stillCurrent()) {
        actionInFlight.current = false;
        setBusy(false);
      }
    }
  }
  function firstPage() {
    setSelection({ owner, cursor: null });
    setReload((value) => value + 1);
  }
  function confirmRemove(item: ClientQueueItem) {
    const confirmationLifetime = lifetime.current;
    overlay.open({
      kind: 'alert',
      title: 'Remove queued message?',
      description:
        'This removes the pending input before it runs. Your existing conversation stays available.',
      confirmLabel: 'Remove queued message',
      onConfirm: () => {
        if (validOwner() && lifetime.current === confirmationLifetime)
          void act('remove', item);
      },
    });
  }
  return (
    <section aria-label="Queued messages" aria-busy={loading || busy}>
      <h2>Queued messages</h2>
      <p className="muted">
        Queued messages run in order with their accepted model and resource
        targets. Consumed means the message reached a model invocation. Paused
        messages need an explicit continuation.
      </p>
      <div className="toolbar" role="group" aria-label="Queued message pages">
        <Button disabled={loading || busy || !cursor} onClick={firstPage}>
          First page
        </Button>
        <Button
          disabled={loading || busy}
          onClick={() => setReload((value) => value + 1)}
        >
          Refresh queue
        </Button>
        <Button
          disabled={loading || busy || !current?.has_more}
          onClick={() =>
            setSelection({ owner, cursor: current?.next_cursor ?? null })
          }
        >
          Next page
        </Button>
      </div>
      {loading && <Skeleton label="Loading queued messages" />}
      {error && (
        <ErrorState
          title="Queue needs attention"
          action={
            <Button disabled={busy} onClick={firstPage}>
              Reload queue
            </Button>
          }
        >
          {error}
        </ErrorState>
      )}
      {current && (
        <ol aria-label="Queued message list">
          {current.items.map((item) => (
            <li key={item.id} style={{ overflowWrap: 'anywhere' }}>
              <p style={{ whiteSpace: 'pre-wrap' }}>
                {item.text ||
                  (item.state === 'cancelled'
                    ? 'Removed queued message'
                    : 'Message content unavailable')}
              </p>
              <p className="muted">
                <span>
                  {item.state[0]!.toUpperCase() + item.state.slice(1)}
                </span>
                {' · '}Message ID: {item.id}
              </p>
              {draft?.item.id === item.id ? (
                <div className="stack">
                  <Field label="Edit queued message">
                    <textarea
                      className="input"
                      maxLength={16000}
                      value={draft.text}
                      disabled={busy}
                      onChange={(event) =>
                        setEditing({ ...draft, text: event.target.value })
                      }
                    />
                  </Field>
                  <div className="button-row">
                    <Button
                      disabled={busy || loading || !draft.text.trim()}
                      onClick={() => void act('edit', draft.item, draft.text)}
                    >
                      Save queued edit
                    </Button>
                    <Button disabled={busy} onClick={() => setEditing(null)}>
                      Cancel edit
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="button-row">
                  {item.editable && (
                    <Button
                      disabled={busy || loading}
                      onClick={() =>
                        setEditing({ owner, item, text: item.text })
                      }
                    >
                      Edit message
                    </Button>
                  )}
                  {item.removable && (
                    <Button
                      disabled={busy || loading}
                      onClick={() => confirmRemove(item)}
                    >
                      Remove message
                    </Button>
                  )}
                  {item.state === 'paused' && item.editable && (
                    <Button
                      disabled={busy || loading}
                      onClick={() => void act('dispatch', item)}
                    >
                      Continue queued message
                    </Button>
                  )}
                </div>
              )}
              {item.state === 'paused' && !item.editable && (
                <p className="muted">
                  This input was admitted before interruption. Use the
                  conversation Resume action to continue it.
                </p>
              )}
            </li>
          ))}
        </ol>
      )}
      {!loading && current?.items.length === 0 && (
        <EmptyState title="No queued messages">
          Messages sent while a run is active appear here.
        </EmptyState>
      )}
      {current && (
        <p className="muted" aria-live="polite">
          {current.items.length} messages on this page.{' '}
          {current.has_more
            ? 'More messages are available.'
            : 'End of this queue.'}
        </p>
      )}
    </section>
  );
}
