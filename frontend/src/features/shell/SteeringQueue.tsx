import { useEffect, useRef, useState } from 'react';
import type { ParentSteeringView } from '../../api/types';
import { Button, EmptyState, ErrorState, Skeleton } from '../../ui/primitives';

export type SteeringQueueProps = {
  conversationId: string;
  generationId: string;
  refreshKey: string;
  loadPage: (
    generationId: string,
    cursor: string | null,
    signal: AbortSignal,
  ) => Promise<ParentSteeringView>;
};

export default function SteeringQueue({
  conversationId,
  generationId,
  refreshKey,
  loadPage,
}: SteeringQueueProps) {
  const owner = JSON.stringify([conversationId, generationId]);
  const [selection, setSelection] = useState<{
    owner: string;
    cursor: string | null;
  }>({ owner, cursor: null });
  const [loaded, setLoaded] = useState<{
    owner: string;
    cursor: string | null;
    page: ParentSteeringView;
  } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const loader = useRef(loadPage);
  const sequence = useRef(0);
  const cursor = selection.owner === owner ? selection.cursor : null;

  useEffect(() => {
    loader.current = loadPage;
  }, [loadPage]);

  useEffect(() => {
    const epoch = ++sequence.current;
    if (!conversationId) return;
    const abort = new AbortController();
    setLoading(true);
    setError('');
    loader.current(generationId, cursor, abort.signal).then(
      (page) => {
        if (abort.signal.aborted || epoch !== sequence.current) return;
        if (
          page.conversation_id !== conversationId ||
          (generationId && page.generation_id !== generationId) ||
          page.items.length > 256 ||
          new Set(page.items.map((item) => item.id)).size !==
            page.items.length ||
          (page.has_more && (!page.next_cursor || page.next_cursor === cursor))
        ) {
          setLoaded(null);
          setError('The queue changed while loading. Reload its first page.');
        } else {
          setLoaded({ owner, cursor, page });
        }
        setLoading(false);
      },
      () => {
        if (abort.signal.aborted || epoch !== sequence.current) return;
        setLoaded(null);
        setError(
          'The steering queue could not load. Your conversation is preserved.',
        );
        setLoading(false);
      },
    );
    return () => abort.abort();
  }, [conversationId, generationId, owner, cursor, refreshKey, reload]);

  const current =
    loaded?.owner === owner && loaded.cursor === cursor ? loaded.page : null;

  function firstPage() {
    setSelection({ owner, cursor: null });
    setReload((value) => value + 1);
  }

  return (
    <section aria-label="Steering queue" aria-busy={loading}>
      <h2>Steering queue</h2>
      <p className="muted">
        Queued guidance is waiting for the agent. Consumed guidance has reached
        the agent. Pending guidance cannot be edited or removed here.
      </p>
      <div className="toolbar" role="group" aria-label="Steering queue pages">
        <Button disabled={loading || !cursor} onClick={firstPage}>
          First page
        </Button>
        <Button
          disabled={loading}
          onClick={() => setReload((value) => value + 1)}
        >
          Refresh queue
        </Button>
        <Button
          disabled={loading || !current?.has_more || !current.next_cursor}
          onClick={() =>
            setSelection({ owner, cursor: current?.next_cursor ?? null })
          }
        >
          Next page
        </Button>
      </div>
      {loading && <Skeleton label="Loading steering queue" />}
      {error && (
        <ErrorState
          title="Steering queue unavailable"
          action={<Button onClick={firstPage}>Reload queue</Button>}
        >
          {error}
        </ErrorState>
      )}
      {current && current.items.length > 0 && (
        <ol aria-label="Steering messages">
          {current.items.map((item) => (
            <li key={item.id} style={{ overflowWrap: 'anywhere' }}>
              <p style={{ whiteSpace: 'pre-wrap' }}>{item.text}</p>
              <p className="muted">
                <span>{item.state === 'consumed' ? 'Consumed' : 'Queued'}</span>
                {' · '}
                <span>Message ID: {item.id}</span>
              </p>
            </li>
          ))}
        </ol>
      )}
      {!loading && !error && current?.items.length === 0 && (
        <EmptyState title="No steering messages">
          Guidance sent during this run will appear here.
        </EmptyState>
      )}
      {current && (
        <p aria-live="polite" className="muted">
          {current.items.length} messages on this page.
          {current.has_more
            ? ' More messages are available.'
            : ' End of this queue.'}
        </p>
      )}
    </section>
  );
}
