import { afterEach, expect, it, vi } from 'vitest';
import type { VoiceSocket } from '../../api/types';
import { VoicePresence } from './presence';
import { VoiceSession } from './voice-session';
import { FakeAudio, FakeSocket } from './voice-test-fakes';

/** The system's media controls: what the page set, and its buttons to press. */
function fakeMediaSession(actions?: Set<string>) {
  const handlers = new Map<string, (() => void) | null>();
  const session = {
    metadata: null as { title: string } | null,
    playbackState: 'none',
    microphone: null as boolean | null,
    setActionHandler: vi.fn((action: string, run: (() => void) | null) => {
      if (actions && !actions.has(action)) throw new TypeError(action);
      handlers.set(action, run);
    }),
    setMicrophoneActive(active: boolean) {
      this.microphone = active;
    },
    press: (action: string) => handlers.get(action)?.(),
    handlers,
  };
  vi.stubGlobal(
    'MediaMetadata',
    class {
      title: string;
      constructor(init: { title: string }) {
        this.title = init.title;
      }
    },
  );
  return session;
}

afterEach(() => vi.unstubAllGlobals());

it('shows what voice is doing, and headset buttons mute, unmute and end it', () => {
  const media = fakeMediaSession();
  const controls = { mute: vi.fn(), end: vi.fn() };
  const presence = new VoicePresence(
    controls,
    media as unknown as MediaSession,
  );
  presence.update({ orb: 'listening', muted: false });
  expect(media.metadata?.title).toBe('Row-Bot · Listening');
  expect(media.playbackState).toBe('playing');
  expect(media.microphone).toBe(true);
  media.press('pause');
  expect(controls.mute).toHaveBeenLastCalledWith(true);
  presence.update({ orb: 'muted', muted: true });
  expect(media.metadata?.title).toBe('Row-Bot · Muted');
  expect(media.playbackState).toBe('paused');
  media.press('togglemicrophone');
  expect(controls.mute).toHaveBeenLastCalledWith(false);
  media.press('play');
  expect(controls.mute).toHaveBeenLastCalledWith(false);
  media.press('hangup');
  media.press('stop');
  expect(controls.end).toHaveBeenCalledTimes(2);
  presence.release();
  expect(media.metadata).toBeNull();
  expect(media.playbackState).toBe('none');
  expect([...media.handlers.values()].every((run) => run === null)).toBe(true);
});

it('uses only the actions the browser knows, and nothing without the API', () => {
  const media = fakeMediaSession(new Set(['play', 'pause']));
  const controls = { mute: vi.fn(), end: vi.fn() };
  new VoicePresence(controls, media as unknown as MediaSession).update({
    orb: 'speaking',
    muted: false,
  });
  expect([...media.handlers.keys()]).toEqual(['play', 'pause']);
  expect(media.metadata?.title).toBe('Row-Bot · Speaking');
  const none = new VoicePresence(controls, undefined);
  expect(() => {
    none.update({ orb: 'listening', muted: false });
    none.release();
  }).not.toThrow();
});

it('a voice session puts itself in the media controls and takes itself out when it ends', async () => {
  const media = fakeMediaSession();
  vi.stubGlobal('navigator', { ...navigator, mediaSession: media });
  const sockets: FakeSocket[] = [];
  const session = new VoiceSession(
    {
      openSocket: () => {
        const socket = new FakeSocket();
        sockets.push(socket);
        return socket as unknown as VoiceSocket;
      },
      ticket: async () => 'ticket',
      stop: async () => undefined,
      audio: new FakeAudio(),
    },
    'ticket',
  );
  await session.start();
  sockets[0].open();
  sockets[0].emit({ type: 'state', state: 'listening', muted: false });
  expect(media.metadata?.title).toBe('Row-Bot · Listening');
  // The headset's button mutes the microphone, as the bar's Mute does.
  media.press('pause');
  expect(session.getSnapshot().muted).toBe(true);
  expect(sockets[0].json().at(-1)).toEqual({ type: 'mute', muted: true });
  media.press('hangup');
  expect(session.getSnapshot().orb).toBe('ended');
  expect(media.metadata).toBeNull();
});
