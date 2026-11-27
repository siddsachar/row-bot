import { afterEach, expect, it, vi } from 'vitest';
import {
  listConversations,
  getConversation,
  getConversationLinks,
  getAnswerState,
  getApproval,
  getBuddy,
  getBuddyPack,
  getBuddyPacks,
  getGlobalBuddy,
  getGlobalBuddyPack,
  getDelivery,
  getPage,
  getScheduledPosts,
  getVideoEdits,
  saveDraft,
  sendConversationCommand,
  cancelUpload,
  cancelSubscriptionProbe,
  reviewMcpRuntime,
  readNativeTerminal,
  writeNativeTerminal,
  sendBrowserInput,
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

it('sends typed terminal keys and the reads that echo them on their own budget (B248)', async () => {
  vi.useFakeTimers();
  const proof: SessionProof = {
    client_session_id: crypto.randomUUID(),
    csrf_token: 'synthetic'.repeat(8),
  };
  const fetcher = vi.fn(async (url: string) => ({
    ok: true,
    json: async () =>
      url.endsWith('/input')
        ? { ok: true }
        : url.includes('/native/terminals/')
          ? {
              cursor: 0,
              latest: 0,
              truncated: false,
              frames: [],
              status: 'running',
            }
          : {
              conversation_id: 'conversation-a',
              revision: '1',
              text: '',
              attachments: [],
            },
  }));
  vi.stubGlobal('fetch', fetcher);
  // Sixty keys, each followed by a read of its echo, go out at once.
  const typed = Promise.all(
    Array.from({ length: 60 }, (_, index) =>
      Promise.all([
        writeNativeTerminal('', proof, 'terminal-a', { data: String(index) }),
        readNativeTerminal('', proof, 'terminal-a', index),
      ]),
    ),
  );
  for (let i = 0; i < 10; i++) await Promise.resolve();
  expect(fetcher).toHaveBeenCalledTimes(120);
  await typed;
  // Drafts and commands keep their whole budget.
  await Promise.all(
    Array.from({ length: 8 }, () =>
      saveDraft('', proof, 'conversation-a', {
        expected_revision: '1',
        text: '',
        attachment_refs: [],
      }),
    ),
  );
  expect(fetcher).toHaveBeenCalledTimes(128);
  expect(vi.getTimerCount()).toBe(0);
});

it('loads a sent-files card at once while background reads wait', async () => {
  vi.useFakeTimers();
  const fetcher = vi.fn(async () => ({
    ok: false,
    json: async () => ({
      type: 'urn:row-bot:error:not_found',
      title: 'not found',
      status: 404,
      code: 'not_found',
      request_id: crypto.randomUUID(),
      retryable: false,
      recovery: 'none',
    }),
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
  await getDelivery('', proof, 'delivery-a').catch(() => undefined);
  expect(fetcher).toHaveBeenCalledTimes(21);
  abort.abort();
  await vi.runAllTimersAsync();
  await queries;
  expect(vi.getTimerCount()).toBe(0);
});

it('sends the person’s keys in the Browser pane at typing speed, keeping the write budget', async () => {
  vi.useFakeTimers();
  const proof: SessionProof = {
    client_session_id: crypto.randomUUID(),
    csrf_token: 'synthetic'.repeat(8),
  };
  const fetcher = vi.fn(async (url: string) => ({
    ok: true,
    json: async () =>
      url.endsWith('/browser/input')
        ? { status: 'ok', copied: '' }
        : {
            conversation_id: 'conversation-a',
            revision: '1',
            text: '',
            attachments: [],
          },
  }));
  vi.stubGlobal('fetch', fetcher);
  // Forty keystrokes (a pasted-by-hand sign-in code) go out at once.
  const typed = Promise.all(
    Array.from({ length: 40 }, (_, index) =>
      sendBrowserInput('', proof, 'conversation-a', {
        events: [{ type: 'key', action: 'down', key: String(index % 10) }],
      }),
    ),
  );
  for (let i = 0; i < 10; i++) await Promise.resolve();
  expect(fetcher).toHaveBeenCalledTimes(40);
  await typed;
  // Drafts and commands keep their whole budget.
  await Promise.all(
    Array.from({ length: 8 }, () =>
      saveDraft('', proof, 'conversation-a', {
        expected_revision: '1',
        text: '',
        attachment_refs: [],
      }),
    ),
  );
  expect(fetcher).toHaveBeenCalledTimes(48);
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

it('follows a running video edit every second while background reads wait', async () => {
  vi.useFakeTimers();
  const fetcher = vi.fn(async (url: string) => ({
    ok: true,
    json: async () =>
      url.includes('/video-edits')
        ? { items: [], running: 0 }
        : { items: [], has_more: false, next_cursor: null },
  }));
  vi.stubGlobal('fetch', fetcher);
  const proof: SessionProof = {
    client_session_id: crypto.randomUUID(),
    csrf_token: 'synthetic'.repeat(8),
  };
  const abort = new AbortController();
  // Far more background reads than the query budget lets through meanwhile.
  const queries = Promise.allSettled(
    Array.from({ length: 60 }, () =>
      listConversations('', proof, 50, undefined, abort.signal),
    ),
  );
  for (let i = 0; i < 10; i++) await Promise.resolve();
  expect(fetcher).toHaveBeenCalledTimes(20);
  // The card reads its edit once a second for ten seconds; none waits.
  for (let second = 0; second < 10; second++) {
    const read = getVideoEdits('', proof, 'conversation-a');
    for (let i = 0; i < 10; i++) await Promise.resolve();
    expect(
      fetcher.mock.calls.filter(([url]) =>
        String(url).startsWith('/api/v1/video-edits'),
      ),
    ).toHaveLength(second + 1);
    await read.catch(() => undefined);
    await vi.advanceTimersByTimeAsync(1000);
  }
  abort.abort();
  await vi.runAllTimersAsync();
  await queries;
  expect(vi.getTimerCount()).toBe(0);
});

it('loads a new approval card at once while the reads a reply sets off wait', async () => {
  vi.useFakeTimers();
  const fetcher = vi.fn(async (url: string) => ({
    ok: true,
    json: async () =>
      url.includes('/approvals/')
        ? {
            id: 'approval-b',
            status: 'pending',
            revision: '0',
            policy_revision: '1',
            nonce: 'synthetic'.repeat(4),
          }
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
  const approval = await getApproval('', proof, 'approval-b');
  expect(approval.id).toBe('approval-b');
  expect(fetcher).toHaveBeenCalledTimes(21);
  abort.abort();
  await vi.runAllTimersAsync();
  await queries;
  expect(vi.getTimerCount()).toBe(0);
});

it('reads a proposed conversation’s card at once while the reads a reply sets off wait (5.3.1 C)', async () => {
  vi.useFakeTimers();
  const fetcher = vi.fn(async (url: string) => ({
    ok: true,
    json: async () =>
      url.endsWith('/links')
        ? {
            schema_version: 1,
            items: [
              {
                link_id: 'a'.repeat(32),
                title: 'Make the demo video',
                conversation_id: null,
                state: 'proposed',
                proposed: true,
                mode: 'fresh',
              },
            ],
          }
        : { items: [], has_more: false, next_cursor: null },
  }));
  vi.stubGlobal('fetch', fetcher);
  const proof: SessionProof = {
    client_session_id: crypto.randomUUID(),
    csrf_token: 'synthetic'.repeat(8),
  };
  const abort = new AbortController();
  // The query budget is spent: a 25-page library read waits for tokens.
  const queries = Promise.allSettled(
    Array.from({ length: 25 }, () =>
      listConversations('', proof, 50, undefined, abort.signal),
    ),
  );
  // The card offers Start and Not now only once it has its state: that read never waits.
  const page = await getConversationLinks('', proof, 'conversation-a');
  expect(page.items[0].state).toBe('proposed');
  expect(fetcher).toHaveBeenCalledTimes(21);
  abort.abort();
  await vi.runAllTimersAsync();
  await queries;
  expect(vi.getTimerCount()).toBe(0);
});

it('reads a reply’s ticks at once while the reads a reply sets off wait (F3)', async () => {
  vi.useFakeTimers();
  const fetcher = vi.fn(async (url: string) => ({
    ok: true,
    json: async () =>
      url.endsWith('/answer-state')
        ? {
            conversation_id: 'conversation-a',
            revision: 'r1',
            ticks: {},
            choices: {},
          }
        : { items: [], has_more: false, next_cursor: null },
  }));
  vi.stubGlobal('fetch', fetcher);
  const proof: SessionProof = {
    client_session_id: crypto.randomUUID(),
    csrf_token: 'synthetic'.repeat(8),
  };
  const abort = new AbortController();
  // The query budget is spent: a 25-page library read waits for tokens.
  const queries = Promise.allSettled(
    Array.from({ length: 25 }, () =>
      listConversations('', proof, 50, undefined, abort.signal),
    ),
  );
  // Before a tick is saved its chat's state is read: that read never waits.
  const state = await getAnswerState('', proof, 'conversation-a');
  expect(state.revision).toBe('r1');
  expect(fetcher).toHaveBeenCalledTimes(21);
  abort.abort();
  await vi.runAllTimersAsync();
  await queries;
  expect(vi.getTimerCount()).toBe(0);
});

it('serves a read that waited before a newer one, even when its timer runs late', async () => {
  vi.useFakeTimers();
  // The page's clock, moved by hand: a busy page runs a waiting read's timer
  // late, and a fresh read can arrive in that gap.
  let now = 0;
  vi.spyOn(performance, 'now').mockImplementation(() => now);
  const order: string[] = [];
  const fetcher = vi.fn(async (url: string) => {
    order.push(url.includes('cursor=waited') ? 'waited' : 'newer');
    return {
      ok: true,
      json: async () => ({ items: [], has_more: false, next_cursor: null }),
    };
  });
  vi.stubGlobal('fetch', fetcher);
  const proof: SessionProof = {
    client_session_id: crypto.randomUUID(),
    csrf_token: 'synthetic'.repeat(8),
  };
  const abort = new AbortController();
  // The query budget is spent by 20 library reads.
  await Promise.all(
    Array.from({ length: 20 }, () =>
      listConversations('', proof, 50, undefined, abort.signal),
    ),
  );
  // A library page waits for the next token...
  const waited = listConversations('', proof, 50, 'waited', abort.signal);
  await vi.advanceTimersByTimeAsync(0);
  // ...which comes, but its timer hasn't run yet when a newer read arrives.
  now += 600;
  const later = Promise.allSettled([
    listConversations('', proof, 50, undefined, abort.signal),
  ]);
  await vi.advanceTimersByTimeAsync(0);
  expect(order.slice(20)).toEqual([]);
  await vi.advanceTimersByTimeAsync(600);
  await waited;
  // The token went to the read that was waiting first.
  expect(order[20]).toBe('waited');
  abort.abort();
  await vi.runAllTimersAsync();
  await later;
  expect(vi.getTimerCount()).toBe(0);
});

it('reads a page card’s page at once while background reads wait (N5)', async () => {
  vi.useFakeTimers();
  const fetcher = vi.fn(async (url: string) => ({
    ok: true,
    json: async () =>
      url.includes('/pages/')
        ? {
            page_id: 'page-1234abcd',
            conversation_id: 'conversation-a',
            conversation_title: 'Beta',
            title: 'Sales by week',
            version: 1,
            libraries: [],
            created_at: '2026-11-15T10:00:00Z',
            updated_at: '2026-11-15T10:00:00Z',
            state_revision: 0,
            pending: 0,
            decisions: [],
          }
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
  const view = await getPage('', proof, 'page-1234abcd');
  expect(view.title).toBe('Sales by week');
  expect(fetcher).toHaveBeenCalledTimes(21);
  abort.abort();
  await vi.runAllTimersAsync();
  await queries;
  expect(vi.getTimerCount()).toBe(0);
});

it('reads the sidebar Buddy’s status and look at once while background reads wait (R9)', async () => {
  vi.useFakeTimers();
  const fetcher = vi.fn(async (_url: string) => ({
    ok: true,
    json: async () => ({ items: [], has_more: false, next_cursor: null }),
  }));
  vi.stubGlobal('fetch', fetcher);
  const proof: SessionProof = {
    client_session_id: crypto.randomUUID(),
    csrf_token: 'synthetic'.repeat(8),
  };
  const abort = new AbortController();
  // A screen's worth of reads uses up the background budget first.
  const queries = Promise.allSettled(
    Array.from({ length: 25 }, () =>
      listConversations('', proof, 50, undefined, abort.signal),
    ),
  );
  await Promise.allSettled([
    getBuddy('', proof, 'conversation-a'),
    getBuddyPacks('', proof, 'conversation-a'),
    getBuddyPack('', proof, 'conversation-a', 'tile'),
    getGlobalBuddy('', proof),
    getGlobalBuddyPack('', proof, 'tile'),
  ]);
  const buddy = fetcher.mock.calls
    .map(([url]) => String(url))
    .filter((url) => url.includes('/buddy'));
  expect(buddy).toHaveLength(5);
  // Only the first 20 background reads went; the rest still wait their turn.
  expect(fetcher).toHaveBeenCalledTimes(25);
  abort.abort();
  await vi.runAllTimersAsync();
  await queries;
  expect(vi.getTimerCount()).toBe(0);
});

it('reads Home’s scheduled posts at once while background reads wait (G3)', async () => {
  vi.useFakeTimers();
  const fetcher = vi.fn(async (url: string) => ({
    ok: true,
    json: async () =>
      url.includes('scheduled-posts')
        ? { schema_version: 1, items: [] }
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
  const posts = await getScheduledPosts('', proof);
  expect(posts.items).toEqual([]);
  expect(fetcher).toHaveBeenCalledTimes(21);
  abort.abort();
  await vi.runAllTimersAsync();
  await queries;
  expect(vi.getTimerCount()).toBe(0);
});
