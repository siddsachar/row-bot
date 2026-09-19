import { act, fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { ProviderSettingsSnapshot } from '../../api/types';
import ProviderSettingsPanel from './ProviderSettingsPanel';
import { createProviderSettingsSessions } from './provider-settings-sessions';

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
  const callbacks = {
    load: vi.fn(async (id: string) => ({ ...snapshot, provider_id: id })),
    review: vi.fn().mockResolvedValue(snapshot),
    apply: vi.fn().mockResolvedValue(snapshot),
    receipt: vi.fn().mockResolvedValue(null),
    clear: vi.fn(),
  };
  const owner = createProviderSettingsSessions(controller, () => callbacks, {
    capacity: 1,
  });
  return {
    owner,
    callbacks,
    changeAuth() {
      handshake = { ...handshake, server_epoch: 'other' };
      subscriptions.forEach((fn) => fn());
    },
  };
}

it('preserves a private draft when the entire provider panel is unmounted then purges it on authentication change', async () => {
  const { owner, callbacks, changeAuth } = fixture();
  const props = {
    owner,
    providerId: 'openai',
    onSaved: vi.fn(),
    onCancel: vi.fn(),
  };
  const first = render(<ProviderSettingsPanel {...props} />);
  fireEvent.change(await screen.findByLabelText('New API key'), {
    target: { value: 'synthetic-private-draft' },
  });
  first.unmount();
  render(<ProviderSettingsPanel {...props} />);
  expect(await screen.findByLabelText('New API key')).toHaveValue(
    'synthetic-private-draft',
  );
  expect(callbacks.load).toHaveBeenCalledOnce();
  act(changeAuth);
  expect(screen.queryByLabelText('New API key')).not.toBeInTheDocument();
  expect(owner.hasRetained()).toBe(false);
  expect(callbacks.clear).toHaveBeenCalledOnce();
});

it('requires explicit discard at capacity and cannot discard an unresolved receipt', async () => {
  const { owner } = fixture();
  const props = {
    owner,
    providerId: 'openai',
    onSaved: vi.fn(),
    onCancel: vi.fn(),
  };
  const view = render(<ProviderSettingsPanel {...props} />);
  fireEvent.change(await screen.findByLabelText('New API key'), {
    target: { value: 'synthetic-private-draft' },
  });
  view.rerender(<ProviderSettingsPanel {...props} providerId="anthropic" />);
  expect(
    await screen.findByRole('button', { name: 'Discard unsent openai draft' }),
  ).toBeEnabled();
  const old = owner.open('openai')!;
  act(() => old.session.set('pending', 'original-command'));
  expect(
    screen.getByRole('button', { name: 'Discard unsent openai draft' }),
  ).toBeDisabled();
  act(() => old.session.set('pending', null));
  fireEvent.click(
    screen.getByRole('button', { name: 'Discard unsent openai draft' }),
  );
  expect(await screen.findByLabelText('New API key')).toHaveValue('');
  expect(old.session.active).toBe(false);
});
