import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { TaskEditableFields } from '../../api/types';
import { taskEdits } from './task-edits';

const fields: TaskEditableFields = {
  name: 'Test workflow',
  description: '',
  icon: '',
  prompts: ['Synthetic prompt'],
  enabled: false,
  schedule: null,
  at: null,
  notify_only: false,
  notify_label: '',
  channels: null,
};
function setup() {
  const saved = { id: 'task-one', revision: 'a'.repeat(64), fields };
  const receipt = {
    status: 'completed',
    task_id: saved.id,
    task_saved: true,
    task_created: true,
  };
  const controller = {
    getSnapshot: () => ({
      handshake: { client_session_id: 'fixture-session' },
    }),
    command: vi.fn().mockResolvedValue(receipt),
    retryCommand: vi.fn().mockResolvedValue(receipt),
    receipt: vi.fn().mockResolvedValue(receipt),
    taskEditor: vi.fn().mockResolvedValue(saved),
  };
  return {
    controller,
    saved,
    edits: taskEdits(controller as unknown as ClientController),
  };
}

it('creates explicitly with inherited delivery intact and reads the saved task', async () => {
  const { controller, edits, saved } = setup();
  expect(await edits.create(fields)).toEqual({
    task: saved,
    created: true,
    replayed: false,
  });
  expect(controller.command).toHaveBeenCalledWith(
    null,
    expect.objectContaining({
      type: 'task.create',
      payload: { fields },
      expected_revision: '0',
    }),
    expect.any(String),
  );
  expect(controller.taskEditor).toHaveBeenCalledWith('task-one');
});

it('recovers a lost response by the original receipt without duplicate creation', async () => {
  const { controller, edits } = setup();
  controller.command.mockRejectedValueOnce(new TypeError('Lost response'));
  await expect(edits.create(fields)).rejects.toThrow();
  const identity = controller.command.mock.calls[0][2];
  await expect(edits.create(fields)).resolves.toMatchObject({ replayed: true });
  expect(controller.receipt).toHaveBeenCalledWith(identity);
  expect(controller.command).toHaveBeenCalledOnce();
  expect(controller.retryCommand).not.toHaveBeenCalled();
});

it('reconciles partial scheduler state with the exact command identity', async () => {
  const { controller, edits } = setup();
  const partial = {
    status: 'partial',
    task_id: 'task-one',
    task_saved: true,
    task_created: true,
    code: 'task_saved_read_unconfirmed',
  };
  controller.command.mockResolvedValueOnce(partial);
  controller.receipt.mockResolvedValueOnce(partial);
  await expect(
    edits.save('task-one', 'a'.repeat(64), fields),
  ).rejects.toMatchObject({ code: partial.code });
  await edits.save('task-one', 'a'.repeat(64), fields);
  expect(controller.retryCommand).toHaveBeenCalledWith(
    null,
    controller.command.mock.calls[0][1],
    controller.command.mock.calls[0][2],
  );
});

it('retains the receipt identity after saved-state read failure and rejects a different uncertain intent', async () => {
  const { controller, edits } = setup();
  controller.taskEditor.mockRejectedValueOnce(new TypeError('Read lost'));
  await expect(edits.create(fields)).rejects.toThrow();
  await expect(
    edits.create({ ...fields, name: 'Other workflow' }),
  ).rejects.toMatchObject({ code: 'operation_uncertain' });
  await edits.create(fields);
  expect(controller.command).toHaveBeenCalledOnce();
});
