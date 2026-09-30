import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { ClientPlatform } from '../../platform';
import { RuntimeContext } from '../../runtime';
import { WorkspaceActionsContext } from '../shell/workspace-actions';
import SettingRoute from './SettingRoute';

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
