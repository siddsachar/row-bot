import { useEffect, useMemo, useRef, useState } from 'react';
import type {
  ArtifactPreview as Preview,
  ArtifactAuthoring,
} from '../../api/types';
import { Button, ErrorState, Select, Skeleton } from '../../ui/primitives';
import ArtifactEditor, { type ArtifactEditorProps } from './ArtifactEditor';
import { artifactBridgeMessage } from './artifact-bridge';
import ArtifactExports, { type ArtifactExportsProps } from './ArtifactExports';
import ArtifactSharingPanel, {
  type ArtifactSharingPanelProps,
} from './ArtifactSharingPanel';
import ArtifactPresentationPanel, {
  type ArtifactPresentationPanelProps,
} from './ArtifactPresentationPanel';
import ArtifactDesignPanel, {
  type ArtifactDesignPanelProps,
} from './ArtifactDesignPanel';
import ArtifactLifecyclePanel, {
  type ArtifactLifecyclePanelProps,
} from './ArtifactLifecyclePanel';

export type ArtifactPreviewProps = {
  resourceId: string;
  resourceRevision: string;
  visible: boolean;
  load: (
    pageId?: string,
    knownRevision?: string,
    signal?: AbortSignal,
    authoring?: ArtifactAuthoring,
  ) => Promise<Preview>;
  loadEditing?: ArtifactEditorProps['load'];
  edit?: ArtifactEditorProps['edit'];
  createExport?: ArtifactExportsProps['create'];
  downloadExport?: ArtifactExportsProps['download'];
  sharing?: Pick<
    ArtifactSharingPanelProps,
    'prepare' | 'execute' | 'loadChannels'
  >;
  presentation?: Pick<ArtifactPresentationPanelProps, 'load' | 'preview'>;
  lifecycle?: Pick<ArtifactLifecyclePanelProps, 'load'>;
  design?: Pick<ArtifactDesignPanelProps, 'session' | 'onDraftText'>;
};

function failureText(error: unknown): string {
  const code =
    typeof error === 'object' && error !== null && 'code' in error
      ? error.code
      : '';
  if (code === 'not_found' || code === 'resource_unavailable')
    return 'This design is no longer available. Your conversation is preserved.';
  if (
    code === 'action_denied' ||
    code === 'capability_revoked' ||
    code === 'resource_binding_revoked'
  )
    return 'Access to this design changed. Review its binding before continuing.';
  if (code === 'resource_revision_conflict')
    return 'This design changed while loading. Refresh to see its current version.';
  if (code === 'artifact_type_unavailable')
    return 'This design type is not supported by this client.';
  if (code === 'page_unavailable')
    return 'That page is no longer available. Reload the design to continue.';
  return 'The design is bound, but its preview could not load. Retry the preview.';
}

export default function ArtifactPreview({
  resourceId,
  resourceRevision,
  visible,
  load,
  loadEditing,
  edit,
  createExport,
  downloadExport,
  sharing,
  presentation,
  lifecycle,
  design,
}: ArtifactPreviewProps) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [selection, setSelection] = useState({
    resourceId,
    pageId: undefined as string | undefined,
  });
  const [refresh, setRefresh] = useState(0);
  const [zoom, setZoom] = useState({ resourceId, value: 'fit' });
  const zoomMode = zoom.resourceId === resourceId ? zoom.value : 'fit';
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [editorOpen, setEditorOpen] = useState(false);
  const [exportsOpen, setExportsOpen] = useState(false);
  const [sharingOpen, setSharingOpen] = useState(false);
  const [presentationOpen, setPresentationOpen] = useState(false);
  const [designOpen, setDesignOpen] = useState(false);
  const [authoring, setAuthoring] = useState(false);
  const [selectedElementId, setSelectedElementId] = useState<string>();
  const [editError, setEditError] = useState('');
  const frame = useRef<HTMLIFrameElement>(null);
  const inlineOperation = useRef(false);
  const [viewport, setViewport] = useState({ width: 400, height: 225 });
  const measured = useRef(viewport);
  const frameHost = useRef<HTMLDivElement>(null);
  const latest = useRef<Preview | null>(null);
  const loader = useRef(load);
  const request = useRef(0);
  const pageId =
    selection.resourceId === resourceId ? selection.pageId : undefined;
  const canEdit = !!edit;
  const authoringScope = useMemo(
    () => ({
      resourceId,
      resourceRevision,
      pageId,
      identity:
        authoring && canEdit
          ? {
              previewId: crypto.randomUUID(),
              capability: crypto.randomUUID(),
            }
          : undefined,
    }),
    [authoring, resourceId, resourceRevision, pageId, canEdit],
  );
  const authoringIdentity = authoringScope.identity;

  useEffect(() => {
    loader.current = load;
  }, [load]);

  useEffect(() => {
    const epoch = ++request.current;
    if (!visible) return;
    const abort = new AbortController();
    const previous = latest.current;
    const known =
      previous?.resource_id === resourceId &&
      (pageId === undefined || previous.page_id === pageId)
        ? previous.preview_revision
        : undefined;
    setLoading(true);
    setError('');
    loader.current(pageId, known, abort.signal, authoringIdentity).then(
      (result) => {
        if (abort.signal.aborted || epoch !== request.current) return;
        if (result.resource_id !== resourceId) {
          setError(
            'The preview returned a different design. Reload this design.',
          );
          setLoading(false);
          return;
        }
        const next = result.unchanged
          ? previous?.resource_id === result.resource_id &&
            previous.page_id === result.page_id &&
            previous.preview_revision === result.preview_revision
            ? { ...result, html: previous.html }
            : null
          : result;
        if (!next?.html) {
          setError(
            'The preview needs a fresh copy. Reload the design to continue.',
          );
        } else {
          latest.current = next;
          setPreview(next);
        }
        setLoading(false);
      },
      (reason: unknown) => {
        if (abort.signal.aborted || epoch !== request.current) return;
        setError(failureText(reason));
        // Revoked or missing content must not remain readable behind an error.
        latest.current = null;
        setPreview(null);
        setLoading(false);
      },
    );
    return () => abort.abort();
  }, [
    resourceId,
    resourceRevision,
    pageId,
    refresh,
    visible,
    authoringIdentity,
  ]);

  const current = preview?.resource_id === resourceId ? preview : null;
  const pageLabel = current?.mode === 'deck' || !current ? 'Slide' : 'Page';
  const interactive =
    current?.scripts_allowed === true &&
    (!!authoringIdentity ||
      ['landing', 'app_mockup', 'storyboard'].includes(current.mode));
  const editCallback = useRef(edit);
  useEffect(() => {
    editCallback.current = edit;
  }, [edit]);
  useEffect(() => {
    if (!visible || !authoringIdentity || !current || !canEdit) return;
    let active = true;
    const receive = (event: MessageEvent) => {
      const message = artifactBridgeMessage(
        event,
        frame.current?.contentWindow ?? null,
        authoringIdentity,
        current.preview_revision,
      );
      if (!message) return;
      if (message.type === 'unavailable') {
        setEditError(
          'This inline edit is too large. Use the text field in Design properties.',
        );
        return;
      }
      setSelectedElementId(message.elementId);
      setEditorOpen(true);
      if (message.type !== 'edit') return;
      if (inlineOperation.current) {
        setEditError(
          'A design edit is still saving. Review the saved version before editing again.',
        );
        return;
      }
      inlineOperation.current = true;
      setEditError('');
      void editCallback
        .current?.(
          {
            operation: 'text',
            page_id: current.page_id,
            element_id: message.elementId,
            text: message.text,
          },
          current.resource_revision,
        )
        .then(
          () => {
            if (active) setRefresh((value) => value + 1);
          },
          () => {
            if (active)
              setEditError(
                'The inline edit was not confirmed. Refresh to review the saved design.',
              );
          },
        )
        .finally(() => {
          inlineOperation.current = false;
        });
    };
    window.addEventListener('message', receive);
    return () => {
      active = false;
      window.removeEventListener('message', receive);
    };
  }, [visible, authoringIdentity, current, canEdit]);
  useEffect(() => {
    if (!visible || !frameHost.current) return;
    const element = frameHost.current;
    let active = true;
    const measure = () => {
      if (!active || frameHost.current !== element) return;
      const width = element.clientWidth,
        height = element.clientHeight;
      if (
        width <= 0 ||
        height <= 0 ||
        (width === measured.current.width && height === measured.current.height)
      )
        return;
      const next = { width, height };
      measured.current = next;
      setViewport(next);
    };
    measure();
    const observer =
      typeof ResizeObserver === 'undefined'
        ? null
        : new ResizeObserver(measure);
    observer?.observe(element);
    return () => {
      active = false;
      observer?.disconnect();
    };
  }, [visible, current?.resource_id]);

  function reload() {
    latest.current = null;
    setSelection({ resourceId, pageId: undefined });
    setRefresh((value) => value + 1);
  }

  if (!visible) return null;
  const scale = current
    ? zoomMode === 'actual'
      ? 1
      : zoomMode === 'width'
        ? viewport.width / current.canvas_width
        : Math.min(
            viewport.width / current.canvas_width,
            viewport.height / current.canvas_height,
          )
    : 1;
  return (
    <section
      aria-label="Design preview"
      aria-busy={loading}
      className="preview-surface"
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        height: '100%',
        minHeight: 0,
        padding: 8,
        boxSizing: 'border-box',
      }}
    >
      <div
        className="toolbar panel-toolbar preview-toolbar"
        role="group"
        aria-label={`${pageLabel} navigation`}
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: 4,
          flexShrink: 0,
        }}
      >
        <Button
          disabled={!current || loading || current.page_index === 0}
          onClick={() =>
            setSelection({
              resourceId,
              pageId: current?.pages[current.page_index - 1]?.id,
            })
          }
        >
          Previous {pageLabel.toLowerCase()}
        </Button>
        <Select
          aria-label={pageLabel}
          value={current?.page_id ?? ''}
          disabled={!current || loading}
          style={{
            width: 'auto',
            flex: '1 1 120px',
            minWidth: 100,
            maxWidth: '100%',
          }}
          onChange={(event) =>
            setSelection({ resourceId, pageId: event.target.value })
          }
        >
          {!current && <option value="">No page loaded</option>}
          {current?.pages.map((page) => (
            <option key={page.id} value={page.id}>
              {page.index + 1}. {page.title}
            </option>
          ))}
        </Select>
        <Button
          disabled={
            !current || loading || current.page_index >= current.page_count - 1
          }
          onClick={() =>
            setSelection({
              resourceId,
              pageId: current?.pages[current.page_index + 1]?.id,
            })
          }
        >
          Next {pageLabel.toLowerCase()}
        </Button>
        <Button
          disabled={loading}
          aria-describedby={
            loading && current ? 'design-preview-refresh-status' : undefined
          }
          onClick={() => setRefresh((value) => value + 1)}
        >
          Refresh preview
        </Button>
        {loadEditing && edit && (
          <Button
            aria-expanded={editorOpen}
            onClick={() => {
              setEditorOpen((value) => !value);
              setExportsOpen(false);
              setSharingOpen(false);
              setPresentationOpen(false);
              setDesignOpen(false);
            }}
          >
            Design properties
          </Button>
        )}
        {!lifecycle && createExport && downloadExport && (
          <Button
            aria-expanded={exportsOpen}
            onClick={() => {
              setExportsOpen((value) => !value);
              setEditorOpen(false);
              setSharingOpen(false);
              setPresentationOpen(false);
              setDesignOpen(false);
            }}
          >
            Export design
          </Button>
        )}
        {!lifecycle && sharing && (
          <Button
            aria-expanded={sharingOpen}
            onClick={() => {
              setSharingOpen((value) => !value);
              setEditorOpen(false);
              setExportsOpen(false);
              setPresentationOpen(false);
              setDesignOpen(false);
            }}
          >
            Share design
          </Button>
        )}
        {!lifecycle && presentation && (
          <Button
            disabled={
              loading ||
              !current ||
              current.resource_revision !== resourceRevision
            }
            aria-expanded={presentationOpen}
            onClick={() => {
              setPresentationOpen((value) => !value);
              setDesignOpen(false);
              setEditorOpen(false);
              setExportsOpen(false);
              setSharingOpen(false);
            }}
          >
            Present design
          </Button>
        )}
        {design && (
          <Button
            aria-expanded={designOpen}
            onClick={() => {
              setDesignOpen((value) => !value);
              setEditorOpen(false);
              setExportsOpen(false);
              setSharingOpen(false);
              setPresentationOpen(false);
            }}
          >
            Design controls
          </Button>
        )}
        <Select
          aria-label="Preview zoom"
          value={zoomMode}
          onChange={(event) =>
            setZoom({ resourceId, value: event.target.value })
          }
          style={{ width: 'auto' }}
        >
          <option value="fit">Fit page</option>
          <option value="width">Fit width</option>
          <option value="actual">Actual size</option>
        </Select>
      </div>
      {loading && current && (
        <p
          id="design-preview-refresh-status"
          className="muted"
          role="status"
          style={{ margin: 0, flexShrink: 0 }}
        >
          The saved preview is refreshing. Refresh preview is available again
          once this request settles.
        </p>
      )}
      {!lifecycle && presentation && (
        <div className="panel-controls" hidden={!presentationOpen}>
          <ArtifactPresentationPanel
            {...presentation}
            resourceId={resourceId}
            resourceRevision={current?.resource_revision ?? resourceRevision}
            visible={visible && presentationOpen}
          />
        </div>
      )}
      {design && current && (
        <div className="panel-controls" hidden={!designOpen}>
          <ArtifactDesignPanel
            {...design}
            resourceRevision={current.resource_revision}
            pageId={current.page_id}
            selectedElementId={selectedElementId}
            onSelectElement={setSelectedElementId}
            visible={visible && designOpen}
          />
        </div>
      )}
      {!lifecycle && sharing && (
        <div className="panel-controls" hidden={!sharingOpen}>
          <ArtifactSharingPanel
            {...sharing}
            resourceId={resourceId}
            resourceRevision={current?.resource_revision ?? resourceRevision}
            visible={visible && sharingOpen}
          />
        </div>
      )}
      {!lifecycle && createExport && downloadExport && (
        <div className="panel-controls" hidden={!exportsOpen}>
          <ArtifactExports
            resourceId={resourceId}
            resourceRevision={current?.resource_revision ?? resourceRevision}
            visible={visible && exportsOpen}
            currentPageIndex={current?.page_index ?? 0}
            pageCount={current?.page_count ?? 0}
            create={createExport}
            download={downloadExport}
          />
        </div>
      )}
      {loadEditing && edit && (
        <div className="panel-controls" hidden={!editorOpen}>
          <ArtifactEditor
            resourceId={resourceId}
            resourceRevision={resourceRevision}
            visible={visible && editorOpen}
            pageId={current?.page_id}
            selectedElementId={selectedElementId}
            authoring={authoring}
            onAuthoringChange={setAuthoring}
            onPageChange={(next) => {
              setSelectedElementId(undefined);
              setSelection({ resourceId, pageId: next });
            }}
            load={loadEditing}
            edit={edit}
            onEdited={() => setRefresh((value) => value + 1)}
          />
        </div>
      )}
      {editError && (
        <ErrorState title="Design edit needs review">{editError}</ErrorState>
      )}
      {error && (
        <ErrorState
          title="Preview unavailable"
          action={<Button onClick={reload}>Reload preview</Button>}
        >
          {error}
        </ErrorState>
      )}
      {loading && !current && <Skeleton label="Loading design preview" />}
      {current?.html && (
        <>
          <p aria-live="polite" style={{ margin: 0, flexShrink: 0 }}>
            {pageLabel} {current.page_index + 1} of {current.page_count}:{' '}
            {current.page_title}
          </p>
          <div
            ref={frameHost}
            style={{
              width: '100%',
              flex: '1 0 96px',
              minHeight: 96,
              position: 'relative',
              overflow: zoomMode === 'fit' ? 'clip' : 'auto',
            }}
          >
            <div
              style={{
                width: current.canvas_width * scale,
                height: current.canvas_height * scale,
              }}
            >
              <iframe
                ref={frame}
                title={`${pageLabel} preview: ${current.page_title}`}
                sandbox={interactive ? 'allow-scripts' : ''}
                referrerPolicy="no-referrer"
                srcDoc={current.html}
                style={{
                  width: current.canvas_width,
                  height: current.canvas_height,
                  position: 'absolute',
                  left: Math.max(
                    0,
                    (viewport.width - current.canvas_width * scale) / 2,
                  ),
                  top: Math.max(
                    0,
                    (viewport.height - current.canvas_height * scale) / 2,
                  ),
                  border: 0,
                  transform: `scale(${scale})`,
                  transformOrigin: 'top left',
                }}
              />
            </div>
          </div>
        </>
      )}
      {lifecycle &&
        !editorOpen &&
        !designOpen &&
        current &&
        current.resource_revision === resourceRevision &&
        presentation &&
        createExport &&
        downloadExport &&
        sharing && (
          <ArtifactLifecyclePanel
            {...lifecycle}
            resourceId={resourceId}
            resourceRevision={current.resource_revision}
            visible={visible}
            renderPresentation={() => (
              <ArtifactPresentationPanel
                {...presentation}
                resourceId={resourceId}
                resourceRevision={current.resource_revision}
                visible={visible}
              />
            )}
            renderExport={() => (
              <ArtifactExports
                resourceId={resourceId}
                resourceRevision={current.resource_revision}
                visible={visible}
                currentPageIndex={current.page_index}
                pageCount={current.page_count}
                create={createExport}
                download={downloadExport}
              />
            )}
            renderSharing={() => (
              <ArtifactSharingPanel
                {...sharing}
                resourceId={resourceId}
                resourceRevision={current.resource_revision}
                visible={visible}
              />
            )}
          />
        )}
      <p className="muted" style={{ margin: 0, flexShrink: 0, fontSize: 12 }}>
        Preview of the saved design. Advanced design controls remain available
        in Designer Studio.
      </p>
    </section>
  );
}
