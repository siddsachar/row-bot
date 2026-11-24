import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import ConversationVoice from './ConversationVoice';
import { startVoiceOnOpen } from './new-chat';
import type { ClientController } from '../../api/controller';
import { FakeAudio, FakeSocket, audioFrame } from '../voice/voice-test-fakes';
import {
  setWakeListener,
  wakeOpenChat,
  type WakeListener,
} from '../voice/wake-word';
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
const live = (
  id: 'openai' | 'gemini' | 'grok',
  label: string,
  company: string,
  price: string,
  available: boolean,
) => ({
  id,
  label,
  company,
  price,
  price_checked: '22 Nov 2026',
  available,
  reason: available
    ? ('available' as const)
    : ('realtime_key_missing' as const),
});
const liveProviders = [
  live('openai', 'OpenAI Realtime', 'OpenAI', 'about $0.10 a minute', true),
  // Gemini Live sees what the person shares on camera or screen.
  {
    ...live('gemini', 'Gemini Live', 'Google', 'about $0.02 a minute', true),
    shares: true,
  },
  live('grok', 'Grok Voice', 'xAI', 'about $0.08 a minute', false),
];

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
    startRealtime: vi.fn().mockResolvedValue({
      snapshot: {
        schema_version: 1,
        handle,
        state: 'connecting',
        quiesced: true,
        expires_in_ms: 1,
        run_id: null,
        transport: 'gemini_live',
      },
      exchange_available: false,
      ticket: TICKET,
      ticket_expires_in_ms: 30000,
    }),
    stopRealtime: vi.fn().mockResolvedValue({}),
    realtimeTicket: vi
      .fn()
      .mockResolvedValue({ ticket: TICKET, expires_in_ms: 30000 }),
    openLiveSocket: vi.fn(() => {
      const socket = new FakeSocket();
      sockets.push(socket);
      return socket;
    }),
    realtimeExchange: vi.fn(),
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
  const view = render(<ConversationVoice {...props} />, {
    wrapper: MemoryRouter,
  });
  return { controller, props, view, sockets };
}

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.clearAllMocks();
});

async function openMenu() {
  fireEvent.pointerDown(screen.getByRole('button', { name: 'Talk' }), {
    button: 0,
  });
  return screen.findAllByRole('menuitem');
}

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

it('the wake word starts Row-Bot Voice in the open chat, and waits while voice runs', async () => {
  const hold = vi.fn();
  setWakeListener({ hold } as unknown as WakeListener);
  try {
    const { controller, sockets } = setup();
    await act(async () => {
      await wakeOpenChat();
    });
    expect(controller.startTalk).toHaveBeenCalledTimes(1);
    expect(hold).toHaveBeenLastCalledWith(true);
    await act(async () => sockets[0].open());
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'End voice' }));
    });
    expect(hold).toHaveBeenLastCalledWith(false);
    expect(hold).toHaveBeenCalledTimes(2);
  } finally {
    setWakeListener(null);
  }
});

it('waits for the current reply before it can start', () => {
  setup({ running: true });
  expect(screen.getByRole('button', { name: 'Talk' })).toBeDisabled();
});

it('the Talk menu offers each Live voice provider with its price, or why it can’t be used', async () => {
  setup({ liveProviders });
  const items = await openMenu();
  expect(items.map((item) => item.textContent)).toEqual([
    'Talk with Row-BotRow-Bot Voice, with this chat’s model',
    'Live voice · OpenAI RealtimeAbout $0.10 a minute · audio goes to OpenAI',
    'Live voice · Gemini LiveAbout $0.02 a minute · audio goes to Google',
    'Live voice · Grok VoiceNeeds your xAI API key',
  ]);
  fireEvent.click(items[3]);
  expect(screen.getByText(/Add your xAI API key in/)).toBeVisible();
  expect(screen.getByRole('link', { name: 'Providers' })).toHaveAttribute(
    'href',
    '/settings/models#providers-api',
  );
});

it('says everything that leaves the device before first use, then Live voice runs in the voice bar', async () => {
  const { controller, sockets } = setup({ liveProviders });
  fireEvent.click((await openMenu())[2]);
  expect(
    screen.getByText(
      'Your voice, this chat’s recent messages, the agent profile’s name, a few related memories and what you share on camera or screen go to Google while you talk. About $0.02 a minute, from Google’s published prices (22 Nov 2026), billed to your Google key.',
    ),
  ).toBeVisible();
  expect(controller.startRealtime).not.toHaveBeenCalled();
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Start Gemini Live' }));
  });
  const [, body] = controller.startRealtime.mock.calls[0];
  expect(body.live_provider).toBe('gemini');
  expect(controller.startTalk).not.toHaveBeenCalled();
  // The page speaks only to Row-Bot: its own socket, with the ticket.
  expect(sockets).toHaveLength(1);
  await act(async () => {
    sockets[0].open();
    sockets[0].emit({ type: 'ready', output_sample_rate: 24000 });
    sockets[0].emit({ type: 'caption', role: 'user', text: 'Good morning' });
    sockets[0].emit(audioFrame(1));
  });
  expect(sockets[0].json()[0]).toEqual({ type: 'hello', ticket: TICKET });
  expect(screen.getByText('Gemini Live · Listening')).toBeVisible();
  expect(screen.getByText('Good morning')).toBeVisible();
  expect(audio.current.played).toEqual([1]);
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'End voice' }));
  });
  expect(controller.stopRealtime).toHaveBeenCalledWith(scope, handle);
  // Read once: the next start goes straight to the bar.
  fireEvent.click((await openMenu())[2]);
  await act(async () => undefined);
  expect(controller.startRealtime).toHaveBeenCalledTimes(2);
});

it('an incognito chat’s disclosure leaves out memories, which it does not send', async () => {
  setup({ liveProviders, incognito: true });
  fireEvent.click((await openMenu())[1]);
  expect(
    screen.getByText(
      /^Your voice, this chat’s recent messages and the agent profile’s name go to OpenAI while you talk\./,
    ),
  ).toBeVisible();
  expect(screen.queryByText(/related memories/)).toBeNull();
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
