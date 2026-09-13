import type { ClientController } from '../../api/controller';
import { clientError } from '../../api/errors';
import type {
  Command,
  CommandReceipt,
  TaskRunReview,
  TaskApprovalReview,
  TaskRunResult,
  TaskApprovalResult,
  TaskStopResult,
} from '../../api/types';

/** Keep execution identities through lost responses; never infer a new run. */
export function taskRuns(controller: ClientController) {
  type Attempt = {
    signature: string;
    command: Command;
    result?: Promise<CommandReceipt>;
  };
  const attempts = new Map<string, Attempt>();
  const execute = async (
    type: 'task.run' | 'task.stop' | 'task.approval',
    payload: Record<string, unknown>,
  ) => {
    const state = controller.getSnapshot();
    if (!state.handshake)
      throw clientError({ code: 'authentication_required' });
    const signature = JSON.stringify([type, payload]);
    const pending = attempts.get(type);
    if (pending && pending.signature !== signature)
      throw clientError({ code: 'operation_uncertain' });
    if (pending?.result) return pending.result;
    const retry = !!pending;
    const attempt = pending ?? {
      signature,
      command: {
        command_id: crypto.randomUUID(),
        client_session_id: state.handshake.client_session_id,
        type,
        expected_revision: '0',
        payload,
      } as Command,
    };
    attempts.set(type, attempt);
    const perform = async () => {
      let receipt: CommandReceipt | null = null;
      if (retry) {
        try {
          receipt = await controller.receipt(attempt.command.command_id);
        } catch (error) {
          if (clientError(error).code !== 'not_found') throw error;
        }
      }
      if (!receipt || receipt.status === 'admitting')
        receipt = retry
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
      if (receipt.status !== 'completed' || !receipt.task_run_id)
        throw clientError({ code: receipt.code || 'operation_uncertain' });
      return receipt;
    };
    attempt.result = perform()
      .catch((error: unknown) => {
        if (
          [
            'task_revision_conflict',
            'task_policy_revision_conflict',
            'task_approval_revision_conflict',
            'task_approval_expired',
            'approval_expired',
            'action_denied',
            'task_not_found',
            'task_run_not_found',
          ].includes(clientError(error).code)
        )
          attempts.delete(type);
        throw error;
      })
      .finally(() => {
        attempt.result = undefined;
      });
    return attempt.result;
  };
  const run = async (review: TaskRunReview): Promise<TaskRunResult> => {
    const replayed = attempts.has('task.run');
    const receipt = await execute('task.run', {
      task_id: review.task_id,
      task_revision: review.task_revision,
      policy_revision: review.policy_revision,
    });
    if (!receipt.task_run_reserved)
      throw clientError({ code: 'operation_uncertain' });
    const saved = await controller.taskRun(
      review.task_id,
      receipt.task_run_id!,
    );
    attempts.delete('task.run');
    return { run: saved, replayed };
  };
  const stop = async (task: string, runId: string): Promise<TaskStopResult> => {
    const receipt = await execute('task.stop', {
      task_id: task,
      run_id: runId,
    });
    const saved = await controller.taskRun(task, runId);
    attempts.delete('task.stop');
    return {
      run: saved,
      stop_requested: receipt.task_stop_requested ?? false,
      quiesced: receipt.task_run_quiesced ?? false,
    };
  };
  const respondApproval = async (
    approval: TaskApprovalReview,
    approved: boolean,
  ): Promise<TaskApprovalResult> => {
    if (!approval.response_available || !approval.nonce)
      throw clientError({ code: 'approval_expired' });
    const receipt = await execute('task.approval', {
      task_id: approval.task_id,
      run_id: approval.run_id,
      approval_id: approval.id,
      approval_revision: approval.revision,
      approved,
      nonce: approval.nonce,
    });
    if (
      !receipt.task_approval_recorded ||
      receipt.task_approval_decision !== (approved ? 'approved' : 'denied')
    )
      throw clientError({ code: 'operation_uncertain' });
    const saved = await controller.taskRun(approval.task_id, approval.run_id);
    attempts.delete('task.approval');
    return {
      approval_id: approval.id,
      decision: receipt.task_approval_decision,
      run: saved,
    };
  };
  return { run, stop, respondApproval };
}
