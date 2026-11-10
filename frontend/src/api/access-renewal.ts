/** How often a signed-in device renews (the server's SESSION_REFRESH_POLL_INTERVAL). */
export const ACCESS_RENEW_MS = 12 * 60 * 60 * 1000;
const CHECK_MS = 60 * 1000;
/** After a failed attempt (offline, server restarting), try again this soon. */
const RETRY_MS = 10 * 60 * 1000;
/** When each session last renewed, kept across page loads and tabs of this browser. */
export const RENEWED_KEY = 'row-bot.access-renewed.';

function remembered(target: Window, session: string): number {
  try {
    const value = Number(target.localStorage?.getItem(RENEWED_KEY + session));
    return Number.isFinite(value) && value > 0 ? value : -Infinity;
  } catch {
    return -Infinity;
  }
}

function remember(target: Window, session: string, at: number) {
  try {
    target.localStorage?.setItem(RENEWED_KEY + session, String(at));
  } catch {
    // Private windows and blocked storage renew on each load, as before.
  }
}

/**
 * Keep a trusted access session alive (B137). A phone or another computer
 * signed in through an invitation holds a 30-day session that the server
 * extends only when asked during its last week; the NiceGUI page asked every
 * 12 hours, the React client never did, so devices were signed out after 30
 * days however often they were used. Only a browser signed in with an access
 * session renews: a bare connection on this computer has none. The request never
 * carries or changes a secret, and it stops for good once the server says the
 * session has ended (it is never revived). A page load renews only when a
 * renewal is due: the last one is kept per session across loads and tabs, so
 * many quick loads never meet the server's rate limit.
 */
export function keepAccessSessionRenewed(
  target: Window = window,
  request: typeof fetch = (input, init) => target.fetch(input, init),
  now: () => number = () => Date.now(),
): () => void {
  let active = true;
  let running = false;
  let last = -Infinity;
  let session = '';
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
      // Read the small answer so a tab kept open for weeks holds nothing back.
      await response.text().catch(() => '');
      if (response.status === 401 || response.status === 403) stop();
      else if (response.ok) {
        last = now();
        if (session) remember(target, session, last);
      } else last = now() - ACCESS_RENEW_MS + RETRY_MS;
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
      const current = (await response.json()) as {
        authenticated?: unknown;
        authentication_kind?: unknown;
        session_id?: unknown;
      };
      // A window or browser on this computer signed in by the launcher (A0)
      // holds an access session too, and renews like any device.
      const invited =
        current.authenticated === true &&
        (current.authentication_kind === 'session' ||
          (current.authentication_kind === 'local_owner' &&
            typeof current.session_id === 'string' &&
            current.session_id !== ''));
      if (!active || !invited) return stop();
      if (typeof current.session_id === 'string' && current.session_id) {
        session = current.session_id;
        last = remembered(target, session);
      }
    } catch {
      return stop();
    }
    // Renew when due (a device opened in its last week renews at once), then
    // twice a day, and when a sleeping phone or tab comes back.
    due();
    timer = target.setInterval(due, CHECK_MS);
    target.addEventListener('focus', due);
    target.document.addEventListener('visibilitychange', due);
  })();
  return stop;
}
