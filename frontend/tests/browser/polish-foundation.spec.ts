import { readFileSync } from 'node:fs';
import type { Page } from '@playwright/test';
import {
  accessibility,
  expect,
  screenshot,
  test,
  assertNoOverflow,
} from './evidence';
import { openFixture, type FixtureWindow } from './fixture';

// Polish program Phase 0: foundation primitives and defects B1–B12.

async function withAppearance(page: Page, appearance: 'light' | 'dark') {
  await page.addInitScript(
    (mode) =>
      localStorage.setItem(
        'row-bot.appearance.v1',
        JSON.stringify({ version: 1, appearance: mode, accent: 'blue' }),
      ),
    appearance,
  );
}

async function withinViewport(page: Page, selector: string) {
  return page.locator(selector).evaluate((element) => {
    const box = element.getBoundingClientRect();
    return {
      inside:
        box.top >= 0 &&
        box.left >= 0 &&
        box.bottom <= innerHeight &&
        box.right <= innerWidth,
      scrolls: element.scrollHeight > element.clientHeight,
    };
  });
}

for (const appearance of ['light', 'dark'] as const) {
  test(`foundation primitives behave and pass axe in ${appearance}`, async ({
    page,
  }, info) => {
    const touch = Boolean(info.project.use.hasTouch);
    await withAppearance(page, appearance);
    await page.goto('/app-v2/primitives?fixture=normal');
    await expect(
      page.getByRole('heading', { name: 'Foundation primitives' }),
    ).toBeVisible();
    await expect(page.locator('html')).toHaveAttribute(
      'data-theme',
      appearance,
    );
    // Bundled Geist is the primary face; nothing is fetched remotely.
    expect(
      await page.evaluate(() => getComputedStyle(document.body).fontFamily),
    ).toMatch(/^Geist,/);

    const search = page.getByRole('button', { name: 'Search', exact: true });
    await expect(search).toHaveAttribute('aria-keyshortcuts', /\+K$/);
    const size = await search.evaluate(
      (element) => element.getBoundingClientRect().height,
    );
    expect(size).toBeGreaterThanOrEqual(touch ? 44 : 32);
    if (!touch) {
      expect(size).toBeLessThanOrEqual(32);
      await search.focus();
      await expect(page.getByRole('tooltip')).toContainText('Search');
      await search.blur();
    }

    const filter = page.getByRole('radiogroup', { name: 'Conversation type' });
    await filter.getByRole('radio', { name: 'All' }).focus();
    await page.keyboard.press('ArrowRight');
    await expect(filter.getByRole('radio', { name: 'Chats' })).toHaveAttribute(
      'aria-checked',
      'true',
    );

    await page.getByRole('button', { name: 'Sample model' }).click();
    const input = page.getByRole('combobox', { name: 'Search sample model' });
    await expect(input).toBeFocused();
    await input.fill('private');
    await expect(
      page.getByRole('listbox', { name: 'Sample model' }).getByRole('option'),
    ).toHaveCount(3);
    await page.keyboard.press('Enter');
    await expect(
      page.getByRole('button', { name: 'Sample model' }),
    ).toHaveAccessibleDescription('Local Small');

    // B3: long menus stay inside the viewport and scroll within.
    await page.getByRole('button', { name: 'Long sample menu' }).click();
    await expect(
      page.getByRole('menuitem', { name: 'Sample option 45' }),
    ).toBeVisible();
    const bounds = await withinViewport(page, '.menu');
    expect(bounds).toEqual({ inside: true, scrolls: true });
    await page.keyboard.press('Escape');

    await page.getByRole('button', { name: 'Open sample inspector' }).click();
    const drawer = page.getByRole('dialog', { name: 'Sample inspector' });
    await expect(drawer).toBeVisible();
    await expect(
      drawer.getByRole('heading', { name: 'Sample inspector' }),
    ).toBeFocused();
    await accessibility(page, info, `polish-primitives-${appearance}-axe`);
    await screenshot(page, info, `polish-primitives-${appearance}`);
    await page.keyboard.press('Escape');
    await expect(drawer).toHaveCount(0);
  });
}

test('route headings take focus silently until the keyboard is used (B1)', async ({
  page,
}, info) => {
  test.skip(
    info.project.name !== 'chromium-desktop',
    'One focused Chromium pass',
  );
  await page.goto('/app-v2/primitives?fixture=normal');
  const heading = page.getByRole('heading', { name: 'Component gallery' });
  await expect(heading).toBeVisible();
  // A programmatic focus target behaves like the Settings page heading.
  await heading.evaluate((element) => {
    element.tabIndex = -1;
    element.focus();
  });
  const outline = () =>
    heading.evaluate((element) => getComputedStyle(element).outlineStyle);
  expect(await outline()).toBe('none');
  await page.keyboard.press('Tab');
  await heading.evaluate((element) => element.focus());
  await expect(page.locator('html')).toHaveAttribute(
    'data-input-modality',
    'keyboard',
  );
  expect(await outline()).toBe('solid');
  await heading.click();
  await expect(page.locator('html')).toHaveAttribute(
    'data-input-modality',
    'pointer',
  );
  expect(await outline()).toBe('none');
});

test('settings section headings without an icon keep their full width (B2)', async ({
  page,
}) => {
  const entry = new URL('../../src/ui/styles/index.css', import.meta.url);
  const css = [...readFileSync(entry, 'utf8').matchAll(/@import '([^']+)';/g)]
    .map((match) => readFileSync(new URL(match[1], entry), 'utf8'))
    .join('\n');
  await page.route('**/*', (route) => route.abort());
  await page.setContent(`<!doctype html><html><head><style>${css}</style></head><body>
    <section class="settings-snapshot-section" style="width:640px">
      <header class="settings-snapshot-heading" id="plain"><div><h3>Browse public skills</h3><p>Search public sources, inspect the exact files and scanner findings, then install locally.</p></div></header>
      <header class="settings-snapshot-heading" id="icon"><svg width="18" height="18"></svg><div><h3>Remove documents</h3></div></header>
    </section></body></html>`);
  const width = (selector: string) =>
    page
      .locator(selector)
      .evaluate((element) => element.getBoundingClientRect().width);
  expect(await width('#plain > div')).toBeGreaterThan(600);
  expect(await width('#icon > div')).toBeGreaterThan(570);
  expect(await width('#icon > svg')).toBe(18);
});

async function composerFixture(page: Page, modelCount: number) {
  await page.evaluate((count) => {
    const fixture = (window as FixtureWindow).__ROW_BOT_FIXTURE__;
    const controller = fixture.controller;
    const commands = ['status', 'help'].map((id) => ({
      id,
      token: `/${id}`,
      aliases: [],
      label: id === 'status' ? 'Status' : 'Help',
      description: `Show ${id}.`,
      icon: 'monitor_heart',
      category: 'Info',
      argument_mode: 'none' as const,
      argument_hint: '',
      handler_kind: id as 'status' | 'help',
    }));
    const composer = {
      schema_version: 1 as const,
      conversation_id: 'conversation-a',
      conversation_revision: '1',
      composer_revision: 'polish-p0',
      library: { availability: 'available' as const, revision: '1' },
      smart_skills_off: false,
      active_skills: [],
      suggestions: [],
      commands,
      command_total: commands.length,
      commands_truncated: false,
    };
    Object.assign(fixture.transport, { composer: async () => composer });
    const models = Array.from({ length: count }, (_, index) => ({
      provider_id: 'fixture',
      model_ref: `fixture/model-${index}`,
      label: `Fixture model ${index + 1}`,
      available: true,
    }));
    (controller as unknown as { update(patch: unknown): void }).update({
      workspace: {
        conversation_id: 'conversation-a',
        revision: '1',
        controls: {
          model_selection: {
            provider_id: 'fixture',
            model_ref: 'fixture/model-0',
          },
          runtime_mode: 'agent',
          approval_mode: 'approve',
          profile_id: '',
        },
        profiles: [],
        resources: [],
        actions: [{ action: 'send', ready: true }],
        composer,
      },
      handshake: { ...controller.getSnapshot().handshake, models },
    });
  }, modelCount);
}

test('composer menus stay in the viewport and only one popover shows at a time (B3, B5)', async ({
  page,
}, info) => {
  test.skip(
    info.project.name !== 'chromium-desktop',
    'One focused Chromium pass',
  );
  await openFixture(page);
  await composerFixture(page, 72);
  const model = page.getByRole('button', { name: 'Model', exact: true });
  await expect(model).toBeVisible();
  await model.click();
  // The picker is a searchable listbox; the current model is marked.
  const models = page.getByRole('listbox', { name: 'Models' });
  await expect(
    models.getByRole('option', { name: 'Fixture model 1 Current' }),
  ).toBeVisible();
  expect((await withinViewport(page, '.model-picker')).inside).toBe(true);
  expect((await withinViewport(page, '.model-picker-list')).scrolls).toBe(true);
  await screenshot(page, info, 'polish-model-menu-bounded');
  await page.keyboard.press('Escape');
  // Wait for the picker to finish closing: it returns focus to its trigger
  // after unmounting, and the palette (B5) steps aside whenever the composer
  // loses focus.
  await expect(models).toHaveCount(0);
  await expect(model).toBeFocused();

  const draft = page.getByRole('textbox', { name: 'Message' });
  await draft.fill('/');
  const palette = page.getByRole('listbox', { name: 'Slash commands' });
  await expect(palette).toBeVisible();
  await page.getByRole('button', { name: 'Approvals' }).click();
  await expect(page.getByRole('menu')).toBeVisible();
  await expect(palette).toHaveCount(0);
  await page.keyboard.press('Escape');
  await draft.focus();
  await expect(palette).toBeVisible();
  await draft.fill('');
  await expect(palette).toHaveCount(0);
});

test('the context rail hides empty sections and keeps Connected quiet (B7)', async ({
  page,
}, info) => {
  test.skip(
    info.project.name !== 'chromium-desktop',
    'One focused Chromium pass',
  );
  await openFixture(page);
  await composerFixture(page, 1);
  const rail = page.getByRole('complementary', {
    name: 'Conversation context',
  });
  await expect(rail).toBeVisible();
  await expect(rail.getByRole('heading', { name: 'Working on' })).toHaveCount(
    0,
  );
  await expect(rail.locator('summary', { hasText: 'Agents' })).toBeHidden();
  const utilities = rail.locator('summary', { hasText: 'Utilities' });
  await expect(utilities).toBeVisible();
  await expect(utilities.locator('.disclosure-chevron')).toBeVisible();
  const status = page.locator('.connection-status.connected');
  await expect(status).toHaveText('Connected');
  expect(
    await status.evaluate((element) => element.getBoundingClientRect().width),
  ).toBeLessThanOrEqual(1);
  await screenshot(page, info, 'polish-context-rail-quiet');
});

test('compact navigation opens Settings after its drawer releases history', async ({
  page,
}, info) => {
  test.skip(
    info.project.name !== 'chromium-desktop',
    'One focused Chromium pass',
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await openFixture(page);
  await page
    .getByRole('button', { name: 'Toggle navigation', exact: true })
    .click();
  const drawer = page.getByRole('dialog', { name: 'Conversations' });
  await expect(drawer).toBeVisible();
  // The drawer sits on a same-URL history entry; closing it pops that entry
  // asynchronously, which must not undo the route the link opens.
  await drawer.getByRole('link', { name: 'Settings', exact: true }).click();
  await expect(drawer).toHaveCount(0);
  await expect(
    page.getByRole('heading', { name: 'Providers', exact: true }),
  ).toBeVisible();
  await expect(page).toHaveURL(/\/app-v2\/settings\/providers$/);
  await page.goBack();
  await expect(page).toHaveURL(/\/app-v2\/conversations\/conversation-a/);
  await expect(drawer).toHaveCount(0);
});

for (const appearance of ['light', 'dark']) {
  test(`shared primitives retain accessible states in ${appearance}`, async ({
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
    await page.goto('/app-v2/primitives?fixture=normal');
    await expect(
      page.getByRole('heading', { name: 'Component gallery', exact: true }),
    ).toBeVisible();
    await page.getByRole('tab', { name: 'Controls', exact: true }).focus();
    await page.keyboard.press('ArrowRight');
    await expect(
      page.getByRole('tab', { name: 'States', exact: true }),
    ).toHaveAttribute('aria-selected', 'true');
    await expect(
      page.getByRole('button', { name: 'Unavailable', exact: true }),
    ).toBeDisabled();
    await expect(
      page.getByRole('textbox', { name: 'Unavailable input', exact: true }),
    ).toBeDisabled();
    await expect(
      page.getByRole('progressbar', { name: 'Example progress', exact: true }),
    ).toHaveAttribute('value', '65');
    await assertNoOverflow(page);
    await accessibility(page, testInfo, `gallery-${appearance}-axe`);
    await screenshot(page, testInfo, `gallery-${appearance}`);
  });
}

test('modal child menus and popovers stay above the modal; suspension preserves form state', async ({
  page,
}, testInfo) => {
  await page.goto('/app-v2/primitives?fixture=normal');
  await page.getByRole('button', { name: 'Open dialog', exact: true }).click();
  await page
    .getByRole('textbox', { name: 'Example name', exact: true })
    .fill('Preserved local draft');
  await page
    .getByRole('button', { name: 'Example actions', exact: true })
    .click();
  const item = page.getByRole('menuitem', {
    name: 'Keep this idea',
    exact: true,
  });
  await expect(item).toBeVisible();
  expect(
    await item.evaluate((element) => {
      const bounds = element.getBoundingClientRect();
      return element.contains(
        document.elementFromPoint(
          bounds.x + bounds.width / 2,
          bounds.y + bounds.height / 2,
        ),
      );
    }),
  ).toBe(true);
  await item.click();
  await page.getByRole('button', { name: 'Dialog help', exact: true }).click();
  await expect(
    page.getByText('This popover belongs to the active dialog.', {
      exact: true,
    }),
  ).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(
    page.getByRole('dialog', { name: 'Sample dialog', exact: true }),
  ).toBeVisible();
  await page
    .getByRole('button', { name: 'Discard example', exact: true })
    .click();
  await expect(
    page.getByRole('alertdialog', {
      name: 'Discard this example?',
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.locator('[aria-modal="true"]')).toHaveCount(1);
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(
    page.getByRole('textbox', { name: 'Example name', exact: true }),
  ).toHaveValue('Preserved local draft');
  await expect(
    page.getByRole('button', { name: 'Discard example', exact: true }),
  ).toBeFocused();
  await accessibility(page, testInfo, 'restored-form-axe');
  await screenshot(page, testInfo, 'restored-modal-form');
  await page.keyboard.press('Escape');
  await expect(
    page.getByRole('button', { name: 'Open dialog', exact: true }),
  ).toBeFocused();
});

test('sheet close action and command search stay reachable with long content', async ({
  page,
}, testInfo) => {
  await page.goto('/app-v2/primitives?fixture=normal');
  await page
    .getByRole('textbox', { name: 'Find a command', exact: true })
    .fill('notification');
  await expect(
    page.getByRole('button', { name: 'Open sample dialog', exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole('button', { name: 'Show notification', exact: true })
    .click();
  await expect(
    page.getByText('Command completed', { exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Open sheet', exact: true }).click();
  await expect(
    page.getByRole('dialog', { name: 'Sample sheet', exact: true }),
  ).toBeVisible();
  // Sheets close from their header action (the footer was removed for sheets).
  const close = page
    .getByRole('dialog')
    .getByRole('button', { name: 'Close dialog', exact: true });
  await expect(close).toBeInViewport();
  expect(
    await close.evaluate((element) => {
      const bounds = element.getBoundingClientRect();
      return element.contains(
        document.elementFromPoint(
          bounds.x + bounds.width / 2,
          bounds.y + bounds.height / 2,
        ),
      );
    }),
    'The sheet close action must remain visible and reachable while a notification is present',
  ).toBe(true);
  await assertNoOverflow(page);
  await screenshot(page, testInfo, 'sheet-long-content-footer');
  await close.click();
  await expect(
    page.getByRole('button', { name: 'Open sheet', exact: true }),
  ).toBeFocused();
});

test('the knowledge graph settles in a same-origin worker and comes back settled (B250)', async ({
  page,
}, info) => {
  const headers = {
    'X-Fixture-Token': process.env.ROW_BOT_BROWSER_CONTROL_TOKEN!,
    Origin: new URL(process.env.ROW_BOT_BROWSER_BASE_URL!).origin,
  };
  const seed = (state: 'populated' | 'empty') =>
    page.request.post(`/__p4_fixture/knowledge/${state}`, { headers });
  expect((await seed('populated')).ok()).toBe(true);
  try {
    const workers: string[] = [];
    const refused: string[] = [];
    page.on('worker', (worker) => workers.push(worker.url()));
    page.on('console', (message) => {
      if (/Content Security Policy/i.test(message.text()))
        refused.push(message.text());
    });
    await page.goto('/app-v2/?tab=knowledge');
    const graph = page.locator('.knowledge-network-shell');
    await expect(graph).toHaveAttribute(
      'data-renderer-status',
      /^(ready|failed)$/,
    );
    test.skip(
      (await graph.getAttribute('data-renderer-status')) === 'failed',
      'No WebGL in this browser: Knowledge lists the memories instead.',
    );
    // Drawn at once, then laid out by a worker this origin serves.
    await expect.poll(() => workers.length).toBe(1);
    expect(new URL(workers[0]).origin).toBe(new URL(page.url()).origin);
    await expect(graph).toHaveAttribute('data-layout', 'settled', {
      timeout: 15_000,
    });
    expect(refused).toEqual([]);
    await screenshot(page, info, 'knowledge-graph-settled');

    // Coming back to the tab shows the settled picture without a new layout.
    await page.getByRole('tab', { name: 'Overview', exact: true }).click();
    await page.getByRole('tab', { name: 'Knowledge', exact: true }).click();
    await expect(graph).toHaveAttribute('data-renderer-status', 'ready');
    await expect(graph).toHaveAttribute('data-layout', 'settled');
    expect(workers).toHaveLength(1);
  } finally {
    await seed('empty');
  }
});
