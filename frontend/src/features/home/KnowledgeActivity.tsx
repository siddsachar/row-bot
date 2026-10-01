import { useEffect, useState, type ReactNode } from 'react';
import type {
  KnowledgeMemoryChangePage,
  KnowledgeRecallPage,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { Button } from '../../ui/primitives';
import { humanizeToken } from '../../ui/format';
import { When } from './home-format';

// A machine code ("needs_review", "high_authority_update") reads as words;
// free text (an extraction's explanation) is left as written (U59).
function codeWords(value: string) {
  return /^[a-z0-9]+(?:_[a-z0-9]+)*$/.test(value)
    ? humanizeToken(value).toLowerCase()
    : value;
}

/** One bounded log, read when the Activity view opens (and on refresh). */
function ActivityLog<P extends { availability: string }>({
  title,
  load,
  refreshKey,
  empty,
  render,
}: {
  title: string;
  load: (signal?: AbortSignal) => Promise<P>;
  refreshKey: string;
  empty: string;
  render: (page: P) => ReactNode[];
}) {
  const [page, setPage] = useState<P | null>(null);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    setError('');
    load(abort.signal).then(
      (value) => {
        if (!abort.signal.aborted) setPage(value);
      },
      (cause: unknown) => {
        if (!abort.signal.aborted) setError(clientError(cause).message);
      },
    );
    return () => abort.abort();
  }, [load, refreshKey, reload]);
  const rows = page?.availability === 'available' ? render(page) : [];
  return (
    <section className="knowledge-activity-log" aria-label={title}>
      <h4>{title}</h4>
      {!page && !error && (
        <p className="home-caption" role="status">
          Loading {title.toLowerCase()}…
        </p>
      )}
      {error && (
        <div className="task-builder-alert" role="alert">
          <strong>{title} could not be read</strong>
          <p>{error}</p>
          <Button
            className="small"
            onClick={() => setReload((value) => value + 1)}
          >
            Try again
          </Button>
        </div>
      )}
      {page &&
        page.availability !== 'available' &&
        page.availability !== 'missing' && (
          <p className="home-caption" role="status">
            This log is {humanizeToken(page.availability).toLowerCase()}.
          </p>
        )}
      {page &&
        (page.availability === 'missing' ||
          (page.availability === 'available' && !rows.length)) && (
          <p className="home-caption">{empty}</p>
        )}
      {rows.length > 0 && <ol className="knowledge-activity-list">{rows}</ol>}
    </section>
  );
}

/**
 * What changed in memory and what recall used, newest first (B264: moved
 * from Settings › Memory).
 */
export default function KnowledgeActivity({
  loadChanges,
  loadRecalls,
  refreshKey,
}: {
  loadChanges?: (signal?: AbortSignal) => Promise<KnowledgeMemoryChangePage>;
  loadRecalls?: (signal?: AbortSignal) => Promise<KnowledgeRecallPage>;
  refreshKey: string;
}) {
  return (
    <section
      className="knowledge-activity"
      aria-labelledby="knowledge-activity-title"
    >
      <header className="knowledge-view-head">
        <h3 id="knowledge-activity-title">Activity</h3>
        <p>
          Recent changes to saved memories, and what recall chose to use in
          conversations.
        </p>
      </header>
      {loadChanges && (
        <ActivityLog
          title="Memory changes"
          load={loadChanges}
          refreshKey={refreshKey}
          empty="No recent memory changes."
          render={(page) =>
            page.items.map((item, index) => (
              <li key={`${item.timestamp}:${index}`}>
                <strong>{humanizeToken(item.action)}</strong>
                <span className="knowledge-activity-meta">
                  <When value={item.timestamp} fallback="Unknown time" /> ·{' '}
                  {item.actor ? humanizeToken(item.actor) : 'Row-Bot'}
                </span>
                {item.subjects.length > 0 && (
                  <p>
                    {item.subjects.join(', ')}
                    {item.additional_subjects
                      ? ` +${item.additional_subjects} more`
                      : ''}
                  </p>
                )}
                {(item.old_status || item.new_status) && (
                  <p>
                    Status: {codeWords(item.old_status || 'unknown')} →{' '}
                    {codeWords(item.new_status || 'unknown')}
                  </p>
                )}
                {item.reason && <p>{codeWords(item.reason)}</p>}
              </li>
            ))
          }
        />
      )}
      {loadRecalls && (
        <ActivityLog
          title="Recall decisions"
          load={loadRecalls}
          refreshKey={refreshKey}
          empty="No recent recall decisions."
          render={(page) =>
            page.items.map((item, index) => (
              <li key={`${item.timestamp}:${index}`}>
                <strong>
                  {item.outcome === 'used' ? 'Memory used' : 'Memory skipped'}
                </strong>
                <span className="knowledge-activity-meta">
                  <When value={item.timestamp} fallback="Unknown time" /> ·{' '}
                  {item.selected_count} of {item.candidate_count} used ·{' '}
                  {item.context_characters.toLocaleString()} characters of
                  context
                </span>
                {item.reason && <p>{codeWords(item.reason)}</p>}
                {item.candidates.length > 0 && (
                  <p>
                    Candidates:{' '}
                    {item.candidates
                      .map(
                        (candidate) =>
                          `${candidate.subject}${candidate.score == null ? '' : ` (${candidate.score.toFixed(2)})`}`,
                      )
                      .join(', ')}
                  </p>
                )}
                {item.rejection_reasons.length > 0 && (
                  <p>
                    Skipped because:{' '}
                    {item.rejection_reasons.map(codeWords).join(', ')}
                  </p>
                )}
              </li>
            ))
          }
        />
      )}
    </section>
  );
}
