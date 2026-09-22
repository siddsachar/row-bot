(() => {
    'use strict';

    const STATES = ['idle', 'thinking', 'working', 'approval', 'success', 'error'];
    const BEATS = ['research', 'create', 'automate', 'ship'];
    const BEAT_STATES = { research: 'thinking', create: 'working', automate: 'idle', ship: 'error' };
    const BEAT_LABELS = {
        research: 'Buddy is researching.',
        create: 'Buddy is building.',
        automate: 'Buddy is running the workflow.',
        ship: 'Buddy is holding at the approval boundary.'
    };
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
    const buddyVideos = [...document.querySelectorAll('[data-buddy-motion]')];
    const triggers = [...document.querySelectorAll('[data-story-trigger]')];
    const panels = [...document.querySelectorAll('[data-story-panel]')];
    const copies = [...document.querySelectorAll('[data-story-copy]')];
    const videos = [...document.querySelectorAll('[data-story-video]')];
    const sovereigntyBuddy = document.querySelector('[data-sovereignty-buddy]');
    const sovereigntyBuddyVideo = document.querySelector('[data-sovereignty-buddy-video]');
    const appStack = document.querySelector('.app-stack');
    const productStage = document.querySelector('.product-stage');

    let stateIndex = STATES.indexOf('thinking');
    let activeImage = 0;
    let currentBeat = 'research';
    let storyVisible = true;
    let mediaFrozen = false;
    let buddyFailed = false;
    let crossfadePending = false;
    let queuedState = null;
    let scrollFrame = 0;
    let autoAdvanceTimer = 0;
    let introTimer = 0;
    let sceneTransitionTimer = 0;
    let buddyTransitionTimer = 0;
    let buddyImageSwapTimer = 0;
    let buddyRevealToken = 0;
    let buddyTransitionToken = 0;
    let buddyPendingState = null;
    const panelHideTimers = new Map();
    let introDismissed = !controller?.classList.contains('is-intro');
    let introHolding = !introDismissed;

    function playbackAllowed() {
        return !reduceMotionQuery.matches && !saveData && !document.hidden && !mediaFrozen && !introHolding;
    }

    function buddyMotionAllowed() {
        return playbackAllowed() && storyVisible && !buddyFailed && !stage?.classList.contains('is-repositioning');
    }

    function pauseVideos(except = null) {
        videos.forEach(video => {
            if (video !== except) {
                video.pause();
                video.classList.remove('is-playing');
            }
        });
    }

    function pauseBuddyVideos() {
        buddyRevealToken += 1;
        buddyPendingState = null;
        buddyVideos.forEach(video => {
            video.pause();
            video.classList.remove('is-active');
            video.hidden = true;
        });
        stage?.classList.remove('is-video-ready');
    }

    function hydrateVideo(video) {
        if (!video || video.dataset.hydrated === 'true') return Boolean(video);
        if (!video.getAttribute('poster') && video.dataset.poster) video.poster = video.dataset.poster;
        let hasSource = Boolean(video.getAttribute('src'));
        video.querySelectorAll('source').forEach(source => {
            if (!source.getAttribute('src') && source.dataset.src) source.src = source.dataset.src;
            if (source.getAttribute('src')) hasSource = true;
        });
        if (!hasSource) return false;
        video.dataset.hydrated = 'true';
        video.load();
        return true;
    }

    function pauseAll() {
        window.clearTimeout(autoAdvanceTimer);
        autoAdvanceTimer = 0;
        window.clearTimeout(buddyTransitionTimer);
        buddyTransitionTimer = 0;
        buddyTransitionToken += 1;
        stage?.classList.remove('is-repositioning');
        stage?.classList.add('is-paused');
        pauseVideos();
        pauseBuddyVideos();
    }

    function dismissIntro() {
        if (introDismissed) return;
        introDismissed = true;
        introHolding = false;
        window.clearTimeout(introTimer);
        controller?.classList.remove('is-intro');
        if (storyVisible && playbackAllowed()) {
            playStoryClip(currentBeat, true);
            playBuddyState(STATES[stateIndex]);
        }
    }

    function resumeBuddy(restart = false) {
        stage?.classList.toggle('is-paused', !buddyMotionAllowed());
        if (buddyMotionAllowed()) playBuddyState(STATES[stateIndex], restart);
        else pauseBuddyVideos();
    }

    function playBuddyState(state, restart = true) {
        const target = buddyVideos.find(video => video.dataset.buddyMotion === state);
        if (!target || !buddyMotionAllowed()) return;
        if (!restart && buddyPendingState === state) return;
        if (!restart && target.classList.contains('is-active') && !target.paused && !target.ended) return;
        if (!target.getAttribute('src') && target.dataset.src) {
            target.src = target.dataset.src;
            target.load();
        }
        buddyVideos.forEach(video => {
            if (video !== target) {
                video.pause();
                video.classList.remove('is-active');
                video.hidden = true;
            }
        });
        target.hidden = false;
        if (restart) {
            try { target.currentTime = 0; } catch (_) { /* Metadata may not be ready yet. */ }
        }
        const revealToken = ++buddyRevealToken;
        buddyPendingState = state;
        target.play().then(() => {
            const reveal = () => {
                window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
                    if (revealToken !== buddyRevealToken || target.dataset.buddyMotion !== STATES[stateIndex]
                        || !buddyMotionAllowed()) return;
                    buddyPendingState = null;
                    target.classList.add('is-active');
                    stage?.classList.add('is-video-ready');
                }));
            };
            if ('requestVideoFrameCallback' in target) target.requestVideoFrameCallback(reveal);
            else window.requestAnimationFrame(reveal);
        }).catch(() => {
            if (revealToken === buddyRevealToken) buddyPendingState = null;
            if (target.dataset.buddyMotion === STATES[stateIndex]) stage?.classList.remove('is-video-ready');
        });
    }

    function revealMotionVideo(video, host) {
        const reveal = () => host?.classList.add('is-video-ready');
        if ('requestVideoFrameCallback' in video) video.requestVideoFrameCallback(reveal);
        else window.requestAnimationFrame(reveal);
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
            const commitSwap = () => {
                buddyImageSwapTimer = 0;
                incoming.classList.add('is-active');
                outgoing.classList.remove('is-active');
                activeImage = incomingIndex;
                crossfadePending = false;
                if (queuedState) {
                    const queued = queuedState;
                    queuedState = null;
                    crossfadeBuddy(queued);
                }
            };
            window.clearTimeout(buddyImageSwapTimer);
            const swapDelay = stage?.classList.contains('is-repositioning') && !reduceMotionQuery.matches ? 1060 : 0;
            if (swapDelay) buddyImageSwapTimer = window.setTimeout(commitSwap, swapDelay);
            else commitSwap();
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
            stage.classList.toggle('is-paused', !buddyMotionAllowed());
            if (buddyMotionAllowed()) playBuddyState(nextState);
            else pauseBuddyVideos();
            return;
        }
        crossfadeBuddy(nextState);
        stage.classList.toggle('is-paused', !buddyMotionAllowed());
        if (buddyMotionAllowed()) playBuddyState(nextState);
        else pauseBuddyVideos();
    }

    function playStoryClip(beat, restart = false) {
        const clip = document.querySelector(`[data-story-video="${beat}"]`);
        if (!clip || !storyVisible || !playbackAllowed()) return;
        if (!hydrateVideo(clip)) return;
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
        window.clearTimeout(autoAdvanceTimer);
        autoAdvanceTimer = 0;
        const changed = currentBeat !== beat;
        currentBeat = beat;
        if (changed && stage && !reduceMotionQuery.matches) {
            window.clearTimeout(buddyTransitionTimer);
            const transitionToken = ++buddyTransitionToken;
            stage.classList.add('is-repositioning');
            pauseBuddyVideos();
            void stage.offsetWidth;
            window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
                if (transitionToken !== buddyTransitionToken) return;
                if (controller) controller.dataset.scene = beat;
                buddyTransitionTimer = window.setTimeout(() => {
                    buddyTransitionTimer = 0;
                    if (transitionToken !== buddyTransitionToken) return;
                    stage.classList.remove('is-repositioning');
                    void stage.offsetWidth;
                    window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
                        if (transitionToken === buddyTransitionToken) resumeBuddy(true);
                    }));
                }, 980);
            }));
        } else if (controller) {
            controller.dataset.scene = beat;
        }
        triggers.forEach(trigger => {
            const selected = trigger.dataset.storyTrigger === beat;
            if (selected) trigger.setAttribute('aria-current', 'step');
            else trigger.removeAttribute('aria-current');
            if (selected && focus) trigger.focus();
        });
        panels.forEach(panel => {
            const selected = panel.dataset.storyPanel === beat;
            const pendingHide = panelHideTimers.get(panel);
            if (pendingHide) {
                window.clearTimeout(pendingHide);
                panelHideTimers.delete(panel);
            }
            if (selected) {
                panel.hidden = false;
                panel.classList.remove('is-leaving');
                window.requestAnimationFrame(() => {
                    if (currentBeat === beat && panel.dataset.storyPanel === beat) panel.classList.add('is-active');
                });
            } else if (!panel.hidden) {
                panel.classList.remove('is-active');
                panel.classList.add('is-leaving');
                const hideTimer = window.setTimeout(() => {
                    if (panel.dataset.storyPanel !== currentBeat) panel.hidden = true;
                    panel.classList.remove('is-leaving');
                    panelHideTimers.delete(panel);
                }, reduceMotionQuery.matches ? 0 : 900);
                panelHideTimers.set(panel, hideTimer);
            }
        });
        copies.forEach(copy => {
            const selected = copy.dataset.storyCopy === beat;
            copy.hidden = !selected;
            copy.classList.toggle('is-active', selected);
        });
        setBuddyState(BEAT_STATES[beat], false);
        if (status) status.textContent = BEAT_LABELS[beat];
        if (changed && productStage && !reduceMotionQuery.matches) {
            window.clearTimeout(sceneTransitionTimer);
            productStage.classList.remove('is-scene-transitioning');
            void productStage.offsetWidth;
            productStage.classList.add('is-scene-transitioning');
            sceneTransitionTimer = window.setTimeout(() => productStage.classList.remove('is-scene-transitioning'), 900);
        }
        playStoryClip(beat, restart || changed);
    }

    function goToBeat(beat, focus = false) {
        dismissIntro();
        setStoryBeat(beat, { focus, restart: true });
    }

    function updateStoryFromScroll() {
        scrollFrame = 0;
        if (!controller || reduceMotionQuery.matches || mediaFrozen) return;
        const rect = controller.getBoundingClientRect();
        if (rect.top < -4) dismissIntro();
    }

    function queueScrollUpdate() {
        if (!scrollFrame) scrollFrame = window.requestAnimationFrame(updateStoryFromScroll);
    }

    function scheduleNextBeat(finishedBeat) {
        window.clearTimeout(autoAdvanceTimer);
        const delay = finishedBeat === 'ship' ? 2600 : 680;
        autoAdvanceTimer = window.setTimeout(() => {
            autoAdvanceTimer = 0;
            if (currentBeat !== finishedBeat || !storyVisible || !playbackAllowed()) return;
            dismissIntro();
            const nextBeat = BEATS[(BEATS.indexOf(finishedBeat) + 1) % BEATS.length];
            setStoryBeat(nextBeat, { restart: true });
        }, delay);
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
    buddyVideos.forEach(video => video.addEventListener('error', () => {
        video.classList.remove('is-active');
        if (video.dataset.buddyMotion === STATES[stateIndex]) stage?.classList.remove('is-video-ready');
        video.removeAttribute('src');
        video.dataset.hydrated = 'false';
    }));

    videos.forEach(video => {
        video.addEventListener('ended', () => {
            video.classList.remove('is-playing');
            video.classList.add('has-played');
            if (video.dataset.storyVideo === 'ship' && currentBeat === 'ship') setBuddyState('success', false);
            if (video.dataset.storyVideo === currentBeat) scheduleNextBeat(currentBeat);
        });
        video.addEventListener('canplay', () => {
            if (video.dataset.storyVideo === currentBeat && video.paused) playStoryClip(currentBeat);
        });
        video.addEventListener('error', () => {
            video.classList.remove('is-playing', 'has-played');
            if (video.dataset.storyVideo === currentBeat) setBuddyState('error', false);
        });
    });

    function playSovereigntyBuddy() {
        if (!sovereigntyBuddyVideo || reduceMotionQuery.matches || saveData || document.hidden) return;
        if (!hydrateVideo(sovereigntyBuddyVideo)) return;
        sovereigntyBuddyVideo.hidden = false;
        sovereigntyBuddyVideo.play().catch(() => {
            sovereigntyBuddyVideo.hidden = true;
            sovereigntyBuddy?.classList.remove('is-video-ready');
        });
    }

    sovereigntyBuddyVideo?.addEventListener('playing', () => revealMotionVideo(sovereigntyBuddyVideo, sovereigntyBuddy));
    sovereigntyBuddyVideo?.addEventListener('error', () => {
        sovereigntyBuddyVideo.hidden = true;
        sovereigntyBuddy?.classList.remove('is-video-ready');
    });
    if ('IntersectionObserver' in window && sovereigntyBuddy) {
        new IntersectionObserver(entries => {
            if (entries[0]?.isIntersecting) playSovereigntyBuddy();
            else sovereigntyBuddyVideo?.pause();
        }, { rootMargin: '280px 0px', threshold: .01 }).observe(sovereigntyBuddy);
    }

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

    if (appStack) {
        const resetStageTilt = () => {
            appStack.style.setProperty('--stage-tilt-x', '0deg');
            appStack.style.setProperty('--stage-tilt-y', '0deg');
        };
        appStack.addEventListener('pointermove', event => {
            if (reduceMotionQuery.matches || event.pointerType === 'touch') return;
            const rect = appStack.getBoundingClientRect();
            const horizontal = ((event.clientX - rect.left) / rect.width - .5) * 2;
            const vertical = ((event.clientY - rect.top) / rect.height - .5) * 2;
            appStack.style.setProperty('--stage-tilt-x', `${(-vertical * 1.35).toFixed(2)}deg`);
            appStack.style.setProperty('--stage-tilt-y', `${(horizontal * 2.1).toFixed(2)}deg`);
        });
        appStack.addEventListener('pointerleave', resetStageTilt);
        reduceMotionQuery.addEventListener?.('change', resetStageTilt);
    }

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
        if (document.hidden) {
            pauseAll();
            sovereigntyBuddyVideo?.pause();
        }
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
    if (mediaFrozen || reduceMotionQuery.matches || requestedScene) dismissIntro();
    else introTimer = window.setTimeout(dismissIntro, 3200);
    if (mediaFrozen) pauseAll();
    drawSovereigntyField();
    queueScrollUpdate();

    window.RowBotLandingStory = {
        setScene(scene) { dismissIntro(); setStoryBeat(sceneAliases[scene] || scene, { restart: true }); },
        setBuddyState,
        freezeMotion() { mediaFrozen = true; pauseAll(); },
        getState() { return { beat: currentBeat, buddy: STATES[stateIndex], motionAllowed: buddyMotionAllowed() }; }
    };
})();
