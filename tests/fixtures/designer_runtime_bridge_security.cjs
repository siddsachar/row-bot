const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const scenario = process.argv[2];
const source = fs.readFileSync(process.argv[3], 'utf8');
function element(values = {}) {
    const attributes = new Map(Object.entries(values));
    return {
        getAttribute: key => attributes.get(key) ?? null,
        setAttribute: (key, value) => attributes.set(key, String(value)),
        removeAttribute: key => attributes.delete(key),
        toggleAttribute(key, enabled) {
            if (enabled) attributes.set(key, ''); else attributes.delete(key);
        },
        hasAttribute: key => attributes.has(key),
    };
}
const root = element();
const sections = ['home', 'second'].map(route => element({'data-row-bot-route': route}));
const button = element({'aria-pressed': 'false'});
const messages = [], media = [], listeners = {}, documentListeners = {};
const parent = {postMessage: payload => messages.push(payload)};
const window = {parent, addEventListener: (kind, callback) => { listeners[kind] = callback; }};
if (scenario === 'published') {
    window.parent = window;
    window.postMessage = parent.postMessage;
}
const document = {
    documentElement: root, body: element(), readyState: 'complete',
    getElementById: () => ({textContent: JSON.stringify({initial: 'home', order: ['home', 'second']})}),
    querySelectorAll: selector => selector === '[data-row-bot-route-host]' ? sections : [button],
    querySelector: selector => selector.includes('video') ? {play: () => media.push('played')} : sections[0],
    addEventListener: (kind, callback) => { documentListeners[kind] = callback; },
};
const forbidden = () => assert.fail('Runtime attempted a privileged or network effect');
vm.runInNewContext(source, {window, document, fetch: forbidden, XMLHttpRequest: forbidden,
    WebSocket: forbidden, navigator: {}, localStorage: {setItem: forbidden},
    setTimeout: forbidden}, {timeout: 1000});
const send = (data, sender = window.parent) => listeners.message({source: sender, origin: 'null', data});
const navigate = {target: 'row-bot-runtime', type: 'navigate', route: 'second'};
const unchanged = () => {
    assert.equal(root.getAttribute('data-row-bot-active-route'), 'home');
    assert.equal(root.getAttribute('data-row-bot-state'), null);
    assert.deepEqual(media, []);
    assert.equal(messages.length, 2); // boot navigation and ready only
};

if (scenario === 'foreign') {
    for (const sender of [null, {}, {postMessage() {}}, window]) {
        send(navigate, sender);
        send({target: 'row-bot-runtime', type: 'toggle_state', key: 'expanded'}, sender);
        send({target: 'row-bot-runtime', type: 'play_media', assetId: 'demo'}, sender);
    }
    unchanged();
} else if (scenario === 'malformed') {
    for (const data of [null, undefined, [], 'navigate', 5, true, {},
        {...navigate, target: 'foreign'}, {...navigate, type: 'native.execute'},
        {...navigate, unknown: true}]) send(data);
    for (const [type, field] of [['navigate', 'route'], ['toggle_state', 'key'], ['play_media', 'assetId']]) {
        for (const value of [null, undefined, {}, [], 5, true, '', 'x'.repeat(4097), 'bad\nvalue', '\0']) {
            send({target: 'row-bot-runtime', type, [field]: value});
        }
    }
    unchanged();
} else if (scenario === 'parent' || scenario === 'published') {
    send(navigate);
    assert.equal(root.getAttribute('data-row-bot-active-route'), 'second');
    assert.equal(sections[1].getAttribute('aria-hidden'), 'false');
    send({target: 'row-bot-runtime', type: 'toggle_state', key: 'expanded'});
    assert.equal(root.getAttribute('data-row-bot-state'), 'expanded');
    assert.equal(button.getAttribute('aria-pressed'), 'true');
    send({target: 'row-bot-runtime', type: 'toggle_state', key: 'expanded'});
    assert.equal(root.getAttribute('data-row-bot-state'), null);
    send({target: 'row-bot-runtime', type: 'play_media', assetId: 'demo'});
    assert.deepEqual(media, ['played']);
} else if (scenario === 'declarative') {
    const control = element({'data-row-bot-action': 'navigate:second'});
    control.parentNode = document.body;
    let prevented = false;
    documentListeners.click({target: control, preventDefault() { prevented = true; }});
    assert.equal(root.getAttribute('data-row-bot-active-route'), 'second');
    assert.equal(prevented, true);
} else {
    assert.fail('Unknown scenario');
}
console.log(`${scenario}: passed`);
