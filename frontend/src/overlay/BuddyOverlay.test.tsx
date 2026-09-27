import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ClientController } from '../api/controller';
import type {
  ApprovalView,
  ClientState,
  ConversationView,
  TranscriptRow,
} from '../api/types';
import { createFakePlatform } from '../platform/fake';
import type { ClientPlatform } from '../platform';
import type { BuddySnapshot } from '../features/shell/BuddyControls';
import BuddyOverlay from './BuddyOverlay';

type Generation = NonNullable<
  NonNullable<ClientState['projection']>['generation']
>;
const conversation = (id: string, title: string): ConversationView =>
  ({
    id,
    title,
    revision: `${id}-r1`,
    category: 'chat',
    pinned: false,
    updated_at: id === 'c1' ? '2026-09-27T10:00:00Z' : '2026-09-27T09:00:00Z',
    resource_bindings: [],
    parent_conversation_id: null,
  }) as unknown as ConversationView;
const row = (
  id: string,
  role: TranscriptRow['role'],
  text: string,
): TranscriptRow =>
  ({ id, role, blocks: [{ type: 'markdown', id, text }] }) as TranscriptRow;
const generation = (changes: Partial<Generation> = {}): Generation =>
  ({
    conversation_id: 'c1',
    generation_id: 'g1',
    execution_id: 'e1',
    pass_id: 'p1',
    revision: '1',
    status: 'completed',
    quiesced: true,
    can_stop: false,
    cancel_requested: false,
    cleanup_complete: true,
    external_outcome: 'not_applicable',
    approval_id: null,
    ...changes,
  }) as Generation;
const buddy: BuddySnapshot = {
  schema_version: 1,
  revision: 'r',
  placement: 'docked',
  native_placement_retained: true,
  preferences: {
    visible: true,
    collapsed: false,
    display_name: 'Buddy',
    personality: 'warm_mystical',
    personality_description: '',
    bubble_verbosity: 'normal',
    animation_intensity: 'normal',
    pack_id: 'glyph',
  },
  status: {
    mood: 'curious',
    animation: 'idle',
    energy: 50,
    focus: 50,
    alert: 0,
    event_id: 1,
    label: 'Ready',
  },
};
const approval: ApprovalView = {
  id: 'approval-1',
  nonce: 'nonce-1',
  revision: '7',
  action_label: 'workspace_file_delete',
  reason: 'Delete notes.txt',
  summary: 'Delete a file',
  risk_class: 'medium',
  scope: 'Only this file',
  status: 'pending',
  safe_argument_summary: '',
  policy_revision: 'p',
  expires_at: null,
  requesting_trace_id: '',
} as unknown as ApprovalView;

const EMPTY = { text: '', attachments: [] };

function fakeController(opened: Partial<ClientState> = {}) {
  let state = {
    status: 'ready',
    error: null,
    connection: 'sse',
    handshake: { instance_id: 'instance', client_session_id: 'session' },
    conversations: [
      conversation('c1', 'Trip plan'),
      conversation('c2', 'Budget'),
    ],
    selectedConversationId: null,
    conversation: null,
    projection: null,
    workspace: null,
    activity: [],
    loadingConversation: false,
    loadingConversations: false,
    draftStatus: 'saved',
    revision: 0,
  } as unknown as ClientState;
  const listeners = new Set<() => void>();
  const drafts = new Map<string, typeof EMPTY>();
  const set = (patch: Partial<ClientState>) => {
    state = { ...state, ...patch, revision: state.revision + 1 };
    listeners.forEach((listener) => listener());
  };
  const controller = {
    getSnapshot: () => state,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    set,
    selectConversation: vi.fn(async (id: string) =>
      set({
        selectedConversationId: id,
        conversation: state.conversations.find((item) => item.id === id)!,
        projection: {
          rows: [
            row('u1', 'user', 'Plan a trip'),
            row('a1', 'assistant', '**Day one:** walk'),
          ],
          generation: generation(),
        } as ClientState['projection'],
        workspace: {
          conversation_id: id,
          controls: { model_selection: { model_ref: 'fixture:model' } },
          actions: [{ action: 'send', ready: true }],
          resources: [],
        } as unknown as ClientState['workspace'],
        ...opened,
      }),
    ),
    getDraft: (id: string) => drafts.get(id) ?? EMPTY,
    setDraft: vi.fn((id: string, draft: typeof EMPTY) => {
      drafts.set(id, draft);
      set({});
    }),
    intent: vi.fn(async (target: string | null) => ({
      status: 'accepted',
      command_id: 'command',
      conversation_id: target,
    })),
    approval: vi.fn(async () => approval),
    globalBuddy: vi.fn(async () => buddy),
    globalBuddyPack: vi.fn(async () => ({
      id: 'glyph',
      name: 'Glyph',
      revision: 'r',
      runtime: 'generated_motion_pack',
      available: false,
      generated: false,
      assets: [],
      animation_map: {},
    })),
    globalBuddyMedia: vi.fn(),
  };
  return controller;
}

function nativePlatform(
  changes: Partial<ClientPlatform> = {},
): ClientPlatform & { calls: string[] } {
  const platform = createFakePlatform({
    discover: {
      status: 'ok',
      value: {
        kind: 'pywebview',
        platform: 'windows',
        capabilities: ['buddy_placement', 'buddy_follow', 'main_window'],
      },
    },
    readBuddyTarget: {
      status: 'ok',
      value: { conversationId: 'c1', revision: 1 },
    },
    buddyPlacement: {
      status: 'ok',
      value: { placement: 'desktop', visible: true },
    },
    showMainWindow: { status: 'ok', value: null },
    moveWindow: true,
  });
  return Object.assign(platform, changes);
}

function renderOverlay(
  controller = fakeController(),
  platform: ClientPlatform = nativePlatform(),
  explicit: string | null = null,
) {
  render(
    <BuddyOverlay
      controller={controller as unknown as ClientController}
      platform={platform}
      explicitConversation={explicit}
    />,
  );
  return { controller, platform };
}

beforeEach(() => {
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    callback(0);
    return 1;
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete document.documentElement.dataset.buddyOverlayReady;
});

describe('BuddyOverlay', () => {
  it('follows the host target, shows the thread and reveals itself once', async () => {
    const platform = nativePlatform();
    const { controller } = renderOverlay(fakeController(), platform);
    await waitFor(() =>
      expect(controller.selectConversation).toHaveBeenCalledWith('c1'),
    );
    expect(
      await screen.findByRole('heading', { name: 'Trip plan' }),
    ).toBeVisible();
    expect(screen.getByText('Chat · Ready')).toBeVisible();
    // Markdown reads as plain text.
    expect(
      screen.getByRole('region', { name: 'Latest response' }),
    ).toHaveTextContent('Day one: walk');
    await waitFor(() =>
      expect(document.documentElement.dataset.buddyOverlayReady).toBeDefined(),
    );
    // The only placement call is "ready", once.
    expect(
      platform.calls.filter((call) => call === 'buddyPlacement'),
    ).toHaveLength(1);
    // An opened, finished conversation rests; it never celebrates.
    expect(document.querySelector('.buddy-overlay-avatar')).toHaveAttribute(
      'data-activity',
      'idle',
    );
  });

  it('switches when the main window changes conversation and the host hints', async () => {
    const readBuddyTarget = vi
      .fn()
      .mockResolvedValueOnce({
        status: 'ok',
        value: { conversationId: 'c1', revision: 1 },
      })
      .mockResolvedValue({
        status: 'ok',
        value: { conversationId: 'c2', revision: 2 },
      });
    const { controller } = renderOverlay(
      fakeController(),
      nativePlatform({ readBuddyTarget }),
    );
    await waitFor(() =>
      expect(controller.selectConversation).toHaveBeenCalledWith('c1'),
    );
    act(() => {
      window.dispatchEvent(new Event('row-bot-native-changed'));
    });
    await waitFor(() =>
      expect(controller.selectConversation).toHaveBeenLastCalledWith('c2'),
    );
    expect(
      await screen.findByRole('heading', { name: 'Budget' }),
    ).toBeVisible();
  });

  it('uses an explicit conversation, else the latest one, without a host', async () => {
    const browser = createFakePlatform();
    const first = renderOverlay(fakeController(), browser, 'c2');
    await waitFor(() =>
      expect(first.controller.selectConversation).toHaveBeenCalledWith('c2'),
    );
    cleanup();
    const second = renderOverlay(fakeController(), createFakePlatform());
    await waitFor(() =>
      expect(second.controller.selectConversation).toHaveBeenCalledWith('c1'),
    );
    // No native host: Dock and Hide are unavailable, the thread opens in a tab.
    expect(
      await screen.findByRole('button', { name: 'Dock Buddy' }),
    ).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Hide Buddy' })).toBeDisabled();
    const open = vi.spyOn(window, 'open').mockReturnValue(null);
    fireEvent.click(screen.getByRole('button', { name: 'Open full thread' }));
    await waitFor(() =>
      expect(open).toHaveBeenCalledWith(
        '/app-v2/conversations/c1',
        '_blank',
        'noopener',
      ),
    );
  });

  it('sends the draft with the thread model and targets, and keeps later typing', async () => {
    const controller = fakeController();
    renderOverlay(controller);
    const field = await screen.findByRole('textbox', { name: 'Buddy message' });
    await waitFor(() => expect(field).toBeEnabled());
    fireEvent.change(field, { target: { value: 'Add a museum' } });
    expect(controller.setDraft).toHaveBeenLastCalledWith('c1', {
      text: 'Add a museum',
      attachments: [],
    });
    fireEvent.keyDown(field, { key: 'Enter', shiftKey: true });
    expect(controller.intent).not.toHaveBeenCalled();
    fireEvent.keyDown(field, { key: 'Enter' });
    await waitFor(() =>
      expect(controller.intent).toHaveBeenCalledWith(
        'c1',
        'conversation.submit',
        expect.objectContaining({
          text: 'Add a museum',
          attachment_refs: [],
          model_selection: { model_ref: 'fixture:model' },
          write_targets: [],
        }),
        'c1-r1',
      ),
    );
    await waitFor(() => expect(controller.getDraft('c1').text).toBe(''));
  });

  it('streams with Stop and resumes an interrupted run', async () => {
    const controller = fakeController({
      projection: {
        rows: [
          row('u1', 'user', 'Plan'),
          row('a1', 'assistant', 'Working on it'),
        ],
        generation: generation({
          status: 'running',
          quiesced: false,
          can_stop: true,
        }),
      } as ClientState['projection'],
    });
    renderOverlay(controller);
    const stop = await screen.findByRole('button', { name: 'Stop' });
    expect(screen.getByText('Chat · Responding…')).toBeVisible();
    fireEvent.click(stop);
    await waitFor(() =>
      expect(controller.intent).toHaveBeenCalledWith(
        'c1',
        'conversation.stop',
        {},
        'c1-r1',
      ),
    );
    act(() =>
      controller.set({
        projection: {
          rows: [row('u1', 'user', 'Plan')],
          generation: generation({ status: 'interrupted' }),
        } as ClientState['projection'],
      }),
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Resume' }));
    await waitFor(() =>
      expect(controller.intent).toHaveBeenCalledWith(
        'c1',
        'conversation.resume',
        { model_selection: { model_ref: 'fixture:model' } },
        'c1-r1',
      ),
    );
  });

  it('never shows an older answer for a turn stopped before its reply', async () => {
    const controller = fakeController({
      projection: {
        rows: [
          row('u1', 'user', 'First'),
          row('a1', 'assistant', 'Old answer'),
          row('u2', 'user', 'Second'),
        ],
        generation: generation({ status: 'stopped' }),
      } as ClientState['projection'],
    });
    renderOverlay(controller);
    expect(await screen.findByText(/Stopped before a reply/)).toBeVisible();
    expect(screen.queryByText('Old answer')).toBeNull();
    expect(screen.getByText('Chat · Stopped')).toBeVisible();
  });

  it('keeps a message typed during a run and says why it did not send', async () => {
    const controller = fakeController({
      projection: {
        rows: [row('u1', 'user', 'Plan'), row('a1', 'assistant', 'Working')],
        generation: generation({
          status: 'running',
          quiesced: false,
          can_stop: true,
        }),
      } as ClientState['projection'],
    });
    renderOverlay(controller);
    const field = await screen.findByRole('textbox', { name: 'Buddy message' });
    fireEvent.change(field, { target: { value: 'One more thing' } });
    fireEvent.keyDown(field, { key: 'Enter' });
    expect(
      await screen.findByText(
        'Row-Bot is still working. Send when it finishes, or Stop it.',
      ),
    ).toHaveAttribute('role', 'status');
    expect(controller.intent).not.toHaveBeenCalled();
    expect(controller.getDraft('c1').text).toBe('One more thing');
    // The hint leaves with the run.
    act(() =>
      controller.set({
        projection: {
          rows: [row('u1', 'user', 'Plan'), row('a1', 'assistant', 'Done')],
          generation: generation(),
        } as ClientState['projection'],
      }),
    );
    await waitFor(() =>
      expect(screen.queryByText(/Row-Bot is still working/)).toBeNull(),
    );
  });

  it('says a message has no reply when nothing is running (after a restart)', async () => {
    const controller = fakeController({
      projection: {
        rows: [
          row('u1', 'user', 'Earlier'),
          row('a1', 'assistant', 'Old'),
          row('u2', 'user', 'Lost in a restart'),
        ],
        generation: null,
      } as unknown as ClientState['projection'],
    });
    renderOverlay(controller);
    expect(
      await screen.findByText('Your last message has no reply.'),
    ).toBeVisible();
    expect(screen.queryByText('Old')).toBeNull();
  });

  it('names the running step while no words have arrived', async () => {
    const controller = fakeController({
      projection: {
        rows: [row('u1', 'user', 'Weather?')],
        generation: generation({
          status: 'running',
          quiesced: false,
          can_stop: true,
        }),
      } as ClientState['projection'],
      activity: [
        {
          event: {
            type: 'tool.activity',
            event_id: 'e1',
            payload: {
              tool_name: 'web_search',
              status: 'pending',
              safe_input: JSON.stringify({ query: 'Oslo weather' }),
            },
          },
        },
      ] as unknown as ClientState['activity'],
    });
    renderOverlay(controller);
    expect(
      await screen.findByText('Searching the web · “Oslo weather”'),
    ).toBeVisible();
    expect(document.querySelector('.buddy-overlay-avatar')).toHaveAttribute(
      'data-activity',
      'tool',
    );
  });

  it('settles an approval with Deny, Approve or Mod+Enter, and Details opens the thread', async () => {
    const controller = fakeController({
      projection: {
        rows: [row('u1', 'user', 'Tidy up')],
        generation: generation({
          status: 'waiting_approval',
          quiesced: true,
          approval_id: 'approval-1',
        }),
      } as ClientState['projection'],
    });
    const platform = nativePlatform();
    renderOverlay(controller, platform);
    const strip = await screen.findByRole('complementary', {
      name: 'Approval required for workspace_file_delete',
    });
    expect(strip).toHaveTextContent('Delete notes.txt');
    fireEvent.click(screen.getByRole('button', { name: 'Details' }));
    await waitFor(() => expect(platform.calls).toContain('showMainWindow'));
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Buddy message' }), {
      key: 'Enter',
      ctrlKey: true,
    });
    await waitFor(() =>
      expect(controller.intent).toHaveBeenCalledWith(
        'approval-1',
        'approval.resolve',
        { decision: 'approve', nonce: 'nonce-1' },
        '7',
      ),
    );
    // One decision per approval.
    expect(screen.getByRole('button', { name: 'Approve' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Deny' })).toBeDisabled();
  });

  it('docks, hides and opens the full thread through the host', async () => {
    const buddyPlacement = vi.fn().mockResolvedValue({
      status: 'ok',
      value: { placement: 'desktop', visible: true },
    });
    const showMainWindow = vi
      .fn()
      .mockResolvedValue({ status: 'ok', value: null });
    renderOverlay(
      fakeController(),
      nativePlatform({ buddyPlacement, showMainWindow }),
    );
    await screen.findByRole('heading', { name: 'Trip plan' });
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Dock Buddy' })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Dock Buddy' }));
    fireEvent.click(screen.getByRole('button', { name: 'Hide Buddy' }));
    fireEvent.click(screen.getByRole('button', { name: 'Open full thread' }));
    await waitFor(() => expect(showMainWindow).toHaveBeenCalledWith('c1'));
    expect(buddyPlacement).toHaveBeenCalledWith('dock');
    expect(buddyPlacement).toHaveBeenCalledWith('hide');
    buddyPlacement.mockResolvedValue({ status: 'unavailable', reason: 'x' });
    fireEvent.click(screen.getByRole('button', { name: 'Hide Buddy' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Buddy could not hide.',
    );
  });

  it('moves the window from the header, never from its buttons', async () => {
    const moveWindow = vi.fn(() => true);
    renderOverlay(fakeController(), nativePlatform({ moveWindow }));
    await screen.findByRole('heading', { name: 'Trip plan' });
    await waitFor(() =>
      expect(document.querySelector('.buddy-overlay-header')).toHaveAttribute(
        'data-draggable',
        'true',
      ),
    );
    const header = document.querySelector('.buddy-overlay-header')!;
    Object.defineProperty(header, 'setPointerCapture', { value: vi.fn() });
    fireEvent.pointerDown(screen.getByRole('button', { name: 'Hide Buddy' }), {
      button: 0,
      pointerId: 1,
      clientX: 300,
      clientY: 10,
      screenX: 1300,
      screenY: 410,
    });
    fireEvent.pointerMove(header, {
      pointerId: 1,
      screenX: 1360,
      screenY: 450,
    });
    expect(moveWindow).not.toHaveBeenCalled();
    fireEvent.pointerDown(header, {
      button: 0,
      pointerId: 2,
      clientX: 40,
      clientY: 12,
      screenX: 1040,
      screenY: 412,
    });
    fireEvent.pointerMove(header, {
      pointerId: 2,
      screenX: 1041,
      screenY: 412,
    });
    expect(moveWindow).not.toHaveBeenCalled();
    fireEvent.pointerMove(header, {
      pointerId: 2,
      screenX: 1100,
      screenY: 500,
    });
    fireEvent.pointerUp(header, { pointerId: 2, screenX: 1100, screenY: 500 });
    // The window's corner follows the pointer: screen point minus grab offset.
    expect(moveWindow).toHaveBeenLastCalledWith(1060, 488);
  });
});
