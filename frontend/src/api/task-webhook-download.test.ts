import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { Blob as NodeBlob } from 'node:buffer';
import { downloadTaskWebhook } from '../../../contracts/client-platform/v1/typescript/client';
const proof = {
  client_session_id: '00000000-0000-4000-8000-000000000001',
  csrf_token: 'synthetic-proof',
};
const revision = 'a'.repeat(64);
afterEach(() => vi.unstubAllGlobals());
beforeEach(() => vi.stubGlobal('Blob', NodeBlob));

it('uses protected no-store transport and accepts the exact 64KiB byte boundary', async () => {
  const fetch = vi.fn().mockResolvedValue(
    new Response(new Uint8Array(65536), {
      headers: {
        'Content-Type': 'application/octet-stream',
        'Cache-Control': 'no-store',
      },
    }),
  );
  vi.stubGlobal('fetch', fetch);
  const result = await downloadTaskWebhook('', proof, 'task', revision);
  expect(result.size).toBe(65536);
  expect(result.type).toBe('application/json');
  expect(fetch).toHaveBeenCalledWith(
    expect.stringContaining('/tasks/task/webhook-configuration?revision='),
    expect.objectContaining({
      credentials: 'same-origin',
      cache: 'no-store',
      headers: expect.any(Object),
    }),
  );
  expect(fetch.mock.calls[0][0]).not.toContain(proof.csrf_token);
});

it.each(['overflow', 'type', 'cache', 'empty', 'read-failure'])(
  'fails closed on %s and releases the response reader',
  async (kind) => {
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        if (kind === 'read-failure') {
          controller.error(new Error('read interrupted'));
          return;
        }
        if (kind !== 'empty')
          controller.enqueue(new Uint8Array(kind === 'overflow' ? 65537 : 10));
        if (kind === 'empty') controller.close();
      },
      cancel,
    });
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(body, {
          headers: {
            'Content-Type':
              kind === 'type' ? 'text/html' : 'application/octet-stream',
            'Cache-Control': kind === 'cache' ? 'public' : 'no-store',
          },
        }),
      ),
    );
    await expect(
      downloadTaskWebhook('', proof, 'task', revision),
    ).rejects.toThrow();
    expect(body.locked).toBe(false);
    if (!['empty', 'read-failure'].includes(kind))
      expect(cancel).toHaveBeenCalledOnce();
  },
);

it('rejects an invalid revision before fetch and forwards explicit cancellation', async () => {
  const fetch = vi
    .fn()
    .mockRejectedValue(new DOMException('cancelled', 'AbortError'));
  vi.stubGlobal('fetch', fetch);
  await expect(
    downloadTaskWebhook('', proof, 'task', 'invalid'),
  ).rejects.toThrow('protocol_incompatible');
  expect(fetch).not.toHaveBeenCalled();
  const abort = new AbortController();
  abort.abort();
  await expect(
    downloadTaskWebhook('', proof, 'task', revision, abort.signal),
  ).rejects.toThrow();
  expect(fetch).toHaveBeenCalledWith(
    expect.any(String),
    expect.objectContaining({ signal: abort.signal }),
  );
});
