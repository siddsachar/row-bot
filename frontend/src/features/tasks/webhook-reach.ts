import type { ClientController } from '../../api/controller';
import { clientError } from '../../api/errors';
import type {
  SettingsMutationRequest,
  SettingsSnapshot,
} from '../../api/types';
import type { WebhookReach } from './WebhookAddress';

const reachOf = (snapshot: SettingsSnapshot): WebhookReach => ({
  publicBase: snapshot.system.tunnel.main_app_url,
  canControl: snapshot.system.tunnel.local_owner_control_available,
});

/**
 * A saved webhook's address and reachability for the workflow page. The
 * address (with its secret) is read from the private configuration only
 * when it is copied; the tunnel starts or stops through the same reviewed
 * settings actions as Settings › Access.
 */
export function webhookReach(controller: ClientController) {
  return {
    readAddress: async (task: string, revision: string) => {
      const blob = await controller.downloadTaskWebhook(task, revision);
      if (!blob.size || blob.size > 65536)
        throw clientError({ code: 'action_denied' });
      let path: unknown;
      try {
        path = (JSON.parse(await blob.text()) as { relative_url?: unknown })
          .relative_url;
      } catch {
        path = undefined;
      }
      if (
        typeof path !== 'string' ||
        !path.startsWith(`/api/webhook/${encodeURIComponent(task)}?`)
      )
        throw clientError({ code: 'action_denied' });
      return path;
    },
    loadTunnel: async (signal?: AbortSignal) =>
      reachOf(await controller.settingsSnapshot(signal)),
    setPublic: async (on: boolean) => {
      const snapshot = await controller.settingsSnapshot();
      const request: SettingsMutationRequest = {
        settings_revision: snapshot.revision,
        page: 'system',
        field: on ? 'tunnel.start_main' : 'tunnel.stop_main',
        value: true,
      };
      const review = await controller.reviewSettingsMutation(request);
      const receipt = await controller.executeSettingsMutation(
        request,
        review,
        crypto.randomUUID(),
      );
      if (receipt.status !== 'completed' || !receipt.snapshot)
        throw clientError({ code: receipt.code || 'operation_uncertain' });
      return {
        tunnel: reachOf(receipt.snapshot),
        message:
          receipt.action_result?.message ??
          (on
            ? 'Reachable from the internet.'
            : 'No longer reachable from the internet.'),
      };
    },
  };
}
