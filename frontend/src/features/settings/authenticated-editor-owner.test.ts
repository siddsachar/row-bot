import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import { createAuthenticatedEditorOwner } from './authenticated-editor-owner';

function fixture() {
  let handshake: Record<string, string> | undefined = {
    instance_id: 'installation',
    server_epoch: 'epoch',
    client_session_id: 'session',
  };
  const listeners = new Set<() => void>();
  const controller = {
    getSnapshot: () => ({ handshake }),
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  } as unknown as ClientController;
  const create = vi.fn(() => {
    let privateDraft = '';
    let active = true;
    return {
      dispose: vi.fn(() => {
        privateDraft = '';
        active = false;
      }),
      hasRetained: () => Boolean(privateDraft),
      settle: (value: string) => {
        if (active) privateDraft = value;
      },
      read: () => privateDraft,
    };
  });
  const owner = createAuthenticatedEditorOwner(controller, create);
  return {
    owner,
    create,
    listeners,
    change: (patch?: Record<string, string>, notify = true) => {
      handshake = patch ? { ...handshake, ...patch } : undefined;
      if (notify) listeners.forEach((listener) => listener());
    },
  };
}

it('retains one reviewed editor across unrelated route updates', () => {
  const f = fixture(),
    editor = f.owner.get()!;
  editor.settle('synthetic-reviewed-private-draft');
  f.change({});
  expect(f.owner.get()).toBe(editor);
  expect(f.owner.hasRetained()).toBe(true);
  expect(f.create).toHaveBeenCalledOnce();
});

it.each(['instance_id', 'server_epoch', 'client_session_id'])(
  'purges private draft and fences late completion when %s changes',
  (field) => {
    const f = fixture(),
      old = f.owner.get()!;
    old.settle('synthetic-pending-original-command');
    f.change({ [field]: 'replacement' });
    old.settle('synthetic-late-private-result');
    expect(old.read()).toBe('');
    expect(old.dispose).toHaveBeenCalledOnce();
    expect(f.owner.get()).not.toBe(old);
    expect(f.owner.hasRetained()).toBe(false);
  },
);

it('synchronously fences a missed authentication notification and disposes once', () => {
  const f = fixture(),
    old = f.owner.get()!;
  old.settle('synthetic-private');
  f.change(undefined, false);
  expect(f.owner.hasRetained()).toBe(false);
  expect(f.owner.get()).toBeUndefined();
  expect(old.read()).toBe('');
  f.owner.dispose();
  f.owner.dispose();
  expect(f.listeners.size).toBe(0);
  f.change({
    instance_id: 'new',
    server_epoch: 'new',
    client_session_id: 'new',
  });
  expect(f.owner.get()).toBeUndefined();
  expect(f.create).toHaveBeenCalledTimes(2);
});
