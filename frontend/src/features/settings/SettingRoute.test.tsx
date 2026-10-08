import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { ClientPlatform } from '../../platform';
import { RuntimeContext } from '../../runtime';
import { WorkspaceActionsContext } from '../shell/workspace-actions';
import SettingRoute from './SettingRoute';
import { createCapabilitySettingsSession } from './CapabilitySettings';

function Where() {
  const location = useLocation();
  return (
    <output aria-label="Location">
      {location.pathname}
      {location.search}
    </output>
  );
}

it.each(['/settings/profiles', '/settings/agent-profiles'])(
  'opens the sidebar Agents dialog from the old %s link (B260)',
  async (path) => {
    const state = { conversations: [], selectedConversationId: null };
    const controller = {
      getSnapshot: () => state,
      subscribe: () => () => {},
      settingsSnapshot: () => new Promise(() => {}),
    } as unknown as ClientController;
    const openAgentProfiles = vi.fn();
    render(
      <RuntimeContext.Provider
        value={{ controller, platform: {} as ClientPlatform }}
      >
        <WorkspaceActionsContext.Provider
          value={{ resetLayout: vi.fn(), openAgentProfiles }}
        >
          <MemoryRouter initialEntries={[path]}>
            <Routes>
              <Route path="/" element={<Where />} />
              <Route path="settings/:setting/*" element={<SettingRoute />} />
            </Routes>
          </MemoryRouter>
        </WorkspaceActionsContext.Provider>
      </RuntimeContext.Provider>,
    );
    expect(await screen.findByLabelText('Location')).toHaveTextContent('/');
    expect(openAgentProfiles).toHaveBeenCalledOnce();
  },
);

const revision = 'a'.repeat(64);

/** A controller with the local Apps catalog empty; every read is a fake. */
function appsController(extra: Record<string, unknown> = {}) {
  const state = { conversations: [], selectedConversationId: null };
  return {
    getSnapshot: () => state,
    subscribe: () => () => {},
    settingsSnapshot: () => new Promise(() => {}),
    integrationItems: vi.fn(async () => ({
      schema_version: 1,
      revision,
      items: [],
      total: 0,
      next_cursor: null,
      sources: [],
    })),
    integrationApps: vi.fn(async () => ({ schema_version: 1, items: [] })),
    integrationIcons: vi.fn(async () => ({ schema_version: 1, items: [] })),
    ...extra,
  } as unknown as ClientController;
}

function renderAt(
  path: string,
  controller: ClientController,
  runtime: Record<string, unknown> = {},
) {
  render(
    <RuntimeContext.Provider
      value={
        { controller, platform: {} as ClientPlatform, ...runtime } as never
      }
    >
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="settings/:setting/*"
            element={
              <>
                <SettingRoute />
                <Where />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </RuntimeContext.Provider>,
  );
}

const owner = <T,>(value: T) => ({ get: () => value });

it.each(['/settings/mcp', '/settings/plugins'])(
  'the old %s link lands on the Apps library',
  async (path) => {
    renderAt(path, appsController());
    expect(
      await screen.findByRole('searchbox', { name: 'Search apps' }),
    ).toBeVisible();
    expect(screen.getByLabelText('Location')).toHaveTextContent(
      /^\/settings\/apps$/,
    );
    expect(screen.getByRole('heading', { name: 'All apps' })).toBeVisible();
    expect(await screen.findByText('No apps here yet')).toBeVisible();
  },
);

it('Apps › Advanced shows the catalogs, their schedule and chat access', async () => {
  const controller = appsController({
    integrationSources: vi.fn(async () => ({
      schema_version: 1,
      items: [
        {
          id: 'registry',
          kinds: ['mcp'],
          label: 'Synthetic Registry',
          access: 'snapshot',
          eligibility: 'eligible',
          network: 'explicit',
          enabled: true,
          message: '',
          catalog: null,
        },
        {
          id: 'coming',
          kinds: ['mcp'],
          label: 'Coming Catalog',
          access: 'snapshot',
          eligibility: 'contract_unresolved',
          network: 'explicit',
          enabled: false,
          message: '',
          catalog: null,
        },
        {
          id: 'examples',
          kinds: ['plugin'],
          label: 'Examples',
          access: 'snapshot',
          eligibility: 'explicit_only',
          network: 'none',
          enabled: true,
          message: '',
          catalog: null,
        },
      ],
    })),
    catalogSchedule: vi.fn(async () => ({ enabled: false, interval_days: 7 })),
    appViewSettings: vi.fn(async () => ({ enabled: true, apps: {} })),
    mcpPolicy: vi.fn(async () => ({
      schema_version: 1,
      revision,
      server_id: null,
      availability: 'available',
      global_enabled: true,
      server_enabled: null,
      resources_enabled: null,
      prompts_enabled: null,
      items: [],
      total: 0,
      next_cursor: null,
    })),
    mcpChat: vi.fn(async () => ({
      schema_version: 1,
      resource_revision: revision,
      availability: 'available',
      saved_enabled: true,
      effective_enabled: true,
      registered: true,
    })),
  });
  renderAt('/settings/apps?view=advanced', controller);
  const catalogs = await screen.findByRole('region', { name: 'Catalogs' });
  expect(await within(catalogs).findByText('Synthetic Registry')).toBeVisible();
  expect(
    within(catalogs).getByText('Apps · Searched when you ask'),
  ).toBeVisible();
  // Only catalogs that are searched for you: none not usable yet, no developer examples.
  expect(within(catalogs).queryByText('Coming Catalog')).toBeNull();
  expect(within(catalogs).queryByText('Examples')).toBeNull();
  expect(
    await within(catalogs).findByRole('switch', {
      name: 'Update catalogs automatically',
    }),
  ).not.toBeChecked();
  const chats = screen.getByRole('region', { name: 'Apps in chats' });
  // The switches people change come first.
  expect(
    chats.compareDocumentPosition(catalogs) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  // One switch for apps: no second one for chats.
  expect(
    await within(chats).findByRole('switch', { name: 'Use apps' }),
  ).toBeChecked();
  expect(within(chats).queryByRole('switch', { name: /in chats/ })).toBeNull();
  expect(
    await within(chats).findByRole('switch', {
      name: 'Show app views in chat',
    }),
  ).toBeChecked();
  // Opening the page only reads; nothing is updated or saved.
  expect(controller.integrationItems).not.toHaveBeenCalled();
});

it('Apps › Advanced › Add a custom connection opens the add dialog of the MCP editor', async () => {
  const controller = appsController({
    integrationSources: vi.fn(async () => ({ schema_version: 1, items: [] })),
    catalogSchedule: vi.fn(async () => ({ enabled: false, interval_days: 7 })),
    appViewSettings: vi.fn(async () => ({ enabled: true, apps: {} })),
    mcpConfiguration: vi.fn(async () => ({
      schema_version: 1,
      revision,
      availability: 'available',
      enabled: true,
      items: [],
      total: 0,
      next_cursor: null,
    })),
  });
  renderAt('/settings/apps?view=advanced', controller, {
    capabilitySettingsOwner: owner(createCapabilitySettingsSession()),
  });
  fireEvent.click(
    await screen.findByRole('link', { name: 'Add a custom connection' }),
  );
  expect(
    await screen.findByRole('dialog', { name: 'Add a server' }),
  ).toBeVisible();
  expect(screen.getByLabelText('Location')).toHaveTextContent(
    '/settings/apps/custom?edit=1',
  );
});

it.each([
  ['/settings/accounts#google', '/settings/apps/google'],
  ['/settings/accounts#github', '/settings/apps/github'],
  ['/settings/channels#telegram', '/settings/apps/telegram'],
  ['/settings/gmail', '/settings/apps/google'],
  ['/settings/channels', '/settings/apps?category=communication'],
])(
  'the old %s link opens that app, where its account or channel is a way to connect',
  async (path, target) => {
    renderAt(path, appsController());
    await waitFor(() =>
      expect(screen.getByLabelText('Location')).toHaveTextContent(target),
    );
  },
);

// Every other link Row-Bot 5.0.0 made to its Accounts, Channels, MCP and
// Plugins pages (palette, onboarding, Monitor and Home fixes).
it.each([
  ['/settings/accounts', '/settings/apps'],
  ['/settings/accounts#x', '/settings/apps/x'],
  ['/settings/google', '/settings/apps/google'],
  ['/settings/calendar', '/settings/apps/google'],
  ['/settings/channels#slack', '/settings/apps/slack'],
  ['/settings/channels#discord', '/settings/apps/discord'],
  ['/settings/channels#whatsapp', '/settings/apps/whatsapp'],
  ['/settings/channels#sms', '/settings/apps/sms'],
  ['/settings/mcp#mcp-servers', '/settings/apps'],
  ['/settings/mcp#mcp-runtimes', '/settings/apps?view=advanced'],
  ['/settings/plugins#installed-plugins', '/settings/apps'],
  ['/settings/plugins#plugin-marketplace', '/settings/apps'],
])('the 5.0.0 link %s lands on %s', async (path, target) => {
  renderAt(
    path,
    appsController({
      integrationSources: vi.fn(async () => ({ schema_version: 1, items: [] })),
      catalogSchedule: vi.fn(async () => ({
        enabled: false,
        interval_days: 7,
      })),
      appViewSettings: vi.fn(async () => ({ enabled: true, apps: {} })),
    }),
  );
  await waitFor(() =>
    expect(screen.getByLabelText('Location').textContent).toBe(target),
  );
});
