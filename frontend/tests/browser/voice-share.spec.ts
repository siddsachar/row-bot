import type { Page, WebSocketRoute } from '@playwright/test';
import { accessibility, expect, screenshot, test } from './evidence';
import {
  blockFixtureServiceWorkers,
  fixtureState,
  newConversation,
  releaseProducer,
} from './unified-helpers';

/*
 * Sharing the camera or screen during Row-Bot Voice. A synthetic microphone
 * (a tone the test turns up to "speak") and a synthetic camera and screen
 * (painted canvases) stand in for the devices; nothing real is captured.
 */
test.beforeEach(async ({ context, page }) => {
  await blockFixtureServiceWorkers(context);
  await page.addInitScript(() => {
    const devices = { level: 0, video: 0, videoStopped: 0, screen: 0 };
    function painted(label: string, hue: number) {
      const canvas = document.createElement('canvas');
      canvas.width = 640;
      canvas.height = 480;
      const context = canvas.getContext('2d')!;
      let frame = 0;
      const paint = () => {
        context.fillStyle = `hsl(${hue} 55% 42%)`;
        context.fillRect(0, 0, 640, 480);
        context.fillStyle = 'white';
        context.font = 'bold 44px sans-serif';
        context.fillText(label, 40, 250);
        context.fillRect(40 + (frame++ % 200), 300, 80, 16);
      };
      paint();
      const timer = setInterval(paint, 100);
      const stream = canvas.captureStream(10);
      for (const track of stream.getTracks()) {
        const stop = track.stop.bind(track);
        track.stop = () => {
          devices.videoStopped++;
          clearInterval(timer);
          stop();
        };
      }
      return stream;
    }
    async function microphone() {
      const context = new AudioContext();
      await context.resume();
      const tone = context.createOscillator();
      tone.frequency.value = 300;
      const gain = context.createGain();
      gain.gain.value = 0;
      const out = context.createMediaStreamDestination();
      tone.connect(gain).connect(out);
      tone.start();
      setInterval(() => (gain.gain.value = devices.level), 10);
      return out.stream;
    }
    Object.assign(window, {
      __voiceDevices: {
        state: devices,
        speak(ms: number) {
          devices.level = 0.6;
          setTimeout(() => (devices.level = 0), ms);
        },
      },
    });
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        getUserMedia: async (constraints: MediaStreamConstraints) => {
          if (constraints.video) {
            devices.video++;
            return painted('Synthetic camera', 205);
          }
          return microphone();
        },
        getDisplayMedia: async () => {
          devices.video++;
          devices.screen++;
          return painted('Synthetic screen', 265);
        },
        enumerateDevices: async () => [
          { kind: 'videoinput', deviceId: 'front', label: 'Front' },
          { kind: 'videoinput', deviceId: 'back', label: 'Back' },
        ],
      },
    });
  });
});

async function startVoice(page: Page) {
  await page.getByRole('button', { name: 'Talk', exact: true }).click();
  const item = page.getByRole('menuitem', { name: 'Talk with Row-Bot' });
  if (await item.isVisible().catch(() => false)) await item.click();
  return page.getByRole('region', { name: 'Voice conversation' });
}

async function share(page: Page, source: 'Camera' | 'Screen or window') {
  const bar = page.getByRole('region', { name: 'Voice conversation' });
  const trigger = bar.getByRole('button', { name: /^Share camera/ });
  await trigger.click();
  const item = page.getByRole('menuitem', { name: source });
  if (await item.isVisible().catch(() => false)) await item.click();
}

function videoInUse(page: Page) {
  return page.evaluate(() => {
    const state = (
      window as unknown as {
        __voiceDevices: { state: { video: number; videoStopped: number } };
      }
    ).__voiceDevices.state;
    return state.video - state.videoStopped;
  });
}

test('a still of the shared camera goes with the spoken turn and shows in the person’s message', async ({
  page,
}, info) => {
  const id = await newConversation(page);
  // This chat's model sees images (a synthetic vision model describes them).
  const seeing = await page.request.post(`/__p4_fixture/voice/vision/${id}`, {
    headers: {
      'X-Fixture-Token': process.env.ROW_BOT_BROWSER_CONTROL_TOKEN!,
      Origin: new URL(process.env.ROW_BOT_BROWSER_BASE_URL!).origin,
    },
  });
  expect(seeing.ok()).toBe(true);
  const bar = await startVoice(page);
  await expect(bar).toHaveAttribute('data-state', 'listening');
  // Nothing is captured before the person shares.
  expect(await videoInUse(page)).toBe(0);
  await share(page, 'Camera');
  await expect(bar.getByText('Camera on')).toBeVisible();
  await expect(bar.locator('video.voice-share-preview')).toBeVisible();
  await page.evaluate(() =>
    (
      window as unknown as { __voiceDevices: { speak(ms: number): void } }
    ).__voiceDevices.speak(900),
  );
  await expect(bar).toContainText("What's on my calendar today?");
  await expect
    .poll(
      async () =>
        (await fixtureState(page)).calls.filter(
          (call) => call.conversation_id === id,
        ).length,
    )
    .toBe(1);
  await releaseProducer(
    page,
    (await fixtureState(page)).calls.find(
      (call) => call.conversation_id === id,
    )!,
  );
  const log = page.getByRole('log', { name: 'Conversation' });
  await expect(log).toContainText("What's on my calendar today?");
  await expect(log.getByText('Live camera.jpg').first()).toBeVisible();
  // The still itself shows in the message, as the person's own image would.
  await expect(log.getByText('Loading Live camera.jpg…')).toBeHidden();
  await expect(log.locator('.media-preview-error')).toHaveCount(0);
  await screenshot(page, info, 'voice-share-turn-with-still');
  // One tap stops sharing and releases the camera; voice goes on.
  await bar.getByRole('button', { name: 'Stop sharing' }).click();
  await expect(bar.getByText('Camera on')).toBeHidden();
  await expect.poll(() => videoInUse(page)).toBe(0);
  // Ending voice ends sharing too.
  await share(page, 'Camera');
  await expect(bar.getByText('Camera on')).toBeVisible();
  await bar.getByRole('button', { name: 'End voice' }).click();
  await expect(bar).toBeHidden();
  await expect.poll(() => videoInUse(page)).toBe(0);
});

/** The share control, indicator and notice, from a scripted socket (no host engine). */
for (const viewport of [
  { label: 'desktop', width: 1280, height: 800 },
  { label: 'phone', width: 375, height: 812 },
])
  for (const scheme of ['light', 'dark'] as const)
    test(`sharing in the voice bar · ${viewport.label} · ${scheme}`, async ({
      page,
    }, info) => {
      await page.setViewportSize({
        width: viewport.width,
        height: viewport.height,
      });
      await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
      let server: WebSocketRoute | null = null;
      const told: unknown[] = [];
      await page.routeWebSocket(/\/api\/v1\/voice\/stream$/, (socket) => {
        server = socket;
        socket.onMessage((message) => {
          if (typeof message === 'string') told.push(JSON.parse(message));
        });
      });
      await newConversation(page);
      const bar = await startVoice(page);
      await expect.poll(() => server !== null).toBe(true);
      const send = (event: Record<string, unknown>) =>
        server!.send(JSON.stringify(event));
      send({ type: 'ready', output_sample_rate: 24000, sees_images: true });
      send({ type: 'state', state: 'listening', muted: false });
      await expect(bar).toHaveAttribute('data-state', 'listening');
      const name = (shot: string) =>
        `voice-share-${viewport.label}-${scheme}-${shot}`;

      await share(page, 'Camera');
      await expect(bar.getByText('Camera on')).toBeVisible();
      await expect(
        bar.getByRole('button', { name: 'Switch camera' }),
      ).toBeVisible();
      expect(told).toContainEqual({ type: 'share', source: 'camera' });
      await screenshot(page, info, name('camera'));
      await accessibility(page, info, name('camera'));
      if (viewport.label === 'phone') {
        await bar.getByRole('button', { name: 'Full screen' }).click();
        await expect(bar).toHaveAttribute('data-focus', 'true');
        await expect(bar.locator('video.voice-share-preview')).toBeVisible();
        await screenshot(page, info, name('focus'));
        await bar.getByRole('button', { name: 'Leave full screen' }).click();
      }
      await bar.getByRole('button', { name: 'Stop sharing' }).click();
      expect(told).toContainEqual({ type: 'share', source: null });

      if (viewport.label === 'desktop') {
        await share(page, 'Screen or window');
        await expect(bar.getByText('Sharing your screen')).toBeVisible();
        await screenshot(page, info, name('screen'));
        await bar.getByRole('button', { name: 'Stop sharing' }).click();
      }

      // A chat whose model can't see images: the bar says so, and nothing starts.
      send({ type: 'ready', output_sample_rate: 24000, sees_images: false });
      await bar.getByRole('button', { name: /^Share camera/ }).click();
      await expect(bar).toContainText('This chat’s model can’t see images');
      await expect(page.getByRole('menuitem')).toHaveCount(0);
      expect(await videoInUse(page)).toBe(0);
      await screenshot(page, info, name('blind'));
      await bar.getByRole('button', { name: 'End voice' }).click();
      await expect(bar).toBeHidden();
    });
