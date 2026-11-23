import { afterEach, expect, it, vi } from 'vitest';
import type { VoiceSocket } from '../../api/types';
import { LIVE_STILL_MS, LiveVoiceSession } from './live-voice-session';
import { shareError, stillSize, STILL_SIDE } from './share';
import { VoiceSession } from './voice-session';
import { FakeAudio, FakeShare, FakeSocket } from './voice-test-fakes';

const TICKET = 'ticket-xxxxxxxxxxxxxxxxxxxxxxxxxxxx';

function rowBotVoice() {
  const sockets: FakeSocket[] = [];
  const share = new FakeShare();
  const deps = {
    openSocket: vi.fn(() => {
      const socket = new FakeSocket();
      sockets.push(socket);
      return socket as unknown as VoiceSocket;
    }),
    ticket: vi.fn(async () => TICKET),
    stop: vi.fn(async () => undefined),
    audio: new FakeAudio(),
    share,
  };
  return { session: new VoiceSession(deps, TICKET), sockets, share, deps };
}

async function ready(
  session: { start(): Promise<void> },
  sockets: FakeSocket[],
  ready: Record<string, unknown>,
) {
  const started = session.start();
  await Promise.resolve();
  sockets[0].open();
  sockets[0].emit({ type: 'ready', output_sample_rate: 24000, ...ready });
  sockets[0].emit({ type: 'state', state: 'listening', muted: false });
  await started;
  return sockets[0];
}

/** What went up the socket after the hello: messages, and stills as their byte counts. */
function sent(socket: FakeSocket) {
  return socket.sent
    .slice(1)
    .map((item) =>
      item instanceof ArrayBuffer
        ? { bytes: item.byteLength }
        : (JSON.parse(String(item)) as Record<string, unknown>),
    );
}

async function settle() {
  for (let index = 0; index < 5; index++) await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
}

afterEach(() => vi.useRealTimers());

it('captures nothing until the person shares, then answers each look with one announced still', async () => {
  const { session, sockets, share } = rowBotVoice();
  const socket = await ready(session, sockets, { sees_images: true });
  expect(session.getSnapshot().canShare).toBe('yes');
  socket.emit({ type: 'look' });
  await settle();
  expect(share.start).not.toHaveBeenCalled();
  expect(share.still).not.toHaveBeenCalled();
  await session.startShare('camera');
  expect(session.getSnapshot().share).toMatchObject({
    source: 'camera',
    facingUser: true,
  });
  socket.emit({ type: 'look' });
  await settle();
  expect(sent(socket)).toEqual([
    { type: 'share', source: 'camera' },
    { type: 'still', bytes: share.bytes.length },
    { bytes: share.bytes.length },
  ]);
  session.stopShare();
  expect(share.stop).toHaveBeenCalled();
  expect(session.getSnapshot().share).toBeNull();
  expect(sent(socket).at(-1)).toEqual({ type: 'share', source: null });
});

it('a chat whose model can’t see images shares nothing', async () => {
  const { session, sockets, share } = rowBotVoice();
  await ready(session, sockets, { sees_images: false });
  expect(session.getSnapshot().canShare).toBe('blind');
  await session.startShare('screen');
  expect(share.start).not.toHaveBeenCalled();
  expect(session.getSnapshot().share).toBeNull();
});

it('the browser’s own stop, a host pause and ending voice each release what is shared', async () => {
  const { session, sockets, share } = rowBotVoice();
  const socket = await ready(session, sockets, {});
  await session.startShare('screen');
  share.ended?.();
  expect(session.getSnapshot().share).toBeNull();
  expect(sent(socket).at(-1)).toEqual({ type: 'share', source: null });
  await session.startShare('camera');
  const told = sent(socket).length;
  socket.emit({ type: 'sharing', source: null });
  expect(session.getSnapshot().share).toBeNull();
  expect(sent(socket)).toHaveLength(told);
  await session.startShare('camera');
  share.stop.mockClear();
  await session.end();
  expect(share.stop).toHaveBeenCalled();
  expect(session.getSnapshot()).toMatchObject({ orb: 'ended', share: null });
});

it('a reconnected socket hears that the camera is still shared', async () => {
  const { session, sockets } = rowBotVoice();
  await ready(session, sockets, {});
  await session.startShare('camera');
  sockets[0].drop();
  await settle();
  sockets[1].open();
  sockets[1].emit({ type: 'ready', output_sample_rate: 24000 });
  expect(sent(sockets[1])).toEqual([{ type: 'share', source: 'camera' }]);
});

it('a camera that can’t start says why; a cancelled screen picker says nothing', async () => {
  const { session, sockets, share } = rowBotVoice();
  await ready(session, sockets, {});
  share.failure = new DOMException('denied', 'NotAllowedError');
  await session.startShare('camera');
  expect(session.getSnapshot()).toMatchObject({
    share: null,
    error: 'Row-Bot can’t use the camera. Check its permission.',
  });
  expect(shareError(share.failure, 'screen')).toBe('');
  expect(shareError(new Error('x'), 'screen')).toBe(
    'Screen sharing isn’t available here.',
  );
});

it('a still is scaled down to fit 768 pixels, never up', () => {
  expect(stillSize(1920, 1080)).toEqual({ width: STILL_SIDE, height: 432 });
  expect(stillSize(720, 1280)).toEqual({ width: 432, height: STILL_SIDE });
  expect(stillSize(640, 480)).toEqual({ width: 640, height: 480 });
});

function live(transport: 'openai_realtime' | 'gemini_live') {
  const sockets: FakeSocket[] = [];
  const share = new FakeShare();
  const deps = {
    transport,
    sessionId: 3,
    conversationId: 'A',
    openSocket: () => {
      const socket = new FakeSocket();
      sockets.push(socket);
      return socket as unknown as VoiceSocket;
    },
    ticket: vi.fn(async () => TICKET),
    stop: vi.fn(async () => undefined),
    exchange: vi.fn(async () => 'v=0'),
    audio: new FakeAudio(),
    share,
    startRuntime: vi.fn(async () => undefined) as never,
  };
  return { session: new LiveVoiceSession(deps, TICKET, null), sockets, share };
}

it('Gemini Live is shown a still now and then while the person shares, and none after', async () => {
  const { session, sockets, share } = live('gemini_live');
  const socket = await ready(session, sockets, { sees_images: true });
  expect(session.getSnapshot().canShare).toBe('yes');
  vi.useFakeTimers();
  await session.startShare('camera');
  await vi.advanceTimersByTimeAsync(0);
  const stills = () =>
    sent(socket).filter((item) => item.type === 'still').length;
  expect(stills()).toBe(1);
  await vi.advanceTimersByTimeAsync(LIVE_STILL_MS * 2);
  expect(stills()).toBe(3);
  session.stopShare();
  await vi.advanceTimersByTimeAsync(LIVE_STILL_MS * 3);
  expect(stills()).toBe(3);
  expect(sent(socket).at(-1)).toEqual({ type: 'share', source: null });
  expect(share.stop).toHaveBeenCalled();
});

it('a Live voice front that takes no images offers no sharing', async () => {
  const { session, sockets, share } = live('openai_realtime');
  await ready(session, sockets, { sees_images: false });
  expect(session.getSnapshot().canShare).toBe('no');
  await session.startShare('camera');
  expect(share.start).not.toHaveBeenCalled();
});
