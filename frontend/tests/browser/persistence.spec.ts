import { test, expect, screenshot, writeEvidence } from './evidence';
import {
  openFixture,
  type FixtureWindow,
  stableConversationMarker,
  assertConversationMarker,
} from './fixture';
import { openPanel, readLayout } from './panel-helpers';

test('an open panel and its instance survive a same-size browser refresh', async ({
  page,
}, testInfo) => {
  await openFixture(page);
  await openPanel(page, 'Activity preview');
  const before = await readLayout(page);
  await page.reload();
  await expect(
    page
      .locator('.sample-panel:visible')
      .getByRole('heading', { name: 'Activity preview', exact: true }),
  ).toBeVisible();
  const after = await readLayout(page);
  expect(after.panels).toEqual(before.panels);
  expect(after.activePanelId).toBe(before.activePanelId);
  expect(
    await page.evaluate(
      () =>
        (window as FixtureWindow).__ROW_BOT_FIXTURE__.transport.counters
          .commands,
    ),
  ).toBe(0);
  await screenshot(page, testInfo, 'panel-restored-after-refresh');
  await writeEvidence(testInfo, 'same-size-panel-persistence', {
    before,
    after,
  });
});

test('compact Back stays on the conversation after refresh while retaining the panel', async ({
  page,
}, testInfo) => {
  test.skip(
    testInfo.project.use.viewport!.width >= 1024,
    'Compact tab Back persistence applies below the desktop breakpoint.',
  );
  await openFixture(page);
  await openPanel(page, 'Activity preview');
  const before = await readLayout(page);
  await page
    .getByRole('button', { name: 'Back to conversation', exact: true })
    .click();
  await expect(page.getByTestId('conversation-workspace')).toBeVisible();
  await expect
    .poll(async () => (await readLayout(page)).activePanelId)
    .toBeNull();
  await page.reload();
  await expect(page.getByTestId('conversation-workspace')).toBeVisible();
  await expect(
    page.getByRole('region', { name: 'Compact panel', exact: true }),
  ).toHaveCount(0);
  const after = await readLayout(page);
  expect(after.activePanelId).toBeNull();
  expect(after.panels).toEqual(before.panels);
  await screenshot(page, testInfo, 'compact-back-persisted');
  await writeEvidence(testInfo, 'compact-back-persistence', { before, after });
});

test('version-zero layout migrates, clamps obsolete sizes and resets only after confirmation', async ({
  page,
}, testInfo) => {
  const width = testInfo.project.use.viewport!.width;
  const size = width >= 1024 ? 'desktop' : width >= 768 ? 'tablet' : 'phone';
  await page.addInitScript((size) => {
    const key = `row-bot:layout:v1:local:${size}`;
    if (!localStorage.getItem(key))
      localStorage.setItem(
        key,
        JSON.stringify({
          version: 0,
          navigation: 278,
          side: 99999,
          bottom: 1,
          panels: [
            {
              instance_id: 'panel-7',
              descriptor: {
                panel_kind: 'fake.activity',
                title: 'Migrated activity',
              },
              placement: 'side',
              visibility: 'visible',
            },
          ],
          activePanelId: 'panel-7',
        }),
      );
  }, size);
  await page.goto('/app-v2/conversations/conversation-a?fixture=normal');
  await expect(
    page
      .locator('.sample-panel:visible')
      .getByRole('heading', { name: 'Migrated activity', exact: true }),
  ).toBeVisible();
  const migrated = await readLayout(page);
  expect(migrated.version).toBe(2);
  expect(migrated.navigation.size).toBe(278);
  expect(migrated.side.size).toBeGreaterThanOrEqual(320);
  expect(migrated.side.size).toBeLessThanOrEqual(720);
  expect(migrated.bottom.size).toBe(160);
  expect(migrated.panels[0].instance_id).toBe('panel-7');
  await screenshot(page, testInfo, 'version-zero-layout-migrated');
  await page.getByRole('button', { name: 'Preferences', exact: true }).click();
  await page.getByRole('button', { name: 'Reset layout', exact: true }).click();
  expect((await readLayout(page)).panels).toHaveLength(1);
  await page
    .getByRole('alertdialog', { name: 'Reset layout?', exact: true })
    .getByRole('button', { name: 'Reset layout', exact: true })
    .click();
  await expect.poll(async () => (await readLayout(page)).panels.length).toBe(0);
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('conversation-workspace')).toBeVisible();
  const reset = await readLayout(page);
  expect(reset.navigation.size).toBe(240);
  expect(
    await page.evaluate(
      () =>
        (window as FixtureWindow).__ROW_BOT_FIXTURE__.transport.counters
          .commands,
    ),
  ).toBe(0);
  await writeEvidence(testInfo, 'migration-and-explicit-reset', {
    migrated,
    reset,
    producerCommands: 0,
  });
});

test.describe('browser lifecycle', () => {
  // Routing intercepts all WebKit requests internally, including unload keepalive.
  // Actual browser lifecycle checks use the host's strict CSP and observation.
  test.use({ nativeNetwork: true });

  test('persisted page suspension resumes one observer without disposing the workspace', async ({
    page,
  }, testInfo) => {
    await openFixture(page);
    if (testInfo.project.use.viewport!.width < 1024)
      await page
        .getByRole('button', { name: 'Toggle navigation', exact: true })
        .click();
    await page
      .getByRole('button', { name: 'A place for your ideas', exact: true })
      .click();
    await expect
      .poll(() =>
        page.evaluate(
          () =>
            (window as FixtureWindow).__ROW_BOT_FIXTURE__.transport.counters
              .streams,
        ),
      )
      .toBe(1);
    await stableConversationMarker(page);
    await page.evaluate(() =>
      window.dispatchEvent(
        new PageTransitionEvent('pagehide', { persisted: true }),
      ),
    );
    await expect
      .poll(() =>
        page.evaluate(
          () =>
            (window as FixtureWindow).__ROW_BOT_FIXTURE__.transport.counters
              .streams,
        ),
      )
      .toBe(0);
    await page.evaluate(() =>
      window.dispatchEvent(
        new PageTransitionEvent('pageshow', { persisted: true }),
      ),
    );
    await expect
      .poll(() =>
        page.evaluate(
          () =>
            (window as FixtureWindow).__ROW_BOT_FIXTURE__.transport.counters
              .streams,
        ),
      )
      .toBe(1);
    const result = await page.evaluate(() => {
      const { controller, transport } = (window as FixtureWindow)
        .__ROW_BOT_FIXTURE__;
      return {
        status: controller.getSnapshot().status,
        selected: controller.getSnapshot().selectedConversationId,
        counters: transport.counters,
      };
    });
    expect(result.status).toBe('ready');
    expect(result.selected).toBe('conversation-a');
    expect(result.counters.active).toBe(1);
    expect(result.counters.commands).toBe(0);
    await assertConversationMarker(page);
    await writeEvidence(testInfo, 'persisted-pagehide-pageshow', {
      ...result,
      method:
        'Explicit persisted PageTransitionEvents exercise the page lifecycle contract; actual browser BFCache eligibility is recorded separately.',
    });
  });

  test('actual browser Back returns to a usable real workspace', async ({
    page,
    request,
  }, testInfo) => {
    let acceptedSubscriptions = 0;
    const cleanupResponses: { status: number; path: string }[] = [];
    page.on('response', (response) => {
      const path = new URL(response.url()).pathname;
      if (
        response.request().method() === 'POST' &&
        path.startsWith('/api/v1/conversations/') &&
        path.endsWith('/subscriptions') &&
        response.status() === 200
      )
        acceptedSubscriptions += 1;
      if (
        response.request().method() === 'DELETE' &&
        path.startsWith('/api/v1/subscriptions/')
      )
        cleanupResponses.push({ status: response.status(), path });
    });
    await page.addInitScript(() => {
      Object.assign(window, { __QA_PAGE_SHOW__: [] as boolean[] });
      window.addEventListener('pageshow', (event) => {
        (
          window as Window & typeof globalThis & { __QA_PAGE_SHOW__: boolean[] }
        ).__QA_PAGE_SHOW__.push(event.persisted);
      });
    });
    await page.goto('/app-v2/');
    await expect(
      page.getByRole('heading', { name: 'Home', exact: true }),
    ).toBeVisible();
    const compact = testInfo.project.use.viewport!.width < 1024;
    if (compact)
      await page
        .getByRole('button', { name: 'Toggle navigation', exact: true })
        .click();
    const initialSubscription = page.waitForResponse(
      (response) =>
        response.request().method() === 'POST' &&
        new URL(response.url()).pathname ===
          '/api/v1/conversations/p1-browser-a/subscriptions' &&
        response.status() === 200,
    );
    await page
      .getByRole('button', { name: 'Phase 1 conversation A', exact: true })
      .click();
    await expect(
      page.getByRole('heading', {
        name: 'Phase 1 conversation A',
        exact: true,
      }),
    ).toBeVisible();
    await expect.poll(() => acceptedSubscriptions).toBe(1);
    const originalResponse = await initialSubscription;
    const originalHeaders = await originalResponse.request().allHeaders();
    const original = (await originalResponse.json()) as {
      subscription_id: string;
      cursor: string;
    };
    // Synthetic session proof stays in this closure and is never logged or attached.
    const originalProof = {
      'x-client-session': originalHeaders['x-client-session'],
      'x-csrf-token': originalHeaders['x-csrf-token'],
    };
    await page.goto('/readyz');
    await page.goBack();
    await expect(page.getByTestId('conversation-workspace')).toBeVisible();
    await expect(page.locator('.connection-status')).toHaveText('Connected');
    const pageShows = await page.evaluate(
      () =>
        (window as Window & typeof globalThis & { __QA_PAGE_SHOW__: boolean[] })
          .__QA_PAGE_SHOW__,
    );
    if (pageShows.includes(true)) {
      await expect(
        page.getByRole('heading', {
          name: 'Phase 1 conversation A',
          exact: true,
        }),
      ).toBeVisible();
    }
    if (compact)
      await page
        .getByRole('button', { name: 'Toggle navigation', exact: true })
        .click();
    await expect(
      page.getByRole('button', { name: 'Phase 1 conversation B', exact: true }),
    ).toBeVisible();
    const response = await request.get('/__p1_fixture/state', {
      headers: {
        'x-fixture-token': process.env.ROW_BOT_BROWSER_CONTROL_TOKEN!,
      },
    });
    expect(response.ok()).toBe(true);
    const state = (await response.json()) as {
      calls: unknown[];
      external_calls: number;
    };
    expect(state.calls).toEqual([]);
    expect(state.external_calls).toBe(0);
    let cleanupProbe = { status: 0, code: '' };
    await expect
      .poll(
        async () => {
          const reply = await request.get('/api/v1/events/poll', {
            headers: originalProof,
            params: {
              subscription_id: original.subscription_id,
              cursor: original.cursor,
            },
          });
          const body = (await reply.json()) as { code?: string };
          cleanupProbe = { status: reply.status(), code: body.code ?? '' };
          return cleanupProbe;
        },
        {
          message:
            'Old synthetic subscription must be released after actual Back',
        },
      )
      .toEqual({ status: 404, code: 'not_found' });
    expect(cleanupResponses.every((response) => response.status === 200)).toBe(
      true,
    );
    await writeEvidence(testInfo, 'actual-browser-back', {
      pageShows,
      bfcacheUsed: pageShows.includes(true),
      state,
      acceptedSubscriptions,
      cleanupResponses,
      cleanupProbe,
      cleanupProof:
        'The original subscription returns404/not_found under its original privately held session proof after Back, proving server metadata release.',
      cleanupObservation: cleanupResponses.length
        ? 'Observed terminal DELETE responses are HTTP200.'
        : 'No terminal DELETE response reached the old page; unload delivery remains browser-controlled.',
    });
  });
});
