import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import {
  createProviderSettingsSessions,
  ProviderSettingsSession,
  providerSettingsCallbacks,
} from './provider-settings-sessions';
import type { ProviderSettingsSnapshot } from '../../api/types';

const snapshot: ProviderSettingsSnapshot = {
  schema_version: 1,
  provider_id: 'openai',
  display_name: 'OpenAI',
  revision: 'a'.repeat(64),
  configured: true,
  source: 'keyring',
  externally_managed: false,
  storage_unavailable: false,
  recovery_available: true,
  runtime_state: 'unknown',
};
function transport() {
  return {
    providerSettings: vi.fn().mockResolvedValue(snapshot),
    reviewProviderSettings: vi.fn().mockResolvedValue({
      provider_id: 'openai',
      provider_revision: snapshot.revision,
      operation: 'save',
      action_digest: 'digest',
      nonce: 'original-nonce',
      snapshot,
    }),
    executeProviderCredential: vi.fn().mockResolvedValue(snapshot),
    providerSettingsReceipt: vi.fn().mockResolvedValue({
      command_id: 'original-id',
      status: 'completed',
      published: true,
      credential: snapshot,
    }),
  };
}

it('binds confirmation to the exact reviewed secret/action/revision and original nonce', async () => {
  const api = transport(),
    callbacks = providerSettingsCallbacks(api);
  await callbacks.review(
    'openai',
    snapshot.revision,
    'save',
    'synthetic-private',
  );
  await expect(
    callbacks.apply(
      'openai',
      snapshot.revision,
      'save',
      'synthetic-private ',
      'wrong',
    ),
  ).rejects.toMatchObject({ code: 'approval_expired' });
  await expect(
    callbacks.apply('openai', snapshot.revision, 'clear', undefined, 'wrong'),
  ).rejects.toMatchObject({ code: 'approval_expired' });
  expect(api.executeProviderCredential).not.toHaveBeenCalled();
  await callbacks.apply(
    'openai',
    snapshot.revision,
    'save',
    'synthetic-private',
    'original-id',
  );
  expect(api.executeProviderCredential).toHaveBeenCalledExactlyOnceWith(
    'openai',
    snapshot.revision,
    'save',
    'synthetic-private',
    'original-id',
    'original-nonce',
  );
  await expect(
    callbacks.apply(
      'openai',
      snapshot.revision,
      'save',
      'synthetic-private',
      'again',
    ),
  ).rejects.toMatchObject({ code: 'approval_expired' });
  expect(api.reviewProviderSettings).toHaveBeenCalledOnce();
});

it('purges nonce and private review tuple, fencing a review resolving after disposal', async () => {
  const api = transport(),
    callbacks = providerSettingsCallbacks(api);
  let resolve!: (value: unknown) => void;
  api.reviewProviderSettings.mockReturnValue(
    new Promise((done) => {
      resolve = done;
    }),
  );
  const reviewing = callbacks.review(
    'openai',
    snapshot.revision,
    'save',
    'synthetic-private',
  );
  callbacks.clear!();
  resolve({
    provider_id: 'openai',
    provider_revision: snapshot.revision,
    operation: 'save',
    nonce: 'late-nonce',
    snapshot,
  });
  await expect(reviewing).rejects.toMatchObject({
    code: 'operation_cancelled',
  });
  await expect(
    callbacks.apply(
      'openai',
      snapshot.revision,
      'save',
      'synthetic-private',
      'late',
    ),
  ).rejects.toMatchObject({ code: 'approval_expired' });
  expect(api.executeProviderCredential).not.toHaveBeenCalled();
});

it('reads only the original receipt and distinguishes rejection from uncertain and completed', async () => {
  const api = transport(),
    callbacks = providerSettingsCallbacks(api);
  api.providerSettingsReceipt.mockResolvedValueOnce({
    command_id: 'wrong',
    status: 'completed',
    credential: snapshot,
  });
  await expect(
    callbacks.receipt('openai', 'original-id'),
  ).rejects.toMatchObject({ code: 'operation_uncertain' });
  api.providerSettingsReceipt.mockResolvedValueOnce({
    command_id: 'original-id',
    status: 'uncertain',
    credential: snapshot,
  });
  expect(await callbacks.receipt('openai', 'original-id')).toBeNull();
  api.providerSettingsReceipt.mockResolvedValueOnce({
    command_id: 'original-id',
    status: 'rejected',
    credential: snapshot,
  });
  expect(await callbacks.receipt('openai', 'original-id')).toEqual({
    rejected: true,
    snapshot,
  });
  expect(await callbacks.receipt('openai', 'original-id')).toEqual(snapshot);
  expect(api.executeProviderCredential).not.toHaveBeenCalled();
});

function fixture(capacity = 2) {
  let handshake = {
    instance_id: 'instance',
    server_epoch: 'epoch',
    client_session_id: 'session',
  };
  const subscribers = new Set<() => void>();
  const callbacks = {
    load: vi.fn(),
    review: vi.fn(),
    apply: vi.fn(),
    receipt: vi.fn(),
  };
  const controller = {
    getSnapshot: () => ({ handshake }),
    subscribe: (fn: () => void) => {
      subscribers.add(fn);
      return () => {
        subscribers.delete(fn);
      };
    },
  } as unknown as ClientController;
  const owner = createProviderSettingsSessions(controller, () => callbacks, {
    capacity,
  });
  return {
    owner,
    callbacks,
    changeAuth() {
      handshake = { ...handshake, client_session_id: 'other' };
      subscribers.forEach((fn) => fn());
    },
  };
}

it('retains exact secret/review state without evicting dirty or uncertain entries', async () => {
  const { owner } = fixture(1),
    entry = owner.open('openai')!;
  entry.session.get('secret', '');
  entry.session.set('secret', 'synthetic-private');
  expect(owner.open('openai')).toBe(entry);
  expect(owner.hasRetained()).toBe(true);
  expect(owner.open('anthropic')).toBeNull();
  expect(owner.getSnapshot().capacity).toBe(true);
  expect(owner.discard('openai')).toBe(true);
  const other = owner.open('anthropic')!;
  other.session.get('pending', null);
  other.session.set('pending', 'original-id');
  expect(owner.discard('anthropic')).toBe(false);
  expect(owner.open('openai')).toBeNull();
  other.session.set('pending', null);
  other.session.resolved();
  expect(owner.hasRetained()).toBe(false);
  expect(owner.open('openai')).not.toBeNull();
});

it('purges secret and pending state on auth changes and masks late retrieval', async () => {
  const { owner, callbacks, changeAuth } = fixture();
  const entry = owner.open('openai')!;
  entry.session.get('secret', '');
  entry.session.set('secret', 'synthetic-private');
  entry.session.get('pending', null);
  entry.session.set('pending', 'private-id');
  let resolve!: (value: unknown) => void;
  callbacks.load.mockReturnValue(
    new Promise((done) => {
      resolve = done;
    }),
  );
  const oldRead = entry.load('openai');
  changeAuth();
  expect(entry.session.active).toBe(false);
  expect(entry.session.get('secret', '')).toBe('');
  expect(entry.session.get('pending', null)).toBeNull();
  expect(owner.hasRetained()).toBe(false);
  resolve({ private: true });
  await expect(oldRead).rejects.toMatchObject({
    code: 'authentication_required',
  });
  await expect(
    entry.apply('openai', 'a', 'clear', undefined, 'original'),
  ).rejects.toMatchObject({ code: 'authentication_required' });
  expect(callbacks.apply).not.toHaveBeenCalled();
  expect(owner.open('openai')).not.toBe(entry);
  owner.dispose();
  expect(owner.open('openai')).toBeNull();
});

it('holds one operation and never replays a failed private body', async () => {
  const session = new ProviderSettingsSession('openai');
  let reject!: (cause: unknown) => void;
  const apply = vi.fn(
    () =>
      new Promise((_, fail) => {
        reject = fail;
      }),
  );
  const result = session.perform(['original', 'synthetic-private'], apply);
  expect(session.perform(['changed'], apply)).toBe(result);
  expect(session.canDiscard()).toBe(false);
  reject({ code: 'operation_uncertain' });
  await expect(result).rejects.toMatchObject({ code: 'operation_uncertain' });
  expect(apply).toHaveBeenCalledOnce();
  expect(session.canDiscard()).toBe(false);
  session.resolved();
  await Promise.resolve();
  expect(session.canDiscard()).toBe(true);
});

it('bounds secret UTF-8 bytes independently of characters and bounds owner capacity', () => {
  const session = new ProviderSettingsSession('openai');
  session.get('secret', '');
  session.set('secret', 'é'.repeat(8192));
  session.set('secret', 'é'.repeat(8193));
  expect(session.get('secret', '')).toHaveLength(8192);
  expect(() => fixture(17)).toThrow('invalid_provider_settings_capacity');
  session.dispose();
  expect(session.get('secret', '')).toBe('');
});
