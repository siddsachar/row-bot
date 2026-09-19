import { afterEach, expect, it, vi } from 'vitest';
import {
  listConversations,
  getConversation,
  saveDraft,
  sendConversationCommand,
  cancelUpload,
  cancelSubscriptionProbe,
  reviewMcpRuntime,
  type ConversationRenameCommand,
  type SessionProof,
} from '../../../contracts/client-platform/v1/typescript/client';
import { FixtureTransport } from './fixtures';

function mutationFixture() {
  const proof: SessionProof = {
    client_session_id: crypto.randomUUID(),
    csrf_token: 'synthetic'.repeat(8),
  };
  const fetcher = vi.fn(
    async (
      url: string,
      init: RequestInit,
    ): Promise<{ ok: boolean; json: () => Promise<unknown> }> => {
      const body = init.body ? JSON.parse(String(init.body)) : {};
      return {
        ok: true,
        json: async () =>
          url.endsWith('/draft')
            ? {
                conversation_id: 'conversation-a',
                revision: '1',
                text: body.text,
                attachments: [],
              }
            : init.method === 'DELETE'
              ? { cancelled: true }
              : { command_id: body.command_id, status: 'accepted' },
      };
    },
  );
  vi.stubGlobal('fetch', fetcher);
  const command = (): ConversationRenameCommand => ({
    command_id: crypto.randomUUID(),
    client_session_id: proof.client_session_id,
    expected_revision: '1',
    type: 'conversation.rename',
    payload: { title: 'Captured title' },
  });
  return { proof, fetcher, command };
}

it('cancels the exact subscription probe immediately while ordinary writes wait', async () => {
  vi.useFakeTimers();
  const { proof, fetcher } = mutationFixture();
  await Promise.all(
    Array.from({ length: 8 }, () =>
      saveDraft('', proof, 'conversation-a', {
        expected_revision: '1',
        text: '',
        attachment_refs: [],
      }),
    ),
  );
  const waiting = saveDraft('', proof, 'conversation-a', {
    expected_revision: '1',
    text: 'waiting',
    attachment_refs: [],
  });
  const id = crypto.randomUUID();
  fetcher.mockImplementationOnce(async () => ({
    ok: true,
    json: async () => ({
      command_id: id,
      provider_id: 'codex',
      state: 'draining',
      quiescent: false,
      result: null,
    }),
  }));
  const cancelled = await cancelSubscriptionProbe('', proof, id);
  expect(cancelled.quiescent).toBe(false);
  expect(fetcher).toHaveBeenCalledTimes(9);
  expect(fetcher.mock.calls[8][0]).toContain(`/probes/${id}/cancel`);
  await vi.advanceTimersByTimeAsync(999);
  expect(fetcher).toHaveBeenCalledTimes(9);
  await vi.advanceTimersByTimeAsync(1);
  await waiting;
  expect(fetcher).toHaveBeenCalledTimes(10);
  expect(vi.getTimerCount()).toBe(0);
});

it('shares one bounded mutation budget across autosaves and commands while Stop, approval and cancel remain immediate', async () => {
  vi.useFakeTimers();
  const { proof, fetcher, command } = mutationFixture();
  const pending = Array.from({ length: 12 }, (_, index) =>
    index % 2
      ? sendConversationCommand(
          '',
          'conversation-a',
          command(),
          proof,
          crypto.randomUUID(),
        )
      : saveDraft('', proof, 'conversation-a', {
          expected_revision: '1',
          text: String(index),
          attachment_refs: [],
        }),
  );
  const settled = Promise.all(pending);
  for (let i = 0; i < 10; i++) await Promise.resolve();
  expect(fetcher).toHaveBeenCalledTimes(8);
  await sendConversationCommand(
    '',
    'conversation-a',
    { ...command(), type: 'conversation.stop', payload: {} },
    proof,
    crypto.randomUUID(),
  );
  await sendConversationCommand(
    '',
    'approval-a',
    {
      ...command(),
      type: 'approval.resolve',
      payload: { decision: 'approve', nonce: 'n'.repeat(32) },
    },
    proof,
    crypto.randomUUID(),
  );
  await cancelUpload('', proof, 'upload-a');
  expect(fetcher).toHaveBeenCalledTimes(11);
  await vi.advanceTimersByTimeAsync(999);
  expect(fetcher).toHaveBeenCalledTimes(11);
  await vi.advanceTimersByTimeAsync(3001);
  await settled;
  expect(fetcher).toHaveBeenCalledTimes(15);
  expect(vi.getTimerCount()).toBe(0);
});

it('keeps reviewed MCP disconnect immediate while ordinary writes are queued', async () => {
  vi.useFakeTimers();
  const { proof, fetcher } = mutationFixture();
  const draft = () =>
    saveDraft('', proof, 'conversation-a', {
      expected_revision: '1',
      text: '',
      attachment_refs: [],
    });
  await Promise.all(Array.from({ length: 8 }, draft));
  const queued = draft();
  const intent = {
    resource_revision: 'a'.repeat(64),
    server_id: 'b'.repeat(64),
    operation: 'disconnect' as const,
    expected_runtime_id: crypto.randomUUID(),
  };
  fetcher.mockImplementationOnce(async () => ({
    ok: true,
    json: async () => ({
      resource_revision: intent.resource_revision,
      server_id: intent.server_id,
      operation: intent.operation,
      runtime_id: intent.expected_runtime_id,
      action_digest: 'c'.repeat(64),
      nonce: 'n'.repeat(32),
    }),
  }));
  const reviewed = await reviewMcpRuntime('', proof, intent);
  const commandId = crypto.randomUUID();
  await sendConversationCommand(
    '',
    null,
    {
      command_id: commandId,
      client_session_id: proof.client_session_id,
      expected_revision: '0',
      type: 'mcp.runtime.control',
      payload: { ...intent, nonce: reviewed.nonce },
    },
    proof,
    commandId,
  );
  expect(fetcher).toHaveBeenCalledTimes(10);
  await vi.advanceTimersByTimeAsync(999);
  expect(fetcher).toHaveBeenCalledTimes(10);
  await vi.advanceTimersByTimeAsync(1);
  await queued;
  expect(fetcher).toHaveBeenCalledTimes(11);
  expect(vi.getTimerCount()).toBe(0);
});

it('captures mutation bytes before waiting and cancels queued writes without replay', async () => {
  vi.useFakeTimers();
  const { proof, fetcher, command } = mutationFixture();
  await Promise.all(
    Array.from({ length: 8 }, () =>
      saveDraft('', proof, 'conversation-a', {
        expected_revision: '1',
        text: '',
        attachment_refs: [],
      }),
    ),
  );
  const captured = command();
  const waiting = sendConversationCommand(
    '',
    'conversation-a',
    captured,
    proof,
    crypto.randomUUID(),
  );
  captured.payload.title = 'Later edit must not replace captured intent';
  const abort = new AbortController();
  const cancelled = sendConversationCommand(
    '',
    'conversation-a',
    command(),
    proof,
    crypto.randomUUID(),
    abort.signal,
  );
  const cancellation = expect(cancelled).rejects.toMatchObject({
    name: 'AbortError',
  });
  abort.abort();
  await vi.advanceTimersByTimeAsync(1000);
  await waiting;
  await cancellation;
  expect(fetcher).toHaveBeenCalledTimes(9);
  expect(
    JSON.parse(String(fetcher.mock.calls.at(-1)![1].body)).payload.title,
  ).toBe('Captured title');
  expect(vi.getTimerCount()).toBe(0);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
it('paces legitimate continuation below the server query budget and cancels waiting work', async () => {
  vi.useFakeTimers();
  const fetcher = vi.fn(async () => ({
    ok: true,
    json: async () => ({ items: [], has_more: false, next_cursor: null }),
  }));
  vi.stubGlobal('fetch', fetcher);
  const proof: SessionProof = {
    client_session_id: crypto.randomUUID(),
    csrf_token: 'synthetic'.repeat(8),
  };
  const abort = new AbortController();
  const requests = Array.from({ length: 25 }, (_, i) =>
    listConversations(
      '',
      proof,
      50,
      String(i),
      i === 24 ? abort.signal : undefined,
    ),
  );
  const results = Promise.allSettled(requests);
  for (let i = 0; i < 10; i++) await Promise.resolve();
  expect(fetcher).toHaveBeenCalledTimes(20);
  abort.abort();
  await vi.advanceTimersByTimeAsync(2000);
  const settled = await results;
  expect(fetcher).toHaveBeenCalledTimes(24);
  expect(
    settled.filter((result) => result.status === 'fulfilled'),
  ).toHaveLength(24);
  expect(settled[24]).toMatchObject({
    status: 'rejected',
    reason: { name: 'AbortError' },
  });
  expect(vi.getTimerCount()).toBe(0);
});

it('keeps a conversation view responsive while library continuation waits', async () => {
  vi.useFakeTimers();
  const conversation = await new FixtureTransport().getConversation(
    'conversation-a',
  );
  const fetcher = vi.fn(async (url: string) => ({
    ok: true,
    json: async () =>
      url.endsWith('/conversation-a')
        ? conversation
        : { items: [], has_more: false, next_cursor: null },
  }));
  vi.stubGlobal('fetch', fetcher);
  const proof: SessionProof = {
    client_session_id: crypto.randomUUID(),
    csrf_token: 'synthetic'.repeat(8),
  };
  const abort = new AbortController();
  const queries = Promise.allSettled(
    Array.from({ length: 25 }, () =>
      listConversations('', proof, 50, undefined, abort.signal),
    ),
  );
  const view = await getConversation('', proof, 'conversation-a');
  expect(view.id).toBe('conversation-a');
  expect(fetcher).toHaveBeenCalledTimes(21);
  abort.abort();
  await vi.runAllTimersAsync();
  await queries;
  expect(vi.getTimerCount()).toBe(0);
});
