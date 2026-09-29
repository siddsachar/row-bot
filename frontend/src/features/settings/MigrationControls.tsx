import { useEffect, useRef, useState } from 'react';
import { ArrowRight, FolderOpen, FolderSearch, RotateCcw } from 'lucide-react';
import type {
  MigrationApplyCommand,
  MigrationApplyReceipt,
  MigrationApplyReview,
  MigrationApplyReviewRequest,
  MigrationPreview,
  MigrationScanRequest,
  MigrationSources,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useRuntime } from '../../runtime';
import {
  Button,
  CompactAction,
  Input,
  Select,
  Toggle,
} from '../../ui/primitives';
import { humanizeToken } from '../../ui/format';

type Owner = {
  scan: (body: MigrationScanRequest) => Promise<MigrationPreview>;
  review: (body: MigrationApplyReviewRequest) => Promise<MigrationApplyReview>;
  apply: (body: MigrationApplyCommand) => Promise<MigrationApplyReceipt>;
  receipt: (commandId: string) => Promise<MigrationApplyReceipt>;
  /** Which old apps are in their usual folders (this computer only). */
  sources?: () => Promise<MigrationSources>;
  /** Browse for the old app's folder in the desktop app (a one-use grant). */
  pick?: () => Promise<string | null>;
};

const pendingKey = 'row-bot:migration:pending:v1';

function savedCommand(): MigrationApplyCommand | null {
  try {
    const raw = sessionStorage.getItem(pendingKey);
    if (!raw || raw.length > 200_000) return null;
    const value = JSON.parse(raw) as MigrationApplyCommand;
    return typeof value.command_id === 'string' &&
      typeof value.review_digest === 'string'
      ? value
      : null;
  } catch {
    return null;
  }
}

export function MigrationControls({
  owner,
  canBrowse = false,
}: {
  owner: Owner;
  canBrowse?: boolean;
}) {
  const [provider, setProvider] = useState<'hermes' | 'openclaw'>('hermes');
  const [source, setSource] = useState('');
  const [target, setTarget] = useState('');
  const [includeSecrets, setIncludeSecrets] = useState(false);
  const [overwrite, setOverwrite] = useState(false);
  const [preview, setPreview] = useState<MigrationPreview | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [review, setReview] = useState<MigrationApplyReview | null>(null);
  const [pending, setPending] = useState<MigrationApplyCommand | null>(
    savedCommand,
  );
  const [receipt, setReceipt] = useState<MigrationApplyReceipt | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [missingReceipt, setMissingReceipt] = useState(false);
  const [detected, setDetected] = useState<MigrationSources['sources']>([]);
  const active = useRef(false);
  const usual = detected.find((item) => item.provider === provider);
  // A folder picked with Browse: its one-use grant, then the preview that
  // spent it (a rescan with other choices reuses that preview's folder).
  const [chosen, setChosen] = useState<string | null>(null);
  const [chosenPlan, setChosenPlan] = useState<string | null>(null);
  const usingChosen = !source.trim() && Boolean(chosen || chosenPlan);
  const useUsual = !source.trim() && !usingChosen && Boolean(usual?.found);

  useEffect(() => {
    if (!owner.sources) return;
    let cancelled = false;
    void owner.sources().then(
      (result) => {
        if (cancelled) return;
        setDetected(result.sources);
        // Start with the app that is actually on this computer.
        const found = result.sources.filter((item) => item.found);
        if (found.length === 1) setProvider(found[0].provider);
      },
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, [owner]);

  useEffect(() => {
    const original = savedCommand();
    if (!original) return;
    let cancelled = false;
    void owner.receipt(original.command_id).then(
      (result) => {
        if (cancelled) return;
        setReceipt(result);
        sessionStorage.removeItem(pendingKey);
        setPending(null);
        setError('');
      },
      (cause) => {
        if (!cancelled) {
          setMissingReceipt(
            clientError(cause).code === 'migration_receipt_missing',
          );
          setError(
            `${clientError(cause).message} Check the original migration before starting another.`,
          );
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [owner]);

  function forgetChosen() {
    setChosen(null);
    setChosenPlan(null);
  }

  async function browse() {
    const picked = await owner.pick?.();
    if (!picked) return;
    setSource('');
    setChosen(picked);
    setChosenPlan(null);
    clearPreview();
  }

  function clearPreview() {
    setPreview(null);
    setSelected([]);
    setReview(null);
    setReceipt(null);
    setError('');
  }

  async function scan() {
    if (busy || pending || active.current) return;
    active.current = true;
    setBusy(true);
    clearPreview();
    try {
      const result = await owner.scan({
        provider,
        source,
        target,
        include_secrets: includeSecrets,
        ...(usingChosen
          ? chosen
            ? { source_grant: chosen }
            : { same_source_as: chosenPlan }
          : {}),
      });
      if (usingChosen) {
        setChosen(null);
        setChosenPlan(result.plan_id);
      }
      setPreview(result);
      setSelected(
        result.items
          .filter((item) => item.selected && item.status !== 'archive_only')
          .map((item) => item.id),
      );
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      active.current = false;
      setBusy(false);
    }
  }

  const selectable = (preview?.items ?? [])
    .filter((item) => canSelect(item.status, item.action))
    .map((item) => item.id);

  function choose(ids: string[]) {
    setSelected(ids);
    setReview(null);
  }

  function canSelect(status: string, action: string) {
    return (
      action !== 'manual_review' &&
      (status === 'planned' ||
        status === 'sensitive' ||
        (overwrite && status === 'conflict'))
    );
  }

  async function prepare() {
    if (!preview || !selected.length || busy || pending || active.current)
      return;
    active.current = true;
    setBusy(true);
    setError('');
    try {
      const result = await owner.review({
        plan_id: preview.plan_id,
        revision: preview.revision,
        selected_ids: selected,
        overwrite,
      });
      setReview(result);
    } catch (cause) {
      setError(clientError(cause).message);
      setReview(null);
    } finally {
      active.current = false;
      setBusy(false);
    }
  }

  async function apply() {
    if (!review || !preview || busy || pending || active.current) return;
    const command: MigrationApplyCommand = {
      plan_id: review.plan_id,
      revision: review.revision,
      selected_ids: selected,
      overwrite: review.overwrite,
      review_digest: review.review_digest,
      command_id: crypto.randomUUID(),
      confirmed: true,
    };
    active.current = true;
    setBusy(true);
    setError('');
    setReview(null);
    sessionStorage.setItem(pendingKey, JSON.stringify(command));
    setPending(command);
    try {
      const result = await owner.apply(command);
      if (result.command_id !== command.command_id)
        throw new Error('Migration receipt did not match this action');
      sessionStorage.removeItem(pendingKey);
      setPending(null);
      setReceipt(result);
    } catch (cause) {
      const issue = clientError(cause);
      if (
        [
          'migration_changed',
          'migration_plan_missing',
          'invalid_migration_selection',
          'migration_confirmation_required',
          'migration_command_conflict',
        ].includes(issue.code)
      ) {
        sessionStorage.removeItem(pendingKey);
        setPending(null);
      }
      setError(
        `${issue.message} Check the original migration before starting another.`,
      );
    } finally {
      active.current = false;
      setBusy(false);
    }
  }

  async function checkPending() {
    if (!pending || busy) return;
    setBusy(true);
    try {
      const result = await owner.receipt(pending.command_id);
      setReceipt(result);
      sessionStorage.removeItem(pendingKey);
      setPending(null);
      setMissingReceipt(false);
      setError('');
    } catch (cause) {
      const issue = clientError(cause);
      setMissingReceipt(issue.code === 'migration_receipt_missing');
      setError(issue.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack" aria-label="Migration controls">
      <p>
        Scan without changes, then select what to import. Source files stay in
        place.
      </p>
      <div className="field-row">
        <label>
          Source app
          <Select
            value={provider}
            onChange={(event) => {
              setProvider(event.target.value as 'hermes' | 'openclaw');
              forgetChosen();
              clearPreview();
            }}
          >
            <option value="hermes">Hermes Agent</option>
            <option value="openclaw">OpenClaw</option>
          </Select>
        </label>
        <label>
          Source folder
          <Input
            value={source}
            onChange={(event) => {
              setSource(event.target.value);
              forgetChosen();
              clearPreview();
            }}
            placeholder={
              usingChosen
                ? 'The folder you chose'
                : usual?.found
                  ? `${usual.place} (found)`
                  : 'Absolute path to the old app folder'
            }
          />
        </label>
        {canBrowse && owner.pick && !usual?.found && (
          <Button disabled={busy || !!pending} onClick={() => void browse()}>
            <FolderOpen size={16} aria-hidden /> Browse…
          </Button>
        )}
        <label>
          Target folder
          <Input
            value={target}
            onChange={(event) => {
              setTarget(event.target.value);
              clearPreview();
            }}
            placeholder="Current Row-Bot data folder"
          />
        </label>
      </div>
      {usingChosen ? (
        <p className="muted" role="status">
          Using the folder you chose.
        </p>
      ) : (
        usual && (
          <p className="muted" role="status">
            {usual.found
              ? `Found ${usual.label} in ${usual.place}. Leave Source folder empty to use it.`
              : canBrowse && owner.pick
                ? `${usual.label} isn't in its usual folder. Browse for it, or enter the folder it uses.`
                : `${usual.label} isn't in its usual folder. Enter the folder it uses.`}
          </p>
        )
      )}
      <div className="check-field">
        <span>Include API keys and tokens</span>
        <Toggle
          label="Include API keys and tokens"
          checked={includeSecrets}
          onChange={(event) => {
            setIncludeSecrets(event.target.checked);
            clearPreview();
          }}
        />
      </div>
      <div className="actions">
        <Button
          disabled={
            (!source.trim() && !useUsual && !usingChosen) || busy || !!pending
          }
          onClick={() => void scan()}
        >
          <FolderSearch size={16} aria-hidden /> Scan folders
        </Button>
      </div>
      {error && <p role="alert">{error}</p>}
      {pending && (
        <div role="status" className="stack">
          <p>
            Migration may be running. Check the original command before starting
            another.
          </p>
          <div className="actions">
            <Button disabled={busy} onClick={() => void checkPending()}>
              Check original migration
            </Button>
            {missingReceipt && (
              <Button
                variant="ghost"
                onClick={() => {
                  sessionStorage.removeItem(pendingKey);
                  setPending(null);
                  setMissingReceipt(false);
                  setError('');
                }}
              >
                I inspected the target; scan again
              </Button>
            )}
          </div>
        </div>
      )}
      {preview && (
        <div className="stack surface" aria-label="Migration preview">
          <p role="status">
            {preview.summary.total} items found; {selected.length} selected.
            Scanning made no changes.
          </p>
          {preview.warnings.map((warning, index) => (
            <p key={index} className="muted">
              {warning}
            </p>
          ))}
          <div className="check-field">
            <span>Overwrite conflicting target files (with backup)</span>
            <Toggle
              label="Overwrite conflicting target files"
              checked={overwrite}
              onChange={(event) => {
                setOverwrite(event.target.checked);
                setReview(null);
              }}
            />
          </div>
          <div className="actions">
            <Button
              variant="ghost"
              disabled={
                busy ||
                !selectable.length ||
                selectable.every((id) => selected.includes(id))
              }
              onClick={() => choose(selectable)}
            >
              Select all
            </Button>
            <Button
              variant="ghost"
              disabled={busy || !selected.length}
              onClick={() => choose([])}
            >
              Clear all
            </Button>
          </div>
          <div className="stack migration-item-list">
            {preview.items.map((item) => (
              <label key={item.id} className="migration-item">
                <input
                  type="checkbox"
                  checked={selected.includes(item.id)}
                  disabled={!canSelect(item.status, item.action) || busy}
                  onChange={(event) => {
                    setSelected((current) =>
                      event.target.checked
                        ? [...current, item.id]
                        : current.filter((id) => id !== item.id),
                    );
                    setReview(null);
                  }}
                />
                <span>
                  <strong>{item.label}</strong>
                  <small>
                    {humanizeToken(item.category)} ·{' '}
                    {humanizeToken(item.status).toLowerCase()}
                    {item.target ? ` · ${item.target}` : ''}
                    {item.reason ? ` · ${item.reason}` : ''}
                  </small>
                </span>
              </label>
            ))}
          </div>
          <div className="actions">
            <Button
              disabled={!selected.length || busy || !!pending}
              onClick={() => void prepare()}
            >
              <ArrowRight size={16} aria-hidden /> Apply selected items
            </Button>
          </div>
        </div>
      )}
      {review && (
        <div
          role="alertdialog"
          aria-label="Confirm migration"
          className="surface stack"
        >
          <h4>Confirm migration</h4>
          <p>
            {review.selected} selected items will be imported.{' '}
            {review.conflicts} conflicting files will be replaced.{' '}
            {review.sensitive} sensitive items are included. Backups and a
            report will be written.
          </p>
          <div className="actions">
            <Button disabled={busy} onClick={() => void apply()}>
              Confirm import
            </Button>
            <Button variant="ghost" onClick={() => setReview(null)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
      {receipt && (
        <div role="status" className="surface stack">
          <h4>
            {receipt.status === 'completed'
              ? 'Migration complete'
              : 'Migration needs attention'}
          </h4>
          {receipt.status === 'interrupted' ? (
            <p>
              The migration was interrupted. Inspect the target folder and
              backups before scanning again.
            </p>
          ) : (
            <p>
              {receipt.summary.migrated} imported; {receipt.summary.errors}{' '}
              errors. Report: {receipt.report || 'unavailable'}.
            </p>
          )}
          {receipt.failed_items.map((item) => (
            <p key={item.id}>
              {humanizeToken(item.id)}: {item.reason}
            </p>
          ))}
          <CompactAction label="Scan again" onClick={() => void scan()}>
            <RotateCcw size={16} aria-hidden />
          </CompactAction>
        </div>
      )}
    </div>
  );
}

export default function ConnectedMigrationControls() {
  const { controller, platform } = useRuntime();
  const [desktop, setDesktop] = useState(false);
  useEffect(() => {
    let live = true;
    void platform
      .discover()
      .then((value) => {
        if (live)
          setDesktop(value.status === 'ok' && value.value.kind === 'pywebview');
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [platform]);
  const owner = useRef<Owner>({
    scan: (body) => controller.scanMigration(body),
    review: (body) => controller.reviewMigration(body),
    apply: (body) => controller.applyMigration(body),
    receipt: (commandId) => controller.migrationReceipt(commandId),
    sources: () => controller.migrationSources() as Promise<MigrationSources>,
    pick: async () => {
      const picked = await platform.selectFolder(undefined, {
        intentId: crypto.randomUUID(),
        intent: 'migration_source',
        conversationId: null,
        destination: 'migration',
      });
      if (picked.status !== 'ok') return null;
      const value = picked.value as { kind?: string; reference?: string };
      return value.kind === 'folder' && value.reference
        ? value.reference
        : null;
    },
  });
  return <MigrationControls owner={owner.current} canBrowse={desktop} />;
}
