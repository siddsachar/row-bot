import type { Page } from '@playwright/test';
import { assertNoOverflow, expect, screenshot, test } from './evidence';
import { blockFixtureServiceWorkers, newConversation } from './unified-helpers';

/*
 * Live voice in the real client: the Talk menu, the one-time disclosure, and a
 * session in the voice bar. Gemini Live is a fixture relay on the host (it
 * hears the page's audio through Row-Bot's socket); OpenAI's call is a
 * synthetic peer. OpenAI's and Google's keys are saved in the fixture, xAI's
 * is not. The page never connects anywhere but Row-Bot.
 */
test.beforeEach(async ({ context, page }) => {
  await blockFixtureServiceWorkers(context);
  await page.addInitScript(() => {
    const mic = { acquired: 0, stopped: 0 };
    Object.assign(window, { __liveMic: mic });
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        getUserMedia: async () => {
          mic.acquired++;
          const context = new AudioContext();
          const tone = context.createOscillator();
          const gain = context.createGain();
          gain.gain.value = 0;
          const out = context.createMediaStreamDestination();
          tone.connect(gain).connect(out);
          tone.start();
          for (const track of out.stream.getTracks()) {
            const stop = track.stop.bind(track);
            track.stop = () => {
              mic.stopped++;
              stop();
              void context.close();
            };
          }
          return out.stream;
        },
      },
    });
    class SyntheticPeer {
      listeners = new Map<string, (event?: unknown) => void>();
      addTrack() {}
      close() {}
      createDataChannel() {
        return {
          readyState: 'open',
          addEventListener: (name: string, fn: (event?: unknown) => void) =>
            this.listeners.set(name, fn),
          send() {},
          close() {},
        };
      }
      async createOffer() {
        return { sdp: 'v=0\r\nsynthetic-browser-offer' };
      }
      async setLocalDescription() {}
      async setRemoteDescription() {
        this.listeners.get('open')?.();
      }
    }
    Object.defineProperty(window, 'RTCPeerConnection', {
      configurable: true,
      value: SyntheticPeer,
    });
  });
});

async function menu(page: Page) {
  await page.getByRole('button', { name: 'Talk', exact: true }).click();
  await expect(
    page.getByRole('menuitem', { name: 'Talk with Row-Bot' }),
  ).toBeVisible();
}

async function startLive(page: Page, label: string, first: boolean) {
  await menu(page);
  await page.getByRole('menuitem', { name: `Live voice · ${label}` }).click();
  if (first)
    await page
      .getByRole('region', { name: 'Live voice' })
      .getByRole('button', { name: `Start ${label}` })
      .click();
  const bar = page.getByRole('region', { name: 'Voice conversation' });
  await expect(bar).toContainText(`${label} · Listening`);
  return bar;
}

for (const [size, width, height] of [
  ['desktop', 1280, 800],
  ['phone', 375, 812],
] as const) {
  for (const scheme of ['light', 'dark'] as const) {
    test(`the Talk menu offers Live voice, says what goes where before first use, and runs it in the bar (${size}, ${scheme})`, async ({
      page,
    }, info) => {
      await page.setViewportSize({ width, height });
      await page.emulateMedia({ colorScheme: scheme });
      // Nothing leaves for a provider: every request stays on Row-Bot's own host.
      const remote: string[] = [];
      page.on('request', (request) => {
        const url = new URL(request.url());
        if (
          /^(https?|wss?):$/.test(url.protocol) &&
          !['127.0.0.1', 'localhost'].includes(url.hostname)
        )
          remote.push(request.url());
      });
      await newConversation(page);
      await menu(page);
      await expect(
        page.getByRole('menuitem', { name: 'Live voice · Grok Voice' }),
      ).toContainText('Needs your xAI API key');
      await screenshot(page, info, `live-voice-menu-${size}-${scheme}`);
      await page
        .getByRole('menuitem', { name: 'Live voice · Grok Voice' })
        .click();
      const panel = page.getByRole('region', { name: 'Live voice' });
      await expect(panel).toContainText('Add your xAI API key in Providers');
      await screenshot(page, info, `live-voice-no-key-${size}-${scheme}`);
      await panel.getByRole('button', { name: 'Close' }).click();
      await menu(page);
      await page
        .getByRole('menuitem', { name: 'Live voice · Gemini Live' })
        .click();
      await expect(panel).toContainText(
        'Your voice, this chat’s recent messages, the agent profile’s name, a few related memories and what you share on camera or screen go to Google while you talk.',
      );
      await assertNoOverflow(page);
      await screenshot(page, info, `live-voice-disclosure-${size}-${scheme}`);
      await panel.getByRole('button', { name: 'Start Gemini Live' }).click();
      const bar = page.getByRole('region', { name: 'Voice conversation' });
      await expect(bar).toContainText('Gemini Live · Listening');
      await screenshot(page, info, `live-voice-active-${size}-${scheme}`);
      await bar.getByRole('button', { name: 'End voice' }).click();
      await expect(bar).toHaveCount(0);
      // Settings › Voice: each front's price, what goes to it, and its key.
      await page.goto('/app-v2/settings/voice');
      await page
        .getByText('Live voice, other speech models, diagnostics')
        .click();
      const missing = page.getByText('Needs your xAI API key');
      await missing.scrollIntoViewIfNeeded();
      await expect(missing).toBeVisible();
      await screenshot(page, info, `live-voice-settings-${size}-${scheme}`);
      expect(remote).toEqual([]);
    });
  }
}

test('Live voice starts and stops again and again on one page, and lets go of the microphone', async ({
  page,
}) => {
  await newConversation(page);
  const rounds = [
    ['Gemini Live', true],
    ['OpenAI Realtime', true],
    ['Gemini Live', false],
    ['OpenAI Realtime', false],
    ['Gemini Live', false],
  ] as const;
  for (const [label, first] of rounds) {
    const bar = await startLive(page, label, first);
    await bar.getByRole('button', { name: 'End voice' }).click();
    await expect(bar).toHaveCount(0);
  }
  await expect(page.locator('.conversation-voice-error')).toHaveCount(0);
  await expect
    .poll(() =>
      page.evaluate(() => {
        const mic = (
          window as unknown as {
            __liveMic: { acquired: number; stopped: number };
          }
        ).__liveMic;
        return mic.acquired - mic.stopped;
      }),
    )
    .toBe(0);
});
