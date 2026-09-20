import { defineConfig } from '@playwright/test';
import path from 'node:path';

const baseURL = process.env.ROW_BOT_BROWSER_BASE_URL ?? 'http://127.0.0.1:4173';
const origin = new URL(baseURL);
if (!['127.0.0.1', 'localhost', '[::1]'].includes(origin.hostname)) {
  throw new Error(
    'Shell browser fixtures require an isolated loopback backend',
  );
}
const evidence = path.resolve(
  process.env.ROW_BOT_BROWSER_EVIDENCE ??
    path.resolve(
      '../.local/evidence/unified-client-platform/phase-5/browser/playwright-local',
    ),
);
const sealedEvidence = path.resolve(
  '../.local/evidence/unified-client-platform/phase-3',
);
const relativeEvidence = path.relative(sealedEvidence, evidence);
if (
  relativeEvidence === '' ||
  (!relativeEvidence.startsWith(`..${path.sep}`) &&
    relativeEvidence !== '..' &&
    !path.isAbsolute(relativeEvidence))
) {
  throw new Error('Browser output cannot overwrite sealed Phase 3 evidence');
}
const viewports = [
  { name: 'desktop', width: 1440, height: 900, touch: false },
  { name: 'laptop', width: 1280, height: 720, touch: false },
  { name: 'tablet', width: 820, height: 1180, touch: true },
  { name: 'phone', width: 390, height: 844, touch: true },
  { name: 'narrow', width: 360, height: 800, touch: true },
];
const engines = ['chromium', 'firefox', 'webkit'] as const;
const selectedEngine = process.env.ROW_BOT_BROWSER_ENGINE;
if (selectedEngine && !engines.some((engine) => engine === selectedEngine)) {
  throw new Error('Unknown ROW_BOT_BROWSER_ENGINE');
}

const chromiumLaunch = {
  args: [
    '--disable-background-networking',
    '--disable-component-update',
    '--js-flags=--expose-gc',
  ],
};
const chromiumChannel = process.env.ROW_BOT_BROWSER_CHANNEL
  ? { channel: process.env.ROW_BOT_BROWSER_CHANNEL }
  : {};
const phase5Projects =
  selectedEngine && selectedEngine !== 'chromium'
    ? []
    : [
        {
          name: 'chromium-p5-localhost-desktop',
          testMatch: /phase5-localhost-desktop\.spec\.ts/,
          use: { viewport: { width: 1440, height: 900 } },
        },
        {
          name: 'chromium-p5-authenticated-remote-desktop',
          testMatch: /phase5-remote-desktop\.spec\.ts/,
          use: { viewport: { width: 1440, height: 900 } },
        },
        {
          name: 'chromium-p5-phone',
          testMatch: /phase5-compact\.spec\.ts/,
          use: {
            viewport: { width: 390, height: 844 },
            hasTouch: true,
            isMobile: true,
          },
        },
        {
          name: 'chromium-p5-tablet',
          testMatch: /phase5-compact\.spec\.ts/,
          use: {
            viewport: { width: 820, height: 1180 },
            hasTouch: true,
            isMobile: true,
          },
        },
        {
          name: 'chromium-p5-narrow',
          testMatch: /phase5-compact\.spec\.ts/,
          use: {
            viewport: { width: 360, height: 800 },
            hasTouch: true,
            isMobile: true,
          },
        },
        ...(['expired', 'revoked', 'unauthorized'] as const).map(
          (scenario) => ({
            name: `chromium-p5-${scenario}`,
            testMatch: /phase5-auth-state\.spec\.ts/,
            metadata: { phase5Scenario: scenario },
            use: { viewport: { width: 1280, height: 720 } },
          }),
        ),
        {
          name: 'chromium-p5-offline-reconnect',
          testMatch: /phase5-offline-reconnect\.spec\.ts/,
          use: {
            viewport: { width: 390, height: 844 },
            hasTouch: true,
            isMobile: true,
          },
        },
        {
          name: 'chromium-p5-old-pwa-update',
          testMatch: /phase5-pwa-update\.spec\.ts/,
          use: {
            viewport: { width: 390, height: 844 },
            hasTouch: true,
            isMobile: true,
          },
        },
        {
          name: 'chromium-p5-remote-artifact-resource',
          testMatch: /phase5-remote-resource\.spec\.ts/,
          use: { viewport: { width: 1440, height: 900 } },
        },
      ].map((project) => ({
        ...project,
        use: {
          browserName: 'chromium' as const,
          ...chromiumChannel,
          launchOptions: chromiumLaunch,
          ...project.use,
        },
      }));

export default defineConfig({
  testDir: './tests/browser',
  outputDir: path.join(evidence, 'artifacts'),
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [
    ['list'],
    ['json', { outputFile: path.join(evidence, 'playwright-results.json') }],
  ],
  use: {
    baseURL,
    headless: true,
    // Playwright's built-in blocker probes navigator.serviceWorker in every
    // frame. That probe raises a page exception in the intentionally opaque
    // Designer preview sandbox. Browser suites that need registration blocked
    // install the sandbox-safe fixture blocker from unified-helpers instead;
    // tests of Playwright's native blocking behavior opt into it explicitly.
    serviceWorkers: 'allow',
    locale: 'en-GB',
    timezoneId: 'UTC',
    colorScheme: 'light',
    deviceScaleFactor: 1,
    // Raw Playwright traces include authentication headers. Evidence helpers
    // record sanitized event timelines, console messages and screenshots instead.
    trace: 'off',
    video: 'off',
    screenshot: 'only-on-failure',
  },
  projects: [
    ...engines
      .filter((engine) => !selectedEngine || selectedEngine === engine)
      .flatMap((engine) =>
        viewports.map(({ name, width, height, touch }) => ({
          name: `${engine}-${name}`,
          testIgnore: /phase5-.*\.spec\.ts/,
          use: {
            browserName: engine,
            viewport: { width, height },
            hasTouch: touch,
            isMobile: engine === 'firefox' ? false : touch,
            ...(engine === 'chromium' ? chromiumChannel : {}),
            launchOptions: engine === 'chromium' ? chromiumLaunch : {},
          },
        })),
      ),
    ...phase5Projects,
  ],
});
