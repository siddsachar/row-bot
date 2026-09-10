import { useId, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import {
  ChevronDown,
  ChevronRight,
  Home,
  MessageSquare,
  Plus,
  Search,
  Settings,
} from 'lucide-react';
import { useClientState, useRuntime } from '../../runtime';
import { useOverlay } from '../../ui/overlays';
import { Brand, Button, Hint, Skeleton, Select } from '../../ui/primitives';
import SearchConversations from './SearchConversations';

const PREVIEW_COUNT = 10;

/** Live store subscription also updates the compact modal's mounted content. */
export default function Navigation({
  onOpenConversation,
  onOpenHome,
  onNewChat,
  creatingChat = false,
  onPreferences,
}: {
  onOpenConversation?: () => void;
  onOpenHome?: () => void;
  onNewChat?: () => void;
  creatingChat?: boolean;
  onPreferences?: () => void;
}) {
  const state = useClientState();
  const { controller } = useRuntime();
  const overlay = useOverlay();
  const navigate = useNavigate();
  const location = useLocation();
  const [sectionOpen, setSectionOpen] = useState(true);
  const [expanded, setExpanded] = useState(false);
  const [page, setPage] = useState(0);
  const sectionId = useId();
  const selected =
    state.conversations.find(({ id }) => id === state.selectedConversationId) ??
    (state.conversation?.id === state.selectedConversationId
      ? state.conversation
      : null);
  const visible = expanded
    ? state.conversations.slice(page * 100, page * 100 + 100)
    : state.conversations.slice(0, PREVIEW_COUNT);
  const rows =
    selected && !visible.some(({ id }) => id === selected.id)
      ? [...visible, selected]
      : visible;
  function conversationRow(conversation: (typeof state.conversations)[number]) {
    const title = conversation.title || 'Untitled conversation';
    return (
      <li key={conversation.id}>
        <Hint label={title}>
          <Button
            variant="ghost"
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
            <MessageSquare size={15} aria-hidden />
            <span className="conversation-title">{title}</span>
            {conversation.pinned && <span aria-label="Pinned">★</span>}
          </Button>
        </Hint>
      </li>
    );
  }
  return (
    <nav className="navigation" aria-label="Workspace navigation">
      <Brand />
      <div className="nav-primary-actions">
        <Link
          className="button ghost nav-home"
          to="/"
          aria-current={location.pathname === '/' ? 'page' : undefined}
          onClick={() => {
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
      <Button
        className="nav-search"
        variant="ghost"
        onClick={() =>
          overlay.open({
            title: 'Search conversations',
            description: 'Search titles and the complete saved public history.',
            content: <SearchConversations />,
          })
        }
      >
        <Search size={16} aria-hidden />
        Search conversations
      </Button>
      <Button
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
      {!sectionOpen && selected && (
        <ul className="conversation-list" aria-label="Current conversation">
          {conversationRow(selected)}
        </ul>
      )}
      <div id={sectionId} className="nav-conversations" hidden={!sectionOpen}>
        {sectionOpen && (
          <>
            <Select
              aria-label="Conversation group"
              value={state.conversationGroup}
              onChange={(event) => {
                setPage(0);
                void controller.setConversationGroup(
                  event.target.value as typeof state.conversationGroup,
                );
              }}
            >
              <option value="all">All conversations</option>
              <option value="pinned">Pinned</option>
              <option value="artifact">With Deck resources</option>
              <option value="workspace">With coding workspaces</option>
            </Select>
            {state.conversationListError && (
              <div role="alert">
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
              <ul className="conversation-list" aria-label="Conversations">
                {rows.map(conversationRow)}
              </ul>
            )}
            {(state.conversations.length > PREVIEW_COUNT ||
              state.hasMoreConversations) && (
              <Button
                className="nav-more"
                variant="ghost"
                aria-expanded={expanded}
                onClick={() => setExpanded((show) => !show)}
              >
                {expanded ? 'Show less' : 'Show more'}
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
            {expanded && state.conversations.length > 100 && (
              <div className="button-row">
                <Button
                  disabled={!page}
                  onClick={() => setPage((value) => value - 1)}
                >
                  Previous rows
                </Button>
                <Button
                  disabled={(page + 1) * 100 >= state.conversations.length}
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
      </div>
      <div className="nav-footer">
        {onPreferences && (
          <Button variant="ghost" onClick={onPreferences}>
            <Settings size={17} aria-hidden />
            Preferences
          </Button>
        )}
        <a href="/" className="button ghost">
          Current application
        </a>
        <Link
          className="button ghost"
          to="/primitives"
          onClick={() => overlay.close()}
        >
          Component gallery
        </Link>
      </div>
    </nav>
  );
}
