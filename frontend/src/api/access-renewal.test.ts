import { describe, expect, it, vi } from 'vitest';
import { ACCESS_RENEW_MS, keepAccessSessionRenewed } from './access-renewal';

function fakeWindow() {
  let tick: () => void = () => {};
  const listeners = new Set<string>();
  const target = {
    setInterval: vi.fn((fn: () => void) => {
      tick = fn;
      return 1;
    }),
    clearInterval: vi.fn(),
    addEventListener: vi.fn((type: string) => listeners.add(type)),
    removeEventListener: vi.fn((type: string) => listeners.delete(type)),
    document: { addEventListener: vi.fn(), removeEventListener: vi.fn() },
  } as unknown as Window;
  return { target, tick: () => tick(), listeners };
}

const reply = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status });
const session = (kind: string) =>
  reply({ authenticated: true, authentication_kind: kind });
/** Let a finished request settle (its body read and its bookkeeping done). */
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('keepAccessSessionRenewed', () => {
  it('renews an invited device at once, then every 12 hours, same-origin', async () => {
    let now = 0;
    const { target, tick } = fakeWindow();
    const request = vi.fn(async (url: RequestInfo | URL) =>
      String(url) === '/api/access/session' ? session('session') : reply({}),
    );
    keepAccessSessionRenewed(target, request as typeof fetch, () => now);
    await vi.waitFor(() => expect(request).toHaveBeenCalledTimes(2));
    expect(request.mock.calls[1]).toEqual([
      '/api/access/session/refresh',
      expect.objectContaining({ method: 'POST', credentials: 'same-origin' }),
    ]);
    await settle();

    now = ACCESS_RENEW_MS - 1;
    tick();
    expect(request).toHaveBeenCalledTimes(2);
    now = ACCESS_RENEW_MS;
    tick();
    await vi.waitFor(() => expect(request).toHaveBeenCalledTimes(3));
  });

  it('never renews for the owner on this computer', async () => {
    const { target } = fakeWindow();
    const request = vi.fn(async () => session('owner'));
    keepAccessSessionRenewed(target, request as typeof fetch, () => 0);
    await vi.waitFor(() => expect(request).toHaveBeenCalledTimes(1));
    await settle();
    expect(target.setInterval).not.toHaveBeenCalled();
    expect(request).toHaveBeenCalledTimes(1);
  });

  it('stops for good once the server says the session has ended', async () => {
    let now = 0;
    const { target, tick, listeners } = fakeWindow();
    const request = vi.fn(async (url: RequestInfo | URL) =>
      String(url) === '/api/access/session'
        ? session('session')
        : reply({}, 401),
    );
    keepAccessSessionRenewed(target, request as typeof fetch, () => now);
    await vi.waitFor(() => expect(target.clearInterval).toHaveBeenCalled());
    expect(listeners.size).toBe(0);

    now = 10 * ACCESS_RENEW_MS;
    tick();
    expect(request).toHaveBeenCalledTimes(2);
  });
});
