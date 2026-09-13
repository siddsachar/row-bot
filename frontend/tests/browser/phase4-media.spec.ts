import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
  writeEvidence,
} from './evidence';
import {
  blockFixtureServiceWorkers,
  composer,
  fixtureState,
  newConversation,
  releaseProducer,
  type FixtureCall,
} from './unified-helpers';
import { captureBrowserDownload } from './download-helpers';
import { createHash } from 'node:crypto';

const FIXTURE_IMAGE = {
  bytes: 538,
  mime: 'image/png',
  sha256: 'e05a5a4722a5f1cdb24969cf4726567c76b8e4d5c775d2f7a77a36a9faf06338',
};
const FIXTURE_VIDEO = {
  bytes: 1_675,
  mime: 'video/mp4',
  sha256: '17d9ca74641c89585177482d84b6e66c12d01f92e6fe0dc7b1e76015746fa7d2',
};

function digest(bytes: Buffer) {
  return createHash('sha256').update(bytes).digest('hex');
}

test.use({ serviceWorkers: 'allow', nativeNetwork: true });
test.beforeEach(async ({ context }) => blockFixtureServiceWorkers(context));

test('Phase 4 generated image and video use the shared authenticated preview with playback and download', async ({
  page,
}, info) => {
  await newConversation(page);
  let call: FixtureCall | undefined;
  let primaryFailure: { error: unknown } | undefined;
  try {
    // Install the Blob observer before the authenticated controller downloads
    // either fixture, then exercise the same anchor click exposed to the user.
    const imageDownload = await captureBrowserDownload(page, async () => {
      await composer(page).fill('rich fixture video');
      await page.getByRole('button', { name: 'Send', exact: true }).click();
      await expect(
        page.getByText('Synthetic tools and media are ready.', { exact: true }),
      ).toBeVisible();
      call = (await fixtureState(page)).calls.at(-1);
      expect(call).toBeDefined();
      await page
        .locator('summary')
        .filter({ hasText: /^Activity \(/ })
        .click();
      const image = page.getByRole('img', {
        name: 'Generated result',
        exact: true,
      });
      await image.scrollIntoViewIfNeeded({ timeout: 10_000 });
      await expect(image).toBeInViewport({ ratio: 0.99 });
      const downloads = page.getByRole('link', {
        name: 'Download generated result',
        exact: true,
      });
      await expect(downloads).toHaveCount(2);
      await downloads
        .nth(0)
        .evaluate((element: HTMLAnchorElement) => element.click());
    });
    expect(imageDownload.name).toBe('result');
    expect(imageDownload.mimeType).toBe(FIXTURE_IMAGE.mime);
    expect(imageDownload.bytes).toHaveLength(FIXTURE_IMAGE.bytes);
    expect(digest(imageDownload.bytes)).toBe(FIXTURE_IMAGE.sha256);
    await screenshot(page, info, 'generated-image-visible');

    const video = page.getByLabel('Generated video result', { exact: true });
    const fallback = page.getByRole('alert').filter({
      hasText:
        'This generated result could not be previewed. Download it or retry.',
    });
    await expect
      .poll(
        async () => {
          if (await fallback.isVisible()) return 'fallback';
          if (
            (await video.count()) === 1 &&
            (await video.evaluate(
              (element: HTMLVideoElement) =>
                element.readyState >= 1 && element.error === null,
            ))
          )
            return 'playback';
          return 'pending';
        },
        { timeout: 10_000 },
      )
      .toMatch(/^(fallback|playback)$/);

    let playback:
      | {
          mode: 'native-playback';
          width: number;
          height: number;
          duration: number;
          currentTime: number;
          paused: boolean;
          readyState: number;
          sourceIsBlob: boolean;
          errorCode: number | null;
        }
      | { mode: 'download-retry-fallback'; retryStatus: number };
    if (await fallback.isVisible()) {
      await expect(video).toHaveCount(0);
      const retry = fallback.getByRole('button', {
        name: 'Retry generated result',
        exact: true,
      });
      await expect(retry).toBeVisible();
      const videoDownloadLink = page
        .getByRole('link', {
          name: 'Download generated result',
          exact: true,
        })
        .nth(1);
      const priorUrl = await videoDownloadLink.getAttribute('href');
      expect(priorUrl).toMatch(/^blob:/);
      const retried = page.waitForResponse(
        (response) =>
          response.url().includes('/api/v1/attachments/') &&
          response.request().method() === 'GET',
      );
      await retry.click();
      const retryResponse = await retried;
      expect(retryResponse.status()).toBe(200);
      await expect(videoDownloadLink).not.toHaveAttribute('href', priorUrl!);
      await expect(fallback).toBeVisible();
      await expect(video).toHaveCount(0);
      playback = {
        mode: 'download-retry-fallback',
        retryStatus: retryResponse.status(),
      };
      await screenshot(page, info, 'generated-video-download-fallback');
    } else {
      await video.scrollIntoViewIfNeeded({ timeout: 10_000 });
      await expect(video).toBeInViewport({ ratio: 0.99 });
      await expect(video).toBeVisible();
      await expect(video).toHaveAttribute('controls', '');
      await expect(video).not.toHaveAttribute('autoplay');
      expect(
        await video.evaluate((element: HTMLVideoElement) => element.paused),
      ).toBe(true);
      await video.focus();
      await expect(video).toBeFocused();
      await page.keyboard.press('Space');
      await expect
        .poll(() =>
          video.evaluate((element: HTMLVideoElement) => element.paused),
        )
        .toBe(false);
      await expect
        .poll(() =>
          video.evaluate((element: HTMLVideoElement) => element.currentTime),
        )
        .toBeGreaterThan(0);
      await page.keyboard.press('Space');
      await expect
        .poll(() =>
          video.evaluate((element: HTMLVideoElement) => element.paused),
        )
        .toBe(true);
      const metadata = await video.evaluate((element: HTMLVideoElement) => ({
        width: element.videoWidth,
        height: element.videoHeight,
        duration: element.duration,
        currentTime: element.currentTime,
        paused: element.paused,
        readyState: element.readyState,
        sourceIsBlob: element.currentSrc.startsWith('blob:'),
        errorCode: element.error?.code ?? null,
      }));
      expect(metadata).toMatchObject({
        width: 160,
        height: 90,
        paused: true,
        sourceIsBlob: true,
        errorCode: null,
      });
      playback = { mode: 'native-playback', ...metadata };
      await expect(video).toBeInViewport({ ratio: 0.99 });
      await screenshot(page, info, 'generated-video-controls');
    }

    const videoDownload = await captureBrowserDownload(page, () =>
      page
        .getByRole('link', {
          name: 'Download generated result',
          exact: true,
        })
        .nth(1)
        .evaluate((element: HTMLAnchorElement) => element.click()),
    );
    expect(videoDownload.name).toBe('result');
    expect(videoDownload.mimeType).toBe(FIXTURE_VIDEO.mime);
    expect(videoDownload.bytes).toHaveLength(FIXTURE_VIDEO.bytes);
    expect(digest(videoDownload.bytes)).toBe(FIXTURE_VIDEO.sha256);

    await expect(composer(page)).toHaveCount(1);
    await writeEvidence(info, 'synthetic-media', {
      image: {
        bytes: imageDownload.bytes.length,
        mime: imageDownload.mimeType,
        name: imageDownload.name,
        sha256: digest(imageDownload.bytes),
      },
      video: {
        bytes: videoDownload.bytes.length,
        mime: videoDownload.mimeType,
        name: videoDownload.name,
        sha256: digest(videoDownload.bytes),
        playback,
      },
    });
    await assertNoOverflow(page);
    await accessibility(page, info, 'generated-image-video');
  } catch (error) {
    primaryFailure = { error };
  }
  try {
    if (call) await releaseProducer(page, call);
  } catch (error) {
    if (!primaryFailure) throw error;
    await writeEvidence(info, 'producer-cleanup-failure', {
      failed: true,
      primaryFailurePreserved: true,
    }).catch(() => undefined);
  }
  if (primaryFailure) throw primaryFailure.error;
});
