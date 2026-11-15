/**
 * The relay for row-bot.ai/railway/ ("Continue with Railway"): a Cloudflare Worker.
 *
 * Railway's GraphQL API answers browsers only on railway.com, so the page's calls pass through here.
 *
 * - OPTIONS /graphql: the CORS preflight, answered only for the origins in ALLOWED_ORIGINS.
 * - POST /graphql: a JSON body of at most 64 KB, `{query, variables?, operationName?}`, from an
 *   allowed origin, with `Authorization: Bearer <token>`. The query must be one of the operations
 *   the page makes (operations.js). The body and the Authorization header go to Railway's API as they
 *   came; nothing else from the request does (no cookies, no address, no other headers). Railway's
 *   status and body come back; none of its headers do.
 * - About 60 calls a minute from one address (Cloudflare's rate limiter); beyond that, 429.
 *
 * Nothing is kept or logged: no console calls, no storage bindings (the rate limiter's counter lasts a
 * minute inside Cloudflare), and Workers Logs, traces and Logpush are off in wrangler.toml.
 */

import { isAllowed } from './operations.js';

export const UPSTREAM = 'https://backboard.railway.com/graphql/v2';
export const MAX_BODY = 64 * 1024;
const MAX_AUTHORIZATION = 4096;
const BEARER = /^Bearer [\x21-\x7e]+$/;
const DEFAULT_ORIGINS = 'https://row-bot.ai';

/** The origins allowed to call the relay, from the environment (separated by spaces or commas). */
export function allowedOrigins(env) {
  const listed = String(env?.ALLOWED_ORIGINS ?? DEFAULT_ORIGINS).split(/[\s,]+/).filter(Boolean);
  return new Set(listed);
}

function corsHeaders(origin) {
  return {
    'access-control-allow-origin': origin,
    'access-control-allow-methods': 'POST',
    'access-control-allow-headers': 'authorization, content-type',
    'access-control-max-age': '600',
    vary: 'Origin',
  };
}

function answer(status, message, origin = null, extra = {}) {
  return new Response(message === null ? null : JSON.stringify({ errors: [{ message }] }), {
    status,
    headers: {
      'cache-control': 'no-store',
      ...(message === null ? {} : { 'content-type': 'application/json' }),
      ...(origin ? corsHeaders(origin) : {}),
      ...extra,
    },
  });
}

/** The body as text, read up to the cap, or the status that refuses it. */
async function readBody(request) {
  if (Number(request.headers.get('content-length') ?? 0) > MAX_BODY) return { refused: 413 };
  const reader = request.body?.getReader();
  const parts = [];
  let size = 0;
  while (reader) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > MAX_BODY) {
      await reader.cancel();
      return { refused: 413 };
    }
    parts.push(value);
  }
  const joined = new Uint8Array(size);
  let offset = 0;
  for (const part of parts) {
    joined.set(part, offset);
    offset += part.byteLength;
  }
  try {
    return { text: new TextDecoder('utf-8', { fatal: true }).decode(joined) };
  } catch {
    return { refused: 400 };
  }
}

/** True for `{query, variables?, operationName?}` with nothing else, and a query the page makes. */
function isPageCall(text) {
  let body;
  try {
    body = JSON.parse(text);
  } catch {
    return false;
  }
  if (body === null || typeof body !== 'object' || Array.isArray(body)) return false;
  const keys = Object.keys(body);
  if (!keys.every((key) => key === 'query' || key === 'variables' || key === 'operationName')) return false;
  if (body.variables !== undefined && body.variables !== null
      && (typeof body.variables !== 'object' || Array.isArray(body.variables))) return false;
  if (body.operationName !== undefined && body.operationName !== null && typeof body.operationName !== 'string') {
    return false;
  }
  return isAllowed(body.query);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== '/graphql') return answer(404, null);

    const origin = request.headers.get('origin');
    const allowed = origin !== null && allowedOrigins(env).has(origin) ? origin : null;

    if (request.method === 'OPTIONS') {
      const asked = (request.headers.get('access-control-request-method') ?? '').toUpperCase();
      if (!allowed || asked !== 'POST') return answer(403, null);
      return new Response(null, { status: 204, headers: { 'cache-control': 'no-store', ...corsHeaders(allowed) } });
    }
    if (request.method !== 'POST') return answer(405, null, allowed, { allow: 'POST, OPTIONS' });
    if (!allowed) return answer(403, 'This relay only answers row-bot.ai/railway/.');

    // Counted by Cloudflare's rate limiter for a minute, keyed by the caller's address; the Worker keeps nothing.
    const { success } = await env.LIMIT.limit({ key: request.headers.get('cf-connecting-ip') ?? 'unknown' });
    if (!success) return answer(429, 'Too many requests. Wait a minute, then try again.', allowed, { 'retry-after': '60' });

    const type = (request.headers.get('content-type') ?? '').split(';')[0].trim().toLowerCase();
    if (type !== 'application/json') return answer(415, 'Send JSON.', allowed);

    const authorization = request.headers.get('authorization') ?? '';
    if (authorization.length > MAX_AUTHORIZATION || !BEARER.test(authorization)) {
      return answer(401, 'Sign in to Railway first.', allowed);
    }

    const { text, refused } = await readBody(request);
    if (refused) return answer(refused, refused === 413 ? 'Too large.' : 'Not JSON.', allowed);
    if (!isPageCall(text)) return answer(400, 'This relay only passes the calls row-bot.ai/railway/ makes.', allowed);

    let upstream;
    try {
      upstream = await fetch(UPSTREAM, {
        method: 'POST',
        headers: { 'content-type': 'application/json', authorization, 'user-agent': 'row-bot-railway-relay' },
        body: text,
        redirect: 'manual',
      });
    } catch {
      return answer(502, "Couldn't reach Railway.", allowed);
    }
    return new Response(upstream.body, {
      status: upstream.status,
      headers: { 'content-type': 'application/json', 'cache-control': 'no-store', ...corsHeaders(allowed) },
    });
  },
};
