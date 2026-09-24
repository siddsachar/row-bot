import { memo, useEffect, useMemo, useState } from 'react';
import { useClientSelector, useRuntime } from '../../runtime';
import { Button, EmptyState, Skeleton } from '../../ui/primitives';
import WorkspaceProcesses from './WorkspaceProcesses';
import WorkspaceImports from './WorkspaceImports';
import WorkspaceUndo from './WorkspaceUndo';
import type { PanelInstance } from './model';
import { validResourcePanelInstance } from './presentation';
import ArtifactPreview from './ArtifactPreview';
import { WorkspaceInspector } from './WorkspaceInspector';
import { artifactEdits } from './artifact-edits';
import { artifactExports } from './artifact-exports';
import { artifactSharing } from './artifact-sharing';
import { workspaceEdits } from './workspace-edits';
import type { ArtifactAuthoring } from '../../api/types';
import type { ArtifactEditingOptions } from './ArtifactEditor';
import type { ArtifactDesignSession } from './artifact-design-sessions';
import type { ClientController } from '../../api/controller';
import type { DeveloperRepositoryReviewRequest } from '../../api/types';
import DeveloperRepositoryPanel, {
  createDeveloperRepositorySession,
} from '../developer/DeveloperRepositoryPanel';
import CustomToolBuilder from '../developer/CustomToolBuilder';

export const resourcePanelMetrics = { mounted: 0, renders: 0 };
const Preview = memo(ArtifactPreview);
const Inspector = memo(WorkspaceInspector);

function RepositorySurface({
  controller,
  conversation,
  binding,
  resourceId,
  bindingRevision,
  visible,
}: {
  controller: ClientController;
  conversation: string;
  binding: string;
  resourceId: string;
  bindingRevision: string;
  visible: boolean;
}) {
  const scope = `${conversation}:${resourceId}:${binding}:${bindingRevision}`;
  const session = useMemo(
    () => createDeveloperRepositorySession(scope),
    [scope],
  );
  useEffect(() => () => session.dispose(), [session]);
  return (
    <DeveloperRepositoryPanel
      scope={scope}
      visible={visible}
      session={session}
      load={(signal) =>
        controller.developerRepository(conversation, binding, signal)
      }
      review={(action, payload, signal) =>
        controller.reviewDeveloperRepository(
          conversation,
          binding,
          action,
          payload as DeveloperRepositoryReviewRequest['payload'],
          signal,
        )
      }
      execute={(command, review) => {
        if (!review.review_id) throw new Error('review_required');
        return controller.executeDeveloperRepository(conversation, binding, {
          ...command,
          payload: { ...command.payload, nonce: review.review_id },
        });
      }}
    />
  );
}

function ResourcePanel({
  panel,
  visible,
}: {
  panel: PanelInstance;
  visible: boolean;
}) {
  const {
    controller,
    workspaceEditSessions,
    workspaceProcessSessions,
    workspaceImportSessions,
    workspaceUndoSessions,
    artifactDesignSessions,
  } = useRuntime();
  const [processesOpen, setProcessesOpen] = useState(false);
  const [importsOpen, setImportsOpen] = useState(false);
  const [repositoryOpen, setRepositoryOpen] = useState(true);
  const [customToolsOpen, setCustomToolsOpen] = useState(false);
  const [importRevision, setImportRevision] = useState(0);
  const [undoSelection, setUndoSelection] = useState<{
    binding: string;
    changeSet: string;
  } | null>(null);
  const workspace = useClientSelector((state) => state.workspace);
  const selected = useClientSelector((state) => state.selectedConversationId);
  const loading = useClientSelector((state) => state.loadingConversation);
  if (import.meta.env.VITE_ENABLE_FIXTURES === '1')
    resourcePanelMetrics.renders++;
  useEffect(() => {
    if (import.meta.env.VITE_ENABLE_FIXTURES !== '1') return;
    resourcePanelMetrics.mounted++;
    return () => {
      resourcePanelMetrics.mounted--;
    };
  }, []);
  const reference = panel.descriptor.resource_ref ?? '';
  const split = reference.lastIndexOf(':');
  const conversation = reference.slice(0, split),
    binding = reference.slice(split + 1);
  const editSessions = useMemo(
    () => workspaceEditSessions?.forBinding(conversation, binding),
    [workspaceEditSessions, conversation, binding],
  );
  const api = useMemo(
    () => ({
      preview: (
        page?: string,
        revision?: string,
        signal?: AbortSignal,
        authoring?: ArtifactAuthoring,
      ) =>
        controller.artifactPreview(
          conversation,
          binding,
          page,
          revision,
          signal,
          authoring,
        ),
      editing: (options: ArtifactEditingOptions, signal: AbortSignal) =>
        controller.artifactEditing(
          conversation,
          binding,
          options.pageId,
          options.pageCursor,
          options.elementCursor,
          options.historyCursor,
          options.elementId,
          options.limit,
          signal,
        ),
      palette: (revision: string, query: string, signal: AbortSignal) =>
        controller.artifactPalette(
          conversation,
          binding,
          revision,
          query,
          signal,
        ),
      edit: artifactEdits(controller, conversation, binding),
      exports: artifactExports(controller, conversation, binding),
      sharing: {
        ...artifactSharing(controller, conversation, binding),
        loadChannels: controller.artifactShareChannels,
      },
      presentation: {
        load: (
          options: import('../../api/types').DesignPresentationOptions,
          signal: AbortSignal,
        ) =>
          controller.designPresentation(conversation, binding, options, signal),
        preview: (page: string, signal: AbortSignal) =>
          controller.artifactStaticPreview(conversation, binding, page, signal),
      },
      lifecycle:
        typeof controller.artifactLifecycle === 'function'
          ? {
              load: (
                _resourceId: string,
                resourceRevision: string,
                signal: AbortSignal,
              ) =>
                controller.artifactLifecycle(
                  conversation,
                  binding,
                  resourceRevision,
                  signal,
                ),
            }
          : undefined,
      editableFile: (path: string, signal?: AbortSignal) =>
        controller.workspaceEditableFile(conversation, binding, path, signal),
      saveFile: workspaceEdits(controller, conversation, binding),
      inspector: (refresh?: boolean, signal?: AbortSignal) =>
        controller.inspector(conversation, binding, refresh, signal),
      changes: (revision: string, cursor?: string, signal?: AbortSignal) =>
        controller.changes(conversation, binding, revision, cursor, signal),
      directory: (
        path: string,
        cursor?: string,
        revision?: string,
        signal?: AbortSignal,
      ) =>
        controller.directory(
          conversation,
          binding,
          path,
          cursor,
          revision,
          signal,
        ),
      file: (
        path: string,
        offset?: number,
        revision?: string,
        signal?: AbortSignal,
      ) =>
        controller.file(conversation, binding, path, offset, revision, signal),
      diff: (
        path: string,
        snapshot: string,
        offset?: number,
        revision?: string,
        signal?: AbortSignal,
      ) =>
        controller.diff(
          conversation,
          binding,
          path,
          snapshot,
          offset,
          revision,
          signal,
        ),
      changeSets: (revision: string, cursor?: string, signal?: AbortSignal) =>
        controller.changeSets(conversation, binding, revision, cursor, signal),
      changeSetFiles: (
        change: string,
        revision: string,
        cursor?: string,
        signal?: AbortSignal,
      ) =>
        controller.changeSetFiles(
          conversation,
          binding,
          change,
          revision,
          cursor,
          signal,
        ),
    }),
    [controller, conversation, binding],
  );
  if (loading) return <Skeleton label="Opening resource" />;
  const resource = workspace?.resources.find(
    (r) => r.resource_ref === reference,
  );
  // Workspace authority arrives before the layout's descriptor reconciliation.
  // Validate its current revision against the panel's stable identity in this
  // render so a revision-only refresh does not destroy the mounted preview.
  const currentPanel =
    resource && panel.presentationKey
      ? {
          ...panel,
          descriptor: {
            ...panel.descriptor,
            resource_revision: resource.resource_revision,
          },
        }
      : panel;
  if (
    selected !== conversation ||
    !resource?.available ||
    !validResourcePanelInstance(
      currentPanel,
      conversation,
      workspace?.resources ?? [],
    )
  )
    return (
      <EmptyState title="Resource unavailable">
        Open its conversation or review the current binding. Closing this panel
        does not delete the resource.
      </EmptyState>
    );
  const processSession =
    resource.binding.kind === 'workspace'
      ? workspaceProcessSessions?.forResource(conversation, resource)
      : null;
  const importSession =
    resource.binding.kind === 'workspace'
      ? workspaceImportSessions?.forResource(conversation, resource)
      : null;
  const undoSession =
    resource.binding.kind === 'workspace'
      ? undoSelection?.binding === binding
        ? workspaceUndoSessions?.forChangeSet(
            conversation,
            resource,
            undoSelection.changeSet,
          )
        : workspaceUndoSessions?.retainedForResource(conversation, resource)
      : null;
  let designSession: ArtifactDesignSession | undefined;
  if (resource.binding.kind === 'artifact' && artifactDesignSessions) {
    try {
      designSession = artifactDesignSessions.get(conversation, resource);
    } catch {
      /* Keep preview available if a retained editing session needs review. */
    }
  }
  const draftDesignText = (text: string) => {
    designSession?.guard();
    if (controller.getSnapshot().selectedConversationId !== conversation)
      throw new Error('resource_binding_revoked');
    const draft = controller.getDraft(conversation);
    const combined = [draft.text, text].filter(Boolean).join('\n\n');
    if (combined.length > 200000) throw new Error('draft_full');
    controller.setDraft(conversation, { ...draft, text: combined });
  };
  return panel.descriptor.panel_kind === 'artifact.preview' ? (
    <Preview
      resourceId={resource.binding.resource_id}
      resourceRevision={resource.resource_revision}
      visible={visible}
      load={api.preview}
      loadEditing={api.editing}
      loadPalette={api.palette}
      onDraftText={designSession ? draftDesignText : undefined}
      edit={api.edit}
      createExport={api.exports.create}
      downloadExport={api.exports.download}
      sharing={api.sharing}
      presentation={api.presentation}
      lifecycle={api.lifecycle}
      design={
        designSession
          ? {
              session: designSession,
              onDraftText: draftDesignText,
            }
          : undefined
      }
    />
  ) : (
    <div className="stack resource-panel">
      {typeof controller.developerRepository === 'function' && (
        <>
          <Button
            aria-expanded={repositoryOpen}
            onClick={() => setRepositoryOpen((value) => !value)}
          >
            {repositoryOpen
              ? 'Hide repository controls'
              : 'Repository controls'}
          </Button>
          <div hidden={!repositoryOpen}>
            <RepositorySurface
              controller={controller}
              conversation={conversation}
              binding={binding}
              resourceId={resource.binding.resource_id}
              bindingRevision={resource.binding.revision}
              visible={visible && repositoryOpen}
            />
          </div>
        </>
      )}
      {typeof controller.customTools === 'function' && (
        <>
          <Button
            aria-expanded={customToolsOpen}
            onClick={() => setCustomToolsOpen((value) => !value)}
          >
            {customToolsOpen
              ? 'Hide Custom Tool Builder'
              : 'Custom Tool Builder'}
          </Button>
          {customToolsOpen && (
            <CustomToolBuilder
              controller={controller}
              conversation={conversation}
              binding={binding}
              visible={visible}
            />
          )}
        </>
      )}
      <Button
        aria-expanded={importsOpen}
        onClick={() => setImportsOpen((value) => !value)}
      >
        {importsOpen ? 'Hide sandbox changes' : 'Sandbox changes'}
      </Button>
      {importsOpen && !importSession && (
        <p role="status">
          Finish retained imports before opening another workspace.
        </p>
      )}
      {importsOpen && importSession && (
        <WorkspaceImports
          {...importSession.api}
          session={importSession.session}
          onImported={() => setImportRevision((value) => value + 1)}
        />
      )}
      <Button
        aria-expanded={processesOpen}
        onClick={() => setProcessesOpen((value) => !value)}
      >
        {processesOpen ? 'Hide processes' : 'Processes'}
      </Button>
      {processesOpen && !processSession && (
        <p role="status">
          Process sessions are unavailable or full. Finish retained sessions
          before opening another workspace.
        </p>
      )}
      {processSession && (
        <div hidden={!processesOpen}>
          <WorkspaceProcesses
            {...processSession.api}
            scope={processSession.scope}
            session={processSession.session}
            resourceRevision={resource.resource_revision}
            visible={visible && processesOpen}
          />
        </div>
      )}
      {undoSession && (
        <WorkspaceUndo
          {...undoSession.api}
          session={undoSession.session}
          onUndone={() => setImportRevision((value) => value + 1)}
        />
      )}
      {undoSelection?.binding === binding && !undoSession && (
        <p role="status">
          Finish retained Undo reviews before opening another change set.
        </p>
      )}
      <Inspector
        onUndo={
          workspaceUndoSessions
            ? (changeSet) => setUndoSelection({ binding, changeSet })
            : undefined
        }
        resourceId={resource.binding.resource_id}
        refreshToken={importRevision}
        resourceRevision={resource.resource_revision}
        visible={visible}
        load={api.inspector}
        changes={api.changes}
        directory={api.directory}
        file={api.file}
        editableFile={api.editableFile}
        saveFile={api.saveFile}
        editSessions={editSessions}
        diff={api.diff}
        changeSets={api.changeSets}
        changeSetFiles={api.changeSetFiles}
      />
    </div>
  );
}
export default memo(ResourcePanel);
