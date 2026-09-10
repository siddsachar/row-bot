import { performance } from 'node:perf_hooks';
import type { BrowserContext, Page } from '@playwright/test';
import {
  test,
  expect,
  assertLocalContentPolicy,
  distribution,
  writeEvidence,
} from './evidence';
import {
  composer,
  fixtureState,
  markWorkspaceIdentity,
  assertWorkspaceIdentity,
  newConversation,
  openConversation,
  releaseProducer,
  startExpiryProducer,
  installResetProbe,
} from './unified-helpers';

test.use({ nativeNetwork: true });

test('actual HTTP startup has five cold and ten warm usable conversation samples', async ({
  page,
  browser,
  baseURL,
}, testInfo) => {
  test.skip(
    testInfo.project.name !== 'chromium-desktop',
    'PB01 controlled same-browser cold/warm calibration uses desktop Chromium; functional startup runs across viewports.',
  );
  test.setTimeout(180_000);
  const cold: number[] = [],
    warm: number[] = [];
  const failures: { sample: number; errors: string[] }[] = [];
  let conversation = 'p1-browser-a';
  for (let index = 0; index < 5; index++) {
    const isolated = await browser.newContext({
      baseURL,
      viewport: { width: 1440, height: 900 },
      serviceWorkers: 'block',
      locale: 'en-GB',
    });
    const fresh = await isolated.newPage();
    const errors: string[] = [];
    fresh.on('pageerror', () => errors.push('JavaScript page exception'));
    fresh.on('console', (event) => {
      if (event.type() === 'error') errors.push('Console error');
    });
    fresh.on('request', (request) => {
      const url = new URL(request.url());
      if (
        ['http:', 'https:'].includes(url.protocol) &&
        url.origin !== new URL(baseURL!).origin
      )
        errors.push('External request');
    });
    try {
      await assertLocalContentPolicy(fresh);
      const started = performance.now();
      conversation = await newConversation(fresh);
      await composer(fresh).fill('Usable cold startup draft');
      cold.push(performance.now() - started);
      failures.push({ sample: index, errors });
      expect(errors).toEqual([]);
    } finally {
      await isolated.close();
    }
  }
  for (let index = 0; index < 10; index++) {
    const started = performance.now();
    await openConversation(page, conversation);
    await composer(page).fill('Usable warm startup draft');
    warm.push(performance.now() - started);
  }
  await writeEvidence(testInfo, 'PB01-real-HTTP-usable-startup', {
    browser: browser.version(),
    cold: { samples: cold, ...distribution(cold) },
    warm: { samples: warm, ...distribution(warm) },
    failures,
    excludedSamples: [],
    method:
      'Five new isolated contexts with empty browser cache/storage in one browser process, then ten navigations in one warmed context. Real HTTP handshake and confirmed native conversation, composer input; server ready time separately recorded by runner. No route interception/cache disabling.',
  });
  expect(distribution(cold).p95).toBeLessThanOrEqual(2000);
  expect(distribution(warm).p95).toBeLessThanOrEqual(1000);
});

test('twenty actual conversation preference openings and theme changes commit within feedback budgets', async ({
  page,
  browser,
}, testInfo) => {
  test.skip(
    testInfo.project.name !== 'chromium-desktop',
    'PB02/PB04 twenty-repeat real-clock calibration uses desktop Chromium; themes are functionally covered across viewports.',
  );
  await newConversation(page);
  await composer(page).fill(
    'Keep this real workspace draft through preferences',
  );
  await markWorkspaceIdentity(page);
  const feedback: number[] = [],
    theme: number[] = [];
  for (let index = 0; index < 20; index++) {
    const trigger = page.getByRole('button', {
      name: 'Preferences',
      exact: true,
    });
    await trigger.evaluate((element) => {
      Object.assign(window, { __QA_PREFERENCE_COMMIT__: null });
      element.addEventListener(
        'click',
        () => {
          const start = performance.now();
          const committed = () => {
            if (document.querySelector('[role="dialog"] select'))
              requestAnimationFrame(() =>
                Object.assign(window, {
                  __QA_PREFERENCE_COMMIT__: performance.now() - start,
                }),
              );
            else requestAnimationFrame(committed);
          };
          requestAnimationFrame(committed);
        },
        { once: true, capture: true },
      );
    });
    await trigger.click();
    const dialog = page.getByRole('dialog', {
      name: 'Preferences',
      exact: true,
    });
    await expect(dialog).toBeVisible();
    await page.waitForFunction(
      () =>
        typeof (window as unknown as { __QA_PREFERENCE_COMMIT__: unknown })
          .__QA_PREFERENCE_COMMIT__ === 'number',
    );
    feedback.push(
      await page.evaluate(
        () =>
          (window as unknown as { __QA_PREFERENCE_COMMIT__: number })
            .__QA_PREFERENCE_COMMIT__,
      ),
    );
    const choice = dialog.getByRole('combobox', {
      name: 'Appearance',
      exact: true,
    });
    await choice.evaluate((element) => {
      Object.assign(window, { __QA_THEME_COMMIT__: null });
      element.addEventListener(
        'change',
        () => {
          const start = performance.now();
          requestAnimationFrame(() =>
            Object.assign(window, {
              __QA_THEME_COMMIT__: performance.now() - start,
            }),
          );
        },
        { once: true },
      );
    });
    const appearance = index % 2 ? 'light' : 'dark';
    await choice.selectOption(appearance);
    await expect(page.locator('html')).toHaveAttribute(
      'data-theme',
      appearance,
    );
    await page.waitForFunction(
      () =>
        typeof (window as unknown as { __QA_THEME_COMMIT__: unknown })
          .__QA_THEME_COMMIT__ === 'number',
    );
    theme.push(
      await page.evaluate(
        () =>
          (window as unknown as { __QA_THEME_COMMIT__: number })
            .__QA_THEME_COMMIT__,
      ),
    );
    await page.keyboard.press('Escape');
    await assertWorkspaceIdentity(page);
  }
  await writeEvidence(testInfo, 'PB02-PB04-real-workspace-feedback', {
    browser: browser.version(),
    feedback: { samples: feedback, ...distribution(feedback) },
    theme: { samples: theme, ...distribution(theme) },
    remounts: 0,
    excludedSamples: [],
    method:
      'Actual DOM click/change in HTTP workspace to committed Preferences/theme and animation frame; no fake clock.',
  });
  expect(distribution(feedback).p95).toBeLessThanOrEqual(100);
  expect(distribution(theme).p95).toBeLessThanOrEqual(100);
});

test('twenty real reconnects per latency condition restore confirmed snapshots and the draft', async ({
  page,
  context,
  browser,
}, testInfo) => {
  test.skip(
    testInfo.project.name !== 'chromium-desktop',
    'PB10 twenty-repeat loopback and injected-RTT calibration uses desktop Chromium.',
  );
  test.setTimeout(900_000);
  const conversation = await newConversation(page);
  await composer(page).fill('Draft retained through real reconnects');
  await expect(
    page.getByRole('status').filter({ hasText: /^Draft saved$/ }),
  ).toBeVisible();
  await markWorkspaceIdentity(page);
  const before = await fixtureState(page);
  let delay = 0;
  await page.route('**/api/v1/**', async (route) => {
    if (delay) await new Promise((resolve) => setTimeout(resolve, delay));
    await route.continue();
  });
  type RecoveryTrial = {
    index: number;
    elapsedMs: number;
    success: boolean;
    stage: string;
    failureType?: string;
    errors?: string[];
    external?: string[];
  };
  type RecoveryObservation = {
    imposedPerRequestDelayMs: number;
    samples: number[];
    trials: RecoveryTrial[];
    p50: number;
    p95: number;
    max: number;
    n: number;
  };
  const observations: RecoveryObservation[] = [];
  const frame = async (target: Page, remaining: () => number) => {
    await target.evaluate(() => {
      Object.assign(window, { __QA_RECOVERY_FRAME__: false });
      requestAnimationFrame(() =>
        Object.assign(window, { __QA_RECOVERY_FRAME__: true }),
      );
    });
    await target.waitForFunction(
      () =>
        (window as unknown as { __QA_RECOVERY_FRAME__: boolean })
          .__QA_RECOVERY_FRAME__,
      undefined,
      { timeout: remaining() },
    );
  };
  const writeFresh = () =>
    writeEvidence(testInfo, 'PB10-real-HTTP-reconnect', {
      browser: browser.version(),
      conversation,
      observations,
      admissionWaitMs: 5000,
      excludedSamples: [],
      method:
        'Actual offline/online, reauthentication and confirmed subscription snapshot plus visible frame. Each of20trials perRTT has a recorded5second admission refill wait outside timing, preserving existing30burst/2-per-second query cap and same session. Each trial has10second recovery deadline. Every attempt elapsed time and success/failure stage is retained; failed durations are not successful recovery latencies. Raw partials are persisted after every trial; final budgets are asserted only after fresh and expired families finish.',
    });
  for (const rtt of [0, 200]) {
    delay = rtt;
    const observation: RecoveryObservation = {
      imposedPerRequestDelayMs: rtt,
      samples: [],
      trials: [],
      p50: 0,
      p95: 0,
      max: 0,
      n: 0,
    };
    observations.push(observation);
    for (let index = 0; index < 20; index++) {
      await page.waitForTimeout(5000);
      let started = performance.now();
      let stage = 'offline preparation';
      let success = false;
      let failureType: string | undefined;
      try {
        await context.setOffline(true);
        await expect(
          page.getByRole('status').filter({ hasText: /^Connected$/ }),
        ).toHaveCount(0, { timeout: 10000 });
        const subscription = page
          .waitForResponse(
            (response) =>
              response.request().method() === 'POST' &&
              /\/conversations\/[^/]+\/subscriptions$/.test(
                new URL(response.url()).pathname,
              ) &&
              response.ok(),
            { timeout: 10000 },
          )
          .then(
            () => true,
            () => false,
          );
        started = performance.now();
        const remaining = () =>
          Math.max(1, 10000 - (performance.now() - started));
        stage = 'confirmed subscription';
        await context.setOffline(false);
        if (!(await subscription))
          throw new Error('Subscription recovery deadline');
        stage = 'connected draft and visible frame';
        await expect(
          page.getByRole('status').filter({ hasText: /^Connected$/ }),
        ).toBeVisible({ timeout: remaining() });
        await expect(composer(page)).toHaveValue(
          'Draft retained through real reconnects',
          { timeout: remaining() },
        );
        await frame(page, remaining);
        await assertWorkspaceIdentity(page);
        success = true;
        stage = 'complete';
      } catch (error) {
        failureType = error instanceof Error ? error.name : typeof error;
      } finally {
        const elapsedMs = performance.now() - started;
        observation.samples.push(elapsedMs);
        observation.trials.push({
          index,
          elapsedMs,
          success,
          stage,
          failureType,
        });
        Object.assign(observation, distribution(observation.samples));
        await context.setOffline(false);
        await writeFresh();
      }
    }
  }
  const freshProviderInvocations =
    (await fixtureState(page)).calls.length - before.calls.length;
  // Drain this page's delayed API handlers before navigation; context guards remain installed.
  await page.unrouteAll({ behavior: 'wait' });
  const expiredProviderCounts: number[] = [];
  const expired: {
    imposedPerRequestDelayMs: number;
    samples: number[];
    resetFlags: boolean[];
    trials: RecoveryTrial[];
    p50: number;
    p95: number;
    max: number;
    n: number;
  }[] = [];
  for (const rtt of [0, 200]) {
    const target = await newConversation(page);
    await composer(page).fill('timed exhaustion fixture');
    await page.getByRole('button', { name: 'Send', exact: true }).click();
    await expect
      .poll(async () =>
        (await fixtureState(page)).calls.some(
          (item) =>
            item.conversation_id === target && item.case === 'exhaustion',
        ),
      )
      .toBe(true);
    const call = (await fixtureState(page)).calls.find(
      (item) => item.conversation_id === target && item.case === 'exhaustion',
    )!;
    expect(call.conversation_id).toBe(target);
    await expect(composer(page)).toHaveValue('');
    const observers: {
      context: BrowserContext;
      page: Page;
      release: () => void;
      errors: string[];
      external: string[];
    }[] = [];
    const closedObservers = new Set<Page>();
    const closeObserver = async (observer: (typeof observers)[number]) => {
      if (closedObservers.has(observer.page)) return;
      observer.release();
      try {
        // Only this page's API handlers drain; the external-origin context guard remains.
        await observer.page.unrouteAll({ behavior: 'wait' });
      } finally {
        closedObservers.add(observer.page);
        await observer.context.close();
      }
    };
    const origin = new URL(page.url()).origin;
    const draft = `Saved draft retained through real expired history at ${rtt}ms`;
    try {
      for (let index = 0; index < 20; index++) {
        const observerContext = await browser.newContext({
          baseURL: origin,
          viewport: page.viewportSize(),
          serviceWorkers: 'block',
        });
        await observerContext.route(
          (url) =>
            ['http:', 'https:'].includes(url.protocol) && url.origin !== origin,
          (route) => route.abort(),
        );
        const observer = await observerContext.newPage();
        let release!: () => void;
        let entered!: () => void;
        const hold = new Promise<void>((resolve) => {
          release = resolve;
        });
        const ready = new Promise<void>((resolve) => {
          entered = resolve;
        });
        let first = true;
        let timed = false;
        await observer.route('**/api/v1/**', async (route) => {
          if (
            first &&
            new URL(route.request().url()).pathname === '/api/v1/events'
          ) {
            first = false;
            entered();
            await hold;
          }
          if (timed && rtt)
            await new Promise((resolve) => setTimeout(resolve, rtt));
          await route.continue();
        });
        await installResetProbe(observer);
        const errors: string[] = [],
          external: string[] = [];
        observer.on('pageerror', (error) => errors.push(error.name));
        observer.on('console', (event) => {
          if (event.type() === 'error') errors.push(event.text());
        });
        observer.on('request', (request) => {
          const url = new URL(request.url());
          if (
            ['http:', 'https:'].includes(url.protocol) &&
            url.origin !== origin
          )
            external.push(url.pathname);
        });
        observers.push({
          context: observerContext,
          page: observer,
          release: () => {
            timed = true;
            release();
          },
          errors,
          external,
        });
        await openConversation(observer, target);
        await ready;
        if (index === 0) {
          await composer(observer).fill(draft);
          await expect(
            observer.getByRole('status').filter({ hasText: /^Draft saved$/ }),
          ).toBeVisible();
        }
        await expect(composer(observer)).toHaveValue(draft);
        await markWorkspaceIdentity(observer);
      }
      await startExpiryProducer(page, call);
      await expect
        .poll(
          async () =>
            (await fixtureState(page)).calls.find(
              (item) => item.generation_id === call.generation_id,
            )?.emitted_tokens,
          { timeout: 90_000 },
        )
        .toBe(5000);
      await releaseProducer(page, call);
      await expect
        .poll(
          async () =>
            (await fixtureState(page)).calls.find(
              (item) => item.generation_id === call.generation_id,
            )?.quiesced,
        )
        .toBe(true);
      const samples: number[] = [],
        resetFlags: boolean[] = [],
        trials: RecoveryTrial[] = [];
      const observation = {
        imposedPerRequestDelayMs: rtt,
        samples,
        resetFlags,
        trials,
        p50: 0,
        p95: 0,
        max: 0,
        n: 0,
      };
      expired.push(observation);
      for (const [index, observer] of observers.entries()) {
        await observer.page.bringToFront();
        const started = performance.now();
        const remaining = () =>
          Math.max(1, 10000 - (performance.now() - started));
        let stage = 'actual snapshot_required';
        let success = false;
        let reset = false;
        let failureType: string | undefined;
        try {
          observer.release();
          await observer.page.waitForFunction(
            () =>
              (window as unknown as { __QA_RESET_COUNT__: number })
                .__QA_RESET_COUNT__ > 0,
            undefined,
            { timeout: remaining() },
          );
          reset = true;
          stage = 'confirmed final draft and visible frame';
          await expect(
            observer.page
              .getByRole('status')
              .filter({ hasText: /^Connected$/ }),
          ).toBeVisible({ timeout: remaining() });
          await expect(
            observer.page
              .getByRole('log', { name: 'Conversation', exact: true })
              .locator('.message-text')
              .filter({ hasText: 'Exhaustion settled.' }),
          ).toHaveCount(1, { timeout: remaining() });
          await expect(composer(observer.page)).toHaveValue(draft, {
            timeout: remaining(),
          });
          await frame(observer.page, remaining);
          await assertWorkspaceIdentity(observer.page);
          expect(observer.errors).toEqual([]);
          expect(observer.external).toEqual([]);
          success = true;
          stage = 'complete';
        } catch (error) {
          failureType = error instanceof Error ? error.name : typeof error;
        } finally {
          const elapsedMs = performance.now() - started;
          samples.push(elapsedMs);
          resetFlags.push(reset);
          trials.push({
            index,
            elapsedMs,
            success,
            stage,
            failureType,
            errors: [...observer.errors],
            external: [...observer.external],
          });
          Object.assign(observation, distribution(samples));
          await writeEvidence(testInfo, 'PB10-real-expired-replay-partial', {
            observations: expired,
            expectedTrialsPerCondition: 20,
            failedDurationsAreRecoveryLatencies: false,
          });
          await closeObserver(observer);
        }
      }
      const providerCount = (await fixtureState(page)).calls.filter(
        (item) => item.conversation_id === target,
      ).length;
      expiredProviderCounts.push(providerCount);
      await writeEvidence(testInfo, `PB10-expired-provider-count-${rtt}`, {
        count: providerCount,
        expected: 1,
      });
    } finally {
      for (const observer of observers) {
        await closeObserver(observer);
      }
      if (
        !(await fixtureState(page)).calls.find(
          (item) => item.generation_id === call.generation_id,
        )?.quiesced
      )
        await releaseProducer(page, call);
    }
  }
  await writeEvidence(testInfo, 'PB10-real-expired-replay', {
    browser: browser.version(),
    observations: expired,
    excludedSamples: [],
    method:
      'Twenty distinct real sessions/subscriptions prepared before each actual5000-delta native producer batch; initial SSE requests held without entering server streams; native retained history expires. After durable final/quiescence, recover observers serially from release of held request to actual snapshot_required plus confirmed final/draft and visible frame. Delay applies per API request only after timed release; initial preparation/hold is excluded by definition, no observed recovery sample is excluded. Other nineteen prepared observers remain dormant. No cap change, forged cursor or client-state injection.',
  });
  expect(freshProviderInvocations).toBe(0);
  expect(expiredProviderCounts).toEqual([1, 1]);
  for (const observation of observations) {
    expect(observation.n).toBe(20);
    expect(observation.trials).toHaveLength(20);
    expect(observation.trials.every((trial) => trial.success)).toBe(true);
    expect(observation.p95).toBeLessThanOrEqual(
      1000 + observation.imposedPerRequestDelayMs,
    );
  }
  for (const observation of expired) {
    expect(observation.trials).toHaveLength(20);
    expect(observation.trials.every((trial) => trial.success)).toBe(true);
    expect(observation.n).toBe(20);
    expect(observation.resetFlags.every(Boolean)).toBe(true);
    expect(observation.p95).toBeLessThanOrEqual(
      1000 + observation.imposedPerRequestDelayMs,
    );
  }
});
