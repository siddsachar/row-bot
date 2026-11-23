import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { ClientPlatform } from '../../platform';
import { RuntimeContext } from '../../runtime';
import { FakeSocket } from './voice-test-fakes';
import { WakeWordHost, WakeWordIndicator } from './WakeWord';
import { announceWakeWord, setWakeTarget } from './wake-word';

// No audio device in tests: the microphone opens as a stream with one track.
const microphones: Array<{ stopped: boolean }> = [];
vi.mock('./devices', () => ({
  deviceChoice: () => ({ input: '', output: '' }),
  openMicrophone: vi.fn(async () => {
    const record = { stopped: false };
    microphones.push(record);
    const track = { onmute: null, onunmute: null };
    return {
      stream: {
        getAudioTracks: () => [track],
        getTracks: () => [{ stop: () => (record.stopped = true) }],
      },
      fellBack: false,
    };
  }),
}));
vi.mock('./voice-audio', () => ({
  CAPTURE_CONSTRAINTS: {},
  captureFrames: vi.fn(async () => ({ close: async () => undefined })),
}));

async function settle() {
  await act(async () => {
    for (let index = 0; index < 8; index++) await Promise.resolve();
  });
}

function host({
  reason = 'available',
  saved = true,
}: { reason?: string; saved?: boolean } = {}) {
  const sockets: FakeSocket[] = [];
  const controller = {
    dictationCapability: vi.fn(async () => ({ wake_reason: reason })),
    settingsSnapshot: vi.fn(async () => ({
      voice: { runtime: { wake_word: saved } },
    })),
    wakeTicket: vi.fn(async () => ({
      ticket: 'wake-ticket-long-enough-1234',
      ticket_expires_in_ms: 30000,
    })),
    openWakeSocket: vi.fn(() => {
      const socket = new FakeSocket();
      sockets.push(socket);
      return socket;
    }),
  } as unknown as ClientController;
  const startInNewChat = vi.fn(async () => undefined);
  render(
    <RuntimeContext.Provider
      value={{ controller, platform: {} as ClientPlatform }}
    >
      <WakeWordHost startInNewChat={startInNewChat} />
      <WakeWordIndicator />
    </RuntimeContext.Provider>,
  );
  return { controller, sockets, startInNewChat };
}

async function ready(sockets: FakeSocket[]) {
  await settle();
  const socket = sockets.at(-1)!;
  act(() => {
    socket.open();
    socket.emit({ type: 'ready', input_sample_rate: 16000 });
  });
  return socket;
}

afterEach(() => {
  cleanup();
  announceWakeWord(false);
  microphones.length = 0;
});

describe('the wake word in the workspace', () => {
  it('stays off, with no microphone or ticket, until Settings has it on', async () => {
    const test = host({ saved: false });
    await settle();
    expect(test.controller.wakeTicket).not.toHaveBeenCalled();
    expect(microphones).toHaveLength(0);
    expect(screen.queryByRole('button', { name: /Hey Row-Bot/ })).toBeNull();
  });

  it('never listens where it cannot: another device', async () => {
    const test = host({ reason: 'not_this_computer' });
    await settle();
    expect(test.controller.settingsSnapshot).not.toHaveBeenCalled();
    expect(test.controller.wakeTicket).not.toHaveBeenCalled();
  });

  it('shows that it listens, and one click stops it in this window', async () => {
    const test = host();
    await ready(test.sockets);
    const indicator = screen.getByRole('button', {
      name: 'Listening for “Hey Row-Bot”. Turn off',
    });
    expect(indicator).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(indicator);
    expect(microphones[0].stopped).toBe(true);
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Wake word off in this window. Listen again',
      }),
    );
    await settle();
    expect(test.sockets).toHaveLength(2);
  });

  it('hearing it starts voice in the open chat, or else in a new chat', async () => {
    const test = host();
    const socket = await ready(test.sockets);
    act(() => socket.emit({ type: 'wake' }));
    await settle();
    expect(test.startInNewChat).toHaveBeenCalledTimes(1);

    const start = vi.fn(async () => undefined);
    const release = setWakeTarget({ conversationId: 'conversation-a', start });
    try {
      const next = await ready(test.sockets);
      act(() => next.emit({ type: 'wake' }));
      await settle();
      expect(start).toHaveBeenCalledTimes(1);
      expect(test.startInNewChat).toHaveBeenCalledTimes(1);
    } finally {
      release();
    }
  });
});
