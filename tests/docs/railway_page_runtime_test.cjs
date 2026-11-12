// The "Continue with Railway" page's flow (docs/railway/flow.js), run in Node against a fake Railway:
// a fake token endpoint and a fake relay answer every call, and any other address fails the test. Every
// GraphQL call the page makes is also checked against the relay's own allow-list.
const assert = require('node:assert/strict');
const path = require('node:path');
const { webcrypto } = require('node:crypto');
const { pathToFileURL } = require('node:url');

global.window = {};
require(path.resolve(__dirname, '../../docs/railway/flow.js'));
const api = window.RowBotRailway;

const PAGE = 'http://127.0.0.1:8153/railway/';
const RELAY = 'http://127.0.0.1:8153';
const TOKEN = 'test-only-railway-token-7f3a91';
const DOMAIN = 'row-bot-production-ab12.up.railway.app';
const CODE_PATTERN = /^[0-9ABCDEFGHJKMNPQRSTVWXYZ]{4}(-[0-9ABCDEFGHJKMNPQRSTVWXYZ]{4}){3}$/;
const REGIONS = [
    { name: 'us-west2', location: 'US West (California, USA)' },
    { name: 'us-east4-eqdc4a', location: 'US East (Virginia, USA)' },
    { name: 'europe-west4-drams3a', location: 'EU West (Amsterdam, Netherlands)' },
    { name: 'asia-southeast1-eqsg3a', location: 'Southeast Asia (Singapore)' },
];

const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const data = value => json({ data: value });
const refused = message => json({ data: null, errors: [{ message }] });

function workspace(overrides = {}) {
    return {
        id: 'w1',
        name: "Sam's Projects",
        plan: 'HOBBY',
        preferredRegion: 'sfo',
        customer: { isTrialing: false },
        projects: { edges: [] },
        ...overrides,
    };
}

/** A fake Railway: each GraphQL field answers from `answers`, which a test can override per field. */
function fakeRailway(overrides = {}) {
    const statuses = ['QUEUED', 'DEPLOYING', 'SUCCESS'];
    const answers = {
        token: () => json({ access_token: TOKEN, token_type: 'Bearer', expires_in: 3600, scope: api.SCOPES }),
        me: () => data({ me: { email: 'sam@example.com', isVerified: true, workspaces: [workspace()] } }),
        regions: () => data({ regions: REGIONS }),
        project: () => data({ project: {
            id: 'p0',
            environments: { edges: [{ node: { id: 'e0', name: 'production' } }] },
            services: { edges: [{ node: { id: 's0', name: 'row-bot' } }] },
        } }),
        domains: () => data({ domains: { serviceDomains: [{ domain: 'row-bot-production-zz99.up.railway.app' }] } }),
        projectCreate: () => data({ projectCreate: { id: 'p1', environments: { edges: [{ node: { id: 'e1', name: 'production' } }] } } }),
        serviceCreate: () => data({ serviceCreate: { id: 's1' } }),
        volumeCreate: () => data({ volumeCreate: { id: 'v1' } }),
        serviceInstanceUpdate: () => data({ serviceInstanceUpdate: true }),
        serviceInstanceLimitsUpdate: () => data({ serviceInstanceLimitsUpdate: true }),
        serviceDomainCreate: () => data({ serviceDomainCreate: { domain: DOMAIN } }),
        environmentPatchCommit: () => data({ environmentPatchCommit: 'workflow-1' }),
        serviceInstanceDeployV2: () => data({ serviceInstanceDeployV2: 'd1' }),
        deployment: () => data({ deployment: { status: statuses.length > 1 ? statuses.shift() : statuses[0] } }),
        ...overrides,
    };
    return answers;
}

const consoleCalls = [];
for (const method of ['log', 'info', 'warn', 'error', 'debug', 'trace']) {
    console[method] = (...args) => consoleCalls.push([method, ...args]);
}

function harness({ search = '', saved = null, timeZone = 'Europe/London', railway = {}, relay } = {}) {
    const answers = fakeRailway(railway);
    const calls = [];
    const stored = new Map();
    const written = [];
    const storage = {
        getItem: key => (stored.has(key) ? stored.get(key) : null),
        setItem: (key, value) => {
            written.push([key, String(value)]);
            stored.set(key, String(value));
        },
        removeItem: key => stored.delete(key),
    };
    if (saved) storage.setItem('row-bot-railway-sign-in', JSON.stringify(saved));
    const assigned = [];
    const replaced = [];
    const states = [];
    const times = [];
    const waits = [];
    let clock = 1_000_000;
    const fetch = async (url, init) => {
        if (url === api.TOKEN_URL) {
            calls.push({ field: 'token', url, init, params: Object.fromEntries(new URLSearchParams(init.body)) });
            return answers.token(init);
        }
        if (url === `${RELAY}/graphql`) {
            const body = JSON.parse(init.body);
            assert.equal(relay.isAllowed(body.query), true, `the relay would refuse: ${body.query}`);
            const [field] = relay.operationOf(body.query).fields;
            calls.push({ field, url, init, query: body.query, variables: body.variables });
            return answers[field](body.variables, calls);
        }
        throw new Error(`the page called ${url}`);
    };
    const flow = api.createFlow({
        config: api.readConfig(JSON.stringify({ redirectUri: PAGE, relay: RELAY }), `${PAGE}${search}`),
        fetch,
        storage,
        location: { search, assign: url => assigned.push(url) },
        history: { replaceState: (_state, _title, url) => replaced.push(url) },
        crypto: webcrypto,
        timeZone,
        now: () => clock,
        wait: async ms => {
            waits.push(ms);
            clock += ms;
        },
        onChange: state => {
            states.push(JSON.parse(JSON.stringify(state)));
            times.push(clock);
        },
    });
    const fields = () => calls.map(call => call.field);
    const call = field => calls.find(entry => entry.field === field);
    return { flow, calls, fields, call, stored, written, assigned, replaced, states, times, waits, answers };
}

/** The page back from Railway's consent screen with a code, and the sign-in it saved before leaving. */
function returning(options = {}) {
    const saved = { verifier: 'v'.repeat(64), state: 'state-123' };
    return harness({ search: '?code=auth-code-1&state=state-123', saved, ...options });
}

/** Nothing the page keeps or shows may hold the token. */
function assertTokenNowhere(h) {
    for (const [, value] of h.written) assert.ok(!value.includes(TOKEN), 'the token reached sessionStorage');
    for (const url of [...h.assigned, ...h.replaced]) assert.ok(!url.includes(TOKEN), 'the token reached an address');
    for (const state of h.states) assert.ok(!JSON.stringify(state).includes(TOKEN), 'the token reached the page state');
    assert.ok(!JSON.stringify(h.flow.state).includes(TOKEN));
    assert.deepEqual(consoleCalls, [], 'the page wrote to the console');
}

const SETUP_ORDER = [
    'projectCreate', 'serviceCreate', 'volumeCreate', 'serviceInstanceUpdate', 'serviceInstanceLimitsUpdate',
    'serviceDomainCreate', 'environmentPatchCommit', 'serviceInstanceDeployV2',
];

async function main() {
    const operations = await import(pathToFileURL(path.resolve(__dirname, '../../services/railway-relay/src/operations.js')).href);
    const make = (options = {}) => harness({ relay: operations, ...options });
    const back = (options = {}) => returning({ relay: operations, ...options });

    // ---- The page's own settings ------------------------------------------------------------------
    const production = JSON.stringify({ redirectUri: 'https://row-bot.ai/railway/', relay: 'https://row-bot-railway.row-bot.workers.dev' });
    assert.deepEqual(api.readConfig(production, 'https://row-bot.ai/railway/?code=x'),
        { redirectUri: 'https://row-bot.ai/railway/', relay: 'https://row-bot-railway.row-bot.workers.dev' });
    const loopback = JSON.stringify({ redirectUri: PAGE, relay: RELAY });
    assert.deepEqual(api.readConfig(loopback, PAGE), { redirectUri: PAGE, relay: RELAY });
    assert.equal(api.readConfig(loopback, 'https://row-bot.ai/railway/'), null, 'a loopback relay on the published page');
    assert.equal(api.readConfig(JSON.stringify({ redirectUri: 'https://row-bot.ai/railway/', relay: 'http://127.0.0.1:8153' }),
        'https://row-bot.ai/railway/'), null);
    assert.equal(api.readConfig(JSON.stringify({ redirectUri: 'https://evil.example/railway/', relay: 'https://r.example' }),
        'https://row-bot.ai/railway/'), null, 'a redirect to another site');
    assert.equal(api.readConfig(JSON.stringify({ redirectUri: 'https://row-bot.ai/railway/', relay: 'https://r.example/x' }),
        'https://row-bot.ai/railway/'), null, 'a relay with a path');
    assert.equal(api.readConfig('not json', 'https://row-bot.ai/railway/'), null);
    assert.equal(api.readConfig('{}', 'https://row-bot.ai/railway/'), null);
    const unconfigured = api.createFlow({ config: null, onChange() {} });
    assert.equal(unconfigured.state.notice, 'config');

    // ---- Regions from the time zone ---------------------------------------------------------------
    const zones = {
        'Europe/London': 'eu-west', 'Africa/Lagos': 'eu-west', 'Asia/Dubai': 'eu-west', 'Atlantic/Reykjavik': 'eu-west',
        'America/New_York': 'us-east', 'America/Sao_Paulo': 'us-east', 'America/Toronto': 'us-east',
        'America/Los_Angeles': 'us-west', 'America/Denver': 'us-west', 'Pacific/Honolulu': 'us-west',
        'Asia/Singapore': 'asia-southeast', 'Asia/Kolkata': 'asia-southeast', 'Australia/Sydney': 'asia-southeast',
        'Asia/Tokyo': 'asia-southeast', 'Pacific/Auckland': 'asia-southeast', UTC: null, 'Etc/GMT+3': null, '': null,
    };
    for (const [zone, area] of Object.entries(zones)) assert.equal(api.guessArea(zone), area, zone);

    const choices = api.regionChoices(REGIONS);
    assert.deepEqual(choices.map(choice => [choice.area, choice.value, choice.label]), [
        ['eu-west', 'europe-west4-drams3a', 'EU West (Amsterdam, Netherlands)'],
        ['us-east', 'us-east4-eqdc4a', 'US East (Virginia, USA)'],
        ['us-west', 'us-west2', 'US West (California, USA)'],
        ['asia-southeast', 'asia-southeast1-eqsg3a', 'Southeast Asia (Singapore)'],
    ]);
    const byName = api.regionChoices([{ name: 'europe-west4-drams3a', location: 'Amsterdam, Netherlands' }, { name: 'us-east4-eqdc4a' }]);
    assert.deepEqual(byName.map(choice => [choice.value, choice.label]),
        [['europe-west4-drams3a', 'EU West (Amsterdam, Netherlands)'], ['us-east4-eqdc4a', 'US East']]);
    assert.deepEqual(api.pickRegion(choices, 'us-west', 'sfo').value, 'us-west2');
    const fallback = api.pickRegion(byName, 'asia-southeast', 'sfo');
    assert.equal(fallback.value, 'sfo', "the workspace's preferred region when the guess isn't offered");
    assert.equal(fallback.guessed, false);
    assert.equal(fallback.options.at(-1).label, "Your workspace's usual region");
    assert.equal(api.pickRegion([], null, null).value, null);
    assert.equal(api.pickRegion(choices, null, null).value, 'europe-west4-drams3a');

    // ---- The setup code -----------------------------------------------------------------------------
    let drawn = 0;
    const counted = { getRandomValues: array => { drawn += 1; return webcrypto.getRandomValues(array); } };
    const codes = new Set(Array.from({ length: 200 }, () => api.setupCode(counted)));
    assert.equal(drawn, 200);
    assert.equal(codes.size, 200);
    for (const code of codes) assert.match(code, CODE_PATTERN);

    // ---- Continue with Railway: PKCE, and the verifier kept only in sessionStorage ----------------
    {
        const h = make();
        await h.flow.start();
        assert.equal(h.flow.state.view, 'start');
        assert.equal(h.flow.state.notice, null);
        await h.flow.signIn();
        assert.equal(h.calls.length, 0);
        assert.equal(h.assigned.length, 1);
        const url = new URL(h.assigned[0]);
        assert.equal(`${url.origin}${url.pathname}`, api.AUTHORIZE_URL);
        const saved = JSON.parse(h.stored.get('row-bot-railway-sign-in'));
        assert.match(saved.verifier, /^[A-Za-z0-9_-]{64}$/);
        const challenge = Buffer.from(await webcrypto.subtle.digest('SHA-256', Buffer.from(saved.verifier))).toString('base64url');
        assert.deepEqual(Object.fromEntries(url.searchParams), {
            response_type: 'code',
            client_id: 'rlwy_oaci_OiB2UrfRkptVSmi4ItzVL0DV',
            redirect_uri: PAGE,
            scope: 'openid email profile workspace:member',
            state: saved.state,
            code_challenge: challenge,
            code_challenge_method: 'S256',
            prompt: 'consent',
        });
        assert.equal(h.flow.state.busy, 'sign-in');
        h.flow.resetAfterReturn();
        assert.equal(h.flow.state.busy, null, 'Back from Railway leaves the button usable');
    }

    // ---- The happy path ------------------------------------------------------------------------------
    {
        const h = back();
        await h.flow.start();
        assert.deepEqual(h.replaced, ['/railway/'], 'the code leaves the address before anything else');
        assert.equal(h.stored.size, 0, 'the verifier and state are gone after the exchange');
        assert.deepEqual(h.fields(), ['token', 'me', 'regions']);
        assert.deepEqual(h.call('token').params, {
            grant_type: 'authorization_code',
            code: 'auth-code-1',
            redirect_uri: PAGE,
            client_id: 'rlwy_oaci_OiB2UrfRkptVSmi4ItzVL0DV',
            code_verifier: 'v'.repeat(64),
        });
        assert.equal(h.call('token').init.credentials, 'omit');
        for (const call of h.calls.slice(1)) {
            assert.equal(call.init.headers.Authorization, `Bearer ${TOKEN}`);
            assert.equal(call.init.credentials, 'omit');
            assert.equal(call.init.referrerPolicy, 'no-referrer');
        }
        const account = h.flow.state.account;
        assert.equal(h.flow.state.view, 'account');
        assert.equal(account.email, 'sam@example.com');
        assert.equal(account.verified, true);
        assert.equal(account.region, 'europe-west4-drams3a', 'EU West, from Europe/London');
        assert.equal(account.regionGuessed, true);
        assert.equal(account.plan, null);
        assert.equal(account.existing, null);
        assert.ok(h.states.some(state => state.view === 'signing-in'));

        h.flow.selectRegion('us-east4-eqdc4a');
        h.flow.selectRegion('not-a-region');
        assert.equal(h.flow.state.account.region, 'us-east4-eqdc4a');
        h.flow.selectRegion('europe-west4-drams3a');

        await h.flow.create();
        const after = h.fields().slice(3);
        assert.deepEqual(after.slice(0, SETUP_ORDER.length), SETUP_ORDER, 'the calls, in order');
        assert.deepEqual([...new Set(after.slice(SETUP_ORDER.length))], ['deployment']);
        assert.equal(after.length, SETUP_ORDER.length + 3, 'polled until SUCCESS');

        assert.deepEqual(h.call('projectCreate').variables, { input: { name: 'Row-Bot', workspaceId: 'w1' } });
        const service = h.call('serviceCreate').variables.input;
        assert.deepEqual({ ...service, variables: undefined }, {
            projectId: 'p1', environmentId: 'e1', name: 'row-bot', source: { image: 'ghcr.io/siddsachar/row-bot:latest' }, variables: undefined,
        });
        const code = service.variables.ROW_BOT_SETUP_CODE;
        assert.match(code, CODE_PATTERN);
        assert.deepEqual(service.variables, {
            ROW_BOT_SETUP_CODE: code,
            ROW_BOT_PUBLIC_URL: 'https://${{RAILWAY_PUBLIC_DOMAIN}}',
            ROW_BOT_HOSTED_BY: 'railway',
            RAILWAY_RUN_UID: '0',
            PORT: '8080',
            ROW_BOT_TRUSTED_PROXY_CIDRS: '100.64.0.0/10',
        });
        assert.deepEqual(h.call('volumeCreate').variables, { input: { projectId: 'p1', environmentId: 'e1', serviceId: 's1', mountPath: '/data' } });
        assert.deepEqual(h.call('serviceInstanceUpdate').variables, { s: 's1', e: 'e1', input: { region: 'europe-west4-drams3a' } },
            'the region, and no health check');
        assert.deepEqual(h.call('serviceInstanceLimitsUpdate').variables, { input: { serviceId: 's1', environmentId: 'e1', memoryGB: 8, vCPUs: 8 } });
        assert.deepEqual(h.call('serviceDomainCreate').variables, { input: { serviceId: 's1', environmentId: 'e1', targetPort: 8080 } });
        assert.deepEqual(h.call('environmentPatchCommit').variables, {
            e: 'e1',
            patch: { services: { s1: { source: { autoUpdates: {
                type: 'patch',
                schedule: [0, 1, 2, 3, 4, 5, 6].map(day => ({ day, startHour: 2, endHour: 6 })),
            } } } } },
            skip: true,
        });
        assert.deepEqual(h.call('serviceInstanceDeployV2').variables, { s: 's1', e: 'e1' });
        assert.deepEqual(h.calls.filter(call => call.field === 'deployment').map(call => call.variables), [{ id: 'd1' }, { id: 'd1' }, { id: 'd1' }]);
        assert.deepEqual(h.waits, [3000, 3000, 3000, 3000], 'polled every 3 s, then a moment before the hand-over');

        assert.deepEqual(h.assigned, [`https://${DOMAIN}/setup#code=${code}`]);
        assert.equal(h.flow.state.progress.step, 'signing-in');
        const steps = h.states.filter(state => state.view === 'progress').map(state => state.progress.step);
        assert.deepEqual([...new Set(steps)], ['project', 'starting', 'signing-in']);
        assertTokenNowhere(h);
        const before = h.calls.length;
        await h.flow.recheck();
        await h.flow.create();
        await h.flow.retry();
        assert.equal(h.calls.length, before, 'nothing more is sent after the hand-over');
    }

    // ---- Signed-in states -----------------------------------------------------------------------------
    {
        // Cancelled at Railway: back to the start, with no error.
        const h = make({ search: '?error=access_denied&error_description=The+user+denied&state=state-123', saved: { verifier: 'v'.repeat(64), state: 'state-123' } });
        await h.flow.start();
        assert.equal(h.flow.state.view, 'start');
        assert.equal(h.flow.state.notice, null);
        assert.deepEqual(h.replaced, ['/railway/']);
        assert.equal(h.stored.size, 0);
        assert.equal(h.calls.length, 0);
    }
    for (const search of [
        '?error=invalid_request&error_description=Interaction+not+found+or+expired&state=state-123',
        '?code=auth-code-1&state=someone-else',
        '?code=&state=state-123',
    ]) {
        // A consent that expired, or a return this tab didn't start: start again.
        const h = make({ search, saved: { verifier: 'v'.repeat(64), state: 'state-123' } });
        await h.flow.start();
        assert.equal(h.flow.state.view, 'start', search);
        assert.equal(h.flow.state.notice, 'expired', search);
        assert.equal(h.stored.size, 0);
        assert.equal(h.calls.length, 0);
    }
    {
        const h = make({ search: '?code=auth-code-1&state=state-123' });
        await h.flow.start();
        assert.equal(h.flow.state.notice, 'expired', 'no saved sign-in in this tab');
    }
    {
        const h = back({ railway: { token: () => json({ error: 'invalid_grant' }, 400) } });
        await h.flow.start();
        assert.equal(h.flow.state.view, 'start');
        assert.equal(h.flow.state.notice, 'expired');
        assert.deepEqual(h.fields(), ['token']);
        assert.equal(h.stored.size, 0);
    }
    {
        const h = back({ railway: { me: () => { throw new TypeError('relay down'); } } });
        await h.flow.start();
        assert.equal(h.flow.state.view, 'start');
        assert.equal(h.flow.state.notice, 'unreachable');
    }
    {
        const h = back({ railway: { me: () => data({ me: { email: 'sam@example.com', isVerified: true, workspaces: [] } }) } });
        await h.flow.start();
        assert.equal(h.flow.state.notice, 'no-workspace');
    }
    {
        // A plain visit clears a sign-in left over from an earlier try.
        const h = make({ saved: { verifier: 'v'.repeat(64), state: 'old' } });
        await h.flow.start();
        assert.equal(h.stored.size, 0);
        assert.equal(h.flow.state.view, 'start');
    }

    {
        // Not verified: the button waits; reading the account again lets it go.
        let verified = false;
        const h = back({ railway: { me: () => data({ me: { email: 'new@example.com', isVerified: verified, workspaces: [workspace()] } }) } });
        await h.flow.start();
        assert.equal(h.flow.state.account.verified, false);
        assert.equal(h.flow.state.account.verifyUrl, 'https://railway.com/dashboard');
        await h.flow.create();
        assert.equal(h.flow.state.view, 'account');
        assert.ok(!h.fields().includes('projectCreate'), 'nothing is created while Railway waits for verification');
        await h.flow.recheck();
        assert.equal(h.flow.state.account.verified, false);
        verified = true;
        await h.flow.recheck();
        assert.equal(h.flow.state.account.verified, true);
        await h.flow.create();
        assert.ok(h.assigned[0].startsWith(`https://${DOMAIN}/setup#code=`));
    }
    {
        // A sign-in that ran out while the page was open.
        const h = back();
        await h.flow.start();
        h.answers.me = () => json({ errors: [{ message: 'Unauthorized' }] }, 401);
        await h.flow.recheck();
        assert.equal(h.flow.state.view, 'start');
        assert.equal(h.flow.state.notice, 'signed-out');
    }

    {
        // Trial: the plan note shows, and a limit above the trial's doesn't stop the create.
        const h = back({
            railway: {
                me: () => data({ me: { email: 'sam@example.com', isVerified: true, workspaces: [workspace({ plan: 'FREE', customer: { isTrialing: true } })] } }),
                serviceInstanceLimitsUpdate: () => refused('Memory limit exceeds your plan'),
            },
        });
        await h.flow.start();
        assert.equal(h.flow.state.account.plan, 'trial');
        assert.equal(h.flow.state.account.plansUrl, 'https://railway.com/workspace/plans');
        await h.flow.create();
        assert.equal(h.assigned.length, 1);
        const free = back({ railway: { me: () => data({ me: { email: 'a@example.com', isVerified: true, workspaces: [workspace({ plan: 'FREE' })] } }) } });
        await free.flow.start();
        assert.equal(free.flow.state.account.plan, 'free');
    }

    {
        // No permission in a team workspace: nothing is made; pick another workspace.
        const h = back({
            railway: {
                me: () => data({ me: { email: 'sam@example.com', isVerified: true, workspaces: [workspace({ id: 'team', name: 'Team' }), workspace({ id: 'w2', name: 'Mine' })] } }),
                projectCreate: variables => (variables.input.workspaceId === 'team' ? refused('Not Authorized')
                    : data({ projectCreate: { id: 'p2', environments: { edges: [{ node: { id: 'e2', name: 'production' } }] } } })),
            },
        });
        await h.flow.start();
        assert.equal(h.flow.state.account.workspaceId, 'team');
        await h.flow.create();
        assert.equal(h.flow.state.view, 'account');
        assert.equal(h.flow.state.account.blocked, 'not-authorized');
        assert.ok(!h.fields().includes('serviceCreate'));
        h.flow.chooseAgain();
        assert.equal(h.flow.state.account.blocked, null);
        await h.flow.selectWorkspace('w2');
        assert.equal(h.flow.state.account.workspaceId, 'w2');
        await h.flow.create();
        assert.deepEqual(h.calls.filter(call => call.field === 'projectCreate').map(call => call.variables.input.workspaceId), ['team', 'w2']);
        assert.equal(h.assigned.length, 1);
    }
    {
        // With one workspace, "Choose again" goes back to Railway's consent screen to pick another.
        const h = back({ railway: { projectCreate: () => refused('Not Authorized') } });
        await h.flow.start();
        await h.flow.create();
        assert.equal(h.flow.state.account.blocked, 'not-authorized');
        await h.flow.chooseAgain();
        assert.equal(h.assigned.length, 1);
        assert.ok(h.assigned[0].startsWith(api.AUTHORIZE_URL));
    }
    {
        // "Not Authorized" because the sign-in ran out, not the workspace: start again.
        const h = back();
        await h.flow.start();
        h.answers.projectCreate = () => refused('Not Authorized');
        h.answers.me = () => refused('Not Authorized');
        await h.flow.create();
        assert.equal(h.flow.state.view, 'start');
        assert.equal(h.flow.state.notice, 'signed-out');
    }

    {
        // Already has a Row-Bot: open it at its own address, or create another.
        const projects = { edges: [
            { node: { id: 'gone', name: 'Row-Bot', deletedAt: '2026-11-07T00:00:00Z' } },
            { node: { id: 'other', name: 'blog', deletedAt: null } },
            { node: { id: 'p0', name: 'Row-Bot', deletedAt: null } },
        ] };
        const h = back({ railway: { me: () => data({ me: { email: 'sam@example.com', isVerified: true, workspaces: [workspace({ projects })] } }) } });
        await h.flow.start();
        assert.deepEqual(h.flow.state.account.existing, { name: 'Row-Bot', url: 'https://row-bot-production-zz99.up.railway.app/', inRailway: false });
        assert.deepEqual(h.call('project').variables, { id: 'p0' });
        assert.deepEqual(h.call('domains').variables, { p: 'p0', e: 'e0', s: 's0' });
        await h.flow.createAnother();
        assert.deepEqual(h.call('projectCreate').variables.input, { name: 'Row-Bot 2', workspaceId: 'w1' });
        assert.equal(h.assigned.length, 1);

        const unknown = back({ railway: {
            me: () => data({ me: { email: 'sam@example.com', isVerified: true, workspaces: [workspace({ projects })] } }),
            project: () => refused('Problem processing request'),
        } });
        await unknown.flow.start();
        assert.deepEqual(unknown.flow.state.account.existing, { name: 'Row-Bot', url: 'https://railway.com/project/p0', inRailway: true });
    }

    // ---- Create: failures, slow, and Try again -----------------------------------------------------
    {
        // The deploy fails: Railway's log link, and Try again deploys the same service again.
        let deploys = 0;
        const h = back({ railway: {
            deployment: variables => data({ deployment: { status: variables.id === 'd1' ? 'FAILED' : 'SUCCESS' } }),
            serviceInstanceDeployV2: () => data({ serviceInstanceDeployV2: `d${++deploys}` }),
        } });
        await h.flow.start();
        await h.flow.create();
        const progress = h.flow.state.progress;
        assert.equal(progress.status, 'failed');
        assert.equal(progress.reason, 'deploy');
        assert.equal(progress.step, 'starting');
        assert.equal(progress.logUrl, 'https://railway.com/project/p1/service/s1?environmentId=e1');
        assert.equal(h.assigned.length, 0);
        const before = h.fields().length;
        await h.flow.retry();
        assert.deepEqual(h.fields().slice(before), ['serviceInstanceDeployV2', 'deployment']);
        assert.equal(h.assigned.length, 1);
        assertTokenNowhere(h);
    }
    {
        // A set-up call Railway refuses: its words, and Try again picks up where it stopped.
        let volumeTries = 0;
        const h = back({ railway: {
            volumeCreate: () => (++volumeTries === 1 ? refused('Volume limit reached') : data({ volumeCreate: { id: 'v1' } })),
        } });
        await h.flow.start();
        await h.flow.create();
        assert.equal(h.flow.state.progress.status, 'failed');
        assert.equal(h.flow.state.progress.reason, 'railway');
        assert.equal(h.flow.state.progress.detail, 'Volume limit reached');
        const before = h.fields().length;
        await h.flow.retry();
        assert.deepEqual(h.fields().slice(before, before + 6), SETUP_ORDER.slice(2));
        assert.equal(h.calls.filter(call => call.field === 'projectCreate').length, 1, 'no second project');
        assert.equal(h.calls.filter(call => call.field === 'serviceCreate').length, 1, 'no second service');
        assert.equal(h.assigned.length, 1);
    }
    {
        // Railway unreachable in the middle: say so, and Try again.
        let down = true;
        const h = back({ railway: {
            serviceDomainCreate: () => {
                if (down) throw new TypeError('offline');
                return data({ serviceDomainCreate: { domain: DOMAIN } });
            },
        } });
        await h.flow.start();
        await h.flow.create();
        assert.equal(h.flow.state.progress.reason, 'unreachable');
        down = false;
        await h.flow.retry();
        assert.equal(h.assigned.length, 1);
    }
    {
        // The relay's rate limit: say so, and Try again carries on; a limited poll just polls again.
        let limited = true;
        let polls = 0;
        const tooMany = () => json({ errors: [{ message: 'Too many requests. Wait a minute, then try again.' }] }, 429);
        const h = back({ railway: {
            serviceDomainCreate: () => (limited ? tooMany() : data({ serviceDomainCreate: { domain: DOMAIN } })),
            deployment: () => (++polls === 1 ? tooMany() : data({ deployment: { status: 'SUCCESS' } })),
        } });
        await h.flow.start();
        await h.flow.create();
        assert.equal(h.flow.state.progress.status, 'failed');
        assert.equal(h.flow.state.progress.reason, 'busy');
        limited = false;
        await h.flow.retry();
        assert.equal(polls, 2);
        assert.equal(h.assigned.length, 1);

        const atStart = back({ railway: { me: tooMany } });
        await atStart.flow.start();
        assert.equal(atStart.flow.state.view, 'start');
        assert.equal(atStart.flow.state.notice, 'busy');
    }
    {
        // Slow: after 4 minutes, where the Row-Bot is and where its code is; it still signs in when ready.
        let polls = 0;
        const h = back({ railway: { deployment: () => data({ deployment: { status: ++polls < 90 ? 'DEPLOYING' : 'SUCCESS' } }) } });
        await h.flow.start();
        await h.flow.create();
        const created = h.times[h.states.findIndex(state => state.view === 'progress')];
        const slowAt = h.states.findIndex(state => state.progress && state.progress.status === 'slow');
        assert.ok(slowAt > 0, 'the slow note showed');
        const slow = h.states[slowAt];
        const waited = h.times[slowAt] - created;
        assert.ok(waited >= 4 * 60 * 1000 && waited < 4 * 60 * 1000 + 3000, `slow after ${waited} ms`);
        assert.equal(slow.progress.address, `https://${DOMAIN}`);
        assert.equal(slow.progress.variablesUrl, 'https://railway.com/project/p1/service/s1/variables?environmentId=e1');
        assert.equal(h.flow.state.progress.step, 'signing-in');
        assert.equal(h.assigned.length, 1, 'still signed in once Railway was ready');
    }
    {
        // A region name Railway refuses: the workspace's preferred region instead.
        const h = back({ railway: { serviceInstanceUpdate: variables => (variables.input.region === 'sfo'
            ? data({ serviceInstanceUpdate: true }) : refused('Invalid region')) } });
        await h.flow.start();
        await h.flow.create();
        assert.deepEqual(h.calls.filter(call => call.field === 'serviceInstanceUpdate').map(call => call.variables.input), [
            { region: 'europe-west4-drams3a' }, { region: 'sfo' },
        ]);
        assert.equal(h.assigned.length, 1);
    }
    {
        // Another time zone, another region; none known, Railway's own default.
        const h = back({ timeZone: 'America/Los_Angeles' });
        await h.flow.start();
        assert.equal(h.flow.state.account.region, 'us-west2');
        const none = back({ timeZone: 'UTC', railway: {
            regions: () => refused('Problem processing request'),
            me: () => data({ me: { email: 'a@example.com', isVerified: true, workspaces: [workspace({ preferredRegion: null })] } }),
        } });
        await none.flow.start();
        assert.equal(none.flow.state.account.region, null);
        assert.deepEqual(none.flow.state.account.regions, []);
        await none.flow.create();
        assert.ok(!none.fields().includes('serviceInstanceUpdate'));
        assert.equal(none.assigned.length, 1);
    }

    assert.deepEqual(consoleCalls, [], 'the page wrote to the console');
}

main().catch(error => {
    process.stderr.write(`${error && error.stack ? error.stack : error}\n`);
    process.exit(1);
});
