import {
  memo,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import { useClientSelector, useRuntime } from '../../runtime';
import { Disclosure, EmptyState, Skeleton } from '../../ui/primitives';
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
import type { ArtifactAuthoring, ResourceView } from '../../api/types';
import type { ArtifactEditingOptions } from './ArtifactEditor';
import type { ArtifactDesignSession } from './artifact-design-sessions';
import type { ClientController } from '../../api/controller';
import type { DeveloperRepositoryReviewRequest } from '../../api/types';
import DeveloperRepositoryPanel, {
  createDeveloperRepositorySession,
  type DeveloperRepositorySession,
} from '../developer/DeveloperRepositoryPanel';
import CustomToolBuilder from '../developer/CustomToolBuilder';
import { sendPrompt } from '../shell/composer-bridge';
import { requestResourcePanel } from './panel-requests';
import { draftingKey, draftingOf } from './design-drafting';
import type { AskOutcome } from './DesignSelection';
import type { WorkspaceEditScope } from './workspace-edit-sessions';

export const resourcePanelMetrics = { mounted: 0, renders: 0 };
const Preview = memo(ArtifactPreview);
const Inspector = memo(WorkspaceInspector);
const noSubscription = () => () => {};
const noSnapshot = () => null;

type ResourceApi = ReturnType<typeof resourceApi>;

function resourceApi(
  controller: ClientController,
  conversation: string,
  binding: string,
) {
  return {
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
      loadPublication: (signal: AbortSignal) =>
        controller.artifactPublication(conversation, binding, signal),
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
    ) => controller.file(conversation, binding, path, offset, revision, signal),
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
  };
}

/**
 * The Developer inspector for one bound code folder: it owns the repository
 * session (branch list, ahead/behind) and re-reads after an agent turn ends,
 * since the agent may have changed files without a binding revision change.
 */
function WorkspaceSurface({
  controller,
  conversation,
  binding,
  resource,
  visible,
  api,
  editSessions,
}: {
  controller: ClientController;
  conversation: string;
  binding: string;
  resource: ResourceView;
  visible: boolean;
  api: ResourceApi;
  editSessions?: WorkspaceEditScope;
}) {
  const {
    workspaceProcessSessions,
    workspaceImportSessions,
    workspaceUndoSessions,
  } = useRuntime();
  const [refreshToken, setRefreshToken] = useState(0);
  const [undoSelection, setUndoSelection] = useState<{
    binding: string;
    changeSet: string;
    summary: string;
  } | null>(null);
  const settled = useClientSelector((state) => {
    const generation = state.projection?.generation;
    return state.selectedConversationId === conversation &&
      generation?.conversation_id === conversation &&
      generation.quiesced
      ? generation.generation_id
      : '';
  });
  const seenSettled = useRef(settled);
  useEffect(() => {
    if (!settled || settled === seenSettled.current) return;
    seenSettled.current = settled;
    setRefreshToken((value) => value + 1);
  }, [settled]);
  const scope = `${conversation}:${resource.binding.resource_id}:${binding}:${resource.binding.revision}`;
  const repositorySession = useMemo<DeveloperRepositorySession | null>(
    () =>
      typeof controller.developerRepository === 'function'
        ? createDeveloperRepositorySession(scope)
        : null,
    [controller, scope],
  );
  useEffect(() => () => repositorySession?.dispose(), [repositorySession]);
  const repositoryState = useSyncExternalStore(
    repositorySession?.subscribe ?? noSubscription,
    repositorySession?.getSnapshot ?? noSnapshot,
    repositorySession?.getSnapshot ?? noSnapshot,
  );
  const processSession = workspaceProcessSessions?.forResource(
    conversation,
    resource,
  );
  const processState = useSyncExternalStore(
    processSession?.session.subscribe ?? noSubscription,
    processSession?.session.getSnapshot ?? noSnapshot,
    processSession?.session.getSnapshot ?? noSnapshot,
  );
  const importSession = workspaceImportSessions?.forResource(
    conversation,
    resource,
  );
  const undoSession =
    undoSelection?.binding === binding
      ? workspaceUndoSessions?.forChangeSet(
          conversation,
          resource,
          undoSelection.changeSet,
        )
      : workspaceUndoSessions?.retainedForResource(conversation, resource);
  const pendingImports =
    repositoryState?.snapshot?.sandbox.pending_imports ?? 0;
  const bump = () => setRefreshToken((value) => value + 1);
  return (
    <div className="resource-panel dev-panel">
      <Inspector
        onUndo={
          workspaceUndoSessions
            ? (changeSet, summary) =>
                setUndoSelection({ binding, changeSet, summary })
            : undefined
        }
        resourceId={resource.binding.resource_id}
        refreshToken={refreshToken}
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
        onAsk={(text) =>
          controller.getSnapshot().selectedConversationId === conversation &&
          sendPrompt(conversation, text)
        }
        repository={repositoryState?.snapshot ?? null}
        processes={processState?.processes}
        renderRun={
          processSession
            ? ({ checks }) => (
                <WorkspaceProcesses
                  {...processSession.api}
                  scope={processSession.scope}
                  session={processSession.session}
                  resourceRevision={resource.resource_revision}
                  visible={visible}
                  checks={checks}
                />
              )
            : () => (
                <p className="dev-empty" role="status">
                  Commands are unavailable here right now. Finish or recover the
                  commands another workspace kept running first.
                </p>
              )
        }
        renderGit={
          repositorySession
            ? (git) => (
                <DeveloperRepositoryPanel
                  scope={scope}
                  visible={visible}
                  session={repositorySession}
                  changedFiles={git.changedFiles}
                  commitSuggestion={git.commitSuggestion}
                  revisionKey={`${refreshToken}:${git.revision}`}
                  onChanged={bump}
                  load={(signal) =>
                    controller.developerRepository(
                      conversation,
                      binding,
                      signal,
                    )
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
                    return controller.executeDeveloperRepository(
                      conversation,
                      binding,
                      {
                        ...command,
                        payload: {
                          ...command.payload,
                          nonce: review.review_id,
                        },
                      },
                    );
                  }}
                  advanced={
                    typeof controller.customTools === 'function' ? (
                      <section
                        className="dev-git-section"
                        aria-label="Custom tools"
                      >
                        <h4>Custom tools</h4>
                        <CustomToolBuilder
                          controller={controller}
                          conversation={conversation}
                          binding={binding}
                          visible={visible}
                        />
                      </section>
                    ) : null
                  }
                />
              )
            : undefined
        }
        undo={
          undoSession ? (
            <WorkspaceUndo
              {...undoSession.api}
              session={undoSession.session}
              summary={
                undoSelection?.binding === binding
                  ? undoSelection.summary
                  : undefined
              }
              onUndone={bump}
              onCancel={() => setUndoSelection(null)}
            />
          ) : undoSelection?.binding === binding ? (
            <p className="dev-muted-line" role="status">
              Finish the Undo review that is still open before undoing another
              change.
            </p>
          ) : null
        }
        imports={
          <Disclosure
            summary="Sandbox changes"
            meta={pendingImports ? `${pendingImports} waiting` : undefined}
            defaultOpen={pendingImports > 0}
            className="dev-disclosure"
          >
            {importSession ? (
              <WorkspaceImports
                {...importSession.api}
                session={importSession.session}
                waiting={pendingImports}
                onImported={bump}
              />
            ) : (
              <p className="dev-muted-line" role="status">
                Finish the sandbox imports another workspace kept open first.
              </p>
            )}
          </Disclosure>
        }
      />
    </div>
  );
}

function ResourcePanel({
  panel,
  visible,
}: {
  panel: PanelInstance;
  visible: boolean;
}) {
  const { controller, workspaceEditSessions, artifactDesignSessions } =
    useRuntime();
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
  // A turn working on this conversation's design: say what it does and
  // refresh the page as each step is saved (U35).
  const drafting = useClientSelector((state) =>
    panel.descriptor.panel_kind === 'artifact.preview' &&
    state.selectedConversationId === conversation
      ? draftingKey(conversation, state.projection?.generation, state.activity)
      : '',
  );
  const api = useMemo(
    () => resourceApi(controller, conversation, binding),
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
  if (panel.descriptor.panel_kind !== 'artifact.preview')
    return (
      <WorkspaceSurface
        controller={controller}
        conversation={conversation}
        binding={binding}
        resource={resource}
        visible={visible}
        api={api}
        editSessions={editSessions}
      />
    );
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
  // "Ask Row-Bot to change this…" goes through the open conversation's
  // composer; the preview falls back to the draft when it cannot send.
  const askDesign = (text: string): AskOutcome => {
    try {
      designSession?.guard();
    } catch {
      return 'unavailable';
    }
    if (controller.getSnapshot().selectedConversationId !== conversation)
      return 'unavailable';
    return sendPrompt(conversation, text) ? 'sent' : 'unavailable';
  };
  // A copy is bound beside the original and opens in its own panel.
  const duplicateDesign = async () => {
    const fresh = await controller.workspaceFor(conversation);
    const source = fresh.resources.find(
      (item) => item.binding.binding_id === binding,
    );
    if (!source?.available || source.binding.kind !== 'artifact')
      throw { code: 'resource_binding_revoked' };
    const result = await controller.intent(
      conversation,
      'resource.setup',
      {
        kind: 'artifact',
        intent: 'create',
        duplicate_of: source.binding.resource_id,
        expected_resource_revision: source.resource_revision,
      },
      fresh.revision,
    );
    if (result.status !== 'completed' || !result.binding_id)
      throw { code: result.code ?? 'setup_stage_failed' };
    requestResourcePanel({
      conversationId: conversation,
      resourceRef: `${conversation}:${result.binding_id}`,
    });
  };
  return (
    <Preview
      title={resource.title}
      drafting={draftingOf(drafting)}
      onAsk={askDesign}
      duplicate={duplicateDesign}
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
      saveExport={api.exports.save}
      revealExport={api.exports.reveal}
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
  );
}
export default memo(ResourcePanel);
