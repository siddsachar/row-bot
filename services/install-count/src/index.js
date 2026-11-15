/**
 * Row-Bot's installation count (N6): a Cloudflare Worker with D1.
 *
 * - POST /v1/ping   {schema: 1, install, version, os}: one row per installation per UTC day,
 *   keyed by an HMAC of the install ID, and the day's count for the country Cloudflare saw (never
 *   kept with the installation). 204, nothing echoed back.
 * - POST /v1/delete {schema: 1, install, secret}: deletes every row of that installation once
 *   the secret matches the ID (the ID is the first 128 bits of SHA-256 over the secret).
 * - GET  /v1/stats  (Authorization: Bearer <STATS_TOKEN>): installations reporting in the last
 *   7 and 15 days, by version and by OS, and today's and yesterday's by country.
 * - GET  /v1/totals (public): installations reporting in the last 7 and 15 days, overall and by OS,
 *   and nothing else (no versions, countries or days), for the owner's own reports.
 * - Daily (cron): the day before's totals are kept; rows older than 35 days are deleted.
 *
 * Hardening: a 1 KB body cap, a strict schema, Cloudflare's rate limiter per address, nothing
 * logged (no console calls; Workers Logs and Logpush are off in wrangler.toml), no request
 * bodies, IDs or addresses stored, and empty answers.
 */

export const MAX_BODY = 1024;
export const KEEP_DAYS = 35;
const SYSTEMS = new Set(['windows', 'macos', 'linux', 'docker']);
const INSTALL = /^[0-9a-f]{32}$/;
const SECRET = /^[0-9a-f]{64}$/;
const VERSION = /^[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}(?:[-.+]?[0-9A-Za-z][0-9A-Za-z.-]{0,23})?$/;
// Cloudflare's two-letter code for the connection's country ("T1" for Tor); anything else is "XX".
const COUNTRY = /^[A-Z][A-Z0-9]$/;
const ID_PREFIX = new TextEncoder().encode('row-bot-install-count:');
const DAY_MS = 24 * 60 * 60 * 1000;

const answer = (status, body = null) =>
  new Response(body === null ? null : JSON.stringify(body), {
    status,
    headers: {
      'cache-control': 'no-store',
      ...(body === null ? {} : { 'content-type': 'application/json' }),
    },
  });

/** The UTC day of a moment, `YYYY-MM-DD`. */
export const dayOf = (moment) => new Date(moment).toISOString().slice(0, 10);
const daysBefore = (day, days) => dayOf(Date.parse(`${day}T00:00:00Z`) - days * DAY_MS);

const hex = (buffer) => [...new Uint8Array(buffer)].map((byte) => byte.toString(16).padStart(2, '0')).join('');
const bytes = (text) => Uint8Array.from(text.match(/../g), (pair) => parseInt(pair, 16));

/** The install ID that belongs to a deletion secret (the client makes it the same way). */
export async function installId(secret) {
  const joined = new Uint8Array([...ID_PREFIX, ...bytes(secret)]);
  return hex(await crypto.subtle.digest('SHA-256', joined)).slice(0, 32);
}

async function idHash(env, install) {
  const key = await crypto.subtle.importKey(
    'raw', new TextEncoder().encode(env.ID_HMAC_KEY), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign'],
  );
  return hex(await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(install)));
}

async function same(left, right) {
  const encode = async (value) => crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  return crypto.subtle.timingSafeEqual(await encode(left), await encode(right));
}

/** The JSON body, read up to the cap, or the status that refuses it. */
async function readBody(request) {
  if (request.method !== 'POST') return { refused: 405 };
  const type = (request.headers.get('content-type') ?? '').split(';')[0].trim().toLowerCase();
  if (type !== 'application/json') return { refused: 415 };
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
  try {
    const body = JSON.parse(new TextDecoder().decode(new Uint8Array(parts.flatMap((part) => [...part]))));
    return body !== null && typeof body === 'object' && !Array.isArray(body) ? { body } : { refused: 400 };
  } catch {
    return { refused: 400 };
  }
}

const exactly = (body, keys) =>
  Object.keys(body).length === keys.length && keys.every((key) => Object.hasOwn(body, key));

async function limited(request, env) {
  if (!env.LIMIT) return false;
  // Counted by Cloudflare's rate limiter for a minute; the Worker never keeps the address.
  const { success } = await env.LIMIT.limit({ key: request.headers.get('cf-connecting-ip') ?? 'unknown' });
  return !success;
}

async function ping(request, env) {
  if (await limited(request, env)) return answer(429);
  const { body, refused } = await readBody(request);
  if (refused) return answer(refused);
  if (
    !exactly(body, ['schema', 'install', 'version', 'os']) ||
    body.schema !== 1 ||
    typeof body.install !== 'string' || !INSTALL.test(body.install) ||
    typeof body.version !== 'string' || body.version.length > 40 || !VERSION.test(body.version) ||
    !SYSTEMS.has(body.os)
  )
    return answer(400);
  if (!env.ID_HMAC_KEY) return answer(503);
  const hash = await idHash(env, body.install);
  const day = dayOf(Date.now());
  const seen = request.cf?.country;
  const country = typeof seen === 'string' && COUNTRY.test(seen) ? seen : 'XX';
  await env.DB.batch([
    // The country counts once per installation per day, before its row exists, and is never
    // written next to the installation.
    env.DB.prepare(
      `INSERT INTO countries (day, country, installs)
       SELECT ?1, ?2, 1 WHERE NOT EXISTS (SELECT 1 FROM pings WHERE id_hash = ?3 AND day = ?1)
       ON CONFLICT (day, country) DO UPDATE SET installs = installs + 1`,
    ).bind(day, country, hash),
    env.DB.prepare(
      `INSERT INTO pings (id_hash, day, version, os) VALUES (?1, ?2, ?3, ?4)
       ON CONFLICT (id_hash, day) DO UPDATE SET version = excluded.version, os = excluded.os`,
    ).bind(hash, day, body.version, body.os),
  ]);
  return answer(204);
}

async function remove(request, env) {
  if (await limited(request, env)) return answer(429);
  const { body, refused } = await readBody(request);
  if (refused) return answer(refused);
  if (
    !exactly(body, ['schema', 'install', 'secret']) ||
    body.schema !== 1 ||
    typeof body.install !== 'string' || !INSTALL.test(body.install) ||
    typeof body.secret !== 'string' || !SECRET.test(body.secret)
  )
    return answer(400);
  if (!(await same(await installId(body.secret), body.install))) return answer(403);
  if (!env.ID_HMAC_KEY) return answer(503);
  await env.DB.prepare('DELETE FROM pings WHERE id_hash = ?1').bind(await idHash(env, body.install)).run();
  return answer(204);
}

/**
 * Installations reporting in the 7 and 15 days up to `day`, overall, by version and by OS. Each
 * installation counts once, under the version and OS of its latest day in the window.
 */
export async function totals(db, day) {
  const { results } = await db.prepare(
    `WITH latest AS (
       SELECT id_hash, version, os, MAX(day) AS last FROM pings WHERE day BETWEEN ?1 AND ?3 GROUP BY id_hash
     )
     SELECT 'all' AS kind, '' AS name, COALESCE(SUM(last >= ?2), 0) AS active_7d, COUNT(*) AS active_15d FROM latest
     UNION ALL SELECT 'version', version, SUM(last >= ?2), COUNT(*) FROM latest GROUP BY version
     UNION ALL SELECT 'os', os, SUM(last >= ?2), COUNT(*) FROM latest GROUP BY os`,
  )
    .bind(daysBefore(day, 14), daysBefore(day, 6), day)
    .all();
  const view = { active_7d: 0, active_15d: 0, by_version: {}, by_os: {}, as_of: day };
  for (const row of results) {
    const counts = { active_7d: row.active_7d, active_15d: row.active_15d };
    if (row.kind === 'all') Object.assign(view, counts);
    else view[row.kind === 'version' ? 'by_version' : 'by_os'][row.name] = counts;
  }
  return view;
}

/** Installations reporting on `day` and the day before, by country: `{GB: {today, yesterday}}`. */
export async function countries(db, day) {
  const yesterday = daysBefore(day, 1);
  const { results } = await db.prepare('SELECT day, country, installs FROM countries WHERE day IN (?1, ?2)')
    .bind(day, yesterday)
    .all();
  const view = {};
  for (const row of results) {
    view[row.country] ??= { today: 0, yesterday: 0 };
    view[row.country][row.day === day ? 'today' : 'yesterday'] = row.installs;
  }
  return view;
}

async function stats(request, env) {
  if (request.method !== 'GET') return answer(405);
  const header = request.headers.get('authorization') ?? '';
  const given = header.startsWith('Bearer ') ? header.slice('Bearer '.length) : '';
  if (!env.STATS_TOKEN || !given || !(await same(given, env.STATS_TOKEN))) return answer(401);
  const today = dayOf(Date.now());
  return answer(200, { ...(await totals(env.DB, today)), by_country: await countries(env.DB, today) });
}

/** Public: the 7- and 15-day totals, overall and by OS, and nothing else (the owner, 29 Oct 2026). */
async function publicTotals(request, env) {
  if (request.method !== 'GET') return answer(405);
  if (await limited(request, env)) return answer(429);
  const { active_7d, active_15d, by_os, as_of } = await totals(env.DB, dayOf(Date.now()));
  return answer(200, { active_7d, active_15d, by_os, as_of });
}

/** The daily run on `today`: keep the day before's totals, then delete rows older than 35 days. */
export async function daily(db, today) {
  const yesterday = daysBefore(today, 1);
  const view = await totals(db, yesterday);
  const rows = [
    ['all', '', view],
    ...Object.entries(view.by_version).map(([name, counts]) => ['version', name, counts]),
    ...Object.entries(view.by_os).map(([name, counts]) => ['os', name, counts]),
  ];
  const keep = db.prepare(
    'INSERT OR REPLACE INTO totals (day, kind, name, active_7d, active_15d) VALUES (?1, ?2, ?3, ?4, ?5)',
  );
  await db.batch([
    ...rows.map(([kind, name, counts]) => keep.bind(yesterday, kind, name, counts.active_7d, counts.active_15d)),
    db.prepare('DELETE FROM pings WHERE day < ?1').bind(daysBefore(today, KEEP_DAYS)),
  ]);
}

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    try {
      if (pathname === '/v1/ping') return await ping(request, env);
      if (pathname === '/v1/delete') return await remove(request, env);
      if (pathname === '/v1/stats') return await stats(request, env);
      if (pathname === '/v1/totals') return await publicTotals(request, env);
      return answer(404);
    } catch {
      // Nothing about the request is logged.
      return answer(500);
    }
  },
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(daily(env.DB, dayOf(controller.scheduledTime)));
  },
};
