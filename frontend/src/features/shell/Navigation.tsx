import BuddySurface from '../buddy/BuddySurface';
import {
  useEffect,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
  type MouseEvent,
  type ReactNode,
} from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import {
  Bot,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Code2,
  FileText,
  Home,
  Layers,
  Library,
  LoaderCircle,
  MessageSquare,
  MoreHorizontal,
  Palette,
  PencilLine,
  Pin,
  Settings,
  Trash2,
  Upload,
  Workflow,
} from 'lucide-react';
import { useClientState, useRuntime } from '../../runtime';
import AttentionIndicator from './AttentionIndicator';
import { useShellSettled } from '../../shell-settled';
import { useOverlay } from '../../ui/overlays';
import {
  Brand,
  Button,
  Hint,
  IconButton,
  Menu,
  Segmented,
  Skeleton,
} from '../../ui/primitives';
import type { ConversationView } from '../../api/types';
import ConversationActions from '../settings/ConversationActions';
import { deleteOneConversation } from './ConversationLibrary';
import type {
  GoalProfileSettingsSession,
  ProfileSummary,
} from '../settings/GoalProfileSettings';
import { openAgentProfiles } from './agent-profiles';
import { ConversationGlyph } from './ConversationGlyph';
import {
  CONVERSATION_TYPES,
  matchesType,
  withRetainedRow,
  recencyGroup,
  type ConversationType,
} from './conversation-groups';
import { absoluteTime } from '../../ui/format';

const PREVIEW_COUNT = 10;
const PINNED_PREVIEW_COUNT = 5;
const TYPE_KEY = 'row-bot.sidebar-type.v1';
const RECENCY_LABELS = {
  today: 'Today',
  yesterday: 'Yesterday',
  week: 'This week',
  older: 'Older',
} as const;

/** The footer dot's tooltip; the status itself is announced in the chat. */
const CONNECTION_LABELS: Record<string, string> = {
  ready: 'Connected',
  loading: 'Connecting…',
  reconnecting: 'Reconnecting…',
  disconnected: 'Disconnected',
  unauthorized: 'Sign-in needed',
  incompatible: 'Update needed',
};

const TYPE_ICONS: Record<ConversationType, ReactNode> = {
  all: <Layers size={14} aria-hidden />,
  chat: <MessageSquare size={14} aria-hidden />,
  designer: <Palette size={14} aria-hidden />,
  code: <Code2 size={14} aria-hidden />,
  workflow: <Workflow size={14} aria-hidden />,
};

function readType(): ConversationType {
  try {
    const value = localStorage.getItem(TYPE_KEY);
    return CONVERSATION_TYPES.some((type) => type.value === value)
      ? (value as ConversationType)
      : 'all';
  } catch {
    return 'all';
  }
}

/**
 * Agent profile counts are a sidebar nicety: they are read once per server
 * instance, after the open conversation has loaded, never on a reconnect to
 * the same instance (B29).
 */
function useProfileCounts(session: GoalProfileSettingsSession) {
  const { controller } = useRuntime();
  const state = useClientState();
  const refresh = useSyncExternalStore(
    session.subscribe,
    session.getSnapshot,
  ).profilesRefresh;
  const [counts, setCounts] = useState<{
    builtins: number;
    custom: number;
  } | null>(null);
  const loaded = useRef('');
  const instance = state.handshake?.instance_id ?? '';
  const settled = useShellSettled();
  useEffect(() => {
    const key = `${instance}\u0000${String(refresh)}`;
    if (!settled || !instance || loaded.current === key) return;
    const abort = new AbortController();
    const load = async () => {
      try {
        let cursor: string | undefined;
        let builtins = 0;
        let custom = 0;
        do {
          const page = await controller.profiles(
            '',
            undefined,
            cursor,
            abort.signal,
          );
          if (page.schema_version !== 1 || page.scope !== 'global')
            throw Error('invalid profile page');
          page.items.forEach((profile) => {
            if (profile.source === 'builtin') builtins++;
            else custom++;
          });
          cursor = page.next_cursor ?? undefined;
        } while (cursor && !abort.signal.aborted);
        if (!abort.signal.aborted) {
          loaded.current = key;
          setCounts({ builtins, custom });
        }
      } catch {
        if (!abort.signal.aborted) setCounts(null);
      }
    };
    void load();
    return () => abort.abort();
  }, [controller, instance, refresh, settled]);
  return counts;
}

function profileLabel(
  counts: { builtins: number; custom: number } | null,
): string {
  return counts
    ? `Agents: Agent profiles, ${counts.builtins} built-in · ${counts.custom} custom`
    : 'Agents: Agent profiles';
}

function openProfiles(
  event: MouseEvent<HTMLButtonElement>,
  options: Omit<Parameters<typeof openAgentProfiles>[0], 'returnFocusTo'>,
) {
  const returnFocusTo = event.currentTarget.closest('[role="dialog"]')
    ? document.querySelector<HTMLElement>(
        '.compact-controls [aria-label="Toggle navigation"]',
      )
    : event.currentTarget;
  openAgentProfiles({ ...options, returnFocusTo });
}

function AgentProfilesEntry({
  session,
  onStartProfileChat,
}: {
  session: GoalProfileSettingsSession;
  onStartProfileChat?: (profile: ProfileSummary) => void;
}) {
  const { controller } = useRuntime();
  const overlay = useOverlay();
  const counts = useProfileCounts(session);
  return (
    <Button
      className="nav-row nav-profiles"
      variant="ghost"
      aria-label={profileLabel(counts)}
      onClick={(event) =>
        openProfiles(event, {
          overlay,
          controller,
          session,
          onStartProfileChat,
        })
      }
    >
      <Bot size={16} aria-hidden />
      <span className="nav-row-label">Agents</span>
      {counts && (
        <small className="nav-row-meta" aria-hidden>
          {counts.builtins + counts.custom}
        </small>
      )}
    </Button>
  );
}

function activityLabel(
  states: NonNullable<ConversationView['generation_state']>,
  activityState: ConversationView['activity_state'],
  activityPhase: ConversationView['activity_phase'],
): { label: string; spin: boolean; attention?: boolean } | null {
  if (states.some((item) => item.status === 'running' && !item.quiesced))
    return { label: 'Generating response', spin: true };
  if (states.some((item) => item.status === 'stopping' && !item.quiesced))
    return { label: 'Stopping response', spin: true };
  if (
    states.some((item) => item.status === 'waiting_approval' && !item.quiesced)
  )
    return { label: 'Waiting for approval', spin: false };
  if (activityState === 'active') {
    const label =
      {
        background: 'Background agents working',
        child_running: 'Child agents working',
        approval_wait: 'Agent group needs approval',
        retry: 'Retrying agent work',
        stopping: 'Stopping agent work',
        later_wave_parent: 'Preparing agent work',
      }[activityPhase ?? ''] ?? 'Agents working';
    return { label, spin: activityPhase !== 'approval_wait' };
  }
  if (activityState === 'attention')
    return {
      label:
        activityPhase === 'waiting_approval'
          ? 'Waiting for approval'
          : 'Agent work needs attention',
      spin: false,
      attention: true,
    };
  return null;
}

/** "14:05" today, a weekday this week, "22 Sep" before that. */
function shortTime(value: string | undefined, now = new Date()): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const group = recencyGroup(value, now);
  return new Intl.DateTimeFormat(
    undefined,
    group === 'today'
      ? { hour: 'numeric', minute: '2-digit' }
      : group === 'older'
        ? { month: 'short', day: 'numeric' }
        : { weekday: 'short' },
  ).format(date);
}

/** Live store subscription also updates the compact modal's mounted content. */
export default function Navigation({
  onOpenConversation,
  onOpenHome,
  onNewChat,
  onStartProfileChat,
  creatingChat = false,
  showBuddy = true,
  headerActions,
}: {
  onOpenConversation?: () => void;
  onOpenHome?: () => void;
  onNewChat?: () => void;
  onStartProfileChat?: (profile: ProfileSummary) => void;
  creatingChat?: boolean;
  showBuddy?: boolean;
  /** Desktop header icon actions (commands, collapse). */
  headerActions?: ReactNode;
}) {
  const state = useClientState();
  const { controller, platform, conversationActionsOwner, goalProfileOwner } =
    useRuntime();
  const overlay = useOverlay();
  const navigate = useNavigate();
  const location = useLocation();
  // Navigate before closing, as Home does: closing the compact drawer pops its
  // same-URL history entry, and a pop that lands after the push undoes it.
  const openRoute = (to: string) => (event: MouseEvent<HTMLAnchorElement>) => {
    if (
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey
    )
      return;
    event.preventDefault();
    navigate(to);
    overlay.close();
  };
  const [sectionOpen, setSectionOpen] = useState(true);
  const [expanded, setExpanded] = useState(false);
  const [page, setPage] = useState(0);
  const [type, setTypeState] = useState<ConversationType>(readType);
  const setType = (value: ConversationType) => {
    setTypeState(value);
    setPage(0);
    try {
      localStorage.setItem(TYPE_KEY, value);
    } catch {
      /* The filter still applies for this session. */
    }
  };
  const [staleActivityIds, setStaleActivityIds] = useState<Set<string>>(
    () => new Set(),
  );
  const sectionId = useId();
  const sectionHeadingId = `${sectionId}-heading`;
  useEffect(() => {
    if (state.conversationGroup !== 'all') {
      setPage(0);
      void controller.setConversationGroup('all');
    }
  }, [controller, state.conversationGroup]);
  const selected =
    state.conversations.find(({ id }) => id === state.selectedConversationId) ??
    (state.conversation?.id === state.selectedConversationId
      ? state.conversation
      : null);
  const topLevel =
    state.conversationGroup === 'all'
      ? state.conversations.filter(
          (row) => !row.parent_conversation_id && matchesType(row, type),
        )
      : [];
  const allPinned = topLevel.filter((row) => row.pinned);
  const allRecent = topLevel.filter((row) => !row.pinned);
  const previewPinned = allPinned.slice(
    0,
    topLevel.length <= PREVIEW_COUNT ? PREVIEW_COUNT : PINNED_PREVIEW_COUNT,
  );
  const previewRecent = allRecent.slice(
    0,
    PREVIEW_COUNT - previewPinned.length,
  );
  const visible = expanded
    ? topLevel.slice(page * 100, page * 100 + 100)
    : [...previewPinned, ...previewRecent];
  const selectedParent = selected?.parent_conversation_id
    ? state.conversations.find(
        (row) => row.id === selected.parent_conversation_id,
      )
    : null;
  const activeTopLevel = selected?.parent_conversation_id
    ? selectedParent
    : selected;
  // The open conversation stays listed (in order) when the preview or a
  // collapsed page hides it, but a type filter is an explicit choice.
  const rows =
    activeTopLevel &&
    matchesType(activeTopLevel, type) &&
    !visible.some(({ id }) => id === activeTopLevel.id)
      ? withRetainedRow(visible, activeTopLevel)
      : visible;
  const pinnedRows = rows.filter((row) => row.pinned);
  const recentRows = rows.filter((row) => !row.pinned);
  const activeRowKey = rows
    .filter((row) =>
      activityLabel(
        row.generation_state ?? [],
        row.activity_state,
        row.activity_phase,
      ),
    )
    .map((row) => row.id)
    .join('|');
  useEffect(() => {
    if (!activeRowKey || state.status !== 'ready') return;
    const ids = activeRowKey.split('|');
    let alive = true;
    let inFlight = false;
    let request: AbortController | null = null;
    const poll = async () => {
      if (inFlight) return;
      inFlight = true;
      request = new AbortController();
      const timeout = window.setTimeout(() => request?.abort(), 8000);
      try {
        await Promise.all(
          ids.map(async (id) => {
            try {
              await controller.refreshListedConversation(id, request!.signal);
              if (alive)
                setStaleActivityIds((previous) => {
                  if (!previous.has(id)) return previous;
                  const next = new Set(previous);
                  next.delete(id);
                  return next;
                });
            } catch {
              if (alive)
                setStaleActivityIds((previous) => new Set(previous).add(id));
            }
          }),
        );
      } finally {
        window.clearTimeout(timeout);
        inFlight = false;
      }
    };
    const timer = window.setInterval(() => void poll(), 15000);
    return () => {
      alive = false;
      request?.abort();
      window.clearInterval(timer);
    };
  }, [activeRowKey, controller, state.status]);
  function openActions(
    conversation: ConversationView,
    initialPin?: boolean,
    initialExport: false | 'markdown' | 'pdf' = false,
  ) {
    const session = conversationActionsOwner?.get()?.get(conversation.id);
    if (!session) {
      overlay.notify(
        'Conversation actions are unavailable while reviewed actions need attention.',
      );
      return;
    }
    overlay.open({
      title: 'Conversation actions',
      description: `Review changes to ${conversation.title || 'this conversation'}.`,
      content: (
        <ConversationActions
          conversationId={conversation.id}
          session={session}
          load={controller.conversationActions}
          review={controller.reviewConversationAction}
          execute={controller.executeConversationAction}
          download={async (reference, fileName) => {
            const result = await platform.save(reference, fileName);
            if (result.status !== 'ok') throw Error(result.status);
          }}
          initialPin={initialPin}
          initialExport={initialExport}
          onChanged={() => {
            void controller.loadMoreConversations(true);
            if (state.selectedConversationId === conversation.id)
              void controller.selectConversation(conversation.id);
          }}
        />
      ),
    });
  }
  const now = new Date();
  function conversationRow(conversation: ConversationView, heading?: string) {
    const title = conversation.title || 'Untitled conversation';
    const observedActivity = activityLabel(
      conversation.id === state.selectedConversationId && state.projection
        ? state.projection.generation
          ? [state.projection.generation]
          : []
        : (conversation.generation_state ?? []),
      conversation.activity_state,
      conversation.activity_phase,
    );
    const activity =
      observedActivity && staleActivityIds.has(conversation.id)
        ? { label: 'Activity status unavailable', spin: false, attention: true }
        : observedActivity;
    const timestamp = shortTime(conversation.updated_at, now);
    return (
      <li
        key={conversation.id}
        className="nav-conversation-item"
        data-activity={
          activity ? (activity.attention ? 'attention' : 'live') : undefined
        }
      >
        {heading && <h4 className="nav-date-heading">{heading}</h4>}
        <Hint label={title}>
          <Button
            variant="ghost"
            className="nav-conversation-link"
            aria-label={title}
            aria-current={
              location.pathname === `/conversations/${conversation.id}`
                ? 'page'
                : undefined
            }
            onClick={() => {
              void controller.selectConversation(conversation.id);
              if (location.pathname !== `/conversations/${conversation.id}`)
                navigate(`/conversations/${conversation.id}`);
              onOpenConversation?.();
              overlay.close();
            }}
          >
            {activity?.attention ? (
              <CircleAlert size={16} role="img" aria-label={activity.label} />
            ) : activity ? (
              <LoaderCircle
                className={`nav-activity ${activity.spin ? 'nav-activity-spin' : ''}`}
                size={16}
                role="img"
                aria-label={activity.label}
              />
            ) : (
              <ConversationGlyph row={conversation} />
            )}
            <span className="nav-conversation-text">
              <span className="conversation-title">{title}</span>
              {timestamp && (
                <time
                  className="nav-conversation-date"
                  dateTime={conversation.updated_at}
                  title={absoluteTime(conversation.updated_at)}
                >
                  {timestamp}
                </time>
              )}
            </span>
          </Button>
        </Hint>
        <div className="nav-conversation-actions">
          <Hint
            label={
              conversation.pinned ? 'Unpin conversation' : 'Pin conversation'
            }
          >
            <Button
              variant="ghost"
              iconOnly
              className={`nav-pin ${conversation.pinned ? 'is-pinned' : ''}`}
              aria-label={`${conversation.pinned ? 'Unpin' : 'Pin'} ${title}`}
              aria-pressed={conversation.pinned}
              onClick={() => openActions(conversation, !conversation.pinned)}
            >
              <Pin
                size={14}
                fill={conversation.pinned ? 'currentColor' : 'none'}
                aria-hidden
              />
            </Button>
          </Hint>
          <Menu
            label={`Actions for ${title}`}
            iconOnly
            variant="ghost"
            className="nav-row-menu"
            actions={[
              {
                label: conversation.pinned ? 'Unpin' : 'Pin',
                icon: <Pin size={16} />,
                onSelect: () => openActions(conversation, !conversation.pinned),
              },
              {
                label: 'Rename',
                icon: <PencilLine size={16} />,
                onSelect: () => openActions(conversation),
              },
              {
                label: 'Export as Markdown',
                icon: <Upload size={16} />,
                onSelect: () =>
                  openActions(conversation, undefined, 'markdown'),
              },
              {
                label: 'Export as PDF',
                icon: <FileText size={16} />,
                onSelect: () => openActions(conversation, undefined, 'pdf'),
              },
              {
                label: 'Delete…',
                icon: <Trash2 size={16} />,
                danger: true,
                onSelect: (opener) => openDelete(conversation, opener),
              },
            ]}
          >
            <MoreHorizontal size={16} aria-hidden />
          </Menu>
        </div>
        {selected?.parent_conversation_id === conversation.id && (
          <Button
            variant="ghost"
            className="nav-child-link"
            aria-current={
              location.pathname === `/conversations/${selected.id}`
                ? 'page'
                : undefined
            }
            onClick={() => {
              void controller.selectConversation(selected.id);
              navigate(`/conversations/${selected.id}`);
              onOpenConversation?.();
              overlay.close();
            }}
          >
            <MessageSquare size={14} aria-hidden />
            {selected.title || 'Child conversation'}
          </Button>
        )}
      </li>
    );
  }
  // Recent rows keep the server's order; a date label starts each run.
  function recentList(list: ConversationView[]) {
    let previous = '';
    return list.map((row) => {
      const group = RECENCY_LABELS[recencyGroup(row.updated_at, now)];
      const heading = group !== previous ? group : undefined;
      previous = group;
      return conversationRow(row, heading);
    });
  }
  // One conversation is one confirmation; bulk deletion lives in the Library.
  function openDelete(
    conversation: ConversationView,
    opener?: HTMLElement | null,
  ) {
    const title = conversation.title || 'this conversation';
    overlay.open({
      kind: 'alert',
      title: `Delete '${title}'?`,
      description:
        'This removes the conversation history. Bound designs and workspaces remain. Running work must stop before deletion can finish.',
      confirmLabel: 'Delete conversation',
      returnFocusTo: opener,
      onConfirm: () =>
        void deleteOneConversation(controller, conversation).then((outcome) => {
          if (outcome.status === 'deleted') {
            if (state.selectedConversationId === conversation.id) navigate('/');
            controller.forgetConversation(conversation.id);
            overlay.notify(
              outcome.notice
                ? `Deleted '${title}'. ${outcome.notice}`
                : `Deleted '${title}'.`,
            );
          } else overlay.notify(outcome.message);
          void controller.loadMoreConversations(true);
        }),
    });
  }
  const typeLabel =
    CONVERSATION_TYPES.find((option) => option.value === type)?.label ?? 'All';
  return (
    <nav className="navigation" aria-label="Workspace navigation">
      <header className="nav-header">
        <Brand />
        <div className="nav-header-actions">
          <IconButton
            size="sm"
            label="New chat"
            shortcut="Mod+Shift+O"
            className="nav-new-chat"
            disabled={!onNewChat || creatingChat || state.status !== 'ready'}
            aria-busy={creatingChat || undefined}
            onClick={() => {
              overlay.close();
              onNewChat?.();
            }}
          >
            <PencilLine size={16} aria-hidden />
          </IconButton>
          {headerActions}
        </div>
      </header>
      <div
        className="nav-primary-actions"
        role="group"
        aria-label="Primary workspace actions"
      >
        <Link
          className="button ghost nav-row nav-home"
          to="/"
          aria-current={location.pathname === '/' ? 'page' : undefined}
          onClick={(event) => {
            if (
              event.button !== 0 ||
              event.metaKey ||
              event.ctrlKey ||
              event.shiftKey ||
              event.altKey
            )
              return;
            event.preventDefault();
            navigate('/');
            onOpenHome?.();
            overlay.close();
          }}
        >
          <Home size={16} aria-hidden />
          <span className="nav-row-label">Home</span>
        </Link>
        {goalProfileOwner?.get() && (
          <AgentProfilesEntry
            session={goalProfileOwner.get()!}
            onStartProfileChat={onStartProfileChat}
          />
        )}
      </div>
      <div className="nav-section-header">
        <Button
          id={sectionHeadingId}
          className="nav-heading"
          variant="ghost"
          aria-expanded={sectionOpen}
          aria-controls={sectionId}
          onClick={() => setSectionOpen((open) => !open)}
        >
          {sectionOpen ? (
            <ChevronDown size={14} aria-hidden />
          ) : (
            <ChevronRight size={14} aria-hidden />
          )}
          Conversations
        </Button>
        {sectionOpen && (
          <Segmented
            size="sm"
            className="nav-type-filter"
            label="Filter conversations"
            value={type}
            onChange={setType}
            options={CONVERSATION_TYPES.map((option) => ({
              value: option.value,
              label: option.label,
              icon: TYPE_ICONS[option.value],
              hideLabel: true,
            }))}
          />
        )}
      </div>
      {!sectionOpen && activeTopLevel && (
        <ul className="conversation-list" aria-label="Current conversation">
          {conversationRow(activeTopLevel)}
        </ul>
      )}
      {!sectionOpen && selected?.parent_conversation_id && !selectedParent && (
        <div className="nav-child-parent">
          <Button
            variant="ghost"
            onClick={() =>
              navigate(`/conversations/${selected.parent_conversation_id}`)
            }
          >
            Parent conversation
          </Button>
          <span>{selected.title || 'Child conversation'}</span>
        </div>
      )}
      <section
        id={sectionId}
        className="nav-conversations"
        aria-labelledby={sectionHeadingId}
        hidden={!sectionOpen}
      >
        {sectionOpen && (
          <>
            {state.conversationListError && (
              <div role="alert" className="nav-conversation-error">
                <p>{state.conversationListError.message}</p>
                <Button
                  disabled={state.loadingConversations}
                  onClick={() => {
                    setPage(0);
                    void controller.loadMoreConversations(true);
                  }}
                >
                  Retry conversations
                </Button>
              </div>
            )}
            {state.loadingConversations && rows.length === 0 ? (
              <Skeleton label="Loading conversations" />
            ) : rows.length === 0 ? (
              <p className="muted nav-empty">
                {type === 'all'
                  ? 'Your conversations will appear here.'
                  : `No ${typeLabel.toLowerCase()} in the loaded conversations.`}
              </p>
            ) : (
              <>
                {pinnedRows.length > 0 && (
                  <div className="nav-conversation-group">
                    <h3>Pinned</h3>
                    <ul
                      className="conversation-list"
                      aria-label="Pinned conversations"
                    >
                      {pinnedRows.map((row) => conversationRow(row))}
                    </ul>
                  </div>
                )}
                {recentRows.length > 0 && (
                  <div className="nav-conversation-group">
                    <ul
                      className="conversation-list"
                      aria-label="Recent conversations"
                    >
                      {recentList(recentRows)}
                    </ul>
                  </div>
                )}
              </>
            )}
            {selected?.parent_conversation_id && !selectedParent && (
              <div className="nav-child-parent">
                <Button
                  variant="ghost"
                  onClick={() => {
                    void controller.selectConversation(
                      selected.parent_conversation_id!,
                    );
                    navigate(
                      `/conversations/${selected.parent_conversation_id}`,
                    );
                    onOpenConversation?.();
                    overlay.close();
                  }}
                >
                  Parent conversation
                </Button>
                <span
                  aria-current={
                    location.pathname === `/conversations/${selected.id}`
                      ? 'page'
                      : undefined
                  }
                >
                  {selected.title || 'Child conversation'}
                </span>
              </div>
            )}
            <div className="nav-list-footer">
              {(topLevel.length > PREVIEW_COUNT ||
                state.hasMoreConversations) && (
                <Button
                  className="nav-more"
                  variant="ghost"
                  aria-expanded={expanded}
                  onClick={() => setExpanded((show) => !show)}
                >
                  {expanded ? 'Show less' : 'Show all'}
                </Button>
              )}
              {expanded && state.hasMoreConversations && (
                <Button
                  className="nav-more"
                  variant="ghost"
                  onClick={() => {
                    void controller
                      .loadMoreConversations()
                      .then(() =>
                        setPage(
                          Math.max(
                            0,
                            Math.ceil(
                              controller
                                .getSnapshot()
                                .conversations.filter(
                                  (row) =>
                                    !row.parent_conversation_id &&
                                    matchesType(row, type),
                                ).length / 100,
                            ) - 1,
                          ),
                        ),
                      );
                  }}
                  disabled={state.loadingConversations}
                >
                  Load more conversations
                </Button>
              )}
              {expanded && topLevel.length > 100 && (
                <div
                  className="button-row nav-pagination"
                  role="group"
                  aria-label="Conversation pages"
                >
                  <Button
                    disabled={!page}
                    onClick={() => setPage((value) => value - 1)}
                  >
                    Previous rows
                  </Button>
                  <Button
                    disabled={(page + 1) * 100 >= topLevel.length}
                    onClick={() => setPage((value) => value + 1)}
                  >
                    Next rows
                  </Button>
                </div>
              )}
              {expanded && state.conversations.length >= 1000 && (
                <Button
                  onClick={() => {
                    setPage(0);
                    void controller.loadMoreConversations(true);
                  }}
                >
                  Return to newest conversations
                </Button>
              )}
              <Link
                className="button ghost nav-more nav-library"
                to="/library"
                aria-current={
                  location.pathname === '/library' ? 'page' : undefined
                }
                onClick={openRoute('/library')}
              >
                <Library size={14} aria-hidden />
                Conversation library
              </Link>
            </div>
          </>
        )}
      </section>
      <footer className="nav-footer" aria-label="Workspace destinations">
        <AttentionIndicator
          load={controller.attention}
          onNavigate={openRoute}
        />
        <div className="nav-footer-row">
          {showBuddy ? (
            <BuddySurface />
          ) : (
            <span className="nav-footer-spacer" />
          )}
          <span
            className="nav-connection"
            data-state={state.status}
            title={CONNECTION_LABELS[state.status] ?? state.status}
            aria-hidden
          />
          <Hint label="Settings">
            <Link
              className="button ghost icon-button nav-settings"
              to="/settings/providers"
              aria-label="Settings"
              aria-current={
                location.pathname.startsWith('/settings') ? 'page' : undefined
              }
              onClick={openRoute('/settings/providers')}
            >
              <Settings size={16} aria-hidden />
            </Link>
          </Hint>
        </div>
        <details className="nav-secondary-destinations">
          <summary>About and developer utilities</summary>
          <Link
            className="button ghost"
            to="/primitives"
            onClick={openRoute('/primitives')}
          >
            Component gallery
          </Link>
        </details>
      </footer>
    </nav>
  );
}

/**
 * The collapsed desktop rail (B11): the same destinations as icons, each with
 * its full name as the accessible label and tooltip.
 */
export function NavigationRail({
  onNewChat,
  onStartProfileChat,
  creatingChat = false,
  railActions,
}: {
  onNewChat?: () => void;
  onStartProfileChat?: (profile: ProfileSummary) => void;
  creatingChat?: boolean;
  /** Expand and commands, owned by the workspace. */
  railActions: ReactNode;
}) {
  const state = useClientState();
  const { controller, goalProfileOwner } = useRuntime();
  const overlay = useOverlay();
  const location = useLocation();
  const session = goalProfileOwner?.get();
  return (
    <nav className="navigation-rail" aria-label="Collapsed navigation">
      <div className="navigation-rail-group">
        {railActions}
        <IconButton
          label="New chat"
          shortcut="Mod+Shift+O"
          disabled={!onNewChat || creatingChat || state.status !== 'ready'}
          onClick={() => onNewChat?.()}
        >
          <PencilLine size={18} aria-hidden />
        </IconButton>
        <Hint label="Home">
          <Link
            className="button ghost icon-button icon-action icon-action-md"
            to="/"
            aria-label="Home"
            aria-current={location.pathname === '/' ? 'page' : undefined}
          >
            <Home size={18} aria-hidden />
          </Link>
        </Hint>
        {session && (
          <IconButton
            label="Agents"
            onClick={(event) =>
              openProfiles(event, {
                overlay,
                controller,
                session,
                onStartProfileChat,
              })
            }
          >
            <Bot size={18} aria-hidden />
          </IconButton>
        )}
      </div>
      <div className="navigation-rail-group navigation-rail-footer">
        <AttentionIndicator load={controller.attention} compact />
        <Hint label="Settings">
          <Link
            className="button ghost icon-button icon-action icon-action-md"
            to="/settings/providers"
            aria-label="Settings"
            aria-current={
              location.pathname.startsWith('/settings') ? 'page' : undefined
            }
          >
            <Settings size={18} aria-hidden />
          </Link>
        </Hint>
        <BuddySurface />
      </div>
    </nav>
  );
}
