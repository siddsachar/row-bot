import { afterEach, expect, it, vi } from 'vitest';
import {
  acknowledge,
  observeEvents,
} from '../../../contracts/client-platform/v1/typescript/client';

const proof = {
  client_session_id: '00000000-0000-4000-8000-000000000001',
  csrf_token: 'synthetic',
};
afterEach(() => vi.unstubAllGlobals());

function serve(body: ReadableStream<Uint8Array>) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: true, body })),
  );
  return observeEvents('', proof, 'subscription', 'cursor');
}

it('releases an errored stream without masking its read failure or leaking cancellation rejection', async () => {
  const primary = new Error('synthetic read failure');
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.error(primary);
    },
  });
  await expect(serve(body).next()).rejects.toBe(primary);
  expect(body.locked).toBe(false);
});

it('preserves a malformed frame error when underlying cancellation also rejects', async () => {
  const cancel = vi.fn(async () => {
    throw new Error('synthetic cleanup failure');
  });
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('event: unsupported\n\n'));
    },
    cancel,
  });
  await expect(serve(body).next()).rejects.toThrow('protocol_incompatible');
  expect(cancel).toHaveBeenCalledOnce();
  expect(body.locked).toBe(false);
});

it('releases a stream on consumer return even if its transport rejects cancellation', async () => {
  const cancel = vi.fn(async () => {
    throw new Error('synthetic cleanup failure');
  });
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(
        new TextEncoder().encode('event: snapshot_required\ndata: {}\n\n'),
      );
    },
    cancel,
  });
  const events = serve(body);
  await expect(events.next()).resolves.toMatchObject({
    done: false,
    value: {},
  });
  await expect(events.return(undefined)).resolves.toEqual({
    done: true,
    value: undefined,
  });
  expect(cancel).toHaveBeenCalledOnce();
  expect(body.locked).toBe(false);
});

it('completes and releases an exhausted stream', async () => {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.close();
    },
  });
  await expect(serve(body).next()).resolves.toEqual({
    done: true,
    value: undefined,
  });
  expect(body.locked).toBe(false);
});

it('never starts a deferred stream request after its observer has retired', async () => {
  const fetch = vi.fn();
  vi.stubGlobal('fetch', fetch);
  const abort = new AbortController();
  const events = observeEvents(
    '',
    proof,
    'subscription',
    'cursor',
    abort.signal,
  );
  abort.abort();
  await expect(events.next()).rejects.toMatchObject({ name: 'AbortError' });
  expect(fetch).not.toHaveBeenCalled();
});

it('does not issue an acknowledgement retired during asynchronous request admission', async () => {
  const fetch = vi.fn();
  vi.stubGlobal('fetch', fetch);
  const abort = new AbortController();
  const request = acknowledge(
    '',
    proof,
    'subscription',
    'cursor',
    abort.signal,
  );
  abort.abort();
  await expect(request).rejects.toMatchObject({ name: 'AbortError' });
  expect(fetch).not.toHaveBeenCalled();
});
