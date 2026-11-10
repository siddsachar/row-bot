import type { ClientController } from '../../api/controller';
import { clientError } from '../../api/errors';
import type { CommandReceipt } from '../../api/types';

/**
 * Answers an approval as the person read it: with the nonce of the view they
 * saw, bound to its revision. That nonce lasts five minutes, while a card may
 * wait open far longer (a long read, a long typed answer, #631). When it has
 * run out, a fresh read of the same approval gives a new one, and the answer
 * goes once more only if the approval is still the one shown (same revision);
 * otherwise the person sees why it couldn't go.
 */
export async function resolveApproval(
  controller: ClientController,
  approvalId: string,
  view: { nonce: string; revision: string },
  payload: Record<string, unknown>,
): Promise<CommandReceipt> {
  try {
    return await controller.intent(
      approvalId,
      'approval.resolve',
      { ...payload, nonce: view.nonce },
      view.revision,
    );
  } catch (cause) {
    if (clientError(cause).code !== 'approval_expired') throw cause;
    const fresh = await controller.approval(approvalId).catch(() => null);
    if (!fresh?.nonce || fresh.revision !== view.revision) throw cause;
    return controller.intent(
      approvalId,
      'approval.resolve',
      { ...payload, nonce: fresh.nonce },
      fresh.revision,
    );
  }
}
