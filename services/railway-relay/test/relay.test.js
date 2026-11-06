import { env } from 'cloudflare:test';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import worker, { MAX_BODY, UPSTREAM } from '../src/index.js';
import { ALLOWED, isAllowed, operationOf } from '../src/operations.js';

const PAGE = 'https://row-bot.ai';
const RELAY = 'https://row-bot-railway.example.workers.dev';
const TOKEN = 'Bearer test-only-token';
const ME = '{ me { email isVerified workspaces { id name } } }';

let upstream;
let logged;
let addresses = 0;

/** Each call comes from its own address, so the rate limiter only meets the test that is about it. */
const address = () => `2001:db8::${(++addresses).toString(16)}`;

// Every call the Worker makes is answered here; nothing reaches Railway. It logs nothing either.
beforeEach(() => {
  upstream = vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
    new Response('{"data":{"me":{"email":"sam@example.com"}}}', {
      status: 200,
      headers: { 'content-type': 'application/json', 'set-cookie': 'railway=1', 'x-railway-request-id': 'r1' },
    }));
  logged = ['log', 'info', 'warn', 'error', 'debug', 'trace'].map((method) => vi.spyOn(console, method));
});
afterEach(() => {
  for (const spy of logged) expect(spy).not.toHaveBeenCalled();
  vi.restoreAllMocks();
});

function call({ method = 'POST', path = '/graphql', origin = PAGE, body, headers = {}, environment = env } = {}) {
  const sent = { ...(origin === null ? {} : { origin }), 'cf-connecting-ip': address(), ...headers };
  if (method === 'POST' && !('content-type' in sent)) sent['content-type'] = 'application/json';
  if (method === 'POST' && !('authorization' in sent)) sent.authorization = TOKEN;
  const text = body === undefined ? JSON.stringify({ query: ME }) : typeof body === 'string' ? body : JSON.stringify(body);
  return worker.fetch(new Request(`${RELAY}${path}`, {
    method,
    headers: sent,
    body: method === 'POST' ? text : undefined,
  }), environment);
}

describe('CORS', () => {
  it('answers the preflight for the page, and only for POST', async () => {
    const response = await call({
      method: 'OPTIONS',
      headers: { 'access-control-request-method': 'POST', 'access-control-request-headers': 'authorization, content-type' },
    });
    expect(response.status).toBe(204);
    expect(response.headers.get('access-control-allow-origin')).toBe(PAGE);
    expect(response.headers.get('access-control-allow-methods')).toBe('POST');
    expect(response.headers.get('access-control-allow-headers')).toBe('authorization, content-type');
    expect(response.headers.get('vary')).toBe('Origin');

    const asksDelete = await call({ method: 'OPTIONS', headers: { 'access-control-request-method': 'DELETE' } });
    expect(asksDelete.status).toBe(403);
    expect(asksDelete.headers.get('access-control-allow-origin')).toBeNull();
  });

  it('refuses any other origin, and a call with no origin', async () => {
    for (const origin of ['https://evil.example', 'https://row-bot.ai.evil.example', 'http://row-bot.ai', 'null', null]) {
      const preflight = await call({ method: 'OPTIONS', origin, headers: { 'access-control-request-method': 'POST' } });
      expect(preflight.status).toBe(403);
      expect(preflight.headers.get('access-control-allow-origin')).toBeNull();
      const post = await call({ origin });
      expect(post.status).toBe(403);
      expect(post.headers.get('access-control-allow-origin')).toBeNull();
    }
    expect(upstream).not.toHaveBeenCalled();
  });

  it('answers the origins its environment lists, such as the page on loopback in tests', async () => {
    const loopback = 'http://127.0.0.1:8153';
    const environment = { ...env, ALLOWED_ORIGINS: `${PAGE} ${loopback}` };
    const response = await call({ origin: loopback, environment });
    expect(response.status).toBe(200);
    expect(response.headers.get('access-control-allow-origin')).toBe(loopback);
    expect((await call({ origin: loopback })).status).toBe(403);
  });
});

describe('what it accepts', () => {
  it('answers only POST /graphql', async () => {
    expect((await call({ path: '/' })).status).toBe(404);
    expect((await call({ path: '/graphql/v2' })).status).toBe(404);
    const get = await call({ method: 'GET' });
    expect(get.status).toBe(405);
    expect(get.headers.get('allow')).toBe('POST, OPTIONS');
    expect((await call({ method: 'PUT' })).status).toBe(405);
    expect(upstream).not.toHaveBeenCalled();
  });

  it('refuses a body over 64 KB, by its length or as it streams', async () => {
    const big = JSON.stringify({ query: ME, variables: { pad: 'x'.repeat(MAX_BODY) } });
    expect((await call({ body: big })).status).toBe(413);
    const unlabelled = new Request(`${RELAY}/graphql`, {
      method: 'POST',
      headers: { origin: PAGE, 'content-type': 'application/json', authorization: TOKEN },
      body: new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode(big));
          controller.close();
        },
      }),
      duplex: 'half',
    });
    expect((await worker.fetch(unlabelled, env)).status).toBe(413);
    expect(upstream).not.toHaveBeenCalled();
  });

  it('wants JSON and a bearer token', async () => {
    expect((await call({ headers: { 'content-type': 'text/plain' } })).status).toBe(415);
    expect((await call({ headers: { authorization: '' } })).status).toBe(401);
    expect((await call({ headers: { authorization: 'Basic dXNlcjpwYXNz' } })).status).toBe(401);
    expect((await call({ headers: { authorization: `Bearer ${'a'.repeat(5000)}` } })).status).toBe(401);
    expect(upstream).not.toHaveBeenCalled();
  });

  it('refuses anything but {query, variables?, operationName?}', async () => {
    for (const body of [
      'not json',
      '[]',
      JSON.stringify([{ query: ME }]),
      JSON.stringify({ query: ME, extensions: { persistedQuery: { sha256Hash: 'x' } } }),
      JSON.stringify({ variables: {} }),
      JSON.stringify({ query: ME, variables: [1] }),
      JSON.stringify({ query: ME, operationName: 3 }),
    ]) {
      const response = await call({ body });
      expect(response.status, body).toBe(400);
    }
    expect(upstream).not.toHaveBeenCalled();
  });
});

describe('the allow-list', () => {
  const allowed = [
    '{ me { email isVerified workspaces { id name plan preferredRegion customer { isTrialing } } } }',
    'query { regions { name location } }',
    'query Project($id: String!) { project(id: $id) { id services { edges { node { id name } } } } }',
    'query($p: String!, $e: String!, $s: String!) { domains(projectId: $p, environmentId: $e, serviceId: $s) { serviceDomains { domain } } }',
    'query($id: String!) { deployment(id: $id) { status } }',
    'mutation($input: ProjectCreateInput!) { projectCreate(input: $input) { id environments { edges { node { id } } } } }',
    'mutation($input: ServiceCreateInput!) { serviceCreate(input: $input) { id } }',
    'mutation($input: VolumeCreateInput!) { volumeCreate(input: $input) { id } }',
    'mutation($s: String!, $e: String, $input: ServiceInstanceUpdateInput!) { serviceInstanceUpdate(serviceId: $s, environmentId: $e, input: $input) }',
    'mutation($input: ServiceInstanceLimitsUpdateInput!) { serviceInstanceLimitsUpdate(input: $input) }',
    'mutation($input: ServiceDomainCreateInput!) { serviceDomainCreate(input: $input) { domain } }',
    'mutation($e: String!, $patch: EnvironmentConfig, $skip: Boolean) { environmentPatchCommit(environmentId: $e, patch: $patch, skipDeploys: $skip) }',
    'mutation($s: String!, $e: String!) { serviceInstanceDeployV2(serviceId: $s, environmentId: $e) }',
    '# what the page reads\n{ me { email } }',
    '{ me { name: email } }',
    'query { regions { name, location, } }',
    'mutation { serviceCreate(input: {name: "a ) } { projectDelete", variables: {X: """b"""}}) { id } }',
  ];

  it('passes each operation the page makes, and covers every listed name', async () => {
    const seen = new Set();
    for (const query of allowed) {
      expect(isAllowed(query), query).toBe(true);
      const operation = operationOf(query);
      operation.fields.forEach((field) => seen.add(`${operation.kind}:${field}`));
      const response = await call({ body: { query, variables: {} } });
      expect(response.status, query).toBe(200);
    }
    const listed = Object.entries(ALLOWED).flatMap(([kind, fields]) => fields.map((field) => `${kind}:${field}`));
    expect([...seen].sort()).toEqual([...listed].sort());
    expect(upstream).toHaveBeenCalledTimes(allowed.length);
  });

  it('refuses anything else, however it is dressed', async () => {
    const refused = [
      'mutation($id: String!) { projectDelete(id: $id) }',
      'mutation { me: projectDelete(id: "p") }',
      'mutation { serviceCreate(input: {}) { id } projectDelete(id: "p") }',
      'mutation { variableUpsert(input: {}) }',
      'mutation { usageLimitSet(input: {}) }',
      'mutation { me }',
      'query { projectCreate { id } }',
      '{ __schema { types { name } } }',
      '{ __typename }',
      '{ me { email } } mutation { projectDelete(id: "p") }',
      '{ me { email } } { regions { name } }',
      'subscription { deploymentLogs(deploymentId: "d") { message } }',
      'fragment F on User { email } { me { ...F } }',
      '{ ...F }',
      '{ ... on Query { projectDelete(id: "p") } }',
      'query @skip(if: true) { me { email } }',
      '{ me @include(if: true) { email } }',
      '{ me { email }',
      '{ }',
      '',
      '# { me { email } }',
      '{ me { email } } }',
      '{ me { email } } # a comment hides nothing\n}',
      '{ me(x: "unterminated) { email } }',
      '{ me } \u0000',
    ];
    for (const query of refused) {
      expect(isAllowed(query), query).toBe(false);
      const response = await call({ body: { query } });
      expect(response.status, query).toBe(400);
    }
    expect(isAllowed(undefined)).toBe(false);
    expect(isAllowed({ query: ME })).toBe(false);
    expect(upstream).not.toHaveBeenCalled();
  });
});

describe('the rate limit', () => {
  it('lets one address make about 60 calls a minute, then answers 429', async () => {
    const from = { 'cf-connecting-ip': '198.51.100.7' };
    const statuses = [];
    for (let index = 0; index < 61; index += 1) statuses.push((await call({ headers: from })).status);
    expect(statuses.slice(0, 60).every((status) => status === 200)).toBe(true);
    expect(statuses[60]).toBe(429);
    expect(upstream).toHaveBeenCalledTimes(60);

    const limited = await call({ headers: from });
    expect(limited.status).toBe(429);
    expect(limited.headers.get('retry-after')).toBe('60');
    expect(limited.headers.get('access-control-allow-origin')).toBe(PAGE);
    expect(await limited.json()).toEqual({ errors: [{ message: 'Too many requests. Wait a minute, then try again.' }] });

    expect((await call({ headers: { 'cf-connecting-ip': '198.51.100.8' } })).status).toBe(200);
    expect(upstream).toHaveBeenCalledTimes(61);
  });
});

describe('forwarding', () => {
  it("sends the body and the token to Railway's API, and nothing else of the request", async () => {
    const body = JSON.stringify({ query: ME, variables: { a: 1 }, operationName: null });
    const response = await call({
      body,
      headers: {
        authorization: TOKEN,
        cookie: 'session=secret',
        'cf-connecting-ip': '203.0.113.7',
        'x-forwarded-for': '203.0.113.7',
        'x-real-ip': '203.0.113.7',
        referer: 'https://row-bot.ai/railway/?code=abc',
        'user-agent': 'Mozilla/5.0 (visitor)',
        'accept-language': 'en-GB',
      },
    });
    expect(upstream).toHaveBeenCalledTimes(1);
    const [address, init] = upstream.mock.calls[0];
    expect(address).toBe(UPSTREAM);
    expect(UPSTREAM).toBe('https://backboard.railway.com/graphql/v2');
    expect(init.method).toBe('POST');
    expect(init.redirect).toBe('manual');
    expect(init.body).toBe(body);
    expect(init.headers).toEqual({
      'content-type': 'application/json',
      authorization: TOKEN,
      'user-agent': 'row-bot-railway-relay',
    });

    expect(response.status).toBe(200);
    expect(await response.text()).toBe('{"data":{"me":{"email":"sam@example.com"}}}');
    expect(response.headers.get('set-cookie')).toBeNull();
    expect(response.headers.get('x-railway-request-id')).toBeNull();
    expect(response.headers.get('cache-control')).toBe('no-store');
    expect(response.headers.get('access-control-allow-origin')).toBe(PAGE);
  });

  it("returns Railway's own status and errors", async () => {
    upstream.mockImplementation(async () =>
      new Response('{"errors":[{"message":"Not Authorized"}]}', { status: 400, headers: { 'content-type': 'application/json' } }));
    const response = await call();
    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ errors: [{ message: 'Not Authorized' }] });
  });

  it("says so when Railway can't be reached", async () => {
    upstream.mockImplementation(async () => {
      throw new TypeError('network down');
    });
    const response = await call();
    expect(response.status).toBe(502);
    expect(response.headers.get('access-control-allow-origin')).toBe(PAGE);
  });
});
