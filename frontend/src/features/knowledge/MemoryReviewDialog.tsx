import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import {
  ArrowLeft,
  Check,
  CheckCheck,
  CircleCheck,
  GitMerge,
  ListChecks,
  ListX,
  Pencil,
  Scissors,
  Square,
  Trash2,
  Undo2,
  WandSparkles,
  X,
} from 'lucide-react';
import type {
  MemoryReviewOverview,
  MemoryReviewProposal,
  MemoryReviewReceipt,
  MemoryReviewRun,
} from '../../api/types';
import { clientError } from '../../api/errors';
import {
  Button,
  Disclosure,
  EmptyState,
  ErrorState,
  IconButton,
  InlineEmpty,
  Input,
  Progress,
  Select,
  Skeleton,
  Stat,
  StatGroup,
} from '../../ui/primitives';
import { ModalTask, useOverlay } from '../../ui/overlays';
import { PHONE_LAYOUT, useMedia } from '../../ui/use-media';
import { CategoryChip } from './MemoryImportParts';
import { plural, shortDate } from './memory-import-io';
import {
  GROUPS,
  receiptWords,
  reviewProblem,
  wordDiff,
  type MemoryReviewIO,
  type ReviewAction,
} from './memory-review-io';
import './memory-import.css';
import './memory-review.css';

type Step = 'intro' | 'working' | 'list' | 'confirm' | 'done';
const POLL_MS = 600;
const PAGE = 40;
const TEXT_LIMIT = 600;
const PROFILE_LIMIT = 1500;

function problem(error: unknown) {
  return clientError(error).message;
}

function uuid() {
  return crypto.randomUUID();
}

/**
 * Knowledge › Review memory: a clean-up over the person's whole memory (or
 * one import's). Row-Bot suggests keeping, merging, rewriting or removing
 * each memory with a reason; the person approves in a list; memory is backed
 * up first and the review can be undone for 30 days. Nothing changes before
 * the person approves.
 */
export default function MemoryReviewDialog({
  open,
  onOpenChange,
  io,
  importBatch,
  onChanged,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  io: MemoryReviewIO;
  /** Only the memories one import saved, and the saved ones closest to them. */
  importBatch?: string;
  /** Memories changed or a review was undone: refresh what shows them. */
  onChanged?: () => void;
}) {
  const phone = useMedia(PHONE_LAYOUT);
  return (
    <ModalTask
      open={open}
      onOpenChange={onOpenChange}
      title={importBatch ? 'Review what was imported' : 'Review memory'}
      description="Suggestions to keep, merge, rewrite or remove, each with a reason."
      kind={phone ? 'sheet' : 'dialog'}
      className="memory-import-dialog memory-review-dialog"
    >
      {open && (
        <ReviewFlow
          io={io}
          importBatch={importBatch}
          phone={phone}
          onClose={() => onOpenChange(false)}
          onChanged={onChanged}
        />
      )}
    </ModalTask>
  );
}

type Choice = { text?: string; remove?: boolean };

function ReviewFlow({
  io,
  importBatch,
  phone,
  onClose,
  onChanged,
}: {
  io: MemoryReviewIO;
  importBatch?: string;
  phone: boolean;
  onClose: () => void;
  onChanged?: () => void;
}) {
  const overlay = useOverlay();
  const [overview, setOverview] = useState<MemoryReviewOverview | null>(null);
  const [overviewError, setOverviewError] = useState('');
  const [step, setStep] = useState<Step>('intro');
  const [run, setRun] = useState<MemoryReviewRun | null>(null);
  const [items, setItems] = useState<MemoryReviewProposal[]>([]);
  const [revision, setRevision] = useState('');
  const [chosen, setChosen] = useState<ReadonlySet<string>>(() => new Set());
  const [choices, setChoices] = useState<Record<string, Choice>>({});
  const [query, setQuery] = useState('');
  const [shown, setShown] = useState('');
  const [receipt, setReceipt] = useState<MemoryReviewReceipt | null>(null);
  const [error, setError] = useState('');
  const [stale, setStale] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState('');
  const runRef = useRef<string | null>(null);
  const applied = useRef(false);
  const alive = useRef(true);

  const loadOverview = useCallback(() => {
    setOverviewError('');
    const controller = new AbortController();
    io.overview(importBatch, controller.signal).then(
      (value) => {
        if (alive.current) setOverview(value);
      },
      (reason: unknown) => {
        if (alive.current && !controller.signal.aborted)
          setOverviewError(problem(reason));
      },
    );
    return () => controller.abort();
  }, [io, importBatch]);

  useEffect(() => {
    alive.current = true;
    const stop = loadOverview();
    return () => {
      alive.current = false;
      stop();
      // Closed before approving: the review and its suggestions go.
      const id = runRef.current;
      if (id && !applied.current) void io.discard(id).catch(() => undefined);
    };
  }, [io, loadOverview]);

  const follow = useCallback(
    async (id: string) => {
      for (;;) {
        if (!alive.current) return null;
        const view = await io.run(id);
        if (!alive.current) return null;
        setRun(view);
        if (view.job?.status !== 'running') return view;
        await new Promise((resolve) => setTimeout(resolve, POLL_MS));
      }
    },
    [io],
  );

  const start = async () => {
    setError('');
    setStale(false);
    setNotice('');
    setStep('working');
    try {
      const old = runRef.current;
      runRef.current = null;
      if (old && !applied.current) void io.discard(old).catch(() => undefined);
      applied.current = false;
      const begun = await io.begin(importBatch);
      runRef.current = begun.run_id;
      setRun(begun);
      const view = await follow(begun.run_id);
      if (!view) return;
      if (view.job?.status !== 'completed') {
        if (view.job?.status === 'failed')
          setError(reviewProblem(view.job.code));
        setStep('intro');
        return;
      }
      const page = await io.proposals(begun.run_id);
      if (!alive.current) return;
      setItems(page.items);
      setRevision(page.revision);
      setChosen(
        new Set(
          page.items
            .filter((item) => item.selected)
            .map((item) => item.proposal_id),
        ),
      );
      setChoices({});
      setStep('list');
    } catch (reason) {
      if (!alive.current) return;
      setError(problem(reason));
      setStep('intro');
    }
  };

  const stop = async () => {
    const id = runRef.current;
    if (id) setRun(await io.cancel(id).catch(() => run));
    setStep('intro');
  };

  const approved = useMemo(
    () =>
      items.filter(
        (item) =>
          chosen.has(item.proposal_id) || choices[item.proposal_id]?.remove,
      ),
    [items, chosen, choices],
  );

  const runApply = async () => {
    const id = runRef.current;
    if (!id) return;
    setError('');
    setBusy('apply');
    try {
      const result = await io.apply(id, {
        command_id: uuid(),
        revision,
        choices: approved.map((item) => {
          const choice = choices[item.proposal_id] ?? {};
          return {
            proposal_id: item.proposal_id,
            ...(choice.remove
              ? { remove: true, confirm: item.subject }
              : choice.text !== undefined
                ? { text: choice.text }
                : {}),
          };
        }),
      });
      applied.current = true;
      setReceipt(result);
      setStep('done');
      onChanged?.();
    } catch (reason) {
      const code = clientError(reason).code;
      if (code === 'review_stale') setStale(true);
      setError(problem(reason));
    } finally {
      setBusy(null);
    }
  };

  const undo = async (target: MemoryReviewReceipt) => {
    setBusy(target.run_id);
    setError('');
    try {
      const result = await io.undo(target.run_id, uuid());
      if (receipt?.run_id === result.run_id) setReceipt(result);
      setOverview((current) =>
        current
          ? {
              ...current,
              recent: current.recent.map((item) =>
                item.run_id === result.run_id ? result : item,
              ),
            }
          : current,
      );
      setNotice('Review undone. Your memories read as they did before.');
      onChanged?.();
    } catch (reason) {
      setError(problem(reason));
    } finally {
      setBusy(null);
    }
  };

  const askRemove = (item: MemoryReviewProposal) => {
    const lesson = item.marks.includes('lesson');
    overlay.open({
      kind: 'alert',
      title: `Remove “${item.subject}”?`,
      description: lesson
        ? 'It is a lesson Row-Bot learned and keeps exactly as it is. Removing it means Row-Bot stops using it. You can undo the review afterwards.'
        : `It is a long record (${item.length.toLocaleString()} characters) that Row-Bot never changes by itself. A workflow may rely on it, for example to know who it already emailed. You can undo the review afterwards.`,
      confirmLabel: `Remove “${item.subject.length > 32 ? `${item.subject.slice(0, 32)}…` : item.subject}”`,
      cancelLabel: 'Keep it',
      onConfirm: () => {
        overlay.close();
        setChoices((current) => ({
          ...current,
          [item.proposal_id]: { remove: true },
        }));
      },
    });
  };

  return (
    <div className="memory-import memory-review" data-step={step}>
      <div className="memory-import-scroll">
        {error && (
          <ErrorState
            title={stale ? 'Memory changed' : "That didn't work"}
            action={
              stale ? (
                <Button onClick={() => void start()}>Review again</Button>
              ) : undefined
            }
          >
            {error}
          </ErrorState>
        )}
        {notice && (
          <p role="status" className="memory-import-notice">
            {notice}
          </p>
        )}
        {step === 'intro' &&
          (overviewError ? (
            <ErrorState
              title="Review isn't available right now"
              action={
                <Button onClick={() => void loadOverview()}>Try again</Button>
              }
            >
              {overviewError}
            </ErrorState>
          ) : !overview ? (
            <Skeleton label="Counting your memories" />
          ) : (
            <IntroStep
              overview={overview}
              importBatch={importBatch}
              busy={busy}
              onUndo={(item) => void undo(item)}
            />
          ))}
        {step === 'working' && <WorkingStep run={run} />}
        {step === 'list' && (
          <ListStep
            items={items}
            phone={phone}
            chosen={chosen}
            onChosen={setChosen}
            choices={choices}
            onChoices={setChoices}
            query={query}
            onQuery={setQuery}
            shown={shown}
            onShown={setShown}
            warnings={run?.warnings ?? []}
            onRemove={askRemove}
          />
        )}
        {step === 'confirm' && (
          <ConfirmStep approved={approved} choices={choices} />
        )}
        {step === 'done' && receipt && <DoneStep receipt={receipt} />}
      </div>
      <footer className="memory-import-footer">
        {step === 'intro' && (
          <Button
            variant="primary"
            disabled={!overview?.model || overview.memories === 0}
            onClick={() => void start()}
          >
            Start review
          </Button>
        )}
        {step === 'working' && (
          <Button onClick={() => void stop()}>
            <Square size={14} aria-hidden /> Stop
          </Button>
        )}
        {step === 'list' && (
          <Button
            variant="primary"
            disabled={approved.length === 0}
            onClick={() => {
              setError('');
              setStep('confirm');
            }}
          >
            {approved.length
              ? `Review ${plural(approved.length, 'change')}`
              : 'Choose changes to make'}
          </Button>
        )}
        {step === 'confirm' && (
          <>
            <IconButton
              label="Back to the suggestions"
              disabled={busy !== null}
              onClick={() => {
                setError('');
                setStep('list');
              }}
            >
              <ArrowLeft size={16} aria-hidden />
            </IconButton>
            <Button
              variant="primary"
              disabled={busy !== null || stale}
              onClick={() => void runApply()}
            >
              {busy === 'apply'
                ? 'Backing up and changing…'
                : `Make ${plural(approved.length, 'change')}`}
            </Button>
          </>
        )}
        {step === 'done' && receipt && (
          <>
            {receipt.status === 'applied' && (
              <Button
                disabled={busy !== null}
                onClick={() => void undo(receipt)}
              >
                <Undo2 size={14} aria-hidden /> Undo review
              </Button>
            )}
            <Button variant="primary" onClick={onClose}>
              Done
            </Button>
          </>
        )}
      </footer>
    </div>
  );
}

function modelWords(model: MemoryReviewOverview['model']) {
  if (!model) return '';
  return `Uses ${model.name} ${model.on_this_computer ? 'on this computer' : `through ${model.provider}`}.`;
}

function IntroStep({
  overview,
  importBatch,
  busy,
  onUndo,
}: {
  overview: MemoryReviewOverview;
  importBatch?: string;
  busy: string | null;
  onUndo: (receipt: MemoryReviewReceipt) => void;
}) {
  const recent = overview.recent.filter((item) => item.status === 'applied');
  return (
    <div className="memory-import-step memory-review-intro">
      {recent.length > 0 && (
        <ul className="memory-import-recent" aria-label="Recent reviews">
          {recent.slice(0, 3).map((item) => (
            <li key={item.run_id}>
              <span>
                <strong>Review of {shortDate(item.applied_at)}</strong> ·{' '}
                {receiptWords(item)}
              </span>
              <IconButton
                size="sm"
                label="Undo this review"
                disabled={busy !== null}
                onClick={() => onUndo(item)}
              >
                <Undo2 size={14} aria-hidden />
              </IconButton>
            </li>
          ))}
        </ul>
      )}
      <h3 className="memory-import-heading">
        {importBatch ? 'Tidy up after your import' : 'Tidy up your memory'}
      </h3>
      <p className="memory-import-muted">
        {importBatch
          ? `Row-Bot reads the ${plural(overview.memories, 'memory', 'memories')} that import saved, with the saved memories closest to them, and explains every suggestion.`
          : `Row-Bot reads each of your ${plural(overview.memories, 'memory', 'memories')} and explains every suggestion.`}
      </p>
      <ul className="memory-review-promises">
        <li>
          <Check size={14} aria-hidden /> Nothing changes until you approve it
          in a list.
        </li>
        <li>
          <Check size={14} aria-hidden /> Your memory is backed up first, and
          you can undo the review for 30 days.
        </li>
        <li>
          <Check size={14} aria-hidden /> Long records and lessons are kept;
          only you can remove one.
        </li>
      </ul>
      {overview.memories === 0 ? (
        <EmptyState title="Nothing to review">
          {importBatch
            ? 'That import saved no memories, or they were undone.'
            : 'Memories appear here as Row-Bot learns about you.'}
        </EmptyState>
      ) : overview.model ? (
        <p className="memory-import-muted">
          {modelWords(overview.model)}
          {overview.many
            ? ` With this many memories it takes about ${plural(overview.model_calls, 'call')} to your chat model.`
            : ''}
        </p>
      ) : (
        <p className="memory-import-muted">
          Choose a chat model in Settings › Models to review your memory.
        </p>
      )}
    </div>
  );
}

function WorkingStep({ run }: { run: MemoryReviewRun | null }) {
  const job = run?.job;
  const total = job?.total ?? null;
  const label =
    total != null && total > 0
      ? `Reading your memories… ${Math.min(job?.done ?? 0, total).toLocaleString()} of ${plural(total, 'step')}`
      : 'Reading your memories…';
  return (
    <div className="memory-import-working" aria-live="polite">
      <Progress
        label={label}
        value={total ? Math.round(((job?.done ?? 0) / total) * 100) : undefined}
      />
      <p className="memory-import-muted">
        Nothing changes yet. You can stop at any time.
      </p>
    </div>
  );
}

const ACTION_ICON: Record<ReviewAction, ReactNode> = {
  remove: <Trash2 size={13} aria-hidden />,
  merge: <GitMerge size={13} aria-hidden />,
  rewrite: <WandSparkles size={13} aria-hidden />,
  split: <Scissors size={13} aria-hidden />,
  keep: <CheckCheck size={13} aria-hidden />,
};

const MARK_WORDS: Record<MemoryReviewProposal['marks'][number], string> = {
  long_record: 'Long record',
  lesson: 'Lesson',
  person_record: 'Your record',
};

function matches(item: MemoryReviewProposal, query: string) {
  if (!query) return true;
  const words = query.toLowerCase();
  return [item.subject, item.text, item.reason, item.proposed_text ?? '']
    .join(' ')
    .toLowerCase()
    .includes(words);
}

function ListStep({
  items,
  phone,
  chosen,
  onChosen,
  choices,
  onChoices,
  query,
  onQuery,
  shown,
  onShown,
  warnings,
  onRemove,
}: {
  items: readonly MemoryReviewProposal[];
  phone: boolean;
  chosen: ReadonlySet<string>;
  onChosen: (chosen: ReadonlySet<string>) => void;
  choices: Record<string, Choice>;
  onChoices: (choices: Record<string, Choice>) => void;
  query: string;
  onQuery: (query: string) => void;
  shown: string;
  onShown: (shown: string) => void;
  warnings: readonly string[];
  onRemove: (item: MemoryReviewProposal) => void;
}) {
  const visible = useMemo(
    () =>
      items.filter(
        (item) =>
          matches(item, query.trim()) &&
          (!shown ||
            (shown === 'marked'
              ? item.marks.some((mark) => mark !== 'person_record')
              : item.action === shown)),
      ),
    [items, query, shown],
  );
  const count = (action: ReviewAction) =>
    items.filter((item) => item.action === action).length;
  const setMany = (rows: readonly MemoryReviewProposal[], on: boolean) => {
    const next = new Set(chosen);
    for (const item of rows)
      if (item.action === 'keep') continue;
      else if (on) next.add(item.proposal_id);
      else next.delete(item.proposal_id);
    onChosen(next);
  };
  const changes = items.length - count('keep');
  if (items.length === 0)
    return (
      <EmptyState title="Nothing to review">
        There are no memories to look at here.
      </EmptyState>
    );
  return (
    <div className="memory-import-step memory-review-list">
      <StatGroup label="What Row-Bot suggests" className="memory-import-stats">
        <Stat label="Remove" value={count('remove')} />
        <Stat label="Merge" value={count('merge')} />
        <Stat label="Rewrite" value={count('rewrite')} />
        {count('split') > 0 && <Stat label="Split" value={count('split')} />}
        <Stat label="Keep" value={count('keep')} />
      </StatGroup>
      {warnings.length > 0 && (
        <ul className="memory-review-warnings">
          {warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}
      {changes === 0 && (
        <p className="memory-import-muted" role="status">
          Your memory looks tidy: Row-Bot suggests no changes.
        </p>
      )}
      <div className="memory-import-filters">
        <Input
          aria-label="Filter suggestions"
          placeholder="Filter"
          type="search"
          value={query}
          onChange={(event) => onQuery(event.target.value)}
          className="memory-review-filter"
        />
        <Select
          aria-label="Show"
          value={shown}
          onChange={(event) => onShown(event.target.value)}
        >
          <option value="">Everything</option>
          {GROUPS.filter((group) => count(group.action) > 0).map((group) => (
            <option key={group.action} value={group.action}>
              {group.title}
            </option>
          ))}
          <option value="marked">Long records and lessons</option>
        </Select>
        <span className="memory-import-filter-actions">
          <IconButton
            size="sm"
            label="Choose every suggested change shown"
            onClick={() => setMany(visible, true)}
          >
            <ListChecks size={15} aria-hidden />
          </IconButton>
          <IconButton
            size="sm"
            label="Choose none shown"
            onClick={() => setMany(visible, false)}
          >
            <ListX size={15} aria-hidden />
          </IconButton>
        </span>
      </div>
      {visible.length === 0 ? (
        <InlineEmpty>No suggestions match.</InlineEmpty>
      ) : (
        <div className="memory-review-groups">
          {GROUPS.map((group) => {
            const rows = visible.filter((item) => item.action === group.action);
            if (!rows.length) return null;
            return (
              <Group
                key={group.action}
                title={group.title}
                hint={group.hint}
                action={group.action}
                rows={rows}
                phone={phone}
                chosen={chosen}
                choices={choices}
                onCheck={(item, on) => {
                  const next = new Set(chosen);
                  if (on) next.add(item.proposal_id);
                  else next.delete(item.proposal_id);
                  onChosen(next);
                }}
                onMany={(on) => setMany(rows, on)}
                onChoice={(item, choice) => {
                  const next = { ...choices };
                  if (choice) next[item.proposal_id] = choice;
                  else delete next[item.proposal_id];
                  onChoices(next);
                }}
                onRemove={onRemove}
                open={
                  group.action !== 'keep' ||
                  shown === 'keep' ||
                  Boolean(query) ||
                  shown === 'marked'
                }
              />
            );
          })}
        </div>
      )}
    </div>
  );
}

function Group({
  title,
  hint,
  action,
  rows,
  phone,
  chosen,
  choices,
  onCheck,
  onMany,
  onChoice,
  onRemove,
  open,
}: {
  title: string;
  hint: string;
  action: ReviewAction;
  rows: readonly MemoryReviewProposal[];
  phone: boolean;
  chosen: ReadonlySet<string>;
  choices: Record<string, Choice>;
  onCheck: (item: MemoryReviewProposal, on: boolean) => void;
  onMany: (on: boolean) => void;
  onChoice: (item: MemoryReviewProposal, choice: Choice | undefined) => void;
  onRemove: (item: MemoryReviewProposal) => void;
  open: boolean;
}) {
  const [limit, setLimit] = useState(PAGE);
  const picked = rows.filter((item) => chosen.has(item.proposal_id)).length;
  const keep = action === 'keep';
  return (
    <Disclosure
      className="memory-review-group"
      defaultOpen={open}
      summary={
        <span className="memory-review-group-title">
          {ACTION_ICON[action]} {title}
        </span>
      }
      meta={
        keep
          ? rows.length.toLocaleString()
          : `${picked.toLocaleString()} of ${rows.length.toLocaleString()} chosen`
      }
    >
      <div className="memory-review-group-head">
        <small className="memory-import-muted">{hint}</small>
        {!keep && (
          <span className="memory-import-filter-actions">
            <IconButton
              size="sm"
              label={`Choose all in ${title}`}
              onClick={() => onMany(true)}
            >
              <ListChecks size={15} aria-hidden />
            </IconButton>
            <IconButton
              size="sm"
              label={`Choose none in ${title}`}
              onClick={() => onMany(false)}
            >
              <ListX size={15} aria-hidden />
            </IconButton>
          </span>
        )}
      </div>
      <ul className="memory-review-rows" aria-label={`${title} suggestions`}>
        {rows.slice(0, limit).map((item) => (
          <ProposalRow
            key={item.proposal_id}
            item={item}
            phone={phone}
            checked={chosen.has(item.proposal_id)}
            choice={choices[item.proposal_id]}
            onCheck={(on) => onCheck(item, on)}
            onChoice={(choice) => onChoice(item, choice)}
            onRemove={() => onRemove(item)}
          />
        ))}
      </ul>
      {rows.length > limit && (
        <Button
          className="small memory-review-more"
          onClick={() => setLimit((value) => value + PAGE)}
        >
          Show {Math.min(PAGE, rows.length - limit).toLocaleString()} more
        </Button>
      )}
    </Disclosure>
  );
}

function Diff({ before, after }: { before: string; after: string }) {
  const diff = useMemo(() => wordDiff(before, after), [before, after]);
  return (
    <div className="memory-review-diff">
      <p className="memory-review-now">
        <span className="memory-review-diff-label">Now</span>{' '}
        {diff.before.map((part, index) =>
          part.changed ? (
            <del key={index}>{part.word} </del>
          ) : (
            <span key={index}>{part.word} </span>
          ),
        )}
      </p>
      <p className="memory-review-becomes">
        <span className="memory-review-diff-label">Becomes</span>{' '}
        {diff.after.map((part, index) =>
          part.changed ? (
            <ins key={index}>{part.word} </ins>
          ) : (
            <span key={index}>{part.word} </span>
          ),
        )}
      </p>
    </div>
  );
}

function ProposalRow({
  item,
  phone,
  checked,
  choice,
  onCheck,
  onChoice,
  onRemove,
}: {
  item: MemoryReviewProposal;
  phone: boolean;
  checked: boolean;
  choice: Choice | undefined;
  onCheck: (on: boolean) => void;
  onChoice: (choice: Choice | undefined) => void;
  onRemove: () => void;
}) {
  const subjectId = useId();
  const reasonId = useId();
  const [editing, setEditing] = useState(false);
  const proposed = choice?.text ?? item.proposed_text ?? '';
  const [draft, setDraft] = useState(proposed);
  const editable = ['rewrite', 'merge', 'split'].includes(item.action);
  const limit = item.action === 'split' ? PROFILE_LIMIT : TEXT_LIMIT;
  const removing = Boolean(choice?.remove);
  const checkable = item.action !== 'keep';
  const longText =
    item.length > item.text.length
      ? ` … (${item.length.toLocaleString()} characters in all)`
      : '';
  return (
    <li
      className="memory-review-row"
      data-action={item.action}
      data-checked={checkable ? checked : removing}
    >
      {checkable ? (
        <label className="memory-import-check">
          <input
            type="checkbox"
            checked={checked}
            aria-labelledby={subjectId}
            aria-describedby={reasonId}
            onChange={(event) => onCheck(event.target.checked)}
          />
        </label>
      ) : (
        <span className="memory-review-check-space" aria-hidden />
      )}
      <div className="memory-review-body">
        <div className="memory-review-head">
          <span id={subjectId} className="memory-review-subject">
            {item.subject || 'Untitled memory'}
          </span>
          {item.category !== 'self_knowledge' && (
            <CategoryChip category={item.category} />
          )}
          {item.marks.map((mark) => (
            <span key={mark} className="memory-review-mark" data-mark={mark}>
              {MARK_WORDS[mark]}
            </span>
          ))}
        </div>
        <p
          id={reasonId}
          className="memory-review-reason"
          data-action={item.action}
        >
          {ACTION_ICON[removing ? 'remove' : item.action]}
          <span>{removing ? 'You chose to remove it' : item.reason}</span>
        </p>
        {editing ? (
          <form
            className="memory-review-edit"
            onSubmit={(event) => {
              event.preventDefault();
              const clean = draft.replace(/\s+/g, ' ').trim();
              if (!clean) return;
              onChoice(
                clean === item.proposed_text ? undefined : { text: clean },
              );
              setEditing(false);
            }}
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                event.preventDefault();
                event.stopPropagation();
                setEditing(false);
              }
            }}
          >
            <textarea
              className="input"
              aria-label={`New text for ${item.subject}`}
              rows={phone ? 5 : 3}
              maxLength={limit}
              value={draft}
              autoFocus
              onChange={(event) => setDraft(event.target.value)}
            />
            <span className="memory-review-edit-actions">
              <small className="memory-import-muted">
                {draft.length} / {limit}
              </small>
              <IconButton size="sm" label="Save edit" type="submit">
                <Check size={15} aria-hidden />
              </IconButton>
              <IconButton
                size="sm"
                label="Cancel edit"
                onClick={() => setEditing(false)}
              >
                <X size={15} aria-hidden />
              </IconButton>
            </span>
          </form>
        ) : item.action === 'rewrite' ? (
          <Diff before={item.text} after={proposed} />
        ) : item.action === 'merge' ? (
          <>
            <p className="memory-review-text">{item.text}</p>
            <Diff before={item.into?.text ?? ''} after={proposed} />
          </>
        ) : item.action === 'split' ? (
          <SplitDetail item={item} profile={proposed} />
        ) : (
          <p className="memory-review-text" data-clamp={item.action === 'keep'}>
            {item.text}
            {longText}
          </p>
        )}
        {choice?.text !== undefined && !editing && (
          <small className="memory-import-muted">Edited by you</small>
        )}
      </div>
      <span className="memory-review-actions">
        {editable && !editing && (
          <IconButton
            size="sm"
            label={`Edit the new text of ${item.subject}`}
            onClick={() => {
              setDraft(proposed);
              setEditing(true);
            }}
          >
            <Pencil size={14} aria-hidden />
          </IconButton>
        )}
        {item.removable &&
          (removing ? (
            <IconButton
              size="sm"
              label={`Keep ${item.subject}`}
              onClick={() => onChoice(undefined)}
            >
              <Undo2 size={14} aria-hidden />
            </IconButton>
          ) : (
            <IconButton
              size="sm"
              label={`Remove ${item.subject}…`}
              onClick={onRemove}
            >
              <Trash2 size={14} aria-hidden />
            </IconButton>
          ))}
      </span>
    </li>
  );
}

function SplitDetail({
  item,
  profile,
}: {
  item: MemoryReviewProposal;
  profile: string;
}) {
  const memories = item.split?.memories ?? [];
  const adds = memories.filter((entry) => entry.action === 'add');
  const others = memories.filter((entry) => entry.action !== 'add');
  return (
    <div className="memory-review-split">
      <p className="memory-review-text" data-clamp>
        <span className="memory-review-diff-label">Now</span> {item.text}
        {item.length > item.text.length
          ? ` … (${item.length.toLocaleString()} characters in all)`
          : ''}
      </p>
      <p className="memory-review-becomes">
        <span className="memory-review-diff-label">Your record becomes</span>{' '}
        {profile}
      </p>
      <Disclosure
        summary={`${plural(adds.length, 'new memory', 'new memories')}`}
        meta={
          others.length
            ? `${others.length.toLocaleString()} already saved`
            : undefined
        }
      >
        <ul className="memory-review-split-list">
          {memories.map((entry, index) => (
            <li key={`${index}:${entry.subject}`}>
              <CategoryChip category={entry.category} />{' '}
              <strong>{entry.subject}</strong>: {entry.text}
              {entry.action !== 'add' && (
                <small className="memory-import-muted">
                  {' '}
                  {entry.action === 'update'
                    ? `· adds to “${entry.saved_subject ?? ''}”`
                    : `· already saved as “${entry.saved_subject ?? ''}”`}
                </small>
              )}
            </li>
          ))}
        </ul>
      </Disclosure>
    </div>
  );
}

function ConfirmStep({
  approved,
  choices,
}: {
  approved: readonly MemoryReviewProposal[];
  choices: Record<string, Choice>;
}) {
  const count = (action: ReviewAction) =>
    approved.filter(
      (item) => item.action === action && !choices[item.proposal_id]?.remove,
    ).length;
  const named = approved.filter((item) => choices[item.proposal_id]?.remove);
  const removed = count('remove') + named.length;
  return (
    <div className="memory-import-step">
      <h3 className="memory-import-heading">
        {plural(approved.length, 'change')}; memory is backed up first
      </h3>
      <StatGroup label="What will change" className="memory-import-stats">
        <Stat label="Remove" value={removed} />
        <Stat label="Merge" value={count('merge')} />
        <Stat label="Rewrite" value={count('rewrite')} />
        {count('split') > 0 && <Stat label="Split" value={count('split')} />}
      </StatGroup>
      {named.length > 0 && (
        <p className="memory-import-muted">
          You chose to remove{' '}
          {named.map((item, index) => (
            <span key={item.proposal_id}>
              {index > 0 ? ', ' : ''}
              <strong>“{item.subject}”</strong>
            </span>
          ))}
          .
        </p>
      )}
      <p className="memory-import-muted">
        Row-Bot copies your memory before changing anything. You can undo the
        whole review for 30 days.
      </p>
    </div>
  );
}

function DoneStep({ receipt }: { receipt: MemoryReviewReceipt }) {
  const undone = receipt.status === 'undone';
  return (
    <div className="memory-import-step memory-import-done" role="status">
      <span
        className="memory-import-done-icon"
        data-undone={undone}
        aria-hidden
      >
        {undone ? <Undo2 size={22} /> : <CircleCheck size={22} />}
      </span>
      <h3 className="memory-import-heading">
        {undone ? 'Review undone' : 'Memory tidied up'}
      </h3>
      {undone ? (
        <p className="memory-import-muted">
          Every memory the review removed or changed reads as it did before.
        </p>
      ) : (
        <>
          <StatGroup label="What changed" className="memory-import-stats">
            <Stat label="Removed" value={receipt.removed} />
            <Stat label="Merged" value={receipt.merged} />
            <Stat label="Rewritten" value={receipt.rewritten} />
            {receipt.split > 0 && (
              <Stat label="New memories" value={receipt.added} />
            )}
          </StatGroup>
          <p className="memory-import-muted">
            {receipt.failed
              ? `${plural(receipt.failed, 'change')} couldn't be made. `
              : ''}
            Your memory was backed up first. You can undo this review until{' '}
            {shortDate(receipt.undo_until)}.
          </p>
        </>
      )}
    </div>
  );
}
