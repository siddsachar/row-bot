import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import Phase4RetainedSettings, {
  type Phase4RetainedSetting,
} from './Phase4RetainedSettings';

const fixture = vi.hoisted(() => ({
  runtime: {} as Record<string, unknown>,
  state: {} as Record<string, unknown>,
}));

vi.mock('../../runtime', () => ({
  useRuntime: () => fixture.runtime,
  useClientState: () => fixture.state,
}));

vi.mock('./SubscriptionAccounts', () => ({
  default: () => <section aria-label="Composed subscription accounts" />,
}));
vi.mock('./SubscriptionOptions', () => ({
  default: () => <section aria-label="Composed subscription options" />,
}));
vi.mock('./SubscriptionProbes', () => ({
  default: ({ onBrowseModels }: { onBrowseModels: () => void }) => (
    <section aria-label="Composed subscription probes">
      <button onClick={onBrowseModels}>Browse models from accounts</button>
    </section>
  ),
}));

function state(overrides: Record<string, unknown> = {}) {
  return {
    status: 'ready',
    connection: 'sse',
    selectedConversationId: 'conversation-a',
    handshake: {
      server_epoch: 'private-server-epoch',
      client_session_id: 'private-client-session',
      authentication_kind: 'local_owner',
      capabilities: [
        {
          id: 'tracker',
          available: true,
          requires_approval: true,
          unavailable_reason: null,
        },
        {
          id: 'calculator',
          available: true,
          requires_approval: false,
          unavailable_reason: null,
        },
        {
          id: 'weather',
          available: false,
          requires_approval: false,
          unavailable_reason: 'unavailable',
        },
      ],
    },
    ...overrides,
  };
}

function owner() {
  return { get: () => ({ active: true }) };
}

function renderSetting(setting: Phase4RetainedSetting) {
  return render(
    <MemoryRouter>
      <Phase4RetainedSettings setting={setting} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  fixture.state = state();
  fixture.runtime = {
    controller: {
      dictationCapability: vi.fn().mockResolvedValue({
        schema_version: 1,
        browser_dictation_available: true,
        native_capture_available: false,
        reason: 'available',
      }),
    },
    capabilitySettingsOwner: owner(),
    runtimeInstallationsOwner: owner(),
  };
});

it('reports passive voice readiness and links to the selected conversation', async () => {
  renderSetting('voice');

  await screen.findByText(/Talk, Realtime Talk, and dictation are available/);
  expect(
    screen.getByRole('link', { name: 'Open conversation voice' }),
  ).toHaveAttribute('href', '/conversations/conversation-a');
  expect(
    screen.getByRole('link', { name: 'Open current Voice settings' }),
  ).toHaveAttribute('href', '/');
  expect(screen.getAllByText('Unsupported in this client')).toHaveLength(1);
  expect(fixture.runtime.controller).toEqual(
    expect.objectContaining({ dictationCapability: expect.any(Function) }),
  );
});

it('does not claim voice availability when the passive check fails', async () => {
  const controller = fixture.runtime.controller as {
    dictationCapability: ReturnType<typeof vi.fn>;
  };
  controller.dictationCapability.mockRejectedValueOnce({
    code: 'capability_unavailable',
  });
  renderSetting('voice');

  const unavailable = await screen.findByText(
    /does not currently expose local dictation/,
  );
  expect(unavailable.closest('[data-capability-state]')).toHaveAttribute(
    'data-capability-state',
    'unsupported',
  );
  expect(screen.getAllByText('Unsupported in this client')).toHaveLength(2);
});

it('composes the existing reviewed subscription account owners', () => {
  fixture.runtime = {
    controller: {
      dictationCapability: vi.fn(),
    },
    subscriptionAccountsOwner: owner(),
    subscriptionOptionsOwner: owner(),
    subscriptionProbesOwner: owner(),
  };
  renderSetting('accounts');

  expect(
    screen.getByRole('region', { name: 'Composed subscription accounts' }),
  ).toBeVisible();
  expect(
    screen.getByRole('region', { name: 'Composed subscription options' }),
  ).toBeVisible();
  expect(
    screen.getByRole('region', { name: 'Composed subscription probes' }),
  ).toBeVisible();
  expect(
    screen.getByText(
      'Saved subscription status and reviewed sign-in controls are available below.',
    ),
  ).toBeVisible();
  expect(
    screen.getByRole('link', { name: 'Open current Accounts settings' }),
  ).toHaveAttribute('href', '/');
  fireEvent.click(
    screen.getByRole('button', { name: 'Browse models from accounts' }),
  );
});

it('reports tracker and utility availability only from the saved capability catalogue', () => {
  const first = renderSetting('tracker');
  expect(screen.getByText(/saved Tracker tool is enabled/)).toBeVisible();
  expect(
    screen.getByRole('link', { name: 'Browse saved tools' }),
  ).toHaveAttribute('href', '/settings/tools');
  first.unmount();

  renderSetting('utilities');
  expect(
    screen.getByText(
      '1 of 2 detected utility tools are enabled for conversations.',
    ),
  ).toBeVisible();
  expect(
    screen.getByRole('link', { name: 'Browse utility tools' }),
  ).toHaveAttribute('href', '/settings/tools');
});

it('shows connection state without exposing session or server identities', () => {
  renderSetting('system');

  expect(
    screen.getByText(
      'The authenticated client is connected as the local owner.',
    ),
  ).toBeVisible();
  expect(
    screen.getByRole('link', { name: 'Open MCP runtime settings' }),
  ).toHaveAttribute('href', '/settings/mcp');
  expect(
    screen.getByRole('link', { name: 'Open current System settings' }),
  ).toHaveAttribute('href', '/');
  expect(document.body).not.toHaveTextContent('private-server-epoch');
  expect(document.body).not.toHaveTextContent('private-client-session');
});
