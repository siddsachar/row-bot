import { useEffect, useId, useMemo, useState, type ReactNode } from 'react';
import { MoreHorizontal } from 'lucide-react';
import { clientError } from '../../api/errors';
import type {
  LearningAdvanced,
  LearningComparison,
  LearningComparisonArm,
  LearningPersonalModel,
  LearningUse,
  LearningVersionDetails,
} from '../../api/types';
import { ModalTask, useNotify } from '../../ui/overlays';
import {
  Button,
  Disclosure,
  Input,
  Menu,
  Select,
  type MenuAction,
} from '../../ui/primitives';
import type { LearningIO } from './learning-state';
import ReviewSheet from './ReviewSheet';
import { usePhone } from './use-phone';
import {
  KINDS_OF_WORK,
  MAIN_UNAVAILABLE,
  VERDICT_WORDS,
  kindLabel,
  kindPhrase,
  plural,
  secondsWords,
  sizeWords,
  tokenWords,
  type KindOfWork,
} from './words';

/** Columns in the order UX §5 shows them: the main model, the baseline, the new version. */
function orderedArms(comparison: LearningComparison) {
  const rank = { main: 0, baseline: 1, new: 2 } as const;
  return [...comparison.arms].sort((a, b) => rank[a.role] - rank[b.role]);
}

/** "v1 (new)", "Starting model", "GPT-6.1 Sol": the column names people see. */
export function armLabel(arm: LearningComparisonArm) {
  if (arm.role === 'baseline' && arm.key === 'base') return 'Starting model';
  return arm.label;
}

function cellWords(passed: number, counted: number, total: number) {
  return counted < total ? `${passed} of ${counted}` : String(passed);
}

/**
 * The versions comparison (UX §5): a table with real headers on a desktop, labelled rows on
 * a phone (UX §10). Counts always with their denominator.
 */
export function ComparisonTable({
  comparison,
}: {
  comparison: LearningComparison;
}) {
  const phone = usePhone();
  const arms = orderedArms(comparison);
  const captionId = useId();
  const extra = [
    {
      label: 'Time per task',
      value: (arm: LearningComparisonArm) => secondsWords(arm.seconds_per_task),
    },
    {
      label: 'Cloud tokens per task',
      value: (arm: LearningComparisonArm) =>
        arm.cloud_tokens_per_task == null
          ? '–'
          : tokenWords(arm.cloud_tokens_per_task),
    },
  ];
  if (phone)
    return (
      <div className="learning-compare-rows" aria-labelledby={captionId}>
        <p id={captionId} className="visually-hidden">
          Tasks passed by each model, out of the number in brackets
        </p>
        {comparison.rows.map((row) => (
          <div className="learning-compare-row" key={row.kind}>
            <p className="learning-compare-row-title">
              {row.label} ({row.total})
              {row.verdict !== 'too_few' && (
                <span className="learning-verdict" data-verdict={row.verdict}>
                  {' · '}
                  {VERDICT_WORDS[row.verdict]}
                </span>
              )}
            </p>
            <p className="learning-muted">
              {arms
                .map((arm) => {
                  const cell = row.cells.find((item) => item.key === arm.key);
                  return cell
                    ? `${armLabel(arm)}: ${cellWords(cell.passed, cell.counted, row.total)} passed`
                    : `${armLabel(arm)}: –`;
                })
                .join(' · ')}
            </p>
          </div>
        ))}
        {extra.map((item) => (
          <div className="learning-compare-row" key={item.label}>
            <p className="learning-compare-row-title">{item.label}</p>
            <p className="learning-muted">
              {arms
                .map((arm) => `${armLabel(arm)}: ${item.value(arm)}`)
                .join(' · ')}
            </p>
          </div>
        ))}
      </div>
    );
  return (
    <table className="learning-results learning-compare">
      <caption className="visually-hidden">
        Tasks passed by each model, out of the number in brackets
      </caption>
      <thead>
        <tr>
          <th scope="col">Kind of work</th>
          {arms.map((arm) => (
            <th scope="col" key={arm.key}>
              {armLabel(arm)}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {comparison.rows.map((row) => (
          <tr key={row.kind}>
            <th scope="row">
              {row.label} ({row.total})
              {row.verdict !== 'too_few' && (
                <span className="learning-verdict" data-verdict={row.verdict}>
                  {VERDICT_WORDS[row.verdict]}
                </span>
              )}
            </th>
            {arms.map((arm) => {
              const cell = row.cells.find((item) => item.key === arm.key);
              return (
                <td key={arm.key} data-label={armLabel(arm)}>
                  {cell ? cellWords(cell.passed, cell.counted, row.total) : '–'}
                </td>
              );
            })}
          </tr>
        ))}
        {extra.map((item) => (
          <tr key={item.label}>
            <th scope="row">{item.label}</th>
            {arms.map((arm) => (
              <td key={arm.key} data-label={armLabel(arm)}>
                {item.value(arm)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** The notes under a comparison: what was left out, and why a model is missing. */
function ComparisonNotes({ comparison }: { comparison: LearningComparison }) {
  const notes = [
    comparison.note,
    comparison.coding_left_out
      ? 'Coding tasks need Docker or Podman, so they were left out.'
      : '',
    comparison.baseline_unavailable
      ? 'The starting model couldn’t run here, so the new version was compared with your main model only.'
      : '',
    comparison.main_unavailable
      ? MAIN_UNAVAILABLE[comparison.main_unavailable]
      : '',
  ].filter(Boolean);
  return (
    <>
      {notes.map((note) => (
        <p className="learning-muted" key={note}>
          {note}
        </p>
      ))}
    </>
  );
}

function useRun(io: LearningIO) {
  const notify = useNotify();
  const [busy, setBusy] = useState(false);
  async function run(
    action: Parameters<LearningIO['run']>[0],
    fields: Parameters<LearningIO['run']>[1] = {},
    undo?: () => void,
  ) {
    setBusy(true);
    try {
      const receipt = await io.run(action, fields);
      notify(
        receipt.summary,
        undefined,
        undo ? { label: 'Undo', onAction: undo } : undefined,
      );
      return true;
    } catch (cause) {
      notify(clientError(cause).message, 'danger');
      return false;
    } finally {
      setBusy(false);
    }
  }
  return { busy, run };
}

/** "Use v1 for document lookups": the person's choice (D24), with Undo on its notice. */
function useUse(io: LearningIO) {
  const { busy, run } = useRun(io);
  const startTrial = (version: number, kind: KindOfWork) =>
    run('use_version', { version, kind }, () => {
      void io.run('undo_use', { kind });
    });
  return { busy, run, startTrial };
}

/** Choose another kind of work to give a version (from the … menu). */
function UseForDialog({
  open,
  onOpenChange,
  io,
  version,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  io: LearningIO;
  version: number;
}) {
  const phone = usePhone();
  const { busy, startTrial } = useUse(io);
  const [kind, setKind] = useState<KindOfWork>('document_lookups');
  const name = useId();
  return (
    <ModalTask
      open={open}
      onOpenChange={onOpenChange}
      title={`Use v${version} for…`}
      description="It answers that kind of work on trial. Your main model finishes anything it gets stuck on, and Undo goes back at once."
      kind={phone ? 'sheet' : 'dialog'}
    >
      <fieldset className="learning-fieldset">
        <legend className="visually-hidden">Kind of work</legend>
        {KINDS_OF_WORK.map((item) => (
          <label key={item.value} className="check-field learning-check">
            <input
              type="radio"
              name={name}
              value={item.value}
              checked={kind === item.value}
              onChange={() => setKind(item.value)}
            />
            <span>{item.label}</span>
          </label>
        ))}
      </fieldset>
      <div className="learning-sheet-actions">
        <Button onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button
          variant="primary"
          disabled={busy}
          data-initial-focus
          onClick={() =>
            void startTrial(version, kind).then(
              (done) => done && onOpenChange(false),
            )
          }
        >
          Use for {kindPhrase(kind)}
        </Button>
      </div>
    </ModalTask>
  );
}

/** Export… (D14): the files go to a folder; their names show here, never on a main screen (O54). */
export function ExportDialog({
  open,
  onOpenChange,
  io,
  version,
  suggested,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  io: LearningIO;
  version: number;
  suggested: string;
}) {
  const phone = usePhone();
  const { busy, run } = useRun(io);
  const [folder, setFolder] = useState(suggested);
  const [details, setDetails] = useState<LearningVersionDetails | null>(null);
  const field = useId();
  useEffect(() => {
    if (!open) return;
    setFolder(suggested);
    let live = true;
    io.version?.(version).then(
      (value) => live && setDetails(value),
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [open, suggested, io, version]);
  const total = (details?.files ?? []).reduce(
    (sum, file) => sum + file.bytes,
    0,
  );
  return (
    <ModalTask
      open={open}
      onOpenChange={onOpenChange}
      title={`Export v${version}`}
      description="Saves a ready-to-run copy of your personal model, the part it learned and a card that says how it was made."
      kind={phone ? 'sheet' : 'dialog'}
    >
      <div className="learning-sheet">
        <div className="learning-field">
          <label className="learning-label" htmlFor={field}>
            Folder
          </label>
          <Input
            id={field}
            value={folder}
            onChange={(event) => setFolder(event.target.value)}
            aria-describedby={`${field}-hint`}
            spellCheck={false}
            autoComplete="off"
          />
          <small id={`${field}-hint`} className="learning-muted">
            A new folder named row-bot-personal-v{version} is made inside it.
          </small>
        </div>
        {details && details.files.length > 0 && (
          <div className="learning-stack-tight">
            <p className="learning-label">Files ({sizeWords(total)} in all)</p>
            <ul className="learning-list learning-files">
              {details.files.map((file) => (
                <li key={file.name}>
                  <code>{file.name}</code> · {sizeWords(file.bytes)}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
      <div className="learning-sheet-actions">
        <Button onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button
          variant="primary"
          disabled={busy || !folder.trim()}
          data-initial-focus
          onClick={() =>
            void run('export_version', { version, folder: folder.trim() }).then(
              (done) => done && onOpenChange(false),
            )
          }
        >
          Export
        </Button>
      </div>
    </ModalTask>
  );
}

/** Delete version: what stops, what goes. */
function DeleteVersionDialog({
  open,
  onOpenChange,
  io,
  version,
  uses,
  bytes,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  io: LearningIO;
  version: number;
  uses: LearningUse[];
  bytes: number;
}) {
  const phone = usePhone();
  const { busy, run } = useRun(io);
  const answering = uses.filter((item) => item.version === version);
  return (
    <ModalTask
      open={open}
      onOpenChange={onOpenChange}
      title={`Delete v${version}?`}
      description={
        answering.length
          ? `${answering.map((item) => item.label).join(', ')} go back to ${answering.some((item) => item.previous_version) ? 'the version used before' : 'your main model'} at once.`
          : 'It isn’t answering anything now.'
      }
      kind={phone ? 'sheet' : 'dialog'}
    >
      <p className="learning-muted">
        Its files ({sizeWords(bytes)}) are deleted from this computer. What
        Learning recorded about your work stays, so a new version can be
        trained.
      </p>
      <div className="learning-sheet-actions">
        <Button onClick={() => onOpenChange(false)} data-initial-focus>
          Keep it
        </Button>
        <Button
          variant="danger"
          disabled={busy}
          onClick={() =>
            void run('delete_version', { version }).then(
              (done) => done && onOpenChange(false),
            )
          }
        >
          Delete v{version}
        </Button>
      </div>
    </ModalTask>
  );
}

/** Experiment details (UX §4, §7): data, method, versions, every run. Read-only. */
export function ExperimentDetails({
  details,
}: {
  details: LearningVersionDetails | null;
}) {
  if (!details)
    return (
      <p className="learning-muted" role="status">
        Loading experiment details…
      </p>
    );
  const data = details.data;
  return (
    <div className="learning-details">
      <section aria-label="Data">
        <h3>Data</h3>
        <p>
          {plural(data.examples, 'example')} of your work from{' '}
          {plural(data.conversations, 'conversation')}
          {data.validation
            ? `; ${plural(data.validation, 'newer example')} held back to check it`
            : ''}
          .
        </p>
        {data.providers.length > 0 && (
          <p className="learning-muted">
            Answers from: {data.providers.join(', ')}.
          </p>
        )}
        {data.left_out.length > 0 && (
          <p className="learning-muted">
            Left out:{' '}
            {data.left_out
              .map((item) => `${item.count.toLocaleString()} ${item.reason}`)
              .join(', ')}
            .
          </p>
        )}
        {data.terms_note && (
          <p className="learning-muted">
            Training on answers from OpenAI, Anthropic or Google models may be
            limited by their terms. Check your provider’s terms.
          </p>
        )}
      </section>
      <section aria-label="Method">
        <h3>Method</h3>
        <p>
          {details.method.name}: LoRA rank {details.method.rank}, learning rate{' '}
          {details.method.learning_rate},{' '}
          {plural(details.method.epochs, 'pass')},{' '}
          {details.method.context.toLocaleString()}-token examples, saved at{' '}
          {details.method.quantization}.
        </p>
      </section>
      <section aria-label="Versions">
        <h3>Versions</h3>
        <dl className="learning-details-list">
          {details.versions.map((item) => (
            <div key={item.name}>
              <dt>{item.name}</dt>
              <dd>{item.value}</dd>
            </div>
          ))}
        </dl>
      </section>
      <section aria-label="Every run">
        <h3>Every run</h3>
        {details.runs.length ? (
          <ul className="learning-list">
            {details.runs.map((item) => (
              <li key={`${item.suite}-${item.arm}`}>
                {item.suite}, {item.arm}: {item.passed} of {item.total} passed
              </li>
            ))}
          </ul>
        ) : (
          <p className="learning-muted">Not compared yet.</p>
        )}
        {details.budget_tokens_per_task > 0 && (
          <p className="learning-muted">
            Every model had the same budget:{' '}
            {tokenWords(details.budget_tokens_per_task)} tokens a task.
          </p>
        )}
      </section>
      {details.files.length > 0 && (
        <section aria-label="Files">
          <h3>Files</h3>
          <ul className="learning-list learning-files">
            {details.files.map((file) => (
              <li key={file.name}>
                <code>{file.name}</code> · {sizeWords(file.bytes)}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

/** The review sheet for a version's comparison (the shared ReviewSheet, UX §4/§5). */
export function VersionReview({
  open,
  onOpenChange,
  io,
  model,
  comparison,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  io: LearningIO;
  model: LearningPersonalModel;
  comparison: LearningComparison;
}) {
  const { busy, run, startTrial } = useUse(io);
  const [details, setDetails] = useState<LearningVersionDetails | null>(null);
  const [showDetails, setShowDetails] = useState(false);
  const [useFor, setUseFor] = useState(false);
  const [exporting, setExporting] = useState(false);
  const version = comparison.version;
  useEffect(() => {
    if (!open) {
      setShowDetails(false);
      return;
    }
    let live = true;
    io.version?.(version).then(
      (value) => live && setDetails(value),
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [open, io, version]);
  const baseline = comparison.arms.find((arm) => arm.role === 'baseline');
  const why = baseline
    ? `Compared with ${baseline.key === 'base' ? 'the starting model it was trained from' : `${baseline.label.replace(' (in use)', '')}, the version in use`} and your main model, on tasks none of them had seen and on work of yours held back from training, at the same budget.`
    : 'Compared with your main model on tasks it hadn’t seen and on work of yours held back from training, at the same budget.';
  const recommend = comparison.recommend;
  return (
    <>
      <ReviewSheet
        open={open}
        onOpenChange={onOpenChange}
        change={{
          title: `Personal model v${version}`,
          kind: 'New version',
          why,
          change: '',
          results: { rows: [], note: '' },
        }}
        results={
          <>
            <ComparisonTable comparison={comparison} />
            <p className="learning-compare-line">{comparison.line}</p>
            <ComparisonNotes comparison={comparison} />
          </>
        }
        primary={
          recommend
            ? {
                label: `Use v${version} for ${kindPhrase(recommend)}`,
                disabled: busy,
                onSelect: () =>
                  void startTrial(version, recommend).then(
                    (done) => done && onOpenChange(false),
                  ),
              }
            : undefined
        }
        onNotNow={() => {
          void run('review_version', { version }).then(() =>
            onOpenChange(false),
          );
        }}
        notNowLabel={recommend ? 'Not now' : 'Close'}
        menuActions={[
          {
            label: 'Use for another kind of work…',
            onSelect: () => setUseFor(true),
          },
          { label: 'Export…', onSelect: () => setExporting(true) },
        ]}
        onDetails={() => setShowDetails((value) => !value)}
        detailsOpen={showDetails}
        details={<ExperimentDetails details={details} />}
        onDontSuggest={undefined}
        onUse={() => undefined}
        onTestAgain={() => undefined}
      />
      <UseForDialog
        open={useFor}
        onOpenChange={setUseFor}
        io={io}
        version={version}
      />
      <ExportDialog
        open={exporting}
        onOpenChange={setExporting}
        io={io}
        version={version}
        suggested={model.export_folder ?? ''}
      />
    </>
  );
}

/** "Versions" on the Personal model view: the comparison, its state, and what the person can do. */
export function VersionsSection({
  io,
  model,
  onReview,
}: {
  io: LearningIO;
  model: LearningPersonalModel;
  /** Open the version's review sheet (it lives with the Learning tab, so Overview opens it too). */
  onReview: () => void;
}) {
  const { busy, run, startTrial } = useUse(io);
  const [useFor, setUseFor] = useState<number | null>(null);
  const [exporting, setExporting] = useState<number | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);
  const comparison = model.comparison ?? null;
  const versions = model.versions ?? [];
  const uses = model.uses ?? [];
  // Until its comparison starts, the version's own card says it is ready to compare.
  if (!versions.length || !comparison || comparison.state === 'waiting')
    return null;
  const version = comparison.version;
  const bytes =
    versions.find((item) => item.version === version)?.file_bytes ?? 0;
  const menu: MenuAction[] = [
    ...(comparison.state === 'done'
      ? [
          {
            label: 'Use for another kind of work…',
            onSelect: () => setUseFor(version),
          },
          { label: 'Review and experiment details', onSelect: onReview },
          {
            label: 'Compare again',
            onSelect: () => void run('compare_version', { version }),
          },
        ]
      : []),
    { label: 'Export…', onSelect: () => setExporting(version) },
    {
      label: `Delete v${version}…`,
      danger: true,
      onSelect: () => setDeleting(version),
    },
  ];
  let body: ReactNode;
  const label = orderedArms(comparison)
    .filter((arm) => arm.role !== 'new')
    .map((arm) => (arm.key === 'base' ? 'its starting model' : arm.label))
    .join(' and ');
  if (comparison.state === 'done') {
    body = (
      <>
        <ComparisonTable comparison={comparison} />
        <p className="learning-compare-line">{comparison.line}</p>
        <ComparisonNotes comparison={comparison} />
      </>
    );
  } else if (comparison.state === 'running') {
    body = (
      <div className="learning-progress" role="status">
        <p className="learning-training-title">
          Comparing v{version} with {label || 'your main model'} ·{' '}
          {comparison.total
            ? `${comparison.done.toLocaleString()} of ${comparison.total.toLocaleString()} tasks`
            : 'starting'}
        </p>
        {comparison.total > 0 && (
          <progress
            max={comparison.total}
            value={comparison.done}
            aria-label={`Comparing v${version}`}
          />
        )}
        <p className="learning-muted">
          Runs on this computer. Stops while you use Row-Bot and goes on later.
        </p>
      </div>
    );
  } else {
    const words: Record<string, string> = {
      paused: `Comparing v${version} paused while you used Row-Bot. It goes on when this computer is free.`,
      stopped: `Comparing v${version} stopped. It runs again later.`,
      failed: `Row-Bot couldn’t compare v${version}. It tries again later.`,
    };
    body = <p>{words[comparison.state] ?? words.stopped}</p>;
  }
  const recommend =
    comparison.state === 'done' && !comparison.reviewed
      ? comparison.recommend
      : null;
  const usedFor = uses.filter((item) => item.version === version);
  const actions =
    !!recommend ||
    ['running', 'paused', 'stopped', 'failed'].includes(comparison.state);
  return (
    <section className="monitor-section" aria-label="Versions">
      <header className="monitor-section-head">
        <h3>Versions</h3>
        <span className="learning-section-more">
          <Menu
            label={`More for v${version}`}
            iconOnly
            variant="ghost"
            className="icon-action"
            actions={menu}
          >
            <MoreHorizontal size={16} aria-hidden />
          </Menu>
        </span>
      </header>
      <div className="learning-training learning-versions">
        {body}
        {actions && (
          <div className="learning-actions-inline">
            {recommend && !usedFor.some((item) => item.kind === recommend) && (
              <Button
                variant="primary"
                className="small"
                disabled={busy}
                onClick={() => void startTrial(version, recommend)}
              >
                Use v{version} for {kindPhrase(recommend)}
              </Button>
            )}
            {recommend && (
              <Button
                variant="ghost"
                className="small"
                disabled={busy}
                onClick={() => void run('review_version', { version })}
              >
                Not now
              </Button>
            )}
            {comparison.state === 'running' && (
              <Button
                className="small"
                disabled={busy}
                onClick={() => void run('stop_compare')}
              >
                Stop
              </Button>
            )}
            {['paused', 'stopped', 'failed'].includes(comparison.state) && (
              <Button
                className="small"
                disabled={busy}
                onClick={() => void run('compare_version', { version })}
              >
                Compare now
              </Button>
            )}
          </div>
        )}
      </div>
      {useFor !== null && (
        <UseForDialog
          open
          onOpenChange={(open) => !open && setUseFor(null)}
          io={io}
          version={useFor}
        />
      )}
      {exporting !== null && (
        <ExportDialog
          open
          onOpenChange={(open) => !open && setExporting(null)}
          io={io}
          version={exporting}
          suggested={model.export_folder ?? ''}
        />
      )}
      {deleting !== null && (
        <DeleteVersionDialog
          open
          onOpenChange={(open) => !open && setDeleting(null)}
          io={io}
          version={deleting}
          uses={uses}
          bytes={bytes}
        />
      )}
    </section>
  );
}

/** "v1 for document lookups · on trial since 9 Oct · 18 requests: 17 answered, 1 finished by your main model". */
export function usageWords(item: LearningUse, mainName: string) {
  const since = item.since
    ? new Intl.DateTimeFormat(undefined, {
        day: 'numeric',
        month: 'short',
      }).format(new Date(item.since))
    : '';
  const tally = item.state === 'trial' ? item.trial : item.total;
  const numbers = tally.requests
    ? `${plural(tally.requests, 'request')}: ${tally.answered.toLocaleString()} answered${tally.fallbacks ? `, ${tally.fallbacks.toLocaleString()} finished by ${mainName}` : ''}`
    : 'No requests yet';
  return {
    title: `${item.label} · v${item.version}`,
    status:
      item.state === 'trial'
        ? `On trial${since ? ` since ${since}` : ''}`
        : `In use${since ? ` since ${since}` : ''}`,
    numbers,
  };
}

/** "In use for": each kind of work a version answers, with Keep (a trial that's due) and Undo. */
export function UsesSection({
  io,
  model,
  mainName,
}: {
  io: LearningIO;
  model: LearningPersonalModel;
  mainName: string;
}) {
  const { busy, run } = useRun(io);
  const uses = model.uses ?? [];
  if (!uses.length) return null;
  return (
    <section className="monitor-section" aria-label="In use for">
      <header className="monitor-section-head">
        <h3>In use for</h3>
      </header>
      <ul className="learning-uses">
        {uses.map((item) => {
          const words = usageWords(item, mainName);
          return (
            <li
              key={item.kind}
              className="learning-use"
              data-state={item.state}
            >
              <div className="learning-use-words">
                <p className="learning-training-title">{words.title}</p>
                <p className="learning-muted">
                  {words.status} · {words.numbers}
                </p>
              </div>
              <div className="learning-actions-inline">
                {item.state === 'trial' && item.keep_due && (
                  <Button
                    variant="primary"
                    className="small"
                    disabled={busy}
                    onClick={() =>
                      void run('keep_version', { kind: item.kind })
                    }
                  >
                    Keep
                  </Button>
                )}
                <Button
                  className="small"
                  disabled={busy}
                  aria-label={`Undo v${item.version} for ${kindPhrase(item.kind)}`}
                  onClick={() => void run('undo_use', { kind: item.kind })}
                >
                  Undo
                </Button>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** "0.0002": the learning rate as a plain decimal (every choice is one). */
const RATE_WORDS = (rate: number) =>
  rate.toLocaleString(undefined, { maximumSignificantDigits: 3 });

/** Advanced › (D9): recommended values filled in, Reset to recommended. */
export function AdvancedSection({
  io,
  advanced,
}: {
  io: LearningIO;
  advanced: LearningAdvanced;
}) {
  const { busy, run } = useRun(io);
  const [values, setValues] = useState(advanced.values);
  useEffect(() => setValues(advanced.values), [advanced.values]);
  const ids = {
    model: useId(),
    rank: useId(),
    rate: useId(),
    epochs: useId(),
    context: useId(),
    method: useId(),
  };
  const changed = useMemo(
    () => JSON.stringify(values) !== JSON.stringify(advanced.values),
    [values, advanced.values],
  );
  const recommended = advanced.recommended;
  const row = (
    id: string,
    label: string,
    control: ReactNode,
    note?: string,
  ) => (
    <div className="learning-field learning-advanced-row" key={id}>
      <label className="learning-label" htmlFor={id}>
        {label}
      </label>
      {control}
      {note && <small className="learning-muted">{note}</small>}
    </div>
  );
  return (
    <Disclosure
      summary="Advanced"
      meta={advanced.changed ? 'Changed' : 'Recommended'}
      className="learning-advanced"
    >
      <div className="learning-sheet">
        <p className="learning-muted">
          Recommended for this computer. Changes apply to the next training run.
        </p>
        {row(
          ids.model,
          'Starting model',
          <Select
            id={ids.model}
            value={values.starting_model}
            onChange={(event) =>
              setValues({ ...values, starting_model: event.target.value })
            }
          >
            {advanced.choices.starting_model.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </Select>,
        )}
        {row(
          ids.rank,
          'LoRA rank',
          <Select
            id={ids.rank}
            value={String(values.rank)}
            onChange={(event) =>
              setValues({ ...values, rank: Number(event.target.value) })
            }
          >
            {advanced.choices.rank.map((item) => (
              <option key={item} value={item}>
                {item}
                {item === recommended.rank ? ' (recommended)' : ''}
              </option>
            ))}
          </Select>,
          'Higher learns more detail and needs more memory.',
        )}
        {row(
          ids.rate,
          'Learning rate',
          <Select
            id={ids.rate}
            value={String(values.learning_rate)}
            onChange={(event) =>
              setValues({
                ...values,
                learning_rate: Number(event.target.value),
              })
            }
          >
            {advanced.choices.learning_rate.map((item) => (
              <option key={item} value={item}>
                {RATE_WORDS(item)}
                {item === recommended.learning_rate ? ' (recommended)' : ''}
              </option>
            ))}
          </Select>,
        )}
        {row(
          ids.epochs,
          'Passes over your examples',
          <Select
            id={ids.epochs}
            value={String(values.epochs)}
            onChange={(event) =>
              setValues({ ...values, epochs: Number(event.target.value) })
            }
          >
            {advanced.choices.epochs.map((item) => (
              <option key={item} value={item}>
                {item}
                {item === recommended.epochs ? ' (recommended)' : ''}
              </option>
            ))}
          </Select>,
          'More passes take longer and can make it repeat your examples too closely.',
        )}
        {row(
          ids.context,
          'Context length',
          <Select
            id={ids.context}
            value={String(values.context)}
            disabled={advanced.choices.context.length < 2}
            onChange={(event) =>
              setValues({ ...values, context: Number(event.target.value) })
            }
          >
            {advanced.choices.context.map((item) => (
              <option key={item} value={item}>
                {Math.round(item / 1024)}K tokens
                {item === recommended.context ? ' (recommended)' : ''}
              </option>
            ))}
          </Select>,
          advanced.choices.context.length < 2
            ? 'The most this graphics card fits.'
            : undefined,
        )}
        <div className="learning-field learning-advanced-row">
          <span className="learning-label" id={`${ids.method}-mix`}>
            Data mix
          </span>
          <ul
            className="learning-list learning-mix"
            aria-labelledby={`${ids.method}-mix`}
          >
            {advanced.data_mix.map((item) => (
              <li key={item.id}>
                {item.label}:{' '}
                {item.available ? `${item.share}%` : 'not available yet'}
              </li>
            ))}
          </ul>
        </div>
        {row(
          ids.method,
          'Method',
          <Select id={ids.method} value={values.method} disabled>
            <option value="standard">Standard</option>
          </Select>,
        )}
        <div className="learning-actions-inline">
          <Button
            variant="primary"
            className="small"
            disabled={busy || !changed}
            onClick={() => void run('save_advanced', { advanced: values })}
          >
            Save
          </Button>
          <Button
            className="small"
            disabled={busy || !advanced.changed}
            onClick={() => void run('reset_advanced')}
          >
            Reset to recommended
          </Button>
        </div>
      </div>
    </Disclosure>
  );
}

/** Overview › Needs you: the personal model's lines (UX §3, §5). */
export function PersonalModelNeeds({
  io,
  onReview,
}: {
  io: LearningIO;
  onReview: (version: number) => void;
}) {
  const { busy, run } = useRun(io);
  const items = io.snapshot?.needs_you ?? [];
  const uses = io.snapshot?.personal_model.uses ?? [];
  const mainName = io.snapshot?.spending.model_name || 'your main model';
  if (!items.length) return null;
  return (
    <ul className="learning-needs">
      {items.map((item) => {
        const trial =
          item.type === 'keep'
            ? uses.find((entry) => entry.kind === item.kind)
            : undefined;
        return (
          <li key={item.id} className="learning-need" data-type={item.type}>
            <div className="learning-use-words">
              <p className="learning-training-title">{item.title}</p>
              {item.type === 'retrain' && (
                <p className="learning-muted">
                  Deleting a chat doesn’t remove what a model already learned
                  from it.
                </p>
              )}
              {trial && (
                <p className="learning-muted">
                  {usageWords(trial, mainName).numbers} on trial.
                </p>
              )}
            </div>
            <div className="learning-actions-inline">
              {item.type === 'compare' && (
                <Button
                  variant="primary"
                  className="small"
                  onClick={() => onReview(item.version)}
                >
                  Review
                </Button>
              )}
              {item.type === 'keep' && item.kind && (
                <>
                  <Button
                    variant="primary"
                    className="small"
                    disabled={busy}
                    onClick={() =>
                      void run('keep_version', { kind: item.kind ?? undefined })
                    }
                  >
                    Keep
                  </Button>
                  <Button
                    className="small"
                    disabled={busy}
                    onClick={() =>
                      void run('keep_not_now', { kind: item.kind ?? undefined })
                    }
                  >
                    Not now
                  </Button>
                  <Button
                    variant="ghost"
                    className="small"
                    disabled={busy}
                    aria-label={`Undo v${item.version} for ${kindPhrase(item.kind)}`}
                    onClick={() =>
                      void run('undo_use', { kind: item.kind ?? undefined })
                    }
                  >
                    Undo
                  </Button>
                </>
              )}
              {item.type === 'retrain' && (
                <>
                  <Button
                    variant="primary"
                    className="small"
                    disabled={busy}
                    onClick={() =>
                      void run('retrain_without', { version: item.version })
                    }
                  >
                    Retrain without it
                  </Button>
                  <Button
                    variant="ghost"
                    className="small"
                    disabled={busy}
                    onClick={() => void run('dismiss', { item_id: item.id })}
                  >
                    Not now
                  </Button>
                </>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export { kindLabel };
