import { FileText, Trash2 } from 'lucide-react';
import type { DocumentSummaryPage } from '../../api/types';
import { IconButton, Select, type Tone } from '../../ui/primitives';
import { absoluteTime, relativeTime } from '../../ui/format';
import { SavedCatalog, type SavedLoader } from './KnowledgeCatalog';

const statuses: Record<string, string> = {
  staging: 'Staging',
  queued: 'Queued',
  indexing: 'Indexing',
  searchable: 'Searchable',
  extracting: 'Extracting knowledge',
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
const recordStates: Record<string, string> = {
  removed: 'Removed from search; ingestion history retained',
  saved: 'Saved job and document record',
  job_only: 'Saved job only',
  record_only: 'Saved document record only',
  partial: 'Partial — completion records disagree or are missing',
};
const stages: Record<string, string> = {
  upload: 'Upload',
  parse: 'Parse',
  embed: 'Embedding',
  index_commit: 'Index commit',
  knowledge_map: 'Knowledge mapping',
  knowledge_reduce: 'Knowledge reduction',
  knowledge_commit: 'Knowledge commit',
  finalize: 'Finalization',
  unknown: 'Unknown',
};
function progress(current: number | null, total: number | null) {
  return `${current == null ? 'Unknown' : current.toLocaleString()} / ${total == null ? 'unknown' : total.toLocaleString()}`;
}

/**
 * Settings › Documents › Your documents (B258): one row per document with
 * its status as a dot and words, details on demand and Remove as an icon.
 */
export default function DocumentsCatalog({
  load,
  onRemove,
}: {
  load: SavedLoader<DocumentSummaryPage>;
  onRemove?: (id: string, label: string) => void;
}) {
  return (
    <SavedCatalog
      title="Your documents"
      noun="documents"
      load={load}
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
          {page.items.map((item) => (
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
                      data-pulse={working.has(item.status) ? 'true' : undefined}
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
                </summary>
                <div className="settings-document-detail">
                  {item.truncated && (
                    <p className="muted">This saved name is shortened.</p>
                  )}
                  <dl>
                    <dt>Record state</dt>
                    <dd>{recordStates[item.record_state] ?? 'Unknown'}</dd>
                    <dt>Saved status</dt>
                    <dd>{statuses[item.status] ?? 'Unknown'}</dd>
                    <dt>Saved stage</dt>
                    <dd>{stages[item.stage] ?? 'Unknown'}</dd>
                    <dt>Saved indexing progress</dt>
                    <dd>{progress(item.index_current, item.index_total)}</dd>
                    <dt>Saved extraction progress</dt>
                    <dd>
                      {progress(item.extraction_current, item.extraction_total)}
                    </dd>
                    <dt>Current searchability</dt>
                    <dd>Unknown</dd>
                    <dt>Last saved update</dt>
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
                  onClick={() => onRemove(item.id, item.name)}
                >
                  <Trash2 size={15} aria-hidden />
                </IconButton>
              )}
            </li>
          ))}
        </ul>
      )}
    />
  );
}
