import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import ConversationVoice from './ConversationVoice';
import { startVoiceOnOpen } from './new-chat';
import type { ClientController } from '../../api/controller';
import type { RealtimeTalkControlsProps } from './RealtimeTalkControls';
import { FakeAudio, FakeSocket } from '../voice/voice-test-fakes';

let realtime: RealtimeTalkControlsProps;
vi.mock('./RealtimeTalkControls', () => ({
  default: (props: RealtimeTalkControlsProps) => {
    realtime = props;
    return <span>Live voice controls</span>;
  },
}));
const audio = { current: new FakeAudio() };
vi.mock('../voice/voice-audio', () => ({
  BrowserVoiceAudio: vi.fn(function () {
    return audio.current;
  }),
  primeVoicePlayback: vi.fn(),
}));

const scope = {
  conversationId: 'conversation-a',
  clientSessionId: 'session',
  serverEpoch: 'epoch',
  selectionKey: '1',
};
const handle = {
  conversation_id: scope.conversationId,
  server_epoch: scope.serverEpoch,
  voice_session_id: 1,
  lease_id: '11111111-1111-4111-8111-111111111111',
};
const TICKET = 'ticket-xxxxxxxxxxxxxxxxxxxxxxxxxxxx';

function setup(extra: Record<string, unknown> = {}) {
  audio.current = new FakeAudio();
  const sockets: FakeSocket[] = [];
  const controller = {
    startTalk: vi.fn().mockResolvedValue({
      snapshot: {
        schema_version: 2,
        handle,
        state: 'listening',
        quiesced: true,
        expires_in_ms: 1,
        transport: 'row_bot_voice',
      },
      ticket: TICKET,
      ticket_expires_in_ms: 30000,
    }),
    stopTalk: vi.fn().mockResolvedValue({}),
    talkTicket: vi
      .fn()
      .mockResolvedValue({ ticket: TICKET, expires_in_ms: 30000 }),
    openVoiceSocket: vi.fn(() => {
      const socket = new FakeSocket();
      sockets.push(socket);
      return socket;
    }),
    startRealtime: vi
      .fn()
      .mockResolvedValue({ snapshot: { handle, run_id: null } }),
    voiceRun: vi.fn(),
    voiceControl: vi.fn(),
    realtimeExchange: vi.fn(),
    realtimeEvent: vi.fn(),
  };
  const props = {
    controller: controller as unknown as ClientController,
    scope,
    available: true,
    disabled: false,
    running: false,
    context: {
      conversation_revision: '2',
      model_selection: {
        provider_id: 'fixture',
        model_ref: 'model:fixture:chat',
      },
      write_targets: [
        {
          kind: 'artifact' as const,
          binding_id: 'binding',
          resource_id: 'artifact',
          binding_revision: '3',
          resource_revision: '4',
        },
      ],
    },
    targets: ['Project sketch'],
    onBusy: vi.fn(),
    focusComposer: vi.fn(),
    ...extra,
  };
  const view = render(<ConversationVoice {...props} />);
  return { controller, props, view, sockets };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function tapTalk() {
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Talk' }));
  });
}

it('one tap starts Row-Bot Voice with the chat’s exact context and opens the bar on its socket', async () => {
  const { controller, props, sockets } = setup();
  await tapTalk();
  const [, body] = controller.startTalk.mock.calls[0];
  expect(body).toMatchObject(props.context);
  expect(body.request_id).toMatch(/^[0-9a-f-]{36}$/);
  expect(sockets).toHaveLength(1);
  await act(async () => {
    sockets[0].open();
    sockets[0].emit({ type: 'state', state: 'listening', muted: false });
  });
  expect(sockets[0].json()[0]).toEqual({ type: 'hello', ticket: TICKET });
  expect(
    screen.getByRole('region', { name: 'Voice conversation' }),
  ).toHaveAttribute('data-state', 'listening');
  expect(
    screen.getByText(/Spoken requests can change Project sketch/),
  ).toBeVisible();
  // Typing stays possible: voice never holds the composer busy.
  expect(props.onBusy).not.toHaveBeenCalledWith(true);
});

it('type instead mutes the microphone and hands over to the composer', async () => {
  const { props, sockets } = setup();
  await tapTalk();
  await act(async () => sockets[0].open());
  fireEvent.click(screen.getByRole('button', { name: 'Type instead' }));
  expect(props.focusComposer).toHaveBeenCalled();
  expect(sockets[0].json().at(-1)).toEqual({ type: 'mute', muted: true });
  fireEvent.click(screen.getByRole('button', { name: 'Hide the keyboard' }));
  expect(sockets[0].json().at(-1)).toEqual({ type: 'mute', muted: false });
});

it('ending or leaving the chat stops the voice lease', async () => {
  const { controller, sockets, view } = setup();
  await tapTalk();
  await act(async () => sockets[0].open());
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'End voice' }));
  });
  expect(controller.stopTalk).toHaveBeenCalledWith(scope, handle);
  expect(
    screen.queryByRole('region', { name: 'Voice conversation' }),
  ).toBeNull();
  await tapTalk();
  view.unmount();
  expect(controller.stopTalk).toHaveBeenCalledTimes(2);
});

it('waits for the current reply before it can start', () => {
  setup({ running: true });
  expect(screen.getByRole('button', { name: 'Talk' })).toBeDisabled();
});

it('Live voice stays selectable beside Row-Bot Voice', async () => {
  const { controller } = setup({ liveAvailable: true });
  fireEvent.pointerDown(screen.getByRole('button', { name: 'Talk' }), {
    button: 0,
  });
  fireEvent.click(await screen.findByRole('menuitem', { name: /Live voice/ }));
  expect(screen.getByText('Live voice controls')).toBeVisible();
  const signal = new AbortController().signal;
  await realtime.exchange!(handle, 'v=0 synthetic', signal);
  expect(controller.realtimeExchange).toHaveBeenCalledWith(
    scope,
    handle,
    'v=0 synthetic',
    signal,
  );
  expect(controller.startTalk).not.toHaveBeenCalled();
});

it('a chat opened from Home’s Talk starts voice once, when it can (D3)', async () => {
  startVoiceOnOpen(scope.conversationId, 'talk');
  const first = setup({ available: false, context: null });
  expect(first.controller.startTalk).not.toHaveBeenCalled();
  await act(async () =>
    first.view.rerender(<ConversationVoice {...first.props} available />),
  );
  expect(first.controller.startTalk).not.toHaveBeenCalled();
  cleanup();
  const second = setup();
  await act(async () => undefined);
  expect(second.controller.startTalk).toHaveBeenCalledTimes(1);
  cleanup();
  const third = setup();
  await act(async () => undefined);
  expect(third.controller.startTalk).not.toHaveBeenCalled();
});
