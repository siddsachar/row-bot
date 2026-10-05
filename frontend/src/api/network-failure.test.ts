import { expect, it, vi } from 'vitest';
import {
  isNetworkFailure,
  networkFailure,
  reportNetworkFailures,
} from './network-failure';

type Transport = {
  read(): Promise<string>;
  sync(): string;
  stream(): AsyncIterable<number>;
};

function transport(overrides: Partial<Transport> = {}): Transport {
  return {
    read: async () => 'ok',
    sync: () => 'ok',
    async *stream() {
      yield 1;
    },
    ...overrides,
  };
}

it('treats what the transport throws or rejects with as a lost connection', async () => {
  const lost = new TypeError('Failed to fetch');
  const reported = reportNetworkFailures(
    transport({
      read: () => Promise.reject(lost),
      sync: () => {
        throw new TypeError('NetworkError when attempting to fetch resource.');
      },
    }),
  );
  await expect(reported.read()).rejects.toBe(lost);
  expect(isNetworkFailure(lost)).toBe(true);
  let thrown: unknown;
  try {
    reported.sync();
  } catch (error) {
    thrown = error;
  }
  expect(isNetworkFailure(thrown)).toBe(true);
});

it('treats a stream that breaks part way as a lost connection', async () => {
  const reported = reportNetworkFailures(
    transport({
      async *stream() {
        yield 1;
        throw new TypeError('network error');
      },
    }),
  );
  const seen: number[] = [];
  let failure: unknown;
  try {
    for await (const value of reported.stream()) seen.push(value);
  } catch (error) {
    failure = error;
  }
  expect(seen).toEqual([1]);
  expect(isNetworkFailure(failure)).toBe(true);
});

it('leaves answers, other errors and TypeErrors raised elsewhere alone', async () => {
  const refused = { code: 'session_expired', status: 401 };
  const reported = reportNetworkFailures(
    transport({ read: () => Promise.reject(refused) }),
  );
  await expect(reported.read()).rejects.toBe(refused);
  expect(reported.sync()).toBe('ok');
  expect(isNetworkFailure(new TypeError('x is not a function'))).toBe(false);
  expect(isNetworkFailure(networkFailure())).toBe(true);
});

it('calls the transport itself, so spies and later replacements still apply', async () => {
  const original = transport();
  const reported = reportNetworkFailures(original);
  const read = vi.spyOn(original, 'read').mockResolvedValue('spied');
  await expect(reported.read()).resolves.toBe('spied');
  expect(read).toHaveBeenCalledOnce();
});
