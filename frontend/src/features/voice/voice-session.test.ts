import { afterEach, expect, it, vi } from 'vitest';
import type { VoiceSocket } from '../../api/types';
import { buddyListening } from '../buddy/living/voice';
import { VoiceSession } from './voice-session';
import { FakeAudio, FakeSocket, audioFrame } from './voice-test-fakes';

function setup() {
  const sockets: FakeSocket[] = [];
  const audio = new FakeAudio();
  const deps = {
    openSocket: vi.fn(() => {
      const socket = new FakeSocket();
      sockets.push(socket);
      return socket as unknown as VoiceSocket;
    }),
    ticket: vi.fn(async () => 'second-ticket-xxxxxxxxxxxxxxxxxxxx'),
    stop: vi.fn(async () => undefined),
    audio,
  };
  const session = new VoiceSession(deps, 'first-ticket-xxxxxxxxxxxxxxxxxxxxx');
  return { session, sockets, audio, deps };
}

afterEach(() => vi.useRealTimers());

it('presents its ticket first, then streams microphone frames while listening', async () => {
  const { session, sockets, audio } = setup();
  await session.start();
  const socket = sockets[0];
  socket.open();
  expect(socket.json()[0]).toEqual({
    type: 'hello',
    ticket: 'first-ticket-xxxxxxxxxxxxxxxxxxxxx',
  });
  socket.emit({
    type: 'ready',
    output_sample_rate: 24000,
    captions: false,
    push_to_talk: false,
  });
  socket.emit({ type: 'state', state: 'listening', muted: false });
  expect(session.getSnapshot()).toMatchObject({
    orb: 'listening',
    captionsDefault: false,
  });
  expect(buddyListening()).toBe(true);
  const frame = new ArrayBuffer(640);
  audio.capture?.frame(frame);
  expect(socket.sent).toContain(frame);
  session.setMuted(true);
  audio.capture?.frame(new ArrayBuffer(640));
  expect(
    socket.sent.filter((item) => item instanceof ArrayBuffer),
  ).toHaveLength(1);
  expect(audio.setMicEnabled).toHaveBeenLastCalledWith(false);
  expect(socket.json().at(-1)).toEqual({ type: 'mute', muted: true });
});

it('shows a spoken sentence’s caption as its audio starts, and reports when it played', async () => {
  const { session, sockets, audio } = setup();
  await session.start();
  const socket = sockets[0];
  socket.open();
  socket.emit({
    type: 'caption',
    role: 'user',
    text: 'What time is it?',
    final: true,
  });
  socket.emit({
    type: 'caption',
    role: 'assistant',
    text: 'It is noon.',
    segment: 1,
    sample_rate: 24000,
    final: true,
  });
  expect(session.getSnapshot().rowBot).toBeNull();
  socket.emit(audioFrame(1));
  expect(audio.played).toEqual([1]);
  audio.playback?.started(1, 42.4);
  audio.playback?.started(1, 50);
  expect(session.getSnapshot().you?.text).toBe('What time is it?');
  expect(session.getSnapshot().rowBot?.text).toBe('It is noon.');
  const reports = socket.json().filter((event) => event.type === 'playback');
  expect(reports).toEqual([
    { type: 'playback', state: 'playing', segment: 1, delay_ms: 42 },
  ]);
  audio.playback?.idle();
  expect(socket.json().at(-1)).toEqual({ type: 'playback', state: 'idle' });
});

it('ducks and flushes playback on the host’s word', async () => {
  const { session, sockets, audio } = setup();
  await session.start();
  const socket = sockets[0];
  socket.open();
  socket.emit({ type: 'duck', on: true });
  expect(audio.duck).toHaveBeenLastCalledWith(true);
  socket.emit({
    type: 'caption',
    role: 'assistant',
    text: 'Unheard.',
    segment: 2,
    final: true,
  });
  socket.emit({ type: 'flush', reason: 'barge_in' });
  expect(audio.flush).toHaveBeenCalled();
  audio.playback?.started(2, 10);
  expect(session.getSnapshot().rowBot).toBeNull();
});

it('a paused session lets go of the microphone and asks for it again on resume', async () => {
  const { session, sockets, audio } = setup();
  await session.start();
  const socket = sockets[0];
  socket.open();
  socket.emit({ type: 'state', state: 'paused', muted: false });
  expect(audio.pauseCapture).toHaveBeenCalled();
  audio.capture?.frame(new ArrayBuffer(640));
  expect(socket.sent.some((item) => item instanceof ArrayBuffer)).toBe(false);
  await session.resume();
  expect(audio.resumeCapture).toHaveBeenCalled();
  expect(socket.json().at(-1)).toEqual({ type: 'resume' });
});

it('a dropped connection reconnects once with a fresh ticket, then ends clearly', async () => {
  const { session, sockets, deps, audio } = setup();
  await session.start();
  sockets[0].open();
  sockets[0].emit({ type: 'state', state: 'listening', muted: false });
  sockets[0].drop();
  await vi.waitFor(() => expect(sockets).toHaveLength(2));
  sockets[1].open();
  expect(sockets[1].json()[0]).toEqual({
    type: 'hello',
    ticket: 'second-ticket-xxxxxxxxxxxxxxxxxxxx',
  });
  sockets[1].emit({ type: 'state', state: 'listening', muted: false });
  sockets[1].drop();
  expect(session.getSnapshot()).toMatchObject({
    orb: 'ended',
    error: 'Voice disconnected.',
  });
  expect(audio.close).toHaveBeenCalled();
  expect(deps.ticket).toHaveBeenCalledTimes(1);
});

it('ending sends end, stops the lease and releases the devices', async () => {
  const { session, sockets, deps, audio } = setup();
  await session.start();
  sockets[0].open();
  await session.end();
  expect(sockets[0].json().at(-1)).toEqual({ type: 'end' });
  expect(deps.stop).toHaveBeenCalled();
  expect(audio.close).toHaveBeenCalled();
  expect(session.getSnapshot().orb).toBe('ended');
  expect(buddyListening()).toBe(false);
});

it('a refused microphone ends the session with a plain reason', async () => {
  const { session, sockets, audio } = setup();
  audio.startCapture.mockRejectedValueOnce(new Error('NotAllowedError'));
  await session.start();
  expect(sockets).toHaveLength(0);
  expect(session.getSnapshot().error).toMatch(/microphone/);
});
