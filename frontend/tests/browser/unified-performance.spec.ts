import {
  test,
  expect,
  distribution,
  writeEvidence,
  assertNoOverflow,
} from './evidence';
import {
  advanceCadence,
  addReviewResourcePair,
  captureActualResourcePanels,
  blockFixtureServiceWorkers,
  composer,
  fixtureState,
  markWorkspaceIdentity,
  assertWorkspaceIdentity,
  newConversation,
  releaseProducer,
} from './unified-helpers';
import {
  openFixture,
  stableConversationMarker,
  assertConversationMarker,
  type FixtureWindow,
} from './fixture';
import { openPanel } from './panel-helpers';

type TokenGeometry = {
  token: string;
  time: number;
  rect: { left: number; top: number; right: number; bottom: number } | null;
  clip: { left: number; top: number; right: number; bottom: number };
  visible: boolean;
  hit: boolean;
  pointerHeld: boolean;
  suppression: {
    tag: string;
    id: string;
    classes: string;
    pointerEvents: string;
    opacity: string;
    inlinePointerEvents: string;
  }[];
  segments: {
    left: number;
    top: number;
    right: number;
    bottom: number;
    clippedVisible: boolean;
    normalHit: boolean;
    suppressedAncestorHit: boolean;
    target: { tag: string; id: string; classes: string } | null;
  }[];
};
type TokenCommit = {
  id: string;
  received: number;
  domObserved: number;
  committed: number;
  latency: number;
  visible: boolean;
  frames: TokenGeometry[];
};
type Cadence = {
  active: boolean;
  started: number;
  ended: number | null;
  frames: number[];
  feedback: number[];
  paneCommits: {
    time: number;
    feedback: number;
    width: number;
    ariaValue: string | null;
  }[];
  longTasks: number[];
  callbacks: number;
  streamCommits: TokenCommit[];
  windows: {
    index: number;
    started: number;
    ended: number | null;
    frames: number[];
  }[];
  beginWindow: (index: number) => void;
  endWindow: () => void;
  stop: () => void;
};
type CadenceWindow = Window & { __QA_CADENCE__: Cadence };

test.use({ serviceWorkers: 'allow' });
test.beforeEach(async ({ context }) => blockFixtureServiceWorkers(context));

test('real transcript stream and responsive resize retain literal frame-cadence and feedback budgets', async ({
  page,
  browser,
}, testInfo) => {
  await page.addInitScript(() => {
    let pointerHeld = false;
    document.addEventListener(
      'pointerdown',
      (event) => {
        pointerHeld = event.buttons === 1;
      },
      true,
    );
    document.addEventListener(
      'pointerup',
      () => {
        pointerHeld = false;
      },
      true,
    );
    document.addEventListener(
      'pointercancel',
      () => {
        pointerHeld = false;
      },
      true,
    );
    const arrivals = new Map<string, number>();
    const scheduled = new Set<string>();
    const commits: TokenCommit[] = [];
    const readers = new WeakMap<
      object,
      { decoder: TextDecoder; carry: string }
    >();
    const read = ReadableStreamDefaultReader.prototype.read;
    ReadableStreamDefaultReader.prototype.read = function (...args) {
      return read.apply(this, args).then((result) => {
        if (result.value instanceof Uint8Array) {
          const state = readers.get(this) ?? {
            decoder: new TextDecoder(),
            carry: '',
          };
          const text =
            state.carry + state.decoder.decode(result.value, { stream: true });
          for (const match of text.matchAll(/Token (\d{3})\./g))
            if (Number(match[1]) < 40 && !arrivals.has(match[1]))
              arrivals.set(match[1], performance.now());
          state.carry = text.slice(-64);
          readers.set(this, state);
        }
        if (result.done) readers.delete(this);
        return result;
      });
    };
    const geometry = (id: string): TokenGeometry => {
      const token = `Token ${id}.`;
      const log = document.querySelector('[role="log"]');
      const clip = { left: 0, top: 0, right: innerWidth, bottom: innerHeight };
      const absent: TokenGeometry = {
        token,
        time: performance.now(),
        rect: null,
        clip,
        visible: false,
        hit: false,
        pointerHeld,
        suppression: [],
        segments: [],
      };
      if (!log) return absent;
      const nodes = document.createTreeWalker(log, NodeFilter.SHOW_TEXT);
      let node: Node | null;
      while ((node = nodes.nextNode())) {
        const index = node.textContent?.indexOf(token) ?? -1;
        if (index < 0) continue;
        const range = document.createRange();
        range.setStart(node, index);
        range.setEnd(node, index + token.length);
        const box = range.getBoundingClientRect();
        const fragments = [...range.getClientRects()].filter(
          (rect) => rect.width > 0 && rect.height > 0,
        );
        const suppression: TokenGeometry['suppression'] = [];
        let panelPointerSuppressed = false;
        for (
          let element = node.parentElement;
          element;
          element = element.parentElement
        ) {
          const style = getComputedStyle(element);
          if (
            style.visibility !== 'visible' ||
            style.display === 'none' ||
            Number(style.opacity) <= 0
          )
            return absent;
          if (style.pointerEvents === 'none') {
            suppression.push({
              tag: element.tagName,
              id: element.id,
              classes: element.className,
              pointerEvents: style.pointerEvents,
              opacity: style.opacity,
              inlinePointerEvents: element.style.pointerEvents,
            });
            if (
              element.hasAttribute('data-panel') &&
              element.style.pointerEvents === 'none'
            )
              panelPointerSuppressed = true;
          }
          const bounds = element.getBoundingClientRect();
          const scaleX = element.offsetWidth
            ? bounds.width / element.offsetWidth
            : 1;
          const scaleY = element.offsetHeight
            ? bounds.height / element.offsetHeight
            : 1;
          if (/(hidden|clip|auto|scroll)/.test(style.overflowX)) {
            clip.left = Math.max(
              clip.left,
              bounds.left + element.clientLeft * scaleX,
            );
            clip.right = Math.min(
              clip.right,
              bounds.left + (element.clientLeft + element.clientWidth) * scaleX,
            );
          }
          if (/(hidden|clip|auto|scroll)/.test(style.overflowY)) {
            clip.top = Math.max(
              clip.top,
              bounds.top + element.clientTop * scaleY,
            );
            clip.bottom = Math.min(
              clip.bottom,
              bounds.top + (element.clientTop + element.clientHeight) * scaleY,
            );
          }
        }
        const segments = fragments.map((rect) => {
          const target = document.elementFromPoint(
            (rect.left + rect.right) / 2,
            (rect.top + rect.bottom) / 2,
          );
          const normalHit = !!target && !!node!.parentElement?.contains(target);
          // The reviewed panel library disables pointer events on its painted
          // panels during a real drag. Only their containing ancestor may be
          // returned instead; unrelated/opaque overlays are never exempted.
          const suppressedAncestorHit =
            pointerHeld && panelPointerSuppressed && !!target?.contains(node);
          return {
            left: rect.left,
            top: rect.top,
            right: rect.right,
            bottom: rect.bottom,
            clippedVisible:
              rect.left >= clip.left &&
              rect.right <= clip.right &&
              rect.top >= clip.top &&
              rect.bottom <= clip.bottom,
            normalHit,
            suppressedAncestorHit,
            target: target
              ? {
                  tag: target.tagName,
                  id: target.id,
                  classes: target.className,
                }
              : null,
          };
        });
        const hit =
          segments.length > 0 &&
          segments.every(
            (segment) => segment.normalHit || segment.suppressedAncestorHit,
          );
        return {
          token,
          time: performance.now(),
          rect: {
            left: box.left,
            top: box.top,
            right: box.right,
            bottom: box.bottom,
          },
          clip,
          hit,
          pointerHeld,
          suppression,
          segments,
          visible: hit && segments.every((segment) => segment.clippedVisible),
        };
      }
      return absent;
    };
    addEventListener('DOMContentLoaded', () => {
      const observer = new MutationObserver(() => {
        const text = document.querySelector('[role="log"]')?.textContent ?? '';
        for (const [id, received] of arrivals) {
          if (scheduled.has(id) || !text.includes(`Token ${id}.`)) continue;
          scheduled.add(id);
          const domObserved = performance.now();
          const frames: TokenGeometry[] = [];
          const measure = () => {
            const frame = geometry(id);
            frames.push(frame);
            if (!frame.visible && frame.time - received < 1000) {
              requestAnimationFrame(measure);
              return;
            }
            commits.push({
              id,
              received,
              domObserved,
              committed: frame.time,
              latency: frame.time - received,
              visible: frame.visible,
              frames,
            });
          };
          requestAnimationFrame(measure);
        }
      });
      observer.observe(document.body, {
        subtree: true,
        childList: true,
        characterData: true,
      });
    });
    Object.assign(window, {
      __QA_STREAM_COMMITS__: commits,
      __QA_STREAM_ARRIVALS__: () =>
        [...arrivals].map(([id, received]) => ({ id, received })),
    });
  });
  const conversation = await newConversation(page);
  const viewport = page.viewportSize()!;
  const desktop = viewport.width >= 1024;
  await composer(page).fill('cadence fixture');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(
    page.getByText('Cadence stream ready.', { exact: true }),
  ).toBeVisible();
  const call = (await fixtureState(page)).calls.at(-1)!;
  await composer(page).fill('Retained while measuring real streamed resize');
  await markWorkspaceIdentity(page);
  await addReviewResourcePair(page, 'Cadence Deck');
  await captureActualResourcePanels(
    page,
    testInfo,
    'Cadence Deck',
    'cadence-resource-setup',
  );
  const sideSeparator = page.getByRole('separator', {
    name: 'Resize side panel',
    exact: true,
  });
  const sideBefore = desktop ? await sideSeparator.boundingBox() : null;
  const paneSteps: {
    index: number;
    width: number;
    ariaValue: string | null;
  }[] = [];
  let collapsedAndRestored = false;
  let restoredPaneGeometry: {
    side: { width: number; height: number };
    transcript: { width: number; height: number };
    conversationScrollTop: number;
  } | null = null;
  const tokenPacing: {
    id: string;
    grantedAt: number;
    arrivalObserved: boolean;
    settled: boolean;
  }[] = [];
  // The frame budget is one display refresh (two on phones), measured on
  // this display at idle: not every panel presents at exactly 60 Hz. It is
  // never looser than 16.7 / 33.3 ms, and a dropped frame still fails.
  const displayRefreshMs = await page.evaluate(
    () =>
      new Promise<number>((resolve) => {
        const intervals: number[] = [];
        let previous = 0;
        const tick = (time: number) => {
          if (previous) intervals.push(time - previous);
          previous = time;
          if (intervals.length < 30) requestAnimationFrame(tick);
          else {
            intervals.sort((a, b) => a - b);
            resolve(intervals[Math.floor(intervals.length / 2)]);
          }
        };
        requestAnimationFrame(tick);
      }),
  );
  const frameBudgetMs = desktop
    ? Math.max(16.7, displayRefreshMs + 1)
    : Math.max(33.3, 2 * displayRefreshMs + 1);
  await page.evaluate(() => {
    const data: Cadence = {
      active: true,
      started: performance.now(),
      ended: null,
      frames: [],
      feedback: [],
      paneCommits: [],
      longTasks: [],
      callbacks: 0,
      streamCommits: (
        window as unknown as { __QA_STREAM_COMMITS__: TokenCommit[] }
      ).__QA_STREAM_COMMITS__,
      windows: [],
      beginWindow: () => undefined,
      endWindow: () => undefined,
      stop: () => undefined,
    };
    let previous = 0;
    let currentWindow: Cadence['windows'][number] | undefined;
    data.beginWindow = (index) => {
      previous = 0;
      currentWindow = {
        index,
        started: performance.now(),
        ended: null,
        frames: [],
      };
      data.windows.push(currentWindow);
    };
    data.endWindow = () => {
      if (currentWindow) currentWindow.ended = performance.now();
      currentWindow = undefined;
      previous = 0;
    };
    let frame = 0;
    const observer = new PerformanceObserver((list) => {
      if (data.active)
        data.longTasks.push(...list.getEntries().map((item) => item.duration));
    });
    observer.observe({ type: 'longtask', buffered: false });
    const resized = () => {
      const started = performance.now();
      requestAnimationFrame(() => {
        if (data.active && currentWindow)
          data.feedback.push(performance.now() - started);
      });
    };
    const pointer = (event: PointerEvent) => {
      if (!data.active || !currentWindow || event.buttons !== 1) return;
      const started = performance.now();
      requestAnimationFrame(() => {
        if (!data.active || !currentWindow) return;
        const side = document.querySelector('#side-pane');
        const separator = document.querySelector(
          '[aria-label="Resize side panel"]',
        );
        const feedback = performance.now() - started;
        data.feedback.push(feedback);
        data.paneCommits.push({
          time: performance.now(),
          feedback,
          width: side?.getBoundingClientRect().width ?? 0,
          ariaValue: separator?.getAttribute('aria-valuenow') ?? null,
        });
      });
    };
    document.addEventListener('pointermove', pointer, true);
    window.addEventListener('resize', resized);
    const tick = (time: number) => {
      if (!data.active) return;
      if (currentWindow) {
        if (previous) {
          const interval = time - previous;
          data.frames.push(interval);
          currentWindow.frames.push(interval);
        }
        previous = time;
      }
      data.callbacks++;
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    data.stop = () => {
      if (!data.active) return;
      data.endWindow();
      data.ended = performance.now();
      data.active = false;
      cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener('resize', resized);
      document.removeEventListener('pointermove', pointer, true);
    };
    Object.assign(window, { __QA_CADENCE__: data });
  });
  try {
    if (desktop) {
      expect(sideBefore).not.toBeNull();
      await page.mouse.move(
        sideBefore!.x + sideBefore!.width / 2,
        sideBefore!.y + Math.min(160, sideBefore!.height / 2),
      );
      await page.mouse.down();
    }
    for (let index = 0; index < 40; index++) {
      const id = String(index).padStart(3, '0');
      const grantedAt = Date.now();
      await advanceCadence(page, call.barrier_id, index);
      const arrivalObserved = await page
        .waitForFunction(
          (id) =>
            (
              window as unknown as {
                __QA_STREAM_ARRIVALS__: () => { id: string }[];
              }
            )
              .__QA_STREAM_ARRIVALS__()
              .some((item) => item.id === id),
          id,
          { timeout: 5000 },
        )
        .then(
          () => true,
          () => false,
        );
      await page.evaluate(
        (index) =>
          (window as unknown as CadenceWindow).__QA_CADENCE__.beginWindow(
            index,
          ),
        index,
      );
      if (desktop) {
        // Since Phase 12 a new design opens with the side region at its widest,
        // so the drag narrows it (to the right) rather than widening it.
        const distance = (index < 20 ? index : 39 - index) * 3;
        await page.mouse.move(
          sideBefore!.x + sideBefore!.width / 2 + distance,
          sideBefore!.y + Math.min(160, sideBefore!.height / 2),
        );
      } else {
        await page.setViewportSize({
          width: viewport.width - (index % 5) * 4,
          height: viewport.height - (index % 4) * 4,
        });
      }
      await page.evaluate(
        () =>
          new Promise<void>((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
          ),
      );
      await page.evaluate(() =>
        (window as unknown as CadenceWindow).__QA_CADENCE__.endWindow(),
      );
      const settled = await page
        .waitForFunction(
          (id) =>
            (
              window as unknown as { __QA_STREAM_COMMITS__: TokenCommit[] }
            ).__QA_STREAM_COMMITS__.some((item) => item.id === id),
          id,
          { timeout: 1500 },
        )
        .then(
          () => true,
          () => false,
        );
      tokenPacing.push({ id, grantedAt, arrivalObserved, settled });
      if (desktop)
        paneSteps.push(
          await sideSeparator.evaluate(
            (element, index) => ({
              index,
              width: document
                .querySelector('#side-pane')!
                .getBoundingClientRect().width,
              ariaValue: element.getAttribute('aria-valuenow'),
            }),
            index,
          ),
        );
    }
    if (desktop) {
      await page.mouse.up();
      await page
        .getByRole('region', { name: 'Side panels', exact: true })
        .getByRole('button', { name: 'Panel actions', exact: true })
        .click();
      await page
        .getByRole('menuitem', { name: 'Collapse panel', exact: true })
        .click();
      await expect(
        page.getByRole('region', { name: 'Side panels', exact: true }),
      ).toHaveCount(0);
      // A collapsed panel reopens from the panel rail.
      await page
        .getByRole('complementary', { name: 'Panel rail', exact: true })
        .getByRole('button', { name: 'Phase 1 workspace', exact: true })
        .click();
      await expect(
        page.getByRole('region', {
          name: 'Phase 1 workspace inspector',
          exact: true,
        }),
      ).toBeVisible();
      collapsedAndRestored = true;
      restoredPaneGeometry = await page.evaluate(() => {
        const side = document
          .querySelector('#side-pane')!
          .getBoundingClientRect();
        const log = document
          .querySelector('[role="log"]')!
          .getBoundingClientRect();
        return {
          side: { width: side.width, height: side.height },
          transcript: { width: log.width, height: log.height },
          conversationScrollTop:
            document.querySelector('.conversation')?.scrollTop ?? 0,
        };
      });
      await page.evaluate(
        () =>
          new Promise<void>((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
          ),
      );
    }
    await page.evaluate(() =>
      (window as unknown as CadenceWindow).__QA_CADENCE__.stop(),
    );
    const allTokensSettled = await page
      .waitForFunction(
        () =>
          (window as unknown as { __QA_STREAM_COMMITS__: TokenCommit[] })
            .__QA_STREAM_COMMITS__.length === 40,
        undefined,
        { timeout: 2500 },
      )
      .then(
        () => true,
        () => false,
      );
    const measured = await page.evaluate(() => {
      const data = (window as unknown as CadenceWindow).__QA_CADENCE__;
      data.stop();
      return {
        activeMeasurement: { started: data.started, ended: data.ended },
        tokenSettlementEnded: performance.now(),
        resizeWindows: data.windows,
        frames: data.frames,
        feedback: data.feedback,
        paneCommits: data.paneCommits,
        longTasks: data.longTasks,
        callbacks: data.callbacks,
        streamCommits: data.streamCommits,
        arrivals: (
          window as unknown as {
            __QA_STREAM_ARRIVALS__: () => { id: string; received: number }[];
          }
        ).__QA_STREAM_ARRIVALS__(),
      };
    });
    await writeEvidence(testInfo, 'PB05-real-transcript-resize', {
      conversation,
      generation: call.generation_id,
      browser: browser.version(),
      viewport,
      method:
        'Actual backend native deltas and real DOM;40single-token grants each followed by exact native arrival and a real pointer/viewport action. Forty named action windows retain only consecutive rAF-to-rAF intervals; first frame establishes each window anchor. Native arrival and commit pacing between action windows is separately recorded inactive cadence time. Whole interaction longtasks and every token failure remain recorded. Producer stays active during separately checked collapse/reopen after40tokens settle. No fake clock or excluded active sample.',
      interaction: desktop
        ? 'Continuous real Inspector side splitter40pointer updates, actual collapse/reopen, real Deck Bottom retained'
        : 'Real resources opened/Back during setup; conversation foreground during40compact viewport updates',
      paneSteps,
      tokenPacing,
      visibilityMethod:
        'Each nonempty exact-token Range client rect must be within transcript/ancestor/viewport clipping with visible nonzero-opacity ancestors and normal text hit-test. During actual pointerdown only, reviewed react-resizable-panels inline pointerEvents:none may substitute a containing ancestor hit; unrelated overlays are not exempted. Every segment, target and suppression chain is retained.',
      collapsedAndRestored,
      restoredPaneGeometry,
      allTokensSettled,
      samples: measured,
      literalFrameIntervals: distribution(measured.frames),
      feedback: distribution(measured.feedback),
      receiveToVisibleCommit: distribution(
        measured.streamCommits.map((item) => item.latency),
      ),
      budgetMs: desktop ? 16.7 : 33.3,
      displayRefreshMs,
      frameBudgetMs,
    });
    await assertWorkspaceIdentity(page);
    await expect(composer(page)).toHaveValue(
      'Retained while measuring real streamed resize',
    );
    await assertNoOverflow(page);
    expect(allTokensSettled).toBe(true);
    expect(tokenPacing).toHaveLength(40);
    expect(
      tokenPacing.every((item) => item.arrivalObserved && item.settled),
    ).toBe(true);
    expect(measured.resizeWindows).toHaveLength(40);
    expect(
      measured.resizeWindows.every((item) => item.frames.length >= 1),
    ).toBe(true);
    if (desktop) {
      expect(collapsedAndRestored).toBe(true);
      expect(paneSteps).toHaveLength(40);
      expect(
        new Set(paneSteps.map((item) => Math.round(item.width))).size,
      ).toBeGreaterThanOrEqual(15);
      expect(measured.paneCommits.length).toBeGreaterThanOrEqual(30);
    }
    expect(
      (await fixtureState(page)).calls.find(
        (item) => item.barrier_id === call.barrier_id,
      )?.quiesced,
    ).toBe(false);
    expect(measured.frames.length).toBeGreaterThanOrEqual(40);
    expect(measured.feedback.length).toBeGreaterThanOrEqual(30);
    const expectedIds = Array.from({ length: 40 }, (_, index) =>
      String(index).padStart(3, '0'),
    );
    expect(measured.arrivals.map((item) => item.id).sort()).toEqual(
      expectedIds,
    );
    expect(measured.streamCommits.map((item) => item.id).sort()).toEqual(
      expectedIds,
    );
    expect(measured.streamCommits.every((item) => item.visible)).toBe(true);
    expect(
      distribution(measured.streamCommits.map((item) => item.latency)).p95,
    ).toBeLessThanOrEqual(50);
    expect(distribution(measured.feedback).p95).toBeLessThanOrEqual(
      desktop ? 16.7 : 33.3,
    );
    expect(distribution(measured.frames).p95).toBeLessThanOrEqual(
      frameBudgetMs,
    );
    expect(measured.longTasks.filter((duration) => duration > 100)).toEqual([]);
  } finally {
    await page.mouse.up();
    await page.evaluate(() =>
      (window as unknown as CadenceWindow).__QA_CADENCE__?.stop(),
    );
    await releaseProducer(page, call);
    await page.setViewportSize(viewport);
  }
});

type TraceEvent = {
  name: string;
  cat?: string;
  ph: string;
  ts: number;
  dur?: number;
  pid: number;
  tid: number;
};
type Geometry = {
  width: number;
  viewport: number;
  side: number;
  aria: number | null;
};
type Commit = {
  kind: string;
  latency: number;
  before: Geometry;
  after: Geometry;
};
type WorkWindow = FixtureWindow & {
  __QA_WORK__: {
    phase: 'idle' | 'active' | null;
    frames: { idle: number[]; active: number[] };
    commits: Commit[];
    timer: number;
    events: number;
  };
};

function unionMilliseconds(
  events: TraceEvent[],
  start: number,
  end: number,
): number {
  const ranges = events
    .map((event) => [
      Math.max(start, event.ts),
      Math.min(end, event.ts + (event.dur ?? 0)),
    ])
    .filter(([left, right]) => right > left)
    .sort((a, b) => a[0] - b[0]);
  let total = 0;
  let right = start;
  for (const [left, next] of ranges) {
    total += Math.max(0, next - Math.max(left, right));
    right = Math.max(right, next);
  }
  return total / 1000;
}

test('resize renderer work and settled geometry stay within the named frame budget', async ({
  page,
  context,
  browser,
}, testInfo) => {
  test.skip(
    !['chromium-desktop', 'chromium-tablet'].includes(testInfo.project.name),
    'Named desktop/compact Chromium renderer-main-thread calibration.',
  );
  const desktop = testInfo.project.name === 'chromium-desktop';
  await page.addInitScript(() => {
    const data: WorkWindow['__QA_WORK__'] = {
      phase: null,
      frames: { idle: [], active: [] },
      commits: [],
      timer: 0,
      events: 0,
    };
    Object.assign(window, { __QA_WORK__: data });
    const geometry = (): Geometry => {
      const side = document.querySelector('[aria-label="Side panels"]');
      const separator = document.querySelector(
        '[aria-label="Resize side panel"]',
      );
      return {
        width:
          document.querySelector('.workspace-columns')?.getBoundingClientRect()
            .width ?? 0,
        viewport: innerWidth,
        side: side?.getBoundingClientRect().width ?? 0,
        aria: separator?.getAttribute('aria-valuenow')
          ? Number(separator.getAttribute('aria-valuenow'))
          : null,
      };
    };
    const capture = (event: Event) => {
      if (
        data.phase !== 'active' ||
        (event instanceof PointerEvent && event.buttons !== 1)
      )
        return;
      const started = performance.now();
      const before = geometry();
      requestAnimationFrame(() => {
        if (data.phase !== 'active') return;
        data.commits.push({
          kind: event.type,
          latency: performance.now() - started,
          before,
          after: geometry(),
        });
      });
    };
    window.addEventListener('pointermove', capture, true);
    window.addEventListener('resize', capture, true);
    const frame = (time: number) => {
      if (data.phase) {
        data.frames[data.phase].push(time);
        performance.mark(`row-bot-qa-frame-${data.phase}`);
      }
      requestAnimationFrame(frame);
    };
    requestAnimationFrame(frame);
  });
  await openFixture(page);
  if (!desktop)
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
  await openPanel(page, desktop ? 'Workspace notes' : 'Activity preview');
  if (desktop) {
    await page
      .getByRole('separator', { name: 'Resize side panel', exact: true })
      .focus();
    await page.keyboard.press('Home');
  }
  const cdp = await context.newCDPSession(page);
  const trace: TraceEvent[] = [];
  cdp.on('Tracing.dataCollected', (event) =>
    trace.push(...(event.value as unknown as TraceEvent[])),
  );
  await cdp.send('Tracing.start', {
    categories: 'devtools.timeline,toplevel,blink.user_timing',
    options: 'record-as-much-as-possible',
    transferMode: 'ReportEvents',
  });
  await page.evaluate(async () => {
    const data = (window as WorkWindow).__QA_WORK__;
    data.phase = 'idle';
    performance.mark('row-bot-qa-idle-start');
    await new Promise<void>((resolve) => {
      let count = 0;
      const next = () => {
        if (++count >= 61) resolve();
        else requestAnimationFrame(next);
      };
      requestAnimationFrame(next);
    });
    performance.mark('row-bot-qa-idle-end');
    data.phase = null;
  });
  const separator = page.getByRole('separator', {
    name: 'Resize side panel',
    exact: true,
  });
  const bounds = desktop ? await separator.boundingBox() : null;
  if (desktop)
    await page.mouse.move(bounds!.x + bounds!.width / 2, bounds!.y + 100);
  await page.evaluate(() => {
    const data = (window as WorkWindow).__QA_WORK__;
    data.phase = 'active';
    performance.mark('row-bot-qa-active-start');
    data.timer = window.setInterval(() => {
      (window as FixtureWindow).__ROW_BOT_FIXTURE__.transport.emitTextDelta();
      data.events++;
    }, 25);
  });
  if (desktop) {
    const x = bounds!.x + bounds!.width / 2;
    await page.mouse.down();
    await page.mouse.move(x - 140, bounds!.y + 100, { steps: 60 });
    await page.mouse.move(x + 50, bounds!.y + 100, { steps: 60 });
    await page.mouse.up();
  } else {
    for (let step = 1; step <= 30; step++) {
      await page.setViewportSize({
        width: 820 - (step % 6) * 8,
        height: 1180 - (step % 5) * 12,
      });
      await page.evaluate(
        () =>
          new Promise<void>((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
          ),
      );
    }
  }
  const measured = await page.evaluate(async () => {
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
    );
    const data = (window as WorkWindow).__QA_WORK__;
    performance.mark('row-bot-qa-active-end');
    data.phase = null;
    clearInterval(data.timer);
    return {
      ...data,
      appliedEvents: (window as FixtureWindow).__ROW_BOT_FIXTURE__.controller
        .metrics.appliedEvents,
    };
  });
  const completed = new Promise<void>((resolve) =>
    cdp.once('Tracing.tracingComplete', () => resolve()),
  );
  await cdp.send('Tracing.end');
  await completed;
  await cdp.detach();
  const activeStart = trace.find(
    (event) => event.name === 'row-bot-qa-active-start',
  );
  expect(
    activeStart,
    'Trace must contain the renderer user timing boundary',
  ).toBeDefined();
  const renderer = trace.filter(
    (event) => event.pid === activeStart!.pid && event.tid === activeStart!.tid,
  );
  const tasks = renderer.filter(
    (event) =>
      event.ph === 'X' &&
      (event.name === 'RunTask' || event.name.endsWith('::RunTask')),
  );
  expect(
    tasks.length,
    'Named renderer top-level tasks must be present',
  ).toBeGreaterThan(0);
  const scripts = renderer.filter(
    (event) =>
      event.ph === 'X' &&
      [
        'FunctionCall',
        'EvaluateScript',
        'EventDispatch',
        'V8.Execute',
      ].includes(event.name),
  );
  const layouts = renderer.filter(
    (event) =>
      event.ph === 'X' && ['Layout', 'UpdateLayoutTree'].includes(event.name),
  );
  const paints = renderer.filter(
    (event) =>
      event.ph === 'X' &&
      ['Paint', 'PrePaint', 'CompositeLayers', 'Layerize'].includes(event.name),
  );
  const phases = (['idle', 'active'] as const).map((phase) => {
    const start = renderer.find(
      (event) => event.name === `row-bot-qa-${phase}-start`,
    )!.ts;
    const end = renderer.find(
      (event) => event.name === `row-bot-qa-${phase}-end`,
    )!.ts;
    const boundaries = [
      start,
      ...renderer
        .filter(
          (event) =>
            event.name === `row-bot-qa-frame-${phase}` &&
            event.ts > start &&
            event.ts < end,
        )
        .map((event) => event.ts)
        .sort((a, b) => a - b),
      end,
    ];
    const frames = boundaries.slice(1).map((right, index) => ({
      start: boundaries[index],
      end: right,
      workMs: unionMilliseconds(tasks, boundaries[index], right),
      scriptMs: unionMilliseconds(scripts, boundaries[index], right),
      layoutMs: unionMilliseconds(layouts, boundaries[index], right),
      paintMs: unionMilliseconds(paints, boundaries[index], right),
    }));
    const timestamps = measured.frames[phase];
    return {
      phase,
      start,
      end,
      frames,
      work: distribution(frames.map((frame) => frame.workMs)),
      cadence: distribution(
        timestamps.slice(1).map((time, index) => time - timestamps[index]),
      ),
      longTasks: tasks.filter(
        (event) =>
          event.ts < end &&
          event.ts + (event.dur ?? 0) > start &&
          (event.dur ?? 0) > 100000,
      ),
    };
  });
  const useful = new Set([
    ...tasks,
    ...scripts,
    ...layouts,
    ...paints,
    ...renderer.filter((event) => event.name.startsWith('row-bot-qa-')),
  ]);
  await writeEvidence(testInfo, 'PB05-renderer-scoped-trace', {
    categories: 'devtools.timeline,toplevel,blink.user_timing',
    renderer: { pid: activeStart!.pid, tid: activeStart!.tid },
    events: [...useful]
      .sort((a, b) => a.ts - b.ts)
      .map(({ name, cat, ph, ts, dur, pid, tid }) => ({
        name,
        cat,
        ph,
        ts,
        dur,
        pid,
        tid,
      })),
    policy:
      'Raw scoped timing events; unrelated processes/events/arguments omitted. No screenshots/netlog/headers/objects. No frame or workload sample excluded.',
  });
  await writeEvidence(testInfo, 'PB05-renderer-work-and-commit', {
    browserVersion: browser.version(),
    layout: desktop ? 'desktop' : 'compact tablet',
    phases,
    commits: measured.commits,
    events: measured.events,
    appliedEvents: measured.appliedEvents,
    budgetMs: desktop ? 16.7 : 33.3,
    excludedSamples: [],
    method:
      'One continuous gesture with120 pointer updates or30 compact viewport changes under25ms synthetic streaming. Renderer-main-thread RunTask union clipped to each user-timing frame/workload boundary; nested intervals never double counted. Script/style/layout/paint subwork reported separately. Idle cadence contextual only, never subtracted. Capture-phase event listener installed before product code verifies actual geometry/ARIA in next rAF.',
  });
  const active = phases.find((phase) => phase.phase === 'active')!;
  expect(active.work.p95).toBeLessThanOrEqual(desktop ? 16.7 : 33.3);
  expect(active.longTasks).toEqual([]);
  expect(measured.commits.length).toBeGreaterThanOrEqual(20);
  for (const commit of measured.commits) {
    expect(commit.after.width).toBeGreaterThan(0);
    expect(
      Math.abs(commit.after.width - commit.after.viewport),
    ).toBeLessThanOrEqual(1);
    if (desktop) {
      expect(commit.after.side).toBeGreaterThanOrEqual(319);
      expect(commit.after.aria).not.toBeNull();
    }
  }
  if (desktop)
    expect(
      measured.commits.filter(
        (commit) => Math.abs(commit.before.side - commit.after.side) > 0.1,
      ).length,
    ).toBeGreaterThanOrEqual(60);
  expect(measured.events).toBeGreaterThan(0);
  expect(measured.appliedEvents).toBeGreaterThan(0);
  await assertConversationMarker(page);
});
