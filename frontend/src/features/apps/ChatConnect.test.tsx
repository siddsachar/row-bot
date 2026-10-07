import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type {
  IntegrationDetail,
  IntegrationEntry,
  InstallPlan,
} from '../../api/types';
import type { ClientPlatform } from '../../platform';
import { RuntimeContext } from '../../runtime';
import ChatConnect from './ChatConnect';

const digest = 'd'.repeat(64);

function entry(fields: Partial<IntegrationEntry> = {}): IntegrationEntry {
  return {
    id: 'mcp:curated:notion',
    kind: 'mcp',
    parent_id: null,
    name: 'notion-mcp',
    description: '',
    app: {
      id: 'notion',
      name: 'Notion',
      publisher: 'Notion',
      category: 'productivity',
      icon: 'letter:N',
      verified: true,
      featured_rank: 3,
    },
    icon: 'letter:N',
    verified: true,
    source: 'recommended',
    method: 'hosted_sign_in',
    publisher: 'Notion',
    version: '',
    installed: false,
    enabled: false,
    account_label: '',
    compatibility: 'supported',
    evidence: 'inspected',
    tested_with_row_bot: false,
    lifecycle: 'available',
    readiness: null,
    blockers: [],
    next_action: { kind: 'connect', label: 'Connect' },
    attributions: [],
    children: [],
    ...fields,
  };
}

function detail(value: IntegrationEntry): IntegrationDetail {
  return {
    entry: value,
    plan: null,
    about: {
      license: '',
      source_url: '',
      pin: '',
      identifier: value.id,
      destination: '',
      runs_locally: false,
      saved_key: false,
      signs_in: true,
      signed_in: false,
      requirements: [],
      access: null,
      package: '',
      files: [],
      profiles: [],
      actions: [],
    },
  };
}

const review: InstallPlan = {
  schema_version: 1,
  plan_id: null,
  item_id: 'mcp:curated:notion',
  kind: 'mcp',
  name: 'Notion',
  intent: 'connect',
  digest,
  state: 'ready',
  pause: null,
  current_step: 'consent',
  steps: [
    {
      id: 'consent',
      type: 'consent',
      state: 'pending',
      title: 'Before you connect',
      message: '',
    },
  ],
  consent: {
    destinations: ['https://mcp.notion.com/mcp'],
    runs_locally: false,
    downloads: [],
    access_preset: 'ask',
  },
  supported: true,
  unsupported_reason: '',
  message: '',
  next_action: { kind: 'connect', label: 'Connect' },
  consent_token: 'token',
};

function show(controller: Record<string, unknown>, onContinue = vi.fn()) {
  const fake = {
    integrationIcons: vi.fn(async () => ({ schema_version: 1, items: [] })),
    ...controller,
  } as unknown as ClientController;
  render(
    <RuntimeContext.Provider
      value={{ controller: fake, platform: {} as ClientPlatform }}
    >
      <MemoryRouter>
        <ChatConnect
          apps={[
            {
              item_id: 'mcp:curated:notion',
              name: 'Notion',
              icon: 'letter:N',
            },
          ]}
          onContinue={onContinue}
        />
      </MemoryRouter>
    </RuntimeContext.Provider>,
  );
  return {
    controller: fake as unknown as Record<string, ReturnType<typeof vi.fn>>,
    onContinue,
  };
}

it('connects only through the app’s own consent sheet, as the catalog names it', async () => {
  const { controller } = show({
    integrationDetail: vi.fn(async () => detail(entry())),
    reviewInstallPlan: vi.fn(async () => review),
    startInstallPlan: vi.fn(() => new Promise(() => {})),
  });
  const card = await screen.findByRole('listitem', { name: 'Notion' });
  expect(within(card).getByText('by Notion')).toBeVisible();
  await act(async () =>
    fireEvent.click(within(card).getByRole('button', { name: 'Connect' })),
  );
  const sheet = await screen.findByRole('dialog', { name: 'Connect Notion' });
  expect(sheet).toHaveTextContent('What you ask goes to mcp.notion.com.');
  expect(controller.startInstallPlan).not.toHaveBeenCalled(); // Nothing starts before consent.
  await act(async () =>
    fireEvent.click(within(sheet).getByRole('button', { name: 'Connect' })),
  );
  expect(controller.startInstallPlan).toHaveBeenCalledWith(
    expect.objectContaining({
      item_id: 'mcp:curated:notion',
      digest,
      consent_token: 'token',
    }),
  );
});

it('offers Continue once the app is ready, and the request goes on in this chat', async () => {
  const { onContinue } = show({
    integrationDetail: vi.fn(async () =>
      detail(
        entry({
          id: 'mcp:abc',
          installed: true,
          lifecycle: 'installed',
          readiness: 'ready',
          next_action: { kind: 'try', label: 'Try it' },
        }),
      ),
    ),
  });
  const card = await screen.findByRole('listitem', { name: 'Notion, Ready' });
  fireEvent.click(within(card).getByRole('button', { name: 'Continue' }));
  expect(onContinue).toHaveBeenCalledExactlyOnceWith(); // Never the app's name: it can come from a listing.
});
