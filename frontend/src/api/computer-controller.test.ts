import { afterEach, expect, it, vi } from 'vitest';
import {
  getComputerUse,
  getComputerUsePreview,
  sendComputerUseCommand,
  type ComputerUseCommand,
  type ComputerUseReceipt,
  type SessionProof,
} from '../../../contracts/client-platform/v1/typescript/client';
import { ClientController } from './controller';
import { FixtureTransport } from './fixtures';

const REVISION = 'a'.repeat(64);

class ComputerTransport extends FixtureTransport {
  readonly sent: Array<{ conversation: string; command: ComputerUseCommand }> =
    [];
  answer: (command: ComputerUseCommand) => Partial<ComputerUseReceipt> =
    () => ({});

  async sendComputerUse(
    conversation: string,
    command: ComputerUseCommand,
  ): Promise<ComputerUseReceipt> {
    this.sent.push({ conversation, command });
    return {
      schema_version: 1,
      command_id: command.command_id,
      action: command.type,
      conversation_id: conversation,
      status: 'completed',
      code: null,
      computer_use: null,
      ...this.answer(command),
    };
  }
}

const clients: ClientController[] = [];
afterEach(() => {
  clients.splice(0).forEach((client) => client.dispose());
  vi.unstubAllGlobals();
});

it('sends Stop, Pause and Resume as one exact command from this session', async () => {
  const transport = new ComputerTransport();
  const client = new ClientController(transport);
  clients.push(client);
  await client.start();
  const session = client.getSnapshot().handshake!.client_session_id;

  const stopped = await client.computerUseCommand(
    'conversation-a',
    'computer_use.stop',
  );
  expect(stopped.status).toBe('completed');
  const [{ conversation, command }] = transport.sent;
  expect(conversation).toBe('conversation-a');
  expect(command).toEqual({
    command_id: expect.stringMatching(/^[0-9a-f-]{36}$/),
    client_session_id: session,
    type: 'computer_use.stop',
  });

  // Checking an earlier command again reuses its id.
  await client.computerUseCommand(
    'conversation-a',
    'computer_use.pause',
    command.command_id,
  );
  expect(transport.sent[1].command.command_id).toBe(command.command_id);

  // An answer about some other command is never taken as this one's.
  transport.answer = () => ({ command_id: crypto.randomUUID() });
  await expect(
    client.computerUseCommand('conversation-a', 'computer_use.resume'),
  ).rejects.toMatchObject({ code: 'protocol_incompatible' });
});

it('reads the card and its picture, and posts commands, on the computer routes', async () => {
  const proof: SessionProof = {
    client_session_id: crypto.randomUUID(),
    csrf_token: 'synthetic'.repeat(8),
  };
  const replies = [
    {
      schema_version: 1,
      conversation_id: 'conversation-a',
      revision: REVISION,
      active: false,
      state: 'stopped',
      app: '',
      has_picture: false,
      approval_id: null,
      can_pause: false,
      can_resume: false,
      can_stop: false,
    },
    {
      schema_version: 1,
      conversation_id: 'conversation-a',
      revision: REVISION,
      state: 'inactive',
      mime_type: null,
      image_base64: null,
    },
  ];
  const fetcher = vi.fn(async (_url: string, init: RequestInit) => ({
    ok: true,
    json: async () => {
      if (init.method === 'POST') {
        const sent = JSON.parse(String(init.body));
        return {
          schema_version: 1,
          command_id: sent.command_id,
          action: sent.type,
          conversation_id: 'conversation-a',
          status: 'completed',
          code: null,
          computer_use: null,
        };
      }
      return replies.shift();
    },
  }));
  vi.stubGlobal('fetch', fetcher);

  await getComputerUse('', proof, 'conversation-a');
  await getComputerUsePreview('', proof, 'conversation-a', REVISION);
  const command: ComputerUseCommand = {
    command_id: crypto.randomUUID(),
    client_session_id: proof.client_session_id,
    type: 'computer_use.stop',
  };
  await sendComputerUseCommand('', proof, 'conversation-a', command);

  const calls = fetcher.mock.calls.map(([url, init]) => ({
    url: new URL(url, 'http://h'),
    init,
  }));
  expect(calls.map(({ url }) => url.pathname)).toEqual([
    '/api/v1/conversations/conversation-a/computer',
    '/api/v1/conversations/conversation-a/computer/preview',
    '/api/v1/conversations/conversation-a/computer/commands',
  ]);
  expect(calls[1].url.searchParams.get('revision')).toBe(REVISION);
  expect(calls.every(({ init }) => init.cache === 'no-store')).toBe(true);
  expect(
    (calls[2].init.headers as Record<string, string>)['Idempotency-Key'],
  ).toBe(command.command_id);
});
