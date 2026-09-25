import BuddySurface from '../buddy/BuddySurface';
import {
  useEffect,
  useId,
  useState,
  useSyncExternalStore,
  type MouseEvent,
  type ReactNode,
} from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import {
  CircleAlert,
  BadgeCheck,
  ChevronDown,
  ChevronRight,
  Home,
  MessageSquare,
  MoreHorizontal,
  Pin,
  Plus,
  RotateCw,
  Search,
  Settings,
} from 'lucide-react';
import { useClientState, useRuntime } from '../../runtime';
import { useOverlay } from '../../ui/overlays';
import { Brand, Button, Hint, Menu, Skeleton } from '../../ui/primitives';
import type { ConversationView } from '../../api/types';
import ConversationActions from '../settings/ConversationActions';
import SearchConversations from './SearchConversations';
import ConversationLibrary from './ConversationLibrary';
import type {
  GoalProfileSettingsSession,
  ProfileSummary,
} from '../settings/GoalProfileSettings';
import { openAgentProfiles } from './agent-profiles';

const PREVIEW_COUNT = 10;
const PINNED_PREVIEW_COUNT = 5;

function AgentProfilesEntry({
  session,
  onStartProfileChat,
}: {
  session: GoalProfileSettingsSession;
  onStartProfileChat?: (profile: ProfileSummary) => void;
}) {
  const { controller } = useRuntime();
  const state = useClientState();
  const overlay = useOverlay();
  const refresh = useSyncExternalStore(
    session.subscribe,
    session.getSnapshot,
  ).profilesRefresh;
  const [counts, setCounts] = useState<{
    builtins: number;
    custom: number;
  } | null>(null);
  useEffect(() => {
    if (state.status !== 'ready') return;
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
        if (!abort.signal.aborted) setCounts({ builtins, custom });
      } catch {
        if (!abort.signal.aborted) setCounts(null);
      }
    };
    void load();
    return () => abort.abort();
  }, [controller, refresh, state.status]);
  return (
    <Button
      className="nav-profiles"
      variant="ghost"
      onClick={(event) => {
        const returnFocusTo = event.currentTarget.closest('[role="dialog"]')
          ? document.querySelector<HTMLElement>(
              '.compact-controls [aria-label="Toggle navigation"]',
            )
          : event.currentTarget;
        openAgentProfiles({
          overlay,
          controller,
          session,
          returnFocusTo,
          onStartProfileChat,
        });
      }}
    >
      <BadgeCheck size={16} aria-hidden />
      <span>Agent profiles</span>
      {counts && (
        <small>
          {counts.builtins} built-in · {counts.custom} custom
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
      label: 'Agent work needs attention',
      spin: false,
      attention: true,
    };
  return null;
}

function updatedLabel(value: string | undefined): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(date);
}

/** Live store subscription also updates the compact modal's mounted content. */
export default function Navigation({
  onOpenConversation,
  onOpenHome,
  onNewChat,
  onStartProfileChat,
  creatingChat = false,
  showBuddy = true,
  workspaceControls,
}: {
  onOpenConversation?: () => void;
  onOpenHome?: () => void;
  onNewChat?: () => void;
  onStartProfileChat?: (profile: ProfileSummary) => void;
  creatingChat?: boolean;
  showBuddy?: boolean;
  workspaceControls?: ReactNode;
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
      ? state.conversations.filter((row) => !row.parent_conversation_id)
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
  const rows =
    activeTopLevel && !visible.some(({ id }) => id === activeTopLevel.id)
      ? [...visible, activeTopLevel]
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
    initialExport = false,
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
  function conversationRow(conversation: ConversationView) {
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
    const timestamp = updatedLabel(conversation.updated_at);
    return (
      <li key={conversation.id} className="nav-conversation-item">
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
              <CircleAlert size={15} role="img" aria-label={activity.label} />
            ) : activity ? (
              <RotateCw
                className={activity.spin ? 'nav-activity-spin' : ''}
                size={15}
                role="img"
                aria-label={activity.label}
              />
            ) : (
              <MessageSquare size={15} aria-hidden />
            )}
            <span className="nav-conversation-text">
              <span className="conversation-title">{title}</span>
              {timestamp && (
                <time
                  className="nav-conversation-date"
                  dateTime={conversation.updated_at}
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
                onSelect: () => openActions(conversation, !conversation.pinned),
              },
              {
                label: 'Rename',
                onSelect: () => openActions(conversation),
              },
              {
                label: 'Export',
                onSelect: () => openActions(conversation, undefined, true),
              },
              {
                label: 'Delete…',
                danger: true,
                onSelect: () => openDelete(conversation),
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
  function openLibrary() {
    overlay.open({
      title: 'Browse conversations',
      description: 'Search history or manage saved conversations.',
      content: (
        <div className="conversation-browser">
          <SearchConversations />
          <details>
            <summary>Manage saved conversations</summary>
            <ConversationLibrary controller={controller} />
          </details>
        </div>
      ),
    });
  }
  function openDelete(conversation: ConversationView) {
    overlay.open({
      title: 'Delete conversation',
      description: `Review deletion of ${conversation.title || 'this conversation'}.`,
      content: (
        <ConversationLibrary
          controller={controller}
          initialSelectedId={conversation.id}
        />
      ),
    });
  }
  return (
    <nav className="navigation" aria-label="Workspace navigation">
      <Brand />
      {showBuddy && <BuddySurface />}
      {workspaceControls}
      <div
        className="nav-primary-actions"
        role="group"
        aria-label="Primary workspace actions"
      >
        <Link
          className="button ghost nav-home"
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
          <Home size={18} aria-hidden />
          Home
        </Link>
        <Button
          className="nav-new-chat"
          variant="primary"
          disabled={!onNewChat || creatingChat || state.status !== 'ready'}
          aria-label="New chat"
          onClick={() => {
            overlay.close();
            onNewChat?.();
          }}
        >
          <Plus size={18} aria-hidden />
          {creatingChat ? 'Creating…' : 'New chat'}
        </Button>
      </div>
      <Button className="nav-search" variant="ghost" onClick={openLibrary}>
        <Search size={16} aria-hidden />
        Browse conversations
      </Button>
      {goalProfileOwner?.get() && (
        <AgentProfilesEntry
          session={goalProfileOwner.get()!}
          onStartProfileChat={onStartProfileChat}
        />
      )}
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
                Your conversations will appear here.
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
                      {pinnedRows.map(conversationRow)}
                    </ul>
                  </div>
                )}
                {recentRows.length > 0 && (
                  <div className="nav-conversation-group">
                    <h3>Recent</h3>
                    <ul
                      className="conversation-list"
                      aria-label="Recent conversations"
                    >
                      {recentRows.map(conversationRow)}
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
                            controller.getSnapshot().conversations.length / 100,
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
          </>
        )}
      </section>
      <footer className="nav-footer" aria-label="Workspace destinations">
        <div className="nav-preferences">
          <Link
            className="button ghost"
            to="/settings/providers"
            aria-current={
              location.pathname.startsWith('/settings') ? 'page' : undefined
            }
            onClick={openRoute('/settings/providers')}
          >
            <Settings size={17} aria-hidden />
            Settings
          </Link>
        </div>
        <details className="nav-secondary-destinations">
          <summary>About and developer utilities</summary>
          <a href="/" className="button ghost">
            Current application
          </a>
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
