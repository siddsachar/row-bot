/**
 * The launcher's one-time sign-in (A0). On this computer only a window or
 * browser a person opened may change anything, so a program Row-Bot starts
 * can't act as them. The launcher opens Row-Bot with `#sign-in=<invitation>`;
 * this claims it once, then the address no longer carries it. A tab that is
 * already signed in leaves the invitation unused (it expires in two minutes).
 */
const SIGN_IN = /(^|[#&])sign-in=([^&]*)/;

export async function claimLauncherSignIn(
  target: Window = window,
  request: typeof fetch = (input, init) => target.fetch(input, init),
): Promise<void> {
  const { hash, pathname, search } = target.location;
  const found = SIGN_IN.exec(hash);
  if (!found) return;
  // Never left in the address bar, history or a bookmark.
  const rest = hash
    .replace(SIGN_IN, '$1')
    .replace(/^#&/, '#')
    .replace(/[#&]$/, '');
  target.history.replaceState(
    target.history.state,
    '',
    pathname + search + rest,
  );
  let invitation: string;
  try {
    invitation = decodeURIComponent(found[2]);
  } catch {
    return;
  }
  if (!invitation) return;
  try {
    const current = await request('/api/access/session', {
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    });
    const session = (await current.json()) as { may_act?: unknown };
    if (session.may_act === true) return;
    const answer = await request('/api/access/invitations/claim', {
      method: 'POST',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        invitation,
        display_name:
          'pywebview' in target ? 'Row-Bot window' : 'Browser on this computer',
      }),
    });
    await answer.text().catch(() => '');
  } catch {
    // The page still opens; it says it is view-only and how to sign in.
  }
}

const SETUP_CODE = /(^|[#&])code=[^&]*/;

/**
 * A setup page's sign-in link (A1), `/setup#code=…`, opened in a browser
 * that is already signed in, arrives here with its fragment: the setup page
 * hands a signed-in browser to the client, and the browser keeps the
 * fragment. The code, used by then, leaves the address bar and history at
 * once; the rest of the address stays as it is.
 */
export function forgetSetupCode(target: Window = window): void {
  const { hash, pathname, search } = target.location;
  if (!SETUP_CODE.test(hash)) return;
  const rest = hash
    .replace(SETUP_CODE, '$1')
    .replace(/^#&/, '#')
    .replace(/[#&]$/, '');
  target.history.replaceState(
    target.history.state,
    '',
    pathname + search + rest,
  );
}
