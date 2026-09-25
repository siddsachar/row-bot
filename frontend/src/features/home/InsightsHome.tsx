import { useEffect, useState } from 'react';
import type { ClientController } from '../../api/controller';
import { readRetainedCommand, retainCommand } from '../../api/retained-command';
import type { InsightCommand, InsightsSnapshot } from '../../api/types';
import type { ClientPlatform } from '../../platform/types';
import { writeClipboardText } from '../../platform/clipboard';
import { Layers, Lightbulb, ListChecks } from 'lucide-react';
import {
  Button,
  EmptyState,
  EntityList,
  EntityRow,
  ErrorState,
  type Tone,
} from '../../ui/primitives';
import { absoluteTime, humanizeToken, relativeTime } from '../../ui/format';

type Action = InsightCommand['action'];

type FindingView = {
  title: string;
  meta: string;
  icon: 'overlap' | 'insight' | 'other';
  status?: { tone: Tone; label: string };
};

const plural = (count: number, word: string) =>
  `${count.toLocaleString()} ${word}${count === 1 ? '' : 's'}`;

/**
 * The API ships each curator finding as a bounded JSON string. Parse it and
 * describe known shapes in words; unknown shapes list their simple fields.
 * Raw JSON is never shown (B9).
 */
function describeFinding(raw: string): FindingView {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return { title: raw.slice(0, 240), meta: '', icon: 'other' };
  }
  if (!value || typeof value !== 'object' || Array.isArray(value))
    return { title: String(value), meta: '', icon: 'other' };
  const record = value as Record<string, unknown>;
  const text = (field: unknown) =>
    typeof field === 'string' || typeof field === 'number' ? String(field) : '';
  if (record.type === 'overlap' && Array.isArray(record.skill_names)) {
    const names = record.skill_names.map((name) => humanizeToken(text(name)));
    const score = Number(record.score);
    return {
      title: `${names.slice(0, 2).join(' and ') || 'Two skills'} overlap`,
      meta: Number.isFinite(score)
        ? `${Math.round(score * 100)}% similar instructions`
        : 'Similar instructions',
      icon: 'overlap',
      status: record.protected
        ? { tone: 'info', label: 'Protected · pinned or built in' }
        : undefined,
    };
  }
  if (record.type === 'skill_insight')
    return {
      title: text(record.title) || 'Skill insight',
      meta: humanizeToken(text(record.category)),
      icon: 'insight',
    };
  const details = Object.entries(record)
    .filter(([key, field]) => key !== 'type' && text(field))
    .slice(0, 4)
    .map(([key, field]) => `${humanizeToken(key)}: ${text(field)}`);
  return {
    title: humanizeToken(text(record.type)) || 'Finding',
    meta: details.join(' · '),
    icon: 'other',
  };
}

const findingIcons = {
  overlap: <Layers size={16} />,
  insight: <Lightbulb size={16} />,
  other: <ListChecks size={16} />,
};

export default function InsightsHome({
  controller,
  openConversation,
  writeClipboard,
}: {
  controller: ClientController;
  openConversation?: (id: string) => void;
  writeClipboard?: ClientPlatform['writeClipboard'];
}) {
  const [snapshot, setSnapshot] = useState<InsightsSnapshot | null>(null);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState(() => readRetainedCommand('insights'));
  const remember = (value: string) => {
    setPending(value);
    retainCommand('insights', value);
  };

  const refresh = () => {
    void controller
      .insights()
      .then(setSnapshot, (cause) => setError(String(cause)));
  };
  useEffect(() => {
    const abort = new AbortController();
    void controller.insights(abort.signal).then(
      (value) => {
        if (!abort.signal.aborted) setSnapshot(value);
      },
      (cause) => {
        if (!abort.signal.aborted) setError(String(cause));
      },
    );
    return () => abort.abort();
  }, [controller]);

  const run = async (action: Action, insightId = '', proposalId = '') => {
    if (!snapshot || busy || pending) return;
    const commandId = crypto.randomUUID();
    setBusy(true);
    remember(commandId);
    setError('');
    setMessage('');
    try {
      const result = await controller.executeInsight({
        command_id: commandId,
        revision: snapshot.revision,
        action,
        insight_id: insightId,
        proposal_id: proposalId,
        reason: '',
      });
      setSnapshot(result.snapshot);
      setMessage(result.summary);
      if (result.status !== 'uncertain') remember('');
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy(false);
    }
  };
  const recover = async () => {
    if (!pending || busy) return;
    setBusy(true);
    try {
      const result = await controller.insightReceipt(pending);
      setSnapshot(result.snapshot);
      setMessage(result.summary);
      setError('');
      if (result.status !== 'uncertain') remember('');
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="stack" aria-label="Insights">
      <div className="actions">
        <h2>Insights</h2>
        <Button disabled={busy} onClick={refresh}>
          Refresh
        </Button>
        <Button
          disabled={!snapshot || busy || Boolean(pending)}
          onClick={() => void run('review_skills')}
        >
          Analyze skill library
        </Button>
      </div>
      <p>
        Skill library review prepares proposals for your inspection. It does not
        apply them.
      </p>
      {error && (
        <ErrorState
          title="Insights unavailable"
          action={
            pending ? (
              <Button onClick={() => void recover()}>Check outcome</Button>
            ) : (
              <Button onClick={refresh}>Try again</Button>
            )
          }
        >
          {error}
        </ErrorState>
      )}
      {pending && !error && !busy && (
        <Button onClick={() => void recover()}>Check previous outcome</Button>
      )}
      {message && <p role="status">{message}</p>}
      {!snapshot && !error && <p role="status">Loading Insights…</p>}
      {snapshot?.items.length === 0 && (
        <EmptyState title="No active insights">
          New insights will appear here after analysis.
        </EmptyState>
      )}
      {snapshot?.curator_report && (
        <section
          className="card stack"
          aria-label="Latest skill library report"
        >
          <h3>Latest skill library report</h3>
          <p className="muted">
            {plural(snapshot.curator_report.manual_skill_count, 'manual skill')}{' '}
            · {plural(snapshot.curator_report.finding_count, 'finding')} ·{' '}
            {plural(snapshot.curator_report.proposal_count, 'proposal')}
            {snapshot.curator_report.created_at && (
              <>
                {' '}
                ·{' '}
                <time
                  dateTime={snapshot.curator_report.created_at}
                  title={absoluteTime(snapshot.curator_report.created_at)}
                >
                  {relativeTime(snapshot.curator_report.created_at)}
                </time>
              </>
            )}
          </p>
          {!!snapshot.curator_report.findings.length && (
            <EntityList label="Skill library findings">
              {snapshot.curator_report.findings.map((finding, index) => {
                const view = describeFinding(finding);
                return (
                  <EntityRow
                    key={`${index}:${finding}`}
                    title={view.title}
                    icon={findingIcons[view.icon]}
                    status={view.status}
                    meta={view.meta}
                  />
                );
              })}
            </EntityList>
          )}
        </section>
      )}
      {snapshot?.items.map((insight) => (
        <article className="card stack" key={insight.id}>
          <div className="actions">
            <h3>{insight.title}</h3>
            <span className="status-chip">{insight.severity}</span>
            <span className="status-chip">{insight.category}</span>
          </div>
          <p>{insight.body}</p>
          {insight.suggestion && <p>{insight.suggestion}</p>}
          <div className="actions">
            <Button
              disabled={busy || Boolean(pending)}
              onClick={() =>
                void run(
                  insight.status === 'pinned' ? 'unpin' : 'pin',
                  insight.id,
                )
              }
            >
              {insight.status === 'pinned' ? 'Unpin' : 'Pin'}
            </Button>
            <Button
              disabled={busy || Boolean(pending)}
              onClick={() => void run('dismiss', insight.id)}
            >
              Dismiss
            </Button>
            {insight.proposals.length === 0 && (
              <Button
                disabled={busy || Boolean(pending)}
                onClick={() => void run('generate', insight.id)}
              >
                Generate proposals
              </Button>
            )}
          </div>
          {insight.proposals.map((proposal) => {
            const terminal = [
              'applied',
              'verified',
              'rejected',
              'failed',
            ].includes(proposal.status);
            return (
              <details key={proposal.id} className="card stack">
                <summary>
                  {proposal.title} · {proposal.proposal_type} ·{' '}
                  {proposal.status}
                </summary>
                <p>Risk: {proposal.risk}</p>
                <p>{proposal.rationale}</p>
                <pre className="insight-preview">{proposal.preview}</pre>
                <p>Verification: {proposal.verification_plan}</p>
                {proposal.open_thread_id && openConversation && (
                  <Button
                    onClick={() => openConversation(proposal.open_thread_id)}
                  >
                    Open investigation draft
                  </Button>
                )}
                {proposal.feedback_body && !terminal && (
                  <Button
                    onClick={() =>
                      void writeClipboardText(
                        proposal.feedback_body,
                        writeClipboard,
                      ).then((ok) =>
                        setMessage(
                          ok ? 'Feedback copied.' : 'Clipboard unavailable.',
                        ),
                      )
                    }
                  >
                    Copy feedback
                  </Button>
                )}
                {proposal.support_url && terminal && (
                  <a
                    href={proposal.support_url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Open support destination
                  </a>
                )}
                {!terminal && (
                  <div className="actions">
                    <Button
                      disabled={busy || Boolean(pending)}
                      onClick={() => void run('apply', insight.id, proposal.id)}
                    >
                      Apply proposal
                    </Button>
                    <Button
                      disabled={busy || Boolean(pending)}
                      onClick={() =>
                        void run('reject', insight.id, proposal.id)
                      }
                    >
                      Reject proposal
                    </Button>
                  </div>
                )}
              </details>
            );
          })}
        </article>
      ))}
    </section>
  );
}
