import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type {
  LearningCommand,
  LearningConversation,
  LearningReceipt,
  LearningSnapshot,
} from '../../api/types';
import { RuntimeContext } from '../../runtime';
import { OverlayProvider } from '../../ui/overlays';
import { Menu } from '../../ui/primitives';
import {
  TeachContext,
  useChatTeach,
  useLearningChatMenu,
  useTeach,
} from './chat-learning';
import { LEARNING_CAPABILITY } from './learning-state';
import { learningSnapshot } from './test-data';

function controllerFor(
  snapshot: LearningSnapshot,
  options: {
    preview?: boolean;
    conversation?: LearningConversation['state'];
  } = {},
) {
  let current = snapshot;
  const state = {
    status: 'ready',
    handshake: {
      instance_id: 'instance',
      client_session_id: 'session',
      application_capabilities:
        options.preview === false ? [] : [LEARNING_CAPABILITY],
    },
  };
  const executeLearning = vi.fn(
    async (
      command: Omit<LearningCommand, 'client_session_id'>,
    ): Promise<LearningReceipt> => {
      if (command.action === 'opt_out')
        current = {
          ...current,
          opted_out_conversations: [command.conversation_id ?? ''],
        };
      if (command.action === 'opt_in')
        current = { ...current, opted_out_conversations: [] };
      return {
        command_id: command.command_id,
        status: 'completed',
        summary:
          command.action === 'opt_out'
            ? 'Row-Bot won’t learn from this chat. What it recorded from it is deleted.'
            : command.action === 'opt_in'
              ? 'Row-Bot can learn from this chat again.'
              : 'Thanks. Row-Bot will learn from this.',
        snapshot: current,
        signal_id: command.action === 'teach' ? 'sig_42' : null,
      };
    },
  );
  const controller = {
    subscribe: () => () => {},
    getSnapshot: () => state,
    learning: vi.fn(async () => current),
    executeLearning,
    learningConversation: vi.fn(async (id: string) => ({
      conversation_id: id,
      state: options.conversation ?? 'learning',
      reason: '',
    })),
    learningHardware: vi.fn(),
    learningLessons: vi.fn(),
  } as unknown as ClientController;
  return { controller, executeLearning };
}

function Reply() {
  const teach = useTeach();
  return teach ? (
    <button type="button" onClick={() => teach({ messageId: 'reply-1' })}>
      Teach Row-Bot
    </button>
  ) : null;
}

function Chat({ id }: { id: string }) {
  const chat = useChatTeach(id);
  const item = useLearningChatMenu()({ id, title: 'Budget chat' });
  return (
    <TeachContext.Provider value={chat.teach}>
      <Reply />
      {item && <Menu label="Chat menu" actions={[item]} />}
      {chat.sheet}
    </TeachContext.Provider>
  );
}

function show(controller: ClientController) {
  return render(
    <RuntimeContext.Provider
      value={
        { controller } as unknown as React.ContextType<typeof RuntimeContext>
      }
    >
      <OverlayProvider>
        <Chat id="chat-a" />
      </OverlayProvider>
    </RuntimeContext.Provider>,
  );
}

it('Teach saves what went wrong, thanks you, and Undo removes it', async () => {
  const { controller, executeLearning } = controllerFor(
    learningSnapshot({ state: 'on' }),
  );
  show(controller);
  const user = userEvent.setup();

  await user.click(
    await screen.findByRole('button', { name: 'Teach Row-Bot' }),
  );
  const sheet = await screen.findByRole('dialog', { name: 'Teach Row-Bot' });
  expect(sheet).toHaveAccessibleDescription(
    'Say what went wrong. Row-Bot learns from it later; nothing changes right away.',
  );
  const send = within(sheet).getByRole('button', { name: 'Send' });
  expect(send).toBeDisabled();
  expect(
    within(sheet)
      .getAllByRole('radio')
      .map((item) => item.textContent),
  ).toEqual([
    'Wrong answer',
    'Wrong tool or step',
    'Ignored my instructions',
    'Other',
  ]);
  await user.click(within(sheet).getByRole('radio', { name: 'Wrong answer' }));
  await user.type(
    within(sheet).getByRole('textbox', {
      name: 'What should it do next time?',
    }),
    'Use the approved budget',
  );
  await user.click(send);

  expect(
    await screen.findByText('Thanks. Row-Bot will learn from this.'),
  ).toBeVisible();
  expect(screen.queryByRole('dialog', { name: 'Teach Row-Bot' })).toBeNull();
  expect(executeLearning.mock.calls[0][0]).toMatchObject({
    action: 'teach',
    conversation_id: 'chat-a',
    message_id: 'reply-1',
    tool_call_id: '',
    category: 'wrong_answer',
    note: 'Use the approved budget',
  });
  // A notice's own button (Radix's toast wants pointer capture jsdom lacks).
  await act(async () =>
    fireEvent.click(await screen.findByRole('button', { name: 'Undo' })),
  );
  expect(executeLearning.mock.calls[1][0]).toMatchObject({
    action: 'undo_teach',
    signal_id: 'sig_42',
  });
  expect(
    await screen.findByText('Removed. Row-Bot won’t learn from it.'),
  ).toBeVisible();
});

it.each([
  ['a chat Learning does not use', { conversation: 'not_used' as const }],
  ['a chat switched off', { conversation: 'opted_out' as const }],
  ['a paused Learning', { conversation: 'paused' as const }],
])('there is no Teach in %s', async (_name, options) => {
  const { controller } = controllerFor(
    learningSnapshot({ state: 'on' }),
    options,
  );
  show(controller);
  await screen.findByRole('button', { name: 'Chat menu' });
  await act(async () => undefined);
  expect(screen.queryByRole('button', { name: 'Teach Row-Bot' })).toBeNull();
});

it('without Learning (no preview flag, or off) a chat shows nothing of it', async () => {
  const off = controllerFor(learningSnapshot({ state: 'on' }), {
    preview: false,
  });
  const { unmount } = show(off.controller);
  await act(async () => undefined);
  expect(screen.queryByRole('button', { name: 'Chat menu' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Teach Row-Bot' })).toBeNull();
  expect(off.controller.learning).not.toHaveBeenCalled();
  unmount();

  show(controllerFor(learningSnapshot({ state: 'off' })).controller);
  await act(async () => undefined);
  expect(screen.queryByRole('button', { name: 'Chat menu' })).toBeNull();
});

it('Don’t learn from this chat is a check item; ticking it confirms that it deletes what was recorded', async () => {
  const { controller, executeLearning } = controllerFor(
    learningSnapshot({ state: 'on' }),
  );
  show(controller);
  const user = userEvent.setup();

  await user.click(await screen.findByRole('button', { name: 'Chat menu' }));
  const item = await screen.findByRole('menuitemcheckbox', {
    name: 'Don’t learn from this chat',
  });
  expect(item).toHaveAttribute('aria-checked', 'false');
  await user.click(item);
  const confirm = await screen.findByRole('alertdialog', {
    name: 'Don’t learn from this chat?',
  });
  expect(confirm).toHaveTextContent(
    'This also deletes what Learning recorded from this chat.',
  );
  await user.click(
    within(confirm).getByRole('button', { name: 'Don’t learn from this chat' }),
  );
  expect(
    await screen.findByText(
      'Row-Bot won’t learn from this chat. What it recorded from it is deleted.',
    ),
  ).toBeVisible();
  expect(executeLearning.mock.calls[0][0]).toMatchObject({
    action: 'opt_out',
    conversation_id: 'chat-a',
  });

  // Ticked now; unticking switches it back on at once.
  await user.click(screen.getByRole('button', { name: 'Chat menu' }));
  const ticked = await screen.findByRole('menuitemcheckbox', {
    name: 'Don’t learn from this chat',
  });
  expect(ticked).toHaveAttribute('aria-checked', 'true');
  await user.click(ticked);
  expect(
    await screen.findByText('Row-Bot can learn from this chat again.'),
  ).toBeVisible();
  expect(executeLearning.mock.calls[1][0]).toMatchObject({
    action: 'opt_in',
    conversation_id: 'chat-a',
  });
});
