import { performance } from 'node:perf_hooks';
import type { Page } from '@playwright/test';
import { test, expect, distribution, writeEvidence } from './evidence';
import {
  composer,
  openConversation,
  seedLargeLibrary,
} from './unified-helpers';
import { openFixture, type FixtureWindow } from './fixture';

// Pinning is reviewed in the Manage conversation dialog.
async function pinOpenConversation(page: Page): Promise<void> {
  await page
    .getByRole('button', { name: 'Conversation actions', exact: true })
    .click();
  await page
    .getByRole('menuitem', { name: 'Manage conversation', exact: true })
    .click();
  // The dialog is named after the conversation (B237).
  const dialog = page.getByRole('dialog');
  const pin = dialog.getByRole('switch', { name: 'Pin', exact: true });
  // Pinning persists in the shared fixture; an earlier run may have pinned it.
  await expect(pin).toBeVisible();
  if (!(await pin.isChecked())) await pin.click();
  await expect(pin).toBeChecked();
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
}

test('twenty warm thousand-row opens and ten-thousand-row retained heap meet bounded transcript budgets', async ({
  page,
  context,
  browser,
}, testInfo) => {
  test.skip(
    testInfo.project.name !== 'chromium-desktop',
    'PB03/PB07 same-process GC calibration is scoped to desktop Chromium; functional large-history cases run all viewports.',
  );
  test.setTimeout(240_000);
  await openConversation(page);
  const fixture = await seedLargeLibrary(page);
  await openConversation(page, fixture.short_conversation_id);
  await pinOpenConversation(page);
  await openConversation(page, 'p1-browser-a');
  await pinOpenConversation(page);
  const target = page
    .getByRole('navigation', { name: 'Workspace navigation' })
    .getByRole('button', { name: 'Library conversation 0001', exact: true });
  const other = page
    .getByRole('navigation', { name: 'Workspace navigation' })
    .getByRole('button', { name: 'Phase 1 conversation A', exact: true });
  const marker = page.locator(
    `[role="log"] [data-message-id="${fixture.short_last_message_id}"]`,
  );
  const warm: number[] = [];
  const visibleAnchors: { messageId: string; width: number; height: number }[] =
    [];
  for (let index = 0; index < 20; index++) {
    await other.click();
    await expect(marker).toHaveCount(0);
    await target.evaluate((element, id) => {
      Object.assign(window, {
        __QA_THREAD_OPEN__: null,
        __QA_THREAD_ANCHOR__: null,
      });
      element.addEventListener(
        'click',
        () => {
          const start = performance.now();
          const visibleRow = () => {
            const log = document.querySelector('[role="log"]');
            if (!log?.querySelector(`[data-message-id="${id}"]`)) return null;
            const clip = log.getBoundingClientRect();
            for (const row of log.querySelectorAll<HTMLElement>(
              `[data-message-id="${id}"]`,
            )) {
              const text = row.querySelector<HTMLElement>('.message-text');
              if (!text?.textContent?.trim()) continue;
              const box = text.getBoundingClientRect();
              const left = Math.max(0, clip.left, box.left),
                right = Math.min(innerWidth, clip.right, box.right);
              const top = Math.max(0, clip.top, box.top),
                bottom = Math.min(innerHeight, clip.bottom, box.bottom);
              if (
                right - left >= 40 &&
                bottom - top >= 24 &&
                row.contains(
                  document.elementFromPoint(
                    (left + right) / 2,
                    (top + bottom) / 2,
                  ),
                )
              )
                return {
                  messageId: row.dataset.messageId!,
                  width: right - left,
                  height: bottom - top,
                };
            }
            return null;
          };
          const committed = () => {
            if (visibleRow())
              requestAnimationFrame(() => {
                const anchor = visibleRow();
                if (anchor)
                  Object.assign(window, {
                    __QA_THREAD_OPEN__: performance.now() - start,
                    __QA_THREAD_ANCHOR__: anchor,
                  });
                else requestAnimationFrame(committed);
              });
            else requestAnimationFrame(committed);
          };
          requestAnimationFrame(committed);
        },
        { once: true, capture: true },
      );
    }, fixture.short_last_message_id);
    await target.click();
    await expect(marker).toBeVisible();
    await page.waitForFunction(
      () =>
        typeof (window as unknown as { __QA_THREAD_OPEN__: unknown })
          .__QA_THREAD_OPEN__ === 'number',
    );
    warm.push(
      await page.evaluate(
        () =>
          (window as unknown as { __QA_THREAD_OPEN__: number })
            .__QA_THREAD_OPEN__,
      ),
    );
    const anchor = await page.evaluate(
      () =>
        (
          window as unknown as {
            __QA_THREAD_ANCHOR__: {
              messageId: string;
              width: number;
              height: number;
            };
          }
        ).__QA_THREAD_ANCHOR__,
    );
    expect(anchor.messageId).toBe(fixture.short_last_message_id);
    expect(anchor.width).toBeGreaterThanOrEqual(40);
    expect(anchor.height).toBeGreaterThanOrEqual(24);
    visibleAnchors.push(anchor);
    await expect(composer(page)).toBeEnabled();
  }
  const cdp = await context.newCDPSession(page);
  const renderedShapes = async () => {
    const log = page.getByRole('log', { name: 'Conversation', exact: true });
    // Tool calls and their results fold into one activity row per turn.
    const shapes = {
      toolResults: await log.locator('.activity-row').count(),
      toolCalls: await log
        .locator('summary.activity-summary')
        .filter({ hasText: /^Used 1 tool/ })
        .count(),
      mediaPlaceholders: await log
        .locator('.message-text')
        .filter({
          hasText: '[Synthetic media placeholder: bounded image output]',
        })
        .count(),
    };
    expect(shapes.toolResults).toBeGreaterThan(0);
    expect(shapes.toolCalls).toBeGreaterThan(0);
    expect(shapes.mediaPlaceholders).toBeGreaterThan(0);
    return shapes;
  };
  const shapes1000 = await renderedShapes();
  await cdp.send('HeapProfiler.collectGarbage');
  const baseline = await cdp.send('Runtime.getHeapUsage');
  const rows1000 = await page.locator('[role="log"] [data-message-id]').count();
  const hydrationStart = performance.now();
  await openConversation(page, fixture.conversation_id);
  await expect(
    page
      .getByRole('log', { name: 'Conversation' })
      .getByText('History row 10000', { exact: true }),
  ).toBeVisible();
  const hydrationMs = performance.now() - hydrationStart;
  await cdp.send('HeapProfiler.collectGarbage');
  const large = await cdp.send('Runtime.getHeapUsage');
  const rows10000 = await page
    .locator('[role="log"] [data-message-id]')
    .count();
  const shapes10000 = await renderedShapes();
  expect(shapes10000).toEqual(shapes1000);
  const delta = large.usedSize - baseline.usedSize;
  await writeEvidence(testInfo, 'PB03-PB07-real-history', {
    browser: browser.version(),
    viewport: page.viewportSize(),
    dpr: await page.evaluate(() => devicePixelRatio),
    warm: { samples: warm, ...distribution(warm) },
    hydrationMs,
    visibleAnchors,
    heap: { baseline, large, delta, limit: 100 * 1024 * 1024 },
    mountedRows: { rows1000, rows10000 },
    renderedShapes: { shapes1000, shapes10000 },
    excludedSamples: [],
    method:
      'Twenty real sidebar clicks to confirmed target window and its exact newest stable-ID nonempty text row intersecting transcript/viewport with >=24px readable height, >=40px width and actual hit-test, confirmed again next frame; each stable anchor ID and geometry recorded; same desktop browser target, isolated data, native checkpoints with identical latest-window text/tool-call/tool-result/safe media-placeholder shapes; placeholders are text blocks, not decoded images or durable inline-image replay; test-only forced GC.',
  });
  await cdp.detach();
  expect(distribution(warm).p95).toBeLessThanOrEqual(300);
  expect(rows10000).toBeLessThanOrEqual(200);
  expect(delta).toBeLessThanOrEqual(100 * 1024 * 1024);
});

test('one-thousand and ten-thousand row protocol continuation stays bounded', async ({
  page,
  context,
  browser,
}, testInfo) => {
  test.skip(
    testInfo.project.name !== 'chromium-desktop',
    'Phase2 retained-cache calibration uses explicit Chromium page GC; full transcript rendering/search remains Phase3.',
  );
  await openFixture(page);
  const cdp = await context.newCDPSession(page);
  const workloads: {
    count: number;
    pages: number;
    delivered: number;
    uniqueRowsSeen: number;
    maxRetained: number;
    heapUsed: number;
    mountedTranscriptRows: number;
    placeholderCount: number;
  }[] = [];
  for (const count of [1000, 10000] as const) {
    await page.evaluate(async (size) => {
      const fixture = (window as FixtureWindow).__ROW_BOT_FIXTURE__;
      fixture.transport.setTranscriptSize(size);
      await fixture.controller.selectConversation('conversation-a');
    }, count);
    await expect
      .poll(() =>
        page.evaluate(
          () =>
            (window as FixtureWindow).__ROW_BOT_FIXTURE__.transport.counters
              .streams,
        ),
      )
      .toBe(1);
    const visited = new Set<string>();
    let maxRetained = 0;
    let hasMore = true;
    let steps = 0;
    while (hasMore) {
      const current = await page.evaluate(() => {
        const state = (
          window as FixtureWindow
        ).__ROW_BOT_FIXTURE__.controller.getSnapshot();
        return {
          ids: state.projection?.rows.map((row) => row.id) ?? [],
          hasMore: state.hasMoreTranscript,
          status: state.status,
        };
      });
      expect(current.status).toBe('ready');
      expect(current.ids.length).toBeLessThanOrEqual(200);
      maxRetained = Math.max(maxRetained, current.ids.length);
      current.ids.forEach((id) => visited.add(id));
      hasMore = current.hasMore;
      if (hasMore)
        await page.evaluate(() =>
          (
            window as FixtureWindow
          ).__ROW_BOT_FIXTURE__.controller.loadMoreTranscript(),
        );
      steps += 1;
      expect(steps).toBeLessThanOrEqual(count / 100 + 1);
    }
    await cdp.send('HeapProfiler.collectGarbage');
    const heap = await cdp.send('Runtime.getHeapUsage');
    const result = await page.evaluate(() => ({
      pages: (window as FixtureWindow).__ROW_BOT_FIXTURE__.transport.counters
        .transcriptPages,
      delivered: (window as FixtureWindow).__ROW_BOT_FIXTURE__.transport
        .counters.transcriptRowsDelivered,
      mountedTranscriptRows: document.querySelectorAll('[data-transcript-row]')
        .length,
      placeholderCount: document.querySelectorAll(
        '[data-testid="conversation-workspace"]',
      ).length,
    }));
    expect(visited.size).toBe(count);
    expect(result.delivered).toBe(count);
    expect(result.pages).toBe(count / 100);
    expect(result.mountedTranscriptRows).toBe(0);
    expect(result.placeholderCount).toBe(1);
    workloads.push({
      count,
      ...result,
      uniqueRowsSeen: visited.size,
      maxRetained,
      heapUsed: heap.usedSize,
    });
  }
  const heapDelta = workloads[1].heapUsed - workloads[0].heapUsed;
  await writeEvidence(testInfo, 'PB07-foundation-cache-calibration', {
    browserVersion: browser.version(),
    workloads,
    heapDelta,
    method:
      'Every100-row continuation visited; synthetic fixture generates each page without holding10k source rows; retained browser heap after test-onlyGC. One unchanged conversation placeholder and0 rendered transcript rows.',
    limitation:
      'This proves bounded protocol cache and cursor reachability only. Anchored10k transcript DOM, full-history search/copy, media/tool-row rendering and PB03 thread-open UX are Phase3 gates.',
  });
  expect(heapDelta).toBeLessThanOrEqual(100 * 1024 * 1024);
});
