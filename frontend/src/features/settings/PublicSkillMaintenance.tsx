import { useEffect, useState } from 'react';
import { RefreshCw, SearchCheck, Trash2, Upload } from 'lucide-react';
import type {
  SkillHubInstalledPage,
  SkillHubInstalledRecord,
  SkillHubMaintenanceCommand,
  SkillHubMaintenanceReceipt,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { ModalTask } from '../../ui/overlays';
import { Button, ErrorState, Skeleton } from '../../ui/primitives';

export type PublicSkillMaintenanceIO = {
  installed: (signal?: AbortSignal) => Promise<SkillHubInstalledPage>;
  action: (
    command: SkillHubMaintenanceCommand,
    signal?: AbortSignal,
  ) => Promise<SkillHubMaintenanceReceipt>;
  receipt: (
    commandId: string,
    signal?: AbortSignal,
  ) => Promise<SkillHubMaintenanceReceipt>;
};

function key(ownerKey: string) {
  return `row-bot-skill-hub-maintenance:${ownerKey}`;
}
function retained(ownerKey: string) {
  try {
    return sessionStorage.getItem(key(ownerKey)) ?? '';
  } catch {
    return '';
  }
}
function retain(ownerKey: string, id: string) {
  try {
    if (id) sessionStorage.setItem(key(ownerKey), id);
    else sessionStorage.removeItem(key(ownerKey));
  } catch {
    /* storage may be unavailable */
  }
}

export default function PublicSkillMaintenance({
  io,
  ownerKey,
  reload = 0,
  onChanged,
}: {
  io: PublicSkillMaintenanceIO;
  ownerKey: string;
  reload?: number;
  onChanged?: () => void;
}) {
  const [page, setPage] = useState<SkillHubInstalledPage | null>(null);
  const [pending, setPending] = useState(() => retained(ownerKey));
  const [confirm, setConfirm] = useState<SkillHubInstalledRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [lostReceipt, setLostReceipt] = useState(false);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const abort = new AbortController();
    void io.installed(abort.signal).then(
      (next) => {
        if (!abort.signal.aborted) setPage(next);
      },
      (cause) => {
        if (!abort.signal.aborted) setError(clientError(cause).message);
      },
    );
    return () => abort.abort();
  }, [io, reload, revision]);

  function accept(result: SkillHubMaintenanceReceipt) {
    setPending('');
    retain(ownerKey, '');
    setLostReceipt(false);
    setNotice(result.message);
    setRevision((value) => value + 1);
    if (result.success && result.action !== 'check') onChanged?.();
  }

  async function act(
    record: SkillHubInstalledRecord,
    action: 'check' | 'update' | 'uninstall',
  ) {
    if (busy || pending) return;
    const id = crypto.randomUUID();
    const command: SkillHubMaintenanceCommand = {
      command_id: id,
      name: record.name,
      expected_revision: record.revision,
      action,
      confirmed: action === 'uninstall',
    };
    setConfirm(null);
    setPending(id);
    retain(ownerKey, id);
    setBusy(true);
    setError('');
    try {
      accept(await io.action(command));
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  async function recover() {
    if (busy || !pending) return;
    setBusy(true);
    setError('');
    try {
      accept(await io.receipt(pending));
    } catch (cause) {
      const failure = clientError(cause);
      setError(failure.message);
      setLostReceipt(failure.code === 'skill_receipt_missing');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      className="settings-snapshot-section stack"
      aria-labelledby="public-installed-title"
    >
      <header className="settings-snapshot-heading">
        <div>
          <h3 id="public-installed-title">Installed public skills</h3>
          <p>
            Source checks are explicit. Updates preserve a backup; uninstall
            removes the local skill.
          </p>
        </div>
        <Button
          iconOnly
          aria-label="Refresh installed public skills"
          title="Refresh installed public skills"
          disabled={busy}
          onClick={() => setRevision((value) => value + 1)}
        >
          <RefreshCw size={18} aria-hidden="true" />
        </Button>
      </header>
      {error && (
        <ErrorState title="Public skill maintenance needs attention">
          {error}
        </ErrorState>
      )}
      {page === null && !error && (
        <Skeleton label="Loading installed public skills" />
      )}
      {page?.items.length === 0 && <p>No public skills installed.</p>}
      {notice && <p role="status">{notice}</p>}
      {pending && (
        <div className="card stack">
          <p>
            Check the original action result before repeating a source check,
            update, or uninstall.
          </p>
          <Button disabled={busy} onClick={() => void recover()}>
            Check original skill action
          </Button>
          {lostReceipt && (
            <Button
              variant="ghost"
              disabled={busy}
              onClick={() => {
                retain(ownerKey, '');
                setPending('');
                setLostReceipt(false);
                setRevision((value) => value + 1);
              }}
            >
              Clear lost record after inspecting Skill Library
            </Button>
          )}
        </div>
      )}
      {page?.items.map((record) => (
        <article className="card stack" key={record.name}>
          <strong>{record.name}</strong>
          <p className="muted">
            {record.source} · {record.enabled ? 'Available' : 'Off'} ·{' '}
            {record.file_count} files · updated {record.updated_at}
          </p>
          <div className="button-row">
            <Button
              iconOnly
              aria-label={`Check update for ${record.name}`}
              title="Check update"
              disabled={busy || Boolean(pending)}
              onClick={() => void act(record, 'check')}
            >
              <SearchCheck size={18} aria-hidden="true" />
            </Button>
            <Button
              iconOnly
              aria-label={`Update ${record.name}`}
              title="Update"
              disabled={busy || Boolean(pending)}
              onClick={() => void act(record, 'update')}
            >
              <Upload size={18} aria-hidden="true" />
            </Button>
            <Button
              iconOnly
              variant="danger"
              aria-label={`Uninstall ${record.name}`}
              title="Uninstall"
              disabled={busy || Boolean(pending)}
              onClick={() => setConfirm(record)}
            >
              <Trash2 size={18} aria-hidden="true" />
            </Button>
          </div>
        </article>
      ))}
      <ModalTask
        open={confirm !== null}
        onOpenChange={(open) => {
          if (!open) setConfirm(null);
        }}
        title="Uninstall public skill"
        description="This removes its local files and provenance."
      >
        <p>{confirm?.name}</p>
        <div className="button-row">
          <Button onClick={() => setConfirm(null)}>Cancel</Button>
          <Button
            variant="danger"
            disabled={busy}
            onClick={() => {
              if (confirm) void act(confirm, 'uninstall');
            }}
          >
            Uninstall
          </Button>
        </div>
      </ModalTask>
    </section>
  );
}
