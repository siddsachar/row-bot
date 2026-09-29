import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Eraser, SquareArrowOutUpRight, Square } from 'lucide-react';
import { clientError } from '../../api/errors';
import { useClientSelector, useRuntime } from '../../runtime';
import {
  Button,
  EmptyState,
  Hint,
  IconButton,
  Toolbar,
} from '../../ui/primitives';

const OUTPUT_LIMIT = 256 * 1024;
// What Ctrl+C types in a terminal: the shell stops the running command
// (ConPTY raises CTRL_C_EVENT, a Unix terminal sends SIGINT).
const INTERRUPT = '\x03';

export default function NativeTerminal({ visible }: { visible: boolean }) {
  const conversationId = useClientSelector(
    (value) => value.selectedConversationId,
  );
  const { controller, platform } = useRuntime();
  const [terminalId, setTerminalId] = useState('');
  const [output, setOutput] = useState('');
  const [input, setInput] = useState('');
  const [error, setError] = useState('');
  const [truncated, setTruncated] = useState(false);
  // Only the desktop app can open the person's own terminal app.
  const [external, setExternal] = useState(false);
  const [externalError, setExternalError] = useState('');
  const cursor = useRef(0);
  const outputRef = useRef<HTMLPreElement>(null);

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

  useEffect(() => {
    let alive = true;
    let terminal = '';
    const abort = new AbortController();
    void platform.openTerminal(conversationId).then(async (result) => {
      if (!alive) return;
      if (result.status !== 'ok') {
        setError('The terminal needs the Row-Bot desktop app.');
        return;
      }
      terminal = result.value.terminalId;
      setTerminalId(terminal);
      try {
        await controller.terminalResize(terminal, 120, 30, abort.signal);
      } catch (cause) {
        if (alive) setError(clientError(cause).message);
      }
    });
    return () => {
      alive = false;
      abort.abort();
      if (terminal)
        void controller.terminalDisconnect(terminal).catch(() => {});
    };
  }, [controller, conversationId, platform]);

  useEffect(() => {
    if (!visible || !terminalId) return;
    let alive = true;
    let timer = 0;
    const abort = new AbortController();
    const poll = async () => {
      try {
        const value = await controller.terminalRead(
          terminalId,
          cursor.current,
          abort.signal,
        );
        if (!alive) return;
        cursor.current = value.cursor;
        if (value.truncated) setTruncated(true);
        const next = value.frames.map((frame) => frame.data).join('');
        if (next)
          setOutput((previous) => (previous + next).slice(-OUTPUT_LIMIT));
        timer = window.setTimeout(poll, value.latest > value.cursor ? 0 : 100);
      } catch (cause) {
        if (alive) setError(clientError(cause).message);
      }
    };
    void poll();
    return () => {
      alive = false;
      abort.abort();
      window.clearTimeout(timer);
    };
  }, [controller, terminalId, visible]);

  useEffect(() => {
    const element = outputRef.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [output]);

  async function send(data: string) {
    if (!terminalId) return;
    try {
      await controller.terminalInput(terminalId, data);
    } catch (cause) {
      setError(clientError(cause).message);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!terminalId || !input) return;
    const value = input;
    setInput('');
    await send(`${value}\r`);
  }

  // Only what is shown goes: the read cursor stays, so old output never
  // comes back on the next read.
  function clear() {
    setOutput('');
    setTruncated(false);
  }

  async function openExternal() {
    setExternalError('');
    const result = await platform.openExternalTerminal(conversationId);
    if (result.status === 'unavailable')
      setExternalError('Row-Bot couldn’t open your terminal app.');
  }

  if (!visible) return null;
  if (error && !terminalId)
    return <EmptyState title="Terminal unavailable">{error}</EmptyState>;
  return (
    <section
      className="native-terminal stack"
      aria-label="Interactive terminal"
    >
      <div className="native-terminal-header">
        <div className="native-terminal-title">
          <strong>Interactive terminal</strong>
          <small>
            {terminalId ? 'Terminal on this computer' : 'Connecting…'}
          </small>
        </div>
        <Toolbar label="Terminal actions">
          <Hint label="Stop" shortcut="Ctrl+C">
            <Button
              iconOnly
              variant="ghost"
              className="icon-action icon-action-sm"
              aria-label="Stop the running command"
              aria-keyshortcuts="Control+C"
              disabled={!terminalId}
              onClick={() => void send(INTERRUPT)}
            >
              <Square size={14} aria-hidden />
            </Button>
          </Hint>
          <IconButton
            size="sm"
            label="Clear"
            disabled={!output && !truncated}
            onClick={clear}
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
        </Toolbar>
      </div>
      {truncated && (
        <p role="status">Some earlier output is no longer shown.</p>
      )}
      <pre ref={outputRef} className="native-terminal-output" tabIndex={0}>
        {output || 'Terminal output will appear here.'}
      </pre>
      <form
        className="native-terminal-input"
        onSubmit={(event) => void submit(event)}
      >
        <label htmlFor="native-terminal-command">Terminal input</label>
        <input
          id="native-terminal-command"
          value={input}
          maxLength={16384}
          autoComplete="off"
          disabled={!terminalId}
          aria-keyshortcuts="Control+C"
          onChange={(event) => setInput(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (
              event.key.toLowerCase() !== 'c' ||
              !event.ctrlKey ||
              event.metaKey ||
              event.altKey ||
              event.shiftKey
            )
              return;
            const field = event.currentTarget;
            // Selected text copies as usual; otherwise Ctrl+C stops the
            // running command, like a terminal. The typed line is kept.
            if (field.selectionStart !== field.selectionEnd) return;
            event.preventDefault();
            void send(INTERRUPT);
          }}
        />
        <Button type="submit" disabled={!terminalId || !input}>
          Send
        </Button>
      </form>
      {(error || externalError) && <p role="alert">{error || externalError}</p>}
    </section>
  );
}
