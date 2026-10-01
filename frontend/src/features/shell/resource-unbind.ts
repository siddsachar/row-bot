import type { ClientController } from '../../api/controller';
import { clientError } from '../../api/errors';
import type { ResourceView } from '../../api/types';
import type { NoticeAction, NoticeTone } from '../../ui/overlays';

type Notify = (
  message: string,
  tone?: NoticeTone,
  action?: NoticeAction,
) => void;

/**
 * Remove a design or code folder from a conversation, with Undo in the
 * notice (decision 19): Undo adds the same saved resource back. Files and
 * the resource itself are never touched.
 */
export async function unbindWithUndo(
  controller: ClientController,
  notify: Notify,
  conversationId: string,
  resource: ResourceView,
): Promise<void> {
  const revision = () => controller.getSnapshot().conversation?.revision ?? '0';
  await controller.intent(
    conversationId,
    'conversation.unbind',
    { binding_id: resource.binding.binding_id },
    revision(),
  );
  notify(`Removed ${resource.title} from this conversation.`, undefined, {
    label: 'Undo',
    onAction: () => {
      controller
        .intent(
          conversationId,
          'resource.setup',
          {
            kind: resource.binding.kind,
            intent: 'add',
            resource_id: resource.binding.resource_id,
            expected_resource_revision: resource.resource_revision,
          },
          revision(),
        )
        .then(
          () => notify(`${resource.title} is back in this conversation.`),
          (cause: unknown) => notify(clientError(cause).message, 'warning'),
        );
    },
  });
}
