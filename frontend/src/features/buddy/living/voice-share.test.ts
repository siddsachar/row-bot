import { afterEach, expect, it, vi } from 'vitest';

/** Same-origin channels: a message reaches every other channel of its name. */
function channels() {
  const open = new Set<FakeChannel>();
  class FakeChannel {
    onmessage: ((event: MessageEvent) => void) | null = null;
    sent: unknown[] = [];
    constructor(readonly name: string) {
      open.add(this);
    }
    postMessage(data: unknown) {
      this.sent.push(data);
      for (const other of open)
        if (other !== this && other.name === this.name)
          other.onmessage?.({ data } as MessageEvent);
    }
    close() {
      open.delete(this);
    }
  }
  return FakeChannel;
}

/** One window's copy of Buddy's voice state (the main window and the desktop pet are two). */
async function windowState() {
  vi.resetModules();
  return import('./voice');
}

afterEach(() => vi.useRealTimers());

it('the desktop pet listens and lip-syncs to voice in the main window', async () => {
  const Channel = channels();
  const main = await windowState();
  const pet = await windowState();
  const stopSharing = main.shareBuddyVoice(Channel);
  const stopReceiving = pet.receiveBuddyVoice(Channel);
  main.setBuddyListening(true);
  expect(pet.buddyListening()).toBe(true);
  main.setBuddyListening(false);
  main.setBuddyVoiceLevel(0.7);
  expect(pet.buddyListening()).toBe(false);
  expect(pet.buddyVoiceLevel()).toBeCloseTo(0.7);
  main.setBuddyVoiceLevel(0);
  expect(pet.buddyVoiceLevel()).toBe(0);
  stopSharing();
  stopReceiving();
});

it('the pet stops listening when the main window stops saying so (it closed mid-voice)', async () => {
  vi.useFakeTimers();
  const Channel = channels();
  const main = await windowState();
  const pet = await windowState();
  main.shareBuddyVoice(Channel);
  pet.receiveBuddyVoice(Channel);
  main.setBuddyListening(true);
  expect(pet.buddyListening()).toBe(true);
  expect(pet.buddyListening(performance.now() + 2500)).toBe(false);
});

it('shares a few updates a second while Buddy speaks, not one per frame', async () => {
  vi.useFakeTimers();
  const Channel = channels();
  const main = await windowState();
  main.shareBuddyVoice(Channel);
  const listener = new Channel('row-bot-buddy-voice');
  const heard: unknown[] = [];
  listener.onmessage = (event) => heard.push(event.data);
  main.setBuddyVoiceLevel(0.5);
  for (let frame = 0; frame < 60; frame++) {
    vi.advanceTimersByTime(16);
    main.setBuddyVoiceLevel(0.4 + (frame % 3) / 10);
  }
  // About one second of frames: roughly 25 updates, never 60.
  expect(heard.length).toBeGreaterThan(15);
  expect(heard.length).toBeLessThan(35);
  expect(heard.at(-1)).toEqual({ listening: false, level: expect.any(Number) });
});

it('does nothing without BroadcastChannel', async () => {
  const main = await windowState();
  expect(() => {
    main.shareBuddyVoice(undefined)();
    main.receiveBuddyVoice(undefined)();
    main.setBuddyListening(true);
  }).not.toThrow();
  expect(main.buddyListening()).toBe(true);
});
