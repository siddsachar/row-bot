import { useEffect, useRef, useState, type FormEvent } from 'react';
import { clientError } from '../../api/errors';
import { useClientSelector, useRuntime } from '../../runtime';
import { Button, EmptyState } from '../../ui/primitives';

const OUTPUT_LIMIT = 256 * 1024;

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
  const cursor = useRef(0);
  const outputRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    let alive = true;
    let terminal = '';
    const abort = new AbortController();
    void platform.openTerminal(conversationId).then(async (result) => {
      if (!alive) return;
      if (result.status !== 'ok') {
        setError(
          'The interactive terminal requires the trusted desktop window.',
        );
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

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!terminalId || !input) return;
    const value = input;
    setInput('');
    try {
      await controller.terminalInput(terminalId, `${value}\r`);
    } catch (cause) {
      setError(clientError(cause).message);
    }
  }

  if (!visible) return null;
  if (error && !terminalId)
    return <EmptyState title="Terminal unavailable">{error}</EmptyState>;
  return (
    <section
      className="native-terminal stack"
      aria-label="Interactive terminal"
    >
      <div className="button-row">
        <strong>Interactive terminal</strong>
        <small>
          {terminalId ? 'Connected to the local PTY' : 'Connecting…'}
        </small>
      </div>
      {truncated && (
        <p role="status">
          Earlier terminal output was dropped to preserve bounds.
        </p>
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
          onChange={(event) => setInput(event.currentTarget.value)}
        />
        <Button type="submit" disabled={!terminalId || !input}>
          Send
        </Button>
      </form>
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
