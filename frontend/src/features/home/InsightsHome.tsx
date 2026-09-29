import { useEffect, useMemo, useRef, useState } from 'react';
import type { ClientController } from '../../api/controller';
import { readRetainedCommand, retainCommand } from '../../api/retained-command';
import type {
  InsightCommand,
  InsightProposalView,
  InsightView,
  InsightsSnapshot,
} from '../../api/types';
import type { ClientPlatform } from '../../platform/types';
import { writeClipboardText } from '../../platform/clipboard';
import {
  AlertOctagon,
  AlertTriangle,
  ChevronDown,
  Info,
  Layers,
  Lightbulb,
  ListChecks,
  Pin,
  PinOff,
  RefreshCw,
  X,
} from 'lucide-react';
import {
  Button,
  EntityList,
  EntityRow,
  ErrorState,
  IconButton,
  InlineEmpty,
  Segmented,
  type Tone,
} from '../../ui/primitives';
import { absoluteTime, humanizeToken, relativeTime } from '../../ui/format';
import { useNotify } from '../../ui/overlays';

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

const SEVERITY: Record<
  string,
  { label: string; tone: Tone; icon: typeof Info }
> = {
  critical: { label: 'Critical', tone: 'danger', icon: AlertOctagon },
  error: { label: 'Error', tone: 'danger', icon: AlertOctagon },
  warning: { label: 'Warning', tone: 'warning', icon: AlertTriangle },
  info: { label: 'Info', tone: 'info', icon: Info },
  suggestion: { label: 'Suggestion', tone: 'accent', icon: Lightbulb },
};

function severity(value: string) {
  return (
    SEVERITY[value.trim().toLowerCase()] ?? {
      label: humanizeToken(value) || 'Info',
      tone: 'info' as Tone,
      icon: Info,
    }
  );
}

const PROPOSAL_TYPES: Record<string, string> = {
  investigate: 'Investigate',
  create_skill: 'New skill',
  patch_skill: 'Skill change',
  consolidate_skills: 'Merge skills',
  send_feedback: 'Send feedback',
  settings_change: 'Settings change',
  memory_correction: 'Memory correction',
};
const TERMINAL = new Set(['applied', 'verified', 'rejected', 'failed']);

/** The server's receipt names the new state ("Insight new."); say what happened. */
const ACTION_NOTICES: Partial<Record<Action, string>> = {
  pin: 'Pinned.',
  unpin: 'Unpinned.',
  dismiss: 'Dismissed.',
  restore: 'Restored.',
};

function proposalType(value: string) {
  return PROPOSAL_TYPES[value] ?? humanizeToken(value);
}

/** The proposal preview as labelled fields rather than a JSON string. */
function PreviewFields({ preview }: { preview: string }) {
  let value: unknown;
  try {
    value = JSON.parse(preview);
  } catch {
    return preview ? <pre className="insight-preview">{preview}</pre> : null;
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const entries = Object.entries(value as Record<string, unknown>).filter(
    ([, field]) => field !== null && field !== '' && field !== undefined,
  );
  if (!entries.length) return null;
  return (
    <dl className="insight-preview-fields">
      {entries.map(([key, field]) => (
        <div key={key}>
          <dt>{humanizeToken(key)}</dt>
          <dd>
            {typeof field === 'string' || typeof field === 'number' ? (
              String(field).length > 160 ? (
                <pre className="insight-preview">{String(field)}</pre>
              ) : (
                String(field)
              )
            ) : typeof field === 'boolean' ? (
              field ? (
                'Yes'
              ) : (
                'No'
              )
            ) : (
              <pre className="insight-preview">
                {JSON.stringify(field, null, 2)}
              </pre>
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}

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
  const notify = useNotify();
  // The latest run(), so an Undo uses the current revision.
  const runner = useRef<
    | ((
        action: Action,
        insightId?: string,
        proposalId?: string,
      ) => Promise<void>)
    | null
  >(null);
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState<'all' | 'pinned'>('all');
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const [pending, setPending] = useState(() => readRetainedCommand('insights'));
  const remember = (value: string) => {
    setPending(value);
    retainCommand('insights', value);
  };

  const refresh = () => {
    setError('');
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
      setMessage(
        result.status === 'completed' && ACTION_NOTICES[action]
          ? ACTION_NOTICES[action]
          : result.summary,
      );
      // Dismissing is easy to regret: the notice offers Undo (decision 19).
      if (action === 'dismiss' && result.status === 'completed')
        notify('Insight dismissed.', undefined, {
          label: 'Undo',
          onAction: () => void runner.current?.('restore', insightId),
        });
      if (result.status !== 'uncertain') remember('');
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy(false);
    }
  };
  runner.current = run;
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
  const locked = busy || Boolean(pending);
  const toggle = (id: string, open?: boolean) =>
    setExpanded((current) => {
      const next = new Set(current);
      if (open ?? !next.has(id)) next.add(id);
      else next.delete(id);
      return next;
    });
  const copyFeedback = (proposal: InsightProposalView) =>
    void writeClipboardText(proposal.feedback_body, writeClipboard).then((ok) =>
      setMessage(ok ? 'Feedback copied.' : 'Clipboard unavailable.'),
    );

  const items = useMemo(
    () =>
      (snapshot?.items ?? []).filter(
        (insight) => filter === 'all' || insight.status === 'pinned',
      ),
    [filter, snapshot?.items],
  );
  const pinned =
    snapshot?.items.filter((insight) => insight.status === 'pinned').length ??
    0;

  function suggested(insight: InsightView) {
    const open = insight.proposals.find(
      (proposal) =>
        proposal.open_thread_id && proposal.proposal_type === 'investigate',
    );
    if (open && openConversation)
      return (
        <Button
          className="small"
          onClick={() => openConversation(open.open_thread_id)}
        >
          Open investigation
        </Button>
      );
    const next = insight.proposals.find(
      (proposal) => !TERMINAL.has(proposal.status),
    );
    if (next?.proposal_type === 'investigate')
      return (
        <Button
          className="small"
          disabled={locked}
          onClick={() => void run('apply', insight.id, next.id)}
        >
          Investigate
        </Button>
      );
    if (next?.proposal_type === 'send_feedback' && next.feedback_body)
      return (
        <Button className="small" onClick={() => copyFeedback(next)}>
          Copy feedback
        </Button>
      );
    if (next)
      return (
        <Button className="small" onClick={() => toggle(insight.id, true)}>
          Review {proposalType(next.proposal_type).toLowerCase()}
        </Button>
      );
    if (!insight.proposals.length)
      return (
        <Button
          className="small"
          disabled={locked}
          onClick={() => void run('generate', insight.id)}
        >
          Suggest a fix
        </Button>
      );
    return null;
  }

  return (
    <section className="insights-home home-page" aria-label="Insights">
      <header className="home-page-header">
        <h2>Insights</h2>
        {snapshot && (
          <p className="home-caption">
            {plural(snapshot.items.length, 'insight')}
            {pinned ? ` · ${pinned} pinned` : ''}
          </p>
        )}
        {snapshot && snapshot.items.length > 0 && (
          <Segmented
            label="Show insights"
            size="sm"
            value={filter}
            onChange={setFilter}
            options={[
              { value: 'all', label: 'All' },
              { value: 'pinned', label: 'Pinned' },
            ]}
          />
        )}
        <span className="home-page-header-spacer" />
        <IconButton
          size="sm"
          label="Refresh insights"
          disabled={busy}
          onClick={refresh}
        >
          <RefreshCw size={15} aria-hidden />
        </IconButton>
        <Button
          className="small"
          disabled={!snapshot || locked}
          title="Prepares proposals for your review. Nothing is applied."
          onClick={() => void run('review_skills')}
        >
          Analyze skill library
        </Button>
      </header>
      {error && (
        <ErrorState
          title="Insights unavailable"
          action={
            pending ? (
              <Button className="small" onClick={() => void recover()}>
                Check outcome
              </Button>
            ) : (
              <Button className="small" onClick={refresh}>
                Try again
              </Button>
            )
          }
        >
          {error}
        </ErrorState>
      )}
      {pending && !error && !busy && (
        <div className="task-builder-note">
          <p>The last action's outcome is not confirmed yet.</p>
          <Button className="small" onClick={() => void recover()}>
            Check previous outcome
          </Button>
        </div>
      )}
      {message && (
        <p className="home-caption" role="status">
          {message}
        </p>
      )}
      {!snapshot && !error && (
        <p className="home-caption" role="status">
          Loading Insights…
        </p>
      )}
      {snapshot && items.length === 0 && (
        <InlineEmpty icon={<Lightbulb size={15} />}>
          {filter === 'pinned'
            ? 'No pinned insights.'
            : 'No active insights. New ones appear after analysis.'}
        </InlineEmpty>
      )}
      {items.length > 0 && (
        <ul className="insight-feed" aria-label="Insights feed">
          {items.map((insight) => {
            const view = severity(insight.severity);
            const Icon = view.icon;
            const open = expanded.has(insight.id);
            const isPinned = insight.status === 'pinned';
            return (
              <li
                key={insight.id}
                className="insight-row"
                data-tone={view.tone}
                data-pinned={isPinned ? 'true' : undefined}
              >
                <span
                  className="insight-severity home-tone-icon"
                  data-tone={view.tone}
                  title={view.label}
                >
                  <Icon size={16} aria-hidden />
                  <span className="visually-hidden">{view.label}</span>
                </span>
                <div className="insight-main">
                  <div className="insight-title-line">
                    <h3 className="insight-title">{insight.title}</h3>
                    {insight.category && (
                      <span className="insight-category">
                        {humanizeToken(insight.category)}
                      </span>
                    )}
                    {isPinned && (
                      <span className="insight-category" data-kind="pinned">
                        Pinned
                      </span>
                    )}
                  </div>
                  <p className="insight-summary">{insight.body}</p>
                  <div className="insight-actions-line">
                    <button
                      type="button"
                      className="insight-why"
                      aria-expanded={open}
                      aria-controls={`insight-why-${insight.id}`}
                      onClick={() => toggle(insight.id)}
                    >
                      <ChevronDown size={13} aria-hidden />
                      Why
                    </button>
                    {suggested(insight)}
                  </div>
                  <div
                    id={`insight-why-${insight.id}`}
                    className="insight-why-body"
                    hidden={!open}
                  >
                    <p>{insight.body}</p>
                    {insight.suggestion && (
                      <p className="insight-suggestion">
                        <strong>Suggested: </strong>
                        {insight.suggestion}
                      </p>
                    )}
                    {insight.proposals.map((proposal) => {
                      const terminal = TERMINAL.has(proposal.status);
                      return (
                        <details key={proposal.id} className="insight-proposal">
                          <summary>
                            {proposal.title} ·{' '}
                            {proposalType(proposal.proposal_type)} ·{' '}
                            {humanizeToken(proposal.status)}
                          </summary>
                          <dl className="insight-proposal-facts">
                            <div>
                              <dt>Risk</dt>
                              <dd>
                                {humanizeToken(proposal.risk) || 'Unknown'}
                              </dd>
                            </div>
                          </dl>
                          {proposal.rationale && <p>{proposal.rationale}</p>}
                          <PreviewFields preview={proposal.preview} />
                          {proposal.verification_plan && (
                            <p className="home-caption">
                              Checked by: {proposal.verification_plan}
                            </p>
                          )}
                          <div className="insight-proposal-actions">
                            {proposal.open_thread_id && openConversation && (
                              <Button
                                className="small"
                                onClick={() =>
                                  openConversation(proposal.open_thread_id)
                                }
                              >
                                Open investigation draft
                              </Button>
                            )}
                            {proposal.feedback_body && !terminal && (
                              <Button
                                className="small"
                                onClick={() => copyFeedback(proposal)}
                              >
                                Copy feedback
                              </Button>
                            )}
                            {proposal.support_url && terminal && (
                              <a
                                className="overview-section-link"
                                href={proposal.support_url}
                                target="_blank"
                                rel="noopener noreferrer"
                              >
                                Open support destination
                              </a>
                            )}
                            {!terminal && (
                              <>
                                <Button
                                  variant="primary"
                                  className="small"
                                  disabled={locked}
                                  onClick={() =>
                                    void run('apply', insight.id, proposal.id)
                                  }
                                >
                                  Apply proposal
                                </Button>
                                <Button
                                  className="small"
                                  disabled={locked}
                                  onClick={() =>
                                    void run('reject', insight.id, proposal.id)
                                  }
                                >
                                  Reject proposal
                                </Button>
                              </>
                            )}
                          </div>
                        </details>
                      );
                    })}
                    {insight.proposals.length === 0 && (
                      <p className="home-caption">
                        No proposals yet. Suggest a fix to prepare some for
                        review; nothing is applied until you choose.
                      </p>
                    )}
                  </div>
                </div>
                <div className="insight-icons">
                  <IconButton
                    size="sm"
                    label={isPinned ? 'Unpin' : 'Pin'}
                    disabled={locked}
                    pressed={isPinned}
                    onClick={() =>
                      void run(isPinned ? 'unpin' : 'pin', insight.id)
                    }
                  >
                    {isPinned ? (
                      <PinOff size={14} aria-hidden />
                    ) : (
                      <Pin size={14} aria-hidden />
                    )}
                  </IconButton>
                  <IconButton
                    size="sm"
                    label="Dismiss"
                    disabled={locked}
                    onClick={() => void run('dismiss', insight.id)}
                  >
                    <X size={14} aria-hidden />
                  </IconButton>
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {snapshot?.curator_report && (
        <section
          className="insight-report"
          aria-label="Latest skill library report"
        >
          <header className="monitor-section-head">
            <h3>Skill library report</h3>
            <p className="home-caption">
              {plural(
                snapshot.curator_report.manual_skill_count,
                'manual skill',
              )}{' '}
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
          </header>
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
    </section>
  );
}
