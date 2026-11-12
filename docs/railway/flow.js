/*
 * "Continue with Railway": the page's logic, with no DOM (railway.js draws it).
 *
 * 1. Sign-in: Railway's OAuth with PKCE (S256) as a public client. The verifier and state wait in
 *    sessionStorage only while Railway's pages are open, and are removed before the code is exchanged.
 *    The token is kept in this closure only: never in storage, a cookie, the address or the console.
 * 2. The account: who is signed in, their workspaces, Railway's regions, and any Row-Bot already there.
 * 3. Create: a project "Row-Bot" with Row-Bot's image, its variables (a setup code made here), a /data
 *    volume, the region, 8 GB / 8 vCPU, an address, nightly image updates, then the deploy.
 * 4. Hand-over: when Railway says the deploy succeeded, the token is dropped and the browser opens
 *    https://<address>/setup#code=<setup code>, which signs it in to the new Row-Bot.
 *
 * Railway's token endpoint accepts browsers, so the exchange goes straight to it. Its GraphQL API
 * doesn't, so every API call goes through the relay (services/railway-relay), which passes on only
 * these operations and keeps nothing.
 */
(() => {
    'use strict';

    const CLIENT_ID = 'rlwy_oaci_OiB2UrfRkptVSmi4ItzVL0DV'; // a public client: there is no secret
    const AUTHORIZE_URL = 'https://backboard.railway.com/oauth/auth';
    const TOKEN_URL = 'https://backboard.railway.com/oauth/token';
    const SCOPES = 'openid email profile workspace:member';
    const IMAGE = 'ghcr.io/siddsachar/row-bot:latest';
    const PORT = 8080;
    const SIGN_IN_KEY = 'row-bot-railway-sign-in';
    // Crockford's base32, as Row-Bot's own setup codes: no I, L, O or U.
    const CODE_ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ';
    const POLL_MS = 3000;
    const SLOW_MS = 4 * 60 * 1000;
    const GIVE_UP_MS = 20 * 60 * 1000;
    // Without a health check Railway reports success as the container starts; Row-Bot listens about
    // 2 s later (C0), so the hand-over waits a moment rather than open an address that isn't answering.
    const SETTLE_MS = 3000;
    const FAILED = new Set(['FAILED', 'CRASHED', 'REMOVED']);
    const PROJECT_NAME = /^row-bot(?: (\d+))?$/i;

    const RAILWAY = 'https://railway.com';
    const PLANS_URL = `${RAILWAY}/workspace/plans`;
    // Railway shows an unverified account how to verify on its dashboard.
    const VERIFY_URL = `${RAILWAY}/dashboard`;

    const QUERIES = Object.freeze({
        me: 'query { me { email isVerified workspaces { id name plan preferredRegion customer { isTrialing } projects { edges { node { id name deletedAt } } } } } }',
        regions: 'query { regions { name location } }',
        project: 'query($id: String!) { project(id: $id) { id environments { edges { node { id name } } } services { edges { node { id name } } } } }',
        domains: 'query($p: String!, $e: String!, $s: String!) { domains(projectId: $p, environmentId: $e, serviceId: $s) { serviceDomains { domain } } }',
        projectCreate: 'mutation($input: ProjectCreateInput!) { projectCreate(input: $input) { id environments { edges { node { id name } } } } }',
        serviceCreate: 'mutation($input: ServiceCreateInput!) { serviceCreate(input: $input) { id } }',
        volumeCreate: 'mutation($input: VolumeCreateInput!) { volumeCreate(input: $input) { id } }',
        serviceInstanceUpdate: 'mutation($s: String!, $e: String, $input: ServiceInstanceUpdateInput!) { serviceInstanceUpdate(serviceId: $s, environmentId: $e, input: $input) }',
        serviceInstanceLimitsUpdate: 'mutation($input: ServiceInstanceLimitsUpdateInput!) { serviceInstanceLimitsUpdate(input: $input) }',
        serviceDomainCreate: 'mutation($input: ServiceDomainCreateInput!) { serviceDomainCreate(input: $input) { domain } }',
        environmentPatchCommit: 'mutation($e: String!, $patch: EnvironmentConfig, $skip: Boolean) { environmentPatchCommit(environmentId: $e, patch: $patch, skipDeploys: $skip) }',
        serviceInstanceDeployV2: 'mutation($s: String!, $e: String!) { serviceInstanceDeployV2(serviceId: $s, environmentId: $e) }',
        deployment: 'query($id: String!) { deployment(id: $id) { status } }',
    });

    // Railway's four regions people are offered, in the order shown. A region from Railway's `regions`
    // list belongs to an area by its location ("EU West (…)"), or else by its name's prefix.
    const AREAS = Object.freeze([
        { id: 'eu-west', label: 'EU West', prefix: 'europe-west' },
        { id: 'us-east', label: 'US East', prefix: 'us-east' },
        { id: 'us-west', label: 'US West', prefix: 'us-west' },
        { id: 'asia-southeast', label: 'Southeast Asia', prefix: 'asia-southeast' },
    ]);
    const WEST_AMERICA = new Set([
        'America/Los_Angeles', 'America/Vancouver', 'America/Tijuana', 'America/Phoenix', 'America/Denver',
        'America/Boise', 'America/Edmonton', 'America/Anchorage', 'America/Juneau', 'America/Sitka', 'America/Nome',
        'America/Yakutat', 'America/Metlakatla', 'America/Whitehorse', 'America/Dawson', 'America/Dawson_Creek',
        'America/Fort_Nelson', 'America/Creston', 'America/Hermosillo', 'America/Mazatlan', 'America/Chihuahua',
        'America/Ciudad_Juarez', 'America/Ojinaga', 'America/Cambridge_Bay', 'America/Inuvik', 'America/Yellowknife',
        'Pacific/Honolulu', 'US/Pacific', 'US/Mountain', 'US/Arizona', 'US/Alaska', 'US/Hawaii',
        'Canada/Pacific', 'Canada/Mountain',
    ]);
    const MIDDLE_EAST = /^Asia\/(Dubai|Muscat|Qatar|Bahrain|Kuwait|Riyadh|Aden|Baghdad|Tehran|Jerusalem|Tel_Aviv|Beirut|Damascus|Amman|Gaza|Hebron|Nicosia|Famagusta|Tbilisi|Yerevan|Baku)$/;

    /** The area nearest a time zone (an IANA name), or null when it can't be told. */
    function guessArea(timeZone) {
        const zone = String(timeZone || '');
        if (WEST_AMERICA.has(zone)) return 'us-west';
        if (zone === 'Atlantic/Bermuda' || /^(America|US|Canada|Brazil|Chile|Mexico)\//.test(zone)) return 'us-east';
        if (/^(Europe|Africa|Atlantic)\//.test(zone) || MIDDLE_EAST.test(zone)) return 'eu-west';
        if (/^(Asia|Australia|Indian|Pacific)\//.test(zone)) return 'asia-southeast';
        return null;
    }

    /** The areas Railway offers, each with the name of its region in Railway's list. */
    function regionChoices(regions) {
        const list = Array.isArray(regions) ? regions.filter(region => region && typeof region.name === 'string') : [];
        const choices = [];
        for (const area of AREAS) {
            const located = list.find(region => typeof region.location === 'string'
                && region.location.toLowerCase().startsWith(area.label.toLowerCase()));
            const found = located || list.find(region => region.name.startsWith(area.prefix));
            if (!found) continue;
            const location = typeof found.location === 'string' ? found.location.trim() : '';
            const label = located ? location : location ? `${area.label} (${location})` : area.label;
            choices.push({ area: area.id, value: found.name, label });
        }
        return choices;
    }

    /**
     * The choices and the one picked: the time zone's area, else the workspace's preferred region, else
     * the first. A preferred region Railway doesn't list among the four is offered as it is.
     */
    function pickRegion(choices, guessedArea, preferred) {
        const options = choices.slice();
        let picked = options.find(choice => choice.area === guessedArea);
        if (!picked && preferred) {
            picked = options.find(choice => choice.value === preferred);
            if (!picked) {
                picked = { area: 'workspace', value: preferred, label: "Your workspace's usual region" };
                options.push(picked);
            }
        }
        if (!picked) picked = options[0] || null;
        return { options, value: picked ? picked.value : null, guessed: Boolean(picked && picked.area === guessedArea) };
    }

    /** A setup code: 16 symbols of Crockford's base32 (80 random bits), in groups of four. */
    function setupCode(crypto) {
        // 256 is a multiple of 32, so the low five bits of each byte are uniform.
        const symbols = Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => CODE_ALPHABET[byte & 31]);
        return symbols.join('').match(/.{4}/g).join('-');
    }

    const base64url = bytes => btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

    /**
     * The page's own settings, from its JSON block: the redirect (this page's own address) and the relay.
     * A loopback http address is accepted only while the page itself runs on loopback (its tests).
     */
    function readConfig(text, pageHref) {
        let raw;
        try {
            raw = JSON.parse(text);
        } catch {
            return null;
        }
        if (!raw || typeof raw.redirectUri !== 'string' || typeof raw.relay !== 'string') return null;
        let page, redirect, relay;
        try {
            page = new URL(pageHref);
            redirect = new URL(raw.redirectUri);
            relay = new URL(raw.relay);
        } catch {
            return null;
        }
        const loopback = url => url.protocol === 'http:' && url.hostname === '127.0.0.1';
        const safe = url => url.protocol === 'https:' || (loopback(url) && loopback(page));
        if (!safe(redirect) || !safe(relay)) return null;
        if (redirect.origin !== page.origin || redirect.pathname !== page.pathname) return null;
        if (relay.pathname !== '/' || relay.search || relay.hash || relay.username || relay.password) return null;
        return Object.freeze({ redirectUri: redirect.href, relay: relay.origin });
    }

    /** Railway's pages for one service, as Row-Bot's host card links them. */
    function railwayPages(job) {
        if (!job || !job.projectId || !job.serviceId || !job.environmentId) return {};
        const service = `${RAILWAY}/project/${encodeURIComponent(job.projectId)}/service/${encodeURIComponent(job.serviceId)}`;
        const environment = `?environmentId=${encodeURIComponent(job.environmentId)}`;
        return { logUrl: service + environment, variablesUrl: `${service}/variables${environment}` };
    }

    class Problem extends Error {
        constructor(code, detail = '') {
            super(code);
            this.code = code;
            this.detail = detail;
        }
    }

    const notAuthorized = errors => errors.some(error => /not authori[sz]ed/i.test(String(error && error.message)));
    const said = errors => {
        const message = errors.map(error => String((error && error.message) || '')).find(Boolean) || '';
        return message.length > 200 ? `${message.slice(0, 199)}…` : message;
    };
    const edges = connection => (connection && Array.isArray(connection.edges) ? connection.edges : [])
        .map(edge => edge && edge.node).filter(Boolean);
    const environmentOf = connection => {
        const environments = edges(connection);
        const chosen = environments.find(environment => environment.name === 'production') || environments[0];
        return chosen ? chosen.id : null;
    };

    /**
     * The page's flow. `deps` gives it the browser: fetch, sessionStorage, location (search, assign),
     * history, crypto, the time zone, a clock (now) and a timer (wait). `onChange` gets each new state.
     */
    function createFlow(deps) {
        const { config, fetch, storage, location, history, crypto, timeZone, now, wait, onChange } = deps;
        let token = null;
        let job = null;
        let state = { view: 'start', notice: config ? null : 'config', busy: null, account: null, progress: null };

        const set = changes => {
            state = { ...state, ...changes };
            onChange(state);
        };
        const setAccount = changes => set({ account: { ...state.account, ...changes } });
        const setProgress = changes => set({ progress: { ...state.progress, ...changes } });

        function signedOut(notice = 'signed-out') {
            token = null;
            job = null;
            set({ view: 'start', notice, busy: null, account: null, progress: null });
        }

        async function gql(query, variables) {
            if (!token) throw new Problem('signed-out');
            let response;
            try {
                response = await fetch(`${config.relay}/graphql`, {
                    method: 'POST',
                    mode: 'cors',
                    credentials: 'omit',
                    cache: 'no-store',
                    referrerPolicy: 'no-referrer',
                    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                    body: JSON.stringify(variables ? { query, variables } : { query }),
                });
            } catch {
                throw new Problem('unreachable');
            }
            if (response.status === 401) throw new Problem('signed-out');
            // The relay's rate limit (about 60 calls a minute): nothing went to Railway.
            if (response.status === 429) throw new Problem('busy');
            let body = null;
            try {
                body = await response.json();
            } catch {
                body = null;
            }
            if (!body || typeof body !== 'object') throw new Problem('unreachable');
            return { data: body.data || null, errors: Array.isArray(body.errors) ? body.errors : [] };
        }

        /** The field's value from a call that must work, or the Problem that says why it didn't. */
        function must(result, field) {
            const value = result.data ? result.data[field] : undefined;
            if (result.errors.length || value === undefined || value === null || value === false) {
                throw new Problem(notAuthorized(result.errors) ? 'not-authorized' : 'railway', said(result.errors));
            }
            return value;
        }

        // ---- Sign-in -------------------------------------------------------------------------------

        function takeSaved() {
            let saved = null;
            try {
                saved = JSON.parse(storage.getItem(SIGN_IN_KEY) || 'null');
            } catch {
                saved = null;
            }
            storage.removeItem(SIGN_IN_KEY);
            return saved && typeof saved.verifier === 'string' && typeof saved.state === 'string' ? saved : null;
        }

        async function signIn() {
            if (!config || state.busy) return;
            const verifier = base64url(crypto.getRandomValues(new Uint8Array(48)));
            const nonce = base64url(crypto.getRandomValues(new Uint8Array(24)));
            const challenge = base64url(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))));
            storage.setItem(SIGN_IN_KEY, JSON.stringify({ verifier, state: nonce }));
            set({ busy: 'sign-in', notice: null });
            const url = new URL(AUTHORIZE_URL);
            const query = {
                response_type: 'code',
                client_id: CLIENT_ID,
                redirect_uri: config.redirectUri,
                scope: SCOPES,
                state: nonce,
                code_challenge: challenge,
                code_challenge_method: 'S256',
                prompt: 'consent',
            };
            Object.entries(query).forEach(([key, value]) => url.searchParams.set(key, value));
            location.assign(url.href);
        }

        async function exchange(code, verifier) {
            let response;
            try {
                response = await fetch(TOKEN_URL, {
                    method: 'POST',
                    mode: 'cors',
                    credentials: 'omit',
                    cache: 'no-store',
                    referrerPolicy: 'no-referrer',
                    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                    body: new URLSearchParams({
                        grant_type: 'authorization_code',
                        code,
                        redirect_uri: config.redirectUri,
                        client_id: CLIENT_ID,
                        code_verifier: verifier,
                    }).toString(),
                });
            } catch {
                throw new Problem('unreachable');
            }
            let body = null;
            try {
                body = await response.json();
            } catch {
                body = null;
            }
            if (!response.ok || !body || typeof body.access_token !== 'string' || !body.access_token) {
                throw new Problem(response.status >= 500 ? 'unreachable' : 'expired');
            }
            token = body.access_token;
        }

        /** Runs once the page loads: finishes a sign-in Railway sent back, or shows the start. */
        async function start() {
            if (!config) return set({ view: 'start', notice: 'config' });
            const params = new URLSearchParams(location.search);
            const saved = takeSaved();
            if (!params.has('code') && !params.has('error')) return set({ view: 'start' });
            history.replaceState(null, '', new URL(config.redirectUri).pathname);
            if (params.get('error') === 'access_denied') return set({ view: 'start', notice: null });
            if (params.has('error') || !params.get('code') || !saved || params.get('state') !== saved.state) {
                return set({ view: 'start', notice: 'expired' });
            }
            set({ view: 'signing-in', notice: null });
            try {
                await exchange(params.get('code'), saved.verifier);
                await loadAccount();
            } catch (problem) {
                signedOut(problem instanceof Problem && problem.code !== 'signed-out' ? problem.code : 'expired');
            }
        }

        // ---- The account ---------------------------------------------------------------------------

        let workspaces = [];
        let regionList = [];

        async function loadAccount() {
            const me = await gql(QUERIES.me);
            const user = me.data && me.data.me;
            if (!user) throw new Problem(notAuthorized(me.errors) ? 'signed-out' : 'unreachable');
            workspaces = (Array.isArray(user.workspaces) ? user.workspaces : [])
                .filter(workspace => workspace && typeof workspace.id === 'string');
            if (!workspaces.length) throw new Problem('no-workspace');
            const regions = await gql(QUERIES.regions).catch(problem => {
                if (problem.code === 'signed-out') throw problem;
                return { data: null, errors: [] };
            });
            regionList = (regions.data && regions.data.regions) || [];
            const account = {
                email: typeof user.email === 'string' ? user.email : '',
                verified: user.isVerified !== false,
                workspaces: workspaces.map(workspace => ({ id: workspace.id, name: String(workspace.name || 'Workspace') })),
                blocked: null,
            };
            set({ account, notice: null });
            await useWorkspace(workspaces[0].id);
            set({ view: 'account' });
        }

        /** What the page shows for one workspace: its region, its plan, and any Row-Bot already in it. */
        async function useWorkspace(id) {
            const workspace = workspaces.find(candidate => candidate.id === id) || workspaces[0];
            const region = pickRegion(regionChoices(regionList), guessArea(timeZone), workspace.preferredRegion || null);
            const customer = workspace.customer || {};
            const plan = customer.isTrialing ? 'trial' : workspace.plan === 'FREE' ? 'free' : null;
            const projects = edges(workspace.projects).filter(project => !project.deletedAt && PROJECT_NAME.test(String(project.name || '')));
            const existing = projects.length ? await existingRowBot(projects[0]) : null;
            setAccount({
                workspaceId: workspace.id,
                regions: region.options,
                region: region.value,
                regionGuessed: region.guessed,
                plan,
                plansUrl: PLANS_URL,
                verifyUrl: VERIFY_URL,
                existing,
                blocked: null,
            });
        }

        /** Where a Row-Bot already in the workspace answers: its own address, or else its Railway project. */
        async function existingRowBot(project) {
            const found = { name: String(project.name), url: `${RAILWAY}/project/${encodeURIComponent(project.id)}`, inRailway: true };
            try {
                const details = must(await gql(QUERIES.project, { id: project.id }), 'project');
                const environment = environmentOf(details.environments);
                const services = edges(details.services);
                const service = services.find(candidate => /row-bot/i.test(String(candidate.name || ''))) || services[0];
                if (!environment || !service) return found;
                const domains = must(await gql(QUERIES.domains, { p: project.id, e: environment, s: service.id }), 'domains');
                const domain = (Array.isArray(domains.serviceDomains) ? domains.serviceDomains : [])
                    .map(entry => entry && entry.domain).find(value => typeof value === 'string' && /^[a-z0-9.-]+$/i.test(value));
                return domain ? { name: found.name, url: `https://${domain}/`, inRailway: false } : found;
            } catch (problem) {
                if (problem.code === 'signed-out') throw problem;
                return found;
            }
        }

        async function selectWorkspace(id) {
            if (state.busy || !state.account) return;
            set({ busy: 'workspace' });
            try {
                await useWorkspace(id);
                set({ busy: null });
            } catch (problem) {
                signedOut(problem.code === 'signed-out' || problem.code === 'busy' ? problem.code : 'unreachable');
            }
        }

        function selectRegion(value) {
            if (!state.account || !state.account.regions.some(choice => choice.value === value)) return;
            setAccount({ region: value, regionGuessed: false });
        }

        /** Reads the account again, after the person verified it at Railway. */
        async function recheck() {
            if (state.busy || !state.account || state.view !== 'account') return;
            set({ busy: 'recheck' });
            try {
                const me = await gql(QUERIES.me);
                const user = me.data && me.data.me;
                if (!user) throw new Problem(notAuthorized(me.errors) ? 'signed-out' : 'unreachable');
                setAccount({ verified: user.isVerified !== false });
                set({ busy: null });
            } catch (problem) {
                if (problem.code === 'signed-out') return signedOut();
                set({ busy: null });
            }
        }

        /** After "no permission here": another workspace, or Railway's consent screen to pick one. */
        function chooseAgain() {
            if (!state.account) return;
            if (state.account.workspaces.length > 1) return setAccount({ blocked: null });
            token = null;
            return signIn();
        }

        // ---- Create --------------------------------------------------------------------------------

        function projectName(workspace) {
            const taken = new Set(edges(workspace.projects).filter(project => !project.deletedAt)
                .map(project => String(project.name || '').toLowerCase()));
            if (!taken.has('row-bot')) return 'Row-Bot';
            let number = 2;
            while (taken.has(`row-bot ${number}`)) number += 1;
            return `Row-Bot ${number}`;
        }

        async function create() {
            const account = state.account;
            if (state.busy || !account || !account.verified || state.view !== 'account') return;
            const workspace = workspaces.find(candidate => candidate.id === account.workspaceId);
            if (!workspace) return;
            job = {
                workspaceId: workspace.id,
                preferredRegion: workspace.preferredRegion || null,
                region: account.region,
                name: projectName(workspace),
                code: setupCode(crypto),
                projectId: null,
                environmentId: null,
                serviceId: null,
                domain: null,
                deploymentId: null,
                done: new Set(),
                startedAt: now(),
            };
            set({ view: 'progress', progress: { step: 'project', status: 'running', reason: null, detail: '' } });
            await run();
        }

        async function run() {
            try {
                await setUp();
                await follow();
            } catch (problem) {
                failed(problem instanceof Problem ? problem : new Problem('railway'));
            }
        }

        /** Each call Railway needs, in order; on Try again, only the ones not yet done. */
        async function setUp() {
            if (!job.projectId) {
                const created = await gql(QUERIES.projectCreate, { input: { name: job.name, workspaceId: job.workspaceId } });
                if (notAuthorized(created.errors)) await noPermission();
                const project = must(created, 'projectCreate');
                const environmentId = environmentOf(project.environments);
                if (typeof project.id !== 'string' || !environmentId) throw new Problem('railway');
                job.projectId = project.id;
                job.environmentId = environmentId;
            }
            setProgress({ step: 'starting', status: 'running', reason: null, detail: '' });
            if (!job.serviceId) {
                const service = must(await gql(QUERIES.serviceCreate, {
                    input: {
                        projectId: job.projectId,
                        environmentId: job.environmentId,
                        name: 'row-bot', // as the template names its service
                        source: { image: IMAGE },
                        variables: {
                            ROW_BOT_SETUP_CODE: job.code,
                            ROW_BOT_PUBLIC_URL: 'https://${{RAILWAY_PUBLIC_DOMAIN}}',
                            ROW_BOT_HOSTED_BY: 'railway',
                            RAILWAY_RUN_UID: '0',
                            PORT: String(PORT),
                            ROW_BOT_TRUSTED_PROXY_CIDRS: '100.64.0.0/10',
                        },
                    },
                }), 'serviceCreate');
                job.serviceId = service.id;
                setProgress(railwayPages(job));
            }
            if (!job.done.has('volume')) {
                must(await gql(QUERIES.volumeCreate, {
                    input: { projectId: job.projectId, environmentId: job.environmentId, serviceId: job.serviceId, mountPath: '/data' },
                }), 'volumeCreate');
                job.done.add('volume');
            }
            if (!job.done.has('region')) {
                // Region only, and no health check: Railway's probe gets a 400 from Row-Bot and the deploy hangs.
                // Which names from Railway's region list it takes is untested, so a refused one falls back
                // to the workspace's preferred region (proven), then to Railway's default.
                for (const region of [job.region, job.preferredRegion].filter((value, index, all) => value && all.indexOf(value) === index)) {
                    const updated = await gql(QUERIES.serviceInstanceUpdate, {
                        s: job.serviceId, e: job.environmentId, input: { region },
                    });
                    if (!updated.errors.length) break;
                }
                job.done.add('region');
            }
            if (!job.done.has('limits')) {
                // Best effort: a plan below 8 GB (Railway's trial) keeps its own limit.
                await gql(QUERIES.serviceInstanceLimitsUpdate, {
                    input: { serviceId: job.serviceId, environmentId: job.environmentId, memoryGB: 8, vCPUs: 8 },
                });
                job.done.add('limits');
            }
            if (!job.domain) {
                // Before the deploy, so ${{RAILWAY_PUBLIC_DOMAIN}} resolves when Row-Bot starts.
                const domain = must(await gql(QUERIES.serviceDomainCreate, {
                    input: { serviceId: job.serviceId, environmentId: job.environmentId, targetPort: PORT },
                }), 'serviceDomainCreate');
                if (typeof domain.domain !== 'string' || !/^[a-z0-9.-]+$/i.test(domain.domain)) throw new Problem('railway');
                job.domain = domain.domain;
                setProgress({ address: `https://${job.domain}` });
            }
            if (!job.done.has('updates')) {
                // Nightly image updates, 02:00-06:00 UTC every day, as Railway's dashboard writes them.
                // Best effort: Row-Bot's Updates page points to Railway's setting either way.
                await gql(QUERIES.environmentPatchCommit, {
                    e: job.environmentId,
                    patch: {
                        services: {
                            [job.serviceId]: {
                                source: {
                                    autoUpdates: {
                                        type: 'patch',
                                        schedule: [0, 1, 2, 3, 4, 5, 6].map(day => ({ day, startHour: 2, endHour: 6 })),
                                    },
                                },
                            },
                        },
                    },
                    skip: true,
                });
                job.done.add('updates');
            }
            if (!job.deploymentId) {
                const deployment = must(await gql(QUERIES.serviceInstanceDeployV2, { s: job.serviceId, e: job.environmentId }),
                    'serviceInstanceDeployV2');
                if (typeof deployment !== 'string') throw new Problem('railway');
                job.deploymentId = deployment;
            }
        }

        /** Nothing was made: back to the account, to pick a workspace this account may create in. */
        async function noPermission() {
            const check = await gql(QUERIES.me).catch(() => null);
            if (!check || !check.data || !check.data.me) throw new Problem('signed-out');
            job = null;
            set({ view: 'account', progress: null });
            setAccount({ blocked: 'not-authorized' });
            throw new Problem('handled');
        }

        async function follow() {
            if (!job || !job.deploymentId) return;
            for (;;) {
                const elapsed = now() - job.startedAt;
                if (elapsed >= SLOW_MS && state.progress.status !== 'slow') setProgress({ status: 'slow' });
                if (elapsed >= GIVE_UP_MS) return;
                await wait(POLL_MS);
                let status = '';
                try {
                    const result = await gql(QUERIES.deployment, { id: job.deploymentId });
                    status = String((result.data && result.data.deployment && result.data.deployment.status) || '');
                } catch (problem) {
                    // A sign-in that ran out mid-way: the slow note says where the Row-Bot and its code are.
                    if (problem.code === 'signed-out') return setProgress({ status: 'slow' });
                    continue;
                }
                if (status === 'SUCCESS') break;
                if (FAILED.has(status)) throw new Problem('deploy');
            }
            setProgress({ step: 'signing-in', status: 'running' });
            await wait(SETTLE_MS);
            const address = `https://${job.domain}/setup#code=${encodeURIComponent(job.code)}`;
            token = null;
            job = null;
            location.assign(address);
        }

        function failed(problem) {
            if (problem.code === 'handled') return;
            if (problem.code === 'signed-out') return signedOut();
            const reason = ['deploy', 'unreachable', 'busy'].includes(problem.code) ? problem.code : 'railway';
            setProgress({ status: 'failed', reason, detail: problem.detail || '' });
        }

        async function retry() {
            if (!job || state.view !== 'progress' || state.progress.status !== 'failed') return;
            if (state.progress.reason === 'deploy') job.deploymentId = null;
            job.startedAt = now();
            setProgress({ status: 'running', reason: null, detail: '', step: job.projectId ? 'starting' : 'project' });
            await run();
        }

        return Object.freeze({
            start,
            signIn,
            selectWorkspace,
            selectRegion,
            recheck,
            chooseAgain,
            create,
            createAnother: create,
            retry,
            resetAfterReturn: () => {
                if (state.busy === 'sign-in') set({ busy: null });
            },
            get state() {
                return state;
            },
        });
    }

    window.RowBotRailway = Object.freeze({
        CLIENT_ID,
        AUTHORIZE_URL,
        TOKEN_URL,
        SCOPES,
        IMAGE,
        QUERIES,
        createFlow,
        readConfig,
        guessArea,
        regionChoices,
        pickRegion,
        setupCode,
    });
})();
