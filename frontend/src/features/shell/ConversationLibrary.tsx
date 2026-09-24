import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import type { ClientController } from '../../api/controller';
import { clientError } from '../../api/errors';
import type { Command, ConversationView } from '../../api/types';
import { useOverlay } from '../../ui/overlays';
import { Button, Select, Skeleton } from '../../ui/primitives';

type Category = 'all' | 'chat' | 'designer' | 'code' | 'workflow';
type FailedDelete = {
  row: ConversationView;
  command: Command;
  key: string;
  message: string;
  uncertain: boolean;
};
type PendingRecovery = {
  target: string;
  key: string;
  revision: string;
  session: string;
};

const PAGE_SIZE = 100;
const RECOVERY_KEY = 'row-bot.conversation-library.pending-delete.v1';
function readPendingRecovery(): PendingRecovery | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(RECOVERY_KEY) || 'null');
    return value &&
      typeof value.target === 'string' &&
      /^[A-Za-z0-9:_-]{1,128}$/.test(value.target) &&
      typeof value.key === 'string' &&
      /^[0-9a-f]{8}-[0-9a-f-]{27,}$/.test(value.key) &&
      typeof value.revision === 'string' &&
      /^[0-9]{1,20}$/.test(value.revision) &&
      typeof value.session === 'string'
      ? value
      : null;
  } catch {
    return null;
  }
}
function writePendingRecovery(value: PendingRecovery) {
  try {
    sessionStorage.setItem(RECOVERY_KEY, JSON.stringify(value));
  } catch {
    // The server's admission receipt still protects the command if tab storage is unavailable.
  }
}
function clearPendingRecovery(key: string) {
  try {
    if (readPendingRecovery()?.key === key)
      sessionStorage.removeItem(RECOVERY_KEY);
  } catch {
    // The library refresh remains the fallback reconciliation path.
  }
}
const LABELS: Record<Category, string> = {
  all: 'All',
  chat: 'Chats',
  designer: 'Design',
  code: 'Code',
  workflow: 'Workflows',
};

/** Explicit library read and revision-fenced per-conversation deletion. */
export default function ConversationLibrary({
  controller,
}: {
  controller: ClientController;
}) {
  const overlay = useOverlay();
  const navigate = useNavigate();
  const [rows, setRows] = useState<ConversationView[] | null>(null);
  const [error, setError] = useState('');
  const [fresh, setFresh] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [filter, setFilter] = useState<Category>('all');
  const [page, setPage] = useState(0);
  const [selectionMode, setSelectionMode] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [completed, setCompleted] = useState(0);
  const [notStarted, setNotStarted] = useState(0);
  const [failures, setFailures] = useState<FailedDelete[]>([]);
  const [retainedNotices, setRetainedNotices] = useState<string[]>([]);
  const [pendingRecovery, setPendingRecovery] = useState(readPendingRecovery);
  const [recoveryStatus, setRecoveryStatus] = useState('');
  const cancel = useRef(false);
  const mounted = useRef(true);
  const running = useRef(false);

  async function refresh(signal?: AbortSignal) {
    setError('');
    setFresh(false);
    setRefreshing(true);
    try {
      const result = await controller.readConversationLibrary(signal);
      if (!mounted.current) return;
      setRows(result);
      setFresh(true);
      setPage(0);
      setSelected((previous) => {
        const available = new Set(result.map((row) => row.id));
        return new Set([...previous].filter((id) => available.has(id)));
      });
    } catch (cause) {
      if (!mounted.current || signal?.aborted) return;
      setError(clientError(cause).message);
    } finally {
      if (mounted.current) setRefreshing(false);
    }
  }

  useEffect(() => {
    mounted.current = true;
    const abort = new AbortController();
    void refresh(abort.signal);
    return () => {
      mounted.current = false;
      cancel.current = true;
      abort.abort();
    };
    // The controller is the stable owner of this mounted library task.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [controller]);

  const matching = (rows ?? []).filter(
    (row) => filter === 'all' || (row.category ?? 'chat') === filter,
  );
  const visible = matching.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const selectedRows = (rows ?? []).filter((row) => selected.has(row.id));
  const allSelected =
    matching.length > 0 && matching.every((row) => selected.has(row.id));

  function toggleOne(id: string) {
    setSelected((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    setSelected((previous) => {
      const next = new Set(previous);
      for (const row of matching) {
        if (allSelected) next.delete(row.id);
        else next.add(row.id);
      }
      return next;
    });
  }

  async function deleteReviewed(reviewed: ConversationView[]) {
    if (running.current || pendingRecovery) return;
    const session = controller.getSnapshot().handshake?.client_session_id;
    if (!session) {
      setError(
        'The session changed. Reconnect, refresh the library and review again.',
      );
      return;
    }
    running.current = true;
    cancel.current = false;
    setBusy(true);
    setCompleted(0);
    setNotStarted(0);
    setFailures([]);
    setRetainedNotices([]);
    const failed: FailedDelete[] = [];
    let done = 0;
    try {
      for (const row of reviewed) {
        if (cancel.current) break;
        const key = crypto.randomUUID();
        const command: Command = {
          command_id: key,
          client_session_id: session,
          type: 'conversation.delete',
          expected_revision: row.revision,
          payload: {},
        };
        writePendingRecovery({
          target: row.id,
          key,
          revision: row.revision,
          session,
        });
        try {
          const receipt = await controller.command(row.id, command, key);
          clearPendingRecovery(key);
          if (receipt.status !== 'DeleteCompleted') {
            failed.push({
              row,
              command,
              key,
              message:
                'Running work has not stopped. Refresh and review this conversation again.',
              uncertain: false,
            });
          } else {
            done += 1;
            if (controller.getSnapshot().selectedConversationId === row.id)
              navigate('/');
            if (
              receipt.retained_developer_work ||
              receipt.deletion_warnings?.length
            ) {
              const detail =
                receipt.deletion_warnings?.join(' ') ||
                'Developer work with changes was retained.';
              if (mounted.current)
                setRetainedNotices((previous) => [
                  ...previous,
                  `${row.title}: ${detail}`,
                ]);
            }
          }
        } catch (cause) {
          const safe = clientError(cause);
          if (safe.code === 'operation_uncertain' && mounted.current)
            setPendingRecovery({
              target: row.id,
              key,
              revision: row.revision,
              session,
            });
          else clearPendingRecovery(key);
          failed.push({
            row,
            command,
            key,
            message: safe.message,
            uncertain: safe.code === 'operation_uncertain',
          });
          if (safe.code === 'operation_uncertain') break;
        }
        if (mounted.current) setCompleted(done);
      }
    } finally {
      running.current = false;
      if (mounted.current) {
        const skipped = reviewed.slice(done + failed.length);
        setNotStarted(skipped.length);
        setFailures(failed);
        setBusy(false);
        setSelected(
          new Set([
            ...failed.map(({ row }) => row.id),
            ...skipped.map((row) => row.id),
          ]),
        );
        await Promise.all([refresh(), controller.loadMoreConversations(true)]);
      }
    }
  }

  function reviewDelete() {
    if (!selectedRows.length || busy || !fresh || pendingRecovery) return;
    const reviewed = [...selectedRows];
    overlay.open({
      kind: 'alert',
      title: `Delete ${reviewed.length} conversation${reviewed.length === 1 ? '' : 's'}?`,
      description:
        'This removes selected conversation histories. Bound designs and workspaces remain. Running work must stop before deletion can finish.',
      content: (
        <p>
          Selected:{' '}
          {reviewed
            .slice(0, 5)
            .map((row) => row.title)
            .join(', ')}
          {reviewed.length > 5 ? ` and ${reviewed.length - 5} more` : ''}.
        </p>
      ),
      confirmLabel: `Delete ${reviewed.length} conversation${reviewed.length === 1 ? '' : 's'}`,
      onConfirm: () => void deleteReviewed(reviewed),
    });
  }

  async function retryUncertain() {
    if (running.current) return;
    running.current = true;
    cancel.current = false;
    setBusy(true);
    const remaining: FailedDelete[] = [];
    try {
      for (const item of failures) {
        if (!item.uncertain || cancel.current) {
          remaining.push(item);
          continue;
        }
        try {
          const receipt = await controller.retryCommand(
            item.row.id,
            item.command,
            item.key,
          );
          if (receipt.status !== 'DeleteCompleted') {
            clearPendingRecovery(item.key);
            setPendingRecovery(null);
            remaining.push({
              ...item,
              uncertain: false,
              message: 'Deletion is still blocked. Refresh and review again.',
            });
          } else {
            clearPendingRecovery(item.key);
            setPendingRecovery(null);
            setCompleted((value) => value + 1);
            if (
              receipt.retained_developer_work ||
              receipt.deletion_warnings?.length
            ) {
              const detail =
                receipt.deletion_warnings?.join(' ') ||
                'Developer work with changes was retained.';
              setRetainedNotices((previous) => [
                ...previous,
                `${item.row.title}: ${detail}`,
              ]);
            }
            if (controller.getSnapshot().selectedConversationId === item.row.id)
              navigate('/');
          }
        } catch (cause) {
          const safe = clientError(cause);
          if (safe.code !== 'operation_uncertain') {
            clearPendingRecovery(item.key);
            setPendingRecovery(null);
          }
          remaining.push({
            ...item,
            message: safe.message,
            uncertain: safe.code === 'operation_uncertain',
          });
        }
      }
    } finally {
      running.current = false;
      if (mounted.current) {
        setFailures(remaining);
        setBusy(false);
        setSelected(new Set(remaining.map(({ row }) => row.id)));
        await Promise.all([refresh(), controller.loadMoreConversations(true)]);
      }
    }
  }

  async function checkOriginalReceipt() {
    if (!pendingRecovery || busy) return;
    if (
      pendingRecovery.session !==
      controller.getSnapshot().handshake?.client_session_id
    ) {
      setRecoveryStatus(
        'The session changed. Refresh the library and review only conversations still present.',
      );
      return;
    }
    try {
      const receipt = await controller.receipt(pendingRecovery.key);
      if (receipt.status === 'admitting' || receipt.status === 'accepted') {
        setRecoveryStatus(
          'The earlier deletion is still active. Check this receipt again.',
        );
        return;
      }
      if (receipt.status === 'DeleteCompleted')
        setRecoveryStatus(
          'The earlier deletion completed. The library has been refreshed.',
        );
      else
        setRecoveryStatus(
          'The earlier deletion did not complete. Refresh and review the remaining conversation.',
        );
      clearPendingRecovery(pendingRecovery.key);
      setPendingRecovery(null);
      await Promise.all([refresh(), controller.loadMoreConversations(true)]);
    } catch (cause) {
      setRecoveryStatus(
        `${clientError(cause).message} Check again or refresh the library before reviewing what remains.`,
      );
    }
  }

  async function reconcileFromLibrary() {
    if (!pendingRecovery || busy) return;
    setFresh(false);
    setRefreshing(true);
    try {
      const current = await controller.readConversationLibrary();
      if (!mounted.current) return;
      setRows(current);
      setPage(0);
      setRecoveryStatus(
        current.some((row) => row.id === pendingRecovery.target)
          ? 'The conversation is still present. Review its current state before retrying.'
          : 'The conversation is absent from the current library.',
      );
      clearPendingRecovery(pendingRecovery.key);
      setPendingRecovery(null);
      setSelected(new Set());
      setFresh(true);
      await controller.loadMoreConversations(true);
    } catch (cause) {
      if (mounted.current) setError(clientError(cause).message);
    } finally {
      if (mounted.current) setRefreshing(false);
    }
  }

  return (
    <section
      className="stack conversation-library"
      aria-label="Conversation library"
    >
      {error && <p role="alert">{error}</p>}
      {pendingRecovery && (
        <div role="status">
          <p>
            An earlier deletion has an uncertain result. Check its original
            receipt or reconcile the current library before another batch.
          </p>
          <Button disabled={busy} onClick={() => void checkOriginalReceipt()}>
            Check original deletion receipt
          </Button>
          <Button
            disabled={busy || refreshing}
            onClick={() => void reconcileFromLibrary()}
          >
            Reconcile current library
          </Button>
        </div>
      )}
      {recoveryStatus && <p role="status">{recoveryStatus}</p>}
      {rows && !fresh && (
        <p role="status">Refresh the library before reviewing deletion.</p>
      )}
      {!rows ? (
        <>
          <Skeleton label="Loading all conversations" />
          <Button disabled={refreshing} onClick={() => void refresh()}>
            Retry library
          </Button>
        </>
      ) : (
        <>
          <div className="button-row">
            <Select
              aria-label="Conversation type"
              value={filter}
              disabled={busy || refreshing}
              onChange={(event) => {
                setFilter(event.target.value as Category);
                setPage(0);
              }}
            >
              {(Object.keys(LABELS) as Category[]).map((category) => (
                <option key={category} value={category}>
                  {LABELS[category]}{' '}
                  {category === 'all'
                    ? rows.length
                    : rows.filter(
                        (row) => (row.category ?? 'chat') === category,
                      ).length}
                </option>
              ))}
            </Select>
            <Button
              disabled={busy || !fresh}
              onClick={() => {
                setSelectionMode((value) => !value);
                setSelected(new Set());
              }}
            >
              {selectionMode ? 'Done' : 'Select'}
            </Button>
            <Button
              disabled={busy || refreshing}
              onClick={() => void refresh()}
            >
              Refresh library
            </Button>
          </div>
          {selectionMode && (
            <div className="button-row">
              <Button
                disabled={busy || !fresh || matching.length === 0}
                onClick={toggleAll}
              >
                {allSelected ? 'Clear all in filter' : 'Select all in filter'}
              </Button>
              <Button
                disabled={
                  busy || !fresh || !!pendingRecovery || selected.size === 0
                }
                onClick={() => setSelected(new Set())}
              >
                Clear selection
              </Button>
              <span>{selected.size} selected</span>
            </div>
          )}
          {matching.length === 0 ? (
            <p>Nothing in this filter.</p>
          ) : (
            <ul className="conversation-library-list">
              {visible.map((row) => (
                <li key={row.id}>
                  {selectionMode ? (
                    <label>
                      <input
                        type="checkbox"
                        checked={selected.has(row.id)}
                        disabled={busy || !fresh}
                        onChange={() => toggleOne(row.id)}
                      />{' '}
                      {row.title || 'Untitled conversation'}
                    </label>
                  ) : (
                    <span>{row.title || 'Untitled conversation'}</span>
                  )}
                  <span className="muted">
                    {LABELS[row.category ?? 'chat']}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {matching.length > PAGE_SIZE && (
            <div className="button-row" role="group" aria-label="Library pages">
              <Button
                disabled={page === 0 || busy}
                onClick={() => setPage((value) => value - 1)}
              >
                Previous rows
              </Button>
              <span>
                Page {page + 1} of {Math.ceil(matching.length / PAGE_SIZE)}
              </span>
              <Button
                disabled={(page + 1) * PAGE_SIZE >= matching.length || busy}
                onClick={() => setPage((value) => value + 1)}
              >
                Next rows
              </Button>
            </div>
          )}
          {selectionMode && (
            <div className="button-row">
              <Button
                variant="danger"
                disabled={busy || !fresh || selected.size === 0}
                onClick={reviewDelete}
              >
                Delete selected
              </Button>
              {busy && (
                <Button
                  onClick={() => {
                    cancel.current = true;
                  }}
                >
                  Stop after current deletion
                </Button>
              )}
            </div>
          )}
          {(completed > 0 || failures.length > 0 || notStarted > 0) && (
            <div role="status">
              {completed} deleted; {failures.length} need review; {notStarted}{' '}
              not started.
              {failures.length > 0 && (
                <ul>
                  {failures.map((item) => (
                    <li key={item.row.id}>
                      {item.row.title}: {item.message}
                    </li>
                  ))}
                </ul>
              )}
              {failures.some((item) => item.uncertain) && (
                <Button disabled={busy} onClick={() => void retryUncertain()}>
                  Retry uncertain deletions
                </Button>
              )}
              {retainedNotices.length > 0 && (
                <div>
                  <p>Retained work and cleanup warnings:</p>
                  <ul>
                    {retainedNotices.map((notice) => (
                      <li key={notice}>{notice}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}
