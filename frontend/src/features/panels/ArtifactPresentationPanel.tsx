import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type {
  ArtifactPreview,
  DesignPresentationOptions,
  DesignPresentationState,
} from '../../api/types';
import { ErrorState, Skeleton } from '../../ui/primitives';
import ArtifactPresentation, {
  type PresentationSession,
} from './ArtifactPresentation';

export type ArtifactPresentationPanelProps = {
  resourceId: string;
  resourceRevision: string;
  visible: boolean;
  load: (
    options: DesignPresentationOptions,
    signal: AbortSignal,
  ) => Promise<DesignPresentationState>;
  preview: (pageId: string, signal: AbortSignal) => Promise<ArtifactPreview>;
};

export function StaticDesignPage({
  resourceId,
  resourceRevision,
  pageId,
  preview,
  thumbnail = false,
  fitViewport = false,
}: {
  resourceId: string;
  resourceRevision: string;
  pageId: string;
  preview: ArtifactPresentationPanelProps['preview'];
  thumbnail?: boolean;
  fitViewport?: boolean;
}) {
  const [value, setValue] = useState<ArtifactPreview | null>(null);
  const [error, setError] = useState(false);
  const [visible, setVisible] = useState(!thumbnail);
  const [width, setWidth] = useState(0);
  const host = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = host.current;
    if (!element) return;
    const ownWindow = element.ownerDocument.defaultView;
    const update = () => setWidth(element.getBoundingClientRect().width);
    update();
    // Resize observation belongs to the document rendering this portal.
    const Observer =
      (ownWindow as typeof window | null)?.ResizeObserver ?? ResizeObserver;
    const measure = new Observer(update);
    measure.observe(element);
    ownWindow?.addEventListener('resize', update);
    const visibility = thumbnail
      ? new IntersectionObserver((entries) =>
          setVisible(!!entries[0]?.isIntersecting),
        )
      : null;
    visibility?.observe(element);
    return () => {
      measure.disconnect();
      ownWindow?.removeEventListener('resize', update);
      visibility?.disconnect();
    };
  }, [thumbnail]);
  useEffect(() => {
    const abort = new AbortController();
    setValue(null);
    setError(false);
    if (!visible) return () => abort.abort();
    preview(pageId, abort.signal).then(
      (next) => {
        if (abort.signal.aborted) return;
        if (
          next.resource_id !== resourceId ||
          next.resource_revision !== resourceRevision ||
          next.page_id !== pageId ||
          next.scripts_allowed !== false ||
          !next.html
        ) {
          setError(true);
          return;
        }
        setValue(next);
      },
      () => {
        if (!abort.signal.aborted) setError(true);
      },
    );
    return () => abort.abort();
  }, [preview, resourceId, resourceRevision, pageId, visible]);
  const current =
    value?.resource_id === resourceId &&
    value.resource_revision === resourceRevision &&
    value.page_id === pageId
      ? value
      : null;
  return (
    <div
      ref={host}
      style={{
        width: '100%',
        maxWidth: thumbnail
          ? 240
          : fitViewport && current
            ? `calc(100vh * ${current.canvas_width / current.canvas_height})`
            : undefined,
        marginInline: 'auto',
        position: 'relative',
        aspectRatio: current
          ? `${current.canvas_width}/${current.canvas_height}`
          : '16/9',
        overflow: 'hidden',
      }}
    >
      {error ? (
        <ErrorState title="Presentation preview unavailable">
          Reload the current design before presenting.
        </ErrorState>
      ) : !current ? (
        <Skeleton
          label={
            thumbnail ? 'Loading slide thumbnail' : 'Loading presentation slide'
          }
        />
      ) : (
        <iframe
          title={`${thumbnail ? 'Thumbnail' : 'Presentation'}: ${current.page_title}`}
          sandbox=""
          referrerPolicy="no-referrer"
          srcDoc={current.html ?? undefined}
          style={{
            position: 'absolute',
            backgroundColor: '#fff',
            inset: 0,
            border: 0,
            width: current.canvas_width,
            height: current.canvas_height,
            transformOrigin: 'top left',
            transform: `scale(${width / current.canvas_width})`,
          }}
        />
      )}
    </div>
  );
}

/** Audience content is a script-free portal owned by this authenticated panel.
 * There is no cross-window command channel or credential in a URL/message.
 */
export default function ArtifactPresentationPanel(
  props: ArtifactPresentationPanelProps,
) {
  const audience = useRef<Window | null>(null);
  const [audienceHost, setAudienceHost] = useState<HTMLElement | null>(null);
  const [session, setSession] = useState<PresentationSession | null>(null);
  const close = useCallback(() => {
    audience.current?.close();
    audience.current = null;
    setAudienceHost(null);
  }, []);
  useEffect(
    () => () => {
      audience.current?.close();
      audience.current = null;
    },
    [],
  );
  const onSession = useCallback(
    (next: PresentationSession) => {
      setSession(next.state === 'ended' ? null : next);
      if (next.state === 'ended') close();
    },
    [close],
  );
  const openAudience = useCallback(async () => {
    if (audience.current && !audience.current.closed) {
      audience.current.focus();
      return true;
    }
    const child = window.open(
      'about:blank',
      '_blank',
      'popup,width=1280,height=800',
    );
    if (!child) return false;
    child.document.title = 'Row-Bot presentation';
    child.document.body.style.cssText =
      'margin:0;background:#000;min-height:100vh;display:flex;align-items:center;';
    const mount = child.document.createElement('main');
    mount.setAttribute('aria-label', 'Audience presentation');
    mount.style.width = '100%';
    child.document.body.append(mount);
    // No notes, review controls, credentials or application DOM are copied.
    audience.current = child;
    setAudienceHost(mount);
    return true;
  }, []);
  return (
    <>
      <ArtifactPresentation
        {...props}
        onSession={onSession}
        openAudience={openAudience}
        renderPreview={(pageId, kind) => (
          <StaticDesignPage
            resourceId={props.resourceId}
            resourceRevision={props.resourceRevision}
            pageId={pageId}
            preview={props.preview}
            thumbnail={kind === 'thumbnail'}
          />
        )}
      />
      {audienceHost &&
        session &&
        createPortal(
          <StaticDesignPage
            resourceId={props.resourceId}
            resourceRevision={props.resourceRevision}
            pageId={session.page_id}
            preview={props.preview}
            fitViewport
          />,
          audienceHost,
        )}
    </>
  );
}
