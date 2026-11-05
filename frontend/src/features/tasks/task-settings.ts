import type { ClientController } from '../../api/controller';
import { clientError } from '../../api/errors';
import type { TaskSettingsFields, TaskSettingsSnapshot } from '../../api/types';
import { taskMutation, type TaskCommandOwner } from './task-edits';

export function taskSettings(
  controller: ClientController,
  owner: TaskCommandOwner<TaskSettingsSnapshot> = { pending: null },
) {
  const mutate = taskMutation(controller, owner);
  const change = (
    task: string,
    revision: string,
    fields?: TaskSettingsFields,
    profileRevision?: string,
    confirmFullAutonomy = false,
  ) =>
    mutate(
      fields ? 'task.settings.update' : 'task.webhook.rotate',
      {
        task_id: task,
        task_revision: revision,
        ...(fields ? { fields, profile_revision: profileRevision } : {}),
        // The person turned Hands-off on in its one-time dialog just now.
        ...(fields && confirmFullAutonomy
          ? { confirm_full_autonomy: true }
          : {}),
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
      confirmFullAutonomy = false,
    ) => change(task, revision, fields, profileRevision, confirmFullAutonomy),
    rotate: (task: string, revision: string) => change(task, revision),
  };
}
