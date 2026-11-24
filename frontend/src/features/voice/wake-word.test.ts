import { describe, expect, it, vi } from 'vitest';
import { FakeSocket } from './voice-test-fakes';
import { WAKE_RETRY_MS, WakeListener, type WakeMicrophone } from './wake-word';

/** A microphone with no device: the test pushes frames and mute changes. */
class FakeMicrophone implements WakeMicrophone {
  open = true;
  muted: ((muted: boolean) => void) | null = null;
  constructor(public frame: (pcm: ArrayBuffer) => void) {}
  close = vi.fn(() => {
    this.open = false;
  });
  onMuted(change: (muted: boolean) => void) {
    this.muted = change;
  }
}

function rig() {
  const sockets: FakeSocket[] = [];
  const mics: FakeMicrophone[] = [];
  const timers: Array<{ run(): void; ms: number }> = [];
  let tickets = 0;
  const onWake = vi.fn(async () => undefined);
  const listener = new WakeListener({
    ticket: vi.fn(async () => `wake-ticket-${++tickets}-long-enough`),
    openSocket: () => {
      const socket = new FakeSocket();
      sockets.push(socket);
      return socket as unknown as WebSocket;
    },
    microphone: async (frame) => {
      const mic = new FakeMicrophone(frame);
      mics.push(mic);
      return mic;
    },
    onWake,
    setTimer: (run, ms) => {
      timers.push({ run, ms });
      return () => undefined;
    },
  });
  const frame = () => new Int16Array(320).buffer;
  const binary = (socket: FakeSocket) =>
    socket.sent.filter((item) => item instanceof ArrayBuffer).length;
  return { listener, sockets, mics, timers, onWake, frame, binary };
}

async function settle() {
  for (let index = 0; index < 5; index++) await Promise.resolve();
}

async function listening(test: ReturnType<typeof rig>) {
  test.listener.arm();
  await settle();
  const socket = test.sockets.at(-1)!;
  socket.open();
  socket.emit({ type: 'ready', input_sample_rate: 16000 });
  return socket;
}

describe('the wake word listener', () => {
  it('opens nothing until armed, then says hello in wake mode and sends frames once ready', async () => {
    const test = rig();
    await settle();
    expect(test.mics).toHaveLength(0);
    expect(test.sockets).toHaveLength(0);

    test.listener.arm();
    await settle();
    const socket = test.sockets[0];
    socket.open();
    expect(socket.json()).toEqual([
      { type: 'hello', mode: 'wake', ticket: 'wake-ticket-1-long-enough' },
    ]);
    // Before the host is ready, no audio leaves the window.
    test.mics[0].frame(test.frame());
    expect(test.binary(socket)).toBe(0);
    socket.emit({ type: 'ready', input_sample_rate: 16000 });
    expect(test.listener.getSnapshot().state).toBe('listening');
    test.mics[0].frame(test.frame());
    expect(test.binary(socket)).toBe(1);
  });

  it('turned off, it lets go of the microphone and sends no more frames', async () => {
    const test = rig();
    const socket = await listening(test);
    test.listener.disarm();
    expect(test.mics[0].close).toHaveBeenCalled();
    expect(socket.json().at(-1)).toEqual({ type: 'end' });
    expect(socket.readyState).toBe(3);
    test.mics[0].frame(test.frame());
    expect(test.binary(socket)).toBe(0);
    expect(test.listener.getSnapshot()).toMatchObject({ state: 'off' });
  });

  it('hearing it starts voice, holds while voice starts, then listens again', async () => {
    const test = rig();
    let started!: () => void;
    test.onWake.mockImplementation(
      () =>
        new Promise<undefined>(
          (resolve) => (started = () => resolve(undefined)),
        ),
    );
    const socket = await listening(test);
    socket.emit({ type: 'wake' });
    await settle();
    expect(test.onWake).toHaveBeenCalledTimes(1);
    expect(test.mics[0].close).toHaveBeenCalled();
    expect(test.listener.getSnapshot().state).toBe('held');
    started();
    await settle();
    // Nothing else holds it: a new ticket and a new microphone.
    expect(test.sockets).toHaveLength(2);
    expect(test.mics).toHaveLength(2);
  });

  it('a voice session holds it: no microphone, no frames, until the session ends', async () => {
    const test = rig();
    await listening(test);
    test.listener.hold(true);
    expect(test.mics[0].close).toHaveBeenCalled();
    expect(test.listener.getSnapshot().state).toBe('held');
    await settle();
    expect(test.sockets).toHaveLength(1);
    test.listener.hold(false);
    await settle();
    expect(test.sockets).toHaveLength(2);
  });

  it('one click pauses it in this window, and another listens again', async () => {
    const test = rig();
    await listening(test);
    test.listener.pause();
    expect(test.mics[0].close).toHaveBeenCalled();
    expect(test.listener.getSnapshot()).toMatchObject({
      state: 'off',
      paused: true,
    });
    // A session ending while paused doesn't start it again.
    test.listener.hold(true);
    test.listener.hold(false);
    await settle();
    expect(test.sockets).toHaveLength(1);
    test.listener.resume();
    await settle();
    expect(test.sockets).toHaveLength(2);
  });

  it('says so when the system mutes the microphone (a hidden window)', async () => {
    const test = rig();
    const socket = await listening(test);
    test.mics[0].muted?.(true);
    expect(test.listener.getSnapshot().state).toBe('hidden');
    test.mics[0].frame(test.frame());
    expect(test.binary(socket)).toBe(0);
    test.mics[0].muted?.(false);
    expect(test.listener.getSnapshot().state).toBe('listening');
  });

  it('a dropped socket is tried again later, not at once', async () => {
    const test = rig();
    const socket = await listening(test);
    socket.drop();
    expect(test.listener.getSnapshot().state).toBe('failed');
    expect(test.mics[0].close).toHaveBeenCalled();
    expect(test.timers.map((timer) => timer.ms)).toEqual([WAKE_RETRY_MS]);
    test.timers[0].run();
    await settle();
    expect(test.sockets).toHaveLength(2);
  });

  it('a refused microphone is reported and not retried', async () => {
    const test = rig();
    const listener = new WakeListener({
      ticket: async () => 'unused-ticket-long-enough',
      openSocket: () => {
        throw new Error('no socket without a microphone');
      },
      microphone: async () => {
        throw Object.assign(new Error('denied'), { name: 'NotAllowedError' });
      },
      onWake: () => undefined,
      setTimer: (run, ms) => {
        test.timers.push({ run, ms });
        return () => undefined;
      },
    });
    listener.arm();
    await settle();
    expect(listener.getSnapshot()).toMatchObject({
      state: 'failed',
      error: 'Row-Bot can’t use the microphone. Check its permission.',
    });
    expect(test.timers).toHaveLength(0);
  });
});
