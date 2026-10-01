import type { Locator, Page, TestInfo } from '@playwright/test';
import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
  writeEvidence,
} from './evidence';
import {
  assertConversationMarker,
  openFixture,
  stableConversationMarker,
  type FixtureWindow,
} from './fixture';
import { openPanel, readLayout } from './panel-helpers';

type LibraryRow = { id: string; title: string };

async function seedLibrary(page: Page): Promise<LibraryRow[]> {
  await openFixture(page);
  await stableConversationMarker(page);
  return page.evaluate(async () => {
    const { controller, transport } = (window as FixtureWindow)
      .__ROW_BOT_FIXTURE__;
    const originals = [...transport.conversations];
    const rows = Array.from({ length: 55 }, (_, index) => ({
      ...originals[0],
      id:
        index === 0
          ? originals[0].id
          : index === 12
            ? originals[1].id
            : index === 54
              ? originals[2].id
              : `sidebar-fixture-${index + 1}`,
      title: `Conversation ${String(index + 1).padStart(2, '0')} — A deliberately long synthetic title about planning a quiet library, preserving the full conversation name, and keeping every server-ordered item available`,
    }));
    transport.conversations.splice(0, transport.conversations.length, ...rows);
    await controller.loadMoreConversations(true);
    return rows.map(({ id, title }) => ({ id, title }));
  });
}

async function navigation(page: Page): Promise<Locator> {
  const nav = page.getByRole('navigation', {
    name: 'Workspace navigation',
    exact: true,
  });
  if (!(await nav.isVisible())) {
    // Below 1024px a panel is a full-height sheet over the conversation and
    // its header; return to the conversation to reach the drawer.
    const back = page.getByRole('button', {
      name: 'Back to conversation',
      exact: true,
    });
    if (await back.isVisible()) await back.click();
    await page
      .getByRole('button', { name: 'Toggle navigation', exact: true })
      .click();
  }
  await expect(nav).toBeVisible();
  return nav;
}

function conversationRows(nav: Locator): Locator {
  return nav.locator('.nav-conversations .nav-conversation-link');
}

async function assertRowOrder(nav: Locator, rows: LibraryRow[]): Promise<void> {
  const buttons = conversationRows(nav);
  await expect(buttons).toHaveCount(rows.length);
  expect(
    await buttons.evaluateAll((elements) =>
      elements.map((element) => element.getAttribute('aria-label')),
    ),
  ).toEqual(rows.map(({ title }) => title));
}

async function assertSelection(page: Page, id: string): Promise<void> {
  await expect
    .poll(() =>
      page.evaluate(() => {
        const { controller, transport } = (window as FixtureWindow)
          .__ROW_BOT_FIXTURE__;
        const state = controller.getSnapshot();
        return {
          selected: state.selectedConversationId,
          conversation: state.conversation?.id,
          loading: state.loadingConversation,
          status: state.status,
          active: transport.counters.active,
          streams: transport.counters.streams,
          commands: transport.counters.commands,
        };
      }),
    )
    .toEqual({
      selected: id,
      conversation: id,
      loading: false,
      status: 'ready',
      active: 1,
      streams: 1,
      commands: 0,
    });
  await assertConversationMarker(page);
}

async function showLess(nav: Locator): Promise<void> {
  const shrink = nav.getByRole('button', { name: 'Show less', exact: true });
  if (await shrink.isVisible()) await shrink.click();
}

/**
 * Wheels to the end of the expanded list: reaching it reads the next page,
 * with no button (B239), and Show less stays reachable below every row.
 */
async function wheelToListEnd(
  page: Page,
  nav: Locator,
  testInfo: TestInfo,
  total: number,
): Promise<void> {
  const readSurface = () =>
    nav.evaluate((element) => {
      let ancestor: HTMLElement | null = element as HTMLElement;
      while (ancestor) {
        const style = getComputedStyle(ancestor);
        if (
          /^(auto|scroll)$/.test(style.overflowY) &&
          ancestor.scrollHeight > ancestor.clientHeight
        ) {
          const rect = ancestor.getBoundingClientRect();
          return {
            tag: ancestor.tagName,
            className: ancestor.className,
            overflowY: style.overflowY,
            scrollHeight: ancestor.scrollHeight,
            clientHeight: ancestor.clientHeight,
            scrollTop: ancestor.scrollTop,
            x: (Math.max(0, rect.left) + Math.min(innerWidth, rect.right)) / 2,
            y: (Math.max(0, rect.top) + Math.min(innerHeight, rect.bottom)) / 2,
          };
        }
        ancestor = ancestor.parentElement;
      }
      return null;
    });
  const scrollSurface = await readSurface();
  expect(
    scrollSurface,
    'Expanded history has a user-scrollable ancestor',
  ).not.toBeNull();
  if (
    testInfo.project.use.browserName === 'webkit' &&
    testInfo.project.use.isMobile
  ) {
    const limitation =
      'Playwright mobile WebKit does not implement mouse.wheel; this project scrolls the end of the list into view programmatically and retains data/cursor and keyboard-selection checks, but makes no native wheel or physical touch-scroll claim.';
    testInfo.annotations.push({
      type: 'coverage-limitation',
      description: limitation,
    });
    await nav.locator('.nav-list-end').scrollIntoViewIfNeeded();
    await expect(conversationRows(nav)).toHaveCount(total);
    await writeEvidence(testInfo, 'sidebar-wheel-pagination-reachability', {
      supported: false,
      limitation,
      scrollSurface,
    });
    return;
  }
  await page.mouse.move(scrollSurface!.x, scrollSurface!.y);
  // Deliberate wheel input, with no locator click, focus or scrollIntoView on
  // the offscreen controls before their visible hit targets are established.
  // Engines clamp a single very large wheel delta. Bounded repeated gestures
  // establish progress without mistaking that input behavior for lost history.
  const samples = [scrollSurface!];
  const wheelToBottom = async () => {
    for (let gesture = 0; gesture < 8; gesture += 1) {
      const previous = samples.at(-1)!;
      const maximum = previous.scrollHeight - previous.clientHeight;
      if (previous.scrollTop >= maximum - 1) break;
      await page.mouse.wheel(0, 1000);
      await expect
        .poll(async () => (await readSurface())!.scrollTop)
        .toBeGreaterThanOrEqual(
          Math.min(previous.scrollTop + 300, maximum - 1),
        );
      samples.push((await readSurface())!);
    }
  };
  await wheelToBottom();
  // The end of the list came into view: the next page arrives by itself.
  await expect(conversationRows(nav)).toHaveCount(total);
  samples.push((await readSurface())!);
  await wheelToBottom();
  await writeEvidence(testInfo, 'sidebar-wheel-scroll-samples', samples);
  const control = nav.getByRole('button', { name: 'Show less', exact: true });
  await expect
    .poll(
      () =>
        control.evaluate((element) => {
          const rect = element.getBoundingClientRect();
          return (
            rect.y >= 0 &&
            rect.bottom <= innerHeight &&
            element.contains(
              document.elementFromPoint(
                rect.x + rect.width / 2,
                rect.y + rect.height / 2,
              ),
            )
          );
        }),
      'Show less becomes reachable through wheel input',
    )
    .toBe(true);
  await writeEvidence(testInfo, 'sidebar-wheel-pagination-reachability', {
    supported: true,
    input:
      'Native wheel gestures of deltaY1000 inside the actual scrolling ancestor, at most eight to reach the end of the list before and after its next page loads',
    gestures: samples.length - 2,
    scrollSurface,
    finalSurface: await readSurface(),
    rowsLoaded: total,
    control: await control.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
    }),
  });
}

test('sidebar preview and cursor pages preserve server order and an out-of-preview selection', async ({
  page,
}, testInfo) => {
  const rows = await seedLibrary(page);
  const readRouteHistory = () =>
    page.evaluate(() => ({
      length: history.length,
      pathname: location.pathname,
      search: location.search,
    }));
  const originalRootHistory = await readRouteHistory();
  let nav = await navigation(page);
  await assertRowOrder(nav, rows.slice(0, 10));
  await expect(
    nav.getByRole('button', { name: 'Show all', exact: true }),
  ).toHaveAttribute('aria-expanded', 'false');
  await screenshot(page, testInfo, 'sidebar-default-ten');

  await nav.getByRole('button', { name: 'Show all', exact: true }).click();
  await assertRowOrder(nav, rows.slice(0, 50));
  await wheelToListEnd(page, nav, testInfo, rows.length);
  await assertRowOrder(nav, rows);
  await expect(
    nav.getByRole('button', { name: 'Load more conversations', exact: true }),
  ).toHaveCount(0);
  await nav.getByRole('button', { name: rows[12].title, exact: true }).click();
  await assertSelection(page, rows[12].id);
  const firstSelectionHistory = await readRouteHistory();
  expect(firstSelectionHistory).toMatchObject({
    pathname: `/app-v2/conversations/${rows[12].id}`,
    search: '',
  });
  expect(firstSelectionHistory.length).toBeGreaterThanOrEqual(
    originalRootHistory.length + 1,
  );
  nav = await navigation(page);
  await showLess(nav);
  await assertRowOrder(nav, [...rows.slice(0, 10), rows[12]]);
  await expect(
    nav.getByRole('button', { name: rows[12].title, exact: true }),
  ).toHaveAttribute('aria-current', 'page');
  const selectionStyle = await nav.evaluate((element) => {
    const selected = element.querySelector('[aria-current="page"]')!;
    const unselected = element.querySelector(
      '.conversation-list button:not([aria-current])',
    )!;
    return {
      selectedBackground: getComputedStyle(selected).backgroundColor,
      unselectedBackground: getComputedStyle(unselected).backgroundColor,
      selectedWeight: getComputedStyle(selected).fontWeight,
    };
  });
  expect(selectionStyle.selectedBackground).not.toBe(
    selectionStyle.unselectedBackground,
  );
  expect(selectionStyle.selectedBackground).not.toBe('rgba(0, 0, 0, 0)');

  await nav.getByRole('button', { name: 'Show all', exact: true }).click();
  await assertRowOrder(nav, rows);
  await nav.getByRole('button', { name: rows[54].title, exact: true }).click();
  await assertSelection(page, rows[54].id);
  const secondSelectionHistory = await readRouteHistory();
  expect(secondSelectionHistory).toMatchObject({
    pathname: `/app-v2/conversations/${rows[54].id}`,
    search: '',
  });
  expect(secondSelectionHistory.length).toBeGreaterThan(
    firstSelectionHistory.length,
  );
  nav = await navigation(page);
  await showLess(nav);
  await assertRowOrder(nav, [...rows.slice(0, 10), rows[54]]);
  await screenshot(page, testInfo, 'sidebar-selected-after-cursor-page');
  if (testInfo.project.use.viewport!.width < 1024) {
    await assertNoOverflow(page);
    return;
  }
  await nav
    .getByRole('link', { name: 'Conversation library', exact: true })
    .click();
  await expect(page).toHaveURL(/\/app-v2\/library(?:[?#].*)?$/);
  const libraryHistory = await readRouteHistory();
  expect(libraryHistory).toMatchObject({
    pathname: '/app-v2/library',
    search: '',
  });
  expect(libraryHistory.length).toBeGreaterThan(secondSelectionHistory.length);
  nav = await navigation(page);
  await nav.getByRole('button', { name: rows[54].title, exact: true }).click();
  await expect(page).toHaveURL(
    new URL(`/app-v2/conversations/${rows[54].id}`, page.url()).href,
  );
  await assertSelection(page, rows[54].id);
  const returnedConversationHistory = await readRouteHistory();
  expect(returnedConversationHistory).toMatchObject({
    pathname: `/app-v2/conversations/${rows[54].id}`,
    search: '',
  });
  expect(returnedConversationHistory.length).toBeGreaterThan(
    libraryHistory.length,
  );
  await expect(page.getByTestId('conversation-workspace')).toBeVisible();
  await assertNoOverflow(page);
  await writeEvidence(testInfo, 'sidebar-order-and-selection', {
    supplied: rows,
    previewCount: 10,
    firstCursorPageCount: 50,
    fullLoadedCount: 55,
    selectedOutsidePreview: [rows[12].id, rows[54].id],
    originalRootHistory,
    firstSelectionHistory,
    secondSelectionHistory,
    libraryHistory,
    returnedConversationHistory,
    routeMethod:
      'Each explicit conversation or library selection pushes its registered route once; conversation selection clears the fixture-only query.',
    selectionStyle,
    commands: await page.evaluate(
      () =>
        (window as FixtureWindow).__ROW_BOT_FIXTURE__.transport.counters
          .commands,
    ),
  });
});

test('a type filter lists its older matches from the server, down to Older', async ({
  page,
}, testInfo) => {
  const rows = await seedLibrary(page);
  // The only workflow is the oldest conversation, beyond the first page.
  await page.evaluate(async (id) => {
    const { controller, transport } = (window as FixtureWindow)
      .__ROW_BOT_FIXTURE__;
    const row = transport.conversations.find((item) => item.id === id)!;
    row.category = 'workflow';
    row.updated_at = '2026-01-05T09:00:00Z';
    await controller.loadMoreConversations(true);
  }, rows[54].id);
  const nav = await navigation(page);
  await assertRowOrder(nav, rows.slice(0, 10));
  await nav.getByRole('radio', { name: 'Workflows', exact: true }).click();
  await assertRowOrder(nav, [rows[54]]);
  await expect(
    nav
      .getByRole('list', { name: 'Recent conversations', exact: true })
      .getByRole('heading', { level: 4 }),
  ).toHaveText(['Older']);
  await screenshot(page, testInfo, 'sidebar-type-filter-older');
  await nav.getByRole('radio', { name: 'Chats', exact: true }).click();
  await expect(nav.getByText('No chats yet.', { exact: true })).toBeVisible();
  await nav.getByRole('radio', { name: 'All', exact: true }).click();
  await assertRowOrder(nav, rows.slice(0, 10));
  await assertNoOverflow(page);
});

test('selecting another conversation preserves its own view and restores the original registered panels on return', async ({
  page,
}, testInfo) => {
  const rows = await seedLibrary(page);
  const controllerHandle = await page.evaluateHandle(
    () => (window as FixtureWindow).__ROW_BOT_FIXTURE__.controller,
  );
  await openPanel(page, 'Activity preview');
  const before = await readLayout(page);
  expect(before.panels).toHaveLength(1);
  const instance = before.panels[0].instance_id;
  const nav = await navigation(page);
  await nav.getByRole('button', { name: 'Show all', exact: true }).click();
  await nav.getByRole('button', { name: rows[12].title, exact: true }).click();
  await assertSelection(page, rows[12].id);
  await expect(page.getByTestId('conversation-workspace')).toBeVisible();
  const afterSelection = await readLayout(page);
  expect(afterSelection.panels).toEqual([]);
  expect(afterSelection.activePanelId).toBeNull();
  await expect(
    page.getByRole('region', { name: 'Compact panel', exact: true }),
  ).toHaveCount(0);
  await expect(page.locator('.sample-panel:visible')).toHaveCount(0);
  await screenshot(page, testInfo, 'sidebar-select-from-activity');
  await accessibility(page, testInfo, 'sidebar-select-from-activity-axe');
  const returnNavigation = await navigation(page);
  await returnNavigation
    .getByRole('button', { name: rows[0].title, exact: true })
    .click();
  await assertSelection(page, rows[0].id);
  const restored = await readLayout(page);
  expect(restored.panels).toEqual(before.panels);
  expect(restored.activePanelId).toBe(
    testInfo.project.use.viewport!.width >= 1024 ? before.activePanelId : null,
  );
  await openPanel(page, 'Activity preview');
  const reopened = await readLayout(page);
  expect(reopened.panels).toEqual(before.panels);
  expect(reopened.panels[0].instance_id).toBe(instance);
  expect(reopened.activePanelId).toBe(before.activePanelId);
  await assertConversationMarker(page);
  const sameController = await controllerHandle.evaluate(
    (controller) =>
      controller === (window as FixtureWindow).__ROW_BOT_FIXTURE__.controller,
  );
  expect(sameController).toBe(true);
  await controllerHandle.dispose();
  await screenshot(page, testInfo, 'sidebar-activity-reopened-same-instance');
  await writeEvidence(testInfo, 'sidebar-selection-preserves-panel-owner', {
    desktop: testInfo.project.use.viewport!.width >= 1024,
    selectedConversationId: rows[0].id,
    otherConversationId: rows[12].id,
    before,
    afterSelection,
    restored,
    reopened,
    sameController,
    conversationNodeRetained: true,
    commands: await page.evaluate(
      () =>
        (window as FixtureWindow).__ROW_BOT_FIXTURE__.transport.counters
          .commands,
    ),
  });
});

test('collapsed Conversations keeps the current row and tracks the same live selection owner', async ({
  page,
}, testInfo) => {
  const rows = await seedLibrary(page);
  // This valid recorded conversation is beyond the loaded first page. The
  // collapsed section must use confirmed selection metadata, without fetching
  // all history or creating a separate compact-navigation state owner.
  await page.evaluate(async (id) => {
    await (
      window as FixtureWindow
    ).__ROW_BOT_FIXTURE__.controller.selectConversation(id);
    // Keep this injected selection on its canonical route without loading the
    // rest of the library or remounting the workspace.
    history.replaceState(history.state, '', `/app-v2/conversations/${id}`);
    window.dispatchEvent(
      new PopStateEvent('popstate', { state: history.state }),
    );
  }, rows[54].id);
  await assertSelection(page, rows[54].id);
  const nav = await navigation(page);
  await assertRowOrder(nav, [...rows.slice(0, 10), rows[54]]);
  const section = nav.getByRole('button', {
    name: 'Conversations',
    exact: true,
  });
  await expect(section).toHaveAttribute('aria-expanded', 'true');
  const controlledId = await section.getAttribute('aria-controls');
  expect(controlledId).toBeTruthy();
  await section.focus();
  await page.keyboard.press('Enter');
  await expect(section).toHaveAttribute('aria-expanded', 'false');
  await expect(section).toBeFocused();
  await expect(
    nav.getByRole('list', { name: 'Recent conversations', exact: true }),
  ).toHaveCount(0);
  const current = nav.getByRole('list', {
    name: 'Current conversation',
    exact: true,
  });
  await expect(current.locator('.nav-conversation-link')).toHaveCount(1);
  await expect(current.locator('.nav-conversation-link')).toHaveAccessibleName(
    rows[54].title,
  );
  await expect(current.locator('.nav-conversation-link')).toHaveAttribute(
    'aria-current',
    'page',
  );
  await expect(
    nav.getByRole('button', { name: 'Show all', exact: true }),
  ).toHaveCount(0);
  await screenshot(page, testInfo, 'sidebar-collapsed-current-conversation');
  await accessibility(page, testInfo, 'sidebar-collapsed-axe');

  await page.evaluate(async (id) => {
    await (
      window as FixtureWindow
    ).__ROW_BOT_FIXTURE__.controller.selectConversation(id);
    // Keep this injected selection on its canonical route without loading the
    // rest of the library or remounting the workspace.
    history.replaceState(history.state, '', `/app-v2/conversations/${id}`);
    window.dispatchEvent(
      new PopStateEvent('popstate', { state: history.state }),
    );
  }, rows[0].id);
  await assertSelection(page, rows[0].id);
  if (!(await nav.isVisible()))
    await page.getByRole('button', { name: 'Toggle navigation' }).click();
  if ((await section.getAttribute('aria-expanded')) === 'true')
    await section.click();
  await expect(current.locator('.nav-conversation-link')).toHaveAccessibleName(
    rows[0].title,
  );
  await section.focus();
  await page.keyboard.press('Space');
  await expect(section).toHaveAttribute('aria-expanded', 'true');
  await expect(section).toBeFocused();
  await assertRowOrder(nav, rows.slice(0, 10));
  expect(
    await page.evaluate(
      (id) => !!document.getElementById(id!),
      await section.getAttribute('aria-controls'),
    ),
  ).toBe(true);
  await writeEvidence(testInfo, 'sidebar-collapse-live-selection', {
    selectionOutsideLoadedPage: rows[54].id,
    updatedSelectionWhileCollapsed: rows[0].id,
    controlRetainsKeyboardFocus: true,
    sameControllerAndConversationNode: true,
    commands: 0,
  });
});

for (const zoom of [1, 2]) {
  test(`long sidebar titles retain touch targets, focus and full hints at ${zoom * 100} percent`, async ({
    page,
  }, testInfo) => {
    const rows = await seedLibrary(page);
    if (zoom === 2)
      await page.evaluate(() => {
        document.documentElement.style.zoom = '2';
      });
    const nav = await navigation(page);
    const first = nav.getByRole('button', { name: rows[0].title, exact: true });
    await first.scrollIntoViewIfNeeded();
    const geometry = await first.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      const label = element.querySelector('.conversation-title')!;
      const labelRect = label.getBoundingClientRect();
      const iconRect = element.querySelector('svg')!.getBoundingClientRect();
      const style = getComputedStyle(label);
      const fineDesktop = matchMedia(
        '(pointer: fine) and (min-width: 1024px)',
      ).matches;
      const density = document.documentElement.dataset.density;
      const minimumHeight = fineDesktop && density === 'compact' ? 34 : 44;
      return {
        fineDesktop,
        density,
        minimumHeight,
        row: {
          x: rect.x,
          y: rect.y,
          right: rect.right,
          bottom: rect.bottom,
          width: rect.width,
          height: rect.height,
        },
        cssHeight: parseFloat(getComputedStyle(element).height),
        label: {
          left: labelRect.left,
          right: labelRect.right,
          clientWidth: label.clientWidth,
          scrollWidth: label.scrollWidth,
          overflow: style.overflow,
          whiteSpace: style.whiteSpace,
          textOverflow: style.textOverflow,
        },
        icon: {
          left: iconRect.left,
          right: iconRect.right,
          width: iconRect.width,
        },
        viewport: { width: innerWidth, height: innerHeight },
        hit: element.contains(
          document.elementFromPoint(
            rect.x + rect.width / 2,
            rect.y + rect.height / 2,
          ),
        ),
      };
    });
    await writeEvidence(
      testInfo,
      `sidebar-${zoom * 100}-title-geometry`,
      geometry,
    );
    expect(geometry.cssHeight).toBeGreaterThanOrEqual(geometry.minimumHeight);
    expect(geometry.row.x).toBeGreaterThanOrEqual(0);
    expect(geometry.row.right).toBeLessThanOrEqual(geometry.viewport.width + 1);
    expect(geometry.row.bottom).toBeLessThanOrEqual(
      geometry.viewport.height + 1,
    );
    expect(geometry.hit).toBe(true);
    expect(geometry.label.clientWidth).toBeGreaterThan(0);
    expect(geometry.label.scrollWidth).toBeGreaterThan(
      geometry.label.clientWidth,
    );
    expect(geometry.label).toMatchObject({
      overflow: 'hidden',
      whiteSpace: 'nowrap',
      textOverflow: 'ellipsis',
    });
    expect(geometry.icon.width).toBeGreaterThan(0);
    expect(geometry.label.left).toBeGreaterThanOrEqual(geometry.icon.right);
    expect(geometry.label.right).toBeLessThanOrEqual(geometry.row.right);

    await first.hover();
    await expect(page.getByRole('tooltip')).toHaveText(rows[0].title);
    const readHintGeometry = () =>
      page.locator('.tooltip').evaluate((element) => {
        const rect = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        const wrapper = element.parentElement!;
        const wrapperStyle = getComputedStyle(wrapper);
        const wrapperRect = wrapper.getBoundingClientRect();
        const offsetParent = (wrapper as HTMLElement).offsetParent;
        return {
          left: rect.left,
          right: rect.right,
          top: rect.top,
          bottom: rect.bottom,
          viewportWidth: innerWidth,
          viewportHeight: innerHeight,
          clientWidth: element.clientWidth,
          scrollWidth: element.scrollWidth,
          computed: {
            width: style.width,
            maxWidth: style.maxWidth,
            height: style.height,
            availableWidth: style.getPropertyValue(
              '--radix-tooltip-content-available-width',
            ),
            availableHeight: style.getPropertyValue(
              '--radix-tooltip-content-available-height',
            ),
            popperAvailableWidth: style.getPropertyValue(
              '--radix-popper-available-width',
            ),
            popperAvailableHeight: style.getPropertyValue(
              '--radix-popper-available-height',
            ),
          },
          wrapper: {
            transform: wrapperStyle.transform,
            position: wrapperStyle.position,
            width: wrapperStyle.width,
            height: wrapperStyle.height,
            zoom: wrapperStyle.zoom,
            left: wrapperRect.left,
            top: wrapperRect.top,
            right: wrapperRect.right,
            bottom: wrapperRect.bottom,
            offsetParent: offsetParent
              ? { tag: offsetParent.tagName, className: offsetParent.className }
              : null,
          },
          rootZoom: getComputedStyle(document.documentElement).zoom,
          visualViewport: window.visualViewport
            ? {
                width: window.visualViewport.width,
                height: window.visualViewport.height,
                scale: window.visualViewport.scale,
                offsetLeft: window.visualViewport.offsetLeft,
                offsetTop: window.visualViewport.offsetTop,
              }
            : null,
        };
      });
    const hintGeometry = await readHintGeometry();
    await writeEvidence(
      testInfo,
      `sidebar-${zoom * 100}-hint-geometry`,
      hintGeometry,
    );
    await screenshot(page, testInfo, `sidebar-${zoom * 100}-full-title-hover`);
    expect(hintGeometry.left).toBeGreaterThanOrEqual(0);
    expect(hintGeometry.right).toBeLessThanOrEqual(
      hintGeometry.viewportWidth + 1,
    );
    expect(hintGeometry.top).toBeGreaterThanOrEqual(0);
    expect(hintGeometry.bottom).toBeLessThanOrEqual(
      hintGeometry.viewportHeight + 1,
    );
    expect(hintGeometry.scrollWidth).toBeLessThanOrEqual(
      hintGeometry.clientWidth + 1,
    );
    await page.mouse.move(0, 0, { steps: 10 });
    const section = nav.getByRole('button', {
      name: 'Conversations',
      exact: true,
    });
    await section.focus();
    await expect(section).toBeFocused();
    await expect(page.getByRole('tooltip')).toHaveCount(0);
    await first.focus();
    await expect(first).toBeFocused();
    await expect(page.getByRole('tooltip')).toHaveText(rows[0].title);
    await screenshot(page, testInfo, `sidebar-${zoom * 100}-full-title-focus`);
    await section.focus();
    await expect(page.getByRole('tooltip')).toHaveCount(0);
    await first.focus();
    await page.keyboard.press('Enter');
    await assertSelection(page, rows[0].id);
    const settledNav = await navigation(page);
    await assertRowOrder(settledNav, rows.slice(0, 10));
    await assertNoOverflow(page);
    await screenshot(page, testInfo, `sidebar-${zoom * 100}-settled`);
    await accessibility(page, testInfo, `sidebar-${zoom * 100}-axe`);
    await writeEvidence(testInfo, `sidebar-${zoom * 100}-scope`, {
      zoomMethod:
        zoom === 2
          ? 'CSS zoom: 2, not browser chrome or physical-device zoom'
          : 'Native CSS scale',
      hoverMethod:
        'Playwright mouse input, including touch-capable emulated viewports; ten native pointer steps to (0,0) exit the hover grace region before the separate keyboard-focus trial',
      keyboardSelection: rows[0].id,
      commands: 0,
    });
  });
}
