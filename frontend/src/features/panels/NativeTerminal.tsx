import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import {
  Eraser,
  Square,
  SquareArrowOutUpRight,
  SquareTerminal,
  X,
} from 'lucide-react';
import { useClientSelector, useRuntime } from '../../runtime';
import {
  Button,
  EmptyState,
  Hint,
  IconButton,
  Toolbar,
} from '../../ui/primitives';
import { terminalSession } from './terminal-session';
import './NativeTerminal.css';

/**
 * The desktop terminal (B248): a real terminal (xterm.js) on the person's
 * own shell. Keys go straight to the shell, output keeps its colours and
 * cursor, and the scrollback stays while the dock is closed.
 */
export default function NativeTerminal({
  onClose,
  focusKey = 0,
}: {
  onClose: () => void;
  /** Each new value moves the keyboard focus into the terminal. */
  focusKey?: number;
}) {
  const conversationId = useClientSelector(
    (value) => value.selectedConversationId,
  );
  const { controller, platform } = useRuntime();
  const session = terminalSession(controller, platform);
  const state = useSyncExternalStore(session.subscribe, session.snapshot);
  const host = useRef<HTMLDivElement>(null);
  // Only the desktop app can open the person's own terminal app.
  const [external, setExternal] = useState(false);
  const [externalError, setExternalError] = useState('');

  useEffect(() => {
    let alive = true;
    setExternal(false);
    void platform
      .discover()
      .then((result) => {
        if (alive)
          setExternal(
            result.status === 'ok' &&
              result.value.kind === 'pywebview' &&
              result.value.capabilities.includes('terminal_external'),
          );
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [platform]);

  useLayoutEffect(() => {
    const element = host.current!;
    const detach = session.attach(element);
    const observer = new ResizeObserver(() => session.layout());
    observer.observe(element);
    return () => {
      observer.disconnect();
      detach();
    };
  }, [session]);

  // Opened once for the page (later calls keep it); closing the dock keeps it.
  useEffect(() => {
    void session.connect(conversationId);
  }, [session, conversationId]);

  useEffect(() => {
    if (focusKey) session.focus();
  }, [session, focusKey]);

  useEffect(() => {
    session.applyTheme();
    const observer = new MutationObserver(() => session.applyTheme());
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme', 'data-accent'],
    });
    return () => observer.disconnect();
  }, [session]);

  async function openExternal() {
    setExternalError('');
    const result = await platform.openExternalTerminal(conversationId);
    if (result.status === 'unavailable')
      setExternalError('Row-Bot couldn’t open your terminal app.');
  }

  const reconnect = () => void session.reconnect(conversationId);
  const unavailable = state.status === 'failed' && !state.started;
  return (
    <section className="native-terminal" aria-label="Terminal">
      <header className="native-terminal-header">
        <h2 className="native-terminal-title">
          <SquareTerminal size={14} aria-hidden />
          Terminal
        </h2>
        {state.status === 'connecting' && (
          <span className="native-terminal-status" role="status">
            Connecting…
          </span>
        )}
        <Toolbar label="Terminal actions" className="native-terminal-actions">
          <Hint label="Stop" shortcut="Ctrl+C">
            <Button
              iconOnly
              variant="ghost"
              className="icon-action icon-action-sm"
              aria-label="Stop the running command"
              aria-keyshortcuts="Control+C"
              disabled={state.status !== 'open'}
              onClick={() => session.interrupt()}
            >
              <Square size={14} aria-hidden />
            </Button>
          </Hint>
          <IconButton
            size="sm"
            label="Clear"
            disabled={!state.started}
            onClick={() => session.clear()}
          >
            <Eraser size={14} aria-hidden />
          </IconButton>
          {external && (
            <IconButton
              size="sm"
              label="Open in your terminal"
              onClick={() => void openExternal()}
            >
              <SquareArrowOutUpRight size={14} aria-hidden />
            </IconButton>
          )}
          <IconButton
            size="sm"
            label="Close terminal"
            shortcut="Ctrl+`"
            onClick={onClose}
          >
            <X size={14} aria-hidden />
          </IconButton>
        </Toolbar>
      </header>
      {state.status === 'ended' && (
        <p className="native-terminal-notice" role="status">
          The terminal session ended.
          <Button variant="ghost" onClick={reconnect}>
            Start again
          </Button>
        </p>
      )}
      {state.status === 'failed' && state.started && (
        <p className="native-terminal-notice" role="alert">
          {state.error}
          {state.recoverable && (
            <Button variant="ghost" onClick={reconnect}>
              Reconnect
            </Button>
          )}
        </p>
      )}
      {state.truncated && (
        <p className="native-terminal-notice" role="status">
          Some earlier output is no longer shown.
        </p>
      )}
      {externalError && (
        <p className="native-terminal-notice" role="alert">
          {externalError}
        </p>
      )}
      {unavailable && (
        <EmptyState
          title="Terminal unavailable"
          action={
            state.recoverable && <Button onClick={reconnect}>Reconnect</Button>
          }
        >
          {state.error}
        </EmptyState>
      )}
      <div ref={host} className="native-terminal-screen" hidden={unavailable} />
    </section>
  );
}
