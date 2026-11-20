import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { ClientController } from '../../api/controller';
import { FixtureTransport } from '../../api/fixtures';
import { createFakePlatform } from '../../platform/fake';
import { RuntimeContext } from '../../runtime';
import { OverlayProvider } from '../../ui/overlays';
import LibraryPage from './LibraryPage';
import { WorkspaceActionsContext } from './workspace-actions';

const clients: ClientController[] = [];
afterEach(() => {
  clients.splice(0).forEach((controller) => controller.dispose());
  onPhone(false);
});

function onPhone(phone = true) {
  vi.mocked(window.matchMedia).mockImplementation(
    (query: string) =>
      ({
        matches: phone && query === '(max-width: 767px)',
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }) as unknown as MediaQueryList,
  );
}

/** The workspace's phone header, with the slot a place fills. */
function Shell({ newChat }: { newChat: () => void }) {
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  return (
    <>
      <header ref={setSlot} aria-label="Phone header" />
      <WorkspaceActionsContext.Provider
        value={{ resetLayout: vi.fn(), newChat, phoneHeaderSlot: slot }}
      >
        <LibraryPage />
      </WorkspaceActionsContext.Provider>
    </>
  );
}

async function renderPage(path = '/library') {
  const controller = new ClientController(
    new FixtureTransport({ conversationCount: 3 }),
    () => 1,
  );
  clients.push(controller);
  await controller.start();
  const newChat = vi.fn();
  render(
    <MemoryRouter initialEntries={[path]}>
      <RuntimeContext.Provider
        value={{ controller, platform: createFakePlatform() }}
      >
        <OverlayProvider>
          <Shell newChat={newChat} />
        </OverlayProvider>
      </RuntimeContext.Provider>
    </MemoryRouter>,
  );
  return { newChat };
}

it('a phone shows Chats | Files with New chat and ⋯ in the header; ⋯ › Select starts select mode (D10)', async () => {
  onPhone();
  const user = userEvent.setup();
  const { newChat } = await renderPage();
  const header = screen.getByRole('banner', { name: 'Phone header' });
  const show = screen.getByRole('radiogroup', { name: 'Show' });
  expect(within(show).getByRole('radio', { name: 'Chats' })).toBeChecked();
  expect(screen.queryByRole('heading', { name: 'Library' })).toBeNull();
  await screen.findAllByRole('link', { name: /^Sample conversation/ });

  await user.click(within(header).getByRole('button', { name: 'New chat' }));
  expect(newChat).toHaveBeenCalledOnce();

  await user.click(
    within(header).getByRole('button', { name: 'More for Chats' }),
  );
  await user.click(await screen.findByRole('menuitem', { name: 'Select' }));
  expect(
    screen.getByRole('toolbar', { name: 'Selected conversations' }),
  ).toBeVisible();
  expect(
    screen.getByRole('button', { name: /^Sample conversation 2/ }),
  ).toHaveAttribute('aria-pressed', 'false');

  await user.click(within(show).getByRole('radio', { name: 'Files' }));
  expect(await screen.findByRole('region', { name: 'Files' })).toBeVisible();
  await user.click(
    within(header).getByRole('button', { name: 'More for Chats' }),
  );
  expect(
    (await screen.findAllByRole('menuitem')).map((item) => item.textContent),
  ).toEqual(['Refresh']);
});

it('a phone opens a Library link to Media as Files with its Media chip on', async () => {
  onPhone();
  await renderPage('/library?show=media');
  expect(screen.getByRole('radio', { name: 'Files' })).toBeChecked();
  expect(
    within(screen.getByRole('radiogroup', { name: 'Kind of file' })).getByRole(
      'radio',
      { name: 'Media' },
    ),
  ).toBeChecked();
});

it('desktop keeps the Library title and its three segments, and fills no header slot', async () => {
  await renderPage();
  expect(screen.getByRole('heading', { name: 'Library' })).toBeVisible();
  const segments = screen.getByRole('radiogroup', {
    name: 'Show in the library',
  });
  expect(within(segments).getAllByRole('radio')).toHaveLength(3);
  expect(screen.queryByRole('radiogroup', { name: 'Show' })).toBeNull();
  expect(
    screen.getByRole('banner', { name: 'Phone header' }),
  ).toBeEmptyDOMElement();
});
