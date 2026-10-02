import { useEffect, useState } from 'react';
import {
  MoreHorizontal,
  RefreshCw,
  SearchCheck,
  Trash2,
  Upload,
} from 'lucide-react';
import type {
  SkillHubPreview,
  SkillHubInstalledPage,
  SkillHubInstalledRecord,
  SkillHubMaintenanceCommand,
  SkillHubMaintenanceReceipt,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { ModalTask } from '../../ui/overlays';
import {
  Button,
  CompactAction,
  ErrorState,
  Menu,
  Skeleton,
} from '../../ui/primitives';
import { absoluteTime, relativeTime } from '../../ui/format';

const sourceLabels: Record<string, string> = {
  github: 'GitHub',
  skills_sh: 'skills.sh',
  browse_sh: 'browse.sh',
  clawhub: 'ClawHub',
  lobehub: 'LobeHub',
};

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
  skillName,
  onChanged,
}: {
  io: PublicSkillMaintenanceIO;
  ownerKey: string;
  reload?: number;
  skillName?: string;
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
  const [updateReview, setUpdateReview] = useState<{
    record: SkillHubInstalledRecord;
    preview: SkillHubPreview;
  } | null>(null);

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
    action: 'check' | 'review_update' | 'update' | 'restore' | 'uninstall',
  ) {
    if (busy || pending) return;
    const id = crypto.randomUUID();
    const command: SkillHubMaintenanceCommand = {
      command_id: id,
      name: record.name,
      expected_revision: record.revision,
      action,
      confirmed: ['uninstall', 'update', 'restore'].includes(action),
      ...(action === 'update' && updateReview
        ? {
            preview_id: updateReview.preview.preview_id,
            content_hash: updateReview.preview.content_hash,
          }
        : {}),
    };
    setConfirm(null);
    setPending(id);
    retain(ownerKey, id);
    setBusy(true);
    setError('');
    try {
      const result = await io.action(command);
      accept(result);
      if (action === 'review_update' && result.update_preview)
        setUpdateReview({ record, preview: result.update_preview });
      else if (action === 'update') setUpdateReview(null);
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
        <CompactAction
          label="Refresh installed public skills"
          disabled={busy}
          onClick={() => setRevision((value) => value + 1)}
        >
          <RefreshCw size={16} aria-hidden="true" />
        </CompactAction>
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
      {!!page?.items.length && (
        <ul className="settings-row-list" aria-label="Installed public skills">
          {page.items
            .filter((record) => !skillName || record.name === skillName)
            .map((record) => (
              <li key={record.name}>
                <div className="settings-row-list-text">
                  <strong>{record.name}</strong>
                  <small>
                    {sourceLabels[record.source] ?? record.source} ·{' '}
                    {record.enabled ? 'Available' : 'Off'} ·{' '}
                    {record.file_count === 1
                      ? '1 file'
                      : `${record.file_count} files`}{' '}
                    · updated{' '}
                    <time
                      dateTime={record.updated_at}
                      title={absoluteTime(record.updated_at)}
                    >
                      {relativeTime(record.updated_at)}
                    </time>
                  </small>
                </div>
                <span className="settings-provider-actions">
                  <CompactAction
                    label={`Check update for ${record.name}`}
                    disabled={busy || Boolean(pending)}
                    onClick={() => void act(record, 'check')}
                  >
                    <SearchCheck size={16} aria-hidden="true" />
                  </CompactAction>
                  <Menu
                    label={`More actions for ${record.name}`}
                    iconOnly
                    variant="ghost"
                    className="icon-action icon-action-sm"
                    disabled={busy || Boolean(pending)}
                    actions={[
                      {
                        label: `Update ${record.name}`,
                        icon: <Upload size={16} />,
                        onSelect: () => void act(record, 'review_update'),
                      },
                      {
                        label: `Restore previous ${record.name}`,
                        onSelect: () => void act(record, 'restore'),
                      },
                      {
                        label: `Uninstall ${record.name}`,
                        icon: <Trash2 size={16} />,
                        danger: true,
                        onSelect: () => setConfirm(record),
                      },
                    ]}
                  >
                    <MoreHorizontal size={16} aria-hidden />
                  </Menu>
                </span>
              </li>
            ))}
        </ul>
      )}
      <ModalTask
        open={Boolean(updateReview)}
        onOpenChange={(open) => {
          if (!open) setUpdateReview(null);
        }}
        title="Review skill update"
        description="Replace only unchanged managed files; retain one previous version."
      >
        <p>{updateReview?.preview.skill_name}</p>
        <ul>
          {updateReview?.preview.files.map((file) => (
            <li key={file}>{file}</li>
          ))}
        </ul>
        {updateReview?.preview.scan.findings.map((finding, index) => (
          <p key={index}>{finding.message}</p>
        ))}
        <Button
          disabled={busy || updateReview?.preview.scan.blocked}
          onClick={() => {
            if (updateReview) void act(updateReview.record, 'update');
          }}
        >
          Update reviewed files
        </Button>
      </ModalTask>
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
