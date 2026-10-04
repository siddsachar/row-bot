import { useEffect, useState } from 'react';
import { useRuntime } from '../../runtime';
import type {
  IntegrationItem,
  SkillHubInstalledRecord,
  SkillHubMaintenanceCommand,
  SkillHubPreview,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { Button } from '../../ui/primitives';
import { ModalTask } from '../../ui/overlays';
import { useSetupOperations } from './setup-operations';
import { SkillReview } from './OwnerSetup';

export default function SkillMaintenance({
  item,
  onChanged,
}: {
  item: IntegrationItem;
  onChanged: (removed?: boolean) => Promise<void>;
}) {
  const { controller } = useRuntime();
  const operations = useSetupOperations(item);
  const scope = `integration-skill:${item.id}:maintenance`;
  const [pending, setPending] = useState(() => operations.read(scope));
  const [record, setRecord] = useState<SkillHubInstalledRecord | null>(null);
  const [preview, setPreview] = useState<SkillHubPreview | null>(null);
  const [confirm, setConfirm] = useState<'uninstall' | 'restore' | null>(null);
  const [message, setMessage] = useState('');
  const refresh = async () => {
    const page = await controller.skillHubInstalled();
    setRecord(page.items.find((row) => row.name === item.owner_ref) ?? null);
  };
  useEffect(() => {
    const abort = new AbortController();
    void controller
      .skillHubInstalled(abort.signal)
      .then((page) => {
        if (!abort.signal.aborted)
          setRecord(
            page.items.find((row) => row.name === item.owner_ref) ?? null,
          );
      })
      .catch((error) => {
        if (!abort.signal.aborted) setMessage(clientError(error).message);
      });
    return () => abort.abort();
  }, [controller, item.owner_ref]);
  const run = async (action: () => Promise<void>, recovery = false) => {
    try {
      await operations.run(action, recovery);
    } catch (error) {
      setMessage(clientError(error).message);
    }
  };
  const settle = async (id: string) => {
    await controller.reconcileIntegrationOperation('skill', id);
    const receipt = await controller.skillHubMaintenanceReceipt(id);
    operations.retain(scope, '');
    setPending('');
    setMessage(receipt.message);
    await refresh();
    await onChanged(receipt.success && receipt.action === 'uninstall');
  };
  const act = async (action: SkillHubMaintenanceCommand['action']) => {
    if (!record) return;
    const id = crypto.randomUUID();
    operations.retain(scope, id);
    setPending(id);
    setConfirm(null);
    const result = await controller.skillHubMaintenance({
      command_id: id,
      name: record.name,
      expected_revision: record.revision,
      action,
      confirmed: ['update', 'restore', 'uninstall'].includes(action),
      ...(action === 'update' && preview
        ? { preview_id: preview.preview_id, content_hash: preview.content_hash }
        : {}),
    });
    if (result.success) {
      operations.retain(scope, '');
      setPending('');
      setMessage(result.message);
      setPreview(result.update_preview ?? null);
      await refresh();
      if (action !== 'check' && action !== 'review_update')
        await onChanged(action === 'uninstall');
    } else {
      setMessage(
        result.message + ' Check the original action before another change.',
      );
    }
  };
  return (
    <section className="stack" aria-label="Skill maintenance">
      {message && <p role="status">{message}</p>}
      {pending && (
        <Button
          disabled={operations.busy}
          onClick={() => void run(() => settle(pending), true)}
        >
          Check original skill action
        </Button>
      )}
      {record && (
        <>
          <p>
            {record.file_count} owned files. Updates and removal retain one
            previous managed revision; unrelated files and service credentials
            are unchanged.
          </p>
          <Button
            disabled={operations.blocked}
            onClick={() => void run(() => act('review_update'))}
          >
            Check for skill updates
          </Button>
          <Button
            disabled={operations.blocked}
            onClick={() => setConfirm('restore')}
          >
            Restore previous skill version
          </Button>
          <Button
            disabled={operations.blocked}
            variant="danger"
            onClick={() => setConfirm('uninstall')}
          >
            Remove skill
          </Button>
        </>
      )}
      <ModalTask
        open={Boolean(preview)}
        onOpenChange={(open) => {
          if (!open) setPreview(null);
        }}
        title="Review skill update"
        description="Review every changed file and instruction before replacing the managed revision. Rejecting this review leaves the current version unchanged."
      >
        {preview && (
          <>
            <ul>
              {preview.changes?.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
            <SkillReview preview={preview} />
          </>
        )}
        <Button onClick={() => setPreview(null)}>Keep current version</Button>
        <Button
          disabled={operations.blocked || preview?.scan.blocked}
          onClick={() => void run(() => act('update'))}
        >
          Update reviewed files
        </Button>
      </ModalTask>
      <ModalTask
        open={Boolean(confirm)}
        onOpenChange={(open) => {
          if (!open) setConfirm(null);
        }}
        title={
          confirm === 'restore'
            ? 'Restore previous skill version'
            : 'Remove skill'
        }
        description={`${item.name}: only this skill's managed files change. A previous revision is retained; other skills, app data and account credentials remain. Unresolved cleanup stays visible.`}
      >
        <Button onClick={() => setConfirm(null)}>Cancel</Button>
        <Button
          disabled={operations.blocked}
          onClick={() => {
            if (confirm) void run(() => act(confirm));
          }}
        >
          Confirm skill change
        </Button>
      </ModalTask>
    </section>
  );
}
