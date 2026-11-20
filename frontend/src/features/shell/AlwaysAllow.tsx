import { useEffect, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { CircleCheck, TriangleAlert } from 'lucide-react';
import type {
  ApprovalRuleDraft,
  ApprovalRuleDryRun,
  ApprovalRuleDryRunRequest,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useDialogFooter } from '../../ui/dialog-footer';
import { relativeTime } from '../../ui/format';
import { Button, Skeleton } from '../../ui/primitives';
import './always-allow.css';

/** What the dry run reads (the app's controller by default). */
export type DryRun = (
  body: ApprovalRuleDryRunRequest,
  signal?: AbortSignal,
) => Promise<ApprovalRuleDryRun>;

/** Rules never cover these, whatever the action: said under every rule list and sheet. */
export const NEVER_COVERED =
  'Rules never cover sending, posting, deleting, paying, pushing or anything only you can approve.';

/** "Always allow **Notion › Create a page**", "Always allow `npm test` in **row-bot**". */
export function RuleWords({ rule }: { rule: ApprovalRuleDraft }) {
  if (rule.kind === 'app')
    return (
      <>
        Always allow{' '}
        <strong>
          {rule.app_name} › {rule.tool_title}
        </strong>
      </>
    );
  if (rule.kind === 'command')
    return (
      <>
        Always allow <code>{rule.command}</code> in{' '}
        <strong>{rule.folder_name}</strong>
      </>
    );
  return (
    <>
      Always allow moving and renaming files in{' '}
      <strong>{rule.folder_name}</strong>
    </>
  );
}

const EXAMPLE_WORDS: Record<
  ApprovalRuleDryRun['examples'][number]['outcome'],
  string
> = {
  approved_by_you: 'You approved',
  approved_by_reviewer: 'Reviewer approved',
  allowed_in_chat: 'Allowed in a chat',
  denied: 'You said no',
  changed: 'Changed after you approved',
  asked: 'Not answered',
};

function times(count: number) {
  return count === 1 ? '1 action' : `${count.toLocaleString()} actions`;
}

/** The counts in one sentence: "you approved 18, the reviewer approved 3, and you said no to 2". */
export function dryRunWords(counts: ApprovalRuleDryRun['counts']): string {
  const parts = [
    counts.approved_by_you && `you approved ${counts.approved_by_you}`,
    counts.approved_by_reviewer &&
      `the reviewer approved ${counts.approved_by_reviewer}`,
    counts.allowed_in_chat && `${counts.allowed_in_chat} you allowed in a chat`,
    counts.asked && `${counts.asked} you didn’t answer`,
    counts.changed && `${counts.changed} changed after you approved`,
    counts.denied && `you said no to ${counts.denied}`,
  ].filter((part): part is string => Boolean(part));
  if (parts.length < 2) return parts.join('');
  return `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}`;
}

/** "Would this have asked?": what the rule would have allowed in the last 30 days, the refusals first. */
export function DryRunPanel({
  result,
  error,
}: {
  result: ApprovalRuleDryRun | null;
  error: string;
}) {
  if (error)
    return (
      <p className="always-allow-dry" role="alert" data-tone="danger">
        {error}
      </p>
    );
  if (!result) return <Skeleton label="Checking the last 30 days" />;
  const refused = result.counts.denied + result.counts.changed;
  return (
    <section
      className="always-allow-dry"
      aria-label="Would this have asked?"
      data-tone={refused ? 'warning' : undefined}
      // A phone's sheet may scroll over it: keyboard users reach it too.
      tabIndex={0}
    >
      <h4>Would this have asked?</h4>
      {result.total ? (
        <p>
          In the last {result.days} days this rule would have allowed{' '}
          {times(result.total)} without asking: {dryRunWords(result.counts)}.
        </p>
      ) : (
        <p>
          Nothing in the last {result.days} days would have matched it. From now
          on, actions like this one run without asking.
        </p>
      )}
      {refused > 0 && (
        <p className="always-allow-warning">
          <TriangleAlert size={15} aria-hidden />
          You said no to{' '}
          {refused === 1 ? 'one of these' : `${refused} of these`}. With this
          rule, {refused === 1 ? 'it' : 'they'} would have run.
        </p>
      )}
      {result.examples.length > 0 && (
        <ul className="always-allow-examples">
          {result.examples.map((example, index) => (
            <li
              key={`${example.at}-${index}`}
              data-refused={
                example.outcome === 'denied' || example.outcome === 'changed'
                  ? true
                  : undefined
              }
            >
              <span className="always-allow-example-what">
                {example.summary || example.title}
              </span>
              <span className="always-allow-example-meta">
                {EXAMPLE_WORDS[example.outcome]}
                {example.conversation_title &&
                  ` · ${example.conversation_title}`}
                {` · ${relativeTime(example.at)}`}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Runs the dry run for *request* once, and again whenever *key* changes; the latest answer wins. */
export function useDryRun(
  dryRun: DryRun | null,
  request: ApprovalRuleDryRunRequest | null,
  key: string,
) {
  const [result, setResult] = useState<ApprovalRuleDryRun | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!dryRun || !request) return;
    const abort = new AbortController();
    setError('');
    dryRun(request, abort.signal)
      .then((value) => {
        if (!abort.signal.aborted) setResult(value);
      })
      .catch((cause) => {
        if (!abort.signal.aborted) {
          setResult(null);
          setError(clientError(cause).message);
        }
      });
    return () => abort.abort();
    // The request is rebuilt each render; its key says when it changed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dryRun, key]);
  return { result, error };
}

/** A sheet's own Cancel and primary action, in the dialog's footer when there is one. */
export function SheetActions({ children }: { children: ReactNode }) {
  const footer = useDialogFooter(true);
  return footer ? (
    createPortal(children, footer)
  ) : (
    <div className="button-row always-allow-actions">{children}</div>
  );
}

/**
 * "Always allow…" on an approval card: the rule the server would make from
 * this card, what it would have allowed in the last 30 days, then Cancel /
 * Save and approve. Nothing is saved until Save; the client never says what the
 * rule covers.
 */
export function AlwaysAllowSheet({
  approvalId,
  dryRun,
  onSave,
  onCancel,
}: {
  approvalId: string;
  dryRun: DryRun;
  onSave: () => Promise<void>;
  onCancel: () => void;
}) {
  const { result, error } = useDryRun(
    dryRun,
    { approval_id: approvalId },
    approvalId,
  );
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState('');
  async function save() {
    setSaving(true);
    setProblem('');
    try {
      await onSave();
    } catch (cause) {
      setProblem(clientError(cause).message);
      setSaving(false);
    }
  }
  return (
    <div className="always-allow stack">
      <p className="always-allow-words">
        {result ? (
          <RuleWords rule={result.rule} />
        ) : error ? null : (
          <Skeleton label="Reading the rule" />
        )}
      </p>
      <DryRunPanel result={result} error={error} />
      <p className="always-allow-note">
        <CircleCheck size={14} aria-hidden />
        Approves this now too. Turn the rule off any time in Settings ›
        Activity. {NEVER_COVERED}
      </p>
      {problem && (
        <p role="alert" className="always-allow-problem">
          {problem}
        </p>
      )}
      <SheetActions>
        <Button disabled={saving} onClick={onCancel}>
          Cancel
        </Button>
        <Button
          variant="primary"
          disabled={!result || saving}
          onClick={() => void save()}
        >
          Save and approve
        </Button>
      </SheetActions>
    </div>
  );
}
