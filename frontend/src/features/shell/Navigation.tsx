import BuddySurface from '../buddy/BuddySurface';
import {
  useEffect,
  useEffectEvent,
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
  SquarePen,
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
  Kbd,
  Menu,
  Segmented,
  Skeleton,
} from '../../ui/primitives';
import { AgentAvatar, agentSeed } from '../../ui/AgentAvatar';
import type { ConversationView } from '../../api/types';
import ConversationActions, {
  conversationActionsDialog,
  runConversationAction,
} from '../settings/ConversationActions';
import { deleteOneConversation } from './ConversationLibrary';
import type {
  GoalProfileSettingsSession,
  ProfileSummary,
} from '../settings/GoalProfileSettings';
import { DEFAULT_PROFILE_ID, openAgentProfiles } from './agent-profiles';
import { useAgentFavourites } from './agent-favourites';
import { ConversationGlyph } from './ConversationGlyph';
import {
  CONVERSATION_TYPES,
  TYPE_GROUPS,
  matchesType,
  withRetainedRow,
  recencyGroup,
  shortTime,
  type ConversationType,
} from './conversation-groups';
import { absoluteTime, ariaKeyShortcut } from '../../ui/format';

const PREVIEW_COUNT = 10;
const PINNED_PREVIEW_COUNT = 5;
const TYPE_KEY = 'row-bot.sidebar-type.v1';
const AGENTS_KEY = 'row-bot.sidebar-agents.v1';
const FAVOURITE_COUNT = 5;
const NEW_CHAT_SHORTCUT = 'Mod+Shift+O';
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

const EMPTY_LABELS: Record<ConversationType, string> = {
  all: 'Your conversations will appear here.',
  chat: 'No chats yet.',
  designer: 'No designs yet.',
  code: 'No code conversations yet.',
  workflow: 'No workflow conversations yet.',
};

/** Reads the next page when the end of the list scrolls into view (B239). */
function ListEnd({
  loading,
  onReach,
}: {
  loading: boolean;
  onReach: () => void;
}) {
  const target = useRef<HTMLDivElement>(null);
  const reach = useEffectEvent(onReach);
  useEffect(() => {
    const element = target.current;
    // jsdom has no IntersectionObserver; every supported browser does. A new
    // observer after each page reports at once whether the end still shows.
    if (loading || !element || typeof IntersectionObserver === 'undefined')
      return;
    const observer = new IntersectionObserver(([entry]) => {
      if (entry?.isIntersecting) reach();
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [loading]);
  return <div ref={target} className="nav-list-end" aria-hidden />;
}

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

function readAgentsOpen(): boolean {
  try {
    return localStorage.getItem(AGENTS_KEY) !== 'collapsed';
  } catch {
    return true;
  }
}

const noSession = () => () => {};

/**
 * The agent library's profiles are a sidebar nicety: they are read once per
 * server instance, after the open conversation has loaded, never on a
 * reconnect to the same instance (B29).
 */
function useProfiles(
  session: GoalProfileSettingsSession | undefined,
): ProfileSummary[] | null {
  const { controller } = useRuntime();
  const state = useClientState();
  const refresh = useSyncExternalStore(
    session?.subscribe ?? noSession,
    () => session?.getSnapshot().profilesRefresh,
  );
  const [profiles, setProfiles] = useState<ProfileSummary[] | null>(null);
  const loaded = useRef('');
  const instance = state.handshake?.instance_id ?? '';
  const settled = useShellSettled();
  useEffect(() => {
    const key = `${instance}\u0000${String(refresh)}`;
    if (!session || !settled || !instance || loaded.current === key) return;
    const abort = new AbortController();
    const load = async () => {
      try {
        let cursor: string | undefined;
        const items: ProfileSummary[] = [];
        do {
          const page = await controller.profiles(
            '',
            undefined,
            cursor,
            abort.signal,
          );
          if (page.schema_version !== 1 || page.scope !== 'global')
            throw Error('invalid profile page');
          items.push(...page.items);
          cursor = page.next_cursor ?? undefined;
        } while (cursor && !abort.signal.aborted);
        if (!abort.signal.aborted) {
          loaded.current = key;
          setProfiles(items);
        }
      } catch {
        if (!abort.signal.aborted) setProfiles(null);
      }
    };
    void load();
    return () => abort.abort();
  }, [controller, instance, refresh, session, settled]);
  return profiles;
}

/**
 * Favourite agents (B268): the pinned profiles, in pin order. Until one is
 * pinned, the library's first five stand in (the Default profile is plain
 * New chat, so it never stands in).
 */
function favouriteProfiles(
  profiles: readonly ProfileSummary[],
  pinned: readonly string[],
): ProfileSummary[] {
  const enabled = profiles.filter((profile) => profile.enabled);
  const chosen = pinned.flatMap(
    (id) => enabled.find((profile) => profile.id === id) ?? [],
  );
  return chosen.length
    ? chosen
    : enabled
        .filter((profile) => profile.id !== DEFAULT_PROFILE_ID)
        .slice(0, FAVOURITE_COUNT);
}

function allAgentsLabel(profiles: readonly ProfileSummary[] | null): string {
  if (!profiles) return 'All agents';
  const builtins = profiles.filter(
    (profile) => profile.source === 'builtin',
  ).length;
  return `All agents (${profiles.length}): ${builtins} built-in · ${profiles.length - builtins} custom`;
}

function openProfiles(
  opener: HTMLElement | null,
  options: Omit<Parameters<typeof openAgentProfiles>[0], 'returnFocusTo'>,
) {
  const returnFocusTo = opener?.closest('[role="dialog"]')
    ? document.querySelector<HTMLElement>(
        '.compact-controls [aria-label="Toggle navigation"]',
      )
    : opener;
  openAgentProfiles({ ...options, returnFocusTo });
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

/** Live store subscription also updates the compact modal's mounted content. */
export default function Navigation({
  onOpenConversation,
  onNewChat,
  onStartProfileChat,
  creatingChat = false,
  showBuddy = true,
  headerActions,
}: {
  onOpenConversation?: () => void;
  onNewChat?: () => void;
  onStartProfileChat?: (profile: ProfileSummary) => void;
  creatingChat?: boolean;
  showBuddy?: boolean;
  /** Desktop header icon actions after Home (commands, collapse). */
  headerActions?: ReactNode;
}) {
  const state = useClientState();
  const { controller, platform, conversationActionsOwner, goalProfileOwner } =
    useRuntime();
  const overlay = useOverlay();
  const navigate = useNavigate();
  const location = useLocation();
  // Navigate before closing: closing the compact drawer pops its same-URL
  // history entry, and a pop that lands after the push undoes it.
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
  const [type, setTypeState] = useState<ConversationType>(readType);
  const setType = (value: ConversationType) => {
    setTypeState(value);
    try {
      localStorage.setItem(TYPE_KEY, value);
    } catch {
      /* The filter still applies for this session. */
    }
  };
  const [agentsOpen, setAgentsOpenState] = useState(readAgentsOpen);
  const toggleAgents = () => {
    setAgentsOpenState(!agentsOpen);
    try {
      localStorage.setItem(AGENTS_KEY, agentsOpen ? 'collapsed' : 'open');
    } catch {
      /* The section still folds for this session. */
    }
  };
  const agentsId = useId();
  const profileSession = goalProfileOwner?.get();
  const profiles = useProfiles(profileSession);
  const favourites = favouriteProfiles(profiles ?? [], useAgentFavourites());
  const newChatDisabled =
    !onNewChat || creatingChat || state.status !== 'ready';
  const profileChatDisabled =
    !onStartProfileChat || creatingChat || state.status !== 'ready';
  const startProfileChat = (profile: ProfileSummary) => {
    overlay.close();
    onStartProfileChat?.(profile);
  };
  const openLibrary = (opener: HTMLElement | null) => {
    if (profileSession)
      openProfiles(opener, {
        overlay,
        controller,
        session: profileSession,
        onStartProfileChat,
      });
  };
  const [staleActivityIds, setStaleActivityIds] = useState<Set<string>>(
    () => new Set(),
  );
  const sectionId = useId();
  const sectionHeadingId = `${sectionId}-heading`;
  // A type filter lists that type from the server, older matches included.
  const group = type === 'all' ? null : TYPE_GROUPS[type];
  useEffect(() => {
    void controller.setTypedConversations(group);
  }, [controller, group]);
  const typed =
    state.typedConversations?.group === group ? state.typedConversations : null;
  const listing = group
    ? {
        rows: typed?.rows ?? [],
        hasMore: typed?.hasMore ?? true,
        loading: typed?.loading ?? true,
        error: typed?.error ?? null,
        loadMore: () => void controller.loadMoreTypedConversations(),
      }
    : {
        rows: state.conversations,
        hasMore: state.hasMoreConversations,
        loading: state.loadingConversations,
        error: state.conversationListError,
        loadMore: () => void controller.loadMoreConversations(),
      };
  const selected =
    state.conversations.find(({ id }) => id === state.selectedConversationId) ??
    (state.conversation?.id === state.selectedConversationId
      ? state.conversation
      : null);
  const topLevel = listing.rows.filter((row) => !row.parent_conversation_id);
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
  const visible = expanded ? topLevel : [...previewPinned, ...previewRecent];
  const selectedParent = selected?.parent_conversation_id
    ? state.conversations.find(
        (row) => row.id === selected.parent_conversation_id,
      )
    : null;
  const activeTopLevel = selected?.parent_conversation_id
    ? selectedParent
    : selected;
  // The open conversation stays listed (in order) when the preview hides it
  // or its page has not loaded, but a type filter is an explicit choice.
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
    initialExport: false | 'markdown' | 'pdf' = false,
  ) {
    const session = conversationActionsOwner?.get()?.get(conversation.id);
    if (!session) {
      overlay.notify(
        'Conversation actions are unavailable while reviewed actions need attention.',
      );
      return;
    }
    overlay.open(
      conversationActionsDialog(
        conversation,
        session,
        <ConversationActions
          conversationId={conversation.id}
          session={session}
          load={controller.conversationActions}
          review={controller.reviewConversationAction}
          execute={controller.executeConversationAction}
          save={platform.save}
          initialExport={initialExport}
          onChanged={() => {
            void controller.loadMoreConversations(true);
            if (state.selectedConversationId === conversation.id)
              void controller.selectConversation(conversation.id);
          }}
          onDelete={(closeActions) => {
            // A rename or pin in the dialog moved the revision on.
            const saved = session.getSnapshot().snapshot;
            openDelete(
              saved
                ? {
                    ...conversation,
                    revision: saved.revision,
                    title: saved.title,
                  }
                : conversation,
              undefined,
              closeActions,
            );
          }}
        />,
      ),
    );
  }
  /** Pin and Unpin are one tap: the reviewed command without the dialog, and the open chat stays as it is. */
  async function togglePin(conversation: ConversationView) {
    const pinned = !conversation.pinned;
    const outcome = await runConversationAction(
      {
        load: controller.conversationActions,
        review: controller.reviewConversationAction,
        execute: controller.executeConversationAction,
      },
      conversation.id,
      'conversation.pin',
      { pinned },
      new AbortController().signal,
    );
    if (outcome.status === 'completed')
      void controller.loadMoreConversations(true);
    else
      overlay.notify(
        outcome.status === 'uncertain'
          ? "Row-Bot couldn't confirm that. Check the list before trying again."
          : `Couldn't ${pinned ? 'pin' : 'unpin'} it. Try again.`,
      );
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
              onClick={() => void togglePin(conversation)}
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
                onSelect: () => void togglePin(conversation),
              },
              {
                label: 'Rename',
                icon: <PencilLine size={16} />,
                onSelect: () => openActions(conversation),
              },
              {
                label: 'Export as Markdown',
                icon: <Upload size={16} />,
                onSelect: () => openActions(conversation, 'markdown'),
              },
              {
                label: 'Export as PDF',
                icon: <FileText size={16} />,
                onSelect: () => openActions(conversation, 'pdf'),
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
  // From the actions dialog, confirming closes the dialog too (B237).
  function openDelete(
    conversation: ConversationView,
    opener?: HTMLElement | null,
    closeActions?: () => void,
  ) {
    const title = conversation.title || 'this conversation';
    overlay.open({
      kind: 'alert',
      title: `Delete '${title}'?`,
      description:
        'This removes the conversation history. Bound designs and workspaces remain. Running work must stop before deletion can finish.',
      confirmLabel: 'Delete conversation',
      returnFocusTo: opener,
      onConfirm: () => {
        closeActions?.();
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
        });
      },
    });
  }
  return (
    <nav className="navigation" aria-label="Workspace navigation">
      <header className="nav-header">
        {/* The logo is a pointer shortcut to Home; keyboard and assistive
            technology reach Home through its button beside it. */}
        <Link
          className="nav-brand"
          to="/"
          tabIndex={-1}
          aria-hidden
          onClick={openRoute('/')}
        >
          <Brand />
        </Link>
        <div className="nav-header-actions">
          <Hint label="Home">
            <Link
              className="button ghost icon-button icon-action icon-action-sm"
              to="/"
              aria-label="Home"
              aria-current={location.pathname === '/' ? 'page' : undefined}
              onClick={openRoute('/')}
            >
              <Home size={16} aria-hidden />
            </Link>
          </Hint>
          {headerActions}
        </div>
      </header>
      {/* The sidebar's one primary action; ▾ starts a chat with an agent
          (B268). Its shortcut shows on hover and focus. */}
      <div className="nav-new-chat">
        <Button
          variant="ghost"
          className="nav-new-chat-main"
          aria-keyshortcuts={ariaKeyShortcut(NEW_CHAT_SHORTCUT)}
          disabled={newChatDisabled}
          aria-busy={creatingChat || undefined}
          onClick={() => {
            overlay.close();
            onNewChat?.();
          }}
        >
          <span className="nav-new-chat-icon" aria-hidden>
            <SquarePen size={15} />
          </span>
          <span className="nav-new-chat-label">New chat</span>
          <span className="nav-new-chat-kbd" aria-hidden>
            <Kbd keys={NEW_CHAT_SHORTCUT} />
          </span>
        </Button>
        {profileSession && (
          <Menu
            label="New chat with an agent…"
            hint="New chat with an agent…"
            iconOnly
            variant="ghost"
            className="nav-new-chat-more"
            actions={[
              ...favourites.map((profile) => ({
                label: profile.display_name,
                icon: (
                  <AgentAvatar
                    seed={agentSeed(profile.id, profile.id)}
                    size={18}
                  />
                ),
                disabled: profileChatDisabled,
                onSelect: () => startProfileChat(profile),
              })),
              {
                label: 'All agents…',
                icon: <Bot size={16} />,
                separatorBefore: true,
                onSelect: openLibrary,
              },
            ]}
          >
            <ChevronDown size={15} aria-hidden />
          </Menu>
        )}
      </div>
      {/* What needs the person (an approval, a problem, an update) sits up here: in the footer it grew the
          sticky footer over the last rows of the list. */}
      <AttentionIndicator
        load={controller.attention}
        loadApprovals={controller.pendingApprovals}
        onNavigate={openRoute}
      />
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
            {listing.error && (
              <div role="alert" className="nav-conversation-error">
                <p>{listing.error.message}</p>
                <Button
                  disabled={listing.loading}
                  onClick={() => void controller.loadMoreConversations(true)}
                >
                  Retry conversations
                </Button>
              </div>
            )}
            {rows.length === 0 &&
            (listing.loading || (listing.hasMore && !listing.error)) ? (
              <Skeleton label="Loading conversations" />
            ) : rows.length === 0 ? (
              <p className="muted nav-empty">{EMPTY_LABELS[type]}</p>
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
            {/* Show all pages in the rest as the list scrolls; the preview
                reads on until it has its rows (a page can hold none). */}
            {listing.hasMore &&
              !listing.error &&
              (expanded || topLevel.length < PREVIEW_COUNT) && (
                <ListEnd loading={listing.loading} onReach={listing.loadMore} />
              )}
            <div className="nav-list-footer">
              {(topLevel.length > PREVIEW_COUNT ||
                (listing.hasMore && topLevel.length > 0)) && (
                <Button
                  className="nav-more"
                  variant="ghost"
                  aria-expanded={expanded}
                  onClick={() => setExpanded((show) => !show)}
                >
                  {expanded ? 'Show less' : 'Show all'}
                </Button>
              )}
              {expanded && listing.rows.length >= 1000 && (
                <Button
                  onClick={() => void controller.loadMoreConversations(true)}
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
      {/* Agents, under the conversations: favourite profiles start a chat
          in one click; the library holds the rest. Runs belong to their
          conversation, so none show here (B268). */}
      {profileSession && (
        <section className="nav-agents" aria-labelledby={`${agentsId}-heading`}>
          <div className="nav-section-header">
            <Button
              id={`${agentsId}-heading`}
              className="nav-heading"
              variant="ghost"
              aria-expanded={agentsOpen}
              aria-controls={agentsId}
              onClick={toggleAgents}
            >
              {agentsOpen ? (
                <ChevronDown size={14} aria-hidden />
              ) : (
                <ChevronRight size={14} aria-hidden />
              )}
              Agents
            </Button>
          </div>
          <div id={agentsId} className="nav-agents-body" hidden={!agentsOpen}>
            {agentsOpen && (
              <>
                {favourites.length > 0 && (
                  <div
                    className="nav-agent-favourites"
                    role="group"
                    aria-label="Favourite agents"
                  >
                    {favourites.slice(0, FAVOURITE_COUNT).map((profile) => (
                      <IconButton
                        key={profile.id}
                        label={`New chat with ${profile.display_name}`}
                        disabled={profileChatDisabled}
                        onClick={() => startProfileChat(profile)}
                      >
                        <AgentAvatar
                          seed={agentSeed(profile.id, profile.id)}
                          size={22}
                        />
                      </IconButton>
                    ))}
                  </div>
                )}
                <Button
                  variant="ghost"
                  className="nav-all-agents"
                  aria-label={allAgentsLabel(profiles)}
                  onClick={(event) => openLibrary(event.currentTarget)}
                >
                  All agents
                  {profiles && (
                    <span className="nav-all-agents-count" aria-hidden>
                      {profiles.length}
                    </span>
                  )}
                  <ChevronRight size={14} aria-hidden />
                </Button>
              </>
            )}
          </div>
        </section>
      )}
      <footer className="nav-footer" aria-label="Workspace destinations">
        {showBuddy && <BuddySurface />}
        {/* Settings is its own row under Buddy's large avatar (B225). */}
        <Link
          className="button ghost nav-row nav-settings"
          to="/settings/providers"
          aria-current={
            location.pathname.startsWith('/settings') ? 'page' : undefined
          }
          onClick={openRoute('/settings/providers')}
        >
          <Settings size={16} aria-hidden />
          <span className="nav-row-label">Settings</span>
          <span
            className="nav-connection"
            data-state={state.status}
            title={CONNECTION_LABELS[state.status] ?? state.status}
            aria-hidden
          />
        </Link>
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
        {/* The rail's one prominent action (B268). */}
        <IconButton
          label="New chat"
          shortcut={NEW_CHAT_SHORTCUT}
          className="navigation-rail-new-chat"
          disabled={!onNewChat || creatingChat || state.status !== 'ready'}
          onClick={() => onNewChat?.()}
        >
          <SquarePen size={18} aria-hidden />
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
              openProfiles(event.currentTarget, {
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
        <AttentionIndicator
          load={controller.attention}
          loadApprovals={controller.pendingApprovals}
          compact
        />
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
