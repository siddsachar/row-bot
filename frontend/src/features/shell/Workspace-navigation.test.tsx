import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom';
import type { ReactNode } from 'react';
import { ClientController } from '../../api/controller';
import { FixtureTransport } from '../../api/fixtures';
import { createFakePlatform } from '../../platform/fake';
import { RuntimeContext } from '../../runtime';
import { OverlayProvider } from '../../ui/overlays';
import Workspace from './Workspace';

// JSDOM has no measured panes. Keep the production shell, navigation, router,
// composer and controller mounted while replacing only resize geometry.
vi.mock('react-resizable-panels', () => ({
  Group: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Panel: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Separator: () => <div />,
  usePanelRef: () => ({ current: null }),
}));

const clients: ClientController[] = [];
beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});
afterEach(() => {
  clients.splice(0).forEach((controller) => controller.dispose());
  vi.unstubAllGlobals();
});

function HistoryControls() {
  const navigate = useNavigate();
  const location = useLocation();
  return (
    <>
      <output aria-label="Current route">{location.pathname}</output>
      <button onClick={() => navigate(-1)}>Browser Back</button>
      <button onClick={() => navigate(1)}>Browser Forward</button>
    </>
  );
}

it.each([1440, 900, 390])(
  'Home adds one history entry and Back restores the same composer and draft at width %i',
  async (width) => {
    vi.stubGlobal('innerWidth', width);
    vi.stubGlobal('innerHeight', 900);
    const transport = new FixtureTransport({ conversationCount: 2 });
    const controller = new ClientController(transport, () => 1);
    clients.push(controller);
    await controller.start();
    await controller.selectConversation('conversation-a');
    render(
      <MemoryRouter initialEntries={['/conversations/conversation-a']}>
        <HistoryControls />
        <RuntimeContext.Provider
          value={{ controller, platform: createFakePlatform() }}
        >
          <OverlayProvider>
            <Workspace />
          </OverlayProvider>
        </RuntimeContext.Provider>
      </MemoryRouter>,
    );
    const composer = screen.getByRole('textbox', {
      name: 'Message',
    });
    await act(async () =>
      fireEvent.change(composer, {
        target: { value: 'Unsent draft retained across Home and Back' },
      }),
    );
    const commands = transport.counters.commands;
    const subscriptions = transport.counters.subscribes;
    const selectionVersion = controller.getSelectionVersion();
    if (width < 1024)
      fireEvent.click(
        screen.getByRole('button', { name: 'Toggle navigation' }),
      );
    await act(async () =>
      fireEvent.click(screen.getByRole('link', { name: 'Home' })),
    );
    expect(screen.getByLabelText('Current route').textContent).toBe('/');
    expect(screen.getByRole('heading', { name: 'Home' })).toBeVisible();
    expect(composer).not.toBeVisible();
    expect(screen.queryByRole('dialog')).toBeNull();
    for (let cycle = 0; cycle < 2; cycle++) {
      await act(async () =>
        fireEvent.click(screen.getByRole('button', { name: 'Browser Back' })),
      );
      expect(screen.getByLabelText('Current route').textContent).toBe(
        '/conversations/conversation-a',
      );
      expect(screen.getByRole('textbox', { name: 'Message' })).toBe(composer);
      expect(composer).toBeVisible();
      expect(composer).toHaveValue(
        'Unsent draft retained across Home and Back',
      );
      expect(controller.getSnapshot().selectedConversationId).toBe(
        'conversation-a',
      );
      expect(controller.getSelectionVersion()).toBe(selectionVersion);
      expect(transport.counters.commands).toBe(commands);
      expect(transport.counters.subscribes).toBe(subscriptions);
      if (cycle === 0)
        await act(async () =>
          fireEvent.click(
            screen.getByRole('button', { name: 'Browser Forward' }),
          ),
        );
    }
  },
);
