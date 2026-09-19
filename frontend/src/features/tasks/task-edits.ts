import type { ClientController } from '../../api/controller';
import { clientError } from '../../api/errors';
import type {
  Command,
  CommandReceipt,
  TaskEditableFields,
  TaskSaveResult,
} from '../../api/types';

export type TaskCommandAttempt<T> = {
  authentication: string;
  signature: string;
  command: Command;
  result?: Promise<T>;
};
export type TaskCommandOwner<T> = { pending: TaskCommandAttempt<T> | null };

/** Shared task mutation receipt boundary; each editor owns its own bounded holder. */
export function taskMutation<T>(
  controller: ClientController,
  owner: TaskCommandOwner<T>,
) {
  const identity = (state = controller.getSnapshot()) =>
    state.handshake
      ? JSON.stringify([
          state.handshake.instance_id,
          state.handshake.server_epoch,
          state.handshake.client_session_id,
        ])
      : '';
  const authentication = identity();
  const authorize = () => {
    const state = controller.getSnapshot();
    if (!authentication || identity(state) !== authentication)
      throw clientError({ code: 'authentication_required' });
    return state.handshake!;
  };
  return (
    type: Command['type'],
    payload: object,
    task: string | undefined,
    read: (id: string, receipt: CommandReceipt, replay: boolean) => Promise<T>,
  ): Promise<T> => {
    let session;
    try {
      session = authorize();
    } catch (cause) {
      return Promise.reject(cause);
    }
    const captured = structuredClone(payload);
    if (owner.pending && owner.pending.authentication !== authentication)
      return Promise.reject(clientError({ code: 'authentication_required' }));
    const signature = JSON.stringify([type, task, captured]);
    if (owner.pending && owner.pending.signature !== signature)
      return Promise.reject(clientError({ code: 'operation_uncertain' }));
    if (owner.pending?.result) return owner.pending.result;
    const replay = !!owner.pending;
    const attempt = owner.pending ?? {
      authentication,
      signature,
      command: {
        command_id: crypto.randomUUID(),
        client_session_id: session.client_session_id,
        type,
        expected_revision: '0',
        payload: captured,
      } as Command,
    };
    owner.pending = attempt;
    const perform = async () => {
      let receipt: CommandReceipt | null = null;
      if (replay) {
        try {
          receipt = await controller.receipt(attempt.command.command_id);
        } catch (cause) {
          if (clientError(cause).code !== 'not_found') throw cause;
        }
      }
      authorize();
      if (receipt?.status !== 'completed' && receipt?.status !== 'rejected') {
        receipt = replay
          ? await controller.retryCommand(
              null,
              attempt.command,
              attempt.command.command_id,
            )
          : await controller.command(
              null,
              attempt.command,
              attempt.command.command_id,
            );
      }
      authorize();
      if (
        receipt.command_id !== undefined &&
        receipt.command_id !== attempt.command.command_id
      )
        throw clientError({ code: 'operation_uncertain' });
      if (receipt.status === 'rejected') {
        owner.pending = null;
        throw clientError({ code: receipt.code || 'action_denied' });
      }
      if (
        receipt.status !== 'completed' ||
        !receipt.task_saved ||
        !receipt.task_id ||
        (task && receipt.task_id !== task)
      )
        throw clientError({ code: receipt.code || 'operation_uncertain' });
      const current = await read(receipt.task_id, receipt, replay);
      authorize();
      owner.pending = null;
      return current;
    };
    attempt.result = perform().finally(() => {
      attempt.result = undefined;
    });
    return attempt.result;
  };
}

/** Retain uncertain save identity until its receipt and saved state are read. */
export function taskEdits(
  controller: ClientController,
  owner: TaskCommandOwner<TaskSaveResult> = { pending: null },
) {
  const mutate = taskMutation(controller, owner);
  const edit = (fields: TaskEditableFields, task?: string, revision?: string) =>
    mutate(
      task ? 'task.update' : 'task.create',
      { fields, ...(task ? { task_id: task, task_revision: revision } : {}) },
      task,
      async (id, receipt, replayed) => {
        const saved = await controller.taskEditor(id);
        if (saved.id !== id) throw clientError({ code: 'operation_uncertain' });
        return {
          task: saved,
          created: receipt.task_created ?? false,
          replayed,
        };
      },
    );
  return {
    create: (fields: TaskEditableFields) => edit(fields),
    save: (task: string, revision: string, fields: TaskEditableFields) =>
      edit(fields, task, revision),
  };
}
