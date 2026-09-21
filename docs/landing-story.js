(() => {
    'use strict';

    const STATES = ['idle', 'thinking', 'working', 'approval', 'success', 'error'];
    const LABELS = {
        idle: 'Orbit is ready.',
        thinking: 'Orbit is researching.',
        working: 'Orbit is building.',
        approval: 'Orbit is waiting for your approval.',
        success: 'Orbit completed the work.',
        error: 'Orbit paused safely.'
    };
    const SOURCES = Object.fromEntries(STATES.map(state => [state, `media/landing-story/buddy/${state}.mp4`]));
    const reduceMotionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    const saveData = Boolean(navigator.connection?.saveData);
    const stage = document.querySelector('[data-buddy-stage]');
    const control = document.querySelector('[data-buddy-control]');
    const status = document.querySelector('[data-buddy-status]');
    const videos = [...document.querySelectorAll('[data-buddy-video]')];
    let stateIndex = 0;
    let activeVideo = 0;
    let stageVisible = true;
    let mediaFailed = false;

    function motionAllowed() {
        return !reduceMotionQuery.matches && !saveData && !document.hidden && stageVisible && !mediaFailed;
    }

    function pauseAll(except = null) {
        videos.forEach(video => {
            if (video !== except) video.pause();
        });
        document.querySelectorAll('[data-story-video]').forEach(video => {
            if (video !== except) {
                video.pause();
                video.classList.remove('is-playing');
            }
        });
    }

    function setBuddyState(nextState, announce = true) {
        const nextIndex = STATES.indexOf(nextState);
        if (nextIndex < 0 || !stage || videos.length < 2) return;
        stateIndex = nextIndex;
        stage.dataset.state = nextState;
        if (status && announce) status.textContent = LABELS[nextState];
        if (!motionAllowed()) {
            videos.forEach(video => video.pause());
            return;
        }
        const incomingIndex = activeVideo === 0 ? 1 : 0;
        const incoming = videos[incomingIndex];
        const outgoing = videos[activeVideo];
        if (!incoming.src.endsWith(SOURCES[nextState])) incoming.src = SOURCES[nextState];
        incoming.currentTime = 0;
        incoming.play().then(() => {
            incoming.classList.add('is-active');
            outgoing.classList.remove('is-active');
            window.setTimeout(() => outgoing.pause(), 430);
            activeVideo = incomingIndex;
        }).catch(() => {
            mediaFailed = true;
            pauseAll();
        });
    }

    function resumeBuddy() {
        if (!motionAllowed()) return;
        const current = videos[activeVideo];
        if (!current.src) current.src = SOURCES[STATES[stateIndex]];
        current.play().catch(() => { mediaFailed = true; });
    }

    control?.addEventListener('click', () => setBuddyState(STATES[(stateIndex + 1) % STATES.length]));
    control?.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            setBuddyState(STATES[(stateIndex + 1) % STATES.length]);
        }
    });
    videos.forEach(video => video.addEventListener('error', () => {
        mediaFailed = true;
        pauseAll();
        if (status) status.textContent = 'Orbit is available as a still image.';
    }));

    if ('IntersectionObserver' in window && stage) {
        new IntersectionObserver(entries => {
            stageVisible = Boolean(entries[0]?.isIntersecting);
            if (stageVisible) resumeBuddy(); else pauseAll();
        }, { threshold: .08 }).observe(stage);
    }
    document.addEventListener('visibilitychange', () => {
        if (document.hidden) pauseAll(); else resumeBuddy();
    });
    reduceMotionQuery.addEventListener?.('change', () => {
        if (motionAllowed()) resumeBuddy(); else pauseAll();
    });

    const beatState = { research: 'thinking', create: 'working', automate: 'success', control: 'approval' };
    const triggers = [...document.querySelectorAll('[data-story-trigger]')];
    const panels = [...document.querySelectorAll('[data-story-panel]')];
    const storyController = document.querySelector('[data-story-controller]');
    let storyVisible = false;

    function playStoryClip(beat) {
        const clip = document.querySelector(`[data-story-video="${beat}"]`);
        if (!clip || !storyVisible || !motionAllowed()) return;
        pauseAll(clip);
        clip.play().then(() => clip.classList.add('is-playing')).catch(() => clip.classList.remove('is-playing'));
    }

    function setStoryBeat(beat, { focus = false } = {}) {
        if (!beatState[beat]) return;
        triggers.forEach(trigger => {
            const selected = trigger.dataset.storyTrigger === beat;
            if (selected) trigger.setAttribute('aria-current', 'step'); else trigger.removeAttribute('aria-current');
            if (selected && focus) trigger.focus();
        });
        panels.forEach(panel => {
            const selected = panel.dataset.storyPanel === beat;
            panel.hidden = !selected;
            panel.classList.toggle('is-active', selected);
        });
        pauseAll();
        playStoryClip(beat);
        setBuddyState(beatState[beat], false);
    }

    if ('IntersectionObserver' in window && storyController) {
        new IntersectionObserver(entries => {
            storyVisible = Boolean(entries[0]?.isIntersecting);
            if (storyVisible) {
                const current = triggers.find(item => item.hasAttribute('aria-current'))?.dataset.storyTrigger;
                if (current) playStoryClip(current);
            } else {
                document.querySelectorAll('[data-story-video]').forEach(video => {
                    video.pause();
                    video.classList.remove('is-playing');
                });
            }
        }, { threshold: .12 }).observe(storyController);
    }

    triggers.forEach((trigger, index) => {
        trigger.addEventListener('click', () => setStoryBeat(trigger.dataset.storyTrigger));
        trigger.addEventListener('keydown', event => {
            const delta = event.key === 'ArrowRight' || event.key === 'ArrowDown' ? 1 : event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 0;
            if (!delta) return;
            event.preventDefault();
            const next = triggers[(index + delta + triggers.length) % triggers.length];
            setStoryBeat(next.dataset.storyTrigger, { focus: true });
        });
    });

    const capabilityCopy = {
        agents: 'Coordinate agent work while keeping dependencies and outcomes visible.',
        models: 'Choose local or hosted routes explicitly for each kind of work.',
        knowledge: 'Keep documents, graph knowledge, and recall durable on your host.',
        workflows: 'Schedule repeatable work with budgets, history, and delivery policy.',
        developer: 'Work with repositories, diffs, tests, and approval-aware tools.',
        designer: 'Create editable pages, decks, documents, mockups, and storyboards.',
        tools: 'Use browser, shell, files, MCP, and skills behind clear boundaries.',
        channels: 'Connect messaging surfaces without silently delivering output.',
        voice: 'Talk with the same workspace, context, models, and controls.'
    };
    const capabilityStatus = document.querySelector('[data-constellation-status]');
    document.querySelectorAll('[data-capability]').forEach(button => button.addEventListener('click', () => {
        document.querySelectorAll('[data-capability]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
        if (capabilityStatus) capabilityStatus.textContent = capabilityCopy[button.dataset.capability];
    }));

    function drawSovereigntyField() {
        const canvas = document.querySelector('[data-sovereignty-canvas]');
        if (!canvas || reduceMotionQuery.matches) return;
        const context = canvas.getContext('2d');
        if (!context) return;
        const ratio = Math.min(window.devicePixelRatio || 1, 2);
        const width = canvas.clientWidth;
        const height = canvas.clientHeight;
        canvas.width = Math.round(width * ratio);
        canvas.height = Math.round(height * ratio);
        context.scale(ratio, ratio);
        let seed = 1977;
        const random = () => ((seed = (seed * 48271) % 2147483647) / 2147483647);
        const count = Math.min(46, Math.max(18, Math.floor(width / 30)));
        const points = Array.from({ length: count }, () => ({ x: random() * width, y: random() * height }));
        context.clearRect(0, 0, width, height);
        context.lineWidth = .7;
        for (let i = 0; i < points.length; i += 1) {
            for (let j = i + 1; j < points.length; j += 1) {
                const distance = Math.hypot(points[i].x - points[j].x, points[i].y - points[j].y);
                if (distance > 150) continue;
                context.strokeStyle = `rgba(120,174,227,${(1 - distance / 150) * .16})`;
                context.beginPath(); context.moveTo(points[i].x, points[i].y); context.lineTo(points[j].x, points[j].y); context.stroke();
            }
            context.fillStyle = i % 9 === 0 ? 'rgba(213,173,114,.75)' : 'rgba(120,174,227,.55)';
            context.beginPath(); context.arc(points[i].x, points[i].y, i % 9 === 0 ? 2.2 : 1.4, 0, Math.PI * 2); context.fill();
        }
    }
    let resizeFrame = 0;
    window.addEventListener('resize', () => {
        window.cancelAnimationFrame(resizeFrame);
        resizeFrame = window.requestAnimationFrame(drawSovereigntyField);
    });
    drawSovereigntyField();
    setStoryBeat('research');
    setBuddyState('idle', false);

    window.RowBotLandingStory = {
        setScene: setStoryBeat,
        setBuddyState,
        freezeMotion() { mediaFailed = true; pauseAll(); },
        getState() { return { beat: triggers.find(item => item.hasAttribute('aria-current'))?.dataset.storyTrigger || '', buddy: STATES[stateIndex], motionAllowed: motionAllowed() }; }
    };
})();
