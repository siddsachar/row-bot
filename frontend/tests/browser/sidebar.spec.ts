import { assertNoOverflow, expect, screenshot, test } from './evidence';
import { openFixture, type FixtureWindow } from './fixture';
import { blockFixtureServiceWorkers } from './unified-helpers';

test('the sidebar leads with New chat, keeps Home in its header and Agents under the conversations, and fits a large nameless Buddy above Settings (B225, B268)', async ({
  context,
  page,
}, info) => {
  const desktop = info.project.use.viewport!.width >= 1024;
  await blockFixtureServiceWorkers(context);
  const headers = {
    'X-Fixture-Token': process.env.ROW_BOT_BROWSER_CONTROL_TOKEN!,
    Origin: new URL(process.env.ROW_BOT_BROWSER_BASE_URL!).origin,
  };
  expect(
    (await page.request.post('/__p4_fixture/buddy', { headers })).ok(),
  ).toBe(true);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/app-v2/conversations/p1-browser-a');
  await expect(
    page.getByRole('heading', { name: 'Phase 1 conversation A', exact: true }),
  ).toBeVisible();
  const nav = page.getByRole('navigation', {
    name: 'Workspace navigation',
    exact: true,
  });
  if (!(await nav.isVisible()))
    await page
      .getByRole('button', { name: 'Toggle navigation', exact: true })
      .click();
  // New chat is the one primary button; ▾ holds the agents.
  const newChat = nav.getByRole('button', { name: 'New chat', exact: true });
  await expect(newChat).toBeVisible();
  await expect(
    nav.getByRole('button', { name: 'New chat with an agent…', exact: true }),
  ).toBeVisible();
  // Home is an icon in the header row, above New chat (beside Search and
  // collapse on desktop), and current only while Home shows.
  const home = nav.getByRole('link', { name: 'Home', exact: true });
  await expect(home).toBeVisible();
  await expect(home).not.toHaveAttribute('aria-current', 'page');
  const homeBox = (await home.boundingBox())!;
  expect(homeBox.y + homeBox.height).toBeLessThanOrEqual(
    (await newChat.boundingBox())!.y + 1,
  );
  if (desktop)
    for (const name of ['Workspace commands', 'Toggle navigation']) {
      const box = (await nav
        .getByRole('button', { name, exact: true })
        .boundingBox())!;
      expect(Math.abs(box.y - homeBox.y)).toBeLessThanOrEqual(1);
    }
  // Agents follow the conversations, Show all and the library link, still
  // in the scrolling list above Buddy's footer.
  const agents = nav.getByRole('region', { name: 'Agents', exact: true });
  await agents.scrollIntoViewIfNeeded();
  await expect(
    agents.getByRole('group', { name: 'Favourite agents', exact: true }),
  ).toBeVisible();
  await expect(
    agents.getByRole('button', { name: /^All agents \(\d+\)/ }),
  ).toBeVisible();
  const libraryBox = (await nav
    .getByRole('link', { name: 'Conversation library', exact: true })
    .boundingBox())!;
  const agentsBox = (await agents.boundingBox())!;
  expect(agentsBox.y).toBeGreaterThanOrEqual(
    libraryBox.y + libraryBox.height - 1,
  );
  const buddy = nav.getByRole('complementary', {
    name: 'Buddy companion',
    exact: true,
  });
  expect(agentsBox.y + agentsBox.height).toBeLessThanOrEqual(
    (await buddy.boundingBox())!.y + 1,
  );
  // Buddy: a large avatar, its name only in the tooltip, Settings below.
  const avatar = buddy.getByRole('button', {
    name: 'Buddy settings',
    exact: true,
  });
  await expect(avatar).toBeVisible();
  const avatarBox = (await avatar.boundingBox())!;
  expect(avatarBox.width).toBeGreaterThanOrEqual(112);
  const name = buddy.locator('.buddy-companion-name');
  expect((await name.boundingBox())!.width).toBeLessThanOrEqual(1);
  const settings = nav.getByRole('link', { name: 'Settings', exact: true });
  await settings.scrollIntoViewIfNeeded();
  const buddyBox = (await buddy.boundingBox())!;
  const settingsBox = (await settings.boundingBox())!;
  expect(settingsBox.y).toBeGreaterThanOrEqual(
    buddyBox.y + buddyBox.height - 1,
  );
  expect(settingsBox.height).toBeGreaterThanOrEqual(28);
  expect(settingsBox.y + settingsBox.height).toBeLessThanOrEqual(
    page.viewportSize()!.height,
  );
  if (info.project.use.viewport!.width >= 1024) {
    await avatar.hover();
    await expect(page.getByRole('tooltip')).toHaveText(
      (await name.textContent())!,
    );
  }
  await assertNoOverflow(page);
  await screenshot(page, info, 'b225-b268-sidebar');

  // Home opens Home and is then marked current (the phone drawer closes).
  await home.click();
  await expect(page).toHaveURL(/\/app-v2\/?$/);
  await expect(
    page.getByRole('heading', { name: 'Home', exact: true }),
  ).toBeVisible();
  if (!desktop) {
    await expect(nav).toHaveCount(0);
    await page
      .getByRole('button', { name: 'Toggle navigation', exact: true })
      .click();
  }
  await expect(home).toHaveAttribute('aria-current', 'page');
  await screenshot(page, info, 'sidebar-home-current');
});

test('at 200% zoom the sidebar header keeps Home beside Search and collapse, and Agents scroll clear of the footer (B225, B268)', async ({
  page,
}, info) => {
  test.skip(
    info.project.use.viewport!.width < 1024,
    'A zoomed desktop sidebar pane; the phone drawer is checked above.',
  );
  await openFixture(page);
  await page.evaluate(() => {
    document.documentElement.style.zoom = '2';
  });
  const nav = page.getByRole('navigation', {
    name: 'Workspace navigation',
    exact: true,
  });
  const header = await Promise.all(
    [
      nav.getByRole('link', { name: 'Home', exact: true }),
      nav.getByRole('button', { name: 'Workspace commands', exact: true }),
      nav.getByRole('button', { name: 'Toggle navigation', exact: true }),
    ].map((control) =>
      control.evaluate((element) => {
        const rect = element.getBoundingClientRect();
        const pane = element.closest('nav')!.getBoundingClientRect();
        return {
          top: rect.top,
          inside: rect.left >= pane.left && rect.right <= pane.right + 1,
        };
      }),
    ),
  );
  for (const control of header) {
    expect(control.inside).toBe(true);
    expect(Math.abs(control.top - header[0].top)).toBeLessThanOrEqual(1);
  }
  await screenshot(page, info, 'sidebar-200-header');
  const allAgents = nav.getByRole('button', { name: /^All agents/ });
  await allAgents.scrollIntoViewIfNeeded();
  // Nothing (the footer included) covers it once it is scrolled to.
  expect(
    await allAgents.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      return element.contains(
        document.elementFromPoint(
          rect.x + rect.width / 2,
          rect.y + rect.height / 2,
        ),
      );
    }),
  ).toBe(true);
  await screenshot(page, info, 'sidebar-200-agents');
});

test('grouped sidebar keeps pin, menu, cursor, and child navigation reachable', async ({
  page,
}, info) => {
  await openFixture(page);
  await page.evaluate(async () => {
    const { controller, transport } = (window as FixtureWindow)
      .__ROW_BOT_FIXTURE__;
    const source = transport.conversations[0];
    const extra = Array.from({ length: 12 }, (_, index) => ({
      ...source,
      id: `sidebar-extra-${index}`,
      title: `Extra conversation ${index}`,
      pinned: false,
      generation_state: [],
      updated_at: '2026-09-25T09:30:00Z',
    }));
    transport.conversations.forEach((row, index) => {
      row.pinned = index === 0;
      row.updated_at = '2026-09-25T09:30:00Z';
    });
    transport.conversations[2].parent_conversation_id = source.id;
    transport.conversations.splice(3, 0, ...extra);
    transport.conversations[3].activity_state = 'active';
    transport.conversations[3].activity_phase = 'background';
    Object.assign(transport, {
      conversationActions: async (id: string) => ({
        schema_version: 1,
        conversation_id: id,
        revision: '1',
        checkpoint_revision: '1',
        title: transport.conversations.find((row) => row.id === id)!.title,
        pinned: transport.conversations.find((row) => row.id === id)!.pinned,
        capabilities: {
          rename: { available: true, code: null },
          pin: { available: true, code: null },
          archive: { available: false, code: 'unavailable' },
          export: { available: true, code: null },
        },
      }),
      reviewConversationAction: async (
        id: string,
        body: {
          type: string;
          expected_revision: string;
          payload: Record<string, unknown>;
        },
      ) => ({
        schema_version: 1,
        conversation_id: id,
        action: body.type,
        revision: body.expected_revision,
        checkpoint_revision: '1',
        fields: body.payload,
        action_digest: 'a'.repeat(64),
        summary: 'Review pin',
        disclosures: [],
        review_id: 'fixture-review',
      }),
      executeConversationAction: async (
        id: string,
        command: {
          command_id: string;
          type: string;
          payload: { pinned?: boolean };
        },
      ) => {
        const row = transport.conversations.find((item) => item.id === id)!;
        row.pinned = Boolean(command.payload.pinned);
        return {
          command_id: command.command_id,
          status: 'completed',
          action: command.type,
          conversation: {
            conversation_id: id,
            revision: '2',
            title: row.title,
            pinned: row.pinned,
          },
        };
      },
    });
    await controller.loadMoreConversations(true);
  });
  if (info.project.use.viewport!.width < 1024)
    await page.getByRole('button', { name: 'Toggle navigation' }).click();
  const nav = page.getByRole('navigation', { name: 'Workspace navigation' });
  await expect(
    nav.getByRole('list', { name: 'Pinned conversations' }).locator('li'),
  ).toHaveCount(1);
  await expect(
    nav.getByRole('list', { name: 'Recent conversations' }).locator('li'),
  ).toHaveCount(9);
  await expect(
    nav.getByRole('button', { name: 'Sample conversation 3' }),
  ).toHaveCount(0);
  const recentPin = nav.getByRole('button', {
    name: 'Pin Sample conversation 2',
  });
  if (info.project.use.viewport!.width < 1024)
    await expect(recentPin).toHaveCSS('opacity', '1');
  else {
    await recentPin.focus();
    await expect(recentPin).toHaveCSS('opacity', '1');
  }
  await screenshot(page, info, 'slice2-grouped-sidebar');
  await expect(
    nav.getByRole('img', { name: 'Background agents working' }),
  ).toHaveClass(/nav-activity-spin/);
  await page.evaluate(async () => {
    const { controller, transport } = (window as FixtureWindow)
      .__ROW_BOT_FIXTURE__;
    transport.conversations[3].activity_state = 'terminal';
    transport.conversations[3].activity_phase = 'completed';
    await controller.loadMoreConversations(true);
  });
  await expect(
    nav.getByRole('img', { name: 'Background agents working' }),
  ).toHaveCount(0);

  await nav
    .getByRole('button', { name: 'Actions for A place for your ideas' })
    .click();
  await expect(page.getByRole('menuitem', { name: 'Rename' })).toBeVisible();
  await expect(
    page.getByRole('menuitem', { name: 'Export as Markdown' }),
  ).toBeVisible();
  await expect(
    page.getByRole('menuitem', { name: 'Export as PDF' }),
  ).toBeVisible();
  await expect(page.getByRole('menuitem', { name: 'Delete…' })).toBeVisible();
  await page.keyboard.press('Escape');
  await nav
    .getByRole('button', { name: 'Unpin A place for your ideas' })
    .click();
  // The actions dialog is named after the conversation (B237); × closes it.
  const actions = page.getByRole('dialog', { name: 'A place for your ideas' });
  await expect(actions).toBeVisible();
  await expect(actions.getByText('Unpinned.')).toBeVisible();
  await expect(
    actions.getByRole('switch', { name: 'Pin', exact: true }),
  ).not.toBeChecked();
  await actions.getByRole('button', { name: 'Close dialog' }).click();
  if (info.project.use.viewport!.width < 1024 && !(await nav.isVisible()))
    await page.getByRole('button', { name: 'Toggle navigation' }).click();
  await expect(
    nav.getByRole('list', { name: 'Pinned conversations' }),
  ).toHaveCount(0);
  await nav.getByRole('button', { name: 'Show all' }).click();
  await expect(nav.locator('.nav-conversation-link')).toHaveCount(14);
  await nav.getByRole('button', { name: 'Show less' }).click();
  await expect(nav.locator('.nav-conversation-link')).toHaveCount(10);

  await page.evaluate(async () => {
    const { controller, transport } = (window as FixtureWindow)
      .__ROW_BOT_FIXTURE__;
    const child = transport.conversations.find(
      (row) => row.parent_conversation_id,
    )!;
    await controller.selectConversation(child.id);
    history.replaceState(
      history.state,
      '',
      `/app-v2/conversations/${child.id}`,
    );
    window.dispatchEvent(
      new PopStateEvent('popstate', { state: history.state }),
    );
  });
  if (info.project.use.viewport!.width < 1024 && !(await nav.isVisible()))
    await page.getByRole('button', { name: 'Toggle navigation' }).click();
  await expect(
    nav.getByRole('button', { name: 'Sample conversation 3' }),
  ).toHaveAttribute('aria-current', 'page');
  await expect(
    nav
      .getByRole('button', { name: 'Sample conversation 3' })
      .locator('..')
      .locator('.nav-conversation-link'),
  ).toHaveAccessibleName('A place for your ideas');
});

test('the conversation library keeps its selection bar in view over a long list (B270)', async ({
  page,
}, info) => {
  await openFixture(page);
  await page.evaluate(async () => {
    const { controller, transport } = (window as FixtureWindow)
      .__ROW_BOT_FIXTURE__;
    const source = transport.conversations[0];
    const rows = Array.from({ length: 150 }, (_, index) => ({
      ...source,
      id: index === 0 ? source.id : `library-fixture-${index + 1}`,
      title: `Library conversation ${String(index + 1).padStart(3, '0')}`,
      pinned: false,
      generation_state: [],
      resource_bindings: [],
      updated_at: '2026-09-25T09:30:00Z',
    }));
    transport.conversations.splice(0, transport.conversations.length, ...rows);
    await controller.loadMoreConversations(true);
  });
  const nav = page.getByRole('navigation', {
    name: 'Workspace navigation',
    exact: true,
  });
  if (!(await nav.isVisible()))
    await page
      .getByRole('button', { name: 'Toggle navigation', exact: true })
      .click();
  await nav
    .getByRole('link', { name: 'Conversation library', exact: true })
    .click();
  const library = page.getByRole('region', {
    name: 'Conversation library',
    exact: true,
  });
  await expect(
    library.getByText('150 conversations · newest first'),
  ).toBeVisible();
  const bar = library.getByRole('toolbar', { name: 'Selected conversations' });
  await expect(bar).toHaveCount(0);

  // Ticking starts a selection; Shift extends it.
  await library
    .getByRole('checkbox', { name: 'Select Library conversation 002' })
    .check();
  await expect(bar.getByText('1 selected')).toBeVisible();
  await library
    .getByRole('checkbox', { name: 'Select Library conversation 005' })
    .click({ modifiers: ['Shift'] });
  await expect(bar.getByText('4 selected')).toBeVisible();
  for (const name of ['Pin', 'Export', 'Delete…', 'Clear selection'])
    await expect(bar.getByRole('button', { name, exact: true })).toBeVisible();

  // Far down the list the bar still sits at the top of the view.
  await library
    .getByRole('checkbox', { name: 'Select Library conversation 090' })
    .scrollIntoViewIfNeeded();
  const view = (await page.locator('.routed-view').boundingBox())!;
  const barBox = (await bar.boundingBox())!;
  expect(barBox.y).toBeGreaterThanOrEqual(view.y - 1);
  expect(barBox.y).toBeLessThanOrEqual(view.y + 8);
  await screenshot(page, info, 'b270-library-selection-scrolled');
  await assertNoOverflow(page);

  // Esc clears the selection.
  await library
    .getByRole('checkbox', { name: 'Select Library conversation 005' })
    .focus();
  await page.keyboard.press('Escape');
  await expect(bar).toHaveCount(0);
});
