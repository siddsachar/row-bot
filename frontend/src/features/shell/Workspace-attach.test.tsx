import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import type { ReactNode } from 'react';
import * as wire from '../../../../contracts/client-platform/v1/typescript/client';
import recording from '../../../../contracts/client-platform/v1/fixtures/F-P12.json';
import { ClientController } from '../../api/controller';
import { FixtureTransport } from '../../api/fixtures';
import {
  createFakePlatform,
  type FakePlatformScript,
} from '../../platform/fake';
import { RuntimeContext } from '../../runtime';
import { OverlayProvider } from '../../ui/overlays';
import Workspace from './Workspace';

// Each test renders the whole Workspace and uploads through the controller;
// a loaded machine (the full client suite in parallel) needs more than the
// default 5 s.
const HEAVY = 20_000;

// JSDOM has no measured panes; the shell, navigation and composer stay real.
vi.mock('react-resizable-panels', () => ({
  Group: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Panel: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Separator: () => <div />,
  usePanelRef: () => ({ current: null }),
}));

/** A recorded workspace, so the composer shows its controls. */
class ComposerTransport extends FixtureTransport {
  async workspace(id: string) {
    const template = wire.validateWire<wire.ConversationWorkspace>(
      'ConversationWorkspace',
      structuredClone(
        recording.records.find(
          (item) => item.schema === 'ConversationWorkspace',
        )!.value,
      ),
    );
    return { ...template, conversation_id: id };
  }
  // The composer's own read answers too, so its failure never replaces the
  // message a test checks.
  async composer(id: string) {
    return (await this.workspace(id)).composer!;
  }
}

const clients: ClientController[] = [];
afterEach(() => {
  clients.splice(0).forEach((controller) => controller.dispose());
  vi.unstubAllGlobals();
});

async function attachWith(
  selectFile: FakePlatformScript['selectFile'],
  prepare?: (controller: ClientController) => void,
) {
  vi.stubGlobal('innerWidth', 1440);
  vi.stubGlobal('innerHeight', 900);
  const controller = new ClientController(
    new ComposerTransport({ conversationCount: 2 }),
    () => 1,
  );
  clients.push(controller);
  await controller.start();
  await controller.selectConversation('conversation-a');
  prepare?.(controller);
  render(
    <MemoryRouter initialEntries={['/conversations/conversation-a']}>
      <RuntimeContext.Provider
        value={{ controller, platform: createFakePlatform({ selectFile }) }}
      >
        <OverlayProvider>
          <Workspace />
        </OverlayProvider>
      </RuntimeContext.Provider>
    </MemoryRouter>,
  );
  const more = await screen.findByRole('button', {
    name: 'Add files and more',
  });
  await waitFor(() => expect(more).toBeEnabled());
  await act(async () => fireEvent.keyDown(more, { key: 'Enter' }));
  await act(async () =>
    fireEvent.click(
      within(screen.getByRole('menu')).getByRole('menuitem', {
        name: 'Attach file',
      }),
    ),
  );
  return controller;
}

it(
  'says when Attach file fails instead of doing nothing (B231)',
  async () => {
    await attachWith({
      status: 'unavailable',
      reason: 'native_operation_unavailable',
    });
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Row-Bot couldn’t attach that file. Try again, or drag it into the conversation.',
    );
  },
  HEAVY,
);

it(
  'attaches what the browser file input picked, as a drop does (B231)',
  async () => {
    const file = new File(['notes'], 'notes.txt', { type: 'text/plain' });
    const controller = await attachWith({
      status: 'ok',
      value: { kind: 'file', files: [file] },
    });
    // The upload runs through the controller; a busy machine can need more
    // than a second.
    await waitFor(
      () =>
        expect(controller.getDraft('conversation-a').attachments).toHaveLength(
          1,
        ),
      { timeout: 15_000 },
    );
    expect(screen.queryByRole('alert')).toBeNull();
  },
  HEAVY,
);

it(
  'files dropped while another uploads attach too (B281)',
  async () => {
    let release = () => {};
    const gate = new Promise<void>((resolve) => (release = resolve));
    const controller = await attachWith(
      {
        status: 'ok',
        value: {
          kind: 'file',
          files: [new File(['notes'], 'notes.txt', { type: 'text/plain' })],
        },
      },
      (value) => {
        const upload = value.upload.bind(value);
        let calls = 0;
        // The picked file's upload is still running when the others drop.
        vi.spyOn(value, 'upload').mockImplementation(async (...args) => {
          if (calls++ === 0) await gate;
          return upload(...args);
        });
      },
    );
    const field = document.querySelector('.composer-field')!;
    await waitFor(() => expect(value(controller)).toBe(1));
    fireEvent.drop(field, {
      dataTransfer: {
        types: ['Files'],
        files: [
          new File(['first'], 'notes-a.txt', { type: 'text/plain' }),
          new File(['second'], 'notes-b.txt', { type: 'text/plain' }),
        ],
      },
    });
    release();
    await waitFor(
      () =>
        expect(controller.getDraft('conversation-a').attachments).toHaveLength(
          3,
        ),
      { timeout: 15_000 },
    );
  },
  HEAVY,
);

/** How many uploads the controller has started. */
function value(controller: ClientController) {
  return vi.mocked(controller.upload).mock.calls.length;
}
