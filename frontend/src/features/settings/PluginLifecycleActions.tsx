import { useState } from 'react';
import type {
  PluginLifecycleCommand,
  PluginLifecycleReceipt,
  PluginLifecycleReview,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { readRetainedCommand, retainCommand } from '../../api/retained-command';
import { Button } from '../../ui/primitives';
import { ModalTask } from '../../ui/overlays';
import type { PluginCatalogItem } from './PluginSettings';

export type PluginLifecycleApi = {
  review: (
    action: PluginLifecycleCommand['action'],
    pluginId: string,
  ) => Promise<PluginLifecycleReview>;
  execute: (
    command: Omit<PluginLifecycleCommand, 'client_session_id'>,
  ) => Promise<PluginLifecycleReceipt>;
  receipt: (commandId: string) => Promise<PluginLifecycleReceipt>;
};

type Confirmable = Exclude<PluginLifecycleCommand['action'], 'refresh'>;

const CONFIRM: Record<Confirmable, { title: string; label: string }> = {
  install: { title: 'Install', label: 'Install plugin' },
  update: { title: 'Update', label: 'Update plugin' },
  prepare: { title: 'Prepare', label: 'Prepare plugin' },
  remove: { title: 'Remove', label: 'Remove package' },
  restore: { title: 'Restore previous version', label: 'Restore' },
  recover: { title: 'Recover publication', label: 'Recover' },
  purge: { title: 'Delete saved data', label: 'Delete saved data' },
};

/**
 * Install, update, prepare, uninstall and marketplace refresh for one plugin
 * (or the marketplace when `plugin` is omitted). The server reviews each
 * action first; install, update, prepare and uninstall show what the review
 * says (source, checksum, what it may do) and run only after the person
 * confirms it (B144). An unconfirmed outcome is retained per plugin so it
 * can be checked instead of repeated.
 */
export function usePluginLifecycle(
  plugin: PluginCatalogItem | undefined,
  api: PluginLifecycleApi,
  onChanged: () => void,
) {
  const [busy, setBusy] = useState(false);
  const commandScope = `plugin-lifecycle:${plugin?.plugin_id ?? 'marketplace'}`;
  const [pending, setPending] = useState(() =>
    readRetainedCommand(commandScope),
  );
  const remember = (value: string) => {
    setPending(value);
    retainCommand(commandScope, value);
  };
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [reviewed, setReviewed] = useState<{
    kind: Confirmable;
    review: PluginLifecycleReview;
  } | null>(null);
  // A failed install or update says why (the installer's reason) as an
  // alert, not as a quiet status line (B266).
  const show = (receipt: PluginLifecycleReceipt) => {
    const failed = receipt.status === 'failed';
    setMessage(failed ? '' : receipt.message);
    setError(failed ? receipt.message : '');
  };
  const run = async (
    kind: PluginLifecycleCommand['action'],
    review: PluginLifecycleReview,
  ) => {
    const commandId = crypto.randomUUID();
    remember(commandId);
    const receipt = await api.execute({
      command_id: commandId,
      action: kind,
      plugin_id: plugin?.plugin_id ?? '',
      revision: review.revision,
    });
    show(receipt);
    if (receipt.status !== 'uncertain') {
      remember('');
      onChanged();
    }
  };
  const action = async (kind: PluginLifecycleCommand['action']) => {
    if (busy || pending) return;
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const review = await api.review(kind, plugin?.plugin_id ?? '');
      // The marketplace refresh says what it fetches on its own button.
      if (kind === 'refresh') await run(kind, review);
      else setReviewed({ kind, review });
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  };
  const confirm = async () => {
    const current = reviewed;
    if (!current || busy || pending) return;
    setReviewed(null);
    setBusy(true);
    try {
      await run(current.kind, current.review);
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  };
  const recover = async () => {
    if (!pending || busy) return;
    setBusy(true);
    try {
      const receipt = await api.receipt(pending);
      show(receipt);
      if (receipt.status !== 'uncertain') {
        remember('');
        onChanged();
      }
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  };
  const locked = busy || Boolean(pending);
  const feedback = (
    <>
      {message && <p role="status">{message}</p>}
      {error && <p role="alert">{error}</p>}
      {pending && (
        <Button disabled={busy} onClick={() => void recover()}>
          Check outcome
        </Button>
      )}
    </>
  );
  const words = reviewed ? CONFIRM[reviewed.kind] : null;
  const confirmation =
    plugin && reviewed && words ? (
      <ModalTask
        open
        onOpenChange={(open) => {
          if (!open) setReviewed(null);
        }}
        title={`${words.title} ${plugin.name}?`}
        description={
          reviewed.kind === 'remove'
            ? 'This removes package files and withdraws its children. Saved data and credentials are preserved.'
            : 'Check what it is and what it may do before it runs.'
        }
        ariaLabel={`${words.title} ${plugin.name}`}
      >
        <div className="stack settings-plugin-review">
          {(reviewed.kind === 'install' || reviewed.kind === 'update') && (
            <dl className="settings-facts">
              <div className="settings-fact">
                <dt>Version</dt>
                <dd>{reviewed.review.version || plugin.version}</dd>
              </div>
              <div className="settings-fact">
                <dt>From</dt>
                <dd className="settings-break-word">
                  {reviewed.review.source}
                </dd>
              </div>
              <div className="settings-fact">
                <dt>Checksum</dt>
                <dd className="settings-break-word">
                  {reviewed.review.checksum || 'Not pinned'}
                </dd>
              </div>
              <div className="settings-fact">
                <dt>May use</dt>
                <dd>
                  {(reviewed.review.permissions ?? []).join(', ') ||
                    'Nothing listed'}
                </dd>
              </div>
            </dl>
          )}
          {(reviewed.review.disclosures ?? []).length > 0 && (
            <ul aria-label="What this does">
              {(reviewed.review.disclosures ?? []).map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          )}
        </div>
        <div className="button-row">
          <Button onClick={() => setReviewed(null)}>Cancel</Button>
          <Button
            variant={reviewed.kind === 'remove' ? 'danger' : 'primary'}
            onClick={() => void confirm()}
          >
            {words.label}
          </Button>
        </div>
      </ModalTask>
    ) : null;
  return {
    busy,
    locked,
    action,
    requestRemove: () => void action('remove'),
    feedback,
    confirmation,
  };
}

/** Where a plugin comes from and what it may do, before anything runs. */
export function PluginProvenance({ plugin }: { plugin: PluginCatalogItem }) {
  return (
    <div className="stack settings-plugin-provenance">
      <small>
        Third-party plugin code may contact external services when enabled.
        Check its permissions and source before installing. Installation keeps
        it disabled until setup and testing.
      </small>
      <small>
        Source: {plugin.source_label || 'configured marketplace repository'}.{' '}
        Publisher:{' '}
        {plugin.verified
          ? 'verified in the saved index'
          : 'unverified in the saved index'}
        . Checksum: {plugin.checksum || 'not supplied'}. Permissions:{' '}
        {plugin.permissions.join(', ') || 'none listed'}.
      </small>
    </div>
  );
}

export default function PluginLifecycleActions({
  plugin,
  api,
  onChanged,
}: {
  plugin?: PluginCatalogItem;
  api: PluginLifecycleApi;
  onChanged: () => void;
}) {
  const lifecycle = usePluginLifecycle(plugin, api, onChanged);
  if (!plugin)
    return (
      <div className="stack settings-plugin-marketplace-refresh">
        <p className="settings-help">
          Fetch the configured plugin marketplace index over the network.
        </p>
        <Button
          disabled={lifecycle.locked}
          onClick={() => void lifecycle.action('refresh')}
        >
          Refresh marketplace
        </Button>
        {lifecycle.feedback}
      </div>
    );

  return (
    <div className="stack">
      <PluginProvenance plugin={plugin} />
      <div className="actions">
        {!plugin.installed && (
          <Button
            disabled={lifecycle.locked}
            onClick={() => void lifecycle.action('install')}
          >
            Install
          </Button>
        )}
        {plugin.installed && plugin.update_version && (
          <Button
            disabled={lifecycle.locked}
            onClick={() => void lifecycle.action('update')}
          >
            Update to {plugin.update_version}
          </Button>
        )}
        {plugin.installed && plugin.capabilities.prepare?.available && (
          <Button
            disabled={lifecycle.locked}
            onClick={() => void lifecycle.action('prepare')}
          >
            Prepare
          </Button>
        )}
        {plugin.installed && (
          <Button
            variant="danger"
            disabled={lifecycle.locked}
            onClick={lifecycle.requestRemove}
          >
            Uninstall
          </Button>
        )}
      </div>
      {lifecycle.feedback}
      {lifecycle.confirmation}
    </div>
  );
}
