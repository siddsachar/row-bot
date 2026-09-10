import { test, expect, writeEvidence } from './evidence';
import {
  blockFixtureServiceWorkers,
  composer,
  conversationState,
  newConversation,
  assertWorkspaceIdentity,
  markWorkspaceIdentity,
} from './unified-helpers';

type Metrics = {
  mounted: number;
  renders: number;
};
type AuditWindow = Window & {
  __ROW_BOT_WORKSPACE_METRICS__: () => Metrics;
  __QA_TIMERS__: () => { timeouts: number; intervals: number };
  __QA_PREVIEW_BUILDS__: number;
};
test.use({ serviceWorkers: 'allow' });
test.beforeEach(async ({ context }) => blockFixtureServiceWorkers(context));

test('real artifact stays unchanged for sixty seconds and one hundred reopen cycles release owned observers', async ({
  page,
  context,
  browser,
}, testInfo) => {
  test.skip(
    testInfo.project.name !== 'chromium-desktop',
    'PB08/PB09 owned counters and forced-GC calibration are desktop Chromium; resource interactions run all viewports.',
  );
  test.setTimeout(300_000);
  await page.addInitScript(() => {
    const timeouts = new Set<number>();
    const intervals = new Set<number>();
    const timeout = window.setTimeout.bind(window),
      interval = window.setInterval.bind(window);
    const clearTimeout = window.clearTimeout.bind(window),
      clearInterval = window.clearInterval.bind(window);
    window.setTimeout = ((
      handler: TimerHandler,
      delay?: number,
      ...args: unknown[]
    ) => {
      if (typeof handler !== 'function')
        return timeout(handler, delay, ...args);
      const id = timeout(() => {
        timeouts.delete(id);
        handler(...args);
      }, delay);
      timeouts.add(id);
      return id;
    }) as typeof window.setTimeout;
    window.setInterval = ((
      handler: TimerHandler,
      delay?: number,
      ...args: unknown[]
    ) => {
      const id = interval(handler, delay, ...args);
      intervals.add(id);
      return id;
    }) as typeof window.setInterval;
    window.clearTimeout = ((id?: number) => {
      timeouts.delete(id!);
      intervals.delete(id!);
      clearTimeout(id);
    }) as typeof window.clearTimeout;
    window.clearInterval = ((id?: number) => {
      timeouts.delete(id!);
      intervals.delete(id!);
      clearInterval(id);
    }) as typeof window.clearInterval;
    Object.assign(window, {
      __QA_TIMERS__: () => ({
        timeouts: timeouts.size,
        intervals: intervals.size,
      }),
      __QA_PREVIEW_BUILDS__: 0,
    });
    addEventListener('DOMContentLoaded', () =>
      new MutationObserver((records) => {
        for (const record of records) {
          if (
            record.type === 'attributes' &&
            record.attributeName === 'srcdoc' &&
            record.target instanceof HTMLIFrameElement
          )
            (window as unknown as AuditWindow).__QA_PREVIEW_BUILDS__++;
          for (const node of record.addedNodes)
            if (node instanceof Element)
              (window as unknown as AuditWindow).__QA_PREVIEW_BUILDS__ +=
                (node instanceof HTMLIFrameElement ? 1 : 0) +
                node.querySelectorAll('iframe').length;
        }
      }).observe(document.body, {
        subtree: true,
        childList: true,
        attributes: true,
        attributeFilter: ['srcdoc'],
      }),
    );
  });
  const conversation = await newConversation(page);
  let panelRequests = 0;
  const inFlight = new Set<unknown>();
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname;
    if (
      /\/api\/v1\/conversations\/[^/]+\/(artifacts|workspaces)\//.test(path)
    ) {
      panelRequests++;
      inFlight.add(request);
    }
  });
  page.on('requestfinished', (request) => inFlight.delete(request));
  page.on('requestfailed', (request) => inFlight.delete(request));
  await composer(page).fill('Real panel lifecycle draft');
  await page.getByRole('button', { name: 'Add resource', exact: true }).click();
  const setup = page.getByRole('dialog', { name: 'Add resource', exact: true });
  await setup
    .getByRole('textbox', { name: 'Name (optional)', exact: true })
    .fill('Lifecycle Deck');
  await setup.getByRole('button', { name: 'Create Deck', exact: true }).click();
  await expect
    .poll(
      async () =>
        (await conversationState(page, conversation)).conversation
          .resource_bindings.length,
    )
    .toBe(1);
  await expect(
    setup.getByText('Resource ready', { exact: true }),
  ).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(
    page
      .frameLocator('iframe[title^="Slide preview:"]')
      .getByText('Blank slide — describe what to build', { exact: true }),
  ).toBeVisible();
  await markWorkspaceIdentity(page);
  const snapshot = () =>
    page.evaluate(() => ({
      wallClock: Date.now(),
      metrics: (
        window as unknown as AuditWindow
      ).__ROW_BOT_WORKSPACE_METRICS__(),
      timers: (window as unknown as AuditWindow).__QA_TIMERS__(),
      previewBuilds: (window as unknown as AuditWindow).__QA_PREVIEW_BUILDS__,
    }));
  await expect.poll(() => inFlight.size).toBe(0);
  const visibleRequestsBefore = panelRequests;
  const visibleBefore = await snapshot();
  await page.waitForTimeout(60_000);
  const visibleAfter = await snapshot();
  const visibleRequestsAfter = panelRequests;
  await writeEvidence(testInfo, 'PB08-visible-wallclock-partial', {
    visibleBefore,
    visibleAfter,
    visibleRequestsBefore,
    visibleRequestsAfter,
  });
  expect(
    visibleAfter.wallClock - visibleBefore.wallClock,
  ).toBeGreaterThanOrEqual(60_000);
  expect(visibleAfter.metrics.renders).toBe(visibleBefore.metrics.renders);
  expect(panelRequests).toBe(visibleRequestsBefore);
  expect(visibleAfter.previewBuilds).toBe(visibleBefore.previewBuilds);
  await page
    .getByRole('button', { name: 'Panel actions', exact: true })
    .click();
  await page
    .getByRole('menuitem', { name: 'Collapse panel', exact: true })
    .click();
  const hiddenBefore = await snapshot();
  const hiddenRequestsBefore = panelRequests;
  await page.waitForTimeout(60_000);
  const hiddenAfter = await snapshot();
  const hiddenRequestsAfter = panelRequests;
  await writeEvidence(testInfo, 'PB08-hidden-wallclock-partial', {
    hiddenBefore,
    hiddenAfter,
    hiddenRequestsBefore,
    hiddenRequestsAfter,
  });
  expect(hiddenAfter.wallClock - hiddenBefore.wallClock).toBeGreaterThanOrEqual(
    60_000,
  );
  expect(hiddenAfter.metrics.renders).toBe(hiddenBefore.metrics.renders);
  expect(hiddenAfter.previewBuilds).toBe(hiddenBefore.previewBuilds);
  expect(panelRequests).toBe(hiddenRequestsBefore);
  await page
    .getByRole('button', { name: 'Close all panels', exact: true })
    .click();
  await page.waitForTimeout(6500);
  const baseline = await snapshot();
  const cdp = await context.newCDPSession(page);
  await cdp.send('HeapProfiler.collectGarbage');
  const beforeHeap = await cdp.send('Runtime.getHeapUsage');
  const cycles: { cycle: number; mounted: number }[] = [];
  for (let index = 0; index < 100; index++) {
    await page
      .getByRole('button', { name: 'Lifecycle Deck', exact: true })
      .click();
    await expect(
      page.getByRole('region', { name: 'Design preview', exact: true }),
    ).toBeVisible();
    await page
      .getByRole('button', { name: 'Close all panels', exact: true })
      .click();
    const observed = await snapshot();
    expect(observed.metrics.mounted).toBe(0);
    cycles.push({
      cycle: index + 1,
      mounted: observed.metrics.mounted,
    });
  }
  await page.waitForTimeout(6500);
  await cdp.send('HeapProfiler.collectGarbage');
  const afterHeap = await cdp.send('Runtime.getHeapUsage');
  const settled = await snapshot();
  expect(settled.metrics.mounted).toBe(0);
  await expect.poll(() => inFlight.size).toBe(0);
  const retainedBytes = afterHeap.usedSize - beforeHeap.usedSize;
  await writeEvidence(testInfo, 'PB08-PB09-real-artifact', {
    browser: browser.version(),
    conversation,
    clock:
      'Two real wallclock60second unchanged windows and real6.5second post-close settlement; actual owner reads and resource panel, no clock injection into opaque preview.',
    visibleBefore,
    visibleAfter,
    hiddenBefore,
    hiddenAfter,
    baseline,
    settled,
    cycles,
    beforeHeap,
    afterHeap,
    retainedBytes,
    network: {
      totalPanelRequests: panelRequests,
      inFlightAfterSettle: inFlight.size,
      visibleRequestsBefore,
      hiddenRequestsBefore,
      visibleRequestsAfter,
      hiddenRequestsAfter,
    },
    limitBytes: 10 * 1024 * 1024,
  });
  await cdp.detach();
  await assertWorkspaceIdentity(page);
  expect(settled.timers.timeouts).toBeLessThanOrEqual(baseline.timers.timeouts);
  expect(settled.timers.intervals).toBeLessThanOrEqual(
    baseline.timers.intervals,
  );
  expect(retainedBytes).toBeLessThanOrEqual(10 * 1024 * 1024);
});
