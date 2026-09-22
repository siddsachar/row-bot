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

function makeRuntime({reducedMotion = false, search = ''} = {}) {
    const controller = element();
    controller.offsetHeight = 4400;
    controller.getBoundingClientRect = () => ({top: 0});
    const stage = element();
    const control = element();
    const status = element();
    status.textContent = '';
    const images = [element(), element()];
    images[0].src = 'https://row-bot.ai/media/landing-story/buddy/thinking.webp';
    images[0].classList.add('is-active');
    images[0].decode = () => Promise.resolve();
    images[1].src = 'https://row-bot.ai/media/landing-story/buddy/idle.webp';
    images[1].decode = () => Promise.resolve();
    const triggers = beats.map(beat => element({storyTrigger: beat}));
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
            if (selector === '.product-stage') return null;
            const match = selector.match(/^\[data-story-video="(.+)"\]$/);
            return match ? videos.find(video => video.dataset.storyVideo === match[1]) : null;
        },
        querySelectorAll(selector) {
            if (selector === '[data-buddy-image]') return images;
            if (selector === '[data-buddy-motion]') return buddyVideos;
            if (selector === '[data-story-trigger]') return triggers;
            if (selector === '[data-story-panel]') return panels;
            if (selector === '[data-story-copy]') return copies;
            if (selector === '[data-story-video]') return videos;
            return [];
        },
    };
    class FakeIntersectionObserver {
        constructor(callback) { this.callback = callback; }
        observe() { Promise.resolve().then(() => this.callback([{isIntersecting: true}])); }
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
        requestAnimationFrame(callback) { callback(); return 1; },
        cancelAnimationFrame() {},
        setTimeout(callback, delay = 0) {
            const id = nextTimer++;
            timers.set(id, {callback, delay});
            return id;
        },
        clearTimeout(id) { timers.delete(id); },
        scrollTo() {},
        IntersectionObserver: FakeIntersectionObserver,
    };
    const context = {
        window: runtimeWindow,
        document,
        navigator: {connection: {saveData: false}},
        IntersectionObserver: FakeIntersectionObserver,
        URLSearchParams,
        console,
        Promise,
        Math,
    };
    vm.runInNewContext(source, context);
    return {
        api: runtimeWindow.RowBotLandingStory,
        controller,
        stage,
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
        mediaQuery,
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

    runtime.triggers[3].emit('click');
    await Promise.resolve();
    assert.equal(runtime.api.getState().beat, 'ship');
    assert.equal(runtime.api.getState().buddy, 'error');
    assert.equal(runtime.controller.dataset.scene, 'ship');
    assert.equal(runtime.panels[3].hidden, false);
    assert.equal(runtime.videos[3].currentTime, 0);
    assert.equal(runtime.stage.classList.contains('is-repositioning'), true);
    assert.equal(runtime.buddyVideos.every(video => video.hidden), true);
    runtime.runTimers(1000);
    await Promise.resolve();
    await Promise.resolve();
    assert.equal(runtime.stage.classList.contains('is-repositioning'), false);
    assert.equal(runtime.buddyVideos.filter(video => !video.hidden).length, 1);
    assert.equal(runtime.buddyVideos.find(video => video.dataset.buddyMotion === 'error').playCalls > 0, true);

    const left = preventDefaultEvent('ArrowLeft');
    runtime.triggers[3].emit('keydown', left);
    assert.equal(left.prevented, true);
    assert.equal(runtime.api.getState().beat, 'automate');
    assert.equal(runtime.triggers[2].focused, true);

    const space = preventDefaultEvent(' ');
    runtime.control.emit('keydown', space);
    assert.equal(space.prevented, true);
    assert.equal(runtime.api.getState().buddy, 'thinking');

    runtime.api.setScene('approval-boundary');
    runtime.videos[3].emit('ended');
    assert.equal(runtime.api.getState().buddy, 'success');
    runtime.videos[3].emit('error');
    assert.equal(runtime.api.getState().buddy, 'error');

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

    const frozen = makeRuntime({search: '?landing_scene=synthesis-sol&motion=freeze'});
    await Promise.resolve();
    assert.equal(frozen.api.getState().beat, 'create');
    assert.equal(frozen.api.getState().motionAllowed, false);
    assert.equal(frozen.videos.every(video => video.playCalls === 0), true);
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
