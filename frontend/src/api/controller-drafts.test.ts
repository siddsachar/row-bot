import { afterEach, expect, it, vi } from 'vitest';
import { ClientController, type DraftChannel } from './controller';
import { FixtureTransport } from './fixtures';
import type { DraftSave, DraftView } from './types';

/** A server draft store shared by two windows of the app. */
class SharedDraftTransport extends FixtureTransport {
  constructor(private readonly store: Map<string, DraftView>) {
    super({ conversationCount: 4 });
  }
  reads = vi.fn();
  draft(id: string): Promise<DraftView> {
    this.reads(id);
    return Promise.resolve(
      this.store.get(id) ?? {
        conversation_id: id,
        revision: '0',
        text: '',
        attachments: [],
      },
    );
  }
  saveDraft(id: string, body: DraftSave): Promise<DraftView> {
    const current = this.store.get(id)?.revision ?? '0';
    if (body.expected_revision !== current)
      return Promise.reject({ code: 'draft_revision_conflict' });
    const saved = {
      conversation_id: id,
      revision: String(Number(current) + 1),
      text: body.text,
      attachments: [],
    };
    this.store.set(id, saved);
    return Promise.resolve(saved);
  }
}

class FakeChannel implements DraftChannel {
  static peers = new Set<FakeChannel>();
  listeners = new Set<(event: MessageEvent) => void>();
  posted: unknown[] = [];
  closed = false;
  constructor() {
    FakeChannel.peers.add(this);
  }
  postMessage(message: unknown) {
    this.posted.push(message);
    for (const peer of FakeChannel.peers)
      if (peer !== this && !peer.closed)
        peer.listeners.forEach((listener) =>
          listener(new MessageEvent('message', { data: message })),
        );
  }
  addEventListener(_type: string, listener: EventListener) {
    this.listeners.add(listener as (event: MessageEvent) => void);
  }
  removeEventListener(_type: string, listener: EventListener) {
    this.listeners.delete(listener as (event: MessageEvent) => void);
  }
  close() {
    this.closed = true;
    FakeChannel.peers.delete(this);
  }
}

const clients: ClientController[] = [];
async function flush() {
  for (let i = 0; i < 40; i++) await Promise.resolve();
}
async function windowOn(store: Map<string, DraftView>, id: string) {
  const transport = new SharedDraftTransport(store);
  const value = new ClientController(transport, () => 1);
  clients.push(value);
  value.setVisible(false);
  await value.start();
  await value.selectConversation(id);
  return { value, transport };
}
afterEach(async () => {
  clients.splice(0).forEach((value) => value.dispose());
  FakeChannel.peers.clear();
  await flush();
});

it('adopts a newer server draft only while this window has no unsaved work', async () => {
  const store = new Map<string, DraftView>();
  const main = await windowOn(store, 'conversation-a');
  const buddy = await windowOn(store, 'conversation-a');
  buddy.value.setDraft('conversation-a', {
    text: 'From Buddy',
    attachments: [],
  });
  await flush();
  expect(store.get('conversation-a')?.text).toBe('From Buddy');

  const before = main.value.getSnapshot().revision;
  expect(await main.value.refreshDraft('conversation-a')).toBe(true);
  expect(main.value.getDraft('conversation-a').text).toBe('From Buddy');
  // Adopting notifies, so a mounted composer shows the new text.
  expect(main.value.getSnapshot().revision).toBeGreaterThan(before);
  // Nothing new: no second adoption.
  expect(await main.value.refreshDraft('conversation-a')).toBe(false);

  // The main window's later edit saves on top of the adopted revision.
  main.value.setDraft('conversation-a', { text: 'From main', attachments: [] });
  await flush();
  expect(store.get('conversation-a')?.text).toBe('From main');
  expect(main.value.getSnapshot().draftStatus).toBe('saved');
});

it('never replaces unsaved typing, an in-flight save or a conflict', async () => {
  const store = new Map<string, DraftView>();
  const main = await windowOn(store, 'conversation-a');
  store.set('conversation-a', {
    conversation_id: 'conversation-a',
    revision: '5',
    text: 'Elsewhere',
    attachments: [],
  });
  const internals = main.value as unknown as {
    dirtyDrafts: Set<string>;
    draftStates: Map<string, string>;
  };
  internals.dirtyDrafts.add('conversation-a');
  expect(await main.value.refreshDraft('conversation-a')).toBe(false);
  internals.dirtyDrafts.delete('conversation-a');
  internals.draftStates.set('conversation-a', 'conflict');
  expect(await main.value.refreshDraft('conversation-a')).toBe(false);
  internals.draftStates.set('conversation-a', 'saved');
  // Unknown conversations (never opened here) are not read at all.
  main.transport.reads.mockClear();
  expect(await main.value.refreshDraft('conversation-9')).toBe(false);
  expect(main.transport.reads).not.toHaveBeenCalled();
  expect(await main.value.refreshDraft('conversation-a')).toBe(true);
  expect(main.value.getDraft('conversation-a').text).toBe('Elsewhere');
});

it('announces saved drafts by id on the shared channel and refreshes on announcements', async () => {
  const store = new Map<string, DraftView>();
  const main = await windowOn(store, 'conversation-a');
  const buddy = await windowOn(store, 'conversation-a');
  const mainChannel = new FakeChannel();
  const buddyChannel = new FakeChannel();
  const unbind = main.value.bindDraftChannel(mainChannel);
  buddy.value.bindDraftChannel(buddyChannel);

  buddy.value.setDraft('conversation-a', {
    text: 'Typed in Buddy',
    attachments: [],
  });
  await flush();
  const instance = buddy.value.getSnapshot().handshake?.instance_id;
  // Ids only: the text never crosses the channel.
  expect(buddyChannel.posted).toEqual([
    {
      type: 'draft',
      conversationId: 'conversation-a',
      revision: '1',
      instance,
    },
  ]);
  expect(main.value.getDraft('conversation-a').text).toBe('Typed in Buddy');

  // Another server instance's announcement is ignored.
  main.transport.reads.mockClear();
  mainChannel.listeners.forEach((listener) =>
    listener(
      new MessageEvent('message', {
        data: {
          type: 'draft',
          conversationId: 'conversation-a',
          instance: 'other',
        },
      }),
    ),
  );
  await flush();
  expect(main.transport.reads).not.toHaveBeenCalled();

  unbind();
  expect(mainChannel.closed).toBe(true);
  buddy.value.dispose();
  expect(buddyChannel.closed).toBe(true);
});

it('asks the same session for a fresh native attestation', async () => {
  const transport = new SharedDraftTransport(new Map());
  const value = new ClientController(transport, () => 1);
  clients.push(value);
  expect(await value.nativeAttestation()).toBeNull();
  value.setVisible(false);
  await value.start();
  const handshake = value.getSnapshot().handshake!;
  const connect = vi.spyOn(transport, 'connect');
  connect.mockResolvedValueOnce({
    ...(await transport.connect()),
    native_adapter: {
      available: true,
      proof_required: true,
      instance_id: handshake.instance_id,
      attestation: 'f'.repeat(32),
    },
  });
  expect(await value.nativeAttestation()).toBe('f'.repeat(32));
  // A different session (the old one expired) is not this window's.
  connect.mockResolvedValueOnce({
    ...(await transport.connect()),
    client_session_id: '00000000-0000-4000-8000-000000000000',
  });
  expect(await value.nativeAttestation()).toBeNull();
});
