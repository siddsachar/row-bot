import type { Page } from '@playwright/test';
import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';

/*
 * V1b, Settings › Voice › Talk: the streaming speech models, the one picked
 * for this computer first. The fixture fakes the download (nothing is fetched)
 * and keeps the real catalog and the pick for an NVIDIA computer whose speech
 * runtime runs on the processor.
 */

async function fixture(page: Page, state: string) {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated Phase 4 runner');
  const response = await page.request.post(
    `/__v1b_fixture/speech-models/${state}`,
    { headers: { 'X-Fixture-Token': token, Origin: new URL(base).origin } },
  );
  expect(response.ok(), await response.text()).toBe(true);
}

async function openVoice(page: Page) {
  await page.goto('/app-v2/settings/voice');
  // Another spec on the shared fixture may have left Talk on Realtime.
  await page.getByLabel('Listen with', { exact: true }).selectOption('local');
  const list = page.getByRole('list', { name: 'Faster speech models' });
  await expect(list).toBeVisible();
  return list;
}

test.afterEach(async ({ page }) => {
  await fixture(page, 'fresh');
});

test('the recommended speech model downloads only when asked and can be removed', async ({
  page,
}, info) => {
  await fixture(page, 'fresh');
  const list = await openVoice(page);
  await expect(
    page.getByText(
      'Moonshine Base is recommended for this computer: fast on the processor',
      { exact: false },
    ),
  ).toBeVisible();
  const first = list.getByRole('listitem').first();
  await expect(first).toContainText('Moonshine Base');
  await expect(first).toContainText('Recommended');
  await first
    .getByRole('button', {
      name: 'Download Moonshine Base (111 MB) and listen with it',
    })
    .click();
  await expect(
    first.getByRole('progressbar', { name: 'Downloading Moonshine Base' }),
  ).toBeVisible();
  await expect(first.getByRole('status')).toContainText('42% of 111 MB');
  await screenshot(page, info, 'speech-models-downloading');

  await fixture(page, 'installed');
  await page.reload();
  const installed = (await openVoice(page)).getByRole('listitem').first();
  await expect(installed).toContainText('In use');
  const talk = page.getByLabel('Talk listens with', { exact: true });
  await expect(talk).toHaveValue('local-moonshine-base-en');
  await installed
    .getByRole('button', { name: 'Remove Moonshine Base' })
    .click();
  const dialog = page.getByRole('alertdialog');
  await expect(dialog).toContainText(
    'Talk goes back to listening with Whisper.',
  );
  await dialog.getByRole('button', { name: 'Remove' }).click();
  await expect(page.getByText('Removed. 141 MB freed.')).toBeVisible();
  await expect(installed).not.toContainText('In use');
  // Talk went back to Whisper, the only engine left, so there is nothing to choose.
  await expect(talk).toBeHidden();
});

for (const viewport of [
  { label: 'desktop', width: 1280, height: 900 },
  { label: 'phone', width: 375, height: 812 },
])
  for (const scheme of ['light', 'dark'] as const)
    test(`the speech model card · ${viewport.label} · ${scheme}`, async ({
      page,
    }, info) => {
      await page.setViewportSize({
        width: viewport.width,
        height: viewport.height,
      });
      await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
      for (const state of ['fresh', 'downloading', 'installed']) {
        await fixture(page, state);
        const list = await openVoice(page);
        await list.scrollIntoViewIfNeeded();
        await assertNoOverflow(page);
        const card = page.locator('.voice-speech-models');
        await card.screenshot({
          path: info.outputPath(
            `speech-models-${viewport.label}-${scheme}-${state}-card.png`,
          ),
          animations: 'disabled',
        });
        await screenshot(
          page,
          info,
          `speech-models-${viewport.label}-${scheme}-${state}`,
        );
      }
      await accessibility(
        page,
        info,
        `speech-models-${viewport.label}-${scheme}`,
      );
    });
