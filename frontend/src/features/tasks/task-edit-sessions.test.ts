import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { TaskEditableFields, TaskSaveResult } from '../../api/types';
import { createTaskEditSessions } from './task-edit-sessions';

const fields: TaskEditableFields = {
  name: 'Synthetic workflow',
  description: '',
  icon: '',
  prompts: ['Original'],
  enabled: false,
  schedule: null,
  at: null,
  notify_only: false,
  notify_label: '',
  channels: null,
};
const saved = { id: 'task', revision: 'b'.repeat(64), fields };
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (cause: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
function fixture(capacity = 8) {
  const state = {
    handshake: {
      client_session_id: 'session',
      server_epoch: 'epoch',
      instance_id: 'instance',
    } as {
      client_session_id: string;
      server_epoch: string;
      instance_id: string;
    } | null,
  };
  const listeners = new Set<() => void>();
  const controller = {
    getSnapshot: () => state,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    command: vi.fn().mockResolvedValue({
      status: 'completed',
      task_id: 'task',
      task_saved: true,
      task_created: true,
    }),
    retryCommand: vi.fn(),
    receipt: vi.fn(),
    taskEditor: vi.fn().mockResolvedValue(saved),
    taskGraph: vi.fn(),
    taskSettings: vi.fn(),
  };
  const owner = createTaskEditSessions(
    controller as unknown as ClientController,
    { capacity },
  );
  return {
    owner,
    state,
    controller,
    listeners,
    emit: () => listeners.forEach((listener) => listener()),
  };
}

it('keeps independent bounded task, graph and settings drafts through close and re-open', () => {
  const { owner } = fixture();
  const editor = owner.open('task', 'task')!;
  editor.session.get('fields', fields);
  editor.session.set('fields', { ...fields, name: 'Retained' }, true);
  owner.close();
  const graph = owner.open('graph', 'task')!;
  graph.session.get('steps', []);
  graph.session.set('steps', [{ id: 'stable' }], true);
  expect(owner.open('task', 'task')).toBe(editor);
  expect(editor.session.get('fields', fields).name).toBe('Retained');
  expect(owner.getSnapshot().drafts).toHaveLength(2);
  expect(owner.hasRetained()).toBe(true);
  owner.dispose();
});

it('retains one exact promise and original command through response loss and a new selection', async () => {
  const { owner, controller } = fixture();
  const entry = owner.open('task')!;
  const response = deferred<unknown>();
  controller.command.mockReturnValue(response.promise);
  const operation = entry.session.run(() => entry.edits.create(fields));
  expect(
    entry.session.run(() =>
      entry.edits.create({ ...fields, name: 'Must not dispatch' }),
    ),
  ).toBe(operation);
  owner.close();
  owner.open('settings', 'task');
  response.reject(new TypeError('lost'));
  await expect(operation).rejects.toThrow();
  const identity = controller.command.mock.calls[0][2];
  expect(entry.session.getMeta().uncertain).toBe(true);
  expect(owner.discard(JSON.stringify(['task', '']))).toBe(false);
  controller.receipt.mockResolvedValue({
    status: 'completed',
    task_saved: true,
    task_created: true,
    task_id: 'task',
    command_id: identity,
  });
  expect(owner.open('task')).toBe(entry);
  const result = await entry.session.retryOperation<TaskSaveResult>();
  expect(result.task.id).toBe('task');
  expect(controller.command).toHaveBeenCalledOnce();
  expect(controller.receipt).toHaveBeenCalledWith(identity);
  entry.session.clean();
  expect(owner.hasRetained()).toBe(false);
  owner.dispose();
});

it('never evicts dirty or uncertain sessions at capacity and exposes explicit refusal', async () => {
  const { owner, controller } = fixture(2);
  const first = owner.open('task', 'first')!;
  first.session.get('draft', '');
  first.session.set('draft', 'Private draft', true);
  const second = owner.open('task', 'second')!;
  controller.command.mockRejectedValue(new TypeError('lost'));
  await expect(
    second.session.run(() => second.edits.save('second', 'old', fields)),
  ).rejects.toThrow();
  expect(owner.open('graph', 'third')).toBeNull();
  expect(owner.getSnapshot().capacity).toBe(true);
  expect(first.session.get('draft', '')).toBe('Private draft');
  expect(owner.discard()).toBe(false);
  expect(owner.discard(JSON.stringify(['task', 'first']))).toBe(true);
  expect(owner.open('graph', 'third')).not.toBeNull();
  expect(owner.getSnapshot().capacity).toBe(false);
  owner.dispose();
});

it('purges private drafts and pending command holders on authentication loss and denies late settlement', async () => {
  const { owner, controller, state, emit } = fixture();
  const entry = owner.open('task')!;
  entry.session.get('draft', '');
  entry.session.set('draft', 'Private draft', true);
  const response = deferred<unknown>();
  controller.command.mockReturnValue(response.promise);
  const operation = entry.session.run(() => entry.edits.create(fields));
  state.handshake = null;
  emit();
  expect(entry.session.get('draft', '')).toBe('');
  expect(entry.session.getMeta().active).toBe(false);
  expect(owner.hasRetained()).toBe(false);
  expect(owner.getSnapshot().drafts).toEqual([]);
  state.handshake = {
    client_session_id: 'next',
    server_epoch: 'epoch',
    instance_id: 'instance',
  };
  emit();
  response.resolve({ status: 'completed', task_id: 'task', task_saved: true });
  await expect(operation).rejects.toMatchObject({
    code: 'authentication_required',
  });
  expect(controller.taskEditor).not.toHaveBeenCalled();
  expect(entry.session.get('draft', '')).toBe('');
  expect(owner.open('task')).not.toBe(entry);
  owner.dispose();
});

it('holds private download admission until actual completion without retaining secret bytes or a replay', async () => {
  const { owner } = fixture(1);
  const entry = owner.open('settings', 'task')!;
  const result = deferred<void>();
  const operation = entry.session.run(() => result.promise, true);
  expect(owner.hasRetained()).toBe(true);
  expect(owner.discard()).toBe(false);
  expect(owner.open('graph', 'other')).toBeNull();
  result.reject(new TypeError('download lost'));
  await expect(operation).rejects.toThrow();
  expect(entry.session.getMeta().uncertain).toBe(false);
  expect(owner.hasRetained()).toBe(false);
  await expect(entry.session.retryOperation()).rejects.toMatchObject({
    code: 'operation_uncertain',
  });
  expect(owner.discard()).toBe(true);
  owner.dispose();
});

it('enforces bounded draft state without truncating the retained content and aborts reads on disposal', () => {
  const { owner, listeners } = fixture();
  const entry = owner.open('graph', 'task')!;
  entry.session.get('draft', '');
  entry.session.set('draft', 'Retained', true);
  entry.session.set('draft', 'x'.repeat(2 * 1024 * 1024 + 1), true);
  expect(entry.session.get('draft', '')).toBe('Retained');
  expect(entry.session.getMeta().limit).toBe(true);
  const read = entry.session.beginRead(0)!;
  owner.dispose();
  expect(read.signal.aborted).toBe(true);
  expect(listeners.size).toBe(0);
  expect(entry.session.get('draft', '')).toBe('');
});
