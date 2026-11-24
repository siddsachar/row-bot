import type { Page } from '@playwright/test';
import { accessibility, expect, screenshot, test } from './evidence';
import { blockFixtureServiceWorkers } from './unified-helpers';

/*
 * "Hey Row-Bot" in the real client: a synthetic microphone (a tone the test
 * turns up) feeds the real capture worklet; the host runs the wake mode of the
 * voice socket with a stand-in model that hears loudness as the wake word
 * (fixture_app.py), then Row-Bot Voice's real engine with synthetic hearing.
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

function microphones(page: Page) {
  return page.evaluate(() => {
    const mic = (
      window as unknown as {
        __voiceMic: { state: { acquired: number; stopped: number } };
      }
    ).__voiceMic.state;
    return { acquired: mic.acquired, open: mic.acquired - mic.stopped };
  });
}

// By role and exact name: the workspace's mark also names "Hey Row-Bot".
const wakeSwitch = (page: Page) =>
  page.getByRole('switch', { name: 'Hey Row-Bot', exact: true });

/**
 * The wake socket's `ready`, awaited before the page that opens it loads: the
 * mark already shows "Listening" while it starts, but only from `ready` on do
 * this window's frames reach the host, and no ticket request is on its way.
 */
function wakeReady(page: Page) {
  const ready = page
    .waitForEvent('websocket', (socket) =>
      socket.url().includes('/api/v1/voice/stream'),
    )
    .then((socket) =>
      socket.waitForEvent('framereceived', (frame) =>
        /"type": ?"ready"/.test(String(frame.payload)),
      ),
    );
  ready.catch(() => undefined);
  return ready;
}

async function setWakeWord(page: Page, on: boolean) {
  const ready = wakeReady(page);
  await page.goto('/app-v2/settings/voice');
  const toggle = wakeSwitch(page);
  await expect(toggle).toBeVisible();
  if ((await toggle.isChecked()) === on) return;
  // A window that loads with it on asks for a wake ticket; one still on its way
  // when the switch turns off is refused (409), so turning off waits for it.
  if (!on) await ready;
  await toggle.click();
  await expect(toggle).toBeChecked({ checked: on });
  // Saved for the specs after this one only once its notice shows.
  await expect(
    page
      .getByText(
        on ? 'Row-Bot listens for “Hey Row-Bot”' : 'Wake word turned off',
      )
      .first(),
  ).toBeVisible();
}

// The switch is this profile's saved one, which the specs after this one share:
// a test that stops early still turns it off, at the width that shows it.
test.afterEach(async ({ page }) => {
  const bar = page.getByRole('region', { name: 'Voice conversation' });
  if (await bar.isVisible().catch(() => false))
    await bar
      .getByRole('button', { name: 'End voice' })
      .click()
      .catch(() => undefined);
  await page.setViewportSize({ width: 1280, height: 800 });
  await setWakeWord(page, false);
});

const LISTENING = 'Listening for “Hey Row-Bot”. Turn off';

test('off until turned on; then it listens with a clear mark, hearing it starts Row-Bot Voice, and one click stops it', async ({
  page,
}, info) => {
  await page.goto('/app-v2/settings/voice');
  const group = page.getByRole('region', { name: 'Wake word' });
  await expect(group).toContainText(
    'Listens on this computer for “Hey Row-Bot” while a Row-Bot window is open; nothing is recorded or sent anywhere.',
  );
  await expect(wakeSwitch(page)).not.toBeChecked();
  expect((await microphones(page)).acquired).toBe(0);
  await expect(page.getByRole('button', { name: LISTENING })).toHaveCount(0);

  await setWakeWord(page, true);
  const mark = page.getByRole('button', { name: LISTENING });
  await expect(mark).toBeVisible();
  await expect.poll(async () => (await microphones(page)).open).toBe(1);
  for (const scheme of ['light', 'dark'] as const) {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
    await group.scrollIntoViewIfNeeded();
    await screenshot(page, info, `wake-word-settings-desktop-${scheme}`);
  }
  await accessibility(page, info, 'wake-word-settings');

  // Saying it (the stand-in hears a loud tone) starts Row-Bot Voice in a new chat, as Home's Talk does.
  const ready = wakeReady(page);
  await page.goto('/app-v2/');
  await expect(page.getByRole('button', { name: LISTENING })).toBeVisible();
  await ready;
  await page.evaluate(() =>
    (
      window as unknown as { __voiceMic: { speak(ms: number): void } }
    ).__voiceMic.speak(400),
  );
  const bar = page.getByRole('region', { name: 'Voice conversation' });
  await expect(bar).toBeVisible({ timeout: 15_000 });
  await expect(page).toHaveURL(/\/conversations\//);
  // While voice runs the wake word waits, with its microphone closed.
  await expect(
    page.getByRole('button', {
      name: 'Wake word waits while voice is on. Turn off',
    }),
  ).toBeVisible();
  await expect.poll(async () => (await microphones(page)).open).toBe(1);
  await screenshot(page, info, 'wake-word-started-voice');

  await bar.getByRole('button', { name: 'End voice' }).click();
  await expect(bar).toBeHidden();
  const again = page.getByRole('button', { name: LISTENING });
  await expect(again).toBeVisible();
  await again.click();
  await expect(
    page.getByRole('button', {
      name: 'Wake word off in this window. Listen again',
    }),
  ).toBeVisible();
  await expect.poll(async () => (await microphones(page)).open).toBe(0);
});

test('a phone has no wake word', async ({ page }, info) => {
  await page.setViewportSize({ width: 375, height: 812 });
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
    await page.goto('/app-v2/settings/voice');
    await expect(page.getByLabel('Live captions')).toBeVisible();
    await expect(page.getByRole('region', { name: 'Wake word' })).toHaveCount(
      0,
    );
    await expect(page.getByLabel('Hey Row-Bot')).toHaveCount(0);
    await screenshot(page, info, `wake-word-absent-phone-${scheme}`);
  }
});
