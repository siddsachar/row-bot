import {
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { ClipboardCopy, Square, Terminal } from 'lucide-react';
import type { BackgroundProcessView } from '../../api/types';
import { clientError } from '../../api/errors';
import { Drawer } from '../../ui/overlays';
import {
  Button,
  CopyGlyph,
  Disclosure,
  IconButton,
  Skeleton,
  StatusDot,
  useCopyFeedback,
} from '../../ui/primitives';
import { absoluteTime } from '../../ui/format';
import { writeClipboardText } from '../../platform/clipboard';
import {
  ProcessIoContext,
  processLive,
  processName,
  processStatus,
  runningSeconds,
  shortAddress,
  useBackgroundProcesses,
  useNow,
  useOpenOutput,
  type ProcessIo,
} from './background-processes';

/** Output kept on screen; older text scrolls off the top. */
const SCREEN_CHARS = 256 * 1024;
/** One read: the latest part when the view opens, then what's new. */
const READ_BYTES = 64 * 1024;
const FOLLOW_MS = 1000;

/** Details one tap away: where it runs, when it started, and the numbers. */
export function ProcessDetails({
  view,
  conversation,
}: {
  view: BackgroundProcessView;
  /** The conversation it belongs to, shown where several conversations' are listed. */
  conversation?: string;
}) {
  return (
    <dl className="process-details">
      <dt>Command</dt>
      <dd>
        <code>{view.command}</code>
      </dd>
      <dt>Folder</dt>
      <dd>
        <code>{view.folder}</code>
      </dd>
      {conversation !== undefined && (
        <>
          <dt>Conversation</dt>
          <dd>{conversation || 'Untitled conversation'}</dd>
        </>
      )}
      <dt>Started</dt>
      <dd>
        <time dateTime={view.started_at}>{absoluteTime(view.started_at)}</time>
      </dd>
      {view.local_urls.length > 0 && (
        <>
          <dt>Address</dt>
          <dd>{view.local_urls.map(shortAddress).join(', ')}</dd>
        </>
      )}
      {view.env_names.length > 0 && (
        <>
          <dt>Environment</dt>
          <dd>{view.env_names.join(', ')}</dd>
        </>
      )}
      {view.exit_code !== null && (
        <>
          <dt>Exit code</dt>
          <dd>{view.exit_code}</dd>
        </>
      )}
      {view.pid !== null && (
        <>
          <dt>Process number</dt>
          <dd>{view.pid}</dd>
        </>
      )}
    </dl>
  );
}

/**
 * A process's output in a side sheet: the latest part, then what's new while
 * it runs. It follows the end unless the person scrolls up.
 */
export function ProcessOutputDrawer({
  view,
  io,
  open,
  onOpenChange,
  onStop,
  stopping = false,
  conversation,
  seconds,
}: {
  view: BackgroundProcessView;
  io: ProcessIo;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onStop?: () => void;
  stopping?: boolean;
  conversation?: string;
  /** How long it has run, counted on while it runs. */
  seconds?: number;
}) {
  const [text, setText] = useState('');
  const [partial, setPartial] = useState(false);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [attempt, setAttempt] = useState(0);
  const [copied, setCopied] = useCopyFeedback();
  const position = useRef(-1);
  const logRef = useRef<HTMLPreElement>(null);
  const pinned = useRef(true);
  const running = processLive(view);
  const id = view.process_id;

  useEffect(() => {
    if (!open) return;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    position.current = -1;
    setText('');
    setPartial(false);
    setState('loading');
    const read = async () => {
      try {
        const first = position.current < 0;
        const after = first
          ? Math.max(0, view.output_size - READ_BYTES)
          : position.current;
        const page = await io.output(id, after, READ_BYTES, abort.signal);
        if (abort.signal.aborted) return;
        let chunk = page.text;
        // Start on a whole line when the view opens part way through.
        if (first && page.start > 0 && chunk.includes('\n'))
          chunk = chunk.slice(chunk.indexOf('\n') + 1);
        if (first) setPartial(page.start > 0);
        position.current = page.end;
        setText((current) => {
          const next = current + chunk;
          if (next.length <= SCREEN_CHARS) return next;
          setPartial(true);
          const cut = next.indexOf('\n', next.length - SCREEN_CHARS);
          return next.slice(cut < 0 ? next.length - SCREEN_CHARS : cut + 1);
        });
        setState('ready');
        // More is waiting now, or comes while it runs.
        if (page.end < page.size) timer = setTimeout(() => void read(), 0);
        else if (page.running) timer = setTimeout(() => void read(), FOLLOW_MS);
      } catch {
        if (!abort.signal.aborted) setState('error');
      }
    };
    void read();
    return () => {
      abort.abort();
      clearTimeout(timer);
    };
    // Reads start again only when the view opens or the person retries.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, id, attempt, io]);

  useLayoutEffect(() => {
    const element = logRef.current;
    if (element && pinned.current) element.scrollTop = element.scrollHeight;
  }, [text]);

  const status = processStatus(view, seconds ?? view.runtime_seconds);
  return (
    <Drawer
      open={open}
      onOpenChange={onOpenChange}
      title={processName(view)}
      description={view.name ? view.command : undefined}
      closeLabel="Close output"
      className="process-output-drawer"
      actions={
        <IconButton
          size="sm"
          label={copied ? 'Copied' : 'Copy output'}
          disabled={!text}
          onClick={() =>
            void writeClipboardText(text).then((ok) => ok && setCopied(true))
          }
        >
          <CopyGlyph copied={copied} size={14} idle={ClipboardCopy} />
        </IconButton>
      }
    >
      <div className="process-output">
        <div className="process-output-status">
          <StatusDot
            tone={status.tone}
            pulse={status.pulse}
            label={status.label}
            showLabel
          />
          {view.local_urls[0] && (
            <span className="process-address">
              Serving <code>{shortAddress(view.local_urls[0])}</code>
            </span>
          )}
          {running && onStop && (
            <Button
              className="small"
              disabled={stopping || view.status === 'stopping'}
              onClick={onStop}
            >
              <Square size={12} aria-hidden />
              Stop
            </Button>
          )}
        </div>
        {partial && (
          <p className="process-output-note" role="status">
            Showing the latest output; earlier lines aren&apos;t shown here.
          </p>
        )}
        {state === 'loading' ? (
          <Skeleton label="Loading output" />
        ) : state === 'error' ? (
          <div className="process-output-error" role="alert">
            <span>The output couldn&apos;t be read just now.</span>
            <Button className="small" onClick={() => setAttempt((n) => n + 1)}>
              Try again
            </Button>
          </div>
        ) : (
          <pre
            ref={logRef}
            className="process-log"
            role="log"
            aria-label="Output"
            tabIndex={0}
            onScroll={(event) => {
              const element = event.currentTarget;
              pinned.current =
                element.scrollHeight -
                  element.scrollTop -
                  element.clientHeight <
                24;
            }}
          >
            {text || (running ? 'Waiting for output…' : 'No output.')}
          </pre>
        )}
        <Disclosure summary="Details" className="process-output-details">
          <ProcessDetails view={view} conversation={conversation} />
        </Disclosure>
      </div>
    </Drawer>
  );
}

/**
 * A background process a step started, in the conversation: what runs, for
 * how long, where it serves, View output and Stop. It follows the process
 * live while it runs and settles on how it ended.
 */
export function ProcessCard({
  processId,
  conversationId,
}: {
  processId: string;
  conversationId?: string | null;
}) {
  const io = useContext(ProcessIoContext);
  const feed = useBackgroundProcesses(io, conversationId ?? undefined);
  const { want } = feed;
  useEffect(() => want(processId), [want, processId]);
  const view = feed.items.find((item) => item.process_id === processId);
  const [open, setOpen] = useOpenOutput(processId);
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState('');
  const running = view ? processLive(view) : false;
  const now = useNow(running);

  async function stop() {
    if (!io || !view || stopping) return;
    setStopping(true);
    setError('');
    try {
      feed.merge(await io.stop(view.process_id));
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setStopping(false);
    }
  }

  if (!io) return null;
  if (!view)
    return (
      <div
        className="transcript-card process-card"
        data-kind="process"
        data-state={feed.loaded ? 'unavailable' : 'loading'}
        role="group"
        aria-label="Background process"
      >
        <Terminal className="transcript-card-icon" aria-hidden />
        <div className="transcript-card-text">
          {feed.loaded && !feed.error ? (
            <span>This background process is no longer listed.</span>
          ) : feed.error ? (
            <span>Couldn&apos;t check this background process.</span>
          ) : (
            <Skeleton label="Checking the background process" />
          )}
        </div>
        {feed.error && (
          <div className="transcript-card-actions">
            <Button variant="ghost" onClick={feed.refresh}>
              Try again
            </Button>
          </div>
        )}
      </div>
    );
  const seconds = runningSeconds(view, feed.readAt, now);
  const status = processStatus(view, seconds);
  const name = processName(view);
  return (
    <div
      className="transcript-card process-card"
      data-kind="process"
      data-state={view.status}
      role="group"
      aria-label={`Background process ${name}: ${status.label}`}
    >
      <Terminal className="transcript-card-icon" aria-hidden />
      <div className="transcript-card-text process-card-text">
        {view.name ? (
          <strong>{view.name}</strong>
        ) : (
          <code className="process-card-command">{view.command}</code>
        )}
        <span className="process-card-meta">
          <StatusDot
            tone={status.tone}
            pulse={status.pulse}
            label={status.label}
            showLabel
          />
          {running && view.local_urls[0] && (
            <span className="process-address">
              Serving <code>{shortAddress(view.local_urls[0])}</code>
            </span>
          )}
        </span>
      </div>
      <div className="transcript-card-actions">
        <Button variant="ghost" onClick={() => setOpen(true)}>
          View output
        </Button>
        {running && (
          <Button
            disabled={stopping || view.status === 'stopping'}
            onClick={() => void stop()}
          >
            <Square size={12} aria-hidden />
            Stop
          </Button>
        )}
      </div>
      {error && (
        <p role="alert" className="transcript-card-error">
          {error}
        </p>
      )}
      <ProcessOutputDrawer
        view={view}
        io={io}
        open={open}
        onOpenChange={setOpen}
        onStop={() => void stop()}
        stopping={stopping}
        seconds={seconds}
      />
    </div>
  );
}
