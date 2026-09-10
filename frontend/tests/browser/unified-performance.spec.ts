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
        const distance = (index < 20 ? index : 39 - index) * 3;
        await page.mouse.move(
          sideBefore!.x + sideBefore!.width / 2 - distance,
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
      await page
        .locator('.resource-chips')
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
      desktop ? 16.7 : 33.3,
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
