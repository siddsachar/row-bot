import { describe, expect, it, vi } from 'vitest';
import { claimLauncherSignIn, forgetSetupCode } from './sign-in';

function fakeWindow(hash: string, extra: Record<string, unknown> = {}) {
  const replaceState = vi.fn();
  const target = {
    location: { hash, pathname: '/app-v2/', search: '?m=1' },
    history: { state: { key: 'k' }, replaceState },
    ...extra,
  } as unknown as Window;
  return { target, replaceState };
}

const reply = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status });

describe('claimLauncherSignIn', () => {
  it('claims the invitation once and removes it from the address', async () => {
    const { target, replaceState } = fakeWindow('#sign-in=rbi_v1.abc%2Edef');
    const request = vi.fn(
      async (url: RequestInfo | URL, _init?: RequestInit) =>
        String(url) === '/api/access/session'
          ? reply({ authenticated: true, may_act: false })
          : reply({ ok: true }),
    );
    await claimLauncherSignIn(target, request as typeof fetch);
    expect(replaceState).toHaveBeenCalledWith({ key: 'k' }, '', '/app-v2/?m=1');
    expect(request).toHaveBeenCalledTimes(2);
    const [url, init] = request.mock.calls[1] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe('/api/access/invitations/claim');
    expect(init).toEqual(
      expect.objectContaining({ method: 'POST', credentials: 'same-origin' }),
    );
    expect(JSON.parse(String(init.body))).toEqual({
      invitation: 'rbi_v1.abc.def',
      display_name: 'Browser on this computer',
    });
  });

  it('names the desktop window and keeps the rest of the address', async () => {
    const { target, replaceState } = fakeWindow('#panel=files&sign-in=tok', {
      pywebview: {},
    });
    const request = vi.fn(
      async (url: RequestInfo | URL, _init?: RequestInit) =>
        String(url) === '/api/access/session'
          ? reply({ authenticated: true, may_act: false })
          : reply({ ok: true }),
    );
    await claimLauncherSignIn(target, request as typeof fetch);
    expect(replaceState).toHaveBeenCalledWith(
      { key: 'k' },
      '',
      '/app-v2/?m=1#panel=files',
    );
    const body = JSON.parse(
      String((request.mock.calls[1][1] as RequestInit).body),
    );
    expect(body.display_name).toBe('Row-Bot window');
  });

  it('leaves the invitation unused when this tab is already signed in', async () => {
    const { target, replaceState } = fakeWindow('#sign-in=tok');
    const request = vi.fn(async () =>
      reply({ authenticated: true, may_act: true }),
    );
    await claimLauncherSignIn(target, request as typeof fetch);
    expect(replaceState).toHaveBeenCalled();
    expect(request).toHaveBeenCalledTimes(1);
  });

  it('does nothing without a sign-in in the address', async () => {
    const { target, replaceState } = fakeWindow('#panel=files');
    const request = vi.fn();
    await claimLauncherSignIn(target, request as unknown as typeof fetch);
    expect(replaceState).not.toHaveBeenCalled();
    expect(request).not.toHaveBeenCalled();
  });

  it('still opens the page when the server cannot be reached', async () => {
    const { target } = fakeWindow('#sign-in=tok');
    const request = vi.fn(async () => {
      throw new TypeError('offline');
    });
    await expect(
      claimLauncherSignIn(target, request as unknown as typeof fetch),
    ).resolves.toBeUndefined();
  });
});

describe('forgetSetupCode', () => {
  it('takes a setup link’s used code out of the address, and keeps the rest (A1)', () => {
    for (const [hash, kept] of [
      ['#code=ABCD-EFGH-JKMN', '/app-v2/?m=1'],
      ['#panel=files&code=ABCD-EFGH-JKMN', '/app-v2/?m=1#panel=files'],
      ['#code=ABCD-EFGH-JKMN&panel=files', '/app-v2/?m=1#panel=files'],
    ]) {
      const { target, replaceState } = fakeWindow(hash);
      forgetSetupCode(target);
      expect(replaceState).toHaveBeenCalledWith({ key: 'k' }, '', kept);
    }
  });

  it('leaves an address without a setup code alone', () => {
    for (const hash of ['', '#panel=files', '#barcode=1', '#sign-in=tok']) {
      const { target, replaceState } = fakeWindow(hash);
      forgetSetupCode(target);
      expect(replaceState).not.toHaveBeenCalled();
    }
  });
});
