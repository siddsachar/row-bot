import type { ClientController } from '../../api/controller';
import { clientError } from '../../api/errors';
import type { TaskGraphSnapshot, TaskGraphStepEdit } from '../../api/types';
import { taskMutation, type TaskCommandOwner } from './task-edits';

/** A committed graph is reconciled by receipt, never rewritten on retry. */
export function taskGraphs(
  controller: ClientController,
  owner: TaskCommandOwner<TaskGraphSnapshot> = { pending: null },
) {
  const mutate = taskMutation(controller, owner);
  return (
    task: string,
    revision: string,
    steps: TaskGraphStepEdit[],
  ): Promise<TaskGraphSnapshot> =>
    mutate(
      'task.graph.update',
      { task_id: task, task_revision: revision, steps },
      task,
      async () => {
        const current = await controller.taskGraph(task);
        if (current.task_id !== task)
          throw clientError({ code: 'operation_uncertain' });
        return current;
      },
    );
}
