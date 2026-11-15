import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { AnswerStateChange, TranscriptRow } from '../../../api/types';
import { registerPromptSender } from '../composer-bridge';
import { TranscriptBlocks } from '../TranscriptBlocks';

type View = {
  conversation_id: string;
  revision: string;
  ticks: Record<string, Record<string, boolean>>;
  choices: Record<string, string>;
};

const server = vi.hoisted(() => ({
  views: new Map<string, View>(),
  saves: [] as AnswerStateChange[],
  failNext: '' as string,
}));

vi.mock('../../../runtime', () => {
  const controller = {
    answerState: async (conversation: string) =>
      structuredClone(
        server.views.get(conversation) ?? {
          conversation_id: conversation,
          revision: 'r0',
          ticks: {},
          choices: {},
        },
      ),
    saveAnswerState: async (
      conversation: string,
      change: AnswerStateChange,
    ) => {
      server.saves.push(change);
      const view = server.views.get(conversation)!;
      if (server.failNext) {
        const code = server.failNext;
        server.failNext = '';
        throw { code };
      }
      if (change.expected_revision !== view.revision)
        throw { code: 'answer_state_revision_conflict' };
      if (change.choice) view.choices[change.block_id] = change.choice;
      else
        view.ticks[change.block_id] = {
          ...view.ticks[change.block_id],
          [change.item_id!]: change.done!,
        };
      view.revision = `r${server.saves.length}`;
      return structuredClone(view);
    },
  };
  return {
    useOptionalRuntime: () => ({ controller }),
    useRuntime: () => ({ controller }),
  };
});

let unregister: (() => void) | undefined;
afterEach(() => unregister?.());

const LIST = `block:${'a'.repeat(24)}`;
const CHOICES = `block:${'b'.repeat(24)}`;

function checklist(conversation: string) {
  const block: TranscriptRow['blocks'][number] = {
    id: LIST,
    type: 'checklist',
    title: 'Before Friday',
    items: [
      { id: 'room', text: 'Book the room', done: false },
      { id: 'agenda', text: 'Send the agenda', done: false },
    ],
    actions: [],
    text: '',
  };
  return render(
    <TranscriptBlocks blocks={[block]} conversationId={conversation} />,
  );
}

it('shows the ticks kept for the chat and saves a new one against the revision read', async () => {
  server.views.set('chat-1', {
    conversation_id: 'chat-1',
    revision: 'r7',
    ticks: { [LIST]: { room: true } },
    choices: {},
  });
  checklist('chat-1');
  const room = screen.getByRole('checkbox', { name: 'Book the room' });
  await waitFor(() => expect(room).toBeChecked());
  await act(async () =>
    fireEvent.click(screen.getByRole('checkbox', { name: 'Send the agenda' })),
  );
  expect(server.saves.at(-1)).toEqual({
    block_id: LIST,
    item_id: 'agenda',
    done: true,
    expected_revision: 'r7',
  });
  expect(screen.getByText('2 of 2 done')).toBeVisible();
});

it('applies a tick again when another device moved the state on', async () => {
  server.views.set('chat-2', {
    conversation_id: 'chat-2',
    revision: 'r1',
    ticks: {},
    choices: {},
  });
  checklist('chat-2');
  await waitFor(() => expect(server.views.get('chat-2')).toBeDefined());
  await act(async () => {});
  // The other device ticked the room meanwhile.
  server.views.set('chat-2', {
    conversation_id: 'chat-2',
    revision: 'r2',
    ticks: { [LIST]: { room: true } },
    choices: {},
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('checkbox', { name: 'Send the agenda' })),
  );
  expect(server.views.get('chat-2')!.ticks[LIST]).toEqual({
    room: true,
    agenda: true,
  });
  expect(screen.getByRole('checkbox', { name: 'Book the room' })).toBeChecked();
  expect(
    screen.getByRole('checkbox', { name: 'Send the agenda' }),
  ).toBeChecked();
});

it('takes a tick back when it could not be saved', async () => {
  server.views.set('chat-3', {
    conversation_id: 'chat-3',
    revision: 'r1',
    ticks: {},
    choices: {},
  });
  checklist('chat-3');
  await act(async () => {});
  server.failNext = 'answer_state_full';
  const agenda = screen.getByRole('checkbox', { name: 'Send the agenda' });
  await act(async () => fireEvent.click(agenda));
  expect(agenda).not.toBeChecked();
});

it('keeps a sent choice, and an older answer’s buttons read Sent from the kept state', async () => {
  server.views.set('chat-4', {
    conversation_id: 'chat-4',
    revision: 'r1',
    ticks: {},
    choices: {},
  });
  const sent = vi.fn(() => true);
  unregister = registerPromptSender('chat-4', sent);
  const block: TranscriptRow['blocks'][number] = {
    id: CHOICES,
    type: 'choices',
    prompt: 'Which one?',
    options: [
      { label: 'Air', reply: 'Air' },
      { label: 'Pro', reply: 'Pro' },
    ],
    text: '',
  };
  const { unmount } = render(
    <TranscriptBlocks blocks={[block]} conversationId="chat-4" />,
  );
  await act(async () => {});
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Pro' })),
  );
  expect(sent).toHaveBeenCalledExactlyOnceWith('Pro');
  expect(server.views.get('chat-4')!.choices).toEqual({ [CHOICES]: 'Pro' });
  unmount();

  // Another device opens the chat: the buttons show what was sent.
  server.views.set('chat-5', {
    ...server.views.get('chat-4')!,
    conversation_id: 'chat-5',
  });
  render(<TranscriptBlocks blocks={[block]} conversationId="chat-5" />);
  const pro = await screen.findByRole('button', { name: /Pro.*Sent/ });
  expect(pro).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Air' })).toBeDisabled();
});

it('keeps a Markdown task list’s ticks for the chat', async () => {
  server.views.set('chat-6', {
    conversation_id: 'chat-6',
    revision: 'r1',
    ticks: { [LIST]: { 'task-1': true } },
    choices: {},
  });
  render(
    <TranscriptBlocks
      conversationId="chat-6"
      blocks={[
        { id: LIST, type: 'markdown', text: '- [ ] Pack\n- [ ] Charge' },
      ]}
    />,
  );
  const charge = screen.getByRole('checkbox', { name: 'Charge' });
  await waitFor(() => expect(charge).toBeChecked());
  await act(async () =>
    fireEvent.click(screen.getByRole('checkbox', { name: 'Pack' })),
  );
  expect(server.saves.at(-1)).toMatchObject({
    block_id: LIST,
    item_id: 'task-0',
    done: true,
  });
});
