(() => {
    'use strict';

    const STATES = ['idle', 'thinking', 'working', 'approval', 'success', 'error'];
    const BEATS = ['research', 'create', 'automate', 'ship'];
    const BEAT_STATES = { research: 'thinking', create: 'working', automate: 'idle', ship: 'approval' };
    const BUDDY_REVEAL_AT = { idle: .16, thinking: .2, working: .2 };
    const IDLE_HOLD_AT = 4.4;
    // The reviewed Ship cut clears its approval dialog at 2.25 seconds.
    const SHIP_APPROVAL_AT = 2.25;
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
    const demoPosters = [...document.querySelectorAll('[data-demo-poster]')];
    if ('IntersectionObserver' in window) {
        const posterObserver = new IntersectionObserver(entries => {
            entries.forEach(entry => {
                if (!entry.isIntersecting) return;
                entry.target.src = entry.target.dataset.src;
                posterObserver.unobserve(entry.target);
            });
        }, { rootMargin: '80px 0px' });
        demoPosters.forEach(image => posterObserver.observe(image));
    } else demoPosters.forEach(image => { image.src = image.dataset.src; });
    const sovereigntyBuddy = document.querySelector('[data-sovereignty-buddy]');
    const sovereigntyBuddyVideo = document.querySelector('[data-sovereignty-buddy-video]');
    const appStack = document.querySelector('.app-stack');
    const productStage = document.querySelector('.product-stage');
    const journeyTitle = document.querySelector('#journey-title');
    const siteNav = document.querySelector('.site-nav');

    let stateIndex = STATES.indexOf('working');
    let currentBeat = 'research';
    let storyVisible = true;
    let mediaFrozen = false;
    let scrollFrame = 0;
    let autoAdvanceTimer = 0;
    let introTimer = 0;
    let autoCenterTimer = 0;
    let buddyTransitionTimer = 0;
    let buddyRevealToken = 0;
    let buddyTransitionToken = 0;
    let buddyTransitionPending = false;
    let clipPlayToken = 0;
    let buddyPlayPending = null;
    let sovereigntyVisible = false;
    let pendingBuddyPoster = null;
    let introDismissed = !controller?.classList.contains('is-intro');
    let introHolding = !introDismissed;
    let introUserInteracted = false;

    function playbackAllowed() {
        return !reduceMotionQuery.matches && !saveData && !document.hidden && !mediaFrozen && !introHolding;
    }

    function buddyMotionAllowed() {
        return !reduceMotionQuery.matches && !saveData && !document.hidden && !mediaFrozen
            && storyVisible && !stage?.classList.contains('is-repositioning');
    }

    function pauseVideos(except = null) {
        clipPlayToken += 1;
        videos.forEach(video => {
            if (video !== except) {
                video.pause();
                video.classList.remove('is-playing', 'has-played');
            }
        });
    }

    function pauseBuddyVideos() {
        buddyRevealToken += 1;
        buddyPlayPending = null;
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
        buddyTransitionPending = false;
        stage?.classList.remove('is-repositioning');
        applyPendingBuddyPoster(STATES[stateIndex]);
        stage?.classList.add('is-paused');
        pauseVideos();
        pauseBuddyVideos();
    }

    function dismissIntro(startScene = true, collapse = true) {
        window.clearTimeout(autoCenterTimer);
        autoCenterTimer = 0;
        if (collapse) {
            controller?.classList.remove('is-intro', 'is-story-active');
            applyPendingBuddyPoster(STATES[stateIndex]);
        } else controller?.classList.add('is-story-active');
        if (introDismissed) return;
        introDismissed = true;
        introHolding = false;
        window.clearTimeout(introTimer);
        if (startScene && storyVisible && playbackAllowed()) {
            playStoryClip(currentBeat, true);
        }
        if (startScene) setBuddyState(BEAT_STATES[currentBeat], false, true);
    }

    function startIntroStory() {
        if (introDismissed) return;
        const shouldCenter = !introUserInteracted && window.scrollY < 4 && storyVisible && !document.hidden;
        dismissIntro(true, true);
        if (!shouldCenter || !productStage) return;
        // Measure after the headline's 800 ms transition, then move the viewport
        // once. Scrolling during that layout transition would chase a moving target.
        autoCenterTimer = window.setTimeout(() => {
            autoCenterTimer = 0;
            if (introUserInteracted || !storyVisible || document.hidden || window.scrollY >= 4) return;
            const rect = productStage.getBoundingClientRect();
            const centered = window.scrollY + rect.top + rect.height / 2 - window.innerHeight / 2;
            const bottomFit = window.scrollY + rect.bottom - (window.innerHeight - 32);
            const navHeight = siteNav?.getBoundingClientRect().height || 66;
            const titleTop = journeyTitle?.getBoundingClientRect().top ?? rect.top;
            const titleLimit = window.scrollY + titleTop - navHeight - 14;
            const framed = Math.max(window.innerWidth <= 760 ? 40 : 32, bottomFit);
            const top = Math.max(0, framed <= titleLimit + 4 ? framed : centered);
            window.scrollTo({ top, behavior: 'smooth' });
        }, 850);
    }

    function resumeBuddy(restart = false) {
        stage?.classList.toggle('is-paused', !buddyMotionAllowed());
        if (buddyMotionAllowed()) playBuddyState(STATES[stateIndex], restart);
        else pauseBuddyVideos();
    }

    function playBuddyState(state, restart = true) {
        const target = buddyVideos.find(video => video.dataset.buddyMotion === state);
        if (!target || !buddyMotionAllowed()) return;
        if (!restart && state === 'idle' && target.classList.contains('is-active') && target.currentTime >= IDLE_HOLD_AT) return;
        if (!restart && ((target.classList.contains('is-active') && !target.paused) || buddyPlayPending === state)) return;
        if (!target.getAttribute('src') && target.dataset.src) {
            target.src = target.dataset.src;
            target.load();
        }
        pauseBuddyVideos();
        const token = buddyRevealToken;
        buddyPlayPending = state;
        target.hidden = false;
        const revealAt = BUDDY_REVEAL_AT[state] || 0;
        const playReadyFrame = () => {
            if (token !== buddyRevealToken || !buddyMotionAllowed()) return;
            target.play().then(() => {
                const reveal = () => window.requestAnimationFrame(() => {
                    if (token !== buddyRevealToken || state !== STATES[stateIndex] || !buddyMotionAllowed()) return;
                    buddyPlayPending = null;
                    target.classList.add('is-active');
                    stage?.classList.add('is-video-ready');
                    applyPendingBuddyPoster(state);
                });
                // Keep the reviewed still up until clean decoded frames arrive.
                if ('requestVideoFrameCallback' in target) {
                    const waitForFrame = () => target.requestVideoFrameCallback((_, frame) => {
                        if (token !== buddyRevealToken) return;
                        if (frame && frame.mediaTime < revealAt - .04) waitForFrame();
                        else target.requestVideoFrameCallback(reveal);
                    });
                    waitForFrame();
                }
                else window.requestAnimationFrame(() => window.requestAnimationFrame(reveal));
            }).catch(() => {
                if (token === buddyRevealToken) {
                    buddyPlayPending = null;
                    target.hidden = true;
                    stage?.classList.remove('is-video-ready');
                    applyPendingBuddyPoster(state);
                }
            });
        };
        if (restart) {
            try { target.currentTime = 0; } catch (_) { /* Metadata may not be ready. */ }
        }
        playReadyFrame();
    }

    function revealMotionVideo(video, host) {
        const reveal = () => host?.classList.add('is-video-ready');
        if ('requestVideoFrameCallback' in video) video.requestVideoFrameCallback(reveal);
        else window.requestAnimationFrame(reveal);
    }

    function applyPendingBuddyPoster(state) {
        if (!pendingBuddyPoster || pendingBuddyPoster.state !== state) return;
        if (images[0]) images[0].src = pendingBuddyPoster.src;
        pendingBuddyPoster = null;
    }

    function loadBuddyPoster(state, token) {
        const image = images[0];
        if (!image || image.src.endsWith(SOURCES[state])) return Promise.resolve(true);
        const candidate = new Image();
        let ready;
        if (candidate.decode) {
            candidate.src = SOURCES[state];
            ready = candidate.decode();
        } else {
            ready = new Promise((resolve, reject) => {
                candidate.onload = resolve;
                candidate.onerror = reject;
                candidate.src = SOURCES[state];
                if (candidate.complete) {
                    if (candidate.naturalWidth) resolve();
                    else reject(new Error('Buddy poster unavailable'));
                }
            });
        }
        return ready.then(() => {
            if (token !== buddyTransitionToken) return false;
            if (controller?.classList.contains('is-intro') && !reduceMotionQuery.matches
                && !saveData && !document.hidden && !mediaFrozen && storyVisible) {
                pendingBuddyPoster = { state, src: candidate.src };
            } else image.src = candidate.src;
            return true;
        }).catch(() => {
            if (token === buddyTransitionToken && status) status.textContent = 'Buddy is available as a still image.';
            return false;
        });
    }

    function setBuddyState(nextState, announce = true, travel = false) {
        const nextIndex = STATES.indexOf(nextState);
        if (nextIndex < 0 || !stage) return;
        const token = ++buddyTransitionToken;
        buddyTransitionPending = true;
        pendingBuddyPoster = null;
        window.clearTimeout(buddyTransitionTimer);
        buddyTransitionTimer = 0;
        stateIndex = nextIndex;
        stage.dataset.state = nextState;
        if (status) {
            status.setAttribute('aria-live', announce ? 'polite' : 'off');
            status.textContent = LABELS[nextState];
        }
        pauseBuddyVideos();
        const moving = travel && !reduceMotionQuery.matches && !mediaFrozen;
        stage.classList.toggle('is-repositioning', moving);
        const posterReady = loadBuddyPoster(nextState, token);
        const finish = () => posterReady.then(() => {
            if (token !== buddyTransitionToken) return;
            buddyTransitionTimer = 0;
            buddyTransitionPending = false;
            stage.classList.remove('is-repositioning');
            resumeBuddy(true);
        });
        if (moving) buddyTransitionTimer = window.setTimeout(finish, 680);
        else finish();
    }

    function playStoryClip(beat, restart = false) {
        const clip = videos.find(video => video.dataset.storyVideo === beat);
        if (!clip || !storyVisible || !playbackAllowed()) return;
        pauseVideos(clip);
        const token = clipPlayToken;
        clip.classList.remove('is-playing', 'has-played');
        if (!hydrateVideo(clip)) {
            scheduleNextBeat(beat, true);
            return;
        }
        if (restart) {
            try { clip.currentTime = 0; } catch (_) { /* Metadata may not be ready. */ }
        }
        clip.play().then(() => {
            const reveal = () => window.requestAnimationFrame(() => {
                if (token !== clipPlayToken || beat !== currentBeat || !playbackAllowed()) return;
                clip.classList.add('is-playing');
            });
            if ('requestVideoFrameCallback' in clip) {
                clip.requestVideoFrameCallback(() => clip.requestVideoFrameCallback(reveal));
            }
            else window.requestAnimationFrame(() => window.requestAnimationFrame(reveal));
        }).catch(() => {
            if (token !== clipPlayToken || beat !== currentBeat) return;
            clip.classList.remove('is-playing');
            scheduleNextBeat(beat, true);
        });
    }

    function setStoryBeat(beat, { focus = false, restart = false } = {}) {
        if (!BEAT_STATES[beat]) return;
        window.clearTimeout(autoAdvanceTimer);
        autoAdvanceTimer = 0;
        const changed = currentBeat !== beat;
        currentBeat = beat;
        if (controller) controller.dataset.scene = beat;
        triggers.forEach(trigger => {
            const selected = trigger.dataset.storyTrigger === beat;
            if (selected) trigger.setAttribute('aria-current', 'step');
            else trigger.removeAttribute('aria-current');
            if (selected && focus) trigger.focus();
        });
        // Only one app surface is ever visible; the poster gates the new video frame.
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
        if (introHolding) return;
        if (changed || restart || STATES[stateIndex] !== BEAT_STATES[beat]) {
            setBuddyState(BEAT_STATES[beat], false, changed);
        }
        if (status) status.textContent = BEAT_LABELS[beat];
        if (changed || restart) playStoryClip(beat, true);
        else if (videos.find(video => video.dataset.storyVideo === beat)?.paused) playStoryClip(beat);
    }

    function goToBeat(beat, focus = false) {
        dismissIntro(false);
        setStoryBeat(beat, { focus, restart: true });
    }

    function updateStoryFromScroll() {
        scrollFrame = 0;
        if (!controller || reduceMotionQuery.matches || mediaFrozen) return;
        const rect = controller.getBoundingClientRect();
        if (rect.top < -4) dismissIntro(true, true);
    }

    function queueScrollUpdate() {
        if (!scrollFrame) scrollFrame = window.requestAnimationFrame(updateStoryFromScroll);
    }

    function scheduleNextBeat(finishedBeat, mediaFailed = false) {
        window.clearTimeout(autoAdvanceTimer);
        const delay = mediaFailed ? 6000 : finishedBeat === 'ship' ? 3100 : 450;
        autoAdvanceTimer = window.setTimeout(() => {
            autoAdvanceTimer = 0;
            if (currentBeat !== finishedBeat || !storyVisible || !playbackAllowed()) return;
            const nextBeat = BEATS[(BEATS.indexOf(finishedBeat) + 1) % BEATS.length];
            setStoryBeat(nextBeat, { restart: true });
        }, delay);
    }

    control?.addEventListener('click', () => goToBeat(BEATS[(BEATS.indexOf(currentBeat) + 1) % BEATS.length]));
    control?.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            goToBeat(BEATS[(BEATS.indexOf(currentBeat) + 1) % BEATS.length]);
        }
    });
    images.forEach(image => image.addEventListener('error', () => {
        pauseBuddyVideos();
        if (status) status.textContent = 'Buddy motion is unavailable.';
    }));
    buddyVideos.forEach(video => video.addEventListener('error', () => {
        video.classList.remove('is-active');
        if (video.dataset.buddyMotion === STATES[stateIndex]) {
            buddyPlayPending = null;
            stage?.classList.remove('is-video-ready');
            applyPendingBuddyPoster(STATES[stateIndex]);
        }
        video.removeAttribute('src');
    }));
    buddyVideos.find(video => video.dataset.buddyMotion === 'idle')?.addEventListener('timeupdate', event => {
        const video = event.currentTarget;
        if (video.currentTime >= IDLE_HOLD_AT && STATES[stateIndex] === 'idle') video.pause();
    });
    buddyVideos.find(video => video.dataset.buddyMotion === 'success')?.addEventListener('ended', () => {
        if (currentBeat === 'ship' && STATES[stateIndex] === 'success') setBuddyState('idle', false);
    });

    videos.forEach(video => {
        video.addEventListener('timeupdate', () => {
            if (video.dataset.storyVideo === 'ship' && currentBeat === 'ship'
                && video.currentTime >= SHIP_APPROVAL_AT && STATES[stateIndex] === 'approval') {
                setBuddyState('success', false);
            }
        });
        video.addEventListener('ended', () => {
            video.classList.remove('is-playing');
            video.classList.add('has-played');
            if (video.dataset.storyVideo === 'ship' && currentBeat === 'ship'
                && STATES[stateIndex] === 'approval') setBuddyState('success', false);
            if (video.dataset.storyVideo === currentBeat) scheduleNextBeat(currentBeat);
        });
        video.addEventListener('error', () => {
            video.classList.remove('is-playing');
            if (video.dataset.storyVideo === currentBeat) scheduleNextBeat(currentBeat, true);
        });
    });

    function playSovereigntyBuddy() {
        if (!sovereigntyBuddyVideo || !sovereigntyVisible || storyVisible || reduceMotionQuery.matches || saveData || document.hidden || mediaFrozen) return;
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
            sovereigntyVisible = Boolean(entries[0]?.isIntersecting);
            if (sovereigntyVisible) playSovereigntyBuddy();
            else sovereigntyBuddyVideo?.pause();
        }, { threshold: .01 }).observe(sovereigntyBuddy);
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
            appStack.style.setProperty('--stage-parallax-x', '0px');
            appStack.style.setProperty('--stage-parallax-y', '0px');
            appStack.style.setProperty('--stage-tilt-x', '0deg');
            appStack.style.setProperty('--stage-tilt-y', '0deg');
        };
        appStack.addEventListener('pointermove', event => {
            if (reduceMotionQuery.matches || event.pointerType === 'touch') return;
            const rect = appStack.getBoundingClientRect();
            const horizontal = ((event.clientX - rect.left) / rect.width - .5) * 2;
            const vertical = ((event.clientY - rect.top) / rect.height - .5) * 2;
            appStack.style.setProperty('--stage-parallax-x', `${Math.round(horizontal * 3)}px`);
            appStack.style.setProperty('--stage-parallax-y', `${Math.round(vertical * 2)}px`);
            appStack.style.setProperty('--stage-tilt-x', `${(-vertical * .8).toFixed(2)}deg`);
            appStack.style.setProperty('--stage-tilt-y', `${(horizontal * 1.1).toFixed(2)}deg`);
        });
        appStack.addEventListener('pointerleave', resetStageTilt);
        reduceMotionQuery.addEventListener?.('change', resetStageTilt);
    }

    if ('IntersectionObserver' in window && controller) {
        new IntersectionObserver(entries => {
            storyVisible = Boolean(entries[0]?.isIntersecting);
            if (storyVisible) {
                sovereigntyBuddyVideo?.pause();
                sovereigntyBuddy?.classList.remove('is-video-ready');
                if (buddyTransitionPending) { /* The current transition owns the poster and motion. */ }
                else if (!images[0]?.src.endsWith(SOURCES[STATES[stateIndex]])) setBuddyState(STATES[stateIndex], false);
                else resumeBuddy();
                playStoryClip(currentBeat);
            } else {
                pauseAll();
                playSovereigntyBuddy();
            }
        }, { threshold: .01 }).observe(controller);
    }

    document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
            pauseAll();
            sovereigntyBuddyVideo?.pause();
        }
        else if (storyVisible) {
            setBuddyState(STATES[stateIndex], false);
            playStoryClip(currentBeat);
        } else playSovereigntyBuddy();
    });
    reduceMotionQuery.addEventListener?.('change', () => {
        if (reduceMotionQuery.matches) {
            pauseAll();
            sovereigntyBuddyVideo?.pause();
            sovereigntyBuddy?.classList.remove('is-video-ready');
        }
        else if (storyVisible) {
            setBuddyState(STATES[stateIndex], false);
            playStoryClip(currentBeat);
        }
        queueScrollUpdate();
    });
    window.addEventListener('scroll', queueScrollUpdate, { passive: true });
    window.addEventListener('resize', queueScrollUpdate);
    ['wheel', 'touchstart', 'pointerdown', 'keydown'].forEach(type => {
        window.addEventListener(type, () => { introUserInteracted = true; }, { passive: true });
    });

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
    else introTimer = window.setTimeout(startIntroStory, 3200);
    if (mediaFrozen) pauseAll();
    drawSovereigntyField();
    queueScrollUpdate();

    window.RowBotLandingStory = {
        setScene(scene) { dismissIntro(false); setStoryBeat(sceneAliases[scene] || scene, { restart: true }); },
        setBuddyState,
        freezeMotion() { mediaFrozen = true; pauseAll(); sovereigntyBuddyVideo?.pause(); },
        getState() { return { beat: currentBeat, buddy: STATES[stateIndex], motionAllowed: buddyMotionAllowed() }; }
    };
})();
