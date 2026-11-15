import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { ConversationLinkView } from '../../api/types';
import type { ClientPlatform } from '../../platform';
import { RuntimeContext } from '../../runtime';
import { LinkedConversationCard } from './LinkedConversationCard';
import { cardOf } from './TranscriptCards';

const LINK = 'a'.repeat(32);

function setup(link: Partial<ConversationLinkView>) {
  const snapshot = { conversations: [], conversation: { revision: '7' } };
  const controller = {
    getSnapshot: () => snapshot,
    subscribe: () => () => undefined,
    conversationLinks: vi.fn(async () => ({
      schema_version: 1,
      items: [
        {
          link_id: LINK,
          title: 'Plan the launch',
          conversation_id: null,
          state: 'proposed',
          proposed: true,
          mode: 'fresh',
          ...link,
        },
      ],
    })),
    intent: vi.fn(async () => ({ linked_conversation_id: 'conversation-c' })),
  } as unknown as ClientController & {
    intent: ReturnType<typeof vi.fn>;
  };
  render(
    <MemoryRouter>
      <RuntimeContext.Provider
        value={{ controller, platform: {} as ClientPlatform }}
      >
        <LinkedConversationCard
          linkId={LINK}
          title="Plan the launch"
          conversationId="conversation-a"
        />
      </RuntimeContext.Provider>
    </MemoryRouter>,
  );
  return controller;
}

it('reads a started or proposed conversation from its tool step (5.3.1 C)', () => {
  expect(
    cardOf({
      kind: 'linked_conversation',
      link_id: LINK,
      display_name: 'Plan the launch',
    } as Parameters<typeof cardOf>[0]),
  ).toEqual({ kind: 'linked', linkId: LINK, title: 'Plan the launch' });
});

it('offers a proposal to start with one tap, and then opens it', async () => {
  const controller = setup({});
  const card = await screen.findByRole('group', {
    name: 'Plan the launch · proposed',
  });
  expect(card).toHaveTextContent('Plan the launch · proposed conversation');

  await userEvent.click(screen.getByRole('button', { name: 'Start' }));

  expect(controller.intent).toHaveBeenCalledWith(
    'conversation-a',
    'conversation.link.start',
    { link_id: LINK },
    '7',
  );
  expect(
    await screen.findByRole('group', { name: 'Plan the launch · working' }),
  ).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Open' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Start' })).toBeNull();
});

it('says not now plainly and offers nothing more', async () => {
  const controller = setup({});
  await userEvent.click(await screen.findByRole('button', { name: 'Not now' }));

  expect(controller.intent).toHaveBeenCalledWith(
    'conversation-a',
    'conversation.link.dismiss',
    { link_id: LINK },
    '7',
  );
  expect(
    await screen.findByRole('group', { name: 'Plan the launch · not started' }),
  ).toBeInTheDocument();
  expect(screen.queryByRole('button')).toBeNull();
});

it('shows a started conversation waiting for the person, with Open', async () => {
  setup({
    state: 'waiting',
    proposed: false,
    conversation_id: 'conversation-c',
  });
  expect(
    await screen.findByRole('group', {
      name: 'Plan the launch · waiting for you',
    }),
  ).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Open' })).toBeInTheDocument();
});
