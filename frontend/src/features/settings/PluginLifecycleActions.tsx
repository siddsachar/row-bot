import { useState } from 'react';
import type {
  PluginLifecycleCommand,
  PluginLifecycleReceipt,
  PluginLifecycleReview,
} from '../../api/types';
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

/**
 * Install, update, uninstall and marketplace refresh for one plugin (or the
 * marketplace when `plugin` is omitted). Each action is reviewed by the
 * server and applied in one step; uninstall asks first. An unconfirmed
 * outcome is retained per plugin so it can be checked instead of repeated.
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
  const [confirmRemove, setConfirmRemove] = useState(false);
  const action = async (kind: PluginLifecycleCommand['action']) => {
    if (busy || pending) return;
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const review = await api.review(kind, plugin?.plugin_id ?? '');
      const commandId = crypto.randomUUID();
      remember(commandId);
      const receipt = await api.execute({
        command_id: commandId,
        action: kind,
        plugin_id: plugin?.plugin_id ?? '',
        revision: review.revision,
      });
      setMessage(receipt.message);
      if (receipt.status !== 'uncertain') {
        remember('');
        onChanged();
      }
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy(false);
    }
  };
  const recover = async () => {
    if (!pending || busy) return;
    setBusy(true);
    try {
      const receipt = await api.receipt(pending);
      setMessage(receipt.message);
      setError('');
      if (receipt.status !== 'uncertain') {
        remember('');
        onChanged();
      }
    } catch (cause) {
      setError(String(cause));
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
  const confirmation = plugin ? (
    <ModalTask
      open={confirmRemove}
      onOpenChange={setConfirmRemove}
      title={`Uninstall ${plugin.name}?`}
      description="This deletes its files, settings, and secret metadata."
      ariaLabel={`Uninstall ${plugin.name}`}
    >
      <div className="button-row">
        <Button onClick={() => setConfirmRemove(false)}>Cancel</Button>
        <Button
          variant="danger"
          onClick={() => {
            setConfirmRemove(false);
            void action('remove');
          }}
        >
          Uninstall plugin
        </Button>
      </div>
    </ModalTask>
  ) : null;
  return {
    busy,
    locked,
    action,
    requestRemove: () => setConfirmRemove(true),
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
