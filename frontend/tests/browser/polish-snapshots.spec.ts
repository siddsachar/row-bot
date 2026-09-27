import fs from 'node:fs';
import type { TestInfo } from '@playwright/test';
import { expect, test } from './evidence';
import { openFixture } from './fixture';

/*
 * Pixel baselines for surfaces whose content is fully synthetic: the
 * component gallery and the in-browser fixture conversation (fixture data),
 * and the Setup Center on the isolated fixture server (fresh data per run).
 * Fonts are bundled, the clock is frozen and animations are finished, so the
 * pixels repeat between runs on one platform and engine. Baselines exist for
 * Chromium on the platform that recorded them; elsewhere these tests skip
 * (font rasterisation differs by OS and engine) unless the run records new
 * ones with --update-snapshots.
 */
const FROZEN = new Date('2026-09-26T09:30:00Z');
const OPTIONS = {
  animations: 'disabled',
  caret: 'hide',
  maxDiffPixelRatio: 0.002,
} as const;

test.beforeEach(async ({ page }, info) => {
  test.skip(
    !['chromium-desktop', 'chromium-phone'].includes(info.project.name),
    'Pixel baselines are recorded for Chromium desktop and phone only.',
  );
  await page.clock.setFixedTime(FROZEN);
});

function baseline(info: TestInfo, name: string) {
  const recording = ['all', 'changed'].includes(info.config.updateSnapshots);
  test.skip(
    !recording && !fs.existsSync(info.snapshotPath(name)),
    `No ${name} baseline for ${process.platform}; record one with --update-snapshots.`,
  );
  return name;
}

for (const appearance of ['light', 'dark'] as const) {
  test(`component gallery pixels hold in ${appearance}`, async ({
    page,
  }, info) => {
    const name = baseline(info, `gallery-${appearance}.png`);
    await page.emulateMedia({
      colorScheme: appearance,
      reducedMotion: 'reduce',
    });
    await page.goto('/app-v2/primitives?fixture=normal');
    await expect(
      page.getByRole('heading', { name: 'Component gallery' }),
    ).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    await expect(page).toHaveScreenshot(name, OPTIONS);
  });
}

test('fixture conversation header and composer pixels hold', async ({
  page,
}, info) => {
  const name = baseline(info, 'conversation-dark.png');
  await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' });
  await openFixture(page);
  await expect(page.getByRole('textbox', { name: 'Message' })).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  await page.mouse.move(0, 0);
  await expect(page).toHaveScreenshot(name, OPTIONS);
});

test('Setup Center pixels hold on the fixture server', async ({
  page,
}, info) => {
  const name = baseline(info, 'setup-light.png');
  await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' });
  await page.goto('/app-v2/setup');
  await expect(
    page.getByRole('heading', { name: 'Setup Center', exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel('Loading setup progress')).toHaveCount(0);
  await page.evaluate(() => document.fonts.ready);
  await expect(page).toHaveScreenshot(name, OPTIONS);
});
