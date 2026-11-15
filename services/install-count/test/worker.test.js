import { createExecutionContext, createScheduledController, env, SELF, waitOnExecutionContext } from 'cloudflare:test';
import { describe, expect, it } from 'vitest';
import worker, { dayOf, installId, totals } from '../src/index.js';

const ORIGIN = 'https://row-bot-count.example.workers.dev';
let addresses = 0;

/** Each test sends from its own address, so the rate limiter only meets the test that is about it. */
const address = () => `192.0.2.${++addresses}`;

const secretOf = (n) => n.toString(16).padStart(64, '0');

function send(path, body, { from = address(), type = 'application/json', method = 'POST', headers = {} } = {}) {
  return SELF.fetch(`${ORIGIN}${path}`, {
    method,
    headers: { 'content-type': type, 'cf-connecting-ip': from, ...headers },
    body: method === 'GET' ? undefined : typeof body === 'string' ? body : JSON.stringify(body),
  });
}

const ping = (install, extra = {}) =>
  send('/v1/ping', { schema: 1, install, version: '5.3.0', os: 'windows', ...extra });

async function rows() {
  const { results } = await env.DB.prepare('SELECT id_hash, day, version, os FROM pings ORDER BY day').all();
  return results;
}

/** A row as the Worker writes it, on a given day (the hash need not be a real one here). */
const seed = (hash, day, version = '5.3.0', os = 'windows') =>
  env.DB.prepare('INSERT INTO pings (id_hash, day, version, os) VALUES (?1, ?2, ?3, ?4)').bind(hash, day, version, os).run();

const before = (day, days) => dayOf(Date.parse(`${day}T00:00:00Z`) - days * 86_400_000);

describe('ping', () => {
  it('stores a keyed hash of the ID with the day, version and OS, and echoes nothing', async () => {
    const install = await installId(secretOf(1));
    const response = await ping(install);
    expect(response.status).toBe(204);
    expect(await response.text()).toBe('');
    const stored = await rows();
    expect(stored).toHaveLength(1);
    expect(stored[0]).toMatchObject({ day: dayOf(Date.now()), version: '5.3.0', os: 'windows' });
    expect(stored[0].id_hash).toMatch(/^[0-9a-f]{64}$/);
    expect(JSON.stringify(stored)).not.toContain(install);
  });

  it('accepts exactly the schema and nothing else', async () => {
    const install = await installId(secretOf(2));
    const refused = [
      { schema: 1, install, version: '5.3.0' },
      { schema: 1, install, version: '5.3.0', os: 'windows', ip: '203.0.113.9' },
      { schema: 2, install, version: '5.3.0', os: 'windows' },
      { schema: '1', install, version: '5.3.0', os: 'windows' },
      { schema: 1, install: install.toUpperCase(), version: '5.3.0', os: 'windows' },
      { schema: 1, install: install.slice(1), version: '5.3.0', os: 'windows' },
      { schema: 1, install, version: 'latest', os: 'windows' },
      { schema: 1, install, version: `5.3.0-${'a'.repeat(40)}`, os: 'windows' },
      { schema: 1, install, version: '5.3.0', os: 'android' },
      [1, install, '5.3.0', 'windows'],
    ];
    for (const body of refused) {
      const response = await send('/v1/ping', body);
      expect(response.status, JSON.stringify(body)).toBe(400);
      expect(await response.text()).toBe('');
    }
    expect((await send('/v1/ping', '{not json')).status).toBe(400);
    expect((await send('/v1/ping', 'x', { type: 'text/plain' })).status).toBe(415);
    expect((await send('/v1/ping', null, { method: 'GET' })).status).toBe(405);
    expect((await send('/v1/somewhere', {})).status).toBe(404);
    for (const version of ['5.3.0', '5.3.0b1', '5.3.0-beta.2', '5.3.0.dev0', '10.0.12']) {
      expect((await ping(install, { version })).status, version).toBe(204);
    }
    for (const os of ['macos', 'linux', 'docker']) expect((await ping(install, { os })).status).toBe(204);
  });

  it('refuses a body over 1 KB', async () => {
    const install = await installId(secretOf(3));
    const large = JSON.stringify({ schema: 1, install, version: '5.3.0', os: 'windows', pad: 'x'.repeat(1100) });
    const response = await send('/v1/ping', large);
    expect(response.status).toBe(413);
    expect(await rows()).toHaveLength(0);
  });

  it('keeps one row per installation per day, with its latest version', async () => {
    const install = await installId(secretOf(4));
    await ping(install);
    await ping(install, { version: '5.3.1', os: 'windows' });
    await ping(install, { version: '5.3.1', os: 'windows' });
    const stored = await rows();
    expect(stored).toHaveLength(1);
    expect(stored[0].version).toBe('5.3.1');
    await ping(await installId(secretOf(5)));
    expect(await rows()).toHaveLength(2);
  });

  it('limits how often one address may send', async () => {
    const from = address();
    const install = await installId(secretOf(6));
    const body = { schema: 1, install, version: '5.3.0', os: 'windows' };
    const statuses = [];
    for (let n = 0; n < 25; n++) statuses.push((await send('/v1/ping', body, { from })).status);
    expect(statuses[0]).toBe(204);
    expect(statuses.at(-1)).toBe(429);
    // Another address is not held back.
    expect((await ping(install)).status).toBe(204);
  });

  it('never stores an ID without its key', async () => {
    const response = await worker.fetch(
      new Request(`${ORIGIN}/v1/ping`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ schema: 1, install: await installId(secretOf(7)), version: '5.3.0', os: 'linux' }),
      }),
      { ...env, ID_HMAC_KEY: '' },
    );
    expect(response.status).toBe(503);
    expect(await rows()).toHaveLength(0);
  });
});

describe('delete', () => {
  it('derives the ID from the secret as Row-Bot does', async () => {
    // The same vectors are checked against row_bot.install_count.install_id.
    expect(await installId(`${'0'.repeat(63)}1`)).toBe('2271477d6128edbe27a9239e54f22a90');
    expect(await installId('ab'.repeat(32))).toBe('1144938174f096fa7ad56132be06b552');
  });

  it('deletes every row of the installation once the secret matches its ID', async () => {
    const secret = secretOf(10);
    const install = await installId(secret);
    const other = await installId(secretOf(11));
    await ping(install);
    await ping(other);
    const { results } = await env.DB.prepare('SELECT id_hash FROM pings').all();
    const mine = results.map((row) => row.id_hash);
    // An older day of the same installation goes too.
    await env.DB.prepare('INSERT INTO pings SELECT id_hash, ?1, version, os FROM pings').bind(before(dayOf(Date.now()), 3)).run();
    expect(await rows()).toHaveLength(4);

    const wrong = await send('/v1/delete', { schema: 1, install, secret: secretOf(12) });
    expect(wrong.status).toBe(403);
    expect(await rows()).toHaveLength(4);

    const deleted = await send('/v1/delete', { schema: 1, install, secret });
    expect(deleted.status).toBe(204);
    expect(await deleted.text()).toBe('');
    const left = await rows();
    expect(left).toHaveLength(2);
    expect(new Set(left.map((row) => row.id_hash)).size).toBe(1);
    expect(mine).toContain(left[0].id_hash);
    // Deleting again says the same: nothing tells whether anything was held.
    expect((await send('/v1/delete', { schema: 1, install, secret })).status).toBe(204);
  });

  it('accepts exactly the schema', async () => {
    const secret = secretOf(13);
    const install = await installId(secret);
    for (const body of [
      { schema: 1, install },
      { schema: 1, install, secret, extra: true },
      { schema: 1, install, secret: secret.slice(2) },
    ])
      expect((await send('/v1/delete', body)).status).toBe(400);
  });
});

describe('stats', () => {
  const token = { authorization: 'Bearer test-only-stats-token' };
  const stats = (headers) => send('/v1/stats', null, { method: 'GET', headers });

  it('needs the owner token', async () => {
    expect((await stats({})).status).toBe(401);
    expect((await stats({ authorization: 'Bearer nope' })).status).toBe(401);
    expect((await stats({ authorization: 'test-only-stats-token' })).status).toBe(401);
    const response = await stats(token);
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      active_7d: 0, active_15d: 0, by_version: {}, by_os: {}, as_of: dayOf(Date.now()), by_country: {},
    });
  });

  it('counts installations reporting in the last 7 and 15 days, by version and by OS', async () => {
    const today = dayOf(Date.now());
    await seed('a', today);
    await seed('a', before(today, 1));
    await seed('b', before(today, 6), '5.3.0', 'macos');
    await seed('c', before(today, 7), '5.2.1', 'linux');
    await seed('d', before(today, 14), '5.2.1', 'docker');
    await seed('e', before(today, 15), '5.2.1', 'windows');
    // An installation that updated during the window counts once, under its latest version.
    await seed('f', before(today, 9), '5.2.1', 'windows');
    await seed('f', before(today, 2), '5.3.0', 'windows');
    const view = await (await stats(token)).json();
    expect(view).toEqual({
      active_7d: 3,
      active_15d: 5,
      by_version: { '5.3.0': { active_7d: 3, active_15d: 3 }, '5.2.1': { active_7d: 0, active_15d: 2 } },
      by_os: {
        windows: { active_7d: 2, active_15d: 2 },
        macos: { active_7d: 1, active_15d: 1 },
        linux: { active_7d: 0, active_15d: 1 },
        docker: { active_7d: 0, active_15d: 1 },
      },
      as_of: today,
      by_country: {},
    });
  });

  it('says how many installations reported from each country today and yesterday', async () => {
    const today = dayOf(Date.now());
    const count = (day, country, installs) =>
      env.DB.prepare('INSERT INTO countries (day, country, installs) VALUES (?1, ?2, ?3)').bind(day, country, installs).run();
    await count(today, 'GB', 3);
    await count(before(today, 1), 'GB', 4);
    await count(before(today, 1), 'US', 2);
    await count(before(today, 2), 'FR', 9);
    expect((await (await stats(token)).json()).by_country).toEqual({
      GB: { today: 3, yesterday: 4 },
      US: { today: 0, yesterday: 2 },
    });
  });
});

describe('public totals', () => {
  const totalsPage = (options = {}) => send('/v1/totals', null, { method: 'GET', ...options });

  it('gives the 7- and 15-day totals overall and by system to anyone, and nothing else', async () => {
    const today = dayOf(Date.now());
    await seed('a', today);
    await seed('b', before(today, 6), '5.3.0', 'macos');
    await seed('c', before(today, 9), '5.2.1', 'linux');
    await env.DB.prepare('INSERT INTO countries (day, country, installs) VALUES (?1, ?2, ?3)').bind(today, 'GB', 2).run();
    const response = await totalsPage();
    expect(response.status).toBe(200);
    // No token, and no versions, countries or days beyond the date it counts to.
    expect(await response.json()).toEqual({
      active_7d: 2,
      active_15d: 3,
      by_os: {
        windows: { active_7d: 1, active_15d: 1 },
        macos: { active_7d: 1, active_15d: 1 },
        linux: { active_7d: 0, active_15d: 1 },
      },
      as_of: today,
    });
  });

  it('only reads, and is rate-limited like a ping', async () => {
    expect((await send('/v1/totals', {}, { method: 'POST' })).status).toBe(405);
    const from = address();
    const answers = [];
    for (let i = 0; i < 21; i++) answers.push((await totalsPage({ from })).status);
    expect(answers.slice(0, 20).every((status) => status === 200)).toBe(true);
    expect(answers[20]).toBe(429);
  });
});

describe('countries', () => {
  /** A ping as Cloudflare hands it to the Worker, with the country it worked out. */
  const pingFrom = async (country, install) =>
    worker.fetch(
      new Request(`${ORIGIN}/v1/ping`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'cf-connecting-ip': address() },
        body: JSON.stringify({ schema: 1, install, version: '5.3.0', os: 'windows' }),
        cf: country === undefined ? undefined : { country },
      }),
      env,
    );

  async function counted() {
    const { results } = await env.DB.prepare('SELECT day, country, installs FROM countries ORDER BY country').all();
    return results;
  }

  it('counts each installation once a day under its country, and never keeps the country with it', async () => {
    const day = dayOf(Date.now());
    const first = await installId(secretOf(20));
    expect((await pingFrom('GB', first)).status).toBe(204);
    // A second count the same day, even from elsewhere, adds nothing.
    expect((await pingFrom('FR', first)).status).toBe(204);
    expect((await pingFrom('GB', await installId(secretOf(21)))).status).toBe(204);
    expect((await pingFrom('US', await installId(secretOf(22)))).status).toBe(204);
    expect(await counted()).toEqual([
      { day, country: 'GB', installs: 2 },
      { day, country: 'US', installs: 1 },
    ]);
    // The installation's own rows hold no country.
    const stored = await rows();
    expect(stored).toHaveLength(3);
    expect(JSON.stringify(stored)).not.toMatch(/GB|US|FR/);
  });

  it('counts an unknown or odd country as XX', async () => {
    for (const [n, country] of [[30, undefined], [31, 'gb'], [32, 'GBR'], [33, '<script>']]) {
      expect((await pingFrom(country, await installId(secretOf(n)))).status, String(country)).toBe(204);
    }
    expect(await counted()).toEqual([{ day: dayOf(Date.now()), country: 'XX', installs: 4 }]);
  });

  it('leaves the country counts, which hold no IDs, when an installation deletes what was sent', async () => {
    const secret = secretOf(40);
    const install = await installId(secret);
    await pingFrom('GB', install);
    expect((await send('/v1/delete', { schema: 1, install, secret })).status).toBe(204);
    expect(await rows()).toHaveLength(0);
    expect(await counted()).toEqual([{ day: dayOf(Date.now()), country: 'GB', installs: 1 }]);
  });
});

describe('daily run', () => {
  it('keeps the day before’s totals and deletes rows older than 35 days', async () => {
    const today = '2026-11-20';
    await seed('old', before(today, 36));
    await seed('kept', before(today, 35));
    await seed('a', before(today, 1), '5.3.0', 'macos');
    await seed('b', before(today, 3), '5.3.0', 'windows');
    const controller = createScheduledController({ scheduledTime: Date.parse(`${today}T03:17:00Z`), cron: '17 3 * * *' });
    const ctx = createExecutionContext();
    await worker.scheduled(controller, env, ctx);
    await waitOnExecutionContext(ctx);

    expect((await rows()).map((row) => row.id_hash)).toEqual(['kept', 'b', 'a']);
    const { results } = await env.DB.prepare('SELECT * FROM totals ORDER BY kind, name').all();
    expect(results).toEqual([
      { day: '2026-11-19', kind: 'all', name: '', active_7d: 2, active_15d: 2 },
      { day: '2026-11-19', kind: 'os', name: 'macos', active_7d: 1, active_15d: 1 },
      { day: '2026-11-19', kind: 'os', name: 'windows', active_7d: 1, active_15d: 1 },
      { day: '2026-11-19', kind: 'version', name: '5.3.0', active_7d: 2, active_15d: 2 },
    ]);
    // The totals hold no IDs.
    expect(JSON.stringify(results)).not.toMatch(/"(a|b|kept)"/);
    expect(await totals(env.DB, '2026-11-19')).toMatchObject({ active_7d: 2, active_15d: 2 });
  });
});
