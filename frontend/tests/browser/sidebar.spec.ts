import { expect, screenshot, test } from './evidence';
import { openFixture, type FixtureWindow } from './fixture';

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
  await expect(
    page.getByRole('dialog', { name: 'Conversation actions' }),
  ).toBeVisible();
  await expect(page.getByText('Conversation action completed.')).toBeVisible();
  await page.getByRole('button', { name: 'Close', exact: true }).click();
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
