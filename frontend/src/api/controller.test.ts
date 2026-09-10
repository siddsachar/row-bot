import { afterEach, describe, expect, it, vi } from 'vitest';
import { webcrypto } from 'node:crypto';
import { validateWire } from '../../../contracts/client-platform/v1/typescript/client';
import * as wire from '../../../contracts/client-platform/v1/typescript/client';
import { ClientController } from './controller';
import { ACK_RETIRE_TIMEOUT_MS } from './acknowledgements';
import { HttpTransport } from './http';
import {
  FixtureClock,
  FixtureTransport,
  recorded,
  recordings,
} from './fixtures';
import { clientError } from './errors';
import thinkingRecording from '../../../contracts/client-platform/v1/fixtures/F-P12.json';
import type {
  Command,
  ConversationView,
  Event,
  EventRecord,
  Snapshot,
  SubscriptionView,
  TranscriptPage,
  DraftSave,
  DraftView,
} from './types';

const clients: ClientController[] = [];
class CachedDraftTransport extends FixtureTransport {
  constructor() {
    super({ conversationCount: 48 });
  }
  reads = vi.fn(async (id: string): Promise<DraftView> => ({
    conversation_id: id,
    revision: '0',
    text: `Saved ${id}`,
    attachments: [],
  }));
  draft(id: string): Promise<DraftView> {
    return this.reads(id);
  }
  writer = (id: string, body: DraftSave): Promise<DraftView> =>
    Promise.resolve({
      conversation_id: id,
      revision: '1',
      text: body.text,
      attachments: [],
    });
  saveDraft(id: string, body: DraftSave): Promise<DraftView> {
    return this.writer(id, body);
  }
}
function client(transport = new FixtureTransport()) {
  const value = new ClientController(transport, () => 1);
  clients.push(value);
  return value;
}
async function flush() {
  for (let i = 0; i < 30; i++) await Promise.resolve();
}
afterEach(async () => {
  clients.splice(0).forEach((value) => value.dispose());
  await flush();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it('bounds clean draft content and metadata while reloading evicted drafts from their retained owner', async () => {
  const transport = new CachedDraftTransport(),
    value = client(transport);
  value.setVisible(false);
  await value.start();
  for (const conversation of transport.conversations)
    await value.selectConversation(conversation.id);
  const maps = value as unknown as {
    drafts: Map<string, unknown>;
    draftRevisions: Map<string, unknown>;
    draftStates: Map<string, unknown>;
  };
  expect(maps.drafts.size).toBeLessThanOrEqual(32);
  expect(maps.draftRevisions.size).toBeLessThanOrEqual(32);
  expect(maps.draftStates.size).toBeLessThanOrEqual(32);
  expect(value.getDraft('conversation-a').text).toBe('');
  expect(value.getDraft('conversation-48').text).toBe('Saved conversation-48');
  await value.selectConversation('conversation-a');
  expect(
    transport.reads.mock.calls.filter(([id]) => id === 'conversation-a'),
  ).toHaveLength(2);
  expect(value.getDraft('conversation-a').text).toBe('Saved conversation-a');
  expect(maps.drafts.size).toBeLessThanOrEqual(32);
});

it('preserves dirty, conflicted and in-flight drafts while reclaiming clean conversations', async () => {
  const transport = new CachedDraftTransport(),
    value = client(transport);
  let finish!: (result: DraftView) => void;
  transport.writer = async (id) => {
    if (id === 'conversation-a') throw { code: 'draft_revision_conflict' };
    if (id === 'conversation-2')
      return new Promise((resolve) => {
        finish = resolve;
      });
    throw new TypeError('Synthetic offline draft');
  };
  value.setVisible(false);
  await value.start();
  for (const id of ['conversation-a', 'conversation-2', 'conversation-3']) {
    await value.selectConversation(id);
    value.setDraft(id, { text: `Unsent ${id}`, attachments: [] });
    await flush();
  }
  for (const conversation of transport.conversations.slice(3))
    await value.selectConversation(conversation.id);
  for (const id of ['conversation-a', 'conversation-2', 'conversation-3'])
    expect(value.getDraft(id).text).toBe(`Unsent ${id}`);
  const maps = value as unknown as {
    drafts: Map<string, unknown>;
    draftRevisions: Map<string, unknown>;
    draftStates: Map<string, unknown>;
    draftWrites: Set<string>;
  };
  expect(maps.draftStates.get('conversation-a')).toBe('conflict');
  expect(maps.draftWrites.has('conversation-2')).toBe(true);
  expect(maps.draftStates.get('conversation-3')).toBe('failed');
  expect(maps.drafts.size).toBeLessThanOrEqual(35);
  expect(maps.draftRevisions.size).toBeLessThanOrEqual(35);
  expect(maps.draftStates.size).toBeLessThanOrEqual(35);
  expect(value.hasUnsavedDraft()).toBe(true);
  finish({
    conversation_id: 'conversation-2',
    revision: '1',
    text: 'Unsent conversation-2',
    attachments: [],
  });
  await flush();
  expect(value.getDraft('conversation-a').text).toBe('Unsent conversation-a');
  expect(value.getDraft('conversation-3').text).toBe('Unsent conversation-3');
});

describe('accepted protocol recordings', () => {
  it('consumes exact-model Thinking HTTP recording with the canonical TypeScript validator', () => {
    for (const record of thinkingRecording.records)
      expect(validateWire(record.schema, record.value)).toEqual(record.value);
    expect(
      thinkingRecording.records.some((record) =>
        JSON.stringify(record.value).includes('capability_revision'),
      ),
    ).toBe(true);
  });
  it('consumes every F-P01 through F-P10 recorded response with the canonical validator', () => {
    expect(recordings.map((value) => value.fixture_id)).toEqual(
      Array.from(
        { length: 10 },
        (_, i) => `F-P${String(i + 1).padStart(2, '0')}`,
      ),
    );
    for (const recording of recordings)
      for (const record of recording.records)
        expect(validateWire(record.schema, record.value)).toEqual(record.value);
  });
  it('uses an explicit fake monotonic clock, independent of Windows timer resolution', () => {
    const clock = new FixtureClock();
    const start = clock.now();
    clock.advance(60000);
    expect(clock.now() - start).toBe(60000);
    expect(() => clock.advance(-1)).toThrow();
  });
  it('sanitizes arbitrary server titles, exception messages and paths', () => {
    expect(
      JSON.stringify(
        clientError({ title: '/private/secret', code: '/private/secret' }),
      ),
    ).not.toContain('/private');
    expect(
      JSON.stringify(clientError(new Error('token=synthetic-secret'))),
    ).not.toContain('synthetic-secret');
  });
});

it.each([
  ['approval_expired', 'expired'],
  ['approval_already_resolved', 'already resolved'],
  ['model_configuration_required', 'configured model'],
])(
  'offers explicit review for %s without exposing server details',
  (code, text) => {
    const error = clientError({ code, status: 409, title: '/private/secret' });
    expect(error).toMatchObject({ code, recovery: 'review' });
    expect(error.message).toContain(text);
    expect(error.message).not.toContain('/private');
    expect(clientError({ code, status: 401 }).recovery).toBe('authenticate');
    expect(clientError({ code, status: 403 }).recovery).toBe('authenticate');
  },
);

describe('connection and lifecycle ownership', () => {
  it('drains an offline subscription before starting either recovery read', async () => {
    const transport = new FixtureTransport(),
      value = client(transport);
    await value.start();
    await value.selectConversation('conversation-a');
    await flush();
    await value.setOnline(false);
    await flush();
    let release!: () => void;
    const original = transport.unsubscribe.bind(transport);
    vi.spyOn(transport, 'unsubscribe').mockImplementationOnce(
      async (...args) => {
        await new Promise<void>((resolve) => {
          release = resolve;
        });
        return original(...args);
      },
    );
    const list = vi.spyOn(transport, 'listConversations');
    const open = vi.spyOn(transport, 'getConversation');
    const recovering = value.setOnline(true);
    await flush();
    expect(list).not.toHaveBeenCalled();
    expect(open).not.toHaveBeenCalled();
    release();
    await recovering;
    expect(list).toHaveBeenCalledTimes(1);
    expect(open).toHaveBeenCalledTimes(1);
  });
  it('does not install a concurrent open after the library revokes authentication', async () => {
    const transport = new FixtureTransport(),
      value = client(transport);
    await value.selectConversation('conversation-a');
    let release!: (row: ConversationView) => void;
    vi.spyOn(transport, 'getConversation').mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    let reject!: (error: unknown) => void;
    vi.spyOn(transport, 'listConversations').mockImplementationOnce(
      () =>
        new Promise((_resolve, fail) => {
          reject = fail;
        }),
    );
    const starting = value.start();
    await flush();
    reject({ status: 401, code: 'session_expired' });
    await flush();
    release(transport.conversations[0]);
    await starting;
    expect(value.getSnapshot()).toMatchObject({
      status: 'unauthorized',
      handshake: null,
      conversation: null,
      projection: null,
      selectedConversationId: null,
    });
    expect(transport.counters.subscribes).toBe(0);
  });
  it('recovers the selected stream while a library refresh is pending and keeps transient failure local', async () => {
    vi.stubGlobal('crypto', webcrypto);
    const transport = new FixtureTransport(),
      value = client(transport);
    await value.start();
    await value.selectConversation('conversation-a');
    await flush();
    let rejectList!: (error: unknown) => void;
    vi.spyOn(transport, 'listConversations').mockImplementationOnce(
      () =>
        new Promise((_resolve, reject) => {
          rejectList = reject;
        }),
    );
    value.setDraft('conversation-a', {
      text: 'Unsent recovery draft',
      attachments: [],
    });
    const reconnecting = value.reconnect();
    await flush();
    expect(value.getSnapshot()).toMatchObject({
      status: 'ready',
      connection: 'sse',
      loadingConversations: true,
      conversation: { id: 'conversation-a' },
    });
    expect(transport.counters.active).toBe(1);
    rejectList({ status: 503 });
    await reconnecting;
    expect(value.getSnapshot()).toMatchObject({
      status: 'ready',
      connection: 'sse',
      error: null,
      loadingConversations: false,
      conversationListError: { recovery: 'retry' },
    });
    expect(value.getDraft('conversation-a').text).toBe('Unsent recovery draft');
    const command: Command = {
      command_id: '00000000-0000-4000-8000-000000000099',
      client_session_id: value.getSnapshot().handshake!.client_session_id,
      type: 'conversation.stop',
      expected_revision: '1',
      payload: {},
    };
    await value.command('conversation-a', command, 'library-failure-command');
    expect(transport.counters.commands).toBe(1);
    await value.loadMoreConversations(true);
    expect(value.getSnapshot().conversationListError).toBeNull();
  });
  it.each(['ready', 'list'] as const)(
    'preserves newer selection made by a synchronous %s listener',
    async (stage) => {
      const transport = new FixtureTransport(),
        value = client(transport);
      await value.selectConversation('conversation-a');
      const reads = vi.spyOn(transport, 'getConversation');
      let selected = false;
      const unsubscribe = value.subscribe(() => {
        const state = value.getSnapshot();
        if (
          !selected &&
          state.status === 'ready' &&
          (stage === 'ready' || state.loadingConversations)
        ) {
          selected = true;
          void value.selectConversation('conversation-3');
        }
      });
      await value.start();
      await flush();
      unsubscribe();
      expect(reads.mock.calls.map(([id]) => id)).toEqual(['conversation-3']);
      expect(value.getSnapshot().conversation?.id).toBe('conversation-3');
    },
  );
  it.each([false, true])(
    'applies a late library authentication failure only to its credential epoch (replacement=%s)',
    async (replacement) => {
      const transport = new FixtureTransport(),
        value = client(transport);
      await value.start();
      await value.selectConversation('conversation-a');
      let rejectList!: (error: unknown) => void;
      vi.spyOn(transport, 'listConversations').mockImplementationOnce(
        () =>
          new Promise((_resolve, reject) => {
            rejectList = reject;
          }),
      );
      const pending = value.loadMoreConversations(true);
      if (replacement) await value.reconnect();
      await value.selectConversation('conversation-3');
      value.setDraft('conversation-3', {
        text: 'Private draft',
        attachments: [],
      });
      rejectList({ status: 401, code: 'session_expired' });
      await pending;
      expect(value.getSnapshot().status).toBe(
        replacement ? 'ready' : 'unauthorized',
      );
      expect(value.getDraft('conversation-3').text).toBe(
        replacement ? 'Private draft' : '',
      );
      if (!replacement)
        expect(value.getSnapshot()).toMatchObject({
          handshake: null,
          conversation: null,
          conversations: [],
          conversationListError: null,
        });
    },
  );
  it.each([false, true])(
    'applies history authentication failure only to its credential epoch (replacement=%s)',
    async (replacement) => {
      let rejectHistory!: (error: unknown) => void;
      class HistoryAuthentication extends FixtureTransport {
        hold = false;
        async history(id: string): Promise<TranscriptPage> {
          if (!this.hold) return this.getTranscript(id);
          return new Promise((_resolve, reject) => {
            rejectHistory = reject;
          });
        }
      }
      const transport = new HistoryAuthentication(),
        value = client(transport);
      value.setVisible(false);
      await value.start();
      await value.selectConversation('conversation-a');
      transport.hold = true;
      const oldHistory = value.showHistory();
      transport.hold = false;
      if (replacement) await value.reconnect();
      await value.selectConversation('conversation-3');
      value.setDraft('conversation-3', {
        text: 'Current private draft',
        attachments: [],
      });
      rejectHistory({ status: 401, code: 'session_expired' });
      await oldHistory;
      if (replacement) {
        expect(value.getSnapshot().handshake).not.toBeNull();
        expect(value.getSnapshot().conversation?.id).toBe('conversation-3');
        expect(value.getDraft('conversation-3').text).toBe(
          'Current private draft',
        );
      } else {
        expect(value.getSnapshot().handshake).toBeNull();
        expect(value.getSnapshot().conversation).toBeNull();
        expect(value.getDraft('conversation-3').text).toBe('');
      }
    },
  );
  it('does not reopen route selection or abort its history after the bootstrap list finishes', async () => {
    let releaseList!: () => void;
    let releaseHistory!: () => void;
    let historySignal: AbortSignal | undefined;
    class StartupBarrier extends FixtureTransport {
      holdHistory = false;
      override async listConversations(cursor?: string, signal?: AbortSignal) {
        const page = await super.listConversations(cursor, signal);
        await new Promise<void>((resolve) => {
          releaseList = resolve;
        });
        return page;
      }
      async history(
        id: string,
        _message?: string,
        _cursor?: string,
        signal?: AbortSignal,
      ): Promise<TranscriptPage> {
        if (!this.holdHistory) return this.getTranscript(id);
        historySignal = signal;
        await new Promise<void>((resolve, reject) => {
          releaseHistory = resolve;
          signal?.addEventListener(
            'abort',
            () => reject(new DOMException('Superseded', 'AbortError')),
            { once: true },
          );
        });
        return this.getTranscript(id);
      }
    }
    const transport = new StartupBarrier();
    const reads = vi.spyOn(transport, 'getConversation');
    const value = client(transport);
    value.setVisible(false);
    const starting = value.start();
    await flush();
    await value.selectConversation('conversation-a');
    const selection = value.getSelectionVersion();
    transport.holdHistory = true;
    const browsing = value.showHistory();
    releaseList();
    await starting;
    expect(value.getSelectionVersion()).toBe(selection);
    expect(reads).toHaveBeenCalledTimes(1);
    expect(historySignal?.aborted).toBe(false);
    releaseHistory();
    await browsing;
    expect(value.getSnapshot().history?.conversation_id).toBe('conversation-a');
  });
  it('waits for an opened conversation and contains obsolete history failures without hiding current failures', async () => {
    let releaseOpen!: () => void;
    let failHistory!: (error: unknown) => void;
    class HistoryBarrier extends FixtureTransport {
      holdHistory = false;
      override async getConversation(id: string, signal?: AbortSignal) {
        const row = await super.getConversation(id, signal);
        if (id === 'conversation-a')
          await new Promise<void>((resolve) => {
            releaseOpen = resolve;
          });
        return row;
      }
      history = vi.fn(async (id: string): Promise<TranscriptPage> => {
        if (!this.holdHistory) return this.getTranscript(id);
        return new Promise((_resolve, reject) => {
          failHistory = reject;
        });
      });
    }
    const transport = new HistoryBarrier(),
      value = client(transport);
    value.setVisible(false);
    await value.start();
    const opening = value.selectConversation('conversation-a');
    await flush();
    expect(transport.history).toHaveBeenCalledTimes(1);
    await value.showHistory();
    expect(transport.history).toHaveBeenCalledTimes(1);
    releaseOpen();
    await opening;
    transport.holdHistory = true;
    const obsolete = value.showHistory();
    transport.holdHistory = false;
    await value.selectConversation('conversation-3');
    failHistory(new TypeError('Synthetic lost old response'));
    await expect(obsolete).resolves.toBeUndefined();
    expect(value.getSnapshot().history).toBeNull();
    transport.holdHistory = true;
    const current = value.showHistory();
    failHistory({ code: 'cursor_expired' });
    await expect(current).rejects.toMatchObject({ code: 'cursor_expired' });
  });
  it('forwards keepalive only when explicitly requested for HTTP subscription release', async () => {
    const handshake = recorded<wire.HandshakeView>('F-P06', 'HandshakeView')[0];
    vi.spyOn(wire, 'handshake').mockResolvedValue(handshake);
    const unsubscribe = vi
      .spyOn(wire, 'unsubscribe')
      .mockResolvedValue({ unsubscribed: true });
    const transport = new HttpTransport();
    await transport.connect();
    const abort = new AbortController();
    await transport.unsubscribe('ordinary-subscription', abort.signal);
    await transport.unsubscribe('terminal-subscription', undefined, true);
    const proof = {
      client_session_id: handshake.client_session_id,
      csrf_token: handshake.csrf_token,
    };
    expect(unsubscribe.mock.calls).toEqual([
      ['', proof, 'ordinary-subscription', abort.signal, false],
      ['', proof, 'terminal-subscription', undefined, true],
    ]);
  });
  it('keeps ordinary subscription release abortable and terminal release bounded to one keepalive request', async () => {
    const releases: { signal?: AbortSignal; keepalive: boolean }[] = [];
    class ReleaseFixture extends FixtureTransport {
      override unsubscribe(
        subscription: string,
        signal?: AbortSignal,
        keepalive = false,
      ) {
        releases.push({ signal, keepalive });
        return super.unsubscribe(subscription);
      }
    }
    const transport = new ReleaseFixture();
    const value = client(transport);
    await value.start();
    await value.selectConversation('conversation-a');
    await flush();
    await value.selectConversation('conversation-2');
    await flush();
    expect(releases).toHaveLength(1);
    expect(releases[0]?.keepalive).toBe(false);
    expect(releases[0]?.signal?.aborted).toBe(false);
    value.dispose();
    value.dispose();
    await flush();
    expect(releases).toHaveLength(2);
    expect(releases[0]?.signal?.aborted).toBe(true);
    expect(releases[1]).toEqual({ signal: undefined, keepalive: true });
    expect(transport.counters.active).toBe(0);
    expect(transport.counters.streams).toBe(0);
    expect(transport.counters.commands).toBe(0);
  });
  it.each(['synchronous', 'deferred'] as const)(
    'contains %s terminal release failure without retries or command replay',
    async (failure) => {
      let rejectRelease: (error: unknown) => void = () => {};
      let releases = 0;
      class FailedReleaseFixture extends FixtureTransport {
        override unsubscribe(
          subscription: string,
          signal?: AbortSignal,
          keepalive = false,
        ) {
          if (!keepalive) return super.unsubscribe(subscription);
          expect(signal).toBeUndefined();
          releases += 1;
          if (failure === 'synchronous')
            throw new DOMException(
              'Synthetic terminal failure',
              'SecurityError',
            );
          return new Promise<wire.Unsubscribed>((_resolve, reject) => {
            rejectRelease = reject;
          });
        }
      }
      const transport = new FailedReleaseFixture();
      const value = client(transport);
      await value.start();
      await value.selectConversation('conversation-a');
      await flush();
      const confirmed = value.getSnapshot();
      value.dispose();
      rejectRelease(
        new DOMException('Synthetic terminal failure', 'SecurityError'),
      );
      await flush();
      await value.reconnect();
      value.setVisible(true);
      await value.setOnline(true);
      await flush();
      expect(value.getSnapshot()).toBe(confirmed);
      expect(releases).toBe(1);
      expect(transport.counters.streams).toBe(0);
      expect(transport.counters.commands).toBe(0);
    },
  );
  it('retains only an opaque HTTP resume identity while suspended and discards it on revocation', async () => {
    const handshake = recorded<wire.HandshakeView>('F-P06', 'HandshakeView')[0];
    const requests: wire.Handshake[] = [];
    let sessions = 0;
    vi.spyOn(wire, 'handshake').mockImplementation(async (_base, request) => {
      requests.push(request);
      return {
        ...handshake,
        client_session_id:
          request.client_session_id ??
          `00000000-0000-4000-8000-${String(++sessions).padStart(12, '0')}`,
      };
    });
    const transport = new HttpTransport();
    const first = await transport.connect();
    for (let index = 0; index < 101; index += 1) {
      transport.clearSession(true);
      expect(() => transport.receipt('fixture')).toThrow();
      const resumed = await transport.connect();
      expect(resumed.client_session_id).toBe(first.client_session_id);
    }
    expect(sessions).toBe(1);
    expect(
      requests
        .slice(1)
        .every(
          (request) => request.client_session_id === first.client_session_id,
        ),
    ).toBe(true);
    expect(JSON.stringify(requests)).not.toContain('csrf');
    transport.clearSession();
    await transport.connect();
    expect(sessions).toBe(2);
    expect(requests.at(-1)?.client_session_id).toBeUndefined();
  });
  it('does no network work while initially offline and restores latest local selection when online', async () => {
    const transport = new FixtureTransport();
    const value = client(transport);
    await value.setOnline(false);
    await value.start();
    await value.reconnect();
    await value.selectConversation('conversation-3');
    value.setVisible(false);
    value.setVisible(true);
    await value.loadMoreConversations();
    await value.loadMoreTranscript();
    await expect(value.download('fixture')).rejects.toMatchObject({
      code: 'network_unavailable',
    });
    expect(transport.counters.connects).toBe(0);
    expect(transport.counters.subscribes).toBe(0);
    expect(value.getSnapshot().status).toBe('disconnected');
    await Promise.all([value.setOnline(true), value.setOnline(true)]);
    await flush();
    expect(value.getSnapshot().status).toBe('ready');
    expect(value.getSnapshot().selectedConversationId).toBe('conversation-3');
    expect(transport.counters.connects).toBe(1);
    expect(transport.counters.active).toBe(1);
  });
  it('suspends and resumes 101 times without session/lease growth or command replay', async () => {
    vi.stubGlobal('crypto', webcrypto);
    class ResumableFixture extends FixtureTransport {
      sessionCount = 0;
      resumeId: string | undefined;
      override async connect(signal?: AbortSignal) {
        const view = await super.connect(signal);
        this.resumeId ??= `00000000-0000-4000-8000-${String(++this.sessionCount).padStart(12, '0')}`;
        return { ...view, client_session_id: this.resumeId };
      }
      override clearSession(preserve = false) {
        if (!preserve) this.resumeId = undefined;
      }
    }
    const transport = new ResumableFixture();
    const value = client(transport);
    await value.start();
    await value.selectConversation('conversation-a');
    await flush();
    await value.command(
      'conversation-a',
      {
        type: 'conversation.rename',
        command_id: '00000000-0000-4000-8000-000000000016',
        client_session_id: value.getSnapshot().handshake!.client_session_id,
        expected_revision: '1',
        payload: { title: 'Synthetic command before suspension' },
      },
      'before-suspension',
    );
    for (let index = 0; index < 101; index += 1) {
      const projection = value.getSnapshot().projection;
      const calls = {
        connects: transport.counters.connects,
        subscribes: transport.counters.subscribes,
        unsubscribes: transport.counters.unsubscribes,
        polls: transport.counters.polls,
      };
      await value.setOnline(false);
      await flush();
      await value.setOnline(false);
      await value.start();
      await value.reconnect();
      value.setVisible(false);
      value.setVisible(true);
      expect(value.getSnapshot().status).toBe('disconnected');
      expect(value.getSnapshot().handshake).toBeNull();
      expect(value.getSnapshot().projection).toBe(projection);
      expect({
        connects: transport.counters.connects,
        subscribes: transport.counters.subscribes,
        unsubscribes: transport.counters.unsubscribes,
        polls: transport.counters.polls,
      }).toEqual(calls);
      expect(transport.counters.streams).toBe(0);
      await value.setOnline(true);
      await flush();
      expect(transport.sessionCount).toBe(1);
      expect(transport.counters.active).toBe(1);
      expect(transport.counters.streams).toBe(1);
      expect(value.getSnapshot().selectedConversationId).toBe('conversation-a');
    }
    expect(transport.counters.subscribes).toBe(102);
    expect(transport.counters.unsubscribes).toBe(101);
    expect(transport.counters.commands).toBe(1);
    vi.spyOn(transport, 'receipt').mockRejectedValueOnce({
      status: 401,
      code: 'session_expired',
    });
    await expect(value.receipt('expired-fixture')).rejects.toMatchObject({
      code: 'session_expired',
    });
    expect(transport.resumeId).toBeUndefined();
    expect(value.getSnapshot().selectedConversationId).toBeNull();
    await value.reconnect();
    await flush();
    expect(transport.sessionCount).toBe(2);
    expect(transport.counters.commands).toBe(1);
    value.dispose();
    await flush();
    expect(transport.counters.active).toBe(0);
    expect(transport.counters.streams).toBe(0);
  });
  it('fences reversed old bootstraps across repeated offline and online transitions', async () => {
    const transport = new FixtureTransport();
    const value = client(transport);
    let first!: (value: wire.HandshakeView) => void;
    let second!: (reason: unknown) => void;
    vi.spyOn(transport, 'connect')
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            first = resolve;
          }),
      )
      .mockImplementationOnce(
        () =>
          new Promise((_resolve, reject) => {
            second = reject;
          }),
      );
    const original = value.start();
    await value.setOnline(false);
    const middle = value.setOnline(true);
    await value.setOnline(false);
    await value.selectConversation('conversation-3');
    await value.setOnline(true);
    await flush();
    first(recorded<wire.HandshakeView>('F-P06', 'HandshakeView')[0]);
    second({ status: 401, code: 'session_expired' });
    await Promise.all([original, middle]);
    await flush();
    expect(value.getSnapshot().status).toBe('ready');
    expect(value.getSnapshot().selectedConversationId).toBe('conversation-3');
    expect(transport.counters.active).toBe(1);
    expect(transport.counters.streams).toBe(1);
  });
  it.each([1000, 10000] as const)(
    'calibrates %i rows with complete on-demand pages and bounded retained projection',
    async (count) => {
      const transport = new FixtureTransport();
      transport.setTranscriptSize(count);
      const value = client(transport);
      await value.start();
      await value.selectConversation('conversation-a');
      await flush();
      const seen = new Set(
        value.getSnapshot().projection!.rows.map((row) => row.id),
      );
      while (value.getSnapshot().hasMoreTranscript) {
        await value.loadMoreTranscript();
        const state = value.getSnapshot();
        expect(state.status).toBe('ready');
        expect(state.projection!.rows.length).toBeLessThanOrEqual(200);
        state.projection!.rows.forEach((row) => seen.add(row.id));
      }
      expect(seen.size).toBe(count);
      expect(transport.fixtureTranscriptRowCount).toBe(count);
      expect(transport.counters.transcriptPages).toBe(count / 100);
      expect(transport.counters.transcriptRowsDelivered).toBe(count);
    },
  );
  it('emits accepted-shape monotonic text deltas and reconciles the same fixture snapshot', async () => {
    const transport = new FixtureTransport();
    const value = client(transport);
    await value.start();
    await value.selectConversation('conversation-a');
    await flush();
    const before = value.getSnapshot().projection!.projection_revision;
    transport.emitTextDelta('first ');
    await flush();
    transport.emitTextDelta('second');
    await flush();
    const snapshot = value.getSnapshot().projection!;
    expect(snapshot.projection_revision).toBe(String(BigInt(before) + 2n));
    expect(
      snapshot.rows.find((row) => row.id === 'fixture-live-row')?.blocks[0]
        .text,
    ).toBe('first second');
    expect(value.metrics.appliedEvents).toBe(2);
    value.setVisible(false);
    value.setVisible(true);
    await flush();
    expect(value.getSnapshot().projection).toEqual(snapshot);
  });
  it('drains the issued ACK and cancels trailing cuts before retiring a reset subscription', async () => {
    vi.useFakeTimers();
    const transport = new FixtureTransport();
    let finish!: () => void;
    const actual = transport.acknowledge.bind(transport);
    let calls = 0;
    vi.spyOn(transport, 'acknowledge').mockImplementation(async (...args) => {
      if (++calls === 2)
        await new Promise<void>((resolve) => {
          finish = resolve;
        });
      return actual(...args);
    });
    const retire = vi.spyOn(transport, 'unsubscribe');
    const value = client(transport);
    await value.start();
    await value.selectConversation('conversation-a');
    await flush();
    transport.emitTextDelta('first');
    await flush();
    await vi.advanceTimersByTimeAsync(0);
    expect(calls).toBe(2);
    transport.emitTextDelta('trailing');
    transport.emit({ snapshot_required: true, recovery: 'resubscribe' });
    await flush();
    await vi.advanceTimersByTimeAsync(1000);
    expect(retire).not.toHaveBeenCalled();
    expect(calls).toBe(2);
    finish();
    await flush();
    expect(retire).toHaveBeenCalledTimes(1);
    expect(transport.counters.subscribes).toBe(2);
    expect(calls).toBe(3);
    expect(value.getSnapshot().status).toBe('ready');
  });
  it('resets after a stalled ACK deadline and contains its late failure without a trailing ACK', async () => {
    vi.useFakeTimers();
    const transport = new FixtureTransport();
    const actual = transport.acknowledge.bind(transport);
    let calls = 0,
      reject!: (error: unknown) => void,
      stalled!: AbortSignal;
    vi.spyOn(transport, 'acknowledge').mockImplementation((...args) => {
      if (++calls === 2) {
        stalled = args[2]!;
        return new Promise((_resolve, no) => {
          reject = no;
        });
      }
      return actual(...args);
    });
    const retired = vi.spyOn(transport, 'unsubscribe');
    const value = client(transport);
    await value.start();
    await value.selectConversation('conversation-a');
    await flush();
    transport.emitTextDelta('first');
    await flush();
    await vi.advanceTimersByTimeAsync(0);
    transport.emitTextDelta('trailing');
    transport.emit({ snapshot_required: true, recovery: 'resubscribe' });
    await flush();
    await vi.advanceTimersByTimeAsync(ACK_RETIRE_TIMEOUT_MS - 1);
    expect(retired).not.toHaveBeenCalled();
    expect(stalled.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    await flush();
    expect(stalled.aborted).toBe(true);
    expect(retired).toHaveBeenCalledTimes(1);
    expect(transport.counters.subscribes).toBe(2);
    expect(calls).toBe(3);
    expect(value.getSnapshot().status).toBe('ready');
    reject({ status: 401, code: 'session_expired' });
    await flush();
    expect(value.getSnapshot().status).toBe('ready');
    expect(value.getSnapshot().error).toBeNull();
    expect(calls).toBe(3);
    expect(transport.counters.active).toBe(1);
  });
  it('expires replay exactly once during reconnect and installs a fresh snapshot', async () => {
    const transport = new FixtureTransport();
    const value = client(transport);
    await value.start();
    await value.selectConversation('conversation-a');
    await flush();
    const before = transport.counters.subscribes;
    transport.expireNextReplay();
    await value.reconnect();
    await flush();
    expect(transport.counters.subscribes - before).toBe(2);
    expect(transport.counters.active).toBe(1);
    expect(transport.counters.streams).toBe(1);
    expect(value.getSnapshot().status).toBe('ready');
    expect(value.getSnapshot().projection).not.toBeNull();
    expect(transport.counters.commands).toBe(0);
  });
  it.each(['upload', 'download', 'receipt'] as const)(
    'discards late %s completion after dispose even when transport ignores abort',
    async (operation) => {
      const transport = new FixtureTransport();
      const value = client(transport);
      await value.start();
      let release!: (value: never) => void;
      let observed: AbortSignal | undefined;
      vi.spyOn(transport, operation).mockImplementation(
        (...args: unknown[]) => {
          observed = args.at(-1) as AbortSignal;
          return new Promise<never>((resolve) => {
            release = resolve;
          });
        },
      );
      const pending =
        operation === 'upload'
          ? value.upload('conversation-a', new File(['fixture'], 'fixture.txt'))
          : operation === 'download'
            ? value.download('fixture')
            : value.receipt('fixture');
      const assertion = expect(pending).rejects.toMatchObject({
        name: 'AbortError',
      });
      value.dispose();
      expect(observed?.aborted).toBe(true);
      release({} as never);
      await assertion;
    },
  );
  it('propagates caller cancellation and blocks media after authentication revocation', async () => {
    const transport = new FixtureTransport();
    const value = client(transport);
    await value.start();
    let release!: (value: Blob) => void;
    const download = vi.spyOn(transport, 'download').mockImplementation(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    const caller = new AbortController();
    const pending = value.download('fixture', caller.signal);
    const assertion = expect(pending).rejects.toMatchObject({
      name: 'AbortError',
    });
    caller.abort();
    release(new Blob(['late fixture']));
    await assertion;
    vi.spyOn(transport, 'receipt').mockRejectedValueOnce({
      code: 'session_expired',
      status: 401,
    });
    await expect(value.receipt('fixture')).rejects.toMatchObject({
      code: 'session_expired',
    });
    await expect(value.download('fixture')).rejects.toMatchObject({
      code: 'authentication_required',
    });
    expect(download).toHaveBeenCalledTimes(1);
    expect(value.getSnapshot().status).toBe('unauthorized');
  });
  it('fences a pending media result across authentication loss and a fresh reconnect', async () => {
    const transport = new FixtureTransport();
    const value = client(transport);
    await value.start();
    let release!: (value: Blob) => void;
    vi.spyOn(transport, 'download').mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    const pending = value.download('fixture');
    const assertion = expect(pending).rejects.toMatchObject({
      name: 'AbortError',
    });
    vi.spyOn(transport, 'receipt').mockRejectedValueOnce({
      code: 'session_expired',
      status: 401,
    });
    await expect(value.receipt('fixture')).rejects.toMatchObject({
      code: 'session_expired',
    });
    await value.reconnect();
    release(new Blob(['old session fixture']));
    await assertion;
    expect(value.getSnapshot().status).toBe('ready');
    expect(await value.download('fixture')).toBeInstanceOf(Blob);
  });
  it('handshakes once and never exposes CSRF proof in public state', async () => {
    const transport = new FixtureTransport();
    const value = client(transport);
    await Promise.all([value.start(), value.start()]);
    expect(transport.counters.connects).toBe(1);
    expect(value.getSnapshot().handshake).not.toHaveProperty('csrf_token');
    expect(value.getSnapshot().conversations).toHaveLength(3);
  });
  it.each(['incompatible', 'unauthorized', 'disconnected'] as const)(
    'exposes truthful %s startup recovery',
    async (scenario) => {
      const value = client(new FixtureTransport({ scenario }));
      await value.start();
      expect(value.getSnapshot().status).toBe(scenario);
      expect(value.getSnapshot().handshake).toBeNull();
    },
  );
  it('has zero unchanged notifications during 60s idle and no observers/timers after 100 cycles', async () => {
    vi.useFakeTimers();
    const transport = new FixtureTransport();
    const value = client(transport);
    await value.start();
    await value.selectConversation('conversation-a');
    await flush();
    const notifications = value.metrics.notifications;
    await vi.advanceTimersByTimeAsync(60000);
    transport.clock.advance(60000);
    expect(value.metrics.notifications - notifications).toBe(0);
    for (let i = 0; i < 100; i++) {
      value.setVisible(false);
      await flush();
      value.setVisible(true);
      await flush();
    }
    expect(transport.counters.active).toBe(1);
    expect(transport.counters.streams).toBe(1);
    expect(transport.counters.listeners).toBe(1);
    value.dispose();
    await flush();
    expect(transport.counters.active).toBe(0);
    expect(transport.counters.streams).toBe(0);
    expect(transport.counters.listeners).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
    expect(transport.counters.commands).toBe(0);
  });
  it('switches sequential SSE retries to identical-cursor polling, never concurrently', async () => {
    vi.useFakeTimers();
    class PollFixture extends FixtureTransport {
      override async *observe(): AsyncGenerator<EventRecord> {
        yield* [];
        throw new TypeError('SSE unavailable');
      }
    }
    const transport = new PollFixture();
    const value = client(transport);
    await value.start();
    await value.selectConversation('conversation-a');
    await flush();
    expect(value.getSnapshot().status).toBe('reconnecting');
    await vi.advanceTimersByTimeAsync(3000);
    await flush();
    expect(value.getSnapshot().connection).toBe('poll');
    expect(transport.counters.polls).toBe(1);
    await vi.advanceTimersByTimeAsync(60000);
    expect(transport.counters.polls).toBeLessThanOrEqual(6);
    expect(transport.counters.streams).toBe(0);
    expect(transport.counters.commands).toBe(0);
  });
  it('halts after authentication revocation and clears protected view without replaying commands', async () => {
    vi.useFakeTimers();
    const transport = new FixtureTransport();
    const value = client(transport);
    await value.start();
    await value.selectConversation('conversation-a');
    await flush();
    transport.scenario = 'unauthorized';
    transport.emit({ snapshot_required: true });
    await flush();
    expect(value.getSnapshot().status).toBe('unauthorized');
    expect(value.getSnapshot().projection).toBeNull();
    const calls = transport.counters.subscribes;
    await vi.advanceTimersByTimeAsync(60000);
    expect(transport.counters.subscribes).toBe(calls);
    expect(transport.counters.commands).toBe(0);
  });
});

describe('revisioned snapshot and independent selection', () => {
  it('handles local panel.suggested as an advisory without selecting or requesting data', async () => {
    const transport = new FixtureTransport();
    const value = client(transport);
    await value.start();
    await value.selectConversation('conversation-a');
    await flush();
    const before = value.getSnapshot();
    const suggestion = {
      type: 'panel.suggested' as const,
      conversation_id: 'conversation-2',
      conversation_revision: '999',
      descriptor: { panel_kind: 'fake.info', title: 'Suggested sample' },
    };
    value.suggestPanel(suggestion);
    value.suggestPanel(suggestion);
    expect(value.getSnapshot().suggestions).toHaveLength(1);
    expect(value.getSnapshot().selectedConversationId).toBe(
      before.selectedConversationId,
    );
    expect(value.getSnapshot().projection).toBe(before.projection);
    expect(transport.counters.commands).toBe(0);
    value.dismissSuggestion(suggestion);
    expect(value.getSnapshot().suggestions).toEqual([]);
    value.suggestPanel({ ...suggestion, conversation_revision: '0' });
    expect(value.getSnapshot().suggestions).toEqual([]);
  });
  it('coalesces reconnect and preserves a newer selection made during handshake', async () => {
    let resume!: () => void;
    class HandshakeBarrier extends FixtureTransport {
      override async connect(signal?: AbortSignal) {
        const response = await super.connect(signal);
        if (this.counters.connects > 1)
          await new Promise<void>((resolve) => {
            resume = resolve;
          });
        return response;
      }
    }
    const transport = new HandshakeBarrier();
    const value = client(transport);
    await value.start();
    await value.selectConversation('conversation-a');
    await flush();
    const first = value.reconnect(),
      second = value.reconnect();
    await flush();
    await value.selectConversation('conversation-3');
    resume();
    await Promise.all([first, second]);
    await flush();
    expect(transport.counters.connects).toBe(2);
    expect(value.getSnapshot().conversation?.id).toBe('conversation-3');
    expect(transport.counters.active).toBe(1);
  });
  it('does not republish a delayed conversation page after authentication revocation', async () => {
    let resume!: (page: import('./types').ConversationPage) => void;
    class PageBarrier extends FixtureTransport {
      override async listConversations(cursor?: string, signal?: AbortSignal) {
        if (!cursor) return super.listConversations(cursor, signal);
        return new Promise<import('./types').ConversationPage>((resolve) => {
          resume = resolve;
        });
      }
    }
    const transport = new PageBarrier({ conversationCount: 60 });
    const value = client(transport);
    await value.start();
    await value.selectConversation('conversation-a');
    await flush();
    const pending = value.loadMoreConversations();
    transport.scenario = 'unauthorized';
    transport.emit({ snapshot_required: true });
    await flush();
    resume({
      items: [
        {
          id: 'private-late',
          revision: '1',
          title: 'Protected delayed item',
          pinned: false,
        },
      ],
      has_more: false,
    });
    await pending;
    expect(value.getSnapshot().status).toBe('unauthorized');
    expect(value.getSnapshot().conversations).toEqual([]);
  });
  it('only installs C after deliberately reversed A-B-C completion', async () => {
    const resolvers = new Map<string, (row: ConversationView) => void>();
    class Delayed extends FixtureTransport {
      override getConversation(id: string): Promise<ConversationView> {
        return new Promise((resolve) => resolvers.set(id, resolve));
      }
    }
    const transport = new Delayed();
    const value = client(transport);
    await value.start();
    const pending = ['conversation-a', 'conversation-2', 'conversation-3'].map(
      (id) => value.selectConversation(id),
    );
    for (const id of ['conversation-3', 'conversation-2', 'conversation-a'])
      resolvers.get(id)!({ id, title: id, pinned: false, revision: '1' });
    await Promise.all(pending);
    await flush();
    expect(value.getSnapshot().selectedConversationId).toBe('conversation-3');
    expect(value.getSnapshot().conversation?.id).toBe('conversation-3');
    expect(transport.counters.active).toBe(1);
  });
  it('keeps two client selections independent', async () => {
    const a = client(),
      b = client();
    await Promise.all([a.start(), b.start()]);
    await a.selectConversation('conversation-a');
    await b.selectConversation('conversation-2');
    await flush();
    expect(a.getSnapshot().selectedConversationId).toBe('conversation-a');
    expect(b.getSnapshot().selectedConversationId).toBe('conversation-2');
  });
  it('keeps a workspace read-hook denial local to the panel while the conversation observes', async () => {
    class PolicyTransport extends FixtureTransport {
      async inspector() {
        throw { status: 403, code: 'workspace_read_hooks_unavailable' };
      }
    }
    const transport = new PolicyTransport();
    const value = client(transport);
    await value.start();
    await value.selectConversation('conversation-a');
    await flush();
    await expect(
      value.inspector('conversation-a', 'binding'),
    ).rejects.toMatchObject({ code: 'workspace_read_hooks_unavailable' });
    expect(
      clientError({ status: 403, code: 'workspace_read_hooks_unavailable' })
        .recovery,
    ).toBe('review');
    expect(
      clientError({ status: 401, code: 'workspace_read_hooks_unavailable' })
        .recovery,
    ).toBe('authenticate');
    expect(value.getSnapshot().status).toBe('ready');
    expect(value.getSnapshot().handshake).not.toBeNull();
    transport.emitTextDelta('Conversation continues');
    await flush();
    expect(
      value
        .getSnapshot()
        .projection?.rows.some((row) =>
          row.blocks.some((block) => block.text === 'Conversation continues'),
        ),
    ).toBe(true);
    expect(transport.counters.active).toBe(1);
  });
  it('traverses all 1005 conversations through continuation without duplicate IDs', async () => {
    const value = client(new FixtureTransport({ conversationCount: 1005 }));
    await value.start();
    const visited = new Set(
      value.getSnapshot().conversations.map((row) => row.id),
    );
    while (value.getSnapshot().hasMoreConversations) {
      await value.loadMoreConversations();
      value.getSnapshot().conversations.forEach((row) => visited.add(row.id));
      expect(value.getSnapshot().conversations.length).toBeLessThanOrEqual(
        1000,
      );
    }
    expect(visited.size).toBe(1005);
  });
  it('accepts an unchanged first-page cursor on repeated library refresh', async () => {
    const value = client(new FixtureTransport({ conversationCount: 1005 }));
    await value.start();
    await value.selectConversation('conversation-a');
    const first = value.getSnapshot().conversations.map((row) => row.id);
    for (let index = 0; index < 3; index++) {
      await value.loadMoreConversations(true);
      expect(value.getSnapshot().status).toBe('ready');
      expect(value.getSnapshot().error).toBeNull();
      expect(value.getSnapshot().conversations.map((row) => row.id)).toEqual(
        first,
      );
    }
    await value.loadMoreConversations();
    expect(value.getSnapshot().conversations.length).toBeGreaterThan(
      first.length,
    );
  });
  it('traverses the accepted 1005-row recording with at most 200 materialized rows', async () => {
    const pages = recorded<TranscriptPage>('F-P05', 'TranscriptPage');
    const observed = new Set<string>();
    class History extends FixtureTransport {
      override async getTranscript(
        id: string,
        cursor?: string,
      ): Promise<TranscriptPage> {
        const index = cursor
          ? pages.findIndex((page) => page.next_cursor === cursor) + 1
          : 0;
        const page = { ...pages[index], conversation_id: id };
        page.rows.forEach((row) => observed.add(row.id));
        return page;
      }
    }
    const value = client(new History());
    value.setVisible(false);
    await value.start();
    await value.selectConversation('conversation-a');
    while (value.getSnapshot().hasMoreTranscript) {
      await value.loadMoreTranscript();
      expect(value.getSnapshot().projection!.rows.length).toBeLessThanOrEqual(
        200,
      );
    }
    expect(observed.size).toBe(1005);
  });
});

describe('event order, atomic reset and commands', () => {
  it('processes a valid 300-event polling page in bounded slices preserving approval and final state', async () => {
    vi.useFakeTimers();
    const initial = recorded<SubscriptionView>('F-P03', 'SubscriptionView')[0]
      .snapshot;
    const source = recorded<Event>('F-P03', 'Event')[0];
    const final = recorded<Event>('F-P01', 'Event').find(
      (event) =>
        event.type === 'generation.state' &&
        event.payload.status === 'completed',
    )!;
    let supplied = false;
    class LargePoll extends FixtureTransport {
      override async *observe(): AsyncGenerator<EventRecord> {
        yield* [];
        throw new TypeError('SSE unavailable');
      }
      override async poll(_subscription: string, cursor: string) {
        const events: EventRecord[] = supplied
          ? []
          : Array.from({ length: 300 }, (_, index) => {
              const revision = String(index + 1);
              const event = {
                ...source,
                event_id: `batch-${revision}`,
                projection_revision: revision,
                source_sequence_start: revision,
                source_sequence_end: revision,
                ...(index === 299
                  ? { type: final.type, payload: final.payload }
                  : index === 256
                    ? {
                        type: 'approval.required',
                        payload: { status: 'waiting_approval' },
                      }
                    : {
                        type: 'queue.updated',
                        payload: { submission_ids: [], revision },
                      }),
              } as Event;
              return { event, cursor: `batch-cursor-${revision}` };
            });
        supplied = true;
        return {
          snapshot_required: false,
          events,
          cursor: events.at(-1)?.cursor ?? cursor,
        };
      }
    }
    const transport = new LargePoll();
    transport.setSnapshot(initial);
    const value = client(transport);
    const approvals: string[] = [];
    value.subscribe(() => {
      if (value.getSnapshot().projection?.projection_revision === '257')
        approvals.push('observed');
    });
    await value.start();
    await value.selectConversation('conversation-a');
    await flush();
    await vi.advanceTimersByTimeAsync(3001);
    await flush();
    expect(value.metrics.appliedEvents).toBe(300);
    expect(value.metrics.maxBatch).toBe(256);
    expect(approvals).toEqual(['observed']);
    expect(value.getSnapshot().projection?.generation?.status).toBe(
      'completed',
    );
    expect(transport.counters.acks.at(-1)).toBe('batch-cursor-300');
  });
  it('bounds repeated snapshot reset without progress', async () => {
    class ResetLoop extends FixtureTransport {
      override async *observe() {
        yield {
          snapshot_required: true as const,
          recovery: 'resubscribe' as const,
        };
      }
    }
    const transport = new ResetLoop();
    const value = client(transport);
    const exhausted = new Promise<void>((resolve) => {
      const detach = value.subscribe(() => {
        if (value.getSnapshot().status === 'incompatible') {
          detach();
          resolve();
        }
      });
    });
    await value.start();
    await value.selectConversation('conversation-a');
    await exhausted;
    await flush();
    expect(value.getSnapshot().status).toBe('incompatible');
    expect(transport.counters.subscribes).toBe(4);
    expect(transport.counters.active).toBe(0);
  });
  it('a late old acknowledgement cannot start a second stream or overwrite new selection', async () => {
    let resume!: () => void;
    let firstAck = true;
    class AckBarrier extends FixtureTransport {
      override async acknowledge(
        subscription: string,
        cursor: string,
        signal?: AbortSignal,
      ) {
        if (firstAck) {
          firstAck = false;
          await new Promise<void>((resolve) => {
            resume = resolve;
          });
          return { acknowledged: true as const };
        }
        return super.acknowledge(subscription, cursor, signal);
      }
    }
    const transport = new AckBarrier();
    const value = client(transport);
    await value.start();
    await value.selectConversation('conversation-a');
    await flush();
    await value.selectConversation('conversation-2');
    await flush();
    resume();
    await flush();
    expect(value.getSnapshot().conversation?.id).toBe('conversation-2');
    expect(transport.counters.maxStreams).toBe(1);
  });
  it('late reset cleanup cannot detach the newer subscription from disposal', async () => {
    let resume!: () => void;
    let firstClose = true;
    class CloseBarrier extends FixtureTransport {
      override async unsubscribe(subscription: string) {
        if (firstClose) {
          firstClose = false;
          await new Promise<void>((resolve) => {
            resume = resolve;
          });
        }
        return super.unsubscribe(subscription);
      }
    }
    const transport = new CloseBarrier();
    const value = client(transport);
    await value.start();
    await value.selectConversation('conversation-a');
    await flush();
    transport.emit({ snapshot_required: true });
    await flush();
    await value.selectConversation('conversation-2');
    await flush();
    resume();
    await flush();
    value.dispose();
    await flush();
    expect(transport.counters.active).toBe(0);
    expect(transport.counters.streams).toBe(0);
  });
  async function observed() {
    const transport = new FixtureTransport();
    const initial = recorded<SubscriptionView>('F-P03', 'SubscriptionView')[0]
      .snapshot;
    transport.setSnapshot(initial);
    const value = client(transport);
    await value.start();
    await value.selectConversation('conversation-a');
    await flush();
    return { transport, value, initial };
  }
  function eventRecord(
    initial: Snapshot,
    revision: number,
    sequence = revision,
  ): EventRecord {
    const event = recorded<Event>('F-P03', 'Event')[0];
    return {
      cursor: `cursor-${revision}`,
      event: {
        ...event,
        event_id: `fixture-event-${revision}`,
        projection_revision: String(revision),
        source_sequence_start: String(sequence),
        source_sequence_end: String(sequence),
        server_epoch: initial.server_epoch,
      },
    };
  }
  it('deduplicates accepted events and never acknowledges a backwards cursor', async () => {
    vi.useFakeTimers();
    const { value, transport, initial } = await observed();
    const first = eventRecord(initial, 1),
      second = eventRecord(initial, 2);
    transport.emit(first);
    transport.emit(second);
    transport.emit(first);
    await flush();
    expect(value.metrics.appliedEvents).toBe(2);
    expect(value.metrics.duplicateEvents).toBe(1);
    expect(value.getSnapshot().projection?.cursor).toBe('cursor-2');
    await vi.advanceTimersByTimeAsync(1000);
    expect(transport.counters.acks.at(-1)).toBe('cursor-2');
  });
  it('resubscribes on a source sequence gap and atomically installs a new snapshot cut', async () => {
    const { value, transport, initial } = await observed();
    transport.emit(eventRecord(initial, 1));
    await flush();
    const fresh = { ...initial, projection_revision: '5', cursor: 'fresh-cut' };
    transport.setSnapshot(fresh);
    transport.emit(eventRecord(initial, 2, 4));
    await flush();
    expect(value.getSnapshot().projection).toEqual(fresh);
    expect(value.metrics.resets).toBe(2);
    expect(transport.counters.acks.at(-1)).toBe('fresh-cut');
    expect(transport.counters.commands).toBe(0);
  });
  it('installs snapshot then suffix on return without changing selection', async () => {
    const { value, transport, initial } = await observed();
    value.setVisible(false);
    await flush();
    const fresh = {
      ...initial,
      server_epoch: 'new-epoch',
      projection_revision: '10',
      cursor: 'epoch-cut',
    };
    transport.setSnapshot(fresh);
    value.setVisible(true);
    await flush();
    transport.emit(eventRecord(fresh, 11));
    await flush();
    expect(value.getSnapshot().projection?.projection_revision).toBe('11');
    expect(value.getSnapshot().projection?.server_epoch).toBe('new-epoch');
    expect(value.getSnapshot().selectedConversationId).toBe('conversation-a');
  });
  it('coalesces duplicate command intent, rejects changed input and never retries response loss', async () => {
    vi.stubGlobal('crypto', webcrypto);
    const transport = new FixtureTransport();
    const value = client(transport);
    await value.start();
    const command: Command = {
      command_id: '00000000-0000-4000-8000-000000000011',
      client_session_id: value.getSnapshot().handshake!.client_session_id,
      type: 'conversation.rename',
      expected_revision: '1',
      payload: { title: 'Synthetic rename' },
    };
    const first = value.command('conversation-a', command, 'same-key');
    const second = value.command('conversation-a', command, 'same-key');
    expect(await first).toEqual(await second);
    expect(transport.counters.commands).toBe(1);
    await expect(
      value.command('conversation-2', command, 'same-key'),
    ).rejects.toMatchObject({ code: 'idempotency_mismatch' });
    vi.spyOn(transport, 'command').mockRejectedValueOnce(
      new TypeError('response lost'),
    );
    await expect(
      value.command(
        'conversation-a',
        { ...command, command_id: '00000000-0000-4000-8000-000000000012' },
        'lost-key',
      ),
    ).rejects.toMatchObject({ code: 'network_unavailable' });
    await value.reconnect();
    expect(transport.counters.commands).toBe(1);
    const lost = {
      ...command,
      command_id: '00000000-0000-4000-8000-000000000012',
    };
    await value.retryCommand('conversation-a', lost, 'lost-key');
    expect(transport.counters.commands).toBe(2);
  });
  it('retires settled command claims without blocking a long-lived session', async () => {
    vi.stubGlobal('crypto', webcrypto);
    const transport = new FixtureTransport();
    const value = client(transport);
    await value.start();
    for (let index = 0; index < 260; index++) {
      await value.command(
        'conversation-a',
        {
          command_id: `00000000-0000-4000-8000-${String(index).padStart(12, '0')}`,
          client_session_id: value.getSnapshot().handshake!.client_session_id,
          type: 'conversation.rename',
          expected_revision: '1',
          payload: { title: `Title ${index}` },
        },
        `claim-${index}`,
      );
    }
    expect(transport.counters.commands).toBe(260);
  });
  it.each(['dispose', 'reconnect'] as const)(
    'does not dispatch delayed command verification across %s',
    async (transition) => {
      let verify!: (value: ArrayBuffer) => void;
      vi.stubGlobal('crypto', {
        subtle: {
          digest: () =>
            new Promise<ArrayBuffer>((resolve) => {
              verify = resolve;
            }),
        },
      });
      const transport = new FixtureTransport();
      const value = client(transport);
      await value.start();
      const command: Command = {
        command_id: '00000000-0000-4000-8000-000000000013',
        client_session_id: value.getSnapshot().handshake!.client_session_id,
        type: 'conversation.rename',
        expected_revision: '1',
        payload: { title: 'Synthetic rename' },
      };
      const pending = value.command(
        'conversation-a',
        command,
        'delayed-verification',
      );
      const assertion = expect(pending).rejects.toMatchObject({
        name: 'AbortError',
      });
      await value[transition]();
      verify(new ArrayBuffer(32));
      await assertion;
      expect(transport.counters.commands).toBe(0);
    },
  );
  it('treats an old dispatched command failure as uncertain without revoking a newer session', async () => {
    vi.stubGlobal('crypto', {
      subtle: { digest: () => Promise.resolve(new ArrayBuffer(32)) },
    });
    const transport = new FixtureTransport();
    let reject!: (reason: unknown) => void;
    vi.spyOn(transport, 'command').mockImplementationOnce(
      () =>
        new Promise((_resolve, failure) => {
          reject = failure;
        }),
    );
    const value = client(transport);
    await value.start();
    const command: Command = {
      command_id: '00000000-0000-4000-8000-000000000014',
      client_session_id: value.getSnapshot().handshake!.client_session_id,
      type: 'conversation.rename',
      expected_revision: '1',
      payload: { title: 'Synthetic rename' },
    };
    const pending = value.command(
      'conversation-a',
      command,
      'old-authentication',
    );
    const assertion = expect(pending).rejects.toMatchObject({
      code: 'operation_uncertain',
    });
    await flush();
    await value.reconnect();
    reject({ code: 'session_expired', status: 401 });
    await assertion;
    expect(value.getSnapshot().status).toBe('ready');
    expect(value.getSnapshot().handshake).not.toBeNull();
  });
});
