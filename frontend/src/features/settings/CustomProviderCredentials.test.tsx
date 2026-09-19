import { act, fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { ProviderSettingsSnapshot } from '../../api/types';
import CustomProviderCredentials, {
  createCustomProviderCredentialSessions,
} from './CustomProviderCredentials';

const id = `custom_openai_${'x'.repeat(64)}`;
const snapshot: ProviderSettingsSnapshot = {
  schema_version: 1,
  provider_id: id,
  display_name: 'Synthetic endpoint',
  revision: 'a'.repeat(64),
  configured: false,
  source: 'unknown',
  externally_managed: false,
  storage_unavailable: false,
  recovery_available: true,
  runtime_state: 'unknown',
};
function fixture() {
  let handshake = {
    instance_id: 'instance',
    server_epoch: 'epoch',
    client_session_id: 'session',
  };
  const subscriptions = new Set<() => void>();
  const controller = {
    getSnapshot: () => ({ handshake }),
    subscribe: (fn: () => void) => {
      subscriptions.add(fn);
      return () => {
        subscriptions.delete(fn);
      };
    },
  } as unknown as ClientController;
  const transport = {
    providerSettings: vi.fn().mockResolvedValue(snapshot),
    reviewProviderSettings: vi.fn().mockResolvedValue({
      provider_id: id,
      provider_revision: snapshot.revision,
      operation: 'save',
      nonce: 'nonce',
      action_digest: 'digest',
      snapshot,
    }),
    executeProviderCredential: vi.fn().mockResolvedValue(snapshot),
    providerSettingsReceipt: vi.fn().mockResolvedValue({
      command_id: 'command',
      status: 'uncertain',
      published: false,
      credential: snapshot,
    }),
  };
  const owner = createCustomProviderCredentialSessions(
    controller,
    transport,
    1,
  );
  return {
    owner,
    transport,
    changeAuth() {
      handshake = { ...handshake, server_epoch: 'other' };
      subscriptions.forEach((fn) => fn());
    },
  };
}

it('accepts the complete canonical ID and rejects aliases without normalization', () => {
  const { owner } = fixture();
  expect(owner.open(id)?.session.providerId).toBe(id);
  for (const invalid of [
    'openai',
    'custom_openai_bad:alias',
    'custom_openai_../bad',
    `${id}x`,
    'custom_openai_',
  ])
    expect(owner.open(invalid)).toBeNull();
  owner.dispose();
});

it('reuses the shared private editor across remount and purges credentials on auth change', async () => {
  const { owner, transport, changeAuth } = fixture();
  const props = { owner, providerId: id, onSaved: vi.fn(), onCancel: vi.fn() };
  const view = render(<CustomProviderCredentials {...props} />);
  fireEvent.change(await screen.findByLabelText('New API key'), {
    target: { value: 'synthetic-private-draft' },
  });
  view.unmount();
  render(<CustomProviderCredentials {...props} />);
  expect(await screen.findByLabelText('New API key')).toHaveValue(
    'synthetic-private-draft',
  );
  expect(transport.providerSettings).toHaveBeenCalledOnce();
  expect(transport.executeProviderCredential).not.toHaveBeenCalled();
  act(changeAuth);
  expect(screen.queryByLabelText('New API key')).not.toBeInTheDocument();
  expect(owner.hasRetained()).toBe(false);
  owner.dispose();
});

it('preserves exact reviewed custom identity and nonce and refuses an edited body', async () => {
  const { owner, transport } = fixture();
  const entry = owner.open(id)!;
  await entry.review(id, snapshot.revision, 'save', 'synthetic-value');
  await expect(
    entry.apply(id, snapshot.revision, 'save', 'different', 'command'),
  ).rejects.toMatchObject({ code: 'approval_expired' });
  expect(transport.executeProviderCredential).not.toHaveBeenCalled();
  await entry.apply(
    id,
    snapshot.revision,
    'save',
    'synthetic-value',
    'command',
  );
  expect(transport.executeProviderCredential).toHaveBeenCalledWith(
    id,
    snapshot.revision,
    'save',
    'synthetic-value',
    'command',
    'nonce',
  );
  owner.dispose();
});
