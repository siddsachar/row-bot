import { act, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import type { ReactNode } from 'react';
import * as wire from '../../../../contracts/client-platform/v1/typescript/client';
import thinkingRecording from '../../../../contracts/client-platform/v1/fixtures/F-P12.json';
import { ClientController } from '../../api/controller';
import { FixtureTransport } from '../../api/fixtures';
import { createFakePlatform } from '../../platform/fake';
import { RuntimeContext } from '../../runtime';
import { OverlayProvider } from '../../ui/overlays';
import Workspace from './Workspace';

// JSDOM has no measured panes; the shell, navigation and header stay real.
vi.mock('react-resizable-panels', () => ({
  Group: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Panel: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Separator: () => <div />,
  usePanelRef: () => ({ current: null }),
}));

class NamingTransport extends FixtureTransport {
  async workspace(id: string) {
    const template = wire.validateWire<wire.ConversationWorkspace>(
      'ConversationWorkspace',
      structuredClone(
        thinkingRecording.records.find(
          (item) => item.schema === 'ConversationWorkspace',
        )!.value,
      ),
    );
    return { ...template, conversation_id: id };
  }
}

const clients: ClientController[] = [];
afterEach(() => {
  clients.splice(0).forEach((controller) => controller.dispose());
  vi.unstubAllGlobals();
  localStorage.removeItem('row-bot.sidebar-type.v1');
});

/** What the server publishes when a conversation changes outside a turn. */
function conversationChanged(controller: ClientController): wire.EventRecord {
  const current = controller.getSnapshot().projection!;
  const revision = String(BigInt(current.projection_revision) + 1n);
  return {
    cursor: `named-${revision}`,
    event: wire.validateWire<wire.Event>('Event', {
      protocol_version: '1.0',
      event_id: `named-${revision}`,
      topic: `conversation.${current.conversation_id}`,
      server_epoch: current.server_epoch,
      conversation_id: current.conversation_id,
      projection_revision: revision,
      source: 'checkpoint',
      source_stream_id: current.conversation_id,
      source_epoch: current.server_epoch,
      source_sequence_start: revision,
      source_sequence_end: revision,
      type: 'transcript.checkpoint',
      payload: { checkpoint_revision: current.checkpoint_revision },
    }),
  };
}

// The fixture's conversations are Code ones: the sidebar's Code filter lists
// them from its own server listing (B239).
it.each(['all', 'code'])(
  'shows the name a conversation got after its first reply in the sidebar (%s) and the header (B230)',
  async (type) => {
    localStorage.setItem('row-bot.sidebar-type.v1', type);
    vi.stubGlobal('innerWidth', 1440);
    vi.stubGlobal('innerHeight', 900);
    const transport = new NamingTransport({ conversationCount: 2 });
    const controller = new ClientController(transport, () => 1);
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
    await waitFor(() =>
      expect(
        screen.getByRole('heading', {
          level: 1,
          name: 'A place for your ideas',
        }),
      ).toBeVisible(),
    );
    expect(
      screen.getByRole('button', { name: 'A place for your ideas' }),
    ).toBeVisible();
    await waitFor(() => expect(transport.counters.subscribes).toBe(1));

    // The server renamed it: later reads return a new row.
    transport.conversations[0] = {
      ...transport.conversations[0],
      title: 'Cornwall Coast Walking Trip',
    };
    await act(async () => transport.emit(conversationChanged(controller)));

    await waitFor(() =>
      expect(
        screen.getByRole('heading', {
          level: 1,
          name: 'Cornwall Coast Walking Trip',
        }),
      ).toBeVisible(),
    );
    expect(
      screen.getByRole('button', { name: 'Cornwall Coast Walking Trip' }),
    ).toBeVisible();
    expect(
      screen.queryByRole('button', { name: 'A place for your ideas' }),
    ).toBeNull();
  },
);
