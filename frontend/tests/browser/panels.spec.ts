import {
  test,
  expect,
  assertNoOverflow,
  screenshot,
  writeEvidence,
  accessibility,
} from './evidence';
import {
  assertConversationMarker,
  openFixture,
  stableConversationMarker,
  type FixtureWindow,
} from './fixture';
import { openPanel, readLayout } from './panel-helpers';
import type { Page } from '@playwright/test';

test('an open compact navigation drawer updates when a large library page arrives', async ({
  page,
}, testInfo) => {
  await openFixture(page);
  await page.evaluate(async () => {
    const fixture = (window as FixtureWindow).__ROW_BOT_FIXTURE__;
    const template = fixture.transport.conversations[0];
    for (let index = 4; index <= 1005; index += 1)
      fixture.transport.conversations.push({
        ...template,
        id: `library-${index}`,
        title: `Library conversation ${index}`,
      });
    await fixture.controller.loadMoreConversations(true);
  });
  if (testInfo.project.use.viewport!.width < 1024)
    await page
      .getByRole('button', { name: 'Toggle navigation', exact: true })
      .click();
  await page.getByRole('button', { name: 'Show all', exact: true }).click();
  await page
    .getByRole('button', { name: 'Load more conversations', exact: true })
    .click();
  await expect(
    page.getByRole('button', { name: 'Library conversation 100', exact: true }),
  ).toBeAttached();
  await page
    .getByRole('button', { name: 'A place for your ideas', exact: true })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (window as FixtureWindow).__ROW_BOT_FIXTURE__.controller.getSnapshot()
            .selectedConversationId,
      ),
    )
    .toBe('conversation-a');
  await assertNoOverflow(page);
});

test('compact activity returns to one desktop dock after resize', async ({
  page,
}, testInfo) => {
  test.skip(
    testInfo.project.use.viewport!.width !== 390,
    'One phone→desktop continuity check per browser engine.',
  );
  await openFixture(page);
  await stableConversationMarker(page);
  await openPanel(page, 'Activity preview');
  const instance = (await readLayout(page)).panels[0].instance_id;
  await page.setViewportSize({ width: 1440, height: 900 });
  await expect(page.getByTestId('conversation-workspace')).toBeVisible();
  await expect(
    page.getByRole('region', { name: 'Compact panel', exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole('heading', { name: 'Activity preview', exact: true }),
  ).toHaveCount(1);
  expect((await readLayout(page)).panels[0].instance_id).toBe(instance);
  await assertConversationMarker(page);
  await screenshot(page, testInfo, 'compact-activity-promoted-to-dock');
});

for (const appearance of ['light', 'dark']) {
  test(`sample panel preserves conversation identity in ${appearance}`, async ({
    page,
  }, testInfo) => {
    await page.addInitScript(
      (mode) =>
        localStorage.setItem(
          'row-bot.appearance.v1',
          JSON.stringify({ version: 1, appearance: mode, accent: 'blue' }),
        ),
      appearance,
    );
    await openFixture(page);
    await stableConversationMarker(page);
    await openPanel(page);
    const first = await readLayout(page);
    expect(first.panels).toHaveLength(1);
    await assertConversationMarker(page);
    await assertNoOverflow(page);
    await screenshot(page, testInfo, `workspace-notes-${appearance}`);
    if (testInfo.project.use.viewport!.width < 1024)
      await page.keyboard.press('Escape');
    await openPanel(page);
    expect((await readLayout(page)).panels).toHaveLength(1);
    if (testInfo.project.use.viewport!.width < 1024)
      await page.keyboard.press('Escape');
    await page
      .getByRole('button', { name: 'Close all panels', exact: true })
      .click();
    expect((await readLayout(page)).panels).toHaveLength(0);
    await assertConversationMarker(page);
    await writeEvidence(testInfo, 'panel-open-focus-close', {
      originalInstance: first.panels[0].instance_id,
      duplicateOpenCreatesNoCopy: true,
      finalPanels: 0,
    });
  });
}

test('dock movement, explicit duplicate and keyboard tab selection', async ({
  page,
}, testInfo) => {
  test.skip(
    testInfo.project.use.viewport!.width < 1024,
    'Desktop dock actions transform into compact sheet/tab navigation at smaller sizes.',
  );
  await openFixture(page);
  await stableConversationMarker(page);
  await openPanel(page);
  const original = (await readLayout(page)).panels[0].instance_id;
  await page
    .getByRole('button', { name: 'Panel actions', exact: true })
    .click();
  await page
    .getByRole('menuitem', { name: 'Move to bottom', exact: true })
    .click();
  expect((await readLayout(page)).panels[0]).toMatchObject({
    instance_id: original,
    placement: 'bottom',
  });
  await expect(
    page.getByRole('region', { name: 'Bottom panels', exact: true }),
  ).toBeVisible();
  await assertConversationMarker(page);
  await screenshot(page, testInfo, 'bottom-panel');
  await page
    .getByRole('button', { name: 'Panel actions', exact: true })
    .click();
  await page
    .getByRole('menuitem', { name: 'Open another copy', exact: true })
    .click();
  const copied = await readLayout(page);
  expect(copied.panels).toHaveLength(2);
  expect(new Set(copied.panels.map((panel) => panel.instance_id)).size).toBe(2);
  const tabs = page
    .getByRole('tablist', { name: 'bottom panel tabs', exact: true })
    .getByRole('tab');
  await tabs.first().focus();
  await page.keyboard.press('ArrowRight');
  await expect(tabs.nth(1)).toBeFocused();
  await expect(tabs.nth(1)).toHaveAttribute('aria-selected', 'true');
  await page.keyboard.press('Home');
  await expect(tabs.first()).toBeFocused();
  await expect(tabs.first()).toHaveAttribute('aria-selected', 'true');
  await assertConversationMarker(page);
  await writeEvidence(testInfo, 'independent-panel-instances', copied);
});

test('keyboard and pointer splitters enforce bounds and collapse/restore', async ({
  page,
}, testInfo) => {
  test.skip(
    testInfo.project.use.viewport!.width < 1024,
    'Compact layouts use a sheet or tab and do not expose desktop splitters.',
  );
  await openFixture(page);
  await stableConversationMarker(page);
  await openPanel(page);
  const navigation = page.getByRole('separator', {
    name: 'Resize navigation',
    exact: true,
  });
  await navigation.focus();
  await page.keyboard.press('Home');
  await expect
    .poll(async () => (await readLayout(page)).navigation.size)
    .toBe(200);
  await page.keyboard.press('Shift+ArrowRight');
  await expect
    .poll(async () => (await readLayout(page)).navigation.size)
    .toBe(248);
  await page.keyboard.press('End');
  await expect
    .poll(async () => (await readLayout(page)).navigation.size)
    .toBe(320);
  await page.keyboard.press('Enter');
  await expect
    .poll(async () => (await readLayout(page)).navigation.collapsed)
    .toBe(true);
  await page
    .getByRole('button', { name: 'Expand navigation', exact: true })
    .click();
  await expect
    .poll(async () => (await readLayout(page)).navigation.collapsed)
    .toBe(false);
  const side = page.getByRole('separator', {
    name: 'Resize side panel',
    exact: true,
  });
  await side.focus();
  await page.keyboard.press('Home');
  await expect.poll(async () => (await readLayout(page)).side.size).toBe(320);
  const box = await side.boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.move(
    box!.x + box!.width / 2,
    box!.y + Math.min(100, box!.height / 2),
  );
  await page.mouse.down();
  await page.mouse.move(box!.x - 160, box!.y + Math.min(100, box!.height / 2), {
    steps: 20,
  });
  await page.mouse.up();
  expect((await readLayout(page)).side.size).toBeGreaterThanOrEqual(320);
  await assertNoOverflow(page);
  await assertConversationMarker(page);
  await screenshot(page, testInfo, 'splitter-bounds-and-pointer');
  await writeEvidence(testInfo, 'final-resized-layout', await readLayout(page));
});

test('responsive transformations retain open resource identity', async ({
  page,
}, testInfo) => {
  test.skip(
    testInfo.project.use.viewport!.width !== 1440,
    'One continuous desktop→tablet→phone→desktop resize per engine; fixed size flows run on all projects.',
  );
  await openFixture(page);
  await stableConversationMarker(page);
  await openPanel(page);
  const original = (await readLayout(page)).panels[0];
  for (const viewport of [
    { width: 820, height: 1180 },
    { width: 390, height: 844 },
    { width: 360, height: 800 },
    { width: 1440, height: 900 },
  ]) {
    await page.setViewportSize(viewport);
    await expect
      .poll(async () =>
        (await readLayout(page)).panels.map((panel) => panel.instance_id),
      )
      .toContain(original.instance_id);
    await assertConversationMarker(page);
    await assertNoOverflow(page);
    await screenshot(page, testInfo, `retained-panel-${viewport.width}`);
  }
});

test('closing a compact notes sheet returns focus to the connected Open panel trigger', async ({
  page,
}, testInfo) => {
  test.skip(
    testInfo.project.use.viewport!.width >= 1024,
    'Notes use a sheet below the desktop breakpoint.',
  );
  await openFixture(page);
  await stableConversationMarker(page);
  await openPanel(page);
  await page
    .getByRole('dialog', { name: 'Workspace notes', exact: true })
    .getByRole('button', { name: 'Close', exact: true })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(
    page.getByRole('button', { name: 'Open panel', exact: true }),
  ).toBeFocused();
  await assertConversationMarker(page);
  await writeEvidence(testInfo, 'compact-sheet-focus-return', {
    trigger: 'Open panel',
    connected: true,
  });
});

test('closing the final desktop panel or all panels returns focus to Open panel', async ({
  page,
}, testInfo) => {
  test.skip(
    testInfo.project.use.viewport!.width < 1024,
    'Desktop dock actions have separate compact sheet/tab navigation.',
  );
  await openFixture(page);
  await stableConversationMarker(page);
  await openPanel(page);
  await page
    .getByRole('button', { name: 'Panel actions', exact: true })
    .click();
  await page
    .getByRole('menuitem', { name: 'Close panel', exact: true })
    .click();
  await expect.poll(async () => (await readLayout(page)).panels.length).toBe(0);
  await expect(
    page.getByRole('button', { name: 'Open panel', exact: true }),
  ).toBeFocused();
  await openPanel(page);
  await openPanel(page, 'Activity preview');
  expect((await readLayout(page)).panels).toHaveLength(2);
  await page
    .getByRole('button', { name: 'Close all panels', exact: true })
    .click();
  await expect.poll(async () => (await readLayout(page)).panels.length).toBe(0);
  await expect(
    page.getByRole('button', { name: 'Open panel', exact: true }),
  ).toBeFocused();
  await assertConversationMarker(page);
  await writeEvidence(testInfo, 'desktop-panel-close-focus-return', {
    finalPanel: 'Open panel',
    allPanels: 'Open panel',
  });
});

test('moving a focused dock follows its panel and collapsing returns focus to Open panel', async ({
  page,
}, testInfo) => {
  test.skip(
    testInfo.project.use.viewport!.width < 1024,
    'Desktop move and collapse focus use dock tabs.',
  );
  await openFixture(page);
  await stableConversationMarker(page);
  await openPanel(page);
  await page
    .getByRole('button', { name: 'Panel actions', exact: true })
    .click();
  await page
    .getByRole('menuitem', { name: 'Move to bottom', exact: true })
    .click();
  const bottom = page.getByRole('region', {
    name: 'Bottom panels',
    exact: true,
  });
  await expect(
    bottom.getByRole('tab', { name: 'Workspace notes', exact: true }),
  ).toBeFocused();
  await bottom
    .getByRole('button', { name: 'Panel actions', exact: true })
    .click();
  await page
    .getByRole('menuitem', { name: 'Collapse panel', exact: true })
    .click();
  await expect(
    page.getByRole('button', { name: 'Open panel', exact: true }),
  ).toBeFocused();
  expect((await readLayout(page)).bottom.collapsed).toBe(true);
  await assertConversationMarker(page);
  await writeEvidence(testInfo, 'move-collapse-focus-continuity', {
    moved: 'Workspace notes tab in Bottom panels',
    collapsed: 'Open panel',
  });
});

test('a command opened on desktop uses the current compact layout after resize', async ({
  page,
}, testInfo) => {
  test.skip(
    testInfo.project.use.viewport!.width !== 1440,
    'One live desktop-to-phone command transition per engine.',
  );
  await openFixture(page);
  await stableConversationMarker(page);
  await page
    .getByRole('button', { name: 'Workspace commands', exact: true })
    .click();
  await expect(
    page.getByRole('dialog', { name: 'Workspace commands', exact: true }),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page
    .getByRole('searchbox', { name: 'Find a workspace command', exact: true })
    .fill('Workspace notes');
  // Palette results are listbox options; the field keeps focus.
  await page
    .getByRole('option', { name: 'Open Workspace notes', exact: true })
    .click();
  await expect(
    page.getByRole('dialog', { name: 'Workspace notes', exact: true }),
  ).toBeVisible();
  await expect(
    page
      .locator('.sample-panel:visible')
      .getByRole('heading', { name: 'Workspace notes', exact: true }),
  ).toBeVisible();
  const layout = await readLayout(page);
  expect(layout.panels).toHaveLength(1);
  await expect(
    page.getByRole('region', { name: 'Side panels', exact: true }),
  ).toHaveCount(0);
  await assertConversationMarker(page);
  await writeEvidence(testInfo, 'command-uses-latest-compact-layout', layout);
});

for (const region of ['navigation', 'side', 'bottom'] as const) {
  test(`pointer collapse of ${region} persists and restores the same resource`, async ({
    page,
  }, testInfo) => {
    test.skip(
      testInfo.project.use.viewport!.width !== 1440,
      'All three pointer-collapse paths run once per engine at desktop size.',
    );
    await openFixture(page);
    await stableConversationMarker(page);
    if (region !== 'navigation') await openPanel(page);
    if (region === 'bottom') {
      await page
        .getByRole('button', { name: 'Panel actions', exact: true })
        .click();
      await page
        .getByRole('menuitem', { name: 'Move to bottom', exact: true })
        .click();
    }
    const original = await readLayout(page);
    const name =
      region === 'navigation' ? 'Resize navigation' : `Resize ${region} panel`;
    const separator = page.getByRole('separator', { name, exact: true });
    await expect(separator).toBeVisible();
    const box = (await separator.boundingBox())!;
    const viewport = page.viewportSize()!;
    const x = box.x + Math.min(box.width / 2, 100);
    const y = box.y + Math.min(box.height / 2, 100);
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(
      region === 'navigation' ? 1 : region === 'side' ? viewport.width - 1 : x,
      region === 'bottom' ? viewport.height - 1 : y,
      { steps: 30 },
    );
    await page.mouse.up();
    await page.evaluate(
      () =>
        new Promise<void>((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
        ),
    );
    await writeEvidence(testInfo, `pointer-${region}-collapse-observed`, {
      original,
      after: await readLayout(page),
      geometry: await page
        .locator(
          region === 'navigation' ? '#navigation-pane' : `#${region}-pane`,
        )
        .evaluate((element) => {
          const bounds = element.getBoundingClientRect();
          return { width: bounds.width, height: bounds.height };
        }),
    });
    await expect
      .poll(async () => (await readLayout(page))[region].collapsed)
      .toBe(true);
    await assertConversationMarker(page);
    await page.reload();
    await expect
      .poll(async () => (await readLayout(page))[region].collapsed)
      .toBe(true);
    expect((await readLayout(page)).panels).toEqual(original.panels);
    if (region === 'navigation') {
      await page
        .getByRole('button', { name: 'Expand navigation', exact: true })
        .click();
    } else {
      await page
        .getByRole('complementary', { name: 'Panel rail', exact: true })
        .getByRole('button', { name: 'Workspace notes', exact: true })
        .click();
      await expect(
        page
          .locator('.sample-panel:visible')
          .getByRole('heading', { name: 'Workspace notes', exact: true }),
      ).toBeVisible();
    }
    await expect
      .poll(async () => (await readLayout(page))[region].collapsed)
      .toBe(false);
    expect((await readLayout(page)).panels).toEqual(original.panels);
    await expect(
      page.getByRole('separator', { name, exact: true }),
    ).toBeVisible();
  });
}

async function range(page: Page, name: string) {
  const separator = page.getByRole('separator', { name, exact: true });
  await expect(separator).toBeVisible();
  await expect
    .poll(
      () =>
        separator.evaluate((element) =>
          ['aria-valuemin', 'aria-valuemax', 'aria-valuenow'].every(
            (attribute) => {
              const value = element.getAttribute(attribute);
              return (
                value !== null && value !== '' && Number.isFinite(Number(value))
              );
            },
          ),
        ),
      { message: `${name} must expose its numeric accessible range` },
    )
    .toBe(true);
  const result = await separator.evaluate((element) => ({
    name: element.getAttribute('aria-label'),
    controls: element.getAttribute('aria-controls'),
    targetExists: !!document.getElementById(
      element.getAttribute('aria-controls') ?? '',
    ),
    min: Number(element.getAttribute('aria-valuemin')),
    max: Number(element.getAttribute('aria-valuemax')),
    now: Number(element.getAttribute('aria-valuenow')),
    orientation: element.getAttribute('aria-orientation'),
  }));
  expect(result.controls).toBeTruthy();
  expect(result.targetExists).toBe(true);
  expect(result.now).toBeGreaterThanOrEqual(result.min);
  expect(result.now).toBeLessThanOrEqual(result.max);
  return result;
}

test('all three separators expose live ranges and controls through registration changes', async ({
  page,
}, testInfo) => {
  test.skip(
    testInfo.project.use.viewport!.width < 1024,
    'Desktop splitter semantics run at both desktop widths in every engine; compact layouts intentionally omit splitters.',
  );
  await openFixture(page);
  await stableConversationMarker(page);
  const snapshots = [await range(page, 'Resize navigation')];
  const navigation = page.getByRole('separator', {
    name: 'Resize navigation',
    exact: true,
  });
  await navigation.focus();
  await page.keyboard.press('Home');
  const navigationSmall = await range(page, 'Resize navigation');
  await page.keyboard.press('ArrowRight');
  await expect
    .poll(async () => (await range(page, 'Resize navigation')).now)
    .not.toBe(navigationSmall.now);
  await page.keyboard.press('Enter');
  await page
    .getByRole('button', { name: 'Expand navigation', exact: true })
    .click();
  snapshots.push(await range(page, 'Resize navigation'));
  await openPanel(page);
  const side = page.getByRole('separator', {
    name: 'Resize side panel',
    exact: true,
  });
  snapshots.push(await range(page, 'Resize side panel'));
  await side.focus();
  await page.keyboard.press('Home');
  const sideSmall = await range(page, 'Resize side panel');
  await page.keyboard.press('ArrowLeft');
  await expect
    .poll(async () => (await range(page, 'Resize side panel')).now)
    .not.toBe(sideSmall.now);
  const beforeDrag = await range(page, 'Resize side panel');
  const bounds = await side.boundingBox();
  await page.mouse.move(bounds!.x + bounds!.width / 2, bounds!.y + 100);
  await page.mouse.down();
  await page.mouse.move(bounds!.x - 70, bounds!.y + 100, { steps: 12 });
  await page.mouse.up();
  await expect
    .poll(async () => (await range(page, 'Resize side panel')).now)
    .not.toBe(beforeDrag.now);
  snapshots.push(await range(page, 'Resize side panel'));
  await page
    .getByRole('button', { name: 'Panel actions', exact: true })
    .click();
  await page
    .getByRole('menuitem', { name: 'Move to bottom', exact: true })
    .click();
  await expect(page.getByRole('menu')).toHaveCount(0);
  await expect(
    page
      .getByRole('region', { name: 'Bottom panels', exact: true })
      .getByRole('tab', { name: 'Workspace notes', exact: true }),
  ).toBeFocused();
  await expect(side).toHaveCount(0);
  const bottom = page.getByRole('separator', {
    name: 'Resize bottom panel',
    exact: true,
  });
  snapshots.push(await range(page, 'Resize bottom panel'));
  await bottom.focus();
  await expect(bottom).toBeFocused();
  await page.keyboard.press('Home');
  const bottomSmall = await range(page, 'Resize bottom panel');
  await expect(bottom).toBeFocused();
  await page.keyboard.press('ArrowUp');
  await expect
    .poll(async () => (await range(page, 'Resize bottom panel')).now)
    .not.toBe(bottomSmall.now);
  await expect(bottom).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(bottom).toHaveCount(0);
  await expect(
    page.getByRole('button', { name: 'Open panel', exact: true }),
  ).toBeFocused();
  await page
    .getByRole('complementary', { name: 'Panel rail', exact: true })
    .getByRole('button', { name: 'Workspace notes', exact: true })
    .click();
  snapshots.push(await range(page, 'Resize bottom panel'));
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole('separator')).toHaveCount(0);
  await page.setViewportSize(testInfo.project.use.viewport!);
  snapshots.push(await range(page, 'Resize navigation'));
  snapshots.push(await range(page, 'Resize bottom panel'));
  await accessibility(page, testInfo, 'registered-separator-axe');
  await assertConversationMarker(page);
  await writeEvidence(testInfo, 'registered-separator-ranges', snapshots);
});

test('emulated touch drag resizes a wide tablet dock without replacing the conversation', async ({
  page,
  context,
}, testInfo) => {
  test.skip(
    testInfo.project.name !== 'chromium-tablet',
    'One Chromium CDP emulated touch drag; this is not physical tablet validation.',
  );
  await page.setViewportSize({ width: 1440, height: 900 });
  await openFixture(page);
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
  await openPanel(page);
  const separator = page.getByRole('separator', {
    name: 'Resize side panel',
    exact: true,
  });
  const before = await readLayout(page);
  const bounds = await separator.boundingBox();
  expect(bounds).not.toBeNull();
  const session = await context.newCDPSession(page);
  const x = bounds!.x + bounds!.width / 2;
  const y = bounds!.y + Math.min(100, bounds!.height / 2);
  await session.send('Input.dispatchTouchEvent', {
    type: 'touchStart',
    touchPoints: [{ x, y, id: 1 }],
  });
  for (let step = 1; step <= 20; step++) {
    await session.send('Input.dispatchTouchEvent', {
      type: 'touchMove',
      touchPoints: [{ x: x - step * 4, y, id: 1 }],
    });
  }
  await session.send('Input.dispatchTouchEvent', {
    type: 'touchEnd',
    touchPoints: [],
  });
  await session.detach();
  await expect
    .poll(async () => (await readLayout(page)).side.size)
    .toBeGreaterThan(before.side.size + 40);
  const after = await readLayout(page);
  const protocol = await page.evaluate(() => {
    const { transport, controller } = (window as FixtureWindow)
      .__ROW_BOT_FIXTURE__;
    return {
      counters: transport.counters,
      selected: controller.getSnapshot().selectedConversationId,
    };
  });
  await writeEvidence(testInfo, 'emulated-touch-drag', {
    method:
      'CDP touchStart/touchMove/touchEnd on an emulated touch context; no physical-device claim',
    before,
    after,
    protocol,
  });
  await assertConversationMarker(page);
  await assertNoOverflow(page);
  expect(protocol.selected).toBe('conversation-a');
  expect(protocol.counters.streams).toBe(1);
  expect(protocol.counters.commands).toBe(0);
  await screenshot(page, testInfo, 'emulated-touch-resized-dock');
});
