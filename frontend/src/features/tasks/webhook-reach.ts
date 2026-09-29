import type { ClientController } from '../../api/controller';
import { clientError } from '../../api/errors';
import type {
  SettingsMutationRequest,
  SettingsSnapshot,
} from '../../api/types';
import type { WebhookConfiguration, WebhookReach } from './WebhookAddress';

const reachOf = (snapshot: SettingsSnapshot): WebhookReach => ({
  publicBase: snapshot.system.tunnel.main_app_url,
  canControl: snapshot.system.tunnel.local_owner_control_available,
});

/**
 * A saved webhook's address and reachability for the workflow page. The
 * address and its secret (sent in a header, B132) are read from the private
 * configuration only when copied; the tunnel starts or stops through the
 * same reviewed settings actions as Settings › Devices & remote access.
 */
export function webhookReach(controller: ClientController) {
  return {
    readAddress: async (
      task: string,
      revision: string,
    ): Promise<WebhookConfiguration> => {
      const blob = await controller.downloadTaskWebhook(task, revision);
      if (!blob.size || blob.size > 65536)
        throw clientError({ code: 'action_denied' });
      let body: {
        relative_url?: unknown;
        relative_url_with_secret?: unknown;
        headers?: unknown;
      } = {};
      try {
        body = JSON.parse(await blob.text()) as typeof body;
      } catch {
        body = {};
      }
      const path = `/api/webhook/${encodeURIComponent(task)}`;
      const headers =
        body.headers && typeof body.headers === 'object'
          ? Object.entries(body.headers as Record<string, unknown>)
          : [];
      const [header, secret] = headers[0] ?? [];
      if (
        body.relative_url !== path ||
        typeof body.relative_url_with_secret !== 'string' ||
        !body.relative_url_with_secret.startsWith(`${path}?`) ||
        headers.length !== 1 ||
        typeof header !== 'string' ||
        typeof secret !== 'string' ||
        !secret
      )
        throw clientError({ code: 'action_denied' });
      return {
        path,
        pathWithSecret: body.relative_url_with_secret,
        header,
        secret,
      };
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
