import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import type { ReactNode } from 'react';
import { ClientController } from '../../api/controller';
import { FixtureTransport } from '../../api/fixtures';
import { createFakePlatform } from '../../platform/fake';
import { RuntimeContext } from '../../runtime';
import { OverlayProvider } from '../../ui/overlays';
import Workspace from './Workspace';

// Rendering the whole Workspace on a loaded machine needs more than 5 s.
const HEAVY = 20_000;

// JSDOM has no measured panes: each pane says which one it is.
vi.mock('react-resizable-panels', () => ({
  Group: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Panel: ({ id, children }: { id: string; children: ReactNode }) => (
    <div data-pane={id}>{children}</div>
  ),
  Separator: () => <div />,
  usePanelRef: () => ({ current: null }),
}));
// The terminal itself has its own tests; here it only shows where it docks.
vi.mock('../panels/NativeTerminal', () => ({
  default: ({ onClose }: { onClose: () => void }) => (
    <section aria-label="Terminal">
      <button onClick={onClose}>Close terminal</button>
    </section>
  ),
}));

const clients: ClientController[] = [];
beforeEach(() => localStorage.clear());
afterEach(() => {
  clients.splice(0).forEach((controller) => controller.dispose());
  vi.unstubAllGlobals();
});

async function workspace(width: number) {
  vi.stubGlobal('innerWidth', width);
  vi.stubGlobal('innerHeight', 900);
  const controller = new ClientController(
    new FixtureTransport({ conversationCount: 2 }),
    () => 1,
  );
  clients.push(controller);
  await controller.start();
  await controller.selectConversation('conversation-a');
  render(
    <MemoryRouter initialEntries={['/conversations/conversation-a']}>
      <RuntimeContext.Provider
        value={{ controller, platform: createFakePlatform() }}
      >
        <OverlayProvider>
          <Workspace />
        </OverlayProvider>
      </RuntimeContext.Provider>
    </MemoryRouter>,
  );
}

const dock = () => screen.queryByRole('region', { name: 'Terminal' });
const toggle = (target: Element | Window = window) =>
  act(async () => {
    fireEvent.keyDown(target, { key: '`', code: 'Backquote', ctrlKey: true });
  });

it(
  'opens the terminal in a dock under the conversation from its header button and Ctrl+` (B249)',
  async () => {
    await workspace(1440);
    const button = screen.getByRole('button', { name: 'Terminal' });
    expect(button).toHaveAttribute('aria-pressed', 'false');
    expect(button).toHaveAttribute('aria-keyshortcuts', 'Control+`');
    await act(async () => fireEvent.click(button));
    const region = await screen.findByRole('region', { name: 'Terminal' });
    expect(region.closest('[data-pane]')).toHaveAttribute(
      'data-pane',
      'terminal-pane',
    );
    expect(button).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('region', { name: 'Conversation' })).toBeVisible();

    await toggle();
    expect(dock()).toBeNull();
    await toggle(screen.getByRole('textbox', { name: 'Message' }));
    expect(
      await screen.findByRole('region', { name: 'Terminal' }),
    ).toBeVisible();
    // Ctrl+` inside the terminal closes it and gives focus back to its button.
    const inside = screen.getByRole('button', { name: 'Close terminal' });
    inside.focus();
    await toggle(inside);
    expect(dock()).toBeNull();
    expect(button).toHaveFocus();
    await act(async () => fireEvent.click(button));
    await act(async () =>
      fireEvent.click(
        await screen.findByRole('button', { name: 'Close terminal' }),
      ),
    );
    expect(dock()).toBeNull();
    expect(button).toHaveAttribute('aria-pressed', 'false');
  },
  HEAVY,
);

it(
  'docks the terminal from Open panel and toggles it from the palette, never as a side panel',
  async () => {
    const user = userEvent.setup();
    await workspace(1440);
    await user.click(screen.getByRole('button', { name: 'Open panel' }));
    await user.click(
      await screen.findByRole('menuitem', { name: 'Interactive terminal' }),
    );
    const region = await screen.findByRole('region', { name: 'Terminal' });
    expect(region.closest('[data-pane]')).toHaveAttribute(
      'data-pane',
      'terminal-pane',
    );
    expect(screen.queryByRole('region', { name: 'Side panels' })).toBeNull();

    await user.click(
      screen.getByRole('button', { name: 'Workspace commands' }),
    );
    const palette = await screen.findByRole('dialog', {
      name: 'Workspace commands',
    });
    await user.keyboard('terminal');
    expect(within(palette).queryByText('Open Interactive terminal')).toBeNull();
    await user.click(await within(palette).findByText('Toggle terminal'));
    await waitFor(() => expect(dock()).toBeNull());
  },
  HEAVY,
);

it(
  'shows the terminal as a full-screen sheet on a phone',
  async () => {
    const user = userEvent.setup();
    await workspace(390);
    await user.click(screen.getByRole('button', { name: 'Conversation menu' }));
    await user.click(
      await screen.findByRole('menuitem', {
        name: 'Open Interactive terminal',
      }),
    );
    const region = await screen.findByRole('region', { name: 'Terminal' });
    expect(region.closest('.panel-sheet')).not.toBeNull();
    expect(screen.queryByRole('region', { name: 'Conversation' })).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Close terminal' }));
    expect(dock()).toBeNull();
    expect(screen.getByRole('region', { name: 'Conversation' })).toBeVisible();
  },
  HEAVY,
);
