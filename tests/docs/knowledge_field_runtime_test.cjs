const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '../../docs/knowledge-field.js'), 'utf8');
const listeners = {};
const callbacks = new Map();
const colors = [];
let drawCount = 0;
let nextFrame = 1;
let mutation;
const observers = new Map();
const context = {
    clearRect() { drawCount += 1; },
    createRadialGradient() { return { addColorStop() {} }; },
    fillRect() {},
    beginPath() {},
    moveTo() {},
    lineTo() {},
    stroke() {},
    arc() {},
    fill() {},
    setTransform() {},
    set fillStyle(value) { colors.push(value); },
    get fillStyle() { return ''; },
};
const host = {
    addEventListener(type, callback) { listeners[type] = callback; },
    getBoundingClientRect() { return { left: 0, top: 0, width: 1440, height: 900 }; },
};
const canvas = {
    parentElement: host,
    hidden: false,
    width: 0,
    height: 0,
    getContext() { return context; },
    getBoundingClientRect() { return { left: 0, top: 0, width: 1440, height: 900 }; },
};
const story = { dataset: { scene: 'research' } };
const media = { matches: false, addEventListener(type, callback) { listeners[`media:${type}`] = callback; } };
const document = {
    hidden: false,
    body: { classList: {
        values: new Set(),
        toggle(name, enabled) { if (enabled) this.values.add(name); else this.values.delete(name); },
        contains(name) { return this.values.has(name); },
    } },
    querySelector(selector) { return selector === '[data-knowledge-field]' ? canvas : story; },
    addEventListener(type, callback) { listeners[`document:${type}`] = callback; },
};
const window = {
    devicePixelRatio: 3,
    location: { search: '' },
    matchMedia() { return media; },
    requestAnimationFrame(callback) { const id = nextFrame++; callbacks.set(id, callback); return id; },
    cancelAnimationFrame(id) { callbacks.delete(id); },
    addEventListener() {},
};
class MutationObserver {
    constructor(callback) { mutation = callback; }
    observe() {}
}
class IntersectionObserver {
    constructor(callback) { this.callback = callback; }
    observe(target) { observers.set(target, this.callback); }
}
class ResizeObserver {
    observe() {}
}
window.IntersectionObserver = IntersectionObserver;
window.ResizeObserver = ResizeObserver;

vm.runInNewContext(source, { window, document, navigator: { connection: { saveData: false } },
    MutationObserver, IntersectionObserver, ResizeObserver, URLSearchParams, Math });

assert.ok(canvas.width * canvas.height <= 1_200_000, 'drawing buffer stays bounded on high-DPI screens');
assert.equal(callbacks.size, 1, 'one animation loop runs while visible');
assert.ok(colors.some(value => typeof value === 'string' && value.includes('71,217,255')));

story.dataset.scene = 'automate';
mutation();
const [frameId, render] = callbacks.entries().next().value;
callbacks.delete(frameId);
render(100);
assert.ok(colors.some(value => typeof value === 'string' && value.includes('116,221,190')),
    'the field responds to the selected scene');

observers.get(story)([{ isIntersecting: false }]);
assert.equal(document.body.classList.contains('is-field-beyond-story'), true,
    'the field becomes quieter outside the product story');
const beforeQuieterFrame = drawCount;
const beforeQuieterColors = colors.length;
const [quieterFrameId, quieterFrame] = callbacks.entries().next().value;
callbacks.delete(quieterFrameId);
quieterFrame(140);
assert.equal(drawCount, beforeQuieterFrame, 'the full-page field throttles between story sections');
const [nextQuieterFrameId, nextQuieterFrame] = callbacks.entries().next().value;
callbacks.delete(nextQuieterFrameId);
nextQuieterFrame(160);
assert.ok(drawCount > beforeQuieterFrame, 'the field still animates below the story');
assert.ok(colors.slice(beforeQuieterColors).some(value => typeof value === 'string' && value.includes('71,217,255')),
    'the field returns to a neutral cyan below the story');
observers.get(host)([{ isIntersecting: false }]);
assert.equal(callbacks.size, 0, 'animation stops offscreen');
observers.get(host)([{ isIntersecting: true }]);
assert.equal(callbacks.size, 1, 'animation resumes when visible');
observers.get(story)([{ isIntersecting: true }]);
assert.equal(document.body.classList.contains('is-field-beyond-story'), false);
media.matches = true;
listeners['media:change']();
assert.equal(canvas.hidden, true, 'reduced motion hides the animated field');
assert.equal(callbacks.size, 0, 'reduced motion cancels the animation loop');
media.matches = false;
listeners['media:change']();
assert.equal(callbacks.size, 1);
window.RowBotKnowledgeField.freeze();
assert.equal(canvas.hidden, true, 'explicit motion freeze also hides the field');
assert.equal(callbacks.size, 0);
