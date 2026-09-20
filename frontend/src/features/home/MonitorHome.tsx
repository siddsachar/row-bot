import { useState } from 'react';
import { BedDouble, BookOpenText, FileText, RefreshCw } from 'lucide-react';
import { ModalTask } from '../../ui/overlays';
import { Button, EmptyState, ErrorState, Skeleton } from '../../ui/primitives';

export type MonitorAvailability =
  'available' | 'missing' | 'unavailable' | 'corrupt';

export type ExtractionJournalEntry = {
  timestamp: string;
  summary: string;
  contradictions_blocked: number;
  low_confidence_skipped: number;
  islands_repaired: number;
  threads: Array<{ label: string; extracted: number; saved: number }>;
  errors: string[];
};

export type DreamJournalEntry = {
  timestamp: string;
  summary: string;
  merges: Array<{
    duplicate_subject: string;
    survivor_subject: string;
    score: number | null;
  }>;
  enrichments: Array<{
    subject: string;
    old_length: number;
    new_length: number;
    new_description: string;
  }>;
  inferred_relations: Array<{
    source_subject: string;
    target_subject: string;
    relation_type: string;
    confidence: number | null;
    evidence: string;
  }>;
  errors: string[];
};

export type MonitorLogEntry = {
  timestamp: string;
  level: string;
  logger: string;
  message: string;
  exception: string;
};

export type MonitorSnapshot = {
  extraction: {
    availability: MonitorAvailability;
    last_run: string | null;
    interval_hours: number;
    threads_scanned: number;
    entities_saved: number;
    islands_repaired: number;
  };
  extraction_journal: ExtractionJournalEntry[];
  dream: {
    availability: MonitorAvailability;
    enabled: boolean;
    window: string;
    last_run: string | null;
    last_summary: string | null;
    recent: Array<{ timestamp: string; summary: string }>;
  };
  dream_journal: DreamJournalEntry[];
  logs: {
    availability: MonitorAvailability;
    authorized: boolean;
    entries: MonitorLogEntry[];
    full_available: boolean;
  };
};

export type MonitorHomeProps = {
  snapshot: MonitorSnapshot | null;
  loading: boolean;
  error?: string | null;
  onRefresh: () => void;
  onLoadFullLogs: () => void;
  fullLogsOpen: boolean;
  fullLogsLoading?: boolean;
  fullLogsError?: string | null;
  fullLogEntries: MonitorLogEntry[];
  onCloseFullLogs: () => void;
};

const MAX_JOURNAL_ENTRIES = 20;
const MAX_RECENT_LOGS = 15;
const MAX_FULL_LOGS = 200;

function formatTimestamp(value: string, includeTime = true) {
  if (!value) return 'Unknown time';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    ...(includeTime
      ? { hour: 'numeric' as const, minute: '2-digit' as const }
      : {}),
  }).format(parsed);
}

function availabilityCopy(subject: string, availability: MonitorAvailability) {
  if (availability === 'missing')
    return `${subject} has not produced status yet.`;
  if (availability === 'corrupt')
    return `${subject} status could not be read safely.`;
  return `${subject} status is unavailable.`;
}

function JournalDialog({
  open,
  title,
  description,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  description: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <ModalTask
      open={open}
      title={title}
      description={description}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <div className="stack">{children}</div>
      <div className="actions">
        <Button onClick={onClose}>Close</Button>
      </div>
    </ModalTask>
  );
}

function ExtractionJournal({ entries }: { entries: ExtractionJournalEntry[] }) {
  const bounded = entries.slice(-MAX_JOURNAL_ENTRIES).reverse();
  if (!bounded.length)
    return (
      <EmptyState title="No extraction entries">
        Knowledge extraction has not recorded a journal entry yet.
      </EmptyState>
    );
  return (
    <div className="stack">
      {bounded.map((entry, index) => (
        <details className="card" key={`${entry.timestamp}-${index}`}>
          <summary>
            {formatTimestamp(entry.timestamp)} — {entry.summary || 'No summary'}
          </summary>
          <div className="stack">
            <p className="muted">
              {entry.contradictions_blocked} contradictions blocked ·{' '}
              {entry.low_confidence_skipped} low-confidence skipped ·{' '}
              {entry.islands_repaired} islands repaired
            </p>
            {entry.threads.length > 0 && (
              <ul>
                {entry.threads.map((thread, threadIndex) => (
                  <li key={`${thread.label}-${threadIndex}`}>
                    {thread.label}: extracted {thread.extracted}, saved{' '}
                    {thread.saved}
                  </li>
                ))}
              </ul>
            )}
            {entry.errors.length > 0 && (
              <div role="alert">
                <strong>Errors ({entry.errors.length})</strong>
                <ul>
                  {entry.errors.map((message, errorIndex) => (
                    <li key={`${message}-${errorIndex}`}>{message}</li>
                  ))}
                </ul>
              </div>
            )}
            {!entry.threads.length && !entry.errors.length && (
              <p className="muted">No details available.</p>
            )}
          </div>
        </details>
      ))}
    </div>
  );
}

function DreamJournal({ entries }: { entries: DreamJournalEntry[] }) {
  const bounded = entries.slice(-MAX_JOURNAL_ENTRIES).reverse();
  if (!bounded.length)
    return (
      <EmptyState title="No Dream Cycle entries">
        Dream Cycle has not recorded a journal entry yet.
      </EmptyState>
    );
  return (
    <div className="stack">
      {bounded.map((entry, index) => {
        const hasChanges =
          entry.merges.length > 0 ||
          entry.enrichments.length > 0 ||
          entry.inferred_relations.length > 0;
        return (
          <details className="card" key={`${entry.timestamp}-${index}`}>
            <summary>
              {formatTimestamp(entry.timestamp)} —{' '}
              {entry.summary || 'No summary'}
            </summary>
            <div className="stack">
              {entry.merges.length > 0 && (
                <section aria-label="Merges">
                  <strong>Merges ({entry.merges.length})</strong>
                  <ul>
                    {entry.merges.map((merge, mergeIndex) => (
                      <li key={`${merge.duplicate_subject}-${mergeIndex}`}>
                        {merge.duplicate_subject} → {merge.survivor_subject}{' '}
                        (score {merge.score})
                      </li>
                    ))}
                  </ul>
                </section>
              )}
              {entry.enrichments.length > 0 && (
                <section aria-label="Enrichments">
                  <strong>Enrichments ({entry.enrichments.length})</strong>
                  <ul>
                    {entry.enrichments.map((enrichment, enrichmentIndex) => (
                      <li key={`${enrichment.subject}-${enrichmentIndex}`}>
                        {enrichment.subject} ({enrichment.old_length} →{' '}
                        {enrichment.new_length} characters)
                        {enrichment.new_description && (
                          <p className="muted">
                            {enrichment.new_description.slice(0, 150)}
                            {enrichment.new_description.length > 150 ? '…' : ''}
                          </p>
                        )}
                      </li>
                    ))}
                  </ul>
                </section>
              )}
              {entry.inferred_relations.length > 0 && (
                <section aria-label="Inferred relations">
                  <strong>
                    Inferred relations ({entry.inferred_relations.length})
                  </strong>
                  <ul>
                    {entry.inferred_relations.map((relation, relationIndex) => (
                      <li
                        key={`${relation.source_subject}-${relation.target_subject}-${relationIndex}`}
                      >
                        {relation.source_subject} —[{relation.relation_type}]→{' '}
                        {relation.target_subject}
                        {relation.confidence === null
                          ? ' (confidence unknown)'
                          : ` (confidence ${relation.confidence.toFixed(2)})`}
                        {relation.evidence && (
                          <p className="muted">
                            Evidence: {relation.evidence.slice(0, 120)}
                            {relation.evidence.length > 120 ? '…' : ''}
                          </p>
                        )}
                      </li>
                    ))}
                  </ul>
                </section>
              )}
              {entry.errors.length > 0 && (
                <section aria-label="Dream Cycle errors" role="alert">
                  <strong>Errors ({entry.errors.length})</strong>
                  <ul>
                    {entry.errors.map((message, errorIndex) => (
                      <li key={`${message}-${errorIndex}`}>{message}</li>
                    ))}
                  </ul>
                </section>
              )}
              {!hasChanges && !entry.errors.length && (
                <p className="muted">No changes this cycle.</p>
              )}
            </div>
          </details>
        );
      })}
    </div>
  );
}

function LogLines({
  entries,
  full = false,
}: {
  entries: MonitorLogEntry[];
  full?: boolean;
}) {
  if (!entries.length) return <p className="muted">No log entries yet.</p>;
  const limit = full ? MAX_FULL_LOGS : MAX_RECENT_LOGS;
  return (
    <ol
      className="stack"
      aria-label={full ? 'Full log entries' : 'Recent logs'}
    >
      {entries.slice(-limit).map((entry, index) => {
        const message =
          !full && entry.message.length > 120
            ? `${entry.message.slice(0, 120)}…`
            : entry.message;
        return (
          <li className="card" key={`${entry.timestamp}-${index}`}>
            <code>
              {entry.timestamp} [{entry.level}]
              {full && entry.logger ? ` [${entry.logger}]` : ''} {message}
              {full && entry.exception ? `\n${entry.exception}` : ''}
            </code>
          </li>
        );
      })}
    </ol>
  );
}

export default function MonitorHome({
  snapshot,
  loading,
  error,
  onRefresh,
  onLoadFullLogs,
  fullLogsOpen,
  fullLogsLoading = false,
  fullLogsError,
  fullLogEntries,
  onCloseFullLogs,
}: MonitorHomeProps) {
  const [extractionJournalOpen, setExtractionJournalOpen] = useState(false);
  const [dreamJournalOpen, setDreamJournalOpen] = useState(false);

  return (
    <section className="stack" aria-labelledby="monitor-heading">
      <header className="capability-header">
        <div>
          <h2 id="monitor-heading">System Monitor</h2>
          <p>
            Passive status for local knowledge maintenance and application
            diagnostics.
          </p>
        </div>
        <Button variant="primary" onClick={onRefresh} disabled={loading}>
          <RefreshCw size={17} aria-hidden />
          {loading ? 'Refreshing…' : 'Refresh'}
        </Button>
      </header>

      {error && (
        <ErrorState
          title="Monitor refresh failed"
          action={<Button onClick={onRefresh}>Try again</Button>}
        >
          {error}
        </ErrorState>
      )}
      {loading && <Skeleton label="Loading System Monitor" />}
      {!snapshot && !loading && !error && (
        <EmptyState title="Monitor unavailable">
          No passive monitor snapshot is available.
        </EmptyState>
      )}

      {snapshot && (
        <>
          <section
            className="capability-section"
            aria-labelledby="extraction-heading"
          >
            <div className="section-heading">
              <div>
                <h3 id="extraction-heading">
                  <BookOpenText size={18} aria-hidden /> Knowledge Extraction
                </h3>
              </div>
              <Button onClick={() => setExtractionJournalOpen(true)}>
                View Journal
              </Button>
            </div>
            {snapshot.extraction.availability !== 'available' ? (
              <p className="muted">
                {availabilityCopy(
                  'Knowledge extraction',
                  snapshot.extraction.availability,
                )}
              </p>
            ) : snapshot.extraction.last_run ? (
              <div className="stack">
                <p>
                  Last run: {formatTimestamp(snapshot.extraction.last_run)} ·
                  Runs every {snapshot.extraction.interval_hours}h
                </p>
                <div
                  className="capability-summary"
                  aria-label="Extraction summary"
                >
                  <span>
                    {snapshot.extraction.threads_scanned} threads scanned
                  </span>
                  <span>
                    {snapshot.extraction.entities_saved} entities saved
                  </span>
                  <span>
                    {snapshot.extraction.islands_repaired} islands repaired
                  </span>
                </div>
              </div>
            ) : (
              <p className="muted">Not yet run — starts automatically.</p>
            )}
          </section>

          <section
            className="capability-section"
            aria-labelledby="dream-heading"
          >
            <div className="section-heading">
              <div>
                <h3 id="dream-heading">
                  <BedDouble size={18} aria-hidden /> Dream Cycle
                </h3>
              </div>
              {snapshot.dream.availability === 'available' &&
                snapshot.dream.enabled && (
                  <Button onClick={() => setDreamJournalOpen(true)}>
                    View Journal
                  </Button>
                )}
            </div>
            {snapshot.dream.availability !== 'available' ? (
              <p className="muted">
                {availabilityCopy('Dream Cycle', snapshot.dream.availability)}
              </p>
            ) : !snapshot.dream.enabled ? (
              <p className="muted">
                Disabled — enable Dream Cycle in Settings → Preferences.
              </p>
            ) : (
              <div className="stack">
                <p>Window: {snapshot.dream.window}</p>
                {snapshot.dream.last_run ? (
                  <p>
                    Last run: {formatTimestamp(snapshot.dream.last_run)}
                    {snapshot.dream.last_summary
                      ? ` — ${snapshot.dream.last_summary}`
                      : ''}
                  </p>
                ) : (
                  <p className="muted">
                    No dream cycles yet — runs during idle hours.
                  </p>
                )}
                {snapshot.dream.recent.length > 0 && (
                  <ul aria-label="Recent Dream Cycle entries">
                    {snapshot.dream.recent
                      .slice(-3)
                      .reverse()
                      .map((entry, index) => (
                        <li key={`${entry.timestamp}-${index}`}>
                          {formatTimestamp(entry.timestamp, false)} —{' '}
                          {entry.summary}
                        </li>
                      ))}
                  </ul>
                )}
              </div>
            )}
          </section>

          <section
            className="capability-section"
            aria-labelledby="logs-heading"
          >
            <div className="section-heading">
              <div>
                <h3 id="logs-heading">
                  <FileText size={18} aria-hidden /> Recent Logs
                </h3>
              </div>
              <div className="actions">
                <Button onClick={onRefresh} disabled={loading}>
                  <RefreshCw size={16} aria-hidden /> Refresh logs
                </Button>
                {snapshot.logs.availability === 'available' &&
                  snapshot.logs.authorized &&
                  snapshot.logs.full_available && (
                    <Button onClick={onLoadFullLogs}>View Full Log</Button>
                  )}
              </div>
            </div>
            {!snapshot.logs.authorized ||
            snapshot.logs.availability === 'unavailable' ? (
              <EmptyState title="Logs unavailable">
                Logs are available only to the local owner in the native
                application. No log contents or private filesystem paths are
                exposed to this client.
              </EmptyState>
            ) : snapshot.logs.availability === 'missing' ? (
              <p className="muted">No local log is available yet.</p>
            ) : snapshot.logs.availability === 'corrupt' ? (
              <ErrorState title="Recent logs unavailable">
                The local log could not be read safely.
              </ErrorState>
            ) : (
              <LogLines entries={snapshot.logs.entries} />
            )}
          </section>
        </>
      )}

      <JournalDialog
        open={extractionJournalOpen}
        title="Extraction Journal"
        description="The 20 most recent bounded extraction records."
        onClose={() => setExtractionJournalOpen(false)}
      >
        <ExtractionJournal entries={snapshot?.extraction_journal ?? []} />
      </JournalDialog>

      <JournalDialog
        open={dreamJournalOpen}
        title="Dream Cycle Journal"
        description="The 20 most recent bounded Dream Cycle records."
        onClose={() => setDreamJournalOpen(false)}
      >
        <DreamJournal entries={snapshot?.dream_journal ?? []} />
      </JournalDialog>

      <ModalTask
        open={fullLogsOpen}
        title="Log Viewer"
        description="Up to 200 redacted log entries from this local application."
        onOpenChange={(next) => {
          if (!next) onCloseFullLogs();
        }}
      >
        <div className="stack">
          {fullLogsLoading && <Skeleton label="Loading full log" />}
          {fullLogsError && (
            <ErrorState title="Full log unavailable">
              {fullLogsError}
            </ErrorState>
          )}
          {!fullLogsLoading && !fullLogsError && (
            <LogLines entries={fullLogEntries} full />
          )}
        </div>
        <div className="actions">
          <Button onClick={onCloseFullLogs}>Close</Button>
        </div>
      </ModalTask>
    </section>
  );
}
