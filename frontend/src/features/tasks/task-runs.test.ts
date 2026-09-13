import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { TaskRunReview, TaskApprovalReview } from '../../api/types';
import { taskRuns } from './task-runs';

const review: TaskRunReview = {
  task_id: 'task',
  task_revision: '1'.repeat(64),
  policy_revision: '2'.repeat(64),
  agent_profile_id: 'profile',
  approval_mode: 'approve',
  notify_only: false,
  steps_total: 2,
  conversation_id: null,
};
const approval: TaskApprovalReview = {
  id: 'approval',
  task_id: 'task',
  run_id: 'run',
  revision: '3'.repeat(64),
  message: 'Review this action',
  requested_at: '',
  expires_at: null,
  approval_mode: 'approve',
  message_truncated: false,
  response_available: true,
  nonce: 'reviewed-nonce',
};
function fixture() {
  const saved = {
    id: 'run',
    task_id: 'task',
    conversation_id: 'chat',
    status: 'running',
    started_at: '',
    finished_at: null,
    steps_total: 2,
    steps_done: 0,
  };
  const controller = {
    getSnapshot: () => ({ handshake: { client_session_id: 'session' } }),
    command: vi.fn(),
    receipt: vi.fn(),
    retryCommand: vi.fn(),
    taskRun: vi.fn().mockResolvedValue(saved),
  };
  return {
    controller,
    saved,
    actions: taskRuns(controller as unknown as ClientController),
  };
}

it('runs only the exact reviewed policy and reads the resulting canonical run', async () => {
  const { controller, actions, saved } = fixture();
  expect(controller.command).not.toHaveBeenCalled();
  controller.command.mockResolvedValue({
    status: 'completed',
    task_run_id: 'run',
    task_run_reserved: true,
  });
  expect(await actions.run(review)).toEqual({ run: saved, replayed: false });
  expect(controller.command).toHaveBeenCalledWith(
    null,
    expect.objectContaining({
      type: 'task.run',
      payload: {
        task_id: 'task',
        task_revision: review.task_revision,
        policy_revision: review.policy_revision,
      },
    }),
    expect.any(String),
  );
});

it('recovers a lost completion response without another run', async () => {
  const { controller, actions } = fixture();
  controller.command.mockRejectedValue(new TypeError('lost response'));
  await expect(actions.run(review)).rejects.toThrow();
  controller.receipt.mockResolvedValue({
    status: 'completed',
    task_run_id: 'run',
    task_run_reserved: true,
  });
  expect((await actions.run(review)).replayed).toBe(true);
  expect(controller.command).toHaveBeenCalledOnce();
  expect(controller.retryCommand).not.toHaveBeenCalled();
});

it('does not replace uncertain Run intent and keeps Stop available', async () => {
  const { controller, actions } = fixture();
  controller.command.mockResolvedValueOnce({
    status: 'partial',
    task_run_id: 'run',
    task_run_reserved: true,
    code: 'task_run_unconfirmed',
  });
  await expect(actions.run(review)).rejects.toMatchObject({
    code: 'task_run_unconfirmed',
  });
  await expect(
    actions.run({ ...review, task_revision: '4'.repeat(64) }),
  ).rejects.toMatchObject({ code: 'operation_uncertain' });
  controller.command.mockResolvedValueOnce({
    status: 'completed',
    task_run_id: 'run',
    task_stop_requested: true,
    task_run_quiesced: false,
  });
  expect(await actions.stop('task', 'run')).toMatchObject({
    stop_requested: true,
    quiesced: false,
  });
  expect(controller.command).toHaveBeenCalledTimes(2);
});

it('sends the reviewed approval nonce once and never changes an uncertain decision', async () => {
  const { controller, actions } = fixture();
  controller.command.mockRejectedValue(new TypeError('lost response'));
  await expect(actions.respondApproval(approval, true)).rejects.toThrow();
  await expect(actions.respondApproval(approval, false)).rejects.toMatchObject({
    code: 'operation_uncertain',
  });
  controller.receipt.mockResolvedValue({
    status: 'completed',
    task_run_id: 'run',
    task_approval_recorded: true,
    task_approval_decision: 'approved',
  });
  expect(await actions.respondApproval(approval, true)).toMatchObject({
    approval_id: 'approval',
    decision: 'approved',
  });
  expect(controller.command).toHaveBeenCalledOnce();
  expect(controller.command.mock.calls[0][1].payload.nonce).toBe(
    'reviewed-nonce',
  );
});

it('rejects unavailable approvals without commands', async () => {
  const { controller, actions } = fixture();
  await expect(
    actions.respondApproval({ ...approval, response_available: false }, true),
  ).rejects.toMatchObject({ code: 'approval_expired' });
  expect(controller.command).not.toHaveBeenCalled();
});
