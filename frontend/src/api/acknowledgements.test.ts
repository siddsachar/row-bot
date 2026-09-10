import { afterEach, expect, it, vi } from 'vitest';
import { Acknowledgements, ACK_RETIRE_TIMEOUT_MS } from './acknowledgements';
afterEach(() => vi.useRealTimers());
it('coalesces a thousand offered cuts without delaying rendering or growing work', async () => {
  vi.useFakeTimers();
  const send = vi.fn(async (_cursor: string) => undefined);
  const queue = new Acknowledgements(send, vi.fn());
  for (let i = 0; i < 1000; i++) queue.offer(String(i));
  expect(send).not.toHaveBeenCalled();
  expect(vi.getTimerCount()).toBe(1);
  await vi.advanceTimersByTimeAsync(0);
  expect(send.mock.calls.map((call) => call[0])).toEqual(['999']);
  for (let i = 1000; i < 2000; i++) queue.offer(String(i));
  await vi.advanceTimersByTimeAsync(999);
  expect(send).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(1);
  expect(send.mock.calls.at(-1)?.[0]).toBe('1999');
  queue.close();
  expect(vi.getTimerCount()).toBe(0);
});
it('has one in-flight request and drops all trailing work and errors after close', async () => {
  vi.useFakeTimers();
  let reject!: (cause: unknown) => void;
  const failed = vi.fn();
  const send = vi.fn(
    () =>
      new Promise((_, no) => {
        reject = no;
      }),
  );
  const queue = new Acknowledgements(send, failed);
  queue.offer('1');
  await vi.advanceTimersByTimeAsync(0);
  for (let i = 2; i < 100; i++) queue.offer(String(i));
  await vi.advanceTimersByTimeAsync(10000);
  expect(send).toHaveBeenCalledTimes(1);
  const closing = queue.close();
  reject(new TypeError('Synthetic disconnect'));
  await closing;
  expect(failed).not.toHaveBeenCalled();
  expect(vi.getTimerCount()).toBe(0);
});

it('retires only after the issued acknowledgement settles and discards trailing cuts', async () => {
  vi.useFakeTimers();
  let complete!: () => void;
  const send = vi.fn(
    () =>
      new Promise<void>((resolve) => {
        complete = resolve;
      }),
  );
  const queue = new Acknowledgements(send, vi.fn());
  queue.offer('1');
  await vi.advanceTimersByTimeAsync(0);
  queue.offer('2');
  const retired = vi.fn();
  const closing = queue.close().then(retired);
  await vi.advanceTimersByTimeAsync(2000);
  expect(retired).not.toHaveBeenCalled();
  complete();
  await closing;
  expect(retired).toHaveBeenCalledOnce();
  expect(send).toHaveBeenCalledExactlyOnceWith('1', expect.any(AbortSignal));
  expect(vi.getTimerCount()).toBe(0);
});

it('bounds retirement of an unresponsive transport and contains late errors without sending a trailing cut', async () => {
  vi.useFakeTimers();
  let reject!: (error: unknown) => void;
  let issued!: AbortSignal;
  const send = vi.fn((_cut: string, signal: AbortSignal) => {
    issued = signal;
    return new Promise<void>((_resolve, no) => {
      reject = no;
    });
  });
  const failed = vi.fn(),
    retired = vi.fn();
  const queue = new Acknowledgements(send, failed);
  queue.offer('issued');
  await vi.advanceTimersByTimeAsync(0);
  queue.offer('trailing');
  const closing = queue.close().then(retired);
  await vi.advanceTimersByTimeAsync(ACK_RETIRE_TIMEOUT_MS - 1);
  expect(retired).not.toHaveBeenCalled();
  expect(issued.aborted).toBe(false);
  await vi.advanceTimersByTimeAsync(1);
  await closing;
  expect(issued.aborted).toBe(true);
  expect(retired).toHaveBeenCalledOnce();
  queue.offer('after-close');
  reject({ status: 401, code: 'session_expired' });
  await vi.advanceTimersByTimeAsync(10000);
  expect(failed).not.toHaveBeenCalled();
  expect(send).toHaveBeenCalledTimes(1);
  expect(vi.getTimerCount()).toBe(0);
});

it('parent cancellation immediately aborts an issued ACK even during graceful retirement', async () => {
  vi.useFakeTimers();
  const parent = new AbortController();
  let issued!: AbortSignal;
  const send = vi.fn((_cut: string, signal: AbortSignal) => {
    issued = signal;
    return new Promise<void>(() => undefined);
  });
  const queue = new Acknowledgements(send, vi.fn(), undefined, parent.signal);
  queue.offer('issued');
  await vi.advanceTimersByTimeAsync(0);
  const closing = queue.close();
  parent.abort();
  await vi.advanceTimersByTimeAsync(0);
  expect(issued.aborted).toBe(true);
  await closing;
  expect(vi.getTimerCount()).toBe(0);
});
