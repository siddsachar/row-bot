import { useState } from 'react';
import type {
  PluginLifecycleCommand,
  PluginLifecycleReceipt,
  PluginLifecycleReview,
} from '../../api/types';
import { readRetainedCommand, retainCommand } from '../../api/retained-command';
import { Button } from '../../ui/primitives';
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

export default function PluginLifecycleActions({
  plugin,
  api,
  onChanged,
}: {
  plugin?: PluginCatalogItem;
  api: PluginLifecycleApi;
  onChanged: () => void;
}) {
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
  const action = async (kind: PluginLifecycleCommand['action']) => {
    if (busy || pending) return;
    if (
      kind === 'remove' &&
      !window.confirm(
        `Uninstall ${plugin?.name}? This deletes its files, settings, and secret metadata.`,
      )
    )
      return;
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

  if (!plugin)
    return (
      <div className="stack">
        <p>Fetch the configured plugin marketplace index over the network.</p>
        <Button
          disabled={busy || Boolean(pending)}
          onClick={() => void action('refresh')}
        >
          Refresh marketplace
        </Button>
        {message && <p role="status">{message}</p>}
        {error && <p role="alert">{error}</p>}
        {pending && (
          <Button disabled={busy} onClick={() => void recover()}>
            Check outcome
          </Button>
        )}
      </div>
    );

  return (
    <div className="stack">
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
      <div className="actions">
        {!plugin.installed && (
          <Button
            disabled={busy || Boolean(pending)}
            onClick={() => void action('install')}
          >
            Install
          </Button>
        )}
        {plugin.installed && plugin.update_version && (
          <Button
            disabled={busy || Boolean(pending)}
            onClick={() => void action('update')}
          >
            Update to {plugin.update_version}
          </Button>
        )}
        {plugin.installed && (
          <Button
            variant="danger"
            disabled={busy || Boolean(pending)}
            onClick={() => void action('remove')}
          >
            Uninstall
          </Button>
        )}
      </div>
      {message && <p role="status">{message}</p>}
      {error && <p role="alert">{error}</p>}
      {pending && (
        <Button disabled={busy} onClick={() => void recover()}>
          Check outcome
        </Button>
      )}
    </div>
  );
}
