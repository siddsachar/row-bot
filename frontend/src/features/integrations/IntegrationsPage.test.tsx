import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { ClientPlatform } from '../../platform';
import type { IntegrationItem } from '../../api/types';
import { RuntimeContext } from '../../runtime';
import { retainCommand } from '../../api/retained-command';
import IntegrationsPage from './IntegrationsPage';

const item: IntegrationItem = {
  id: 'skill:writing',
  kind: 'skill',
  owner_ref: 'writing',
  name: 'Writing',
  description: 'Local writing instructions',
  parent_id: null,
  source: 'local',
  publisher: '',
  source_url: '',
  version: '1',
  pin: '',
  license: 'MIT',
  compatibility: 'supported',
  reasons: [],
  platforms: [],
  evidence: 'Local only',
  installed: true,
  enabled: false,
  status: 'off',
  revision: 'a'.repeat(64),
  actions: ['configure'],
  auth_status: 'none',
  account_label: '',
  children: [],
  target: null,
};
const page = {
  schema_version: 1 as const,
  revision: 'a'.repeat(64),
  items: [item],
  total: 1,
  next_cursor: null,
  sources: [],
};
function Location() {
  const location = useLocation();
  return <output aria-label="Location">{location.search}</output>;
}
function fixture(path = '/settings/integrations') {
  const controller = {
    integrations: vi.fn().mockResolvedValue(page),
    integration: vi.fn().mockResolvedValue(item),
    searchIntegrations: vi
      .fn()
      .mockResolvedValue({ ...page, items: [{ ...item, installed: false }] }),
    previewIntegration: vi.fn(),
    reconcileIntegrationOperation: vi.fn().mockResolvedValue({
      settled: true,
      message: 'Recovered original package.',
    }),
  };
  render(
    <RuntimeContext.Provider
      value={{
        controller: controller as unknown as ClientController,
        platform: {} as ClientPlatform,
      }}
    >
      <MemoryRouter initialEntries={[path]}>
        <Location />
        <IntegrationsPage
          renderDetail={(row) => <p>Editor for {row.name}</p>}
          renderAdvanced={() => <p>Advanced editor</p>}
        />
      </MemoryRouter>
    </RuntimeContext.Provider>,
  );
  return controller;
}
afterEach(() => sessionStorage.clear());

it('loads local inventory, focuses selected details and preserves URL filters', async () => {
  const controller = fixture('/settings/integrations?type=skill&q=writing');
  fireEvent.click(await screen.findByRole('button', { name: 'Writing' }));
  expect(await screen.findByText('Editor for Writing')).toBeVisible();
  await waitFor(() =>
    expect(screen.getByRole('heading', { name: 'Writing' })).toHaveFocus(),
  );
  expect(screen.getByLabelText('Location')).toHaveTextContent(
    'type=skill&q=writing&selected=skill%3Awriting',
  );
  expect(controller.searchIntegrations).not.toHaveBeenCalled();
  expect(controller.previewIntegration).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Back to integrations' }));
  expect(screen.getByLabelText('Location')).not.toHaveTextContent('selected=');
});

it('contacts public catalogs only on explicit search and inspects only on request', async () => {
  const controller = fixture(
    '/settings/integrations?tab=discover&source=hermes',
  );
  await screen.findByRole('button', { name: 'Writing' });
  expect(controller.searchIntegrations).toHaveBeenCalledWith({
    query: '',
    sources: ['hermes'],
    refresh: false,
  });
  fireEvent.click(screen.getByRole('button', { name: 'Search public source' }));
  await waitFor(() =>
    expect(controller.searchIntegrations).toHaveBeenLastCalledWith({
      query: '',
      sources: ['hermes'],
      refresh: true,
    }),
  );
  expect(controller.previewIntegration).not.toHaveBeenCalled();
});

it('recovers a retained operation without sending a second command', async () => {
  const id = crypto.randomUUID();
  retainCommand('integrations:plugin', id);
  const controller = fixture();
  fireEvent.click(
    await screen.findByRole('button', {
      name: 'Check original plugin operation',
    }),
  );
  expect(await screen.findByText('Recovered original package.')).toBeVisible();
  expect(
    controller.reconcileIntegrationOperation,
  ).toHaveBeenCalledExactlyOnceWith('plugin', id);
  expect(controller.previewIntegration).not.toHaveBeenCalled();
});

it('keeps specialized advanced editors in one active view', async () => {
  const controller = fixture();
  await screen.findByRole('button', { name: 'Writing' });
  fireEvent.click(
    screen.getByText('Advanced configuration and existing editors'),
  );
  fireEvent.click(
    screen.getByRole('button', {
      name: 'Create, import, pin and maintain skills',
    }),
  );
  expect(screen.getByText('Advanced editor')).toBeVisible();
  expect(
    screen.queryByRole('button', { name: 'Writing' }),
  ).not.toBeInTheDocument();
  controller.integrations.mockResolvedValue({
    ...page,
    items: [{ ...item, name: 'Edited writing' }],
  });
  fireEvent.click(screen.getByRole('button', { name: 'Back to integrations' }));
  expect(
    await screen.findByRole('button', { name: 'Edited writing' }),
  ).toBeVisible();
});
