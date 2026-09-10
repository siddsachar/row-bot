import { beforeEach, expect, it } from 'vitest';
import { SetupSessions } from './setup-state';

beforeEach(() => sessionStorage.clear());
it('discards corrupt receipt fields while retaining the identity needed for recovery', () => {
  const owner = new SetupSessions(() => sessionStorage);
  const key = owner.scope('instance', 'conversation');
  owner.reserve(key, '00000000-0000-4000-8000-000000000001');
  const raw = JSON.parse(sessionStorage.getItem(key)!);
  raw.receipt = {
    command_id: raw.commandId,
    status: 'completed',
    confirmed_stages: {},
    resource_id: {},
  };
  sessionStorage.setItem(key, JSON.stringify(raw));
  const restored = new SetupSessions(() => sessionStorage);
  expect(restored.read(key).receipt).toBeNull();
  expect(restored.read(key).commandId).toBe(raw.commandId);
  expect(() => restored.reserve(key, 'duplicate')).toThrow('setup_pending');
});
it('restores bounded inputs and receipt identity across owner recreation without granting authority', () => {
  const first = new SetupSessions(() => sessionStorage),
    key = first.scope('instance', 'conversation-a');
  first.update(key, {
    name: 'Draft deck',
    brief: 'Keep this input',
    generate: true,
  });
  first.reserve(key, 'setup-command');
  const reloaded = new SetupSessions(() => sessionStorage);
  expect(reloaded.read(key)).toMatchObject({
    name: 'Draft deck',
    brief: 'Keep this input',
    generate: true,
    commandId: 'setup-command',
  });
  expect(() => reloaded.reserve(key, 'duplicate')).toThrow('setup_pending');
  expect(() => reloaded.reset(key)).toThrow('setup_pending');
  expect(sessionStorage.getItem(key)).not.toMatch(
    /grant|csrf|token|session_id/,
  );
  expect(
    reloaded.read(reloaded.scope('other-instance', 'conversation-a')).commandId,
  ).toBeNull();
  expect(
    reloaded.read(reloaded.scope('instance', 'conversation-b')).commandId,
  ).toBeNull();
});
it('records generation separately and never downgrades a confirmed receipt with a stale read', () => {
  const owner = new SetupSessions(() => sessionStorage),
    key = owner.scope('instance', null);
  owner.reserve(key, 'setup');
  owner.confirm(key, 'setup', {
    command_id: 'setup',
    status: 'completed',
    resource_id: 'deck',
  });
  owner.confirm(key, 'setup', { command_id: 'setup', status: 'admitting' });
  owner.confirm(key, 'foreign', { command_id: 'foreign', status: 'rejected' });
  expect(owner.read(key).receipt?.status).toBe('completed');
  owner.reserve(key, 'generation', true);
  expect(() => owner.reset(key)).toThrow('setup_pending');
  owner.confirm(
    key,
    'generation',
    { command_id: 'generation', status: 'admitting' },
    true,
  );
  expect(() => owner.reset(key)).toThrow('setup_pending');
  owner.confirm(
    key,
    'generation',
    { command_id: 'generation', status: 'accepted' },
    true,
  );
  expect(owner.read(key).receipt?.resource_id).toBe('deck');
  expect(() => owner.reserve(key, 'again', true)).toThrow('setup_pending');
  owner.reset(key);
  expect(owner.read(key).commandId).toBeNull();
});
it('requires a successful recovery write before reserving any mutation', () => {
  const owner = new SetupSessions(() => {
      throw new Error('Storage denied');
    }),
    key = owner.scope('instance', null);
  expect(() => owner.reserve(key, 'must-not-dispatch')).toThrow(
    'Storage denied',
  );
  expect(owner.read(key).commandId).toBeNull();
});
it('bounds stored scopes and malformed input without erasing a pending record', () => {
  const owner = new SetupSessions(() => sessionStorage);
  for (let index = 0; index < 16; index++)
    owner.update(owner.scope('instance', String(index)), { brief: 'Retained' });
  expect(() =>
    owner.update(owner.scope('instance', 'overflow'), { brief: 'Extra' }),
  ).toThrow('setup_capacity');
  expect(sessionStorage.length).toBe(16);
  const key = owner.scope('new-instance', 'malformed');
  sessionStorage.clear();
  sessionStorage.setItem(
    key,
    JSON.stringify({ kind: 'artifact', brief: 'bad shape' }),
  );
  expect(new SetupSessions(() => sessionStorage).read(key).brief).toBe('');
});
