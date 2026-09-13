import { useEffect, useState, useSyncExternalStore } from 'react';
import { Button } from '../../ui/primitives';
import ProviderSettingsEditor, {
  type ProviderSettingsEditorProps,
} from './ProviderSettingsEditor';
import type { createProviderSettingsSessions } from './provider-settings-sessions';

type Owner = ReturnType<typeof createProviderSettingsSessions>;
export default function ProviderSettingsPanel({
  owner,
  providerId,
  onSaved,
  onCancel,
}: {
  owner: Owner;
  providerId: string;
  onSaved: ProviderSettingsEditorProps['onSaved'];
  onCancel: () => void;
}) {
  const state = useSyncExternalStore(owner.subscribe, owner.getSnapshot);
  const [entry, setEntry] = useState<ReturnType<Owner['open']>>(null);
  useEffect(() => {
    setEntry(owner.open(providerId));
  }, [owner, providerId]);
  if (
    !entry ||
    !entry.session.active ||
    entry.session.providerId !== providerId
  )
    return (
      <section className="stack" aria-label="Provider credential settings">
        <p role="status">
          {state.capacity
            ? 'Resolve a pending credential change or explicitly discard an unsent draft before opening another provider.'
            : 'Open these settings again after authentication is available.'}
        </p>
        {state.retained.map((id) => (
          <Button
            key={id}
            disabled={!owner.canDiscard(id)}
            onClick={() => {
              if (owner.discard(id)) setEntry(owner.open(providerId));
            }}
          >
            Discard unsent {id} draft
          </Button>
        ))}
        <Button onClick={onCancel}>Close</Button>
      </section>
    );
  return (
    <ProviderSettingsEditor
      {...entry}
      providerId={providerId}
      onSaved={onSaved}
      onCancel={onCancel}
    />
  );
}
