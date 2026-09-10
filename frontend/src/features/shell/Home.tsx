import { useCallback, useEffect, useState } from 'react';
import {
  FolderOpen,
  MessageSquare,
  Plus,
  Presentation,
  Search,
} from 'lucide-react';
import type {
  ConversationPage,
  ResourceChoice,
  ResourceChoicePage,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useClientState, useRuntime } from '../../runtime';
import { Button, Hint, Skeleton } from '../../ui/primitives';

export type HomeSetupEntry = {
  kind: 'artifact' | 'workspace';
  mode: 'create' | 'existing';
  resource?: ResourceChoice;
};

const RECENT_COUNT = 6;

/** Bounded Home reads never change the sidebar filter or active conversation. */
function useHomePage<T>(
  identity: string | null,
  load: (signal: AbortSignal) => Promise<T>,
) {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{
    identity: string;
    attempt: number;
    data?: T;
    error?: string;
  } | null>(null);
  useEffect(() => {
    if (!identity) return;
    const abort = new AbortController();
    void load(abort.signal).then(
      (data) => {
        if (!abort.signal.aborted) setResult({ identity, attempt, data });
      },
      (cause) => {
        if (!abort.signal.aborted)
          setResult({ identity, attempt, error: clientError(cause).message });
      },
    );
    return () => abort.abort();
  }, [identity, attempt, load]);
  const current =
    result?.identity === identity && result?.attempt === attempt
      ? result
      : null;
  return {
    data: current?.data,
    error: current?.error,
    loading: Boolean(identity && !current),
    retry: () => setAttempt((value) => value + 1),
  };
}

function ResourceLibrary({
  kind,
  identity,
  onSetup,
}: {
  kind: HomeSetupEntry['kind'];
  identity: string | null;
  onSetup: (entry: HomeSetupEntry) => void;
}) {
  const { controller } = useRuntime();
  const load = useCallback(
    (signal: AbortSignal) => controller.library(kind, undefined, signal),
    [controller, kind],
  );
  const page = useHomePage<ResourceChoicePage>(identity, load);
  const deck = kind === 'artifact';
  const title = deck ? 'Designer' : 'Developer';
  const noun = deck ? 'Decks' : 'workspaces';
  const Icon = deck ? Presentation : FolderOpen;
  return (
    <section className="home-section" aria-label={`${title} library`}>
      <header className="home-section-header">
        <div className="home-section-title">
          <Icon size={20} aria-hidden />
          <h2>{title}</h2>
        </div>
        <Button
          variant="primary"
          disabled={!identity}
          onClick={() => onSetup({ kind, mode: 'create' })}
        >
          <Plus size={16} aria-hidden />
          {deck ? 'Create Deck' : 'Open folder'}
        </Button>
      </header>
      <p className="muted">
        {deck
          ? 'Create a Deck or continue a saved design in its conversation.'
          : 'Register an existing folder or return to a saved workspace.'}
      </p>
      {page.loading && <Skeleton label={`Loading ${noun}`} />}
      {page.error && (
        <div role="alert" className="home-library-error">
          <p>{page.error}</p>
          <Button onClick={page.retry}>Retry {noun}</Button>
        </div>
      )}
      {page.data &&
        (page.data.items.length ? (
          <ul className="home-resource-list">
            {page.data.items.slice(0, RECENT_COUNT).map((resource) => (
              <li key={resource.resource_id} className="home-resource-card">
                <Hint label={`${resource.name} · ${resource.resource_id}`}>
                  <Button
                    variant="ghost"
                    disabled={!resource.available || !identity}
                    aria-label={`Open ${resource.name}`}
                    onClick={() =>
                      onSetup({ kind, mode: 'existing', resource })
                    }
                  >
                    <Icon size={18} aria-hidden />
                    <span className="home-item-copy">
                      <span className="home-item-title">{resource.name}</span>
                      <small>
                        {!resource.available
                          ? 'Unavailable'
                          : resource.origin_status === 'repair_required'
                            ? 'Review missing conversation'
                            : resource.origin_status === 'unassociated'
                              ? 'Start its conversation'
                              : 'Continue conversation'}
                      </small>
                    </span>
                  </Button>
                </Hint>
                <details className="home-resource-identity">
                  <summary>
                    {deck ? 'Deck details' : 'Workspace details'}
                  </summary>
                  <small>
                    {deck ? 'Deck' : 'Saved workspace'} ID:{' '}
                    {resource.resource_id}
                  </small>
                </details>
              </li>
            ))}
          </ul>
        ) : (
          <p className="home-empty muted">No saved {noun} yet.</p>
        ))}
      <Button
        className="home-library-link"
        variant="ghost"
        disabled={!identity}
        onClick={() => onSetup({ kind, mode: 'existing' })}
      >
        Browse all {noun}
      </Button>
    </section>
  );
}

export default function Home({
  onNewChat,
  creatingChat = false,
  onSetup,
  onOpenConversation,
  onSearch,
}: {
  onNewChat: () => void;
  creatingChat?: boolean;
  onSetup: (entry: HomeSetupEntry) => void;
  onOpenConversation: (id: string) => void;
  onSearch: () => void;
}) {
  const state = useClientState();
  const { controller } = useRuntime();
  const identity =
    state.status === 'ready' && state.handshake
      ? `${state.handshake.instance_id}:${state.handshake.client_session_id}`
      : null;
  const load = useCallback(
    (signal: AbortSignal) => controller.recentConversations(signal),
    [controller],
  );
  const recent = useHomePage<ConversationPage>(identity, load);
  return (
    <div className="home-view">
      <header className="home-header">
        <div>
          <h1>Home</h1>
          <p className="muted">
            Pick up a conversation or start something new.
          </p>
        </div>
        <Button
          variant="primary"
          disabled={!identity || creatingChat}
          onClick={onNewChat}
        >
          <Plus size={18} aria-hidden />
          {creatingChat ? 'Creating…' : 'New chat'}
        </Button>
      </header>
      {identity ? (
        <p role="status" className="home-connection-status">
          Connected
        </p>
      ) : (
        <p role="status" className="home-connection-status">
          {state.status === 'loading' || state.status === 'reconnecting'
            ? 'Connecting to your library…'
            : 'Connect to open your saved conversations and resources.'}
        </p>
      )}
      <section
        className="home-section home-recent"
        aria-label="Recent conversations"
      >
        <header className="home-section-header">
          <div className="home-section-title">
            <MessageSquare size={20} aria-hidden />
            <h2>Recent conversations</h2>
          </div>
          <Button variant="ghost" disabled={!identity} onClick={onSearch}>
            <Search size={16} aria-hidden />
            Search all conversations
          </Button>
        </header>
        {recent.loading && <Skeleton label="Loading recent conversations" />}
        {recent.error && (
          <div role="alert" className="home-library-error">
            <p>{recent.error}</p>
            <Button onClick={recent.retry}>Retry recent conversations</Button>
          </div>
        )}
        {recent.data &&
          (recent.data.items.length ? (
            <ul className="home-conversation-list">
              {recent.data.items.slice(0, RECENT_COUNT).map((conversation) => {
                const title = conversation.title || 'Untitled conversation';
                return (
                  <li key={conversation.id}>
                    <Hint label={title}>
                      <Button
                        variant="ghost"
                        aria-label={`Open ${title}`}
                        onClick={() => onOpenConversation(conversation.id)}
                      >
                        <MessageSquare size={17} aria-hidden />
                        <span className="home-item-title">{title}</span>
                        {conversation.pinned && (
                          <span aria-label="Pinned">★</span>
                        )}
                      </Button>
                    </Hint>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="home-empty muted">
              Your conversations will appear here.
            </p>
          ))}
      </section>
      <div className="home-libraries">
        <ResourceLibrary
          kind="artifact"
          identity={identity}
          onSetup={onSetup}
        />
        <ResourceLibrary
          kind="workspace"
          identity={identity}
          onSetup={onSetup}
        />
      </div>
      <p className="home-compatibility muted">
        Workflows, Knowledge and Monitor are available in the current app.{' '}
        <a href="/">Open in current app</a>
      </p>
    </div>
  );
}
