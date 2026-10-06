import { useEffect, useRef, useState } from 'react';
import { useClientSelector, useRuntime } from '../../runtime';
import { clientError } from '../../api/errors';
import type { AppViewRender, TraceAppRef } from '../../api/types';
import { Button } from '../../ui/primitives';
import ApprovalCard from '../shell/ApprovalCard';
import { AppIcon } from './parts';
import { ViewBridge, type ViewState } from './view-bridge';

/**
 * An app's view in chat (MCP Apps). It loads once into a frame with `sandbox="allow-scripts"`
 * only (an opaque origin: no Row-Bot cookies, storage or page) under the policy the server sent
 * with it, and talks to Row-Bot only through the host bridge, where the app's access and this
 * chat's approvals apply to anything it asks for.
 */
export default function AppViewFrame({
  conversation,
  callId,
  app,
}: {
  conversation: string;
  callId: string;
  app: TraceAppRef;
}) {
  const { controller, platform } = useRuntime();
  const [render, setRender] = useState<AppViewRender | null>(null);
  const [state, setState] = useState<ViewState | 'off'>('loading');
  const [message, setMessage] = useState('');
  const [height, setHeight] = useState(160);
  const [attempt, setAttempt] = useState(0);
  const frame = useRef<HTMLIFrameElement>(null);
  const bridge = useRef<ViewBridge | null>(null);
  const loads = useRef(0);
  const [answered, setAnswered] = useState<string[]>([]);
  // A call from this view that must ask waits on the standard approval card, shown here: no turn waits on it.
  const asking = useClientSelector((snapshot) =>
    [...snapshot.activity]
      .reverse()
      .find(
        (record) =>
          record.event.type === 'approval.required' &&
          record.event.conversation_id === conversation &&
          render !== null &&
          record.event.payload.requesting_trace_id === render.render_id,
      ),
  );
  const approval =
    asking?.event.type === 'approval.required' &&
    !answered.includes(asking.event.payload.approval_id ?? '')
      ? asking.event.payload
      : null;

  useEffect(() => {
    const abort = new AbortController();
    setRender(null);
    setState('loading');
    setMessage('');
    controller.renderAppView(conversation, callId, abort.signal).then(
      (value) => setRender(value),
      (cause) => {
        if (abort.signal.aborted) return;
        const error = clientError(cause);
        setState(error.code === 'views_off' ? 'off' : 'failed');
        setMessage(error.message);
      },
    );
    return () => abort.abort();
  }, [controller, conversation, callId, attempt]);

  useEffect(() => {
    const element = frame.current;
    if (!render || !element) return;
    loads.current = 0;
    const instance = new ViewBridge(element, {
      tool: render.tool,
      input: render.input ?? {},
      result: render.result ?? null,
      theme: () =>
        document.documentElement.dataset.theme === 'light' ? 'light' : 'dark',
      platform: 'pywebview' in window ? 'desktop' : 'web',
      callTool: async (name, args) => {
        try {
          return await controller.callAppViewTool(render.render_id, {
            name,
            arguments: args,
          });
        } catch (cause) {
          throw new Error(clientError(cause).message, { cause });
        }
      },
      openLink: (url) => void platform.openExternal(url),
      onHeight: setHeight,
      onState: (next, text) => {
        setState(next);
        if (text) setMessage(text);
      },
    });
    bridge.current = instance;
    const listener = (event: MessageEvent) => instance.handle(event);
    window.addEventListener('message', listener);
    return () => {
      window.removeEventListener('message', listener);
      void instance.teardown();
      bridge.current = null;
    };
  }, [render, controller, platform]);

  const title = render ? `${app.name} · ${render.tool.title}` : app.name;
  const shown = render && state !== 'failed' && state !== 'ended';
  return (
    <section
      className="app-view"
      aria-label={`${title} view`}
      data-state={state}
      data-border={render?.prefers_border === false ? 'none' : undefined}
    >
      <header className="app-view-header">
        <AppIcon icon={app.icon} size={16} />
        <span>{title}</span>
      </header>
      {shown && (
        <iframe
          ref={frame}
          className="app-view-frame"
          title={title}
          src={render.frame_url}
          sandbox="allow-scripts"
          referrerPolicy="no-referrer"
          style={{ height }}
          onLoad={() => {
            loads.current += 1;
            if (loads.current > 1) bridge.current?.navigated();
          }}
        />
      )}
      {state === 'loading' && !render && (
        <p className="app-view-note" role="status">
          Opening {app.name}’s view…
        </p>
      )}
      {(state === 'failed' || state === 'ended' || state === 'off') &&
        message && (
          <p className="app-view-note" role="status">
            {message}
            {state !== 'off' && (
              <>
                {' '}
                <Button
                  className="settings-link"
                  onClick={() => setAttempt((n) => n + 1)}
                >
                  Open again
                </Button>
              </>
            )}
          </p>
        )}
      {approval?.approval_id && (
        <ApprovalCard
          id={approval.approval_id}
          conversationId={conversation}
          hint={approval}
          onResolved={() =>
            setAnswered((ids) => [...ids, approval.approval_id ?? ''])
          }
        />
      )}
      {render && render.domains && render.domains.length > 0 && (
        <p className="app-view-note">
          This view also loads from {render.domains.join(', ')}.
        </p>
      )}
    </section>
  );
}
