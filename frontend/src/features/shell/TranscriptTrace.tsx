import { useEffect, useState } from 'react';
import type {
  TranscriptTraceGroup,
  TranscriptTraceItem,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import { Button } from '../../ui/primitives';

const ATTENTION = new Set(['failed', 'blocked', 'cancelled', 'uncertain']);

function label(group: TranscriptTraceGroup) {
  if (group.status === 'pending') return `Using ${group.name}`;
  const prefix = ATTENTION.has(group.status) ? 'Needs attention' : 'Done';
  return `${prefix} ${group.name}${group.items.length > 1 ? ` · ${group.items.length}` : ''}`;
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
    <p className="trace-specialization">
      Media result · {value.media_kind || 'attachment'}
      {(value.media ?? []).length
        ? ` · ${(value.media ?? []).length} item${(value.media ?? []).length === 1 ? '' : 's'}`
        : ''}
    </p>
  );
}

function TraceItem({
  conversation,
  item,
}: {
  conversation: string;
  item: TranscriptTraceItem;
}) {
  const { controller } = useRuntime();
  const [text, setText] = useState(item.safe_summary);
  const [cursor, setCursor] = useState<string | undefined>();
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    setText(item.safe_summary);
    setCursor(undefined);
    setLoaded(false);
    setError('');
  }, [item.item_id, item.result_message_id, item.safe_summary]);
  async function load() {
    if (!item.content_ref || busy) return;
    setBusy(true);
    try {
      const page = await controller.messageText(
        conversation,
        item.content_ref,
        cursor,
      );
      const value = new TextDecoder().decode(
        Uint8Array.from(atob(page.data), (character) =>
          character.charCodeAt(0),
        ),
      );
      setText((current) => (cursor ? `${current}${value}` : value));
      setCursor(page.next_cursor ?? undefined);
      setLoaded(true);
      setError('');
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <details className="trace-item" data-trace-status={item.status}>
      <summary>
        {item.canonical_name} · {item.status}
      </summary>
      {specialization(item)}
      {text ? <pre className="trace-output">{text}</pre> : null}
      {item.summary_truncated && !loaded && (
        <small>Summary truncated. Load the public result to review more.</small>
      )}
      {item.content_ref && (!loaded || cursor) && (
        <Button disabled={busy} onClick={() => void load()}>
          {cursor ? 'Load next result page' : 'Load public result'}
        </Button>
      )}
      {error && <p role="alert">{error}</p>}
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
          <summary>{label(group)}</summary>
          <div className="trace-items">
            {group.items.map((item) => (
              <TraceItem
                conversation={conversation}
                item={item}
                key={item.item_id}
              />
            ))}
          </div>
        </details>
      ))}
    </div>
  );
}
