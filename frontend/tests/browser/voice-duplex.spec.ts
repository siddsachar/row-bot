import type { Page, WebSocketRoute } from '@playwright/test';
import { accessibility, expect, screenshot, test } from './evidence';
import {
  blockFixtureServiceWorkers,
  fixtureState,
  newConversation,
  releaseProducer,
} from './unified-helpers';

/*
 * Row-Bot Voice in the real client: a synthetic microphone (a tone the test
 * turns up to "speak") feeds the real capture worklet; the host runs the real
 * engine and socket with synthetic hearing and speech (fixture_app.py).
 */
test.beforeEach(async ({ context, page }) => {
  await blockFixtureServiceWorkers(context);
  await page.addInitScript(() => {
    const mic = { level: 0, acquired: 0, stopped: 0 };
    Object.assign(window, {
      __voiceMic: {
        state: mic,
        speak(ms: number) {
          mic.level = 0.6;
          setTimeout(() => (mic.level = 0), ms);
        },
      },
    });
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        getUserMedia: async () => {
          mic.acquired++;
          const context = new AudioContext();
          await context.resume();
          const tone = context.createOscillator();
          tone.frequency.value = 300;
          const gain = context.createGain();
          gain.gain.value = 0;
          const out = context.createMediaStreamDestination();
          tone.connect(gain).connect(out);
          tone.start();
          const timer = setInterval(() => (gain.gain.value = mic.level), 10);
          for (const track of out.stream.getTracks()) {
            const stop = track.stop.bind(track);
            track.stop = () => {
              mic.stopped++;
              clearInterval(timer);
              stop();
              void context.close();
            };
          }
          return out.stream;
        },
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

test('a spoken turn reaches the chat, is answered aloud, and ending releases the microphone', async ({
  page,
}, info) => {
  const id = await newConversation(page);
  const bar = await startVoice(page);
  await expect(bar).toHaveAttribute('data-state', 'listening');
  await page.evaluate(() =>
    (
      window as unknown as { __voiceMic: { speak(ms: number): void } }
    ).__voiceMic.speak(900),
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
  await expect(bar).toHaveAttribute('data-state', 'thinking');
  await releaseProducer(
    page,
    (await fixtureState(page)).calls.find(
      (call) => call.conversation_id === id,
    )!,
  );
  const log = page.getByRole('log', { name: 'Conversation' });
  await expect(log).toContainText("What's on my calendar today?");
  await expect(log).toContainText('Synthetic stream settled.');
  await expect(bar).toContainText('Synthetic stream settled.');
  // The bar sits in the layout above the composer: the newest message stays
  // in view above it, and nothing in the chat is hidden behind it.
  const newest = log.getByText('Synthetic stream settled.').last();
  await expect(newest).toBeInViewport();
  const message = (await newest.boundingBox())!;
  const barBox = (await bar.boundingBox())!;
  const transcript = (await log.boundingBox())!;
  expect(message.y + message.height).toBeLessThanOrEqual(barBox.y);
  expect(transcript.y + transcript.height).toBeLessThanOrEqual(barBox.y + 1);
  await screenshot(page, info, 'row-bot-voice-answered');
  await accessibility(page, info, 'row-bot-voice-answered');
  await expect(bar).toHaveAttribute('data-state', 'listening', {
    timeout: 20_000,
  });
  await bar.getByRole('button', { name: 'End voice' }).click();
  await expect(bar).toBeHidden();
  await expect
    .poll(() =>
      page.evaluate(() => {
        const mic = (
          window as unknown as {
            __voiceMic: { state: { acquired: number; stopped: number } };
          }
        ).__voiceMic.state;
        return mic.acquired - mic.stopped;
      }),
    )
    .toBe(0);
});

/** The bar's look in every state, from a scripted socket (no host engine). */
const STATES = [
  { name: 'connecting', events: [] },
  {
    name: 'listening',
    events: [{ type: 'state', state: 'listening', muted: false }],
  },
  {
    name: 'thinking',
    events: [
      {
        type: 'caption',
        role: 'user',
        text: 'What’s on my calendar today?',
        final: true,
      },
      { type: 'state', state: 'thinking', muted: false },
    ],
  },
  {
    name: 'speaking',
    events: [
      {
        type: 'caption',
        role: 'assistant',
        text: 'You have two meetings, and the first is at ten.',
        segment: 1,
        sample_rate: 24000,
        final: true,
      },
      { type: 'state', state: 'speaking', muted: false },
    ],
    audio: 1,
  },
  {
    name: 'waiting',
    events: [{ type: 'state', state: 'waiting', muted: false }],
  },
  { name: 'muted', events: [{ type: 'state', state: 'muted', muted: true }] },
  {
    name: 'paused',
    events: [{ type: 'state', state: 'paused', muted: false }],
  },
] as const;

function audioFrame(segment: number) {
  const pcm = new Int16Array(24000 * 3);
  for (let i = 0; i < pcm.length; i++)
    pcm[i] = Math.round(Math.sin(i / 20) * 1500);
  const out = Buffer.alloc(5 + pcm.byteLength);
  out.writeUInt8(1, 0);
  out.writeUInt32LE(segment, 1);
  Buffer.from(pcm.buffer).copy(out, 5);
  return out;
}

for (const viewport of [
  { label: 'desktop', width: 1280, height: 800 },
  { label: 'phone', width: 375, height: 812 },
])
  for (const scheme of ['light', 'dark'] as const)
    test(`the voice bar in every state · ${viewport.label} · ${scheme}`, async ({
      page,
    }, info) => {
      await page.setViewportSize({
        width: viewport.width,
        height: viewport.height,
      });
      await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
      let server: WebSocketRoute | null = null;
      await page.routeWebSocket(/\/api\/v1\/voice\/stream$/, (socket) => {
        server = socket;
        socket.onMessage(() => undefined);
      });
      await newConversation(page);
      // The page's own theme setting follows the system scheme by default.
      const bar = await startVoice(page);
      await expect(bar).toBeVisible();
      await expect.poll(() => server !== null).toBe(true);
      for (const state of STATES) {
        for (const event of state.events) server!.send(JSON.stringify(event));
        if ('audio' in state) server!.send(audioFrame(state.audio));
        await expect(bar).toHaveAttribute('data-state', state.name);
        // Row-Bot's caption shows as its words start playing.
        if (state.name === 'speaking')
          await expect(bar).toContainText('You have two meetings');
        await screenshot(
          page,
          info,
          `voice-bar-${viewport.label}-${scheme}-${state.name}`,
        );
      }
      if (viewport.label === 'phone') {
        server!.send(
          JSON.stringify({ type: 'state', state: 'listening', muted: false }),
        );
        await bar.getByRole('button', { name: 'Full screen' }).click();
        await expect(bar).toHaveAttribute('data-focus', 'true');
        await screenshot(
          page,
          info,
          `voice-bar-${viewport.label}-${scheme}-focus`,
        );
        await accessibility(
          page,
          info,
          `voice-bar-${viewport.label}-${scheme}-focus`,
        );
      } else
        await accessibility(
          page,
          info,
          `voice-bar-${viewport.label}-${scheme}`,
        );
      // Ending stops the host's voice lease for the next test.
      await bar.getByRole('button', { name: 'End voice' }).click();
      await expect(bar).toBeHidden();
    });
