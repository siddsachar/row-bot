import { memo, useEffect, useMemo } from 'react';
import { useClientSelector, useRuntime } from '../../runtime';
import { EmptyState, Skeleton } from '../../ui/primitives';
import type { PanelInstance } from './model';
import { validResourcePanelInstance } from './presentation';
import ArtifactPreview from './ArtifactPreview';
import { WorkspaceInspector } from './WorkspaceInspector';

export const resourcePanelMetrics = { mounted: 0, renders: 0 };
const Preview = memo(ArtifactPreview);
const Inspector = memo(WorkspaceInspector);

function ResourcePanel({
  panel,
  visible,
}: {
  panel: PanelInstance;
  visible: boolean;
}) {
  const { controller } = useRuntime();
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
  const api = useMemo(
    () => ({
      preview: (page?: string, revision?: string, signal?: AbortSignal) =>
        controller.artifactPreview(
          conversation,
          binding,
          page,
          revision,
          signal,
        ),
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
  if (
    selected !== conversation ||
    !resource?.available ||
    !validResourcePanelInstance(panel, conversation, workspace?.resources ?? [])
  )
    return (
      <EmptyState title="Resource unavailable">
        Open its conversation or review the current binding. Closing this panel
        does not delete the resource.
      </EmptyState>
    );
  return panel.descriptor.panel_kind === 'artifact.preview' ? (
    <Preview
      resourceId={resource.binding.resource_id}
      resourceRevision={resource.resource_revision}
      visible={visible}
      load={api.preview}
    />
  ) : (
    <Inspector
      resourceId={resource.binding.resource_id}
      resourceRevision={resource.resource_revision}
      visible={visible}
      load={api.inspector}
      changes={api.changes}
      directory={api.directory}
      file={api.file}
      diff={api.diff}
      changeSets={api.changeSets}
      changeSetFiles={api.changeSetFiles}
    />
  );
}
export default memo(ResourcePanel);
