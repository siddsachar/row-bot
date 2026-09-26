import { afterEach, expect, it, vi } from 'vitest';
import {
  sendConversationCommand,
  type Command,
  type SessionProof,
} from '../../../contracts/client-platform/v1/typescript/client';

afterEach(() => {
  vi.unstubAllGlobals();
});

const REVISION = 'a'.repeat(64);

function transport() {
  const proof: SessionProof = {
    client_session_id: crypto.randomUUID(),
    csrf_token: 'synthetic'.repeat(8),
  };
  const fetcher = vi.fn(async (_url: string, init: RequestInit) => ({
    ok: true,
    json: async () => ({
      command_id: JSON.parse(String(init.body)).command_id,
      status: 'accepted',
    }),
  }));
  vi.stubGlobal('fetch', fetcher);
  const command = (type: string, payload: object) =>
    ({
      command_id: crypto.randomUUID(),
      client_session_id: proof.client_session_id,
      expected_revision: '0',
      type,
      payload,
    }) as Command;
  return { proof, fetcher, command };
}

it('sends every saved-workflow command to the task route', async () => {
  const { proof, fetcher, command } = transport();
  const commands = [
    command('task.delete', { task_id: 'task-a', task_revision: REVISION }),
    command('task.delivery.update', {
      delivery_revision: REVISION,
      channels: ['app'],
    }),
    command('task.run', {
      task_id: 'task-a',
      task_revision: REVISION,
      policy_revision: REVISION,
    }),
  ];
  for (const item of commands)
    await sendConversationCommand('', null, item, proof, item.command_id);
  expect(
    fetcher.mock.calls.map(([url]) => new URL(url, 'http://h').pathname),
  ).toEqual([
    '/api/v1/tasks/commands',
    '/api/v1/tasks/commands',
    '/api/v1/tasks/commands',
  ]);
});

it('keeps conversation commands on the conversation route', async () => {
  const { proof, fetcher, command } = transport();
  const rename = command('conversation.rename', { title: 'Captured title' });
  await sendConversationCommand(
    '',
    'conversation-a',
    rename,
    proof,
    rename.command_id,
  );
  expect(new URL(fetcher.mock.calls[0][0], 'http://h').pathname).toBe(
    '/api/v1/conversations/conversation-a/commands',
  );
});
