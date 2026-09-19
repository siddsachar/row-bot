import { afterEach, expect, it, vi } from 'vitest';
import { ClientController } from './controller';
import { FixtureTransport } from './fixtures';
import type {
  DictationResult,
  DictationSnapshot,
  DictationHandle,
} from './types';

const clients: ClientController[] = [];
afterEach(() => {
  clients.splice(0).forEach((client) => client.dispose());
});

async function setup() {
  const transport = new FixtureTransport();
  const client = new ClientController(transport);
  clients.push(client);
  client.setVisible(false);
  await client.start();
  await client.selectConversation('conversation-a');
  const scope = client.dictationScope()!;
  const capture: DictationSnapshot = {
    schema_version: 1,
    state: 'capturing',
    quiesced: true,
    expires_in_ms: 120000,
    max_audio_bytes: 8388608,
    max_utterance_ms: 30000,
    handle: {
      lease_id: '11111111-1111-4111-8111-111111111111',
      voice_session_id: 1,
      conversation_id: scope.conversationId,
      server_epoch: scope.serverEpoch,
    },
  };
  const start = vi.fn(async () => capture);
  const stop = vi.fn(async (handle: DictationHandle) => ({
    ...capture,
    handle,
    state: 'stopped' as const,
  }));
  Object.assign(transport, { startDictation: start, stopDictation: stop });
  await client.startDictation(
    scope,
    '22222222-2222-4222-8222-222222222222',
    new AbortController().signal,
  );
  const result: DictationResult = {
    snapshot: { ...capture, state: 'completed' },
    utterance_id: '33333333-3333-4333-8333-333333333333',
    outcome: 'transcribed',
    text: 'spoken addition',
  };
  return { client, transport, scope, capture, result, start, stop };
}

it('appends once to the current unsent draft, preserving intervening edits and attachments', async () => {
  const { client, scope, result, transport } = await setup();
  const attachments = [
    {
      attachment_ref: 'fixture:file',
      name: 'fixture.png',
      mime_type: 'image/png' as const,
      size_bytes: 1,
      revision: '1',
    },
  ];
  client.setDraft(scope.conversationId, {
    text: 'Typed while recording',
    attachments,
  });
  expect(client.applyDictation(scope, result)).toBe('applied');
  expect(client.getDraft(scope.conversationId)).toEqual({
    text: 'Typed while recording spoken addition',
    attachments,
  });
  expect(client.applyDictation(scope, result)).toBe('applied');
  expect(client.getDraft(scope.conversationId).text).toBe(
    'Typed while recording spoken addition',
  );
  expect(transport.counters.commands).toBe(0);
});

it('preserves a full draft and permits applying the retained transcript after space is made', async () => {
  const { client, scope, result } = await setup();
  client.setDraft(scope.conversationId, {
    text: 'x'.repeat(200000),
    attachments: [],
  });
  expect(client.applyDictation(scope, result)).toBe('draft_full');
  expect(client.getDraft(scope.conversationId).text).toHaveLength(200000);
  client.setDraft(scope.conversationId, {
    text: 'Shorter draft ',
    attachments: [],
  });
  expect(client.applyDictation(scope, result)).toBe('applied');
  expect(client.getDraft(scope.conversationId).text).toBe(
    'Shorter draft spoken addition',
  );
});

it('rejects a late A-to-B-to-A result while permitting exact old-lease cancellation', async () => {
  const { client, scope, result, stop } = await setup();
  await client.selectConversation('conversation-2');
  await client.selectConversation('conversation-a');
  expect(client.dictationScope()?.selectionKey).not.toBe(scope.selectionKey);
  expect(client.applyDictation(scope, result)).toBe('stale');
  expect(client.getDraft(scope.conversationId).text).toBe('');
  await client.stopDictation(scope, result.snapshot.handle);
  expect(stop).toHaveBeenCalledOnce();
});

it('rejects changed authentication/epoch and incomplete or foreign results', async () => {
  const { client, scope, result } = await setup();
  expect(
    client.applyDictation({ ...scope, clientSessionId: 'other' }, result),
  ).toBe('stale');
  expect(
    client.applyDictation({ ...scope, serverEpoch: 'other' }, result),
  ).toBe('stale');
  expect(
    client.applyDictation(scope, {
      ...result,
      snapshot: { ...result.snapshot, quiesced: false },
    }),
  ).toBe('stale');
  expect(
    client.applyDictation(scope, {
      ...result,
      snapshot: {
        ...result.snapshot,
        handle: {
          ...result.snapshot.handle,
          lease_id: '44444444-4444-4444-8444-444444444444',
        },
      },
    }),
  ).toBe('stale');
  await expect(
    client.stopDictation(
      { ...scope, clientSessionId: 'other' },
      result.snapshot.handle,
    ),
  ).rejects.toMatchObject({ code: 'voice_session_expired' });
});

it('does not duplicate an already applied utterance when Start is replayed for the same lease', async () => {
  const { client, scope, result } = await setup();
  expect(client.applyDictation(scope, result)).toBe('applied');
  await client.startDictation(
    scope,
    '22222222-2222-4222-8222-222222222222',
    new AbortController().signal,
  );
  expect(client.applyDictation(scope, result)).toBe('applied');
  expect(client.getDraft(scope.conversationId).text).toBe('spoken addition');
});

it('validates transcript identity before allowing application', async () => {
  const { client, transport, scope, result } = await setup();
  Object.assign(transport, {
    transcribeDictation: async () => ({
      ...result,
      utterance_id: '44444444-4444-4444-8444-444444444444',
    }),
  });
  await expect(
    client.transcribeDictation(
      scope,
      result.snapshot.handle,
      result.utterance_id,
      new Blob(['fake']),
      new AbortController().signal,
    ),
  ).rejects.toMatchObject({ code: 'protocol_incompatible' });
  expect(client.getDraft(scope.conversationId).text).toBe('');
});
