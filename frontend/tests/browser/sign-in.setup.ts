import { writeFile } from 'node:fs/promises';
import { request } from '@playwright/test';
import { RENEWED_KEY } from '../../src/api/access-renewal';
import { SIGNED_IN_STATE } from './signed-in';

/**
 * Sign the suite in as the launcher signs in Row-Bot's window: ask the backend
 * for a one-time invitation with the launch secret, claim it, keep the cookie.
 * A backend without a launch secret leaves the suite view-only on purpose.
 *
 * Every test opens a fresh context from this state, yet they are all one
 * device on one session. A real device remembers when it last renewed its
 * session (`api/access-renewal.ts`, in this origin's storage), so its loads
 * don't renew again; a context with empty storage renews on every load, and a
 * burst of tests met the server's limit on session calls (HTTP 429). The state
 * therefore carries the renewal the claim just made, as the device would.
 */
export default async function signIn(): Promise<void> {
  const baseURL =
    process.env.ROW_BOT_BROWSER_BASE_URL ?? 'http://127.0.0.1:4173';
  const secret = process.env.ROW_BOT_LAUNCH_SECRET;
  if (!secret) {
    await writeFile(
      SIGNED_IN_STATE,
      JSON.stringify({ cookies: [], origins: [] }),
    );
    return;
  }
  const origin = new URL(baseURL).origin;
  const context = await request.newContext({ baseURL });
  try {
    const minted = await context.post('/api/launcher/sign-in', {
      headers: { Authorization: `Bearer ${secret}` },
    });
    if (!minted.ok()) throw new Error(`launcher sign-in: ${minted.status()}`);
    const { invitation } = (await minted.json()) as { invitation: string };
    const claimed = await context.post('/api/access/invitations/claim', {
      headers: { Origin: origin, Accept: 'application/json' },
      data: { invitation, display_name: 'Browser tests' },
    });
    if (!claimed.ok()) throw new Error(`claim: ${claimed.status()}`);
    const current = await context.get('/api/access/session', {
      headers: { Accept: 'application/json' },
    });
    if (!current.ok()) throw new Error(`session: ${current.status()}`);
    const { session_id: session } = (await current.json()) as {
      session_id?: unknown;
    };
    if (typeof session !== 'string' || !session)
      throw new Error('session: no session id');
    const state = await context.storageState();
    await writeFile(
      SIGNED_IN_STATE,
      JSON.stringify({
        ...state,
        origins: [
          {
            origin,
            localStorage: [
              {
                name: RENEWED_KEY + session,
                value: String(Date.now()),
              },
            ],
          },
        ],
      }),
    );
  } finally {
    await context.dispose();
  }
}
