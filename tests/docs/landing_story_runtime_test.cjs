const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '../../docs/landing-story.js'), 'utf8');
const beats = ['research', 'create', 'automate', 'ship'];

class FakeClassList {
    constructor() { this.values = new Set(); }
    add(...names) { names.forEach(name => this.values.add(name)); }
    remove(...names) { names.forEach(name => this.values.delete(name)); }
    contains(name) { return this.values.has(name); }
    toggle(name, force) {
        const enabled = force === undefined ? !this.values.has(name) : Boolean(force);
        if (enabled) this.values.add(name);
        else this.values.delete(name);
        return enabled;
    }
}

function element(dataset = {}) {
    const attributes = {};
    return {
        dataset: {...dataset},
        classList: new FakeClassList(),
        style: {setProperty() {}},
        listeners: {},
        hidden: false,
        focused: false,
        addEventListener(type, handler) { this.listeners[type] = handler; },
        setAttribute(name, value) { attributes[name] = String(value); },
        getAttribute(name) { return attributes[name] ?? null; },
        removeAttribute(name) { delete attributes[name]; },
        hasAttribute(name) { return Object.hasOwn(attributes, name); },
        querySelectorAll() { return []; },
        focus() { this.focused = true; },
        emit(type, event = {}) { this.listeners[type]?.(event); },
    };
}

function makeRuntime({reducedMotion = false, saveData = false, search = '', intro = false} = {}) {
    const controller = element();
    if (intro) controller.classList.add('is-intro');
    controller.offsetHeight = 4400;
    controller.getBoundingClientRect = () => ({top: 0});
    const stage = element();
    const productStage = element();
    productStage.getBoundingClientRect = () => ({top: 300, bottom: 900, height: 600});
    const journeyTitle = element();
    journeyTitle.getBoundingClientRect = () => ({top: 180});
    const siteNav = element();
    siteNav.getBoundingClientRect = () => ({height: 66});
    const control = element();
    const status = element();
    status.textContent = '';
    const images = [element()];
    images[0].src = 'https://row-bot.ai/media/landing-story/buddy/working.webp';
    images[0].classList.add('is-active');
    const posterDecodes = [];
    class FakeImage {
        decode() {
            if (this.defer) return new Promise(resolve => posterDecodes.push(resolve));
            return Promise.resolve();
        }
    }
    const triggers = beats.map(beat => element({storyTrigger: beat}));
    const demoPosters = ['launch-campaign', 'research-report', 'background-workflow', 'inbox-action-plan']
        .map(name => element({src: `demos/${name}.jpg`}));
    const panels = beats.map(beat => element({storyPanel: beat}));
    panels.slice(1).forEach(panel => { panel.hidden = true; });
    const copies = beats.map(beat => element({storyCopy: beat}));
    copies.slice(1).forEach(copy => { copy.hidden = true; });
    const videos = beats.map(beat => {
        const video = element({storyVideo: beat});
        video.setAttribute('src', `${beat}.webm`);
        video.currentTime = 4;
        video.paused = true;
        video.ended = false;
        video.playCalls = 0;
        video.pauseCalls = 0;
        video.load = () => {};
        video.play = () => {
            video.playCalls += 1;
            video.paused = false;
            video.ended = false;
            return Promise.resolve();
        };
        video.pause = () => { video.pauseCalls += 1; video.paused = true; };
        return video;
    });
    const buddyVideos = ['idle', 'thinking', 'working', 'approval', 'success', 'error'].map(state => {
        const video = element({buddyMotion: state});
        video.currentTime = 0;
        video.readyState = 1;
        video.seeking = false;
        video.duration = 12;
        video.paused = true;
        video.ended = false;
        video.playCalls = 0;
        video.pauseCalls = 0;
        video.load = () => {};
        video.play = () => {
            video.playCalls += 1;
            video.paused = false;
            video.ended = false;
            return Promise.resolve();
        };
        video.pause = () => { video.pauseCalls += 1; video.paused = true; };
        video.requestVideoFrameCallback = callback => callback();
        return video;
    });
    const mediaQuery = {
        matches: reducedMotion,
        listeners: {},
        addEventListener(type, handler) { this.listeners[type] = handler; },
    };
    const documentListeners = {};
    const windowListeners = {};
    const timers = new Map();
    const scrollCalls = [];
    let nextTimer = 1;
    const document = {
        hidden: false,
        addEventListener(type, handler) { documentListeners[type] = handler; },
        querySelector(selector) {
            if (selector === '[data-story-controller]') return controller;
            if (selector === '[data-buddy-stage]') return stage;
            if (selector === '[data-buddy-control]') return control;
            if (selector === '[data-buddy-status]') return status;
            if (selector === '[data-sovereignty-canvas]') return null;
            if (selector === '[data-sovereignty-buddy]') return null;
            if (selector === '[data-sovereignty-buddy-video]') return null;
            if (selector === '.app-stack') return null;
            if (selector === '.product-stage') return productStage;
            if (selector === '#journey-title') return journeyTitle;
            if (selector === '.site-nav') return siteNav;
            const match = selector.match(/^\[data-story-video="(.+)"\]$/);
            return match ? videos.find(video => video.dataset.storyVideo === match[1]) : null;
        },
        querySelectorAll(selector) {
            if (selector === '[data-demo-poster]') return demoPosters;
            if (selector === '[data-buddy-image]') return images;
            if (selector === '[data-buddy-motion]') return buddyVideos;
            if (selector === '[data-story-trigger]') return triggers;
            if (selector === '[data-story-panel]') return panels;
            if (selector === '[data-story-copy]') return copies;
            if (selector === '[data-story-video]') return videos;
            return [];
        },
    };
    const observers = [];
    class FakeIntersectionObserver {
        constructor(callback) { this.callback = callback; observers.push(this); }
        observe(target) {
            if (target === controller) Promise.resolve().then(() => this.callback([{isIntersecting: true, target}]));
        }
        unobserve() {}
    }
    const runtimeWindow = {
        document,
        location: {search},
        innerHeight: 1000,
        innerWidth: 1920,
        scrollY: 0,
        devicePixelRatio: 1,
        matchMedia: () => mediaQuery,
        addEventListener(type, handler) { windowListeners[type] = handler; },
        requestAnimationFrame(callback) { callback(); return 0; },
        cancelAnimationFrame() {},
        setTimeout(callback, delay = 0) {
            const id = nextTimer++;
            timers.set(id, {callback, delay});
            return id;
        },
        clearTimeout(id) { timers.delete(id); },
        scrollTo(options) { scrollCalls.push(options); runtimeWindow.scrollY = options.top; },
        IntersectionObserver: FakeIntersectionObserver,
    };
    const context = {
        window: runtimeWindow,
        document,
        navigator: {connection: {saveData}},
        IntersectionObserver: FakeIntersectionObserver,
        URLSearchParams,
        console,
        Promise,
        Math,
        Image: FakeImage,
    };
    vm.runInNewContext(source, context);
    return {
        api: runtimeWindow.RowBotLandingStory,
        controller,
        stage,
        productStage,
        scrollCalls,
        control,
        status,
        images,
        triggers,
        panels,
        copies,
        videos,
        buddyVideos,
        document,
        documentListeners,
        windowListeners,
        mediaQuery,
        FakeImage,
        posterDecodes,
        demoPosters,
        observers,
        runTimers(maxDelay = Infinity) {
            for (const [id, timer] of [...timers]) {
                if (timer.delay > maxDelay) continue;
                timers.delete(id);
                timer.callback();
            }
        },
    };
}

const preventDefaultEvent = key => ({key, prevented: false, preventDefault() { this.prevented = true; }});
const flush = () => new Promise(resolve => setImmediate(resolve));

(async () => {
    const runtime = makeRuntime();
    await Promise.resolve();
    await Promise.resolve();
    assert.deepEqual(JSON.parse(JSON.stringify(runtime.api.getState())), {
        beat: 'research',
        buddy: 'thinking',
        motionAllowed: true,
    });
    assert.equal(runtime.controller.dataset.scene, 'research');
    assert.equal(runtime.panels[0].hidden, false);
    assert.equal(runtime.panels.slice(1).every(panel => panel.hidden), true);
    assert.equal(runtime.videos[0].playCalls > 0, true);
    assert.equal(runtime.demoPosters.every(image => !image.src), true);
    runtime.observers[0].callback([{isIntersecting: true, target: runtime.demoPosters[0]}]);
    assert.equal(runtime.demoPosters[0].src, 'demos/launch-campaign.jpg');
    assert.equal(runtime.demoPosters.slice(1).every(image => !image.src), true);

    runtime.triggers[3].emit('click');
    await flush();
    assert.equal(runtime.api.getState().beat, 'ship');
    assert.equal(runtime.api.getState().buddy, 'approval');
    assert.equal(runtime.controller.dataset.scene, 'ship');
    assert.equal(runtime.panels[3].hidden, false);
    assert.equal(runtime.videos[3].currentTime, 0);
    assert.equal(runtime.stage.classList.contains('is-repositioning'), true);
    assert.equal(runtime.buddyVideos.every(video => video.hidden), true);
    runtime.runTimers(1000);
    await flush();
    assert.equal(runtime.stage.classList.contains('is-repositioning'), false);
    assert.equal(runtime.buddyVideos.filter(video => !video.hidden).length, 1);
    assert.equal(runtime.buddyVideos.find(video => video.dataset.buddyMotion === 'approval').playCalls > 0, true);

    const left = preventDefaultEvent('ArrowLeft');
    runtime.triggers[3].emit('keydown', left);
    assert.equal(left.prevented, true);
    assert.equal(runtime.api.getState().beat, 'automate');
    assert.equal(runtime.api.getState().buddy, 'idle');
    assert.equal(runtime.triggers[2].focused, true);
    runtime.runTimers(1000);
    await flush();
    const idleMotion = runtime.buddyVideos.find(video => video.dataset.buddyMotion === 'idle');
    assert.equal(idleMotion.currentTime, 0);
    idleMotion.currentTime = 4.5;
    idleMotion.emit('timeupdate', {currentTarget: idleMotion});
    assert.equal(idleMotion.paused, true);
    const idlePlayCalls = idleMotion.playCalls;
    runtime.observers.at(-1).callback([{isIntersecting: true}]);
    assert.equal(idleMotion.playCalls, idlePlayCalls);

    const space = preventDefaultEvent(' ');
    runtime.control.emit('keydown', space);
    assert.equal(space.prevented, true);
    assert.equal(runtime.api.getState().buddy, 'approval');

    runtime.api.setScene('approval-boundary');
    runtime.videos[3].currentTime = 2.25;
    runtime.videos[3].emit('timeupdate');
    assert.equal(runtime.api.getState().buddy, 'success');
    runtime.videos[3].emit('ended');
    assert.equal(runtime.api.getState().buddy, 'success');
    runtime.videos[3].emit('error');
    assert.equal(runtime.api.getState().buddy, 'success');

    runtime.api.setScene('research');
    runtime.api.setScene('ship');
    runtime.api.setScene('create');
    runtime.runTimers(1000);
    await flush();
    assert.equal(runtime.api.getState().beat, 'create');
    assert.equal(runtime.images[0].src.endsWith('/working.webp'), true);
    assert.equal(runtime.buddyVideos.filter(video => !video.hidden).length, 1);
    assert.equal(runtime.panels.filter(panel => !panel.hidden).length, 1);

    const delayed = makeRuntime();
    await flush();
    delayed.FakeImage.prototype.defer = true;
    delayed.api.setScene('ship');
    delayed.api.setScene('create');
    assert.equal(delayed.posterDecodes.length, 2);
    delayed.posterDecodes[1]();
    delayed.posterDecodes[0]();
    delayed.runTimers(1000);
    await flush();
    assert.equal(delayed.api.getState().beat, 'create');
    assert.equal(delayed.images[0].src.endsWith('/working.webp'), true);
    assert.equal(delayed.buddyVideos.filter(video => video.classList.contains('is-active')).length, 1);

    const staleClip = makeRuntime();
    await flush();
    let resolveOldPlay;
    staleClip.videos[1].play = () => new Promise(resolve => { resolveOldPlay = resolve; });
    staleClip.api.setScene('create');
    staleClip.api.setScene('ship');
    resolveOldPlay();
    await flush();
    assert.equal(staleClip.videos[1].classList.contains('is-playing'), false);
    assert.equal(staleClip.panels.filter(panel => !panel.hidden).length, 1);

    runtime.api.setScene('ship');
    runtime.videos[3].emit('ended');
    runtime.runTimers(4000);
    await flush();
    assert.equal(runtime.api.getState().beat, 'research');
    runtime.observers.at(-1).callback([{isIntersecting: false}]);
    assert.equal(runtime.api.getState().motionAllowed, false);
    assert.equal(runtime.videos.every(video => video.paused), true);
    runtime.observers.at(-1).callback([{isIntersecting: true}]);
    await flush();
    assert.equal(runtime.api.getState().motionAllowed, true);

    runtime.api.freezeMotion();
    assert.equal(runtime.api.getState().motionAllowed, false);
    assert.equal(runtime.stage.classList.contains('is-paused'), true);
    assert.equal(runtime.videos.every(video => video.pauseCalls > 0), true);

    runtime.document.hidden = true;
    runtime.documentListeners.visibilitychange();
    assert.equal(runtime.api.getState().motionAllowed, false);

    const reduced = makeRuntime({reducedMotion: true, search: '?landing_scene=ship'});
    await Promise.resolve();
    assert.equal(reduced.api.getState().beat, 'ship');
    assert.equal(reduced.api.getState().motionAllowed, false);
    assert.equal(reduced.videos.every(video => video.playCalls === 0), true);
    const dataSaver = makeRuntime({saveData: true});
    await flush();
    assert.equal(dataSaver.api.getState().motionAllowed, false);
    assert.equal(dataSaver.videos.every(video => video.playCalls === 0), true);
    assert.equal(dataSaver.buddyVideos.every(video => video.playCalls === 0), true);

    const failedClip = makeRuntime({search: '?landing_scene=research'});
    await flush();
    failedClip.videos[0].emit('error');
    failedClip.runTimers(1000);
    assert.equal(failedClip.api.getState().beat, 'research');
    failedClip.runTimers(6000);
    await flush();
    assert.equal(failedClip.api.getState().beat, 'create');


    const frozen = makeRuntime({search: '?landing_scene=synthesis-sol&motion=freeze'});
    await Promise.resolve();
    assert.equal(frozen.api.getState().beat, 'create');
    assert.equal(frozen.api.getState().motionAllowed, false);
    assert.equal(frozen.videos.every(video => video.playCalls === 0), true);

    const intro = makeRuntime({intro: true});
    await flush();
    assert.equal(intro.api.getState().buddy, 'working');
    assert.equal(intro.buddyVideos.find(video => video.dataset.buddyMotion === 'working').playCalls > 0, true);
    assert.equal(intro.videos.every(video => video.playCalls === 0), true);
    intro.runTimers(3200);
    await flush();
    assert.equal(intro.controller.classList.contains('is-intro'), false);
    assert.equal(intro.controller.classList.contains('is-story-active'), false);
    assert.equal(intro.api.getState().buddy, 'thinking');
    assert.equal(intro.videos[0].playCalls > 0, true);
    intro.runTimers(1000);
    await flush();
    assert.equal(intro.buddyVideos.find(video => video.dataset.buddyMotion === 'thinking').currentTime, 0);
    assert.deepEqual(JSON.parse(JSON.stringify(intro.scrollCalls)), [{top: 32, behavior: 'smooth'}]);
    intro.controller.getBoundingClientRect = () => ({top: -5});
    intro.windowListeners.scroll();
    assert.equal(intro.controller.classList.contains('is-intro'), false);
    assert.equal(intro.controller.classList.contains('is-story-active'), false);
    const interruptedIntro = makeRuntime({intro: true});
    interruptedIntro.windowListeners.wheel();
    interruptedIntro.runTimers(3200);
    interruptedIntro.runTimers(1000);
    assert.equal(interruptedIntro.scrollCalls.length, 0);
    const interruptedCenter = makeRuntime({intro: true});
    interruptedCenter.runTimers(3200);
    interruptedCenter.windowListeners.wheel();
    interruptedCenter.runTimers(1000);
    assert.equal(interruptedCenter.scrollCalls.length, 0);
    const failedMotion = makeRuntime({intro: true});
    failedMotion.buddyVideos.find(video => video.dataset.buddyMotion === 'thinking').play = () => Promise.reject(new Error('decode failed'));
    failedMotion.runTimers(3200);
    failedMotion.runTimers(1000);
    await flush();
    assert.equal(failedMotion.images[0].src.endsWith('/thinking.webp'), true);
    assert.equal(failedMotion.stage.classList.contains('is-video-ready'), false);

    const frameGate = makeRuntime();
    await flush();
    const appFrames = [];
    const buddyFrames = [];
    frameGate.videos[1].requestVideoFrameCallback = callback => appFrames.push(callback);
    frameGate.buddyVideos.find(video => video.dataset.buddyMotion === 'working')
        .requestVideoFrameCallback = callback => buddyFrames.push(callback);
    frameGate.api.setScene('create');
    frameGate.runTimers(1000);
    await flush();
    assert.equal(frameGate.videos[1].classList.contains('is-playing'), false);
    assert.equal(frameGate.stage.classList.contains('is-video-ready'), false);
    appFrames.shift()();
    buddyFrames.shift()();
    assert.equal(frameGate.videos[1].classList.contains('is-playing'), false);
    assert.equal(frameGate.stage.classList.contains('is-video-ready'), false);
    appFrames.shift()();
    buddyFrames.shift()();
    assert.equal(frameGate.videos[1].classList.contains('is-playing'), true);
    assert.equal(frameGate.stage.classList.contains('is-video-ready'), true);

    const idleGate = makeRuntime({intro: true});
    await flush();
    const idleFrames = [];
    const idleVideo = idleGate.buddyVideos.find(video => video.dataset.buddyMotion === 'idle');
    idleVideo.requestVideoFrameCallback = callback => idleFrames.push(callback);
    idleGate.api.setScene('automate');
    assert.equal(idleGate.stage.classList.contains('is-repositioning'), true);
    idleGate.observers.at(-1).callback([{isIntersecting: true}]);
    assert.equal(idleGate.stage.classList.contains('is-repositioning'), true);
    idleGate.runTimers(1000);
    await flush();
    assert.equal(idleVideo.classList.contains('is-active'), false);
    idleFrames.shift()(0, {mediaTime: .08});
    assert.equal(idleVideo.classList.contains('is-active'), false);
    idleFrames.shift()(0, {mediaTime: .18});
    idleFrames.shift()(0, {mediaTime: .22});
    assert.equal(idleVideo.classList.contains('is-active'), true);
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
