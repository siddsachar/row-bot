import { describe, expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import { noteAppOpen, proactiveStore } from './proactive-state';
import { fakeIO, moment } from './test-data';

describe('Proactive state', () => {
  it('reads moments again when the server publishes a proactive change', async () => {
    const io = fakeIO({ moments: [] });
    const store = proactiveStore(io);
    const stop = store.subscribe(() => undefined);
    await vi.waitFor(() => expect(store.getView().moments).toEqual([]));
    io.moments.mockResolvedValueOnce({ schema_version: 1, items: [moment()] });
    io.publish();
    await vi.waitFor(() => expect(store.getView().moments).toHaveLength(1));
    stop();
  });

  it('tells the server the window opened, once, after the handshake and the first screen', async () => {
    let handshake: object | null = null;
    let firstScreen = () => {};
    const listeners = new Set<() => void>();
    const controller = {
      getSnapshot: () => ({ handshake }),
      subscribe: (listener: () => void) => {
        listeners.add(listener);
        return () => listeners.delete(listener);
      },
      afterFirstScreen: () =>
        new Promise<void>((resolve) => (firstScreen = resolve)),
      noteProactiveBoundary: vi.fn(async () => ({})),
    } as unknown as ClientController;
    noteAppOpen(controller);
    await Promise.resolve();
    expect(controller.noteProactiveBoundary).not.toHaveBeenCalled();
    // The first screen is up before the handshake: it waits for that too.
    firstScreen();
    await vi.waitFor(() => expect(listeners.size).toBe(1));
    expect(controller.noteProactiveBoundary).not.toHaveBeenCalled();
    handshake = {};
    listeners.forEach((listener) => listener());
    listeners.forEach((listener) => listener());
    expect(controller.noteProactiveBoundary).toHaveBeenCalledTimes(1);
    expect(controller.noteProactiveBoundary).toHaveBeenCalledWith({
      kind: 'app_open',
    });
  });

  it('waits for the first screen to be up before telling the server', async () => {
    let firstScreen = () => {};
    const controller = {
      getSnapshot: () => ({ handshake: {} }),
      subscribe: () => () => undefined,
      afterFirstScreen: () =>
        new Promise<void>((resolve) => (firstScreen = resolve)),
      noteProactiveBoundary: vi.fn(async () => ({})),
    } as unknown as ClientController;
    noteAppOpen(controller);
    await Promise.resolve();
    expect(controller.noteProactiveBoundary).not.toHaveBeenCalled();
    firstScreen();
    await vi.waitFor(() =>
      expect(controller.noteProactiveBoundary).toHaveBeenCalledTimes(1),
    );
  });
});
