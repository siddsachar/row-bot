import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  ClientController,
  FIRST_SCREEN_LONGEST_MS,
  FIRST_SCREEN_SETTLE_MS,
} from './controller';
import { FixtureTransport } from './fixtures';

/*
 * Visible first (request budget): a window reads what its first screen shows
 * (the conversation list and the open conversation) before anything else.
 * Reads the first screen doesn't show wait for `afterFirstScreen`.
 */

const clients: ClientController[] = [];
afterEach(() => {
  clients.splice(0).forEach((client) => client.dispose());
  vi.useRealTimers();
});

function opened(transport: FixtureTransport) {
  const controller = new ClientController(transport, () => 1);
  clients.push(controller);
  const up = vi.fn();
  void controller.afterFirstScreen().then(up);
  return { controller, up };
}

describe('the first screen', () => {
  it('is up a moment after the list is read and the open conversation is connected', async () => {
    vi.useFakeTimers();
    const transport = new FixtureTransport();
    let answer!: () => void;
    const read = transport.getConversation.bind(transport);
    vi.spyOn(transport, 'getConversation').mockImplementationOnce(
      async (...args) => {
        await new Promise<void>((resolve) => (answer = resolve));
        return read(...args);
      },
    );
    const { controller, up } = opened(transport);
    void controller.selectConversation('conversation-a');
    const started = controller.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(controller.getSnapshot().conversations.length).toBeGreaterThan(0);
    // The open conversation is still being read: nothing else goes yet.
    await vi.advanceTimersByTimeAsync(FIRST_SCREEN_SETTLE_MS);
    expect(up).not.toHaveBeenCalled();
    expect(controller.firstScreenSettled).toBe(false);
    answer();
    await started;
    expect(controller.getSnapshot().conversation?.id).toBe('conversation-a');
    await vi.advanceTimersByTimeAsync(FIRST_SCREEN_SETTLE_MS - 1);
    expect(up).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(up).toHaveBeenCalledTimes(1);
    expect(controller.firstScreenSettled).toBe(true);
    // A screen opened later doesn't wait again.
    const later = vi.fn();
    await controller.afterFirstScreen().then(later);
    expect(later).toHaveBeenCalledTimes(1);
  });

  it('never waits longer than a few seconds for a slow open', async () => {
    vi.useFakeTimers();
    const transport = new FixtureTransport();
    vi.spyOn(transport, 'getConversation').mockImplementation(
      () => new Promise(() => undefined),
    );
    const { controller, up } = opened(transport);
    void controller.selectConversation('conversation-a');
    void controller.start();
    await vi.advanceTimersByTimeAsync(FIRST_SCREEN_LONGEST_MS - 1);
    expect(up).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(up).toHaveBeenCalledTimes(1);
  });

  it('is up with no conversation open once the list is read', async () => {
    vi.useFakeTimers();
    const { controller, up } = opened(new FixtureTransport());
    await controller.start();
    expect(controller.getSnapshot().conversations.length).toBeGreaterThan(0);
    await vi.advanceTimersByTimeAsync(FIRST_SCREEN_SETTLE_MS);
    expect(up).toHaveBeenCalledTimes(1);
  });
});
