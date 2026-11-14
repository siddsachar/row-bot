import { useEffect, useId, useState, type FormEvent } from 'react';
import {
  Brain,
  CalendarDays,
  ChevronRight,
  HeartPulse,
  Mail,
  MessageSquare,
  PenLine,
  Repeat,
  Workflow,
} from 'lucide-react';
import type {
  ProactiveEvidence,
  ProactiveMomentDetail,
  ProactiveMomentView,
} from '../../api/types';
import { clientError } from '../../api/errors';
import {
  Button,
  Disclosure,
  ErrorState,
  Skeleton,
  StatusDot,
} from '../../ui/primitives';
import { ModalTask } from '../../ui/overlays';
import { When } from '../../ui/When';
import { parseTimestamp } from '../../ui/format';
import type { LessScope } from './moment-actions';
import type { ProactiveStore } from './proactive-state';
import {
  EVIDENCE_WORDS,
  KIND_ONE,
  KIND_WORDS,
  SOURCE_WORDS,
  hourWords,
  isLive,
} from './words';
import './proactive.css';

function evidenceIcon(type: ProactiveEvidence['type']) {
  switch (type) {
    case 'event':
      return <CalendarDays size={15} aria-hidden />;
    case 'message':
    case 'thread':
      return <Mail size={15} aria-hidden />;
    case 'memory':
      return <Brain size={15} aria-hidden />;
    case 'conversation':
      return <MessageSquare size={15} aria-hidden />;
    case 'workflow_run':
      return <Workflow size={15} aria-hidden />;
    case 'ask':
      return <Repeat size={15} aria-hidden />;
    default:
      return <HeartPulse size={15} aria-hidden />;
  }
}

/** The three ways "Less like this" can go, each with the rule it writes. */
export function lessOptions(
  moment: ProactiveMomentView,
): { value: LessScope; label: string; rule: string }[] {
  const at =
    parseTimestamp(moment.shown_at) ??
    parseTimestamp(moment.created_at) ??
    new Date();
  const one = KIND_ONE[moment.kind];
  return [
    {
      value: 'topic',
      label: 'This person or topic',
      rule: 'Never bring this up again.',
    },
    {
      value: 'kind',
      label: KIND_WORDS[moment.kind],
      rule: `At most one ${one} a day.`,
    },
    {
      value: 'time',
      label: 'Around this time',
      rule: `No ${one}s around ${hourWords(at.getHours())}.`,
    },
  ];
}

/** The words for a "Less like this" that couldn't be saved. */
export function lessProblem(scope: LessScope, cause: unknown): string {
  const problem = clientError(cause);
  return scope === 'topic' && problem.code === 'invalid_command'
    ? 'This one isn’t about a person or topic. Choose another.'
    : problem.message;
}

/**
 * "Less like this about…": a radio group, each option showing the rule it
 * will write, so the person sees the rule before it exists. No free text.
 */
export function LessLikeThis({
  moment,
  onSave,
  onCancel,
}: {
  moment: ProactiveMomentView;
  onSave(scope: LessScope): Promise<void>;
  onCancel(): void;
}) {
  const name = useId();
  const options = lessOptions(moment);
  const [scope, setScope] = useState<LessScope>('kind');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      await onSave(scope);
    } catch (cause) {
      setError(lessProblem(scope, cause));
      setBusy(false);
    }
  }
  return (
    <form className="proactive-less" onSubmit={submit}>
      <fieldset>
        <legend>Less like this about…</legend>
        {options.map((option) => (
          <label key={option.value} className="proactive-less-option">
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={scope === option.value}
              onChange={() => setScope(option.value)}
            />
            <span className="proactive-less-label">{option.label}</span>
            <span className="proactive-less-help">{option.rule}</span>
          </label>
        ))}
      </fieldset>
      {error && (
        <p className="proactive-error" role="alert">
          {error}
        </p>
      )}
      <div className="proactive-sheet-footer">
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" disabled={busy}>
          Save rule
        </Button>
      </div>
    </form>
  );
}

/**
 * Why a moment reached the person: what Row-Bot saw (each source's label,
 * as plain text), why now and what let it through (the server's lines), what
 * was prepared and the trust line, then Less like this and Done.
 */
export function WhyThisBody({
  store,
  moment,
  startWithLess = false,
  onClose,
  onLess,
  onOpenDraft,
  onOpenSettings,
}: {
  store: ProactiveStore;
  moment: ProactiveMomentView;
  startWithLess?: boolean;
  onClose(): void;
  onLess(moment: ProactiveMomentView, scope: LessScope): Promise<void>;
  onOpenDraft?: (draftId: string) => void;
  onOpenSettings?: () => void;
}) {
  const [detail, setDetail] = useState<ProactiveMomentDetail | null>(null);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const [less, setLess] = useState(startWithLess);
  const momentId = moment.moment_id;
  useEffect(() => {
    const request = new AbortController();
    setDetail(null);
    setError('');
    store.io
      .moment(momentId, request.signal)
      .then((value) => {
        if (!request.signal.aborted) setDetail(value);
      })
      .catch((cause) => {
        if (!request.signal.aborted) setError(clientError(cause).message);
      });
    return () => request.abort();
  }, [store, momentId, attempt]);
  if (error)
    return (
      <ErrorState
        title="Couldn’t load why."
        action={
          <Button
            variant="ghost"
            onClick={() => setAttempt((value) => value + 1)}
          >
            Try again
          </Button>
        }
      >
        The moment is still in For you.
      </ErrorState>
    );
  if (!detail) return <Skeleton label="Reading why" />;
  const live = isLive(detail.moment, new Date());
  const prepared = detail.moment.prepared_ref;
  return (
    <div className="proactive-why">
      {detail.summary && (
        <p className="proactive-why-summary">{detail.summary}</p>
      )}
      {detail.evidence.length > 0 && (
        <section aria-labelledby={`why-saw-${momentId}`}>
          <h3 id={`why-saw-${momentId}`} className="proactive-why-head">
            What Row-Bot saw
          </h3>
          <ul className="proactive-evidence">
            {detail.evidence.map((item) => (
              <li key={`${item.type}:${item.id}`}>
                <span className="proactive-evidence-icon">
                  {evidenceIcon(item.type)}
                </span>
                <span className="proactive-evidence-text">
                  <span className="proactive-evidence-source">
                    {[
                      item.source
                        ? (SOURCE_WORDS[item.source] ?? item.source)
                        : 'Row-Bot',
                      EVIDENCE_WORDS[item.type] ?? 'Source',
                    ]
                      .filter((word, index, all) => all.indexOf(word) === index)
                      .join(' · ')}
                  </span>
                  <q className="proactive-evidence-label">{item.label}</q>
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
      {!live && (
        <p className="proactive-recheck">
          <StatusDot tone="neutral" label="This has passed." showLabel />
        </p>
      )}
      {detail.why.length > 0 && (
        <section aria-labelledby={`why-now-${momentId}`}>
          <h3 id={`why-now-${momentId}`} className="proactive-why-head">
            Why now
          </h3>
          <ul className="proactive-why-lines">
            {detail.why.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          {onOpenSettings && (
            <button
              type="button"
              className="proactive-text-link"
              onClick={onOpenSettings}
            >
              Change this in Settings › Proactive
              <ChevronRight size={13} aria-hidden />
            </button>
          )}
        </section>
      )}
      {prepared && (
        <section aria-labelledby={`why-prepared-${momentId}`}>
          <h3 id={`why-prepared-${momentId}`} className="proactive-why-head">
            What Row-Bot prepared
          </h3>
          <div className="proactive-prepared">
            <PenLine size={15} aria-hidden />
            <span>
              {detail.moment.actions.some((a) => a.id === 'open_draft')
                ? 'Draft · saved, not sent'
                : 'Brief'}
            </span>
            {onOpenDraft && (
              <Button
                variant="ghost"
                className="small"
                onClick={() => onOpenDraft(prepared)}
              >
                Open
              </Button>
            )}
          </div>
          <p className="proactive-trust">
            Row-Bot only read. Nothing was sent or changed.
          </p>
        </section>
      )}
      {detail.history.length > 0 && (
        <Disclosure summary="History">
          <ul className="proactive-history">
            {detail.history.map((entry) => (
              <li key={entry.entry_id}>
                <span>{entry.summary}</span>
                <span className="proactive-history-time">
                  <When value={entry.at} fallback="" />
                </span>
              </li>
            ))}
          </ul>
        </Disclosure>
      )}
      {less ? (
        <LessLikeThis
          moment={detail.moment}
          onSave={async (scope) => {
            await onLess(detail.moment, scope);
            onClose();
          }}
          onCancel={() => (startWithLess ? onClose() : setLess(false))}
        />
      ) : (
        <div className="proactive-sheet-footer">
          <Button variant="secondary" onClick={() => setLess(true)}>
            Less like this
          </Button>
          <Button variant="primary" onClick={onClose}>
            Done
          </Button>
        </div>
      )}
    </div>
  );
}

/** "Why this?": a side sheet on a wide window, a bottom sheet on a phone. */
export function WhyThisSheet({
  store,
  moment,
  startWithLess = false,
  onClose,
  onLess,
  onOpenDraft,
  onOpenSettings,
}: {
  store: ProactiveStore;
  moment: ProactiveMomentView | null;
  startWithLess?: boolean;
  onClose(): void;
  onLess(moment: ProactiveMomentView, scope: LessScope): Promise<void>;
  onOpenDraft?: (draftId: string) => void;
  onOpenSettings?: () => void;
}) {
  return (
    <ModalTask
      open={Boolean(moment)}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      kind="sheet"
      className="proactive-side-sheet"
      title={startWithLess ? 'Less like this' : 'Why this?'}
      description={moment?.title ?? ''}
    >
      {moment && (
        <WhyThisBody
          key={moment.moment_id}
          store={store}
          moment={moment}
          startWithLess={startWithLess}
          onClose={onClose}
          onLess={onLess}
          onOpenDraft={onOpenDraft}
          onOpenSettings={
            onOpenSettings &&
            (() => {
              onClose();
              onOpenSettings();
            })
          }
        />
      )}
    </ModalTask>
  );
}
