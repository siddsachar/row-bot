import { expect, screenshot, test } from './evidence';
import { openFixture, type FixtureWindow } from './fixture';
import { openPanel } from './panel-helpers';

async function expectSingleToolbarRow(page: import('@playwright/test').Page) {
  const toolbar = page.locator('.composer-toolbar');
  await expect(toolbar).toBeAttached();
  const layout = await toolbar.evaluate((element) => {
    const composer = element.closest('.composer')!;
    // A one-line composer places +, the field and the actions in the field's
    // grid (the toolbar is display: contents); compare their centres.
    const single = composer.hasAttribute('data-single-line');
    const row = single
      ? ['.composer-control-cluster', '.message-composer', '.composer-actions']
          .map((selector) => composer.querySelector(selector)!)
          .filter(Boolean)
      : Array.from(element.children).filter(
          (child) => getComputedStyle(child).display !== 'none',
        );
    const container = single
      ? composer.querySelector('.composer-field')!
      : element;
    const bounds = row.map((child) => child.getBoundingClientRect());
    return {
      tops: bounds.map((box) => (single ? box.top + box.height / 2 : box.top)),
      right: Math.max(...bounds.map((box) => box.right)),
      toolbarRight: container.getBoundingClientRect().right,
      overflow: getComputedStyle(composer).overflowY,
      scrollWidth: composer.scrollWidth,
      clientWidth: composer.clientWidth,
    };
  });
  expect(Math.max(...layout.tops) - Math.min(...layout.tops)).toBeLessThan(8);
  expect(layout.right).toBeLessThanOrEqual(layout.toolbarRight + 1);
  expect(layout.overflow).toBe('visible');
  expect(layout.scrollWidth).toBeLessThanOrEqual(layout.clientWidth + 1);
}

test('context card and composer stay compact through panels, narrowing, keyboard use, and 200% zoom', async ({
  page,
}, info) => {
  test.skip(
    info.project.name !== 'chromium-desktop',
    'One focused Chromium layout pass',
  );
  await openFixture(page);
  await page.evaluate(() => {
    const fixture = (window as FixtureWindow).__ROW_BOT_FIXTURE__;
    const controller = fixture.controller;
    const commands = Array.from({ length: 15 }, (_, index) => ({
      id: `status-${index}`,
      token: `/status-${index}`,
      aliases: [],
      label: `Status ${index}`,
      description: `Inspect local status ${index}.`,
      icon: index === 0 ? 'monitor_heart' : 'unknown_local_icon',
      category: 'Info',
      argument_mode: 'none' as const,
      argument_hint: '',
      handler_kind: 'status' as const,
    }));
    const composer = {
      schema_version: 1 as const,
      conversation_id: 'conversation-a',
      conversation_revision: '1',
      composer_revision: 'slice-6',
      library: { availability: 'available' as const, revision: '1' },
      smart_skills_off: false,
      active_skills: [],
      suggestions: [],
      commands,
      command_total: commands.length,
      commands_truncated: false,
    };
    Object.assign(fixture.transport, { composer: async () => composer });
    (controller as unknown as { update(patch: unknown): void }).update({
      workspace: {
        conversation_id: 'conversation-a',
        revision: '1',
        controls: {
          model_selection: {
            provider_id: 'fixture',
            model_ref: 'fixture/model',
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
      handshake: {
        ...controller.getSnapshot().handshake,
        models: [
          {
            provider_id: 'fixture',
            model_ref: 'fixture/model',
            label: 'Local model',
            available: true,
          },
        ],
      },
    });
  });
  await expect(page.getByRole('button', { name: 'Model' })).toBeVisible();
  const draft = page.getByRole('textbox', { name: 'Message' });
  await draft.fill('/status');
  const palette = page.getByRole('listbox', { name: 'Slash commands' });
  await expect(palette.getByRole('option')).toHaveCount(12);
  await expect(palette.locator('.slash-palette-icon svg')).toHaveCount(12);
  await draft.press('ArrowDown');
  await expect(draft).toBeFocused();
  await expect(draft).toHaveAttribute(
    'aria-activedescendant',
    (await palette.getByRole('option').nth(1).getAttribute('id'))!,
  );
  for (let index = 0; index < 9; index += 1) await draft.press('ArrowDown');
  await expect
    .poll(() => palette.evaluate((element) => element.scrollTop))
    .toBeGreaterThan(0);
  expect(
    await palette.evaluate((element) =>
      Array.from(element.querySelectorAll('.slash-palette-row')).every(
        (row) => row.scrollWidth <= row.clientWidth + 1,
      ),
    ),
  ).toBe(true);
  await screenshot(page, info, 'slice6-slash');
  await draft.press('Escape');
  await expect(palette).toHaveCount(0);
  await draft.fill('');
  const card = page.getByRole('complementary', {
    name: 'Conversation details',
  });
  await expect(card).toBeVisible();
  const height = await card.evaluate((element) => ({
    card: element.getBoundingClientRect().height,
    workspace: element.closest('.chat-workspace')!.getBoundingClientRect()
      .height,
    scroll: getComputedStyle(element.querySelector('.context-rail-body')!)
      .overflowY,
  }));
  expect(height.card).toBeLessThan(height.workspace * 0.8);
  expect(height.scroll).toBe('auto');
  await expectSingleToolbarRow(page);
  await screenshot(page, info, 'slice6-wide');

  await page.evaluate(() => {
    const controller = (window as FixtureWindow).__ROW_BOT_FIXTURE__.controller;
    const workspace = controller.getSnapshot().workspace!;
    const resources = Array.from({ length: 20 }, (_, index) => ({
      resource_ref: `fixture:resource-${index}`,
      conversation_revision: '1',
      resource_revision: '1',
      title: `Synthetic design ${index}`,
      available: false,
      binding: {
        binding_id: `binding-${index}`,
        kind: 'artifact' as const,
        resource_id: `resource-${index}`,
        role: 'reference' as const,
        revision: '1',
      },
    }));
    (controller as unknown as { update(patch: unknown): void }).update({
      workspace: { ...workspace, resources },
    });
  });
  const body = card.locator('.context-rail-body');
  await expect
    .poll(() =>
      body.evaluate((element) => element.scrollHeight - element.clientHeight),
    )
    .toBeGreaterThan(0);
  await body.evaluate((element) => {
    element.scrollTop = element.scrollHeight;
  });
  await expect
    .poll(() => body.evaluate((element) => element.scrollTop))
    .toBeGreaterThan(0);
  // No visible title (B221): the card's actions stay in its top row while
  // its sections scroll.
  await expect(
    card.getByRole('button', { name: 'Conversation actions' }),
  ).toBeVisible();
  const anchor = await card.evaluate((element) => ({
    cardTop: element.getBoundingClientRect().top,
    headingTop: element
      .querySelector('.context-rail-heading')!
      .getBoundingClientRect().top,
  }));
  expect(anchor.cardTop).toBeGreaterThanOrEqual(0);
  expect(anchor.headingTop).toBeGreaterThanOrEqual(anchor.cardTop);
  await screenshot(page, info, 'slice6-context-populated');
  await page.evaluate(() => {
    const controller = (window as FixtureWindow).__ROW_BOT_FIXTURE__.controller;
    (controller as unknown as { update(patch: unknown): void }).update({
      workspace: { ...controller.getSnapshot().workspace!, resources: [] },
    });
  });

  const actions = card.getByRole('button', { name: 'Conversation actions' });
  await actions.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('menu')).toBeVisible();
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Escape');
  await expect(actions).toBeFocused();

  await openPanel(page);
  await expectSingleToolbarRow(page);
  await openPanel(page, 'Activity preview');
  await expectSingleToolbarRow(page);
  await screenshot(page, info, 'slice6-panel');

  await page.setViewportSize({ width: 900, height: 700 });
  await page.getByRole('button', { name: 'Close all panels' }).click();
  await expectSingleToolbarRow(page);
  await screenshot(page, info, 'slice6-narrow');

  await page.setViewportSize({ width: 700, height: 900 });
  await page.evaluate(() => {
    document.documentElement.style.zoom = '2';
  });
  await expectSingleToolbarRow(page);
  const extras = page.getByRole('button', { name: 'Attachments and audio' });
  if (await extras.isVisible()) {
    await extras.click();
    await expect(
      page.getByRole('button', { name: 'Attach file' }),
    ).toBeVisible();
    await extras.click();
  }
  // 700px at 200% zoom is a 350px composer: one line, with approvals, the
  // model and context usage in + (the mode switches after the zoom lands).
  await expect(page.locator('.composer').first()).toHaveAttribute(
    'data-single-line',
  );
  await page
    .getByRole('button', { name: 'Add files and more', exact: true })
    .click();
  // CSS zoom on the root skews floating-menu geometry for pointer hit
  // tests (browser zoom does not); the keyboard reaches the submenu.
  await page.getByRole('menuitem', { name: /^Approvals/ }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(
    page.getByRole('menuitemradio', { name: /^Ask/ }),
  ).toHaveAttribute('aria-checked', 'true');
  await page.keyboard.press('Escape');
  await page.keyboard.press('Escape');
  await page
    .getByRole('button', { name: 'Add files and more', exact: true })
    .click();
  await expect(page.getByRole('menuitem', { name: /^Mode\b/ })).toBeVisible();
  await page.keyboard.press('Escape');
  await screenshot(page, info, 'slice6-zoom-200');
});

test('Conversation details floats beside the reading column at desktop widths (B221)', async ({
  page,
}, info) => {
  test.skip(
    info.project.name !== 'chromium-desktop',
    'One focused Chromium layout pass',
  );
  await openFixture(page);
  await page.evaluate(() => {
    const { controller } = (window as FixtureWindow).__ROW_BOT_FIXTURE__;
    const projection = controller.getSnapshot().projection!;
    const base = projection.rows[0];
    const text =
      'A long synthetic line about tides and harbours that wraps across the whole reading column. '.repeat(
        6,
      );
    const rows = Array.from({ length: 24 }, (_, index) => ({
      ...base,
      id: `b221-${index}`,
      message_id: `b221-${index}`,
      role: index % 2 ? ('assistant' as const) : ('user' as const),
      blocks: [{ id: `b221-${index}-text`, type: 'markdown' as const, text }],
    }));
    (controller as unknown as { update(patch: unknown): void }).update({
      history: null,
      projection: { ...projection, rows },
    });
  });
  const card = page.getByRole('complementary', {
    name: 'Conversation details',
  });
  const toggle = page.getByRole('button', {
    name: 'Conversation details',
    exact: true,
  });
  for (const width of [1280, 1440, 1920]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(card).toBeVisible();
    await expect(toggle).toHaveAttribute('aria-pressed', 'true');
    const layout = await page.evaluate(() => {
      const box = (element: Element) => element.getBoundingClientRect();
      const transcript = document.querySelector<HTMLElement>('.transcript')!;
      const buttons = Array.from(
        document.querySelectorAll(
          '.chat-content > .conversation-heading .conversation-actions .button',
        ),
      ).map(box);
      return {
        conversation: box(document.querySelector('.chat-workspace')!),
        transcript: box(transcript),
        scrollbar: transcript.offsetWidth - transcript.clientWidth,
        card: box(document.querySelector('.context-card')!),
        composer: box(document.querySelector('.composer')!),
        buttonsBottom: Math.max(...buttons.map((button) => button.bottom)),
        lastButtonRight: Math.max(...buttons.map((button) => button.right)),
        texts: Array.from(
          document.querySelectorAll('.transcript .message-text'),
        ).map(box),
      };
    });
    // The scroller spans the conversation: its scrollbar is at the app's edge.
    expect(
      Math.abs(layout.transcript.right - layout.conversation.right),
    ).toBeLessThanOrEqual(1);
    // The card sits under the header's buttons, its right edge on the last
    // one's, left of the scrollbar and above the composer.
    expect(layout.card.top).toBeGreaterThanOrEqual(layout.buttonsBottom);
    expect(
      Math.abs(layout.card.right - layout.lastButtonRight),
    ).toBeLessThanOrEqual(1);
    expect(layout.card.right).toBeLessThanOrEqual(
      layout.transcript.right - layout.scrollbar,
    );
    expect(layout.card.bottom).toBeLessThanOrEqual(layout.composer.top);
    // No message text runs under the card.
    expect(layout.texts.length).toBeGreaterThan(0);
    for (const text of layout.texts)
      expect(
        text.right > layout.card.left &&
          text.left < layout.card.right &&
          text.bottom > layout.card.top &&
          text.top < layout.card.bottom,
      ).toBe(false);
    await screenshot(page, info, `b221-details-${width}`);
  }
  // The toggle hides the card (the column re-centres) and brings it back.
  await toggle.click();
  await expect(card).toBeHidden();
  await expect(toggle).toHaveAttribute('aria-pressed', 'false');
  await toggle.click();
  await expect(card).toBeVisible();
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
});
