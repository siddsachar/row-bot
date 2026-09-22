(() => {
    'use strict';

    const STATES = ['idle', 'thinking', 'working', 'approval', 'success', 'error'];
    const BEATS = ['research', 'create', 'automate', 'ship'];
    const BEAT_STATES = { research: 'thinking', create: 'working', automate: 'working', ship: 'approval' };
    const LABELS = {
        idle: 'Buddy is ready.',
        thinking: 'Buddy is researching.',
        working: 'Buddy is building.',
        approval: 'Buddy is waiting for your approval.',
        success: 'Buddy completed the work.',
        error: 'Buddy paused safely.'
    };
    const SOURCES = Object.fromEntries(STATES.map(state => [state, `media/landing-story/buddy/${state}.webp`]));
    const reduceMotionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    const saveData = Boolean(navigator.connection?.saveData);
    const controller = document.querySelector('[data-story-controller]');
    const stage = document.querySelector('[data-buddy-stage]');
    const control = document.querySelector('[data-buddy-control]');
    const status = document.querySelector('[data-buddy-status]');
    const images = [...document.querySelectorAll('[data-buddy-image]')];
    const triggers = [...document.querySelectorAll('[data-story-trigger]')];
    const panels = [...document.querySelectorAll('[data-story-panel]')];
    const copies = [...document.querySelectorAll('[data-story-copy]')];
    const videos = [...document.querySelectorAll('[data-story-video]')];

    let stateIndex = STATES.indexOf('thinking');
    let activeImage = 0;
    let currentBeat = 'research';
    let storyVisible = true;
    let mediaFrozen = false;
    let buddyFailed = false;
    let crossfadePending = false;
    let queuedState = null;
    let scrollFrame = 0;

    function playbackAllowed() {
        return !reduceMotionQuery.matches && !saveData && !document.hidden && !mediaFrozen;
    }

    function buddyMotionAllowed() {
        return playbackAllowed() && storyVisible && !buddyFailed;
    }

    function pauseVideos(except = null) {
        videos.forEach(video => {
            if (video !== except) {
                video.pause();
                video.classList.remove('is-playing');
            }
        });
    }

    function pauseAll() {
        stage?.classList.add('is-paused');
        pauseVideos();
    }

    function resumeBuddy() {
        stage?.classList.toggle('is-paused', !buddyMotionAllowed());
    }

    function crossfadeBuddy(nextState) {
        const incomingIndex = activeImage === 0 ? 1 : 0;
        const incoming = images[incomingIndex];
        const outgoing = images[activeImage];
        if (!incoming || !outgoing) return;
        if (crossfadePending) {
            queuedState = nextState;
            return;
        }
        crossfadePending = true;
        const source = SOURCES[nextState];
        if (!incoming.src.endsWith(source)) incoming.src = source;
        const ready = incoming.decode ? incoming.decode() : Promise.resolve();
        ready.then(() => {
            incoming.classList.add('is-active');
            outgoing.classList.remove('is-active');
            activeImage = incomingIndex;
            window.setTimeout(() => {
                crossfadePending = false;
                if (queuedState) {
                    const queued = queuedState;
                    queuedState = null;
                    crossfadeBuddy(queued);
                }
            }, reduceMotionQuery.matches ? 0 : 380);
        }).catch(() => {
            buddyFailed = true;
            crossfadePending = false;
            queuedState = null;
            stage?.classList.add('is-paused');
            if (status) status.textContent = 'Buddy is available as a still image.';
        });
    }

    function setBuddyState(nextState, announce = true) {
        const nextIndex = STATES.indexOf(nextState);
        if (nextIndex < 0 || !stage || images.length < 2) return;
        stateIndex = nextIndex;
        stage.dataset.state = nextState;
        if (status) {
            status.setAttribute('aria-live', announce ? 'polite' : 'off');
            status.textContent = LABELS[nextState];
        }
        if (images[activeImage]?.src.endsWith(SOURCES[nextState])) {
            resumeBuddy();
            return;
        }
        crossfadeBuddy(nextState);
        resumeBuddy();
    }

    function playStoryClip(beat, restart = false) {
        const clip = document.querySelector(`[data-story-video="${beat}"]`);
        if (!clip || !storyVisible || !playbackAllowed()) return;
        pauseVideos(clip);
        if (restart) {
            try { clip.currentTime = 0; } catch (_) { /* Metadata may not be ready yet. */ }
        }
        clip.play().then(() => {
            clip.classList.add('is-playing', 'has-played');
        }).catch(() => clip.classList.remove('is-playing'));
    }

    function setStoryBeat(beat, { focus = false, restart = false } = {}) {
        if (!BEAT_STATES[beat]) return;
        const changed = currentBeat !== beat;
        currentBeat = beat;
        if (controller) controller.dataset.scene = beat;
        triggers.forEach(trigger => {
            const selected = trigger.dataset.storyTrigger === beat;
            if (selected) trigger.setAttribute('aria-current', 'step');
            else trigger.removeAttribute('aria-current');
            if (selected && focus) trigger.focus();
        });
        panels.forEach(panel => {
            const selected = panel.dataset.storyPanel === beat;
            panel.hidden = !selected;
            panel.classList.toggle('is-active', selected);
        });
        copies.forEach(copy => {
            const selected = copy.dataset.storyCopy === beat;
            copy.hidden = !selected;
            copy.classList.toggle('is-active', selected);
        });
        setBuddyState(BEAT_STATES[beat], false);
        playStoryClip(beat, restart || changed);
    }

    function goToBeat(beat, focus = false) {
        setStoryBeat(beat, { focus, restart: true });
        if (!controller || reduceMotionQuery.matches || window.innerWidth <= 760 || typeof window.scrollTo !== 'function') return;
        const index = BEATS.indexOf(beat);
        const available = Math.max(controller.offsetHeight - window.innerHeight, 0);
        const top = window.scrollY + controller.getBoundingClientRect().top + available * (index / BEATS.length + .025);
        window.scrollTo({ top, behavior: 'smooth' });
    }

    function updateStoryFromScroll() {
        scrollFrame = 0;
        if (!controller || reduceMotionQuery.matches || mediaFrozen || window.innerWidth <= 760) return;
        const rect = controller.getBoundingClientRect();
        const available = Math.max(controller.offsetHeight - window.innerHeight, 1);
        const progress = Math.min(1, Math.max(0, -rect.top / available));
        const index = Math.min(BEATS.length - 1, Math.floor(progress * BEATS.length));
        const beat = BEATS[index];
        if (beat !== currentBeat) setStoryBeat(beat, { restart: true });
    }

    function queueScrollUpdate() {
        if (!scrollFrame) scrollFrame = window.requestAnimationFrame(updateStoryFromScroll);
    }

    control?.addEventListener('click', () => setBuddyState(STATES[(stateIndex + 1) % STATES.length]));
    control?.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            setBuddyState(STATES[(stateIndex + 1) % STATES.length]);
        }
    });
    images.forEach(image => image.addEventListener('error', () => {
        buddyFailed = true;
        stage?.classList.add('is-paused');
        if (status) status.textContent = 'Buddy is available as a still image.';
    }));

    videos.forEach(video => {
        video.addEventListener('ended', () => {
            video.classList.remove('is-playing');
            video.classList.add('has-played');
            if (video.dataset.storyVideo === 'ship' && currentBeat === 'ship') setBuddyState('success', false);
        });
        video.addEventListener('error', () => {
            video.classList.remove('is-playing', 'has-played');
            if (video.dataset.storyVideo === currentBeat) setBuddyState('error', false);
        });
    });

    triggers.forEach((trigger, index) => {
        trigger.addEventListener('click', () => goToBeat(trigger.dataset.storyTrigger));
        trigger.addEventListener('keydown', event => {
            const delta = event.key === 'ArrowRight' || event.key === 'ArrowDown' ? 1
                : event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 0;
            if (!delta) return;
            event.preventDefault();
            const next = triggers[(index + delta + triggers.length) % triggers.length];
            goToBeat(next.dataset.storyTrigger, true);
        });
    });

    if ('IntersectionObserver' in window && controller) {
        new IntersectionObserver(entries => {
            storyVisible = Boolean(entries[0]?.isIntersecting);
            if (storyVisible) {
                resumeBuddy();
                playStoryClip(currentBeat);
            } else {
                pauseAll();
            }
        }, { threshold: .01 }).observe(controller);
    }

    document.addEventListener('visibilitychange', () => {
        if (document.hidden) pauseAll();
        else if (storyVisible) {
            resumeBuddy();
            playStoryClip(currentBeat);
        }
    });
    reduceMotionQuery.addEventListener?.('change', () => {
        if (reduceMotionQuery.matches) pauseAll();
        else if (storyVisible) {
            resumeBuddy();
            playStoryClip(currentBeat);
        }
        queueScrollUpdate();
    });
    window.addEventListener('scroll', queueScrollUpdate, { passive: true });
    window.addEventListener('resize', queueScrollUpdate);

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
        context.setTransform(ratio, 0, 0, ratio, 0, 0);
        let seed = 1977;
        const random = () => ((seed = (seed * 48271) % 2147483647) / 2147483647);
        const count = Math.min(52, Math.max(20, Math.floor(width / 28)));
        const points = Array.from({ length: count }, () => ({ x: random() * width, y: random() * height }));
        context.clearRect(0, 0, width, height);
        context.lineWidth = .7;
        for (let i = 0; i < points.length; i += 1) {
            for (let j = i + 1; j < points.length; j += 1) {
                const distance = Math.hypot(points[i].x - points[j].x, points[i].y - points[j].y);
                if (distance > 165) continue;
                context.strokeStyle = `rgba(71,217,255,${(1 - distance / 165) * .17})`;
                context.beginPath();
                context.moveTo(points[i].x, points[i].y);
                context.lineTo(points[j].x, points[j].y);
                context.stroke();
            }
            context.fillStyle = i % 11 === 0 ? 'rgba(213,173,114,.85)' : 'rgba(71,217,255,.62)';
            context.beginPath();
            context.arc(points[i].x, points[i].y, i % 11 === 0 ? 2.4 : 1.4, 0, Math.PI * 2);
            context.fill();
        }
    }

    let resizeFrame = 0;
    window.addEventListener('resize', () => {
        window.cancelAnimationFrame(resizeFrame);
        resizeFrame = window.requestAnimationFrame(drawSovereigntyField);
    });

    const sceneAliases = {
        'research-local': 'research',
        'knowledge-control': 'research',
        'synthesis-sol': 'create',
        'designer-output': 'create',
        'workflow-repeat': 'automate',
        'approval-boundary': 'ship'
    };
    const params = new URLSearchParams(window.location.search);
    const requestedScene = sceneAliases[params.get('landing_scene')] || params.get('landing_scene');
    if (params.get('motion') === 'freeze') mediaFrozen = true;
    setStoryBeat(BEATS.includes(requestedScene) ? requestedScene : 'research');
    if (mediaFrozen) pauseAll();
    drawSovereigntyField();
    queueScrollUpdate();

    window.RowBotLandingStory = {
        setScene(scene) { setStoryBeat(sceneAliases[scene] || scene, { restart: true }); },
        setBuddyState,
        freezeMotion() { mediaFrozen = true; pauseAll(); },
        getState() { return { beat: currentBeat, buddy: STATES[stateIndex], motionAllowed: buddyMotionAllowed() }; }
    };
})();
