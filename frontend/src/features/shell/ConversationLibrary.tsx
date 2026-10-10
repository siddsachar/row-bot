import { Fragment, useEffect, useId, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  ChevronLeft,
  ChevronRight,
  Code2,
  Download,
  FileDown,
  FileText,
  Layers,
  MessageSquare,
  Palette,
  Pin,
  RefreshCw,
  Search,
  Trash2,
  Workflow,
  X,
  type LucideIcon,
} from 'lucide-react';
import type { ClientController } from '../../api/controller';
import { clientError } from '../../api/errors';
import type { Command, ConversationView } from '../../api/types';
import type { CapabilityResult, SavedFile } from '../../platform';
import { useClientSelector, useRuntime } from '../../runtime';
import { absoluteTime } from '../../ui/format';
import { useOverlay } from '../../ui/overlays';
import {
  Button,
  IconButton,
  Input,
  Menu,
  Segmented,
  Skeleton,
  Toolbar,
  ToolbarSeparator,
} from '../../ui/primitives';
import {
  runConversationAction,
  type ConversationActionsApi,
} from '../settings/ConversationActions';
import { ConversationGlyph } from './ConversationGlyph';
import {
  CONVERSATION_TYPES,
  groupConversations,
  matchesType,
  shortTime,
  type ConversationType,
} from './conversation-groups';
import { SearchResults } from './SearchConversations';

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
const TYPE_ICONS: Record<ConversationType, LucideIcon> = {
  all: Layers,
  chat: MessageSquare,
  designer: Palette,
  code: Code2,
  workflow: Workflow,
};
const count = (value: number) =>
  `${value.toLocaleString()} conversation${value === 1 ? '' : 's'}`;
const titleOf = (row: ConversationView) => row.title || 'Untitled conversation';
/** The sidebar's order: pinned first, then Today, Yesterday, … */
const inGroupOrder = (rows: ConversationView[], now: Date) =>
  groupConversations(rows, now).flatMap((group) => group.rows);

export type SingleDeleteOutcome =
  | { status: 'deleted'; notice: string }
  | { status: 'not_stopped' | 'failed' | 'uncertain'; message: string };

/**
 * Delete one conversation with the Library's receipt and recovery rules: an
 * unconfirmed outcome is kept for the Library page to check, never retried.
 */
export async function deleteOneConversation(
  controller: Pick<ClientController, 'getSnapshot' | 'command'>,
  row: Pick<ConversationView, 'id' | 'revision'>,
): Promise<SingleDeleteOutcome> {
  const session = controller.getSnapshot().handshake?.client_session_id;
  if (!session)
    return {
      status: 'failed',
      message: 'The session changed. Reconnect and try again.',
    };
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
    if (receipt.status !== 'DeleteCompleted')
      return {
        status: 'not_stopped',
        message: 'Running work has not stopped. Stop it, then delete again.',
      };
    const notice =
      receipt.retained_developer_work || receipt.deletion_warnings?.length
        ? receipt.deletion_warnings?.join(' ') ||
          'Developer work with changes was retained.'
        : '';
    return { status: 'deleted', notice };
  } catch (cause) {
    const safe = clientError(cause);
    if (safe.code === 'operation_uncertain')
      return {
        status: 'uncertain',
        message:
          'The deletion outcome is unconfirmed. Open the Library to check it before trying again.',
      };
    clearPendingRecovery(key);
    return { status: 'failed', message: safe.message };
  }
}

/**
 * Every conversation: one search for titles and messages, a type filter, and
 * selection by ticking rows, with a bar that stays in view (B270). Bulk Pin
 * and Export run the reviewed conversation actions one by one; deletion is
 * revision-fenced per conversation with receipts.
 */
export default function ConversationLibrary({
  initialSelectedId,
}: {
  initialSelectedId?: string;
}) {
  const { controller, platform } = useRuntime();
  // Only the search: token updates elsewhere must not re-render the list.
  const hasSearch = useClientSelector((state) => Boolean(state.search));
  const overlay = useOverlay();
  const navigate = useNavigate();
  const [rows, setRows] = useState<ConversationView[] | null>(null);
  const [error, setError] = useState('');
  const [fresh, setFresh] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [filter, setFilter] = useState<ConversationType>('all');
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<'' | 'delete' | 'bulk'>('');
  const [completed, setCompleted] = useState(0);
  const [notStarted, setNotStarted] = useState(0);
  const [failures, setFailures] = useState<FailedDelete[]>([]);
  const [retainedNotices, setRetainedNotices] = useState<string[]>([]);
  const [pendingRecovery, setPendingRecovery] = useState(readPendingRecovery);
  const [recoveryStatus, setRecoveryStatus] = useState('');
  const [query, setQuery] = useState('');
  const [searchedFor, setSearchedFor] = useState('');
  const [searchError, setSearchError] = useState('');
  const cancel = useRef(false);
  const mounted = useRef(true);
  const running = useRef(false);
  const bulkAbort = useRef<AbortController | null>(null);
  const anchor = useRef<string | null>(null);
  const initialSelection = useRef(Boolean(initialSelectedId));
  const headingId = useId();

  async function refresh(signal?: AbortSignal) {
    setError('');
    setFresh(false);
    setRefreshing(true);
    try {
      const result = await controller.readConversationLibrary(signal);
      if (!mounted.current) return;
      setRows(result);
      setFresh(true);
      if (initialSelection.current && initialSelectedId) {
        initialSelection.current = false;
        const index = inGroupOrder(result, new Date()).findIndex(
          (row) => row.id === initialSelectedId,
        );
        setPage(index >= 0 ? Math.floor(index / PAGE_SIZE) : 0);
        setSelected(index >= 0 ? new Set([initialSelectedId]) : new Set());
      } else {
        setPage(0);
      }
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
      bulkAbort.current?.abort();
      abort.abort();
    };
    // The controller is the stable owner of this mounted library task.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [controller]);

  const now = new Date();
  const matching = inGroupOrder(
    (rows ?? []).filter((row) => matchesType(row, filter)),
    now,
  );
  const visible = matching.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const selectedRows = (rows ?? []).filter((row) => selected.has(row.id));
  const allSelected =
    matching.length > 0 && matching.every((row) => selected.has(row.id));
  const someSelected = matching.some((row) => selected.has(row.id));
  const allPinned =
    selectedRows.length > 0 && selectedRows.every((row) => row.pinned);
  const searching = Boolean(searchedFor) && hasSearch;

  // A tick toggles one row; Shift extends from the last ticked row to this
  // one, setting the whole range the way this row goes.
  function toggle(id: string, range: boolean) {
    const ids = visible.map((row) => row.id);
    const from = anchor.current ? ids.indexOf(anchor.current) : -1;
    const to = ids.indexOf(id);
    const targets =
      range && from >= 0 && to >= 0
        ? ids.slice(Math.min(from, to), Math.max(from, to) + 1)
        : [id];
    anchor.current = id;
    setSelected((previous) => {
      const next = new Set(previous);
      const on = !previous.has(id);
      for (const target of targets) {
        if (on) next.add(target);
        else next.delete(target);
      }
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

  async function runSearch(cursor?: string) {
    const text = cursor ? searchedFor : query.trim();
    if (!text) return;
    setSearchError('');
    setSearchedFor(text);
    try {
      await controller.searchLibrary(text, undefined, cursor);
    } catch (cause) {
      if (mounted.current) setSearchError(clientError(cause).message);
    }
  }

  // Results follow typing after a short pause; Enter searches at once.
  useEffect(() => {
    const text = query.trim();
    if (!text || text === searchedFor) return;
    const timer = setTimeout(() => void runSearch(), 350);
    return () => clearTimeout(timer);
  }, [query]); // eslint-disable-line react-hooks/exhaustive-deps

  function clearSearch() {
    setSearchedFor('');
    setSearchError('');
    void controller.searchLibrary('');
  }

  async function bulk(kind: 'pin' | 'markdown' | 'pdf') {
    if (running.current || !selectedRows.length) return;
    const api: ConversationActionsApi = {
      load: controller.conversationActions,
      review: controller.reviewConversationAction,
      execute: controller.executeConversationAction,
    };
    const pinned = !allPinned;
    const targets =
      kind === 'pin'
        ? selectedRows.filter((row) => row.pinned !== pinned)
        : [...selectedRows];
    running.current = true;
    cancel.current = false;
    const abort = new AbortController();
    bulkAbort.current = abort;
    setBusy('bulk');
    const failed: string[] = [];
    let done = 0;
    let saved: SavedFile | null = null;
    try {
      for (const row of targets) {
        if (cancel.current) break;
        const outcome = await runConversationAction(
          api,
          row.id,
          kind === 'pin' ? 'conversation.pin' : 'conversation.export',
          kind === 'pin' ? { pinned } : kind === 'pdf' ? { format: 'pdf' } : {},
          abort.signal,
        );
        const exported =
          outcome.status === 'completed' ? outcome.receipt.export : undefined;
        if (outcome.status !== 'completed' || (kind !== 'pin' && !exported)) {
          failed.push(titleOf(row));
          continue;
        }
        if (exported) {
          const result = await platform
            .save(exported.attachment_ref, exported.file_name)
            .catch((): CapabilityResult<SavedFile> => ({
              status: 'unavailable',
              reason: 'operation_failed',
            }));
          // Cancelling one Save dialog stops the rest.
          if (result.status === 'cancelled') break;
          if (result.status !== 'ok') {
            failed.push(titleOf(row));
            continue;
          }
          saved = result.value;
        }
        done += 1;
      }
    } finally {
      running.current = false;
      bulkAbort.current = null;
    }
    if (!mounted.current) return;
    setBusy('');
    if (kind === 'pin' && done)
      await Promise.all([refresh(), controller.loadMoreConversations(true)]);
    if (!done && !failed.length) return;
    const problems = failed.length
      ? ` ${failed.length} couldn’t be ${kind === 'pin' ? 'changed' : 'exported'}: ${failed.slice(0, 3).join(', ')}${failed.length > 3 ? ` and ${failed.length - 3} more` : ''}.`
      : '';
    const tone = failed.length ? 'warning' : undefined;
    const folder = saved?.kind === 'exports' ? saved : null;
    if (kind === 'pin')
      overlay.notify(
        `${pinned ? 'Pinned' : 'Unpinned'} ${count(done)}.${problems}`,
        tone,
      );
    else if (folder)
      overlay.notify(
        `Saved ${count(done)} to ${folder.folder}.${problems}`,
        tone,
        {
          label: 'Show in folder',
          onAction: () =>
            void folder.reveal().then((shown) => {
              if (!shown)
                overlay.notify(
                  'Row-Bot couldn’t open the Exports folder.',
                  'warning',
                );
            }),
        },
      );
    else
      overlay.notify(
        `${saved?.kind === 'download' ? `Started ${done} download${done === 1 ? '' : 's'}` : `Exported ${count(done)}`}.${problems}`,
        tone,
      );
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
    setBusy('delete');
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
            controller.forgetConversation(row.id);
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
        setBusy('');
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

  function reviewDelete(opener: HTMLElement) {
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
      returnFocusTo: opener,
      onConfirm: () => void deleteReviewed(reviewed),
    });
  }

  async function retryUncertain() {
    if (running.current) return;
    running.current = true;
    cancel.current = false;
    setBusy('delete');
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
        setBusy('');
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
          'The earlier deletion is still running. Check again.',
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

  const pages = Math.ceil(matching.length / PAGE_SIZE);
  return (
    <section
      className="conversation-library"
      aria-label="Conversation library"
      onKeyDown={(event) => {
        // Esc clears the selection, but not from the search field or from a
        // menu opened here (its portal is outside this section).
        const target = event.target as HTMLElement;
        if (
          event.key !== 'Escape' ||
          !selected.size ||
          busy ||
          !event.currentTarget.contains(target) ||
          (target instanceof HTMLInputElement && target.type !== 'checkbox')
        )
          return;
        event.preventDefault();
        setSelected(new Set());
      }}
    >
      <div className="library-toolbar">
        <form
          className="library-search"
          role="search"
          onSubmit={(event) => {
            event.preventDefault();
            void runSearch();
          }}
        >
          <Search size={16} aria-hidden />
          <Input
            type="search"
            aria-label="Search titles and messages"
            placeholder="Search titles and messages"
            value={query}
            maxLength={200}
            onChange={(event) => {
              setQuery(event.target.value);
              if (!event.target.value.trim() && searchedFor) clearSearch();
            }}
          />
        </form>
        <Segmented
          size="sm"
          className="library-types"
          label="Conversation type"
          value={filter}
          onChange={(value) => {
            setFilter(value);
            setPage(0);
          }}
          options={CONVERSATION_TYPES.map((option) => {
            const Icon = TYPE_ICONS[option.value];
            return {
              value: option.value,
              label: option.label,
              icon: <Icon size={15} aria-hidden />,
            };
          })}
        />
        <IconButton
          label="Refresh library"
          disabled={Boolean(busy) || refreshing}
          onClick={() => void refresh()}
        >
          <RefreshCw size={16} aria-hidden />
        </IconButton>
      </div>
      {error && <p role="alert">{error}</p>}
      {searchError && <p role="alert">{searchError}</p>}
      {pendingRecovery && (
        <div role="status">
          <p>
            An earlier deletion has an uncertain result. Check its original
            receipt or reconcile the current library before another batch.
          </p>
          <Button
            disabled={Boolean(busy)}
            onClick={() => void checkOriginalReceipt()}
          >
            Check deletion
          </Button>
          <Button
            disabled={Boolean(busy) || refreshing}
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
      {searching ? (
        <div className="library-search-results">
          <SearchResults onContinue={(cursor) => void runSearch(cursor)} />
        </div>
      ) : !rows ? (
        <>
          <Skeleton label="Loading all conversations" />
          <Button disabled={refreshing} onClick={() => void refresh()}>
            Retry library
          </Button>
        </>
      ) : (
        <>
          <div
            className="library-bar"
            data-selecting={selected.size > 0 || undefined}
          >
            {selected.size > 0 ? (
              <Toolbar
                label="Selected conversations"
                className="library-selection"
              >
                <label className="library-check-all">
                  <input
                    type="checkbox"
                    aria-label={`Select all ${matching.length.toLocaleString()} in this filter`}
                    checked={allSelected}
                    ref={(element) => {
                      if (element)
                        element.indeterminate = someSelected && !allSelected;
                    }}
                    disabled={Boolean(busy) || matching.length === 0}
                    onChange={toggleAll}
                  />
                </label>
                <span className="library-count" aria-live="polite">
                  {selected.size.toLocaleString()} selected
                </span>
                <ToolbarSeparator />
                <IconButton
                  label={allPinned ? 'Unpin' : 'Pin'}
                  disabled={Boolean(busy)}
                  onClick={() => void bulk('pin')}
                >
                  <Pin size={16} aria-hidden />
                </IconButton>
                <Menu
                  label="Export"
                  hint="Export as Markdown or PDF"
                  iconOnly
                  variant="ghost"
                  className="icon-action icon-action-md"
                  disabled={Boolean(busy)}
                  actions={[
                    {
                      label: 'Markdown',
                      icon: <FileText size={16} />,
                      onSelect: () => void bulk('markdown'),
                    },
                    {
                      label: 'PDF',
                      icon: <FileDown size={16} />,
                      onSelect: () => void bulk('pdf'),
                    },
                  ]}
                >
                  <Download size={16} aria-hidden />
                </Menu>
                <IconButton
                  label="Delete…"
                  variant="danger"
                  disabled={Boolean(busy) || !fresh || !!pendingRecovery}
                  onClick={(event) => reviewDelete(event.currentTarget)}
                >
                  <Trash2 size={16} aria-hidden />
                </IconButton>
                <span className="library-bar-spacer" />
                <IconButton
                  label="Clear selection"
                  shortcut="Escape"
                  disabled={Boolean(busy)}
                  onClick={() => setSelected(new Set())}
                >
                  <X size={16} aria-hidden />
                </IconButton>
              </Toolbar>
            ) : (
              <p className="library-summary">
                {count(matching.length)} · newest first
              </p>
            )}
          </div>
          {busy === 'delete' && (
            <Button
              onClick={() => {
                cancel.current = true;
              }}
            >
              Stop after current deletion
            </Button>
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
                <Button
                  disabled={Boolean(busy)}
                  onClick={() => void retryUncertain()}
                >
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
          {matching.length === 0 ? (
            <p>Nothing in this filter.</p>
          ) : (
            <div
              className="library-list"
              data-selecting={selected.size > 0 || undefined}
            >
              {groupConversations(visible, now).map((group) => (
                <Fragment key={group.id}>
                  <h2 id={`${headingId}-${group.id}`} className="library-group">
                    {group.label}
                  </h2>
                  <ul aria-labelledby={`${headingId}-${group.id}`}>
                    {group.rows.map((row) => {
                      const title = titleOf(row);
                      const date = shortTime(row.updated_at, now);
                      const checked = selected.has(row.id);
                      return (
                        <li
                          key={row.id}
                          className="library-row"
                          data-selected={checked || undefined}
                        >
                          {/* The label is the 44 px touch target. */}
                          <label className="library-check">
                            <input
                              type="checkbox"
                              aria-label={`Select ${title}`}
                              checked={checked}
                              disabled={Boolean(busy)}
                              onChange={(event) =>
                                toggle(
                                  row.id,
                                  Boolean(
                                    (event.nativeEvent as MouseEvent).shiftKey,
                                  ),
                                )
                              }
                            />
                          </label>
                          <span className="library-type" aria-hidden>
                            <ConversationGlyph row={row} />
                          </span>
                          <Link
                            className="library-row-link"
                            to={`/conversations/${encodeURIComponent(row.id)}`}
                            onKeyDown={(event) => {
                              // Space ticks the focused row (Enter opens it).
                              if (event.key !== ' ' || busy) return;
                              event.preventDefault();
                              toggle(row.id, event.shiftKey);
                            }}
                          >
                            <span className="library-row-title">{title}</span>
                            {date && (
                              <time
                                className="library-row-date"
                                dateTime={row.updated_at}
                                title={absoluteTime(row.updated_at)}
                              >
                                {date}
                              </time>
                            )}
                          </Link>
                        </li>
                      );
                    })}
                  </ul>
                </Fragment>
              ))}
            </div>
          )}
          {pages > 1 && (
            <div
              className="library-pages"
              role="group"
              aria-label="Library pages"
            >
              <IconButton
                label="Previous rows"
                disabled={page === 0 || Boolean(busy)}
                onClick={() => setPage((value) => value - 1)}
              >
                <ChevronLeft size={16} aria-hidden />
              </IconButton>
              <span>
                Page {page + 1} of {pages}
              </span>
              <IconButton
                label="Next rows"
                disabled={page + 1 >= pages || Boolean(busy)}
                onClick={() => setPage((value) => value + 1)}
              >
                <ChevronRight size={16} aria-hidden />
              </IconButton>
            </div>
          )}
        </>
      )}
    </section>
  );
}
