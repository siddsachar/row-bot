import type { Page } from '@playwright/test';
import {
  accessibility,
  expect,
  screenshot,
  test,
  writeEvidence,
} from './evidence';
import { composer, newConversation } from './unified-helpers';

// Phase 7: the desktop Buddy (/app-v2/buddy-overlay) at its native 380×230
// size beside the main window it follows, against the isolated fixture
// backend. Run with --project=chromium-buddy-overlay.

const buddyMessage = (page: Page) =>
  page.getByRole('textbox', { name: 'Buddy message', exact: true });

async function fits(page: Page) {
  const geometry = await page.evaluate(() => ({
    viewport: [innerWidth, innerHeight],
    scroll: [
      document.documentElement.scrollWidth,
      document.documentElement.scrollHeight,
    ],
    controls: [...document.querySelectorAll('button, textarea')]
      .filter((element) => element.getClientRects().length > 0)
      .map((element) => {
        const box = element.getBoundingClientRect();
        return {
          name: element.getAttribute('aria-label') ?? element.textContent ?? '',
          inside:
            box.left >= 0 &&
            box.top >= 0 &&
            box.right <= innerWidth &&
            box.bottom <= innerHeight,
        };
      }),
  }));
  expect(geometry.viewport).toEqual([380, 230]);
  expect(geometry.scroll[0]).toBeLessThanOrEqual(380);
  expect(geometry.scroll[1]).toBeLessThanOrEqual(230);
  expect(geometry.controls.filter((control) => !control.inside)).toEqual([]);
  return geometry;
}

async function motion(page: Page) {
  return page.evaluate(() => {
    const avatar = document.querySelector('.buddy-overlay-avatar');
    return avatar ? getComputedStyle(avatar, '::before').animationName : null;
  });
}

for (const appearance of ['light', 'dark'] as const) {
  test(`desktop Buddy follows, shares drafts, streams, stops and approves in ${appearance}`, async ({
    context,
    page,
  }, testInfo) => {
    test.setTimeout(120_000);
    await context.addInitScript((mode) => {
      // Every page in the context shares the choice; opaque sandboxed
      // frames have no storage.
      try {
        localStorage.setItem(
          'row-bot.appearance.v1',
          JSON.stringify({ version: 1, appearance: mode, accent: 'blue' }),
        );
      } catch {
        /* not a document of this origin */
      }
    }, appearance);
    // The main window beside Buddy; the overlay page keeps the project's
    // 380×230 viewport.
    await page.setViewportSize({ width: 1280, height: 800 });
    const id = await newConversation(page);
    const overlay = await context.newPage();
    await overlay.goto(
      `/app-v2/buddy-overlay?conversation=${encodeURIComponent(id)}`,
    );
    await expect(overlay.locator('html')).toHaveAttribute(
      'data-buddy-overlay-ready',
      /^\d+$/,
    );
    await expect(overlay.locator('html')).toHaveAttribute(
      'data-theme',
      appearance,
    );

    // Idle: the thread it follows, three direct actions, the composer.
    await expect(overlay.getByRole('heading', { level: 1 })).not.toBeEmpty();
    const actions = overlay.getByRole('toolbar', { name: 'Buddy actions' });
    for (const name of ['Open full thread', 'Dock Buddy', 'Hide Buddy'])
      await expect(actions.getByRole('button', { name })).toBeVisible();
    // Placement needs the attested desktop bridge; a browser tab has none.
    await expect(
      actions.getByRole('button', { name: 'Dock Buddy' }),
    ).toBeDisabled();
    await expect(buddyMessage(overlay)).toBeVisible();
    const idle = await fits(overlay);
    await screenshot(overlay, testInfo, `overlay-idle-${appearance}`);
    await accessibility(overlay, testInfo, `overlay-idle-axe-${appearance}`);

    // Drafts follow the conversation both ways, without a reload.
    await buddyMessage(overlay).fill('Draft typed in Buddy');
    await expect(composer(page)).toHaveValue('Draft typed in Buddy', {
      timeout: 15_000,
    });
    await composer(page).fill('Draft typed in the main window');
    await expect(buddyMessage(overlay)).toHaveValue(
      'Draft typed in the main window',
      { timeout: 15_000 },
    );
    await screenshot(overlay, testInfo, `overlay-draft-${appearance}`);

    // Streaming from Buddy, with reduced motion honoured, then Stop.
    await buddyMessage(overlay).fill('stop fixture from Buddy');
    await overlay.getByRole('button', { name: 'Send', exact: true }).click();
    const stop = overlay.getByRole('button', { name: 'Stop', exact: true });
    await expect(stop).toBeVisible({ timeout: 20_000 });
    // What was sent leaves both composers (B103).
    await expect(buddyMessage(overlay)).toHaveValue('');
    await expect(
      overlay.getByText('Synthetic stream is active.', { exact: false }),
    ).toBeVisible({ timeout: 20_000 });
    // The main window shows the same turn.
    await expect(
      page.getByText('Synthetic stream is active.', { exact: false }).first(),
    ).toBeVisible({ timeout: 20_000 });
    const liveMotion = await motion(overlay);
    await overlay.emulateMedia({ reducedMotion: 'reduce' });
    const reducedMotion = await motion(overlay);
    expect(reducedMotion).toBe('none');
    await fits(overlay);
    await screenshot(overlay, testInfo, `overlay-streaming-${appearance}`);
    await accessibility(
      overlay,
      testInfo,
      `overlay-streaming-axe-${appearance}`,
    );
    await overlay.emulateMedia({ reducedMotion: null });
    await stop.click();
    await expect(
      overlay.getByRole('button', { name: 'Send', exact: true }),
    ).toBeVisible({ timeout: 20_000 });

    // An approval raised by a turn sent from Buddy is settled in Buddy.
    await buddyMessage(overlay).fill('approval fixture from Buddy');
    await overlay.getByRole('button', { name: 'Send', exact: true }).click();
    const approve = overlay.getByRole('button', {
      name: 'Approve',
      exact: true,
    });
    await expect(approve).toBeVisible({ timeout: 20_000 });
    await expect(buddyMessage(overlay)).toHaveValue('');
    await expect(composer(page)).toHaveValue('', { timeout: 15_000 });
    // Enabled once the approval's details have loaded.
    await expect(approve).toBeEnabled({ timeout: 20_000 });
    await expect(
      overlay.getByRole('button', { name: 'Deny', exact: true }),
    ).toBeVisible();
    await expect(
      overlay.getByRole('button', { name: 'Details', exact: true }),
    ).toBeVisible();
    await fits(overlay);
    await screenshot(overlay, testInfo, `overlay-approval-${appearance}`);
    await accessibility(
      overlay,
      testInfo,
      `overlay-approval-axe-${appearance}`,
    );
    testInfo.annotations.push({
      type: 'expected-console-error',
      description: JSON.stringify({
        signature:
          'Failed to load resource: the server responded with a status of 409 (Conflict)',
        count: 1,
        upTo: true,
        owner: 'buddy-overlay approval fixture',
        fixture:
          'Approving in Buddy settles the approval while the main window may still read its details: that read answers 409 and the window drops it',
      }),
    });
    await approve.click();
    await expect(
      overlay.getByText('Synthetic approval resumed.', { exact: false }),
    ).toBeVisible({ timeout: 20_000 });
    await expect(approve).toHaveCount(0);

    // Open full thread: without the desktop bridge it opens a tab.
    const [opened] = await Promise.all([
      context.waitForEvent('page'),
      actions.getByRole('button', { name: 'Open full thread' }).click(),
    ]);
    await expect(opened).toHaveURL(new RegExp(`/app-v2/conversations/${id}$`));
    await opened.close();

    await writeEvidence(testInfo, `overlay-states-${appearance}`, {
      appearance,
      idle,
      liveMotion,
      reducedMotion,
    });
  });
}
