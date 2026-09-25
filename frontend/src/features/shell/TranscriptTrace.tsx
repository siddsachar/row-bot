import { useEffect, useRef, useState } from 'react';
import { Check, CircleAlert, Clock3, Copy, Wrench } from 'lucide-react';
import type {
  TranscriptTraceGroup,
  TranscriptTraceItem,
} from '../../api/types';
import { useRuntime } from '../../runtime';
import { Button } from '../../ui/primitives';
import { MediaPreview } from './MediaPreview';

const ATTENTION = new Set(['failed', 'blocked', 'cancelled', 'uncertain']);
const MAX_RESULT_PAGES = 20;

function statusIcon(status: string) {
  if (status === 'pending') return <Clock3 aria-hidden="true" />;
  if (ATTENTION.has(status)) return <CircleAlert aria-hidden="true" />;
  return <Check aria-hidden="true" />;
}

function specialization(item: TranscriptTraceItem) {
  const value = item.specialization;
  if (!value) return null;
  if (value.kind === 'skill_load')
    return (
      <p className="trace-specialization">
        Skill · {value.display_name || value.skill_id}
        {value.newly_active ? ' activated' : ' already active'}
      </p>
    );
  if (value.kind === 'delegated_agent')
    return (
      <ul className="trace-specialization" aria-label="Delegated agent runs">
        {(value.agent_runs ?? []).map((run) => (
          <li key={run.run_id}>
            {run.display_name} · {run.status}
          </li>
        ))}
      </ul>
    );
  return (
    <div className="trace-specialization trace-media">
      <p>
        Media result · {value.media_kind || 'attachment'}
        {(value.media ?? []).length
          ? ` · ${(value.media ?? []).length} item${(value.media ?? []).length === 1 ? '' : 's'}`
          : ''}
      </p>
      {value.error_code ? (
        <p role="alert">The generated media is unavailable.</p>
      ) : null}
      {(value.media ?? []).map((media) => (
        <MediaPreview
          key={media.media_ref}
          reference={media.media_ref}
          mime={media.mime_type}
        />
      ))}
    </div>
  );
}

function TraceItem({
  conversation,
  item,
  number,
}: {
  conversation: string;
  item: TranscriptTraceItem;
  number: number;
}) {
  const { controller, platform } = useRuntime();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(item.safe_summary);
  const [cursor, setCursor] = useState<string | undefined>();
  const [pages, setPages] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [copyStatus, setCopyStatus] = useState('');
  const request = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const attempted = useRef(false);
  const details = useRef<HTMLDetailsElement>(null);
  const loadRef = useRef<(nextCursor?: string) => Promise<void>>(
    async () => {},
  );

  useEffect(() => {
    generation.current++;
    request.current?.abort();
    request.current = null;
    attempted.current = false;
    setText(item.safe_summary);
    setCursor(undefined);
    setPages(0);
    setBusy(false);
    setError('');
    setCopyStatus('');
    if (details.current?.open && item.content_ref) void loadRef.current();
    const activeRequest = request;
    const activeGeneration = generation;
    return () => {
      activeGeneration.current++;
      activeRequest.current?.abort();
    };
  }, [
    conversation,
    item.item_id,
    item.result_message_id,
    item.content_ref,
    item.safe_summary,
  ]);

  async function load(nextCursor?: string) {
    if (!item.content_ref || request.current) return;
    if (!nextCursor) attempted.current = true;
    const abort = new AbortController();
    const current = ++generation.current;
    request.current = abort;
    setBusy(true);
    setError('');
    try {
      const page = await controller.messageText(
        conversation,
        item.content_ref,
        nextCursor,
        abort.signal,
      );
      if (abort.signal.aborted || current !== generation.current) return;
      const value = new TextDecoder().decode(
        Uint8Array.from(atob(page.data), (character) =>
          character.charCodeAt(0),
        ),
      );
      setText((previous) => (nextCursor ? `${previous}${value}` : value));
      setCursor(page.has_more ? (page.next_cursor ?? undefined) : undefined);
      setPages((count) => count + 1);
    } catch {
      if (!abort.signal.aborted && current === generation.current)
        setError(
          'Public result could not be loaded. Showing the available text.',
        );
    } finally {
      if (current === generation.current) {
        request.current = null;
        setBusy(false);
      }
    }
  }
  loadRef.current = load;

  function toggle(isOpen: boolean) {
    setOpen(isOpen);
    if (isOpen && item.content_ref && !attempted.current && !request.current)
      void load();
    if (!isOpen) {
      attempted.current = false;
      if (request.current) {
        generation.current++;
        request.current.abort();
        request.current = null;
        setBusy(false);
      }
    }
  }

  async function copy() {
    try {
      setCopyStatus(
        (await platform.writeClipboard(text)).status === 'ok'
          ? 'Result copied.'
          : 'Copy is unavailable.',
      );
    } catch {
      setCopyStatus('Copy is unavailable.');
    }
  }

  return (
    <details
      ref={details}
      className="trace-item"
      data-trace-status={item.status}
      onToggle={(event) => toggle(event.currentTarget.open)}
    >
      <summary
        onClick={(event) =>
          toggle(
            !(event.currentTarget.parentElement as HTMLDetailsElement).open,
          )
        }
      >
        <span className="trace-call-number">{number}</span>
        <span className="trace-call-name">{item.canonical_name}</span>
        <span className="trace-status">{item.status}</span>
        {!open && item.safe_summary && (
          <span className="trace-preview">{item.safe_summary}</span>
        )}
      </summary>
      <div className="trace-item-body">
        {specialization(item)}
        {item.safe_input && (
          <div className="trace-input">
            <strong>Input</strong>
            <pre>{item.safe_input}</pre>
          </div>
        )}
        <div className="trace-result-heading">
          <strong>Result</strong>
          {text && (
            <Button variant="ghost" onClick={() => void copy()}>
              <Copy aria-hidden="true" /> Copy result
            </Button>
          )}
        </div>
        {text && <pre className="trace-output">{text}</pre>}
        {busy && <small role="status">Loading public result…</small>}
        {error && <p role="alert">{error}</p>}
        {error && item.content_ref && (
          <Button disabled={busy} onClick={() => void load(cursor)}>
            Retry public result
          </Button>
        )}
        {copyStatus && <small role="status">{copyStatus}</small>}
        {cursor && pages < MAX_RESULT_PAGES && (
          <Button disabled={busy} onClick={() => void load(cursor)}>
            Load next result page
          </Button>
        )}
        {cursor && pages >= MAX_RESULT_PAGES && (
          <small>Result display limited to {MAX_RESULT_PAGES} pages.</small>
        )}
      </div>
    </details>
  );
}

export default function TranscriptTrace({
  conversation,
  groups,
}: {
  conversation: string;
  groups: TranscriptTraceGroup[];
}) {
  return (
    <div className="transcript-traces" aria-label="Tool results">
      {groups.map((group) => (
        <details
          className="trace-group"
          data-trace-status={group.status}
          data-trace-kind={group.kind}
          key={group.group_id}
        >
          <summary>
            <span className="trace-group-icon">
              {group.status === 'succeeded' ? (
                <Wrench aria-hidden="true" />
              ) : (
                statusIcon(group.status)
              )}
            </span>
            <span className="trace-group-name">{group.name}</span>
            <span className="trace-count">
              {group.items.length} {group.items.length === 1 ? 'call' : 'calls'}
            </span>
            <span className="trace-status">{group.status}</span>
          </summary>
          <div className="trace-items">
            {[...group.items]
              .sort((first, second) => first.call_order - second.call_order)
              .map((item, index) => (
                <TraceItem
                  conversation={conversation}
                  item={item}
                  number={index + 1}
                  key={item.item_id}
                />
              ))}
          </div>
        </details>
      ))}
    </div>
  );
}
