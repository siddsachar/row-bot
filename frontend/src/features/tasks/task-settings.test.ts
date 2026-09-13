import { afterEach, expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { TaskSettingsFields, TaskSettingsSnapshot } from '../../api/types';
import { taskSettings } from './task-settings';
import type { TaskCommandOwner } from './task-edits';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
const fields: TaskSettingsFields = {
  concurrency_group: null,
  trigger_type: 'webhook',
  trigger_task_id: null,
  model_override: null,
  agent_profile_id: 'builtin:worker',
  approval_mode: 'block',
  persistent_enabled: false,
};
function fixture() {
  const state = {
    handshake: {
      client_session_id: 'session',
      server_epoch: 'epoch',
      instance_id: 'instance',
    },
  };
  const saved = { task_id: 'task', revision: 'new', fields };
  const controller = {
    getSnapshot: () => state,
    command: vi.fn().mockResolvedValue({
      status: 'completed',
      task_id: 'task',
      task_saved: true,
    }),
    retryCommand: vi.fn(),
    receipt: vi.fn(),
    taskSettings: vi.fn().mockResolvedValue(saved),
    downloadTaskWebhook: vi.fn(),
  };
  const owner: TaskCommandOwner<TaskSettingsSnapshot> = { pending: null };
  const typed = controller as unknown as ClientController;
  return {
    state,
    controller,
    owner,
    saved,
    typed,
    settings: taskSettings(typed, owner),
  };
}

it('retains immutable reviewed settings and original identity in a fresh adapter', async () => {
  const { controller, settings, owner, typed } = fixture();
  controller.command.mockRejectedValueOnce(new TypeError('lost'));
  const input = structuredClone(fields);
  await expect(
    settings.save('task', 'old', 'profile', input),
  ).rejects.toThrow();
  input.approval_mode = 'allow_all';
  expect(
    (controller.command.mock.calls[0][1].payload.fields as TaskSettingsFields)
      .approval_mode,
  ).toBe('block');
  const identity = owner.pending!.command.command_id;
  controller.receipt.mockResolvedValue({
    status: 'completed',
    task_saved: true,
    task_id: 'task',
    command_id: identity,
  });
  await taskSettings(typed, owner).save('task', 'old', 'profile', fields);
  expect(controller.command).toHaveBeenCalledOnce();
  expect(owner.pending).toBeNull();
});

it('rechecks authority after receipt await and cannot adopt an old holder in a new session', async () => {
  const { controller, settings, owner, typed, state } = fixture();
  controller.command.mockRejectedValueOnce(new TypeError('lost'));
  await expect(settings.rotate('task', 'old')).rejects.toThrow();
  controller.receipt.mockImplementation(async () => {
    state.handshake.client_session_id = 'different';
    return { status: 'partial' };
  });
  await expect(settings.rotate('task', 'old')).rejects.toMatchObject({
    code: 'authentication_required',
  });
  expect(controller.retryCommand).not.toHaveBeenCalled();
  expect(owner.pending).not.toBeNull();
  await expect(
    taskSettings(typed, owner).rotate('task', 'old'),
  ).rejects.toMatchObject({ code: 'authentication_required' });
});

it('never clears an uncertain command from an unrelated read denial, but resolves an authoritative rejection', async () => {
  const { controller, settings, owner } = fixture();
  controller.taskSettings.mockRejectedValueOnce({ code: 'action_denied' });
  await expect(
    settings.save('task', 'old', 'profile', fields),
  ).rejects.toThrow();
  expect(owner.pending).not.toBeNull();
  controller.receipt.mockResolvedValue({
    status: 'rejected',
    code: 'task_revision_conflict',
    command_id: owner.pending!.command.command_id,
  });
  await expect(
    settings.save('task', 'old', 'profile', fields),
  ).rejects.toMatchObject({ code: 'task_revision_conflict' });
  expect(owner.pending).toBeNull();
  expect(controller.command).toHaveBeenCalledOnce();
});

it.each(['receipt', 'task', 'snapshot'])(
  'rejects wrong %s identity without declaring completion',
  async (kind) => {
    const { controller, settings, owner } = fixture();
    if (kind === 'snapshot')
      controller.taskSettings.mockResolvedValue({ task_id: 'other', fields });
    else
      controller.command.mockResolvedValue({
        status: 'completed',
        task_saved: true,
        task_id: kind === 'task' ? 'other' : 'task',
        ...(kind === 'receipt' ? { command_id: 'different' } : {}),
      });
    await expect(settings.rotate('task', 'old')).rejects.toMatchObject({
      code: 'operation_uncertain',
    });
    expect(owner.pending).not.toBeNull();
  },
);

it.each(['oversize', 'empty', 'type', 'revoked', 'aborted'])(
  'does not expose a %s private webhook download',
  async (kind) => {
    const { controller, settings, state } = fixture();
    const abort = new AbortController();
    const create = vi.fn();
    vi.stubGlobal('URL', { createObjectURL: create, revokeObjectURL: vi.fn() });
    controller.downloadTaskWebhook.mockImplementation(async () => {
      if (kind === 'revoked') state.handshake.server_epoch = 'other';
      if (kind === 'aborted') abort.abort();
      return new Blob(
        [kind === 'empty' ? '' : 'x'.repeat(kind === 'oversize' ? 65537 : 5)],
        { type: kind === 'type' ? 'text/html' : 'application/json' },
      );
    });
    await expect(
      settings.download('task', 'old', abort.signal),
    ).rejects.toThrow();
    expect(create).not.toHaveBeenCalled();
  },
);

it('downloads bounded authenticated bytes through one revocable object URL', async () => {
  const { controller, settings } = fixture();
  const create = vi.fn().mockReturnValue('blob:private-fixture'),
    revoke = vi.fn();
  vi.stubGlobal('URL', { createObjectURL: create, revokeObjectURL: revoke });
  const click = vi
    .spyOn(HTMLAnchorElement.prototype, 'click')
    .mockImplementation(() => {});
  controller.downloadTaskWebhook.mockResolvedValue(
    new Blob(['synthetic private configuration'], { type: 'application/json' }),
  );
  await settings.download('task', 'old');
  expect(click).toHaveBeenCalledOnce();
  expect(revoke).toHaveBeenCalledWith('blob:private-fixture');
  expect(document.querySelector('a[download]')).toBeNull();
});
