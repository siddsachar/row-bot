import type { ClientController } from '../../api/controller';
import { ProviderConfigurationSession } from './ProviderConfiguration';
import { createAuthenticatedEditorOwner } from './authenticated-editor-owner';

/** One private global editor per authenticated client lifetime. */
export function createProviderConfigurationOwner(
  controller: Pick<ClientController, 'getSnapshot' | 'subscribe'>,
) {
  return createAuthenticatedEditorOwner(
    controller,
    () => new ProviderConfigurationSession(),
  );
}
