import { useCallback, useEffect, useRef, useState } from 'react';
import { FileText, Trash2 } from 'lucide-react';
import type { DocumentSummaryPage } from '../../api/types';
import { IconButton, Select, type Tone } from '../../ui/primitives';
import { absoluteTime, relativeTime } from '../../ui/format';
import { documentFailure } from '../knowledge/document-words';
import { SavedCatalog, type SavedLoader } from './KnowledgeCatalog';

type DocumentSummary = DocumentSummaryPage['items'][number];

const statuses: Record<string, string> = {
  staging: 'Uploading',
  queued: 'Queued',
  indexing: 'Reading',
  searchable: 'Searchable',
  extracting: 'Finding knowledge',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
  skipped_duplicate: 'Skipped duplicate',
  unknown: 'Unknown',
};
/** A document's dot: working (pulses), ready, failed or stopped. */
const statusTones: Record<string, Tone> = {
  staging: 'info',
  queued: 'info',
  indexing: 'info',
  extracting: 'info',
  searchable: 'success',
  completed: 'success',
  failed: 'danger',
};
const working = new Set(['staging', 'queued', 'indexing', 'extracting']);
const finishedUnsearchable = new Set([
  'failed',
  'cancelled',
  'skipped_duplicate',
]);
/** Only the saved states a person can act on get a sentence. */
const recordNotes: Record<string, string> = {
  removed: 'Removed from search. Its history is kept.',
  partial:
    "Row-Bot couldn't confirm this file finished. Try it again, or remove it.",
};
/** How often the list reads itself again while documents are being added. */
export const DOCUMENTS_POLL_MS = 3000;

function progress(label: string, current: number | null, total: number | null) {
  if (!total) return null;
  return (
    <>
      <dt>{label}</dt>
      <dd>
        {(current ?? 0).toLocaleString()} of {total.toLocaleString()}
      </dd>
    </>
  );
}

/** Never reached search: Remove only takes it off this list. */
export function listOnlyDocument(item: DocumentSummary) {
  return (
    item.record_state === 'job_only' && finishedUnsearchable.has(item.status)
  );
}

/**
 * Settings › Documents › Your documents (B258): one row per document with
 * its status as a dot and words, why it stopped when it did, details on
 * demand and Remove as an icon. While documents are being added the list
 * reads itself again, so their states move on without a refresh.
 */
export default function DocumentsCatalog({
  load,
  onRemove,
}: {
  load: SavedLoader<DocumentSummaryPage>;
  onRemove?: (
    id: string,
    label: string,
    options: { listOnly: boolean },
  ) => void;
}) {
  // Newer saved states for the rows on show, read while work is active.
  const [fresh, setFresh] = useState<ReadonlyMap<string, DocumentSummary>>(
    () => new Map(),
  );
  const [active, setActive] = useState(false);
  const shown = useRef<{ query?: string; status?: string } | null>(null);
  const observed = useCallback<SavedLoader<DocumentSummaryPage>>(
    async (query, status, cursor, signal) => {
      const page = await load(query, status, cursor, signal);
      if (!cursor) {
        shown.current = { query, status };
        setFresh(new Map());
        setActive(page.items.some((item) => working.has(item.status)));
      }
      return page;
    },
    [load],
  );
  useEffect(() => {
    if (!active) return;
    const abort = new AbortController();
    const timer = setInterval(() => {
      const filter = shown.current;
      if (!filter) return;
      load(filter.query, filter.status, undefined, abort.signal).then(
        (page) => {
          if (abort.signal.aborted) return;
          setFresh(new Map(page.items.map((item) => [item.id, item])));
          setActive(page.items.some((item) => working.has(item.status)));
        },
        () => {
          if (!abort.signal.aborted) setActive(false);
        },
      );
    }, DOCUMENTS_POLL_MS);
    return () => {
      clearInterval(timer);
      abort.abort();
    };
  }, [active, load]);
  return (
    <SavedCatalog
      title="Your documents"
      noun="documents"
      load={observed}
      headless
      description="Saved document and ingestion records."
      filter={(selected, change) => (
        <Select
          aria-label="Saved document status"
          value={selected}
          onChange={(event) => change(event.target.value)}
        >
          <option value="">All statuses</option>
          {Object.entries(statuses).map(([value, label]) => (
            <option value={value} key={value}>
              {label}
            </option>
          ))}
        </Select>
      )}
      renderItems={(page) => (
        <ul className="settings-results settings-document-results">
          {page.items.map((saved) => {
            const item = fresh.get(saved.id) ?? saved;
            const failure = documentFailure(item.status, item.error_code);
            const note = recordNotes[item.record_state];
            return (
              <li
                className="settings-document-result settings-divided"
                key={item.id}
              >
                <span className="settings-row-icon" data-tone="1" aria-hidden>
                  <FileText size={16} aria-hidden />
                </span>
                <details>
                  <summary>
                    <span className="settings-document-name">{item.name}</span>
                    <span
                      className="status-indicator"
                      data-tone={statusTones[item.status]}
                    >
                      <span
                        className="status-indicator-dot"
                        data-pulse={
                          working.has(item.status) ? 'true' : undefined
                        }
                        aria-hidden
                      />
                      <span>{statuses[item.status] ?? 'Unknown'}</span>
                    </span>
                    {item.updated_at ? (
                      <small>
                        <time
                          dateTime={item.updated_at}
                          title={absoluteTime(item.updated_at)}
                        >
                          {relativeTime(item.updated_at)}
                        </time>
                      </small>
                    ) : null}
                    {failure && (
                      <span className="settings-document-reason">
                        {failure}
                      </span>
                    )}
                  </summary>
                  <div className="settings-document-detail">
                    {item.truncated && (
                      <p className="muted">This name is shortened.</p>
                    )}
                    {note && <p>{note}</p>}
                    {listOnlyDocument(item) && (
                      <p className="muted">
                        It never reached search. Remove takes it off this list.
                      </p>
                    )}
                    <dl>
                      {progress('Read', item.index_current, item.index_total)}
                      {progress(
                        'Knowledge found',
                        item.extraction_current,
                        item.extraction_total,
                      )}
                      <dt>Updated</dt>
                      <dd>
                        {item.updated_at
                          ? absoluteTime(item.updated_at) || item.updated_at
                          : 'Unknown'}
                      </dd>
                    </dl>
                  </div>
                </details>
                {onRemove && (
                  <IconButton
                    size="sm"
                    label={`Remove ${item.name}`}
                    onClick={() =>
                      onRemove(item.id, item.name, {
                        listOnly: listOnlyDocument(item),
                      })
                    }
                  >
                    <Trash2 size={15} aria-hidden />
                  </IconButton>
                )}
              </li>
            );
          })}
        </ul>
      )}
    />
  );
}
