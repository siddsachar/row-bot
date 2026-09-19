import type { ClientController } from '../../api/controller';
import { clientError } from '../../api/errors';
import type { TaskSettingsFields, TaskSettingsSnapshot } from '../../api/types';
import { saveBrowserDownload } from '../../platform/download';
import { taskMutation, type TaskCommandOwner } from './task-edits';

export function taskSettings(
  controller: ClientController,
  owner: TaskCommandOwner<TaskSettingsSnapshot> = { pending: null },
) {
  const mutate = taskMutation(controller, owner);
  const identity = () => {
    const handshake = controller.getSnapshot().handshake;
    return handshake
      ? JSON.stringify([
          handshake.instance_id,
          handshake.server_epoch,
          handshake.client_session_id,
        ])
      : '';
  };
  const authentication = identity();
  const change = (
    task: string,
    revision: string,
    fields?: TaskSettingsFields,
    profileRevision?: string,
  ) =>
    mutate(
      fields ? 'task.settings.update' : 'task.webhook.rotate',
      {
        task_id: task,
        task_revision: revision,
        ...(fields ? { fields, profile_revision: profileRevision } : {}),
      },
      task,
      async () => {
        const current = await controller.taskSettings(task);
        if (current.task_id !== task)
          throw clientError({ code: 'operation_uncertain' });
        return current;
      },
    );
  return {
    save: (
      task: string,
      revision: string,
      profileRevision: string,
      fields: TaskSettingsFields,
    ) => change(task, revision, fields, profileRevision),
    rotate: (task: string, revision: string) => change(task, revision),
    download: async (task: string, revision: string, signal?: AbortSignal) => {
      const authorize = () => {
        if (!authentication || identity() !== authentication || signal?.aborted)
          throw clientError({ code: 'authentication_required' });
      };
      authorize();
      const result = await saveBrowserDownload(
        async () => {
          const blob = await controller.downloadTaskWebhook(
            task,
            revision,
            signal,
          );
          authorize();
          if (
            !blob.size ||
            blob.size > 65536 ||
            !['application/json', 'application/octet-stream'].includes(
              blob.type,
            )
          )
            throw clientError({ code: 'action_denied' });
          return blob;
        },
        'workflow-webhook.json',
        signal,
      );
      if (result.status !== 'ok') throw clientError({ code: 'action_denied' });
    },
  };
}
