import { useEffect, useRef, useState } from 'react';
import type {
  EntitySummaryPage,
  KnowledgeMaintenanceReceipt,
  KnowledgeMaintenanceReview,
  KnowledgeSettingsSnapshot,
  WikiSettingsSnapshot,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { Button, Toggle } from '../../ui/primitives';
import { AppLink } from '../../ui/app-link';
import { humanizeToken } from '../../ui/format';
import { useNotify } from '../../ui/overlays';
import {
  DangerAction,
  SettingsDangerZone,
  SettingsGroup,
  SettingsItem,
  SettingsStatus,
} from './anatomy';
import WikiSettings, {
  type WikiSettingsSession,
} from '../knowledge/WikiSettings';
import type { SettingsMutationIO } from './SettingsSnapshotPanels';

export type KnowledgeMaintenanceIO = {
  review(
    action: 'knowledge.delete_all',
    catalogRevision: string,
    targets: { entity_id: string; revision: string }[],
    signal?: AbortSignal,
  ): Promise<KnowledgeMaintenanceReview>;
  execute(
    review: KnowledgeMaintenanceReview,
    commandId: string,
  ): Promise<KnowledgeMaintenanceReceipt>;
  receipt(
    commandId: string,
    signal?: AbortSignal,
  ): Promise<KnowledgeMaintenanceReceipt>;
};

/**
 * Settings › Memory: only settings (B264). Browsing, search, review, bulk
 * actions and the recall and change logs live in Home › Knowledge.
 */
export default function MemorySettings({
  snapshot,
  settingsMutation,
  wikiSession,
  wikiSnapshot,
  loadCatalog,
  maintenance,
  onMutation,
  refreshToken = 0,
}: {
  snapshot?: KnowledgeSettingsSnapshot;
  settingsMutation?: SettingsMutationIO | null;
  wikiSession?: WikiSettingsSession;
  wikiSnapshot?: WikiSettingsSnapshot;
  /** The saved catalog, whose revision "Delete all" is reviewed against. */
  loadCatalog?: (signal?: AbortSignal) => Promise<EntitySummaryPage>;
  maintenance?: KnowledgeMaintenanceIO;
  onMutation?: () => void;
  refreshToken?: number;
}) {
  const notify = useNotify();
  const [catalog, setCatalog] = useState<EntitySummaryPage | null>(null);
  const [error, setError] = useState('');
  const [memoryCommand, setMemoryCommand] = useState<{
    commandId: string;
    request: Parameters<SettingsMutationIO['execute']>[0];
    review: Parameters<SettingsMutationIO['execute']>[1];
  } | null>(null);
  const [memoryPending, setMemoryPending] = useState(false);
  const memoryBusy = useRef(false);
  const [review, setReview] = useState<KnowledgeMaintenanceReview | null>(null);
  const [deletePending, setDeletePending] = useState<string | null>(null);
  const [deleteResult, setDeleteResult] =
    useState<KnowledgeMaintenanceReceipt | null>(null);
  const [dangerOpen, setDangerOpen] = useState(false);

  useEffect(() => {
    if (!loadCatalog) return;
    const abort = new AbortController();
    setCatalog(null);
    loadCatalog(abort.signal).then(
      (value) => {
        if (!abort.signal.aborted) setCatalog(value);
      },
      (cause: unknown) => {
        if (!abort.signal.aborted) setError(clientError(cause).message);
      },
    );
    return () => abort.abort();
  }, [loadCatalog, refreshToken]);

  async function reviewMemory(enabled: boolean) {
    if (
      !settingsMutation ||
      !snapshot ||
      memoryPending ||
      memoryCommand ||
      memoryBusy.current
    )
      return;
    memoryBusy.current = true;
    const request = {
      settings_revision: settingsMutation.revision,
      page: 'knowledge' as const,
      field: 'memory_enabled',
      value: enabled,
    };
    let admitted = false;
    setMemoryPending(true);
    setError('');
    try {
      const reviewed = await settingsMutation.review(request);
      const captured = {
        commandId: crypto.randomUUID(),
        request,
        review: reviewed,
      };
      setMemoryCommand(captured);
      admitted = true;
      const receipt = await settingsMutation.execute(
        captured.request,
        captured.review,
        captured.commandId,
      );
      if (receipt.status === 'completed' && receipt.snapshot) {
        settingsMutation.onSnapshot(receipt.snapshot);
        setMemoryCommand(null);
        notify(`Memory turned ${enabled ? 'on' : 'off'}`);
      } else
        setError(
          "Row-Bot couldn't confirm the memory setting. Check again before retrying.",
        );
    } catch (cause) {
      setError(
        admitted
          ? "Row-Bot couldn't confirm the memory setting. Check again before retrying."
          : clientError(cause).message,
      );
    } finally {
      memoryBusy.current = false;
      setMemoryPending(false);
    }
  }

  async function checkMemoryReceipt() {
    if (!settingsMutation || !memoryCommand || memoryPending) return;
    setMemoryPending(true);
    try {
      const receipt = await settingsMutation.receipt(memoryCommand.commandId);
      if (receipt.status === 'completed' && receipt.snapshot) {
        settingsMutation.onSnapshot(receipt.snapshot);
        setMemoryCommand(null);
        setError('');
      } else
        setError(
          'The memory setting outcome is still uncertain. No action was repeated.',
        );
    } catch {
      setError(
        'The memory setting outcome is still uncertain. No action was repeated.',
      );
    } finally {
      setMemoryPending(false);
    }
  }

  async function beginDeleteAll() {
    if (!maintenance || !catalog || deletePending) return;
    setError('');
    try {
      setReview(
        await maintenance.review('knowledge.delete_all', catalog.revision, []),
      );
      setDeleteResult(null);
      setDangerOpen(true);
    } catch (cause) {
      setError(clientError(cause).message);
    }
  }

  function finish(result: KnowledgeMaintenanceReceipt) {
    setDeleteResult(result);
    setDeletePending(null);
    if (result.status !== 'completed') return;
    setReview(null);
    onMutation?.();
  }

  async function applyDeleteAll() {
    if (!maintenance || !review || deletePending) return;
    const commandId = crypto.randomUUID();
    setDeletePending(commandId);
    try {
      finish(await maintenance.execute(review, commandId));
    } catch {
      setError(
        "Row-Bot couldn't confirm the deletion. Check again before retrying.",
      );
    }
  }

  async function checkDeleteReceipt() {
    if (!maintenance || !deletePending) return;
    try {
      finish(await maintenance.receipt(deletePending));
    } catch {
      setError(
        'The deletion outcome is still unconfirmed. No action was repeated.',
      );
    }
  }

  const memoryOn = snapshot?.memory_enabled === true;
  const canDeleteAll = Boolean(
    maintenance &&
    catalog?.availability === 'available' &&
    catalog.total != null &&
    snapshot?.entities,
  );
  return (
    <div className="stack settings-snapshot-page">
      {snapshot && (
        <SettingsStatus
          tone={memoryOn ? 'success' : 'neutral'}
          more={
            snapshot.availability === 'available'
              ? [`${snapshot.entities.toLocaleString()} memories`]
              : []
          }
        >
          {!snapshot.memory_available
            ? 'Memory unavailable'
            : memoryOn
              ? 'Memory on'
              : 'Memory off'}
        </SettingsStatus>
      )}
      {snapshot && (
        <SettingsGroup title="Remembering">
          <SettingsItem
            label="Memory"
            help="Row-Bot remembers what it learns and recalls it when it helps."
            layout="inline"
            control={
              <Toggle
                label="Enable Memory"
                checked={memoryOn}
                disabled={
                  !settingsMutation ||
                  !snapshot.memory_available ||
                  memoryPending ||
                  Boolean(memoryCommand)
                }
                onChange={(event) => void reviewMemory(event.target.checked)}
              />
            }
            trailing={
              memoryCommand && (
                <Button
                  disabled={memoryPending}
                  onClick={() => void checkMemoryReceipt()}
                >
                  Check again
                </Button>
              )
            }
          />
          <SettingsItem
            label="Knowledge"
            help="Browse, search and edit memories, review the ones Row-Bot was unsure about, and see what recall used."
            bind={false}
            control={
              <AppLink className="button secondary" to="/?tab=knowledge">
                Open Knowledge
              </AppLink>
            }
          />
        </SettingsGroup>
      )}
      {snapshot && <GraphHealth snapshot={snapshot} />}
      {wikiSession && (
        <WikiSettings compact session={wikiSession} snapshot={wikiSnapshot} />
      )}
      {error && <p role="alert">{error}</p>}
      {snapshot && (
        <SettingsDangerZone
          meta="Permanent actions on the whole knowledge store"
          open={dangerOpen}
          onOpenChange={setDangerOpen}
        >
          <DangerAction
            title="Delete all knowledge"
            description="Permanently removes the current saved graph after an exact revision review. Row-Bot-managed Wiki files and local indexes are cleaned up; files outside Row-Bot's managed Wiki scope are preserved."
          >
            <Button
              variant="danger"
              disabled={
                !canDeleteAll || Boolean(deletePending) || Boolean(review)
              }
              onClick={() => void beginDeleteAll()}
            >
              Delete all knowledge ({snapshot.entities.toLocaleString()})
            </Button>
          </DangerAction>
          {review && (
            <section
              className="settings-reviewed-action stack"
              aria-label="Reviewed knowledge deletion"
            >
              <p>
                {review.entity_count.toLocaleString()}{' '}
                {review.entity_count === 1 ? 'entry' : 'entries'} will be
                removed. Related graph links, local search indexes, and
                Row-Bot-managed Wiki files will be cleaned up; external files
                are preserved.
              </p>
              <div className="actions">
                <Button
                  disabled={Boolean(deletePending)}
                  onClick={() => setReview(null)}
                >
                  Cancel
                </Button>
                <Button
                  variant="danger"
                  disabled={Boolean(deletePending)}
                  onClick={() => void applyDeleteAll()}
                >
                  Confirm permanent deletion
                </Button>
              </div>
            </section>
          )}
          {deleteResult && deleteResult.status !== 'completed' && (
            <p role="alert">
              Deletion was {humanizeToken(deleteResult.status).toLowerCase()}.
              Deleted {deleteResult.deleted.length}; stale{' '}
              {deleteResult.stale.length}; missing {deleteResult.missing.length}
              .
            </p>
          )}
          {deletePending && (
            <Button onClick={() => void checkDeleteReceipt()}>
              Check deletion
            </Button>
          )}
        </SettingsDangerZone>
      )}
    </div>
  );
}

/** How many memories there are and how well they connect. */
function GraphHealth({ snapshot }: { snapshot: KnowledgeSettingsSnapshot }) {
  return (
    <SettingsGroup title="Graph health" anchor="memory-graph">
      {snapshot.availability === 'available' ? (
        <>
          <SettingsItem
            label="Memories"
            help={
              snapshot.entity_types
                .map(
                  (item) =>
                    `${humanizeToken(item.kind)} ${item.count.toLocaleString()}`,
                )
                .join(' · ') || undefined
            }
            bind={false}
            control={
              <span className="settings-row-value">
                {snapshot.entities.toLocaleString()} memories ·{' '}
                {snapshot.relations.toLocaleString()} links
              </span>
            }
          />
          <SettingsItem
            label="Connected groups"
            help={`The largest holds ${snapshot.largest_component.toLocaleString()} memories.`}
            bind={false}
            control={
              <span className="settings-row-value">
                {snapshot.connected_components.toLocaleString()}
              </span>
            }
          />
          <SettingsItem
            label="Unconnected memories"
            help="Memories with no links to others yet."
            bind={false}
            control={
              <span className="settings-row-value">
                {snapshot.isolated_entities.toLocaleString()}
              </span>
            }
          />
        </>
      ) : (
        <SettingsItem
          label="Memory graph"
          help={
            snapshot.availability === 'missing'
              ? 'No saved memory graph has been created yet.'
              : 'Saved memory graph statistics are unavailable.'
          }
        />
      )}
    </SettingsGroup>
  );
}
