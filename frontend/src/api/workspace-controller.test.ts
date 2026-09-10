import { afterEach, expect, it, vi } from 'vitest';
import { ClientController } from './controller';
import { FixtureTransport } from './fixtures';
import type { ConversationOpenView, DraftSave, DraftView } from './types';

class DraftTransport extends FixtureTransport {
  saved = new Map<string, DraftView>();
  writer: (id: string, body: DraftSave) => Promise<DraftView> = async (
    id,
    body,
  ) => this.commit(id, body);
  commit(id: string, body: DraftSave): DraftView {
    const previous = this.saved.get(id) ?? {
      conversation_id: id,
      revision: '0',
      text: '',
      attachments: [],
    };
    if (previous.revision !== body.expected_revision)
      throw { code: 'draft_revision_conflict' };
    const result = {
      ...previous,
      revision: String(Number(previous.revision) + 1),
      text: body.text,
    };
    this.saved.set(id, result);
    return result;
  }
  async draft(id: string): Promise<DraftView> {
    return structuredClone(
      this.saved.get(id) ?? {
        conversation_id: id,
        revision: '0',
        text: '',
        attachments: [],
      },
    );
  }
  saveDraft(id: string, body: DraftSave): Promise<DraftView> {
    return this.writer(id, body);
  }
}
const clients: ClientController[] = [];
async function start() {
  const transport = new DraftTransport(),
    controller = new ClientController(transport, () => 1);
  clients.push(controller);
  controller.setVisible(false);
  await controller.start();
  await controller.selectConversation('conversation-a');
  return { transport, controller };
}
async function flush() {
  for (let i = 0; i < 25; i++) await Promise.resolve();
}
afterEach(() => clients.splice(0).forEach((client) => client.dispose()));

it.each([false, true])(
  'opens one bounded bundle, refreshes advanced cuts (%s) and visibility return',
  async (advanced) => {
    class OpenTransport extends DraftTransport {
      workspace = vi.fn(async (id: string) => ({
        conversation_id: id,
        revision: '0',
        controls: {},
        profiles: [],
        resources: [],
        actions: [],
      }));
      openConversation = vi.fn(
        async (id: string): Promise<ConversationOpenView> => ({
          conversation: await super.getConversation(id),
          history: await super.getTranscript(id),
          workspace: await this.workspace(id),
          draft: await this.draft(id),
        }),
      );
      override async subscribe(id: string, signal?: AbortSignal) {
        const result = await super.subscribe(id, signal);
        if (advanced)
          result.snapshot.projection_revision = String(
            Number(result.snapshot.projection_revision) + 1,
          );
        return result;
      }
    }
    const transport = new OpenTransport();
    const direct = vi.spyOn(transport, 'getConversation');
    const controller = new ClientController(transport, () => 1);
    clients.push(controller);
    await controller.start();
    await controller.selectConversation('conversation-a');
    await flush();
    expect(transport.openConversation).toHaveBeenCalledTimes(1);
    expect(direct).toHaveBeenCalledTimes(advanced ? 1 : 0);
    expect(transport.workspace).toHaveBeenCalledTimes(advanced ? 2 : 1);
    expect(controller.getSnapshot().status).toBe('ready');
    controller.setVisible(false);
    controller.setVisible(true);
    await flush();
    expect(transport.workspace).toHaveBeenCalledTimes(advanced ? 3 : 2);
    expect(direct).toHaveBeenCalledTimes(advanced ? 2 : 1);
  },
);

it('adopts a newer saved server draft on return when local text is clean', async () => {
  const { transport, controller } = await start();
  controller.setDraft('conversation-a', {
    text: 'Saved locally',
    attachments: [],
  });
  await flush();
  expect(controller.hasUnsavedDraft()).toBe(false);
  await controller.selectConversation('conversation-2');
  transport.saved.set('conversation-a', {
    conversation_id: 'conversation-a',
    revision: '2',
    text: 'New retained-client edit',
    attachments: [],
  });
  await controller.selectConversation('conversation-a');
  expect(controller.getDraft('conversation-a').text).toBe(
    'New retained-client edit',
  );
  controller.setDraft('conversation-a', {
    text: 'After refresh',
    attachments: [],
  });
  await flush();
  expect(transport.saved.get('conversation-a')?.text).toBe('After refresh');
  expect(controller.getSnapshot().draftStatus).toBe('saved');
});

it('rejects a foreign draft before it can replace the selected conversation draft', async () => {
  const { transport, controller } = await start();
  transport.draft = async () => ({
    conversation_id: 'foreign-chat',
    revision: '2',
    text: 'Foreign text',
    attachments: [],
  });
  await controller.selectConversation('conversation-a');
  expect(controller.getSnapshot().status).toBe('incompatible');
  expect(controller.getDraft('conversation-a').text).not.toBe('Foreign text');
});

it.each(['', 'Saved in another client'])(
  'retains typing during initial loading and handles the saved draft (%s)',
  async (savedText) => {
    const transport = new DraftTransport();
    let loaded!: (value: DraftView) => void;
    transport.draft = () =>
      new Promise((resolve) => {
        loaded = resolve;
      });
    const writer = vi.spyOn(transport, 'saveDraft');
    const controller = new ClientController(transport, () => 1);
    clients.push(controller);
    controller.setVisible(false);
    await controller.start();
    const opening = controller.selectConversation('conversation-a');
    await flush();
    controller.setDraft('conversation-a', {
      text: 'Typed while opening',
      attachments: [],
    });
    expect(controller.getSnapshot().draftStatus).toBe('saving');
    expect(writer).not.toHaveBeenCalled();
    loaded({
      conversation_id: 'conversation-a',
      revision: '0',
      text: savedText,
      attachments: [],
    });
    await opening;
    await flush();
    expect(controller.getDraft('conversation-a').text).toBe(
      'Typed while opening',
    );
    if (savedText) {
      expect(controller.getSnapshot().draftStatus).toBe('conflict');
      expect(writer).not.toHaveBeenCalled();
      expect(controller.hasUnsavedDraft()).toBe(true);
    } else {
      expect(writer).toHaveBeenCalledTimes(1);
      expect(transport.saved.get('conversation-a')?.text).toBe(
        'Typed while opening',
      );
      expect(controller.getSnapshot().draftStatus).toBe('saved');
      expect(controller.hasUnsavedDraft()).toBe(false);
    }
  },
);

it('retains dirty local text across navigation and resolves only the reviewed server revision', async () => {
  const { transport, controller } = await start();
  transport.saved.set('conversation-a', {
    conversation_id: 'conversation-a',
    revision: '1',
    text: 'Other client',
    attachments: [],
  });
  controller.setDraft('conversation-a', {
    text: 'Local unsaved input',
    attachments: [],
  });
  await flush();
  expect(controller.getSnapshot().draftStatus).toBe('conflict');
  await controller.selectConversation('conversation-2');
  await controller.selectConversation('conversation-a');
  expect(controller.getDraft('conversation-a').text).toBe(
    'Local unsaved input',
  );
  expect(controller.hasUnsavedDraft()).toBe(true);
  await expect(
    controller.resolveDraft('conversation-a', '0', true),
  ).rejects.toMatchObject({ code: 'draft_revision_conflict' });
  expect(transport.saved.get('conversation-a')?.text).toBe('Other client');
  await controller.resolveDraft('conversation-a', '1', true);
  expect(transport.saved.get('conversation-a')?.text).toBe(
    'Local unsaved input',
  );
  expect(controller.hasUnsavedDraft()).toBe(false);
  transport.saved.set('conversation-a', {
    conversation_id: 'conversation-a',
    revision: '3',
    text: 'Reviewed replacement',
    attachments: [],
  });
  controller.setDraft('conversation-a', {
    text: 'Second local conflict',
    attachments: [],
  });
  await flush();
  await controller.resolveDraft('conversation-a', '3', false);
  expect(controller.getDraft('conversation-a').text).toBe(
    'Reviewed replacement',
  );
  expect(controller.getSnapshot().draftStatus).toBe('saved');
});

it('does not mark selected B saved when an older A write finally succeeds', async () => {
  const { transport, controller } = await start();
  let finish!: (value: DraftView) => void;
  transport.writer = (id, _body) =>
    id === 'conversation-a'
      ? new Promise((resolve) => {
          finish = resolve;
        })
      : Promise.reject({ code: 'draft_revision_conflict' });
  controller.setDraft('conversation-a', { text: 'A pending', attachments: [] });
  await controller.selectConversation('conversation-2');
  controller.setDraft('conversation-2', {
    text: 'B conflict',
    attachments: [],
  });
  await flush();
  expect(controller.getSnapshot().draftStatus).toBe('conflict');
  finish({
    conversation_id: 'conversation-a',
    revision: '1',
    text: 'A pending',
    attachments: [],
  });
  await flush();
  expect(controller.getSnapshot().selectedConversationId).toBe(
    'conversation-2',
  );
  expect(controller.getSnapshot().draftStatus).toBe('conflict');
  expect(controller.getDraft('conversation-2').text).toBe('B conflict');
});

it('keeps conflict recovery open when replacing the reviewed draft fails', async () => {
  const { transport, controller } = await start();
  transport.writer = async () => {
    throw { code: 'draft_save_failed' };
  };
  controller.setDraft('conversation-a', { text: 'Keep me', attachments: [] });
  await flush();
  await expect(
    controller.resolveDraft('conversation-a', '0', true),
  ).rejects.toMatchObject({ code: 'draft_save_failed' });
  expect(controller.hasUnsavedDraft()).toBe(true);
  expect(controller.getDraft('conversation-a').text).toBe('Keep me');
});
