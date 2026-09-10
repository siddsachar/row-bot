import { useEffect, useRef, useState } from 'react';
import type { ArtifactPreview as Preview } from '../../api/types';
import { Button, ErrorState, Select, Skeleton } from '../../ui/primitives';

export type ArtifactPreviewProps = {
  resourceId: string;
  resourceRevision: string;
  visible: boolean;
  load: (
    pageId?: string,
    knownRevision?: string,
    signal?: AbortSignal,
  ) => Promise<Preview>;
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
    return 'This preview currently supports Deck designs.';
  if (code === 'page_unavailable')
    return 'That slide is no longer available. Reload the design to continue.';
  return 'The design is bound, but its preview could not load. Retry the preview.';
}

export default function ArtifactPreview({
  resourceId,
  resourceRevision,
  visible,
  load,
}: ArtifactPreviewProps) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [selection, setSelection] = useState({
    resourceId,
    pageId: undefined as string | undefined,
  });
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [viewport, setViewport] = useState({ width: 400, height: 225 });
  const measured = useRef(viewport);
  const frameHost = useRef<HTMLDivElement>(null);
  const latest = useRef<Preview | null>(null);
  const loader = useRef(load);
  const request = useRef(0);
  const pageId =
    selection.resourceId === resourceId ? selection.pageId : undefined;

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
    loader.current(pageId, known, abort.signal).then(
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
  }, [resourceId, resourceRevision, pageId, refresh, visible]);

  const current = preview?.resource_id === resourceId ? preview : null;
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
    ? Math.min(
        viewport.width / current.canvas_width,
        viewport.height / current.canvas_height,
      )
    : 1;
  return (
    <section
      aria-label="Design preview"
      aria-busy={loading}
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
        className="toolbar"
        role="group"
        aria-label="Slide navigation"
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
          Previous slide
        </Button>
        <Select
          aria-label="Slide"
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
          {!current && <option value="">No slide loaded</option>}
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
          Next slide
        </Button>
        <Button
          disabled={loading}
          onClick={() => setRefresh((value) => value + 1)}
        >
          Refresh preview
        </Button>
      </div>
      {error && (
        <ErrorState
          title="Preview unavailable"
          action={<Button onClick={reload}>Reload preview</Button>}
        >
          {error}
        </ErrorState>
      )}
      {loading && <Skeleton label="Loading design preview" />}
      {current?.html && (
        <>
          <p aria-live="polite" style={{ margin: 0, flexShrink: 0 }}>
            Slide {current.page_index + 1} of {current.page_count}:{' '}
            {current.page_title}
          </p>
          <div
            ref={frameHost}
            style={{
              width: '100%',
              flex: '1 0 96px',
              minHeight: 96,
              position: 'relative',
              overflow: 'clip',
            }}
          >
            <iframe
              title={`Slide preview: ${current.page_title}`}
              sandbox=""
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
        </>
      )}
      <p className="muted" style={{ margin: 0, flexShrink: 0, fontSize: 12 }}>
        Deck preview. Export and advanced design controls remain available in
        Designer Studio.
      </p>
    </section>
  );
}
