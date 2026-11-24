import type { Page, TestInfo, WebSocketRoute } from '@playwright/test';
import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';
import { blockFixtureServiceWorkers, newConversation } from './unified-helpers';

/*
 * V7 Presence and control: the microphone and speaker picker (Settings and
 * the voice bar) with synthetic devices that are plugged in and pulled out,
 * the Windows app's hold-to-talk key through a stand-in for its bridge, Talk
 * from the computer's Home, and the iPhone's listening limits. The bar's
 * socket is scripted where the host engine isn't the point.
 */

type Row = { deviceId: string; kind: MediaDeviceKind; label: string };
type Devices = {
  state: { asked: MediaStreamConstraints[]; sinks: string[] };
  unplug(id: string): void;
  plug(row: Row): void;
};

/** Synthetic devices: a laptop's own and a Bluetooth headset, each a real silent stream. */
async function syntheticDevices(page: Page) {
  await page.addInitScript(() => {
    const rows = [
      {
        deviceId: 'default',
        kind: 'audioinput',
        label: 'Default - Laptop microphone',
      },
      { deviceId: 'laptop', kind: 'audioinput', label: 'Laptop microphone' },
      {
        deviceId: 'headset',
        kind: 'audioinput',
        label: 'Headset (WH-1000XM5)',
      },
      {
        deviceId: 'default',
        kind: 'audiooutput',
        label: 'Default - Laptop speakers',
      },
      { deviceId: 'speakers', kind: 'audiooutput', label: 'Laptop speakers' },
      {
        deviceId: 'headphones',
        kind: 'audiooutput',
        label: 'Headset (WH-1000XM5)',
      },
    ];
    const state = {
      rows,
      asked: [] as unknown[],
      sinks: [] as string[],
      live: [] as { id: string; track: MediaStreamTrack }[],
    };
    const media = new EventTarget() as EventTarget & Record<string, unknown>;
    media.enumerateDevices = async () =>
      state.rows.map((row) => ({ ...row, groupId: '', toJSON: () => row }));
    media.getUserMedia = async (constraints: MediaStreamConstraints) => {
      state.asked.push(JSON.parse(JSON.stringify(constraints)));
      const audio = (constraints.audio ?? {}) as {
        deviceId?: { ideal?: string };
      };
      const inputs = state.rows.filter(
        (row) => row.kind === 'audioinput' && row.deviceId !== 'default',
      );
      if (!inputs.length) throw new DOMException('none', 'NotFoundError');
      const wanted = audio.deviceId?.ideal;
      const id = inputs.some((row) => row.deviceId === wanted)
        ? wanted!
        : 'laptop';
      const context = new AudioContext();
      const out = context.createMediaStreamDestination();
      const track = out.stream.getAudioTracks()[0];
      track.getSettings = () => ({ deviceId: id });
      const stop = track.stop.bind(track);
      track.stop = () => {
        stop();
        void context.close();
      };
      state.live.push({ id, track });
      return out.stream;
    };
    // The headless browser has no such speakers: playing "to" one is recorded.
    Object.assign(AudioContext.prototype, {
      setSinkId: async (id: string) => {
        state.sinks.push(String(id));
      },
    });
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: media,
    });
    Object.assign(window, {
      __devices: {
        state,
        unplug(id: string) {
          state.rows = state.rows.filter((row) => row.deviceId !== id);
          state.live
            .filter((item) => item.id === id)
            .forEach((item) => item.track.dispatchEvent(new Event('ended')));
          media.dispatchEvent(new Event('devicechange'));
        },
        plug(row: (typeof rows)[number]) {
          state.rows.push(row);
          media.dispatchEvent(new Event('devicechange'));
        },
      },
    });
  });
}

const lastAsked = (page: Page) =>
  page.evaluate(() =>
    JSON.stringify(
      (window as unknown as { __devices: Devices }).__devices.state.asked.at(
        -1,
      ),
    ),
  );
const lastSink = (page: Page) =>
  page.evaluate(() =>
    (window as unknown as { __devices: Devices }).__devices.state.sinks.at(-1),
  );
const unplug = (page: Page, id: string) =>
  page.evaluate(
    (device) =>
      (window as unknown as { __devices: Devices }).__devices.unplug(device),
    id,
  );
const plug = (page: Page, row: Row) =>
  page.evaluate(
    (device) =>
      (window as unknown as { __devices: Devices }).__devices.plug(device),
    row,
  );

test.beforeEach(async ({ context, page }) => {
  await blockFixtureServiceWorkers(context);
  await syntheticDevices(page);
});

test.afterEach(async ({ page }) => {
  // An open menu would cover the bar: close it first.
  await page.keyboard.press('Escape').catch(() => undefined);
  const bar = page.getByRole('region', { name: 'Voice conversation' });
  if (await bar.isVisible().catch(() => false))
    await bar
      .getByRole('button', { name: 'End voice' })
      .click()
      .catch(() => undefined);
});

/** Voice in a new chat, its socket scripted: `send` speaks for the host. */
async function scriptedVoice(page: Page) {
  let server: WebSocketRoute | null = null;
  const heard: Record<string, unknown>[] = [];
  await page.routeWebSocket(/\/api\/v1\/voice\/stream$/, (socket) => {
    server = socket;
    socket.onMessage((message) => {
      if (typeof message === 'string') heard.push(JSON.parse(message));
    });
  });
  await newConversation(page);
  await page.getByRole('button', { name: 'Talk', exact: true }).click();
  const item = page.getByRole('menuitem', { name: 'Talk with Row-Bot' });
  if (await item.isVisible().catch(() => false)) await item.click();
  const bar = page.getByRole('region', { name: 'Voice conversation' });
  await expect.poll(() => server !== null).toBe(true);
  const send = (event: Record<string, unknown>) =>
    server!.send(JSON.stringify(event));
  return { bar, send, heard };
}

const VIEWPORTS = [
  { label: 'desktop', width: 1280, height: 800 },
  { label: 'phone', width: 375, height: 812 },
] as const;

async function look(
  page: Page,
  viewport: (typeof VIEWPORTS)[number],
  scheme: 'light' | 'dark',
) {
  await page.setViewportSize(viewport);
  await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
}

for (const viewport of VIEWPORTS)
  for (const scheme of ['light', 'dark'] as const)
    test(`the voice bar picks the microphone and speaker, and says when the default stands in · ${viewport.label} · ${scheme}`, async ({
      page,
    }, info) => {
      await look(page, viewport, scheme);
      const { bar, send } = await scriptedVoice(page);
      send({ type: 'ready', output_sample_rate: 24000 });
      send({ type: 'state', state: 'listening', muted: false });
      await expect(bar).toHaveAttribute('data-state', 'listening');
      const menu = bar.getByRole('button', { name: 'Microphone and speaker' });
      await menu.click();
      const headset = page.getByRole('menuitem', {
        name: 'Headset (WH-1000XM5)',
      });
      await expect(headset).toHaveCount(2);
      await expect(
        page.getByRole('menuitem', { name: 'Laptop microphone' }),
      ).toBeVisible();
      await screenshot(
        page,
        info,
        `voice-devices-menu-${viewport.label}-${scheme}`,
      );
      // The headset's microphone (the first of its two rows).
      await headset.first().click();
      await expect.poll(() => lastAsked(page)).toContain('"ideal":"headset"');
      await menu.click();
      await page
        .getByRole('menuitem', { name: 'Headset (WH-1000XM5)' })
        .last()
        .click();
      await expect.poll(() => lastSink(page)).toBe('headphones');
      // The headset is switched off mid-sentence: the laptop's microphone takes over at once.
      await unplug(page, 'headset');
      await expect(bar).toContainText(
        'Your chosen microphone isn’t connected, so Row-Bot is listening with the default one.',
      );
      // The menu's button keeps focus (and its tooltip) after choosing: let it go.
      await page.evaluate(() =>
        (document.activeElement as HTMLElement)?.blur(),
      );
      await screenshot(
        page,
        info,
        `voice-device-fallback-${viewport.label}-${scheme}`,
      );
      if (viewport.label === 'phone') await assertNoOverflow(page);
      await plug(page, {
        deviceId: 'headset',
        kind: 'audioinput',
        label: 'Headset (WH-1000XM5)',
      });
      await expect(bar).not.toContainText('isn’t connected');
      await accessibility(
        page,
        info,
        `voice-devices-${viewport.label}-${scheme}`,
      );
      await bar.getByRole('button', { name: 'End voice' }).click();
      await expect(bar).toBeHidden();
    });

/** Settings › Voice's group, scrolled to; the phone's whole screen. */
async function shotGroup(
  page: Page,
  anchor: string,
  info: TestInfo,
  name: string,
  phone: boolean,
) {
  for (const dismiss of await page
    .getByRole('button', { name: 'Dismiss notification' })
    .all())
    await dismiss.click({ timeout: 2000 }).catch(() => undefined);
  const group = page.locator(`section[data-setting-anchor="${anchor}"]`);
  await group.evaluate((node) => node.scrollIntoView({ block: 'start' }));
  const path = info.outputPath(`${name}.png`);
  if (phone) await page.screenshot({ path, animations: 'disabled' });
  else await group.screenshot({ path, animations: 'disabled' });
  await info.attach(name, { path, contentType: 'image/png' });
  return group;
}

for (const viewport of VIEWPORTS)
  for (const scheme of ['light', 'dark'] as const)
    test(`Settings › Voice keeps the microphone and speaker for this browser · ${viewport.label} · ${scheme}`, async ({
      page,
    }, info) => {
      await look(page, viewport, scheme);
      await page.goto('/app-v2/settings/voice');
      const group = page.locator('section[data-setting-anchor="devices"]');
      const microphone = group.getByRole('combobox', { name: 'Microphone' });
      await expect(microphone).toHaveValue('');
      await expect(microphone).toContainText(
        'System default (Laptop microphone)',
      );
      await microphone.selectOption('headset');
      await group
        .getByRole('combobox', { name: 'Speaker' })
        .selectOption('headphones');
      await shotGroup(
        page,
        'devices',
        info,
        `settings-devices-${viewport.label}-${scheme}`,
        viewport.label === 'phone',
      );
      await accessibility(
        page,
        info,
        `settings-devices-${viewport.label}-${scheme}`,
      );
      if (viewport.label === 'phone') await assertNoOverflow(page);
      // Kept in this browser: a reload shows it again.
      await page.reload();
      await expect(microphone).toHaveValue('headset');
      await expect(
        group.getByRole('combobox', { name: 'Speaker' }),
      ).toHaveValue('headphones');
      // Settings says what a phone does when it locks.
      await expect(
        page.locator('section[data-setting-anchor="talk"]'),
      ).toContainText(
        'Phones stop listening when the screen locks or turns off, or when you switch apps',
      );
    });

/** A stand-in for the Windows app's bridge offering the hotkey; what the page asked is recorded. */
async function desktopHotkeyBridge(page: Page) {
  await page.addInitScript(() => {
    if (window !== window.top) return;
    const calls: (string | null)[] = [];
    let instance = '';
    let granted = false;
    Object.assign(window, {
      __hotkeyCalls: calls,
      __ROW_BOT_NATIVE_CLIENT__: {
        async dispatch(operation: string, payload: Record<string, unknown>) {
          if (operation === 'discover') {
            if (!granted) {
              const identity = await fetch('/api/v1/native/bootstrap');
              instance = ((await identity.json()) as { instance_id: string })
                .instance_id;
              const reply = await fetch('/api/v1/native/attest', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                  attestation: payload.attestation,
                  instance_id: instance,
                  window_id: 'v7-desktop-window',
                  window_epoch: 1,
                }),
              });
              granted = reply.ok;
            }
            return {
              status: 'ok',
              value: {
                kind: 'pywebview',
                platform: 'windows',
                capabilities: ['voice_hotkey'],
                instanceId: instance,
                windowId: 'v7-desktop-window',
                epoch: 1,
              },
            };
          }
          if (operation === 'voice_hotkey') {
            calls.push(payload.key as string | null);
            return { status: 'ok', value: null };
          }
          return { status: 'unavailable', reason: 'unsupported' };
        },
      },
    });
  });
}

const pushToTalk = (page: Page) =>
  page.getByRole('switch', { name: 'Push to talk' });

/**
 * Push to talk is this profile's saved setting, which the specs after this one
 * share: wait for its saved notice, or the page may close before it is saved.
 */
async function setPushToTalk(page: Page, on: boolean) {
  const toggle = pushToTalk(page);
  if ((await toggle.isChecked()) === on) return;
  await toggle.click();
  await expect(toggle).toBeChecked({ checked: on });
  await expect(
    page.getByText(`Push to talk turned ${on ? 'on' : 'off'}`).first(),
  ).toBeVisible();
}

for (const viewport of VIEWPORTS)
  for (const scheme of ['light', 'dark'] as const)
    test(`the Windows app's hold-to-talk key: chosen in Settings, armed only during push-to-talk voice · ${viewport.label} · ${scheme}`, async ({
      page,
    }, info) => {
      await look(page, viewport, scheme);
      await desktopHotkeyBridge(page);
      await page.goto('/app-v2/settings/voice');
      const setting = page.getByRole('combobox', {
        name: 'Hold a key to talk',
      });
      // Shown only with push to talk on.
      await expect(pushToTalk(page)).toBeVisible();
      await setPushToTalk(page, false);
      await expect(setting).toHaveCount(0);
      await setPushToTalk(page, true);
      await expect(setting).toHaveValue('');
      await setting.selectOption('ctrl+alt+space');
      await shotGroup(
        page,
        'talk',
        info,
        `settings-hotkey-${viewport.label}-${scheme}`,
        viewport.label === 'phone',
      );
      await setting.scrollIntoViewIfNeeded();
      if (viewport.label === 'phone')
        await screenshot(
          page,
          info,
          `settings-hotkey-${viewport.label}-${scheme}-row`,
        );
      await accessibility(
        page,
        info,
        `settings-hotkey-${viewport.label}-${scheme}`,
      );
      const calls = () =>
        page.evaluate(
          () =>
            (window as unknown as { __hotkeyCalls: unknown[] }).__hotkeyCalls,
        );
      // Nothing is armed outside voice.
      expect(await calls()).toEqual([]);
      const { bar, send, heard } = await scriptedVoice(page);
      send({ type: 'ready', output_sample_rate: 24000, push_to_talk: true });
      send({ type: 'state', state: 'listening', muted: false });
      await expect(
        bar.getByRole('button', { name: 'Hold to talk' }),
      ).toBeVisible();
      await expect.poll(calls).toEqual(['ctrl+alt+space']);
      // The host says the key went down, then up.
      const press = (held: boolean) =>
        page.evaluate(
          (value) =>
            window.dispatchEvent(
              new CustomEvent('row-bot-voice-hotkey', {
                detail: { held: value },
              }),
            ),
          held,
        );
      await press(true);
      await expect
        .poll(() => heard.filter((event) => event.type === 'hold').at(-1))
        .toEqual({ type: 'hold', held: true });
      await expect(
        bar.getByRole('button', { name: 'Hold to talk' }),
      ).toHaveAttribute('aria-pressed', 'true');
      await press(false);
      await expect
        .poll(() => heard.filter((event) => event.type === 'hold').at(-1))
        .toEqual({ type: 'hold', held: false });
      // Holding Space in the window does the same when nothing typed into has focus.
      await page.locator('body').focus();
      await page.keyboard.down('Space');
      await expect
        .poll(() => heard.filter((event) => event.type === 'hold').at(-1))
        .toEqual({ type: 'hold', held: true });
      await page.keyboard.up('Space');
      await bar.getByRole('button', { name: 'End voice' }).click();
      await expect(bar).toBeHidden();
      await expect.poll(calls).toEqual(['ctrl+alt+space', null]);
      // Back off for the specs after this one.
      await page.goto('/app-v2/settings/voice');
      await setPushToTalk(page, false);
    });

for (const scheme of ['light', 'dark'] as const)
  for (const viewport of VIEWPORTS)
    test(`Home's Ask box starts Talk in a new chat · ${viewport.label} · ${scheme}`, async ({
      page,
    }, info) => {
      await look(page, viewport, scheme);
      await page.goto('/app-v2/');
      const ask = page.getByRole('textbox', { name: 'Ask Row-Bot' });
      await expect(ask).toBeVisible();
      const talk = page.getByRole('button', {
        name: viewport.label === 'phone' ? 'Talk' : 'Talk in a new chat',
        exact: true,
      });
      await expect(talk).toBeEnabled();
      await expect(
        page.getByRole('button', { name: 'Dictate in a new chat' }),
      ).toBeVisible();
      await screenshot(page, info, `home-voice-${viewport.label}-${scheme}`);
      await accessibility(page, info, `home-voice-${viewport.label}-${scheme}`);
      await talk.click();
      await expect(page).toHaveURL(/\/conversations\/[^/?]+$/);
      const bar = page.getByRole('region', { name: 'Voice conversation' });
      await expect(bar).toHaveAttribute('data-state', 'listening');
      await bar.getByRole('button', { name: 'End voice' }).click();
      await expect(bar).toBeHidden();
    });

for (const scheme of ['light', 'dark'] as const)
  test(`an iPhone hears once that listening stops when the screen locks · phone · ${scheme}`, async ({
    page,
  }, info) => {
    await page.addInitScript(() =>
      Object.defineProperty(navigator, 'userAgent', {
        get: () =>
          'Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1',
      }),
    );
    await look(page, VIEWPORTS[1], scheme);
    const { bar, send } = await scriptedVoice(page);
    send({ type: 'ready', output_sample_rate: 24000 });
    send({ type: 'state', state: 'listening', muted: false });
    const notice = bar.getByText(
      'On iPhone, Row-Bot stops listening when the screen locks or you switch apps, the home-screen app included. The screen stays on while you talk.',
    );
    await expect(notice).toBeVisible();
    await screenshot(page, info, `voice-iphone-limits-phone-${scheme}`);
    await accessibility(page, info, `voice-iphone-limits-phone-${scheme}`);
    await assertNoOverflow(page);
    await bar.getByRole('button', { name: 'Got it' }).click();
    await expect(notice).toBeHidden();
    await bar.getByRole('button', { name: 'End voice' }).click();
    await expect(bar).toBeHidden();
    // Only once in this browser.
    const again = await scriptedVoice(page);
    again.send({ type: 'state', state: 'listening', muted: false });
    await expect(again.bar).toHaveAttribute('data-state', 'listening');
    await expect(again.bar.getByText(/stops listening/)).toHaveCount(0);
  });
