import { afterEach, expect, it, vi } from 'vitest';
import * as wire from '../../../contracts/client-platform/v1/typescript/client';
import { FixtureTransport } from './fixtures';
import { HttpTransport } from './http';

afterEach(() => vi.restoreAllMocks());

it('revokes the captured probe owner even while its initial request remains unresolved', async () => {
  const handshake = await new FixtureTransport().connect();
  vi.spyOn(wire, 'handshake').mockResolvedValue(handshake);
  const revoke = vi
    .spyOn(wire, 'revokeSubscriptionFlows')
    .mockResolvedValue({ quiescent: false });
  let reject!: (cause: Error) => void;
  const send = vi.spyOn(wire, 'sendSubscriptionProbe').mockImplementation(
    () =>
      new Promise((_resolve, failed) => {
        reject = failed;
      }),
  );
  const http = new HttpTransport();
  await http.connect();
  const pending = http
    .applySubscriptionProbe({
      command_id: crypto.randomUUID(),
      client_session_id: handshake.client_session_id,
      expected_revision: '0',
      type: 'provider.subscription.probe',
      payload: {
        provider_id: 'codex',
        provider_revision: 'a'.repeat(64),
        kind: 'tokens',
        model_ref: null,
        nonce: 'synthetic',
      },
    })
    .catch(() => undefined);
  http.clearSession(true);
  expect(revoke).not.toHaveBeenCalled();
  http.clearSession();
  http.clearSession();
  expect(revoke).toHaveBeenCalledExactlyOnceWith('', send.mock.calls[0][1]);
  reject(new Error('Synthetic cancelled probe'));
  await pending;
});

it('cancels only the captured account owner on authentication teardown, including pending Start', async () => {
  const handshake = await new FixtureTransport().connect();
  vi.spyOn(wire, 'handshake').mockResolvedValue(handshake);
  const revoke = vi
    .spyOn(wire, 'revokeSubscriptionFlows')
    .mockResolvedValue({ quiescent: false });
  let reject!: (cause: Error) => void;
  const start = vi.spyOn(wire, 'sendSubscriptionAction').mockImplementation(
    () =>
      new Promise((_done, failed) => {
        reject = failed;
      }),
  );
  const http = new HttpTransport();
  await http.connect();
  const command = {
    command_id: '00000000-0000-4000-8000-000000000001',
    client_session_id: handshake.client_session_id,
    expected_revision: '0',
    type: 'provider.subscription.start',
    payload: {
      provider_id: 'codex',
      provider_revision: 'a'.repeat(64),
      nonce: 'synthetic',
    },
  } as const;
  const pending = http.subscriptionAction(command).catch(() => undefined);
  const originalProof = start.mock.calls[0][1];
  http.clearSession();
  http.clearSession();
  expect(revoke).toHaveBeenCalledExactlyOnceWith('', originalProof);
  reject(new Error('Synthetic cancelled Start'));
  await pending;
});

it('does not perform account network calls for passive sessions or temporary offline resumption', async () => {
  const handshake = await new FixtureTransport().connect();
  vi.spyOn(wire, 'handshake').mockResolvedValue(handshake);
  const revoke = vi
    .spyOn(wire, 'revokeSubscriptionFlows')
    .mockResolvedValue({ quiescent: true });
  const http = new HttpTransport();
  await http.connect();
  http.clearSession();
  expect(revoke).not.toHaveBeenCalled();
  await http.connect();
  vi.spyOn(wire, 'sendSubscriptionAction').mockRejectedValue(
    new Error('Synthetic lost response'),
  );
  await http
    .subscriptionAction({
      command_id: '00000000-0000-4000-8000-000000000001',
      client_session_id: handshake.client_session_id,
      expected_revision: '0',
      type: 'provider.subscription.start',
      payload: {
        provider_id: 'codex',
        provider_revision: 'a'.repeat(64),
        nonce: 'synthetic',
      },
    })
    .catch(() => {});
  http.clearSession(true);
  expect(revoke).not.toHaveBeenCalled();
  await http.connect();
  http.clearSession();
  expect(revoke).toHaveBeenCalledOnce();
});
