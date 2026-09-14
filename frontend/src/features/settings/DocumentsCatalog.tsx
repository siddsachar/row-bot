import type { DocumentSummaryPage } from '../../api/types';
import { Button, Field, Select } from '../../ui/primitives';
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

export default function DocumentsCatalog({
  load,
  onRemove,
}: {
  load: SavedLoader<DocumentSummaryPage>;
  onRemove?: (id: string, label: string) => void;
}) {
  return (
    <SavedCatalog
      title="Indexed Documents"
      noun="documents"
      load={load}
      description="Browse saved document and ingestion records. Status and progress are historical; current searchability is unknown."
      filter={(selected, change) => (
        <Field label="Saved document status">
          <Select
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
        </Field>
      )}
      renderItems={(page) => (
        <ul className="settings-results">
          {page.items.map((item) => (
            <li className="surface" key={item.id}>
              <details>
                <summary>{item.name}</summary>
                {item.truncated && (
                  <p className="muted">This saved name is shortened.</p>
                )}
                <dl>
                  <dt>Record state</dt>
                  <dd>{recordStates[item.record_state] ?? 'Unknown'}</dd>
                  <dt>Saved identity</dt>
                  <dd>{item.id}</dd>
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
                  <dd>{item.updated_at || 'Unknown'}</dd>
                </dl>
              </details>
              {onRemove && (
                <Button onClick={() => onRemove(item.id, item.name)}>
                  Remove {item.name}
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    />
  );
}
