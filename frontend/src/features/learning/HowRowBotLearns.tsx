import { useState } from 'react';
import { Sparkles } from 'lucide-react';
import { clientError } from '../../api/errors';
import type {
  LearningMethod,
  LearningMethodArm,
  LearningMethodStep,
  LearningMethodTrial,
  LearningOvernight,
} from '../../api/types';
import { Button, InlineEmpty } from '../../ui/primitives';
import { Drawer, useNotify } from '../../ui/overlays';
import type { LearningIO } from './learning-state';
import { LearningJobs } from './LearningJobs';
import { dayWords, plural, tokenWords } from './words';
import './learning.css';

/** What no learning method can change (designs/learning-ux.md §6), said on its screen. */
export const NEVER_CHANGES = [
  'Your goals and limits',
  'Approvals, tools and permissions',
  'The hidden tests that judge it',
  'The rules for keeping a change',
];

function day(at: string | null | undefined): string {
  return at ? dayWords(at.slice(0, 10)) : '';
}

function perMillion(value: number): string {
  return value.toLocaleString(undefined, { maximumFractionDigits: 1 });
}

export function useMethodUndo(io: LearningIO) {
  const notify = useNotify();
  return async function undo(): Promise<boolean> {
    try {
      notify((await io.run('undo_method')).summary);
      return true;
    } catch (cause) {
      notify(clientError(cause).message, 'danger');
      return false;
    }
  };
}

/** Overview › How Row-Bot learns (designs §3): the method and this month's record, with Details. */
export function MethodSummary({
  method,
  onDetails,
}: {
  method: LearningMethod | null | undefined;
  onDetails: () => void;
}) {
  if (!method)
    return (
      <InlineEmpty icon={<Sparkles size={15} />}>
        Its learning method shows here once Learning is on.
      </InlineEmpty>
    );
  return (
    <div className="learning-method-summary">
      <div>
        <p className="learning-method-head">
          Method v{method.version}
          {method.since ? ` · since ${day(method.since)}` : ''}
        </p>
        <p className="learning-muted">
          {method.tried
            ? `Kept ${method.kept.toLocaleString()} of ${plural(method.tried, 'idea')} this month`
            : 'No ideas tested yet this month'}{' '}
          · {tokenWords(method.tokens)} tokens
        </p>
      </div>
      <Button
        variant="ghost"
        className="small"
        aria-label="How Row-Bot learns: details"
        onClick={onDetails}
      >
        Details
      </Button>
    </div>
  );
}

/** Overnight (designs §3): a new learning method that switched in by itself, with Undo. */
export function MethodSwitched({
  io,
  night,
  method,
}: {
  io: LearningIO;
  night: LearningOvernight | null | undefined;
  method: LearningMethod | null | undefined;
}) {
  const undo = useMethodUndo(io);
  if (night?.method !== 'switched' || !night.method_version) return null;
  return (
    <ul className="learning-overnight learning-method-switched">
      <li>
        <span>
          Changed how it learns (method v{night.method_version})
          {night.method_words ? `: ${night.method_words}` : ''}
        </span>
        {method?.can_undo && method.version === night.method_version && (
          <Button
            variant="ghost"
            className="small"
            aria-label={`Undo learning method v${night.method_version}`}
            onClick={() => void undo()}
          >
            Undo
          </Button>
        )}
      </li>
    </ul>
  );
}

function armLabel(arm: LearningMethodArm, trial: LearningMethodTrial): string {
  if (trial.outcome === 'final') return `v${arm.version}`;
  if (arm.role === 'incumbent')
    return trial.outcome === 'switched'
      ? `v${arm.version} (before)`
      : `v${arm.version} (in use)`;
  if (arm.role === 'challenger')
    return trial.outcome === 'switched'
      ? `v${arm.version} (now)`
      : `v${arm.version} (tried)`;
  return `v${arm.version} (earlier method)`;
}

/** The newest trial: each method's ideas kept per 1M tokens, with the denominators (designs §6). */
function TrackRecord({ method }: { method: LearningMethod }) {
  const trial = (method.trials ?? []).find((item) => item.outcome !== 'final');
  const next = method.next_trial;
  if (!trial)
    return (
      <p className="learning-muted">
        {next.state === 'limit'
          ? 'Not compared with another method yet: this month’s share for comparing methods is used up.'
          : `Not compared with another method yet. A comparison runs once ${next.needed.toLocaleString()} new problems have built up (${next.fresh.toLocaleString()} so far).`}
      </p>
    );
  return (
    <>
      <table className="learning-results learning-method-results">
        <caption className="visually-hidden">
          Ideas kept by each method on the same new problems
        </caption>
        <thead>
          <tr>
            <th scope="col">Method</th>
            <th scope="col">Ideas kept, per 1M tokens</th>
            <th scope="col">Better on new problems</th>
          </tr>
        </thead>
        <tbody>
          {trial.arms.map((arm) => (
            <tr key={`${arm.role}-${arm.version}`}>
              <th scope="row">{armLabel(arm, trial)}</th>
              <td data-label="Ideas kept, per 1M tokens">
                {perMillion(arm.per_million)}
              </td>
              <td data-label="Better on new problems">
                {arm.better.toLocaleString()} of {plural(arm.accepted, 'idea')}{' '}
                · {tokenWords(arm.tokens)} tokens
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="learning-muted">
        Compared on {plural(trial.problems, 'new problem')} at the same budget
        (up to {tokenWords(trial.arm_tokens)} tokens each) · {day(trial.at)}.
        Small test, so expect some variation.
      </p>
    </>
  );
}

function stepWords(step: LearningMethodStep): string {
  switch (step.event) {
    case 'first':
      return `v${step.version}: the first method`;
    case 'undone':
      return `Undone: back to v${step.version}`;
    case 'tried':
      return `v${step.version} tried and dropped: no better than v${step.against ?? step.version - 1}`;
    default:
      return `v${step.version}${step.words ? `: ${step.words}` : ''}`;
  }
}

/** How Row-Bot learns (designs/learning-ux.md §6): read-mostly, plain words, with Undo of the last switch. */
export function MethodSheet({
  io,
  method,
  open,
  onOpenChange,
}: {
  io: LearningIO;
  method: LearningMethod;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const undo = useMethodUndo(io);
  const [busy, setBusy] = useState(false);
  const history = method.history ?? [];
  return (
    <Drawer
      open={open}
      onOpenChange={onOpenChange}
      modal
      title="How Row-Bot learns"
      description={`Learning method v${method.version}`}
      className="learning-review learning-method"
      closeLabel="Close How Row-Bot learns"
    >
      <div
        className="learning-review-body"
        role="group"
        aria-label="How Row-Bot learns"
        tabIndex={0}
      >
        <section aria-label="Current method">
          <h3>
            Current method{method.since ? ` (since ${day(method.since)})` : ''}
          </h3>
          <p>{method.description}</p>
        </section>
        <section aria-label="Track record">
          <h3>Track record</h3>
          <TrackRecord method={method} />
        </section>
        <section aria-label="History">
          <h3>History</h3>
          {history.length ? (
            <ul className="learning-method-history">
              {history.map((step) => (
                <li key={`${step.at}-${step.event}-${step.version}`}>
                  <span className="learning-muted">{day(step.at)}</span>
                  <span>{stepWords(step)}</span>
                  {step.can_undo && (
                    <Button
                      variant="ghost"
                      className="small"
                      disabled={busy}
                      aria-label={`Undo learning method v${step.version}`}
                      onClick={async () => {
                        setBusy(true);
                        await undo();
                        setBusy(false);
                      }}
                    >
                      Undo
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="learning-muted">
              It still uses the first method. A new one is tried against it on
              new problems at the same budget, and kept only if it does better.
            </p>
          )}
        </section>
        <LearningJobs jobs={method.jobs} />
        <section aria-label="What it can never change">
          <h3>What it can never change</h3>
          <ul className="learning-method-never">
            {NEVER_CHANGES.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>
      </div>
    </Drawer>
  );
}
