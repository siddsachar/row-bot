const assert = require('node:assert/strict');
const path = require('node:path');

const defaultNavigator = {
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
    platform: 'Win32',
    maxTouchPoints: 0,
};

global.window = {
    navigator: defaultNavigator,
    screen: { width: 1920, height: 1080 },
    location: { href: 'https://row-bot.ai/' },
    matchMedia: () => ({ matches: false }),
    addEventListener() {},
    setTimeout() {},
};
global.navigator = defaultNavigator;
global.document = {
    documentElement: {
        clientHeight: 900,
        dataset: {},
        scrollHeight: 900,
        scrollTop: 0,
    },
    body: {},
    addEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
};
window.document = document;

require(path.resolve(__dirname, '../../docs/site.js'));

const { detectDevice } = window.RowBotLanding;
const media = coarse => query => ({ matches: query === '(pointer: coarse)' && coarse });
const environment = ({
    userAgent,
    platform,
    touchPoints = 0,
    coarse = false,
    width = 1920,
    height = 1080,
    userAgentData,
}) => ({
    navigator: {
        userAgent,
        platform,
        maxTouchPoints: touchPoints,
        ...(userAgentData ? { userAgentData } : {}),
    },
    matchMedia: media(coarse),
    screen: { width, height },
});

const cases = [
    {
        name: 'Windows desktop',
        input: environment({ userAgent: defaultNavigator.userAgent, platform: 'Win32' }),
        expected: { device: 'desktop', platform: 'windows', name: 'Windows' },
    },
    {
        name: 'macOS desktop',
        input: environment({ userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)', platform: 'MacIntel' }),
        expected: { device: 'desktop', platform: 'macos', name: 'macOS' },
    },
    {
        name: 'Linux desktop',
        input: environment({ userAgent: 'Mozilla/5.0 (X11; Linux x86_64)', platform: 'Linux x86_64' }),
        expected: { device: 'desktop', platform: 'linux', name: 'Linux' },
    },
    {
        name: 'iPhone',
        input: environment({ userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Mobile/15E148', platform: 'iPhone', touchPoints: 5, coarse: true, width: 390, height: 844 }),
        expected: { device: 'mobile', platform: 'ios', name: 'iOS' },
    },
    {
        name: 'Android before Linux fallback',
        input: environment({ userAgent: 'Mozilla/5.0 (Linux; Android 15; Pixel 9) Mobile', platform: 'Linux armv8l', touchPoints: 5, coarse: true, width: 412, height: 915 }),
        expected: { device: 'mobile', platform: 'android', name: 'Android' },
    },
    {
        name: 'iPadOS desktop user agent',
        input: environment({ userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15)', platform: 'MacIntel', touchPoints: 5, coarse: true, width: 820, height: 1180 }),
        expected: { device: 'mobile', platform: 'ios', name: 'iPadOS' },
    },
    {
        name: 'UA client hint mobile',
        input: environment({ userAgent: 'Mozilla/5.0 AppleWebKit/537.36', platform: 'MysteryOS', touchPoints: 5, coarse: true, width: 430, height: 932, userAgentData: { mobile: true, platform: 'MysteryOS' } }),
        expected: { device: 'mobile', platform: 'mobile', name: 'Mobile' },
    },
    {
        name: 'unknown mobile-like device',
        input: environment({ userAgent: 'Mozilla/5.0 AppleWebKit/537.36', platform: 'MysteryOS', touchPoints: 2, coarse: true, width: 600, height: 960 }),
        expected: { device: 'mobile', platform: 'mobile', name: 'Mobile' },
    },
    {
        name: 'unknown desktop fallback',
        input: environment({ userAgent: 'Mozilla/5.0 AppleWebKit/537.36', platform: 'MysteryOS', width: 1366, height: 768 }),
        expected: { device: 'desktop', platform: 'windows', name: 'Windows' },
    },
    {
        name: 'Windows touch device',
        input: environment({ userAgent: defaultNavigator.userAgent, platform: 'Win32', touchPoints: 10, coarse: true, width: 800, height: 1280 }),
        expected: { device: 'desktop', platform: 'windows', name: 'Windows' },
    },
];

for (const testCase of cases) {
    assert.deepEqual(detectDevice(testCase.input), testCase.expected, testCase.name);
}
