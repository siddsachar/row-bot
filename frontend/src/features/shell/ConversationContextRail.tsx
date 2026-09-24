import { useEffect, useState, type ReactNode } from 'react';
import type {
  ClientPanelSuggestion,
  ClientStatus,
  ResourceView,
} from '../../api/types';
import { useRuntime } from '../../runtime';
import { Button, Menu, Skeleton } from '../../ui/primitives';

type ResourceSummary = {
  primary: string;
  secondary: string;
  state: 'ready' | 'unavailable';
};

type Props = {
  conversationId: string;
  resources: ResourceView[];
  suggestions: ClientPanelSuggestion[];
  ready: boolean;
  connectionStatus: ClientStatus;
  terminalAvailable: boolean;
  agents: ReactNode;
  onAddResource: () => void;
  onOpenResource: (resource: ResourceView) => void;
  onUnbindResource: (resource: ResourceView) => void;
  onFind: () => void;
  onManageConversation: () => void;
  onManageBrowser: () => void;
  onDeleteConversation: () => void;
  onOpenTerminal: () => void;
  onOpenSuggestion: (suggestion: ClientPanelSuggestion) => void;
  onDismissSuggestion: (suggestion: ClientPanelSuggestion) => void;
};

function resourceKind(resource: ResourceView) {
  return resource.binding.kind === 'artifact' ? 'Design' : 'Developer';
}

export default function ConversationContextRail({
  conversationId,
  resources,
  suggestions,
  ready,
  connectionStatus,
  terminalAvailable,
  agents,
  onAddResource,
  onOpenResource,
  onUnbindResource,
  onFind,
  onManageConversation,
  onManageBrowser,
  onDeleteConversation,
  onOpenTerminal,
  onOpenSuggestion,
  onDismissSuggestion,
}: Props) {
  const { controller } = useRuntime();
  const [summaries, setSummaries] = useState<Record<string, ResourceSummary>>(
    {},
  );
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const request = new AbortController();
    const bounded = resources.slice(0, 20);
    setSummaries({});
    setLoading(false);
    if (!ready || !bounded.length) return () => request.abort();
    setLoading(true);
    void Promise.all(
      bounded.map(async (resource) => {
        const binding = resource.binding.binding_id;
        if (!resource.available)
          return [
            binding,
            {
              primary: 'Resource unavailable',
              secondary: 'Review or remove this saved binding.',
              state: 'unavailable',
            },
          ] as const;
        try {
          if (resource.binding.kind === 'artifact') {
            const value = await controller.artifactEditing(
              conversationId,
              binding,
              undefined,
              undefined,
              undefined,
              undefined,
              undefined,
              10,
              request.signal,
            );
            return [
              binding,
              {
                primary: `${value.mode.replaceAll('_', ' ')} · ${value.page_count} ${value.page_count === 1 ? 'page' : 'pages'}`,
                secondary: `${value.page_title || 'Untitled page'} · ${value.history_count} history ${value.history_count === 1 ? 'entry' : 'entries'}`,
                state: 'ready',
              },
            ] as const;
          }
          const value = await controller.inspector(
            conversationId,
            binding,
            false,
            request.signal,
          );
          const running = value.processes.filter(
            (process) => process.status === 'running',
          ).length;
          return [
            binding,
            {
              primary: value.is_git
                ? `${value.branch || 'detached'} · ${value.changed_total} changed`
                : `${value.status} workspace`,
              secondary: `${running} running · ${value.todos.length} ${value.todos.length === 1 ? 'todo' : 'todos'}`,
              state: value.status === 'unavailable' ? 'unavailable' : 'ready',
            },
          ] as const;
        } catch {
          return [
            binding,
            {
              primary: 'Summary unavailable',
              secondary: 'Open the detail panel to retry.',
              state: 'unavailable',
            },
          ] as const;
        }
      }),
    )
      .then((rows) => {
        if (!request.signal.aborted) setSummaries(Object.fromEntries(rows));
      })
      .finally(() => {
        if (!request.signal.aborted) setLoading(false);
      });
    return () => request.abort();
  }, [controller, conversationId, ready, resources]);

  return (
    <aside
      className="conversation-context-rail"
      aria-label="Conversation context"
    >
      <header className="context-rail-heading">
        <div>
          <span className="eyebrow">Conversation</span>
          <h2>Context</h2>
          <small role="status" className="muted">
            {ready
              ? 'Connected'
              : connectionStatus === 'loading'
                ? 'Loading context'
                : `Context ${connectionStatus}`}
          </small>
        </div>
        <Button disabled={!ready} onClick={onAddResource}>
          Add resource
        </Button>
      </header>

      <section
        className="context-rail-section"
        aria-labelledby="context-resources"
      >
        <h3 id="context-resources">Resources</h3>
        {loading && !Object.keys(summaries).length && (
          <Skeleton label="Loading resource summaries" />
        )}
        {!resources.length && (
          <p className="muted">No coding workspace or design is bound yet.</p>
        )}
        <ul className="context-resource-list">
          {resources.slice(0, 20).map((resource) => {
            const summary = summaries[resource.binding.binding_id];
            return (
              <li
                key={resource.binding.binding_id}
                data-kind={resource.binding.kind}
              >
                <div className="context-resource-title">
                  <Button
                    variant="ghost"
                    onClick={() => onOpenResource(resource)}
                  >
                    <span>{resource.title}</span>
                    <small>{resourceKind(resource)}</small>
                  </Button>
                  <Menu
                    label={`Actions for ${resource.title}`}
                    actions={[
                      {
                        label: 'Unbind resource',
                        onSelect: () => onUnbindResource(resource),
                      },
                    ]}
                  />
                </div>
                {summary && (
                  <p
                    className="context-resource-summary"
                    data-state={summary.state}
                  >
                    <span>{summary.primary}</span>
                    <small>{summary.secondary}</small>
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      </section>

      {!!suggestions.length && (
        <section
          className="context-rail-section"
          aria-labelledby="context-suggestions"
        >
          <h3 id="context-suggestions">Suggested panels</h3>
          {suggestions.slice(0, 10).map((suggestion) => (
            <div
              className="context-suggestion"
              key={`${suggestion.descriptor.panel_kind}:${suggestion.descriptor.resource_ref ?? ''}`}
            >
              <span>{suggestion.descriptor.title}</span>
              <div className="button-row">
                <Button onClick={() => onOpenSuggestion(suggestion)}>
                  Open
                </Button>
                <Button
                  variant="ghost"
                  onClick={() => onDismissSuggestion(suggestion)}
                >
                  Dismiss
                </Button>
              </div>
            </div>
          ))}
        </section>
      )}

      <section
        className="context-rail-section context-agents"
        aria-labelledby="context-agents"
      >
        <h3 id="context-agents">Agents</h3>
        {agents}
      </section>

      <section
        className="context-rail-section"
        aria-labelledby="context-utilities"
      >
        <h3 id="context-utilities">Utilities</h3>
        <div className="context-utility-list">
          <Button variant="ghost" onClick={onFind}>
            Find in conversation
          </Button>
          <Button variant="ghost" onClick={onManageBrowser}>
            Managed browser
          </Button>
          <Button
            variant="ghost"
            disabled={!terminalAvailable}
            title={
              terminalAvailable
                ? 'Open the trusted desktop terminal.'
                : 'The interactive terminal requires the trusted desktop window.'
            }
            onClick={onOpenTerminal}
          >
            Interactive terminal
          </Button>
          {!terminalAvailable && (
            <small className="muted">
              Terminal unavailable in this browser.
            </small>
          )}
          <Menu
            label="Conversation actions"
            actions={[
              { label: 'Manage conversation', onSelect: onManageConversation },
              { label: 'Delete conversation', onSelect: onDeleteConversation },
            ]}
          />
        </div>
      </section>
    </aside>
  );
}
