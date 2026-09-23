const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '../../docs/knowledge-field.js'), 'utf8');
const listeners = {};
const callbacks = new Map();
const colors = [];
let nextFrame = 1;
let mutation;
let visibility;
const context = {
    clearRect() {},
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
    getBoundingClientRect() { return { width: 1440, height: 900 }; },
};
const story = { dataset: { scene: 'research' } };
const media = { matches: false, addEventListener(type, callback) { listeners[`media:${type}`] = callback; } };
const document = {
    hidden: false,
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
    constructor(callback) { visibility = callback; }
    observe() {}
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

visibility([{ isIntersecting: false }]);
assert.equal(callbacks.size, 0, 'animation stops offscreen');
visibility([{ isIntersecting: true }]);
assert.equal(callbacks.size, 1, 'animation resumes when visible');
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
