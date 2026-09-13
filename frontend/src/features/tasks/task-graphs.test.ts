import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { TaskGraphStepEdit } from '../../api/types';
import { taskGraphs } from './task-graphs';

function fixture() {
  const saved = { task_id: 'task', revision: 'b'.repeat(64), steps: [] };
  const receipt = { status: 'completed', task_id: 'task', task_saved: true };
  const controller = {
    getSnapshot: () => ({ handshake: { client_session_id: 'session' } }),
    command: vi.fn().mockResolvedValue(receipt),
    retryCommand: vi.fn().mockResolvedValue(receipt),
    receipt: vi.fn().mockResolvedValue(receipt),
    taskGraph: vi.fn().mockResolvedValue(saved),
  };
  return {
    controller,
    saved,
    save: taskGraphs(controller as unknown as ClientController),
  };
}

it('reads the saved graph only after explicit durable save', async () => {
  const { controller, saved, save } = fixture();
  expect(controller.command).not.toHaveBeenCalled();
  expect(await save('task', 'a'.repeat(64), [])).toEqual(saved);
  expect(controller.command.mock.calls[0][1]).toMatchObject({
    type: 'task.graph.update',
    payload: { task_id: 'task', task_revision: 'a'.repeat(64), steps: [] },
  });
});

it('recovers lost response from original receipt without resaving', async () => {
  const { controller, save, saved } = fixture();
  controller.command.mockRejectedValueOnce(new TypeError('Lost response'));
  await expect(save('task', 'a'.repeat(64), [])).rejects.toThrow();
  const identity = controller.command.mock.calls[0][2];
  await expect(save('task', 'a'.repeat(64), [])).resolves.toEqual(saved);
  expect(controller.receipt).toHaveBeenCalledWith(identity);
  expect(controller.command).toHaveBeenCalledOnce();
  expect(controller.retryCommand).not.toHaveBeenCalled();
});

it('retains an independent copy of uncertain steps and rejects a different intent', async () => {
  const { controller, save } = fixture();
  const steps = [
    { id: 'one', type: 'prompt', fields: { prompt: 'Original' } },
  ] as TaskGraphStepEdit[];
  controller.command.mockRejectedValueOnce(new TypeError('Lost response'));
  await expect(save('task', 'a'.repeat(64), steps)).rejects.toThrow();
  steps[0].fields.prompt = 'Changed';
  expect(
    controller.command.mock.calls[0][1].payload.steps[0].fields.prompt,
  ).toBe('Original');
  await expect(save('task', 'a'.repeat(64), steps)).rejects.toMatchObject({
    code: 'operation_uncertain',
  });
  expect(controller.command).toHaveBeenCalledOnce();
});

it('reconciles a proven partial commit with the same command identity', async () => {
  const { controller, save } = fixture();
  const partial = {
    status: 'partial',
    task_saved: true,
    task_id: 'task',
    code: 'task_saved_read_unconfirmed',
  };
  controller.command.mockResolvedValueOnce(partial);
  controller.receipt.mockResolvedValueOnce(partial);
  await expect(save('task', 'a'.repeat(64), [])).rejects.toThrow();
  await save('task', 'a'.repeat(64), []);
  expect(controller.retryCommand).toHaveBeenCalledWith(
    null,
    controller.command.mock.calls[0][1],
    controller.command.mock.calls[0][2],
  );
});
