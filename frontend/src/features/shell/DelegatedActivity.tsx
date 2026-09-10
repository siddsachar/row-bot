import { useEffect, useRef, useState } from 'react';
import type { DelegatedActivityView, DelegatedRun } from '../../api/types';
import { Button, Skeleton } from '../../ui/primitives';
import { useOverlay } from '../../ui/overlays';

type Props = {
  conversationId: string;
  refreshKey: string;
  ready?: boolean;
  loadPage: (
    cursor?: string,
    signal?: AbortSignal,
  ) => Promise<DelegatedActivityView>;
  loadRun: (runId: string, signal?: AbortSignal) => Promise<DelegatedRun>;
  openConversation: (id: string) => Promise<void>;
};

function RunDetail({
  runId,
  loadRun,
  openConversation,
  close,
}: {
  runId: string;
  loadRun: Props['loadRun'];
  openConversation: Props['openConversation'];
  close: () => void;
}) {
  const [run, setRun] = useState<DelegatedRun | null>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [opening, setOpening] = useState(false);
  const alive = useRef(false);
  const loader = useRef(loadRun);
  loader.current = loadRun;
  useEffect(() => {
    alive.current = true;
    const request = new AbortController();
    setError(false);
    void loader
      .current(runId, request.signal)
      .then((result) => {
        if (!request.signal.aborted) setRun(result);
      })
      .catch(() => {
        if (!request.signal.aborted) setError(true);
      });
    return () => {
      alive.current = false;
      request.abort();
    };
  }, [runId, attempt]);
  async function openChild() {
    if (opening) return;
    setOpening(true);
    try {
      const fresh = await loader.current(runId);
      if (!alive.current) return;
      setRun(fresh);
      if (fresh.child_conversation_id) {
        await openConversation(fresh.child_conversation_id);
        if (alive.current) close();
      }
    } catch {
      if (alive.current) setError(true);
    } finally {
      if (alive.current) setOpening(false);
    }
  }
  return (
    <>
      {error && (
        <p role="alert">
          This delegated task is unavailable.{' '}
          <Button onClick={() => setAttempt((value) => value + 1)}>
            Retry task
          </Button>
        </p>
      )}
      {!run && !error && <Skeleton label="Loading delegated task" />}
      {run && (
        <>
          <p role="status">{run.status.replaceAll('_', ' ')}</p>
          <p>{run.summary || 'No public summary is available yet.'}</p>
          {run.child_conversation_id ? (
            <Button
              disabled={opening || error}
              onClick={() => void openChild()}
            >
              Open child conversation
            </Button>
          ) : (
            <p>Child conversation history is unavailable.</p>
          )}
        </>
      )}
      <Button onClick={close}>Back to parent</Button>
    </>
  );
}

export default function DelegatedActivity(props: Props) {
  const ready = props.ready ?? true;
  const overlay = useOverlay();
  const callbacks = useRef(props);
  callbacks.current = props;
  const [page, setPage] = useState<DelegatedActivityView | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const ticket = useRef(0);
  const continuation = useRef<AbortController | null>(null);
  const [laterPage, setLaterPage] = useState(false);
  const dismiss = useRef(overlay.dismiss);
  dismiss.current = overlay.dismiss;
  const key = `delegated-${props.conversationId}`;
  useEffect(
    () => () => {
      dismiss.current(key);
    },
    [key],
  );
  useEffect(() => {
    const current = ++ticket.current;
    if (!ready) {
      setLoading(false);
      setError(false);
      setPage(null);
      dismiss.current(key);
      return;
    }
    const request = new AbortController();
    setLoading(true);
    setError(false);
    setPage(null);
    setLaterPage(false);
    void callbacks.current
      .loadPage(undefined, request.signal)
      .then((result) => {
        if (!request.signal.aborted && current === ticket.current)
          setPage(result);
      })
      .catch(() => {
        if (!request.signal.aborted && current === ticket.current)
          setError(true);
      })
      .finally(() => {
        if (!request.signal.aborted && current === ticket.current)
          setLoading(false);
      });
    return () => {
      request.abort();
      continuation.current?.abort();
      // This is a request epoch, not a DOM node; invalidate load-more on unmount.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      ++ticket.current;
    };
  }, [props.conversationId, props.refreshKey, attempt, ready, key]);
  async function more() {
    if (loading || !page?.next_cursor) return;
    const current = ticket.current;
    const request = new AbortController();
    continuation.current?.abort();
    continuation.current = request;
    setLoading(true);
    try {
      const result = await callbacks.current.loadPage(
        page.next_cursor,
        request.signal,
      );
      if (!request.signal.aborted && current === ticket.current) {
        setPage(result);
        setLaterPage(true);
      }
    } catch {
      if (!request.signal.aborted && current === ticket.current) setError(true);
    } finally {
      if (!request.signal.aborted && current === ticket.current)
        setLoading(false);
    }
  }
  return (
    <section aria-label="Delegated tasks" className="activity">
      {page?.parent_conversation_id && (
        <Button
          onClick={() =>
            void callbacks.current
              .openConversation(page.parent_conversation_id!)
              .catch(() => setError(true))
          }
        >
          Back to parent conversation
        </Button>
      )}
      {error && (
        <p role="alert">
          Delegated tasks could not be loaded.{' '}
          <Button onClick={() => setAttempt((value) => value + 1)}>
            Retry delegated tasks
          </Button>
        </p>
      )}
      {loading && !page && <Skeleton label="Loading delegated tasks" />}
      {!!page?.items.length && (
        <>
          <h2>Delegated tasks</h2>
          <ul>
            {page.items.map((run) => (
              <li key={run.run_id}>
                <Button
                  onClick={() =>
                    overlay.open({
                      key,
                      title: run.name,
                      description: 'Public delegated task status and history.',
                      content: (
                        <RunDetail
                          runId={run.run_id}
                          loadRun={props.loadRun}
                          openConversation={props.openConversation}
                          close={() => dismiss.current(key)}
                        />
                      ),
                    })
                  }
                >
                  {run.name}
                </Button>{' '}
                — {run.status.replaceAll('_', ' ')}
              </li>
            ))}
          </ul>
        </>
      )}
      {page?.has_more && (
        <Button disabled={loading} onClick={() => void more()}>
          More delegated tasks
        </Button>
      )}
      {laterPage && (
        <Button onClick={() => setAttempt((value) => value + 1)}>
          Return to first delegated tasks
        </Button>
      )}
    </section>
  );
}
