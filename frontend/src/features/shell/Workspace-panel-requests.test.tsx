import { act, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import type { ReactNode } from 'react';
import * as wire from '../../../../contracts/client-platform/v1/typescript/client';
import recording from '../../../../contracts/client-platform/v1/fixtures/F-P12.json';
import { ClientController } from '../../api/controller';
import { FixtureTransport } from '../../api/fixtures';
import { createFakePlatform } from '../../platform/fake';
import { RuntimeContext } from '../../runtime';
import { OverlayProvider } from '../../ui/overlays';
import { requestResourcePanel } from '../panels/panel-requests';
import Workspace from './Workspace';

// The whole Workspace renders; a loaded machine needs more than 5 s.
const HEAVY = 20_000;

vi.mock('react-resizable-panels', () => ({
  Group: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Panel: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Separator: () => <div />,
  usePanelRef: () => ({ current: null }),
}));

/** A recorded workspace with one design in Working on. */
class DesignTransport extends FixtureTransport {
  async workspace(id: string) {
    const template = wire.validateWire<wire.ConversationWorkspace>(
      'ConversationWorkspace',
      structuredClone(
        recording.records.find(
          (item) => item.schema === 'ConversationWorkspace',
        )!.value,
      ),
    );
    return {
      ...template,
      conversation_id: id,
      resources: [
        {
          resource_ref: `${id}:binding-design`,
          conversation_revision: template.revision,
          binding: {
            binding_id: 'binding-design',
            kind: 'artifact' as const,
            resource_id: 'design-qa',
            role: 'context' as const,
            revision: '1',
          },
          title: 'QA test deck',
          resource_revision: 'design-revision',
          available: true,
        },
      ],
    };
  }
  async composer(id: string) {
    return (await this.workspace(id)).composer!;
  }
}

const clients: ClientController[] = [];
afterEach(() => {
  clients.splice(0).forEach((controller) => controller.dispose());
  vi.unstubAllGlobals();
});

it(
  'closes the panels of a design its panel deleted, and only in that conversation',
  async () => {
    vi.stubGlobal('innerWidth', 1440);
    vi.stubGlobal('innerHeight', 900);
    const controller = new ClientController(
      new DesignTransport({ conversationCount: 2 }),
      () => 1,
    );
    clients.push(controller);
    await controller.start();
    await controller.selectConversation('conversation-a');
    render(
      <MemoryRouter initialEntries={['/conversations/conversation-a']}>
        <RuntimeContext.Provider
          value={{ controller, platform: createFakePlatform({}) }}
        >
          <OverlayProvider>
            <Workspace />
          </OverlayProvider>
        </RuntimeContext.Provider>
      </MemoryRouter>,
    );
    await screen.findByRole('button', { name: 'Add files and more' });
    const reference = 'conversation-a:binding-design';
    act(() => {
      requestResourcePanel({
        conversationId: 'conversation-a',
        resourceRef: reference,
      });
    });
    await waitFor(() =>
      expect(screen.queryAllByTitle('QA test deck').length).toBeGreaterThan(0),
    );
    // Another conversation's request changes nothing here.
    act(() => {
      requestResourcePanel({
        conversationId: 'conversation-b',
        resourceRef: 'conversation-b:binding-design',
        close: true,
      });
    });
    expect(screen.queryAllByTitle('QA test deck').length).toBeGreaterThan(0);
    act(() => {
      requestResourcePanel({
        conversationId: 'conversation-a',
        resourceRef: reference,
        close: true,
      });
    });
    await waitFor(() =>
      expect(screen.queryAllByTitle('QA test deck')).toHaveLength(0),
    );
  },
  HEAVY,
);
