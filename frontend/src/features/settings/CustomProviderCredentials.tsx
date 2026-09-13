import type { ComponentProps } from 'react';
import ProviderSettingsPanel from './ProviderSettingsPanel';
import {
  createProviderSettingsSessions,
  providerSettingsCallbacks,
  type ProviderSettingsTransport,
} from './provider-settings-sessions';

/** The canonical custom provider ID is preserved exactly, never slugified. */
export function createCustomProviderCredentialSessions(
  controller: Parameters<typeof createProviderSettingsSessions>[0],
  transport: ProviderSettingsTransport,
  capacity = 4,
) {
  return createProviderSettingsSessions(
    controller,
    () => providerSettingsCallbacks(transport),
    {
      capacity,
      allowProviderId: (id) =>
        /^custom_openai_[a-z0-9][a-z0-9_-]{0,63}$/.test(id),
    },
  );
}

export default function CustomProviderCredentials(
  props: ComponentProps<typeof ProviderSettingsPanel>,
) {
  return (
    <section className="stack" aria-label="Custom endpoint credential">
      <p>
        This credential belongs to the saved endpoint. Replacement verifies
        local secure storage without contacting the endpoint. After an endpoint
        is removed and created again, explicitly restore its retained credential
        or save a replacement.
      </p>
      <ProviderSettingsPanel {...props} />
    </section>
  );
}
