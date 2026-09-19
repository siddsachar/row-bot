import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Button, ErrorState } from '../../ui/primitives';

export type DesignPresentationState = {
  resource_id: string;
  resource_revision: string;
  page_id: string;
  title: string;
  notes: string;
  page_index: number;
  page_count: number;
  pages: { id: string; title: string; index: number }[];
  next_cursor: string | null;
};
export type PresentationSession = {
  session_id: string;
  resource_id: string;
  resource_revision: string;
  page_id: string;
  state: 'started' | 'page' | 'ended';
};
export type ArtifactPresentationProps = {
  resourceId: string;
  resourceRevision: string;
  visible: boolean;
  load: (
    options: { page_index?: number; cursor?: string; limit: number },
    signal: AbortSignal,
  ) => Promise<DesignPresentationState>;
  renderPreview: (pageId: string, kind: 'stage' | 'thumbnail') => ReactNode;
  onSession?: (session: PresentationSession) => void;
  openAudience?: (session: PresentationSession) => Promise<boolean>;
};

export default function ArtifactPresentation(props: ArtifactPresentationProps) {
  const [active, setActive] = useState(false);
  const [state, setState] = useState<DesignPresentationState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [thumbnails, setThumbnails] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const host = useRef<HTMLDivElement>(null);
  const request = useRef<AbortController | null>(null);
  const session = useRef<PresentationSession | null>(null);
  const current = useRef(props);
  useEffect(() => {
    current.current = props;
  }, [props]);

  const { resourceId, resourceRevision, visible } = props;
  useEffect(() => {
    request.current?.abort();
    request.current = null;
    if (document.fullscreenElement === host.current)
      void document.exitFullscreen?.().catch(() => {});
    if (session.current) {
      current.current.onSession?.({ ...session.current, state: 'ended' });
      session.current = null;
    }
    setActive(false);
    setState(null);
    setBusy(false);
    setError('');
    setElapsed(0);
    return () => {
      request.current?.abort();
      if (session.current) {
        current.current.onSession?.({ ...session.current, state: 'ended' });
        session.current = null;
      }
    };
  }, [resourceId, resourceRevision, visible]);

  useEffect(() => {
    if (!active) return;
    const started = Date.now();
    const timer = window.setInterval(
      () => setElapsed(Math.floor((Date.now() - started) / 1000)),
      1000,
    );
    return () => window.clearInterval(timer);
  }, [active]);

  async function load(pageIndex?: number, cursor?: string) {
    if (request.current || !props.visible) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setError('');
    try {
      const result = await props.load(
        { page_index: pageIndex, cursor, limit: 25 },
        controller.signal,
      );
      if (
        controller.signal.aborted ||
        current.current.resourceId !== resourceId ||
        current.current.resourceRevision !== resourceRevision
      )
        return;
      if (
        result.resource_id !== resourceId ||
        result.resource_revision !== resourceRevision ||
        result.page_count < 1 ||
        result.page_index < 0 ||
        result.page_index >= result.page_count
      )
        throw new Error('stale presentation');
      // Keep one bounded thumbnail page; forward pagination must not retain
      // every previously visited iframe and its document.
      setState(result);
      if (!session.current) {
        session.current = {
          session_id: crypto.randomUUID(),
          resource_id: resourceId,
          resource_revision: resourceRevision,
          page_id: result.page_id,
          state: 'started',
        };
        setElapsed(0);
        setActive(true);
      } else
        session.current = {
          ...session.current,
          page_id: result.page_id,
          state: 'page',
        };
      props.onSession?.(session.current);
    } catch {
      if (
        !controller.signal.aborted &&
        current.current.resourceId === resourceId &&
        current.current.resourceRevision === resourceRevision
      ) {
        setState(null);
        if (session.current)
          props.onSession?.({ ...session.current, state: 'ended' });
        session.current = null;
        setActive(false);
        setError(
          'This presentation is unavailable. Review the current saved design and its access before retrying.',
        );
      }
    } finally {
      if (request.current === controller) {
        request.current = null;
        setBusy(false);
      }
    }
  }

  function end() {
    request.current?.abort();
    request.current = null;
    if (session.current)
      props.onSession?.({ ...session.current, state: 'ended' });
    session.current = null;
    setActive(false);
    setState(null);
    setBusy(false);
    setError('');
    if (document.fullscreenElement === host.current)
      void document.exitFullscreen?.().catch(() => {});
  }

  async function audience() {
    const selected = session.current;
    if (!selected || !props.openAudience) return;
    try {
      const opened = await props.openAudience(selected);
      if (!opened && session.current?.session_id === selected.session_id)
        setError(
          'The audience window could not open. Check popup permissions and try again.',
        );
    } catch {
      if (session.current?.session_id === selected.session_id)
        setError(
          'The audience window is unavailable. The presentation remains in this panel.',
        );
    }
  }

  if (!props.visible) return null;
  return (
    <div
      ref={host}
      className="artifact-presentation panel-section studio-section stack"
      onKeyDown={(event) => {
        if (
          !active ||
          busy ||
          event.target instanceof HTMLInputElement ||
          event.target instanceof HTMLTextAreaElement
        )
          return;
        if (event.key === 'Escape') end();
        else if (
          event.key === 'ArrowRight' &&
          state &&
          state.page_index + 1 < state.page_count
        ) {
          event.preventDefault();
          void load(state.page_index + 1);
        } else if (event.key === 'ArrowLeft' && state && state.page_index > 0) {
          event.preventDefault();
          void load(state.page_index - 1);
        }
      }}
    >
      <header className="capability-header">
        <div>
          <h3>Presentation</h3>
          <p>Present the exact saved design without editing the source.</p>
        </div>
      </header>
      {!active ? (
        <Button variant="primary" disabled={busy} onClick={() => void load()}>
          Start presentation
        </Button>
      ) : (
        <>
          <div className="panel-toolbar action-cluster">
            <Button
              disabled={busy || !state || state.page_index === 0}
              onClick={() => state && void load(state.page_index - 1)}
            >
              Previous slide
            </Button>
            <Button
              disabled={
                busy || !state || state.page_index + 1 >= state.page_count
              }
              onClick={() => state && void load(state.page_index + 1)}
            >
              Next slide
            </Button>
            <Button
              onClick={() => {
                if (!host.current?.requestFullscreen) {
                  setError('Fullscreen is unavailable in this browser.');
                  return;
                }
                void host.current
                  .requestFullscreen()
                  .catch(() => setError('Fullscreen was not permitted.'));
              }}
            >
              Fullscreen
            </Button>
            {props.openAudience && (
              <Button onClick={() => void audience()}>
                Open audience window
              </Button>
            )}
            <Button onClick={end}>End presentation</Button>
          </div>
          {state && (
            <>
              <p aria-live="polite">
                Slide {state.page_index + 1} of {state.page_count}:{' '}
                {state.title}
              </p>
              <div role="group" aria-label="Presentation slide">
                {props.renderPreview(state.page_id, 'stage')}
              </div>
              <aside className="capability-section" aria-label="Speaker notes">
                <h4>Speaker notes</h4>
                <p style={{ whiteSpace: 'pre-wrap' }}>
                  {state.notes || 'No speaker notes for this slide.'}
                </p>
              </aside>
              <p role="timer" aria-label="Presentation elapsed time">
                {String(Math.floor(elapsed / 60)).padStart(2, '0')}:
                {String(elapsed % 60).padStart(2, '0')}
              </p>
              <Button
                aria-pressed={thumbnails}
                onClick={() => setThumbnails((value) => !value)}
              >
                Slide thumbnails
              </Button>
              {thumbnails && (
                <nav
                  className="capability-section stack"
                  aria-label="Slide thumbnails"
                >
                  <Button
                    disabled={busy}
                    onClick={() => void load(state.page_index)}
                  >
                    First thumbnails
                  </Button>
                  {state.pages.map((page) => (
                    <div key={page.id}>
                      <Button
                        disabled={busy}
                        aria-current={
                          page.id === state.page_id ? 'page' : undefined
                        }
                        onClick={() => void load(page.index)}
                      >
                        {page.index + 1}. {page.title}
                      </Button>
                      {props.renderPreview(page.id, 'thumbnail')}
                    </div>
                  ))}
                  {state.next_cursor && (
                    <Button
                      disabled={busy}
                      onClick={() =>
                        void load(state.page_index, state.next_cursor!)
                      }
                    >
                      More slides
                    </Button>
                  )}
                </nav>
              )}
            </>
          )}
        </>
      )}
      {error && <ErrorState title="Presentation notice">{error}</ErrorState>}
    </div>
  );
}
