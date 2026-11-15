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

  it('renews a window on this computer that the launcher signed in', async () => {
    const { target } = fakeWindow();
    const request = vi.fn(async (url: RequestInfo | URL) =>
      String(url) === '/api/access/session'
        ? reply({
            authenticated: true,
            authentication_kind: 'local_owner',
            session_id: 'session-1',
          })
        : reply({}),
    );
    keepAccessSessionRenewed(target, request as typeof fetch, () => 0);
    await vi.waitFor(() => expect(request).toHaveBeenCalledTimes(2));
    expect(request.mock.calls[1][0]).toBe('/api/access/session/refresh');
  });

  it('never renews a connection on this computer without a sign-in', async () => {
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

describe('renewing across page loads', () => {
  it('renews on a page load only when a renewal is due for that session', async () => {
    const stored = new Map<string, string>();
    const storage = {
      getItem: (key: string) => stored.get(key) ?? null,
      setItem: (key: string, value: string) => void stored.set(key, value),
    };
    const answer = (url: RequestInfo | URL) =>
      String(url) === '/api/access/session'
        ? reply({
            authenticated: true,
            authentication_kind: 'session',
            session_id: 'session-1',
          })
        : reply({});
    let now = 1_000;
    const first = fakeWindow();
    Object.assign(first.target, { localStorage: storage });
    const request = vi.fn(async (url: RequestInfo | URL) => answer(url));
    keepAccessSessionRenewed(first.target, request as typeof fetch, () => now);
    await vi.waitFor(() => expect(request).toHaveBeenCalledTimes(2));
    await settle();

    // Many quick page loads of the same session: none renews again.
    for (let load = 0; load < 40; load += 1) {
      const page = fakeWindow();
      Object.assign(page.target, { localStorage: storage });
      const again = vi.fn(async (url: RequestInfo | URL) => answer(url));
      now += 1_000;
      keepAccessSessionRenewed(page.target, again as typeof fetch, () => now);
      await vi.waitFor(() => expect(again).toHaveBeenCalledTimes(1));
      await settle();
      expect(again).toHaveBeenCalledTimes(1);
    }

    // Twelve hours on, the next load renews.
    now = 1_000 + ACCESS_RENEW_MS;
    const later = fakeWindow();
    Object.assign(later.target, { localStorage: storage });
    const due = vi.fn(async (url: RequestInfo | URL) => answer(url));
    keepAccessSessionRenewed(later.target, due as typeof fetch, () => now);
    await vi.waitFor(() => expect(due).toHaveBeenCalledTimes(2));
  });
});
