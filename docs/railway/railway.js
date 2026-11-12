/* "Continue with Railway": draws flow.js's state into the page and passes on what the person does. */
(() => {
    'use strict';

    const api = window.RowBotRailway;
    const doc = document;
    doc.documentElement.classList.add('js');

    const one = selector => doc.querySelector(selector);
    const views = {
        start: one('[data-view="start"]'),
        'signing-in': one('[data-view="signing-in"]'),
        account: one('[data-view="account"]'),
        progress: one('[data-view="progress"]'),
    };
    const el = {
        startNotice: one('[data-start-notice]'),
        signIn: one('[data-action="sign-in"]'),
        signedIn: one('[data-signed-in]'),
        workspaceChoice: one('[data-workspace-choice]'),
        workspace: one('[data-workspace]'),
        workspaceLabel: one('[data-workspace-label]'),
        workspaceName: one('[data-workspace-name]'),
        regionField: one('[data-region-field]'),
        region: one('[data-region]'),
        regionHint: one('[data-region-hint]'),
        unverified: one('[data-unverified]'),
        verifyLink: one('[data-verify-link]'),
        blocked: one('[data-blocked]'),
        existing: one('[data-existing]'),
        existingLink: one('[data-existing-link]'),
        existingAddress: one('[data-existing-address]'),
        createActions: one('[data-create-actions]'),
        create: one('[data-action="create"]'),
        recheck: one('[data-action="recheck"]'),
        planNote: one('[data-plan-note]'),
        planText: one('[data-plan-text]'),
        plansLink: one('[data-plans-link]'),
        keepOpen: one('[data-keep-open]'),
        stepAside: one('[data-step-aside]'),
        slow: one('[data-slow]'),
        address: one('[data-address]'),
        variablesLink: one('[data-variables-link]'),
        failed: one('[data-failed]'),
        failedText: one('[data-failed-text]'),
        logLink: one('[data-log-link]'),
        announce: one('[data-announce]'),
    };

    const NOTICES = {
        expired: "Railway's sign-in didn't finish, or took too long. Continue with Railway to start again.",
        'signed-out': 'Your Railway sign-in has expired. Continue with Railway to sign in again.',
        unreachable: "Couldn't reach Railway. Check your connection, then try again.",
        busy: 'Too many tries in a minute. Wait a minute, then continue with Railway again.',
        'no-workspace': "Railway didn't share a workspace with this page. Continue with Railway and choose one.",
        config: "This page can't sign in to Railway from here. Open row-bot.ai/railway/ to continue.",
        framed: 'Open row-bot.ai/railway/ in its own tab to continue.',
    };
    const PLAN_NOTES = {
        trial: "On Railway's trial, chats work. Browser tasks need Railway's Hobby plan ($5 a month).",
        free: "Row-Bot needs more than Railway's free plan gives. Choose Hobby ($5 a month).",
    };
    const FAILURES = {
        deploy: "Railway couldn't start Row-Bot.",
        unreachable: "Couldn't reach Railway. Check your connection, then try again.",
        railway: "Railway couldn't create Row-Bot.",
        busy: 'Too many tries in a minute. Wait a minute, then try again.',
    };
    const STEP_ORDER = ['project', 'starting', 'signing-in'];
    const STEP_STATUS = { done: 'Done: ', running: 'In progress: ', failed: 'Failed: ', pending: 'Next: ' };
    const VIEW_ANNOUNCEMENTS = {
        'signing-in': 'Signing in to Railway.',
        account: 'Signed in to Railway.',
        progress: 'Creating your Row-Bot.',
    };

    let lastView = null;
    let lastProgress = '';

    const show = (node, visible) => {
        node.hidden = !visible;
    };
    const announce = text => {
        el.announce.textContent = '';
        if (text) window.setTimeout(() => { el.announce.textContent = text; }, 60);
    };

    function fillSelect(select, options, value) {
        const wanted = options.map(option => `${option.value}\u0000${option.label}`).join('\u0001');
        if (select.dataset.options !== wanted) {
            select.replaceChildren(...options.map(option => new Option(option.label, option.value)));
            select.dataset.options = wanted;
        }
        if (value !== null && value !== undefined) select.value = value;
    }

    function renderStart(state) {
        const notice = state.notice ? NOTICES[state.notice] : '';
        el.startNotice.textContent = notice || '';
        show(el.startNotice, Boolean(notice));
        const blocked = state.notice === 'config' || state.notice === 'framed';
        el.signIn.disabled = blocked || state.busy === 'sign-in';
        el.signIn.textContent = state.busy === 'sign-in' ? 'Opening Railway…' : 'Continue with Railway';
        el.signIn.setAttribute('aria-busy', String(state.busy === 'sign-in'));
    }

    function renderAccount(state) {
        const account = state.account;
        if (!account || !account.workspaceId) return;
        el.signedIn.textContent = account.email ? `Signed in to Railway as ${account.email}` : 'Signed in to Railway';

        const many = account.workspaces.length > 1;
        show(el.workspaceChoice, many);
        show(el.workspaceName, !many);
        el.workspaceLabel.htmlFor = many ? 'workspace' : '';
        if (many) {
            fillSelect(el.workspace, account.workspaces.map(workspace => ({ value: workspace.id, label: workspace.name })), account.workspaceId);
        } else {
            el.workspaceName.textContent = account.workspaces[0].name;
        }
        el.workspace.disabled = Boolean(state.busy);

        const regions = Array.isArray(account.regions) ? account.regions : [];
        show(el.regionField, regions.length > 0);
        fillSelect(el.region, regions, account.region);
        el.region.disabled = Boolean(state.busy);
        show(el.regionHint, Boolean(account.regionGuessed));

        show(el.unverified, !account.verified);
        el.verifyLink.href = account.verifyUrl;
        show(el.recheck, !account.verified);
        el.recheck.disabled = state.busy === 'recheck';
        el.recheck.setAttribute('aria-busy', String(state.busy === 'recheck'));
        const blocked = account.blocked === 'not-authorized';
        show(el.blocked, blocked);
        el.create.disabled = !account.verified || blocked || Boolean(state.busy);
        const reason = !account.verified ? 'unverified-note' : blocked ? 'blocked-note' : '';
        if (reason) el.create.setAttribute('aria-describedby', reason);
        else el.create.removeAttribute('aria-describedby');

        const existing = account.existing;
        show(el.existing, Boolean(existing) && account.verified);
        show(el.createActions, !existing || !account.verified);
        if (existing) {
            el.existingLink.href = existing.url;
            el.existingLink.textContent = existing.inRailway ? 'Open it in Railway' : 'Open it';
            el.existingAddress.textContent = existing.inRailway ? '' : new URL(existing.url).host;
            show(el.existingAddress, !existing.inRailway);
        }

        const planText = account.plan ? PLAN_NOTES[account.plan] : '';
        show(el.planNote, Boolean(planText));
        el.planText.textContent = planText || '';
        el.plansLink.href = account.plansUrl;
    }

    function renderProgress(state) {
        const progress = state.progress;
        if (!progress) return;
        const current = STEP_ORDER.indexOf(progress.step);
        for (const item of doc.querySelectorAll('[data-step]')) {
            const index = STEP_ORDER.indexOf(item.dataset.step);
            const status = index < current ? 'done' : index > current ? 'pending'
                : progress.status === 'failed' ? 'failed' : 'running';
            item.dataset.state = status;
            item.querySelector('[data-step-status]').textContent = STEP_STATUS[status];
            if (status === 'running') item.setAttribute('aria-current', 'step');
            else item.removeAttribute('aria-current');
            const text = item.querySelector('[data-step-text]');
            if (text) {
                text.textContent = status === 'done' ? 'Project created in your Railway account'
                    : 'Creating a project in your Railway account';
            }
        }

        show(el.keepOpen, progress.status === 'running');
        show(el.stepAside, progress.step === 'starting' && progress.status === 'running');
        const slow = progress.status === 'slow' && Boolean(progress.address);
        show(el.slow, slow);
        if (slow) {
            el.address.href = progress.address;
            el.address.textContent = progress.address;
            el.variablesLink.href = progress.variablesUrl || 'https://railway.com/dashboard';
        }

        const failed = progress.status === 'failed';
        show(el.failed, failed);
        if (failed) {
            const detail = progress.reason === 'railway' && progress.detail ? ` It said: “${progress.detail}”` : '';
            el.failedText.textContent = (FAILURES[progress.reason] || FAILURES.railway) + detail;
            show(el.logLink, Boolean(progress.logUrl));
            if (progress.logUrl) el.logLink.href = progress.logUrl;
        }

        const key = `${progress.step}:${progress.status}`;
        if (key !== lastProgress) {
            lastProgress = key;
            if (failed) return;
            if (progress.status === 'slow') announce('Still starting. Railway is downloading Row-Bot.');
            else if (progress.step === 'starting') announce('Project created. Starting Row-Bot.');
            else if (progress.step === 'signing-in') announce('Row-Bot started. Signing you in.');
        }
    }

    function render(state) {
        for (const [name, node] of Object.entries(views)) show(node, name === state.view);
        renderStart(state);
        renderAccount(state);
        renderProgress(state);
        if (state.view !== lastView) {
            const first = lastView === null;
            lastView = state.view;
            lastProgress = '';
            if (!first) views[state.view].querySelector('h1').focus({ preventScroll: false });
            announce(VIEW_ANNOUNCEMENTS[state.view] || '');
        }
    }

    // A page in someone else's frame could be clicked through; it only works in its own tab.
    if (window.top !== window.self) {
        render({ view: 'start', notice: 'framed', busy: null, account: null, progress: null });
        return;
    }

    let storage;
    try {
        storage = window.sessionStorage;
    } catch {
        storage = { getItem: () => null, setItem() {}, removeItem() {} };
    }
    const config = api.readConfig((one('#railway-config') || {}).textContent || '', window.location.href);
    let timeZone = '';
    try {
        timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
    } catch {
        timeZone = '';
    }

    const flow = api.createFlow({
        config,
        fetch: window.fetch.bind(window),
        storage,
        location: window.location,
        history: window.history,
        crypto: window.crypto,
        timeZone,
        now: () => Date.now(),
        wait: ms => new Promise(resolve => window.setTimeout(resolve, ms)),
        onChange: render,
    });

    const actions = {
        'sign-in': () => flow.signIn(),
        create: () => flow.create(),
        'create-another': () => flow.createAnother(),
        recheck: () => flow.recheck(),
        retry: () => flow.retry(),
        'choose-again': () => {
            flow.chooseAgain();
            if (flow.state.account && flow.state.account.workspaces.length > 1) el.workspace.focus();
        },
    };
    doc.addEventListener('click', event => {
        const button = event.target.closest('[data-action]');
        if (!button || button.disabled || !actions[button.dataset.action]) return;
        actions[button.dataset.action]();
    });
    el.workspace.addEventListener('change', () => flow.selectWorkspace(el.workspace.value));
    el.region.addEventListener('change', () => flow.selectRegion(el.region.value));

    // Back from Railway's verification tab: read the account again.
    doc.addEventListener('visibilitychange', () => {
        const state = flow.state;
        if (doc.visibilityState === 'visible' && state.view === 'account' && state.account && !state.account.verified) flow.recheck();
    });
    // Back from Railway's sign-in with the browser's Back button (a page restored from its cache).
    window.addEventListener('pageshow', event => {
        if (event.persisted) flow.resetAfterReturn();
    });

    flow.start();
})();
