/** How often a signed-in device renews (the server's SESSION_REFRESH_POLL_INTERVAL). */
export const ACCESS_RENEW_MS = 12 * 60 * 60 * 1000;
const CHECK_MS = 60 * 1000;
/** After a failed attempt (offline, server restarting), try again this soon. */
const RETRY_MS = 10 * 60 * 1000;

/**
 * Keep a trusted access session alive (B137). A phone or another computer
 * signed in through an invitation holds a 30-day session that the server
 * extends only when asked during its last week; the NiceGUI page asked every
 * 12 hours, the React client never did, so devices were signed out after 30
 * days however often they were used. Only a browser signed in with an access
 * session renews: the owner on this computer has none. The request never
 * carries or changes a secret, and it stops for good once the server says the
 * session has ended (it is never revived).
 */
export function keepAccessSessionRenewed(
  target: Window = window,
  request: typeof fetch = (input, init) => target.fetch(input, init),
  now: () => number = () => Date.now(),
): () => void {
  let active = true;
  let running = false;
  let last = -Infinity;
  let timer: number | undefined;

  const stop = () => {
    active = false;
    if (timer !== undefined) target.clearInterval(timer);
    target.removeEventListener('focus', due);
    target.document.removeEventListener('visibilitychange', due);
  };

  async function renew() {
    running = true;
    try {
      const response = await request('/api/access/session/refresh', {
        method: 'POST',
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { Accept: 'application/json' },
      });
      if (response.status === 401 || response.status === 403) stop();
      else last = response.ok ? now() : now() - ACCESS_RENEW_MS + RETRY_MS;
    } catch {
      last = now() - ACCESS_RENEW_MS + RETRY_MS;
    } finally {
      running = false;
    }
  }

  function due() {
    if (active && !running && now() - last >= ACCESS_RENEW_MS) void renew();
  }

  void (async () => {
    try {
      const response = await request('/api/access/session', {
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { Accept: 'application/json' },
      });
      const session = (await response.json()) as {
        authenticated?: unknown;
        authentication_kind?: unknown;
      };
      const invited =
        session.authenticated === true &&
        session.authentication_kind === 'session';
      if (!active || !invited) return stop();
    } catch {
      return stop();
    }
    // Renew at once (a device opened in its last week), then twice a day,
    // and when a sleeping phone or tab comes back.
    due();
    timer = target.setInterval(due, CHECK_MS);
    target.addEventListener('focus', due);
    target.document.addEventListener('visibilitychange', due);
  })();
  return stop;
}
