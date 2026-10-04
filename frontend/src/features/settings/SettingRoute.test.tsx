import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { ClientPlatform } from '../../platform';
import { RuntimeContext } from '../../runtime';
import { WorkspaceActionsContext } from '../shell/workspace-actions';
import SettingRoute from './SettingRoute';
import { createCapabilitySettingsSession } from './CapabilitySettings';
import { createMcpFacadeSession } from './McpFacadeControls';
import { createMcpConnections } from './McpConnections';
import { createRuntimeInstallations } from '../mcp/RuntimeInstallations';

function Where() {
  const location = useLocation();
  return <output aria-label="Location">{location.pathname}</output>;
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
              <Route path="settings/:setting" element={<SettingRoute />} />
            </Routes>
          </MemoryRouter>
        </WorkspaceActionsContext.Provider>
      </RuntimeContext.Provider>,
    );
    expect(await screen.findByLabelText('Location')).toHaveTextContent('/');
    expect(openAgentProfiles).toHaveBeenCalledOnce();
  },
);

it('Settings › MCP puts the switches and runtimes first, then the servers (B262)', async () => {
  const state = { conversations: [], selectedConversationId: null };
  const revision = 'a'.repeat(64);
  const controller = {
    getSnapshot: () => state,
    subscribe: () => () => {},
    settingsSnapshot: () => new Promise(() => {}),
    integrationSources: vi
      .fn()
      .mockResolvedValue({ schema_version: 1, items: [] }),
    integrations: vi.fn().mockResolvedValue({
      schema_version: 1,
      revision,
      items: [],
      total: 0,
      next_cursor: null,
      sources: [],
    }),
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
    mcpConfiguration: vi.fn(async () => ({
      schema_version: 1,
      revision,
      availability: 'available',
      enabled: true,
      items: [
        {
          server_id: 'b'.repeat(64),
          name: 'context7',
          transport: 'streamable_http',
          enabled: true,
          runtime_status: 'connected',
          configured_fields: ['url'],
          tool_count: 2,
          connection_present: true,
        },
      ],
      total: 1,
      next_cursor: null,
    })),
    runtimeInstallation: vi.fn(async (runtime: string) => ({
      schema_version: 1,
      runtime_id: runtime,
      resource_revision: revision,
      availability: 'available',
      installed: true,
      active_command_id: null,
      quiesced: true,
      version: '1.0.0',
      system_available: false,
    })),
  } as unknown as ClientController;
  const owner = <T,>(value: T) => ({ get: () => value });
  render(
    <RuntimeContext.Provider
      value={
        {
          controller,
          platform: {} as ClientPlatform,
          capabilitySettingsOwner: owner(createCapabilitySettingsSession()),
          mcpChatOwner: owner(createMcpFacadeSession()),
          mcpConnectionsOwner: owner(createMcpConnections()),
          runtimeInstallationsOwner: owner(createRuntimeInstallations()),
        } as never
      }
    >
      <MemoryRouter initialEntries={['/settings/mcp']}>
        <Routes>
          <Route path="settings/:setting" element={<SettingRoute />} />
        </Routes>
      </MemoryRouter>
    </RuntimeContext.Provider>,
  );
  fireEvent.click(
    await screen.findByText('Advanced configuration and existing editors'),
  );
  fireEvent.click(
    screen.getByRole('button', {
      name: 'Custom MCP configuration and chat access',
    }),
  );
  const useMcp = await screen.findByRole('switch', { name: 'Use MCP servers' });
  const chat = screen.getByRole('switch', { name: 'Offer MCP tools in chats' });
  const runtimes = screen.getByText('Runtimes');
  const servers = screen.getByRole('region', { name: 'Servers' });
  expect(
    await within(servers).findByRole('button', { name: 'Disconnect context7' }),
  ).toBeVisible();
  // Page order: the two switches, the runtimes, then the servers.
  const order = [useMcp, chat, runtimes, servers];
  for (const [index, element] of order.slice(1).entries())
    expect(
      order[index].compareDocumentPosition(element) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  expect(await screen.findAllByText('Installed v1.0.0')).toHaveLength(2);
  expect(screen.getByText('1 of 1 server connected')).toBeVisible();
});
