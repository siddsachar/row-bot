import { useEffect, useState, type ReactNode } from 'react';
import type {
  ClientPanelSuggestion,
  ClientStatus,
  ResourceView,
} from '../../api/types';
import { useRuntime } from '../../runtime';
import { clientError } from '../../api/errors';
import { Button, Menu, Skeleton } from '../../ui/primitives';
import {
  Code2,
  FileImage,
  FolderPlus,
  MoreHorizontal,
  Palette,
  Search,
  Terminal,
} from 'lucide-react';
import { MediaPreview } from './MediaPreview';

type ResourceSummary = {
  primary: string;
  secondary: string;
  state: 'ready' | 'unavailable';
};

type Props = {
  conversationId: string;
  conversationRevision: string;
  resources: ResourceView[];
  suggestions: ClientPanelSuggestion[];
  ready: boolean;
  connectionStatus: ClientStatus;
  terminalAvailable: boolean;
  compactHeading?: boolean;
  agents: ReactNode;
  outputs?: { id: string; reference: string; mime: string }[];
  completedDesignId?: string;
  writerQueued?: boolean;
  onCancelWait?: () => void;
  onUseOutputInCode?: (output: { reference: string; mime: string }) => void;
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
  conversationRevision,
  resources,
  suggestions,
  ready,
  connectionStatus,
  terminalAvailable,
  compactHeading = false,
  agents,
  outputs = [],
  completedDesignId,
  writerQueued = false,
  onCancelWait,
  onUseOutputInCode,
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
  const { controller, artifactDesignSessions } = useRuntime();
  const [summaries, setSummaries] = useState<Record<string, ResourceSummary>>(
    {},
  );
  const [loading, setLoading] = useState(false);
  const [outputBusy, setOutputBusy] = useState('');
  const [outputError, setOutputError] = useState('');
  const [savedOutputs, setSavedOutputs] = useState<Record<string, string>>({});
  const [writerStatus, setWriterStatus] = useState('');
  const hasWorkspace = resources.some(
    (resource) => resource.binding.kind === 'workspace',
  );
  useEffect(() => {
    setWriterStatus('');
    if (!hasWorkspace) return;
    let disposed = false;
    const poll = () => {
      void controller
        .workspaceFor(conversationId)
        .then((workspace) => {
          if (!disposed) setWriterStatus(workspace.writer_status ?? '');
        })
        .catch(() => {});
    };
    poll();
    const timer = window.setInterval(poll, 2000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [controller, conversationId, hasWorkspace]);

  async function saveOutput(output: { reference: string }) {
    if (outputBusy) return;
    setOutputBusy(output.reference);
    setOutputError('');
    try {
      const receipt = await controller.intent(
        conversationId,
        'media.save',
        { media_ref: output.reference },
        conversationRevision,
      );
      if (receipt.status !== 'completed' || !receipt.saved_name)
        throw new Error('media_save_uncertain');
      setSavedOutputs((current) => ({
        ...current,
        [output.reference]: receipt.saved_name!,
      }));
    } catch (cause) {
      setOutputError(clientError(cause).message);
    } finally {
      setOutputBusy('');
    }
  }

  async function addOutputToDesign(
    output: { reference: string; mime: string },
    design: ResourceView,
  ) {
    if (!artifactDesignSessions || outputBusy || !design.available) return;
    setOutputBusy(output.reference);
    setOutputError('');
    try {
      const blob = await controller.download(output.reference);
      if (blob.size > 25 * 1024 * 1024 || blob.type !== output.mime)
        throw new Error('media_identity_conflict');
      const extension =
        output.mime === 'image/jpeg' ? 'jpg' : output.mime.split('/')[1];
      const filename = `generated-${output.reference.split(':').at(-1)}.${extension}`;
      const file = new File([blob], filename, { type: output.mime });
      const session = artifactDesignSessions.get(conversationId, design);
      const result = await session.upload(file, design.resource_revision);
      if (result.status === 'partial')
        throw new Error('media_import_needs_review');
    } catch (cause) {
      setOutputError(clientError(cause).message);
    } finally {
      setOutputBusy('');
    }
  }

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
          <h2 className={compactHeading ? 'visually-hidden' : undefined}>
            Context
          </h2>
          {!ready && (
            <small role="status" className="muted">
              {connectionStatus === 'loading' || connectionStatus === 'ready'
                ? 'Loading context'
                : `Context ${connectionStatus}`}
            </small>
          )}
        </div>
        <div className="context-rail-actions">
          <Menu
            label="Conversation actions"
            iconOnly
            variant="ghost"
            hint="Manage or delete this conversation"
            actions={[
              { label: 'Manage conversation', onSelect: onManageConversation },
              { label: 'Manage browser', onSelect: onManageBrowser },
              { label: 'Delete conversation', onSelect: onDeleteConversation },
            ]}
          >
            <MoreHorizontal size={18} aria-hidden />
          </Menu>
          <Button
            iconOnly
            variant="ghost"
            aria-label="Add resource"
            title="Add resource"
            disabled={!ready}
            onClick={onAddResource}
          >
            <FolderPlus size={18} aria-hidden />
          </Button>
        </div>
      </header>

      {(writerQueued || writerStatus === 'queued') && (
        <p className="context-writer-status" role="status">
          Checkout busy · Waiting for the other coding run
          <Button variant="ghost" onClick={onCancelWait}>
            Cancel wait
          </Button>
        </p>
      )}

      <section
        className="context-rail-section"
        aria-labelledby="context-resources"
      >
        <h3 id="context-resources">Working on</h3>
        {loading && !Object.keys(summaries).length && (
          <Skeleton label="Loading resource summaries" />
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
                    title={`Open ${resource.title}`}
                    onClick={() => onOpenResource(resource)}
                  >
                    {resource.binding.kind === 'artifact' ? (
                      <Palette size={16} aria-hidden />
                    ) : (
                      <Code2 size={16} aria-hidden />
                    )}
                    <span>{resource.title}</span>
                    <small>{resourceKind(resource)}</small>
                  </Button>
                  {completedDesignId === resource.binding.binding_id && (
                    <small className="context-completed-badge">Completed</small>
                  )}
                  <Menu
                    label={`Actions for ${resource.title}`}
                    iconOnly
                    variant="ghost"
                    actions={[
                      {
                        label: 'Unbind resource',
                        onSelect: () => onUnbindResource(resource),
                      },
                    ]}
                  >
                    <MoreHorizontal size={16} aria-hidden />
                  </Menu>
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

      {(!!suggestions.length || !!outputs.length) && (
        <section
          className="context-rail-section"
          aria-labelledby="context-outputs"
        >
          <h3 id="context-outputs">Outputs</h3>
          {outputs.slice(-20).map((output) => (
            <details className="context-output" key={output.id}>
              <summary>
                <FileImage size={16} aria-hidden />{' '}
                {output.mime.startsWith('video/') ? 'Video' : 'Image'} output
              </summary>
              <MediaPreview reference={output.reference} mime={output.mime} />
              <div className="button-row">
                <Button
                  variant="ghost"
                  disabled={Boolean(outputBusy)}
                  onClick={() => void saveOutput(output)}
                >
                  Save to workspace
                </Button>
                {onUseOutputInCode &&
                  resources.some(
                    (resource) =>
                      resource.binding.kind === 'workspace' &&
                      resource.available,
                  ) && (
                    <Button
                      variant="ghost"
                      onClick={() => onUseOutputInCode(output)}
                    >
                      Use in code folder
                    </Button>
                  )}
              </div>
              {savedOutputs[output.reference] && (
                <small role="status">
                  Saved outputs/{savedOutputs[output.reference]}
                </small>
              )}
              {resources.filter(
                (resource) =>
                  resource.binding.kind === 'artifact' && resource.available,
              ).length === 1 && (
                <Button
                  variant="ghost"
                  disabled={Boolean(outputBusy)}
                  onClick={() =>
                    void addOutputToDesign(
                      output,
                      resources.find(
                        (resource) =>
                          resource.binding.kind === 'artifact' &&
                          resource.available,
                      )!,
                    )
                  }
                >
                  Add to design
                </Button>
              )}
              {resources.filter(
                (resource) =>
                  resource.binding.kind === 'artifact' && resource.available,
              ).length > 1 && (
                <Menu
                  label="Add output to design"
                  actions={resources
                    .filter(
                      (resource) =>
                        resource.binding.kind === 'artifact' &&
                        resource.available,
                    )
                    .map((resource) => ({
                      label: resource.title,
                      onSelect: () => void addOutputToDesign(output, resource),
                    }))}
                />
              )}
            </details>
          ))}
          {outputError && <p role="alert">{outputError}</p>}
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

      <details className="context-rail-section context-agents">
        <summary>Agents</summary>
        {agents}
      </details>

      <details className="context-rail-section context-utilities">
        <summary>Utilities</summary>
        <div className="context-utility-list">
          <Button variant="ghost" onClick={onFind} title="Find in conversation">
            <Search size={16} aria-hidden /> Find in conversation
          </Button>
          {terminalAvailable && (
            <Button
              variant="ghost"
              title="Open the trusted desktop terminal."
              onClick={onOpenTerminal}
            >
              <Terminal size={16} aria-hidden /> Interactive terminal
            </Button>
          )}
        </div>
      </details>
    </aside>
  );
}
