import { expect, test } from '@playwright/test';
import { screenshot } from './evidence';
import { openFixture, type FixtureWindow } from './fixture';

test('long tool output, adjacent table, and immediate video embed fit wide and narrow chat', async ({
  page,
}, info) => {
  test.skip(
    info.project.name !== 'chromium-desktop',
    'One focused Chromium layout pass',
  );
  const external: { url: string; referer?: string }[] = [];
  await page.route('https://www.youtube-nocookie.com/**', (route) => {
    external.push({
      url: route.request().url(),
      referer: route.request().headers().referer,
    });
    void route.abort();
  });
  await openFixture(page);
  await page.evaluate(() => {
    const { controller } = (window as FixtureWindow).__ROW_BOT_FIXTURE__;
    const current = controller.getSnapshot();
    const projection = current.projection!;
    const base = projection.rows[0];
    const longText = Array.from(
      { length: 120 },
      (_, index) => `Line ${index + 1}: synthetic public result`,
    ).join('\n');
    controller.messageText = async (_conversation, _message, cursor) => ({
      conversation_id: 'conversation-a',
      content_ref: 'public-result',
      checkpoint_revision: '1',
      encoding: 'base64',
      media_type: 'text/plain',
      data: btoa(cursor ? 'Second page.' : longText),
      has_more: !cursor,
      next_cursor: cursor ? null : 'page-2',
    });
    const rows = [
      {
        ...base,
        id: 'slice7-tool',
        message_id: 'slice7-tool',
        role: 'assistant' as const,
        blocks: [
          {
            id: 'slice7-tool-text',
            type: 'markdown' as const,
            text: 'I checked the file.',
          },
        ],
        traces: [
          {
            group_id: 'slice7-group',
            name: 'files',
            kind: 'generic' as const,
            group_order: 0,
            status: 'succeeded' as const,
            counts: { succeeded: 2 },
            items: [0, 1].map((index) => ({
              item_id: `slice7-call-${index}`,
              group_id: 'slice7-group',
              call_id: `call-${index}`,
              result_message_id: `result-${index}`,
              call_order: index,
              group_order: 0,
              canonical_name: 'read_file',
              group_name: 'files',
              group_kind: 'generic' as const,
              status: 'succeeded' as const,
              safe_input: '{"path":"fixture.txt"}',
              safe_summary: 'Synthetic bounded summary.',
              summary_truncated: true,
              content_ref: 'public-result',
            })),
          },
        ],
      },
      {
        ...base,
        id: 'slice7-table',
        message_id: 'slice7-table',
        role: 'assistant' as const,
        blocks: [
          {
            id: 'slice7-table-text',
            type: 'markdown' as const,
            text: 'Results follow:\nName | State\n:--- | ---:\nBuild | Ready\nReview | Done',
          },
        ],
      },
      {
        ...base,
        id: 'slice7-video',
        message_id: 'slice7-video',
        role: 'assistant' as const,
        blocks: [
          {
            id: 'slice7-video-block',
            type: 'youtube' as const,
            video_id: 'dQw4w9WgXcQ',
            title: 'Synthetic video',
            url: 'https://youtu.be/dQw4w9WgXcQ',
          },
        ],
      },
    ];
    (controller as unknown as { update(patch: unknown): void }).update({
      history: null,
      projection: { ...projection, rows },
    });
  });

  const group = page.locator('.trace-group');
  await expect(group).toBeVisible();
  await expect(page.getByRole('table')).toHaveCount(1);
  await expect(page.locator('.rich-youtube iframe')).toHaveAttribute(
    'src',
    'https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ',
  );
  await expect.poll(() => external.length).toBeGreaterThan(0);
  expect(external[0].referer).toBe(`${new URL(page.url()).origin}/`);
  await expect(page.getByRole('link', { name: 'Open video' })).toHaveCount(0);
  await expect(
    page.getByRole('button', { name: /Play YouTube video/i }),
  ).toHaveCount(0);
  await group.locator('summary').first().click();
  await group.locator('.trace-item summary').first().click();
  await expect(group.locator('.trace-output').first()).toContainText(
    'Line 120: synthetic public result',
  );
  await expect(
    group.getByRole('button', { name: 'Load next result page' }),
  ).toBeVisible();
  await screenshot(page, info, 'slice7-wide');
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 844 });
    const layout = await page.evaluate(() => ({
      documentWidth: document.documentElement.scrollWidth,
      viewportWidth: document.documentElement.clientWidth,
      resultScroll:
        document.querySelector('.trace-output')!.scrollHeight >
        document.querySelector('.trace-output')!.clientHeight,
      videoRatio: (() => {
        const rect = document
          .querySelector('.youtube-player')!
          .getBoundingClientRect();
        return rect.width / rect.height;
      })(),
    }));
    expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewportWidth + 1);
    expect(layout.resultScroll).toBe(true);
    expect(layout.videoRatio).toBeGreaterThan(1.7);
    expect(layout.videoRatio).toBeLessThan(1.85);
    if (width === 390) await screenshot(page, info, 'slice7-narrow');
    const table = page.getByRole('table');
    await table.scrollIntoViewIfNeeded();
    await expect(table.getByRole('cell', { name: 'Ready' })).toBeVisible();
    await screenshot(page, info, `slice7-table-${width}`);
    const player = page.locator('.youtube-player');
    await player.scrollIntoViewIfNeeded();
    await expect(player).toBeVisible();
    await screenshot(page, info, `slice7-video-${width}`);
  }
});
