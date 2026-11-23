import type { Locator, Page, TestInfo, WebSocketRoute } from '@playwright/test';
import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';
import { blockFixtureServiceWorkers, newConversation } from './unified-helpers';

/*
 * V8 Languages: Settings › Voice › Language (the English-only notice and the
 * voice for each other language), and the voice bar's captions in the
 * language heard, right to left where the script is. Settings run on the
 * fixture's real snapshot; the bar's socket is scripted.
 */

async function fixture(page: Page, path: string) {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated Phase 4 runner');
  const response = await page.request.post(path, {
    headers: { 'X-Fixture-Token': token, Origin: new URL(base).origin },
  });
  expect(response.ok(), await response.text()).toBe(true);
}

test.beforeEach(async ({ context, page }) => {
  await blockFixtureServiceWorkers(context);
  // A silent microphone: a real stream that never speaks.
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        getUserMedia: async () => {
          const context = new AudioContext();
          return context.createMediaStreamDestination().stream;
        },
      },
    });
  });
});

test.afterEach(async ({ page }) => {
  const bar = page.getByRole('region', { name: 'Voice conversation' });
  if (await bar.isVisible().catch(() => false))
    await bar
      .getByRole('button', { name: 'End voice' })
      .click()
      .catch(() => undefined);
  await fixture(page, '/__v8_fixture/kokoro/fresh');
  await fixture(page, '/__v1b_fixture/speech-models/fresh');
});

/** The Language group as a person sees it: notices dismissed, its top in view; the phone's whole screen. */
async function shotGroup(
  page: Page,
  group: Locator,
  info: TestInfo,
  name: string,
  phone: boolean,
) {
  for (const dismiss of await page
    .getByRole('button', { name: 'Dismiss notification' })
    .all())
    await dismiss.click({ timeout: 2000 }).catch(() => undefined);
  await group.evaluate((node) => node.scrollIntoView({ block: 'start' }));
  const path = info.outputPath(`${name}.png`);
  if (phone) await page.screenshot({ path, animations: 'disabled' });
  else await group.screenshot({ path, animations: 'disabled' });
}

const VIEWPORTS = [
  { label: 'desktop', width: 1280, height: 800 },
  { label: 'phone', width: 375, height: 812 },
] as const;

for (const viewport of VIEWPORTS)
  for (const scheme of ['light', 'dark'] as const)
    test(`Language: an English-only Talk model says so, and each language has its voice · ${viewport.label} · ${scheme}`, async ({
      page,
    }, info) => {
      await page.setViewportSize(viewport);
      await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
      await fixture(page, '/__v8_fixture/kokoro/installed');
      await fixture(page, '/__v1b_fixture/speech-models/installed');
      await page.goto('/app-v2/settings/voice');
      await page
        .getByLabel('Listen with', { exact: true })
        .selectOption('local');
      const group = page.locator('section[data-setting-anchor="language"]');
      const language = group.getByRole('combobox', { name: 'Language' });
      await expect(language).toHaveValue('auto');
      await group.getByText('Voices for other languages').click();
      // Detect automatically: every other language Kokoro speaks has its voice.
      await expect(
        group.getByRole('combobox', { name: 'Spanish voice' }),
      ).toHaveValue('ef_dora');
      await expect(group).toContainText('Siwis (female)');
      await expect(group).toContainText(
        'Kokoro speaks English, Spanish, French, Hindi, Italian and Portuguese. Answers in other languages stay on screen.',
      );
      await shotGroup(
        page,
        group,
        info,
        `language-${viewport.label}-${scheme}-voices`,
        viewport.label === 'phone',
      );
      await group
        .getByRole('combobox', { name: 'Spanish voice' })
        .selectOption('em_alex');
      await expect(
        page.locator('.toast').getByText('Spanish voice saved'),
      ).toBeVisible();

      // French with Moonshine Base, which understands English only: said plainly, nothing switches.
      await language.selectOption('fr');
      const notice = group.getByText(
        'Moonshine Base understands English only; for French, use Whisper.',
      );
      await expect(notice).toBeVisible();
      await expect(
        group.getByRole('combobox', { name: 'Spanish voice' }),
      ).toBeHidden();
      await expect(
        page.getByLabel('Talk listens with', { exact: true }),
      ).toHaveValue('local-moonshine-base-en');
      await shotGroup(
        page,
        group,
        info,
        `language-${viewport.label}-${scheme}-notice`,
        viewport.label === 'phone',
      );
      await accessibility(page, info, `language-${viewport.label}-${scheme}`);
      if (viewport.label === 'phone') await assertNoOverflow(page);
      await group.getByRole('button', { name: 'Use Whisper for Talk' }).click();
      await expect(notice).toBeHidden();
      await expect(
        page.getByLabel('Talk listens with', { exact: true }),
      ).toHaveValue('local-whisper');

      // A language Kokoro can't speak: its answers stay on screen.
      await language.selectOption('de');
      await expect(group).toContainText(
        'Kokoro can’t speak German yet, so answers in German stay on screen.',
      );
      await shotGroup(
        page,
        group,
        info,
        `language-${viewport.label}-${scheme}-unspoken`,
        viewport.label === 'phone',
      );
    });

function audioFrame(segment: number) {
  const pcm = new Int16Array(24000 * 2);
  for (let i = 0; i < pcm.length; i++)
    pcm[i] = Math.round(Math.sin(i / 20) * 1500);
  const out = Buffer.alloc(5 + pcm.byteLength);
  out.writeUInt8(1, 0);
  out.writeUInt32LE(segment, 1);
  Buffer.from(pcm.buffer).copy(out, 5);
  return out;
}

async function startVoice(page: Page) {
  await page.getByRole('button', { name: 'Talk', exact: true }).click();
  const item = page.getByRole('menuitem', { name: 'Talk with Row-Bot' });
  if (await item.isVisible().catch(() => false)) await item.click();
  return page.getByRole('region', { name: 'Voice conversation' });
}

for (const viewport of VIEWPORTS)
  for (const scheme of ['light', 'dark'] as const)
    test(`the voice bar captions each language, right to left where it is written so · ${viewport.label} · ${scheme}`, async ({
      page,
    }, info) => {
      await page.setViewportSize(viewport);
      await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
      let server: WebSocketRoute | null = null;
      await page.routeWebSocket(/\/api\/v1\/voice\/stream$/, (socket) => {
        server = socket;
        socket.onMessage(() => undefined);
      });
      await newConversation(page);
      const bar = await startVoice(page);
      await expect.poll(() => server !== null).toBe(true);
      const send = (event: object) => server!.send(JSON.stringify(event));
      send({ type: 'state', state: 'listening', muted: false });
      // French, heard and answered in French; the details name the language.
      send({
        type: 'caption',
        role: 'user',
        text: 'Quel temps fait-il à Paris ?',
        final: true,
        language: 'fr',
      });
      send({
        type: 'caption',
        role: 'assistant',
        text: 'Il fait beau, avec vingt degrés cet après-midi.',
        segment: 1,
        sample_rate: 24000,
        final: true,
      });
      server!.send(audioFrame(1));
      send({ type: 'state', state: 'speaking', muted: false });
      send({
        type: 'latency',
        last: { voice_to_voice: 820 },
        summary: {
          turns: 1,
          stages: { voice_to_voice: { p50: 820, p90: 820 } },
        },
      });
      await expect(bar).toContainText('Il fait beau');
      await bar.getByRole('button', { name: 'Show details' }).click();
      await expect(bar).toContainText('Replied in 0.8 s · French');
      await screenshot(
        page,
        info,
        `voice-bar-${viewport.label}-${scheme}-french`,
      );

      // Arabic: written right to left, and Kokoro can't speak it, so the answer stays on screen.
      send({
        type: 'caption',
        role: 'user',
        text: 'ما حالة الطقس في القاهرة اليوم؟',
        final: true,
        language: 'ar',
      });
      send({
        type: 'caption',
        role: 'assistant',
        text: 'الجو مشمس وحار، ودرجة الحرارة خمس وثلاثون.',
        segment: 2,
        kind: 'answer',
        final: true,
        spoken: false,
      });
      send({ type: 'unspoken', language: 'ar' });
      send({ type: 'state', state: 'listening', muted: false });
      const you = bar.getByText('ما حالة الطقس في القاهرة اليوم؟');
      await expect(you).toHaveAttribute('lang', 'ar');
      // The caption runs right to left: its first word sits at its right end.
      expect(
        await you.evaluate((node) => getComputedStyle(node).direction),
      ).toBe('rtl');
      await expect(bar).toContainText(
        'Row-Bot’s voice can’t speak Arabic, so its answers stay on screen.',
      );
      await expect(bar).toContainText('Replied in 0.8 s · Arabic');
      await screenshot(
        page,
        info,
        `voice-bar-${viewport.label}-${scheme}-arabic`,
      );
      await accessibility(
        page,
        info,
        `voice-bar-languages-${viewport.label}-${scheme}`,
      );
      if (viewport.label === 'phone') await assertNoOverflow(page);
      await bar.getByRole('button', { name: 'End voice' }).click();
      await expect(bar).toBeHidden();
    });
