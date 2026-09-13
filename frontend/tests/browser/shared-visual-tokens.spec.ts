import { readFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import {
  bootstrapTheme,
  TOKENS,
  type ThemePreference,
} from '../../src/ui/theme-model';

// Offline computed-style contract, not a substitute for paired application captures.
const css = readFileSync(
  new URL('../../src/ui/styles.css', import.meta.url),
  'utf8',
);
async function theme(page: Page, preference: Partial<ThemePreference> = {}) {
  await page.addScriptTag({
    content: `(${bootstrapTheme.toString()})(${JSON.stringify(TOKENS)},${JSON.stringify({ version: 1, appearance: 'dark', accent: 'blue', density: 'compact', reduce_transparency: false, ...preference })})`,
  });
}
test.beforeEach(async ({ page }) => {
  await page.route('**/*', (route) => route.abort());
  await page.setContent(`<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>${css}</style></head><body>
    <main class="surface stack" style="max-width:600px;margin:8px">
      <span class="brand-name">Row-Bot</span>
      <section class="nav-conversations"><h3>Conversations</h3><p>Saved local conversations</p></section>
      <label class="field"><span>Workspace name</span><input class="input" value="Reviewed local workspace"></label>
      <div class="actions"><button class="button primary">Review</button><button class="button">Cancel</button></div>
      <div class="transcript"><article class="message message-user"><span class="message-role">You</span><p class="message-text">Preserve readable conversation text.</p></article></div>
      <form class="composer"><textarea class="input message-composer" aria-label="Message">Draft stays readable</textarea><div class="composer-toolbar"><button type="button" class="button composer-control">Model</button><button type="button" class="button primary">Send</button></div></form>
    </main></body></html>`);
  await theme(page);
});

test('reference chrome density preserves reading size, focus and accessible theme pairs', async ({
  page,
}, info) => {
  const compact = await page.evaluate(
    () => matchMedia('(pointer: fine) and (min-width:1024px)').matches,
  );
  for (const appearance of ['dark', 'light'] as const) {
    for (const accent of ['blue', 'teal', 'violet', 'amber'] as const) {
      await theme(page, { appearance, accent });
      const measured = await page.evaluate(() => {
        const style = (selector: string) =>
          getComputedStyle(document.querySelector(selector)!);
        const luminance = (color: string) => {
          const channels = color
            .match(/[\d.]+/g)!
            .slice(0, 3)
            .map(Number)
            .map((n) => {
              const v = n / 255;
              return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
            });
          return (
            channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722
          );
        };
        const contrast = (a: string, b: string) => {
          const values = [luminance(a), luminance(b)].sort((x, y) => y - x);
          return (values[0] + 0.05) / (values[1] + 0.05);
        };
        const button = style('.primary');
        const surface = style('.surface');
        return {
          body: [style('body').fontSize, style('body').lineHeight],
          reading: [
            style('.message-text').fontSize,
            style('.message-text').lineHeight,
          ],
          composer: [style('textarea').fontSize, style('textarea').lineHeight],
          cardRadius: style('.nav-conversations').borderRadius,
          controlRadius: button.borderRadius,
          controlHeight: document
            .querySelector('.primary')!
            .getBoundingClientRect().height,
          inputHeight: document.querySelector('input')!.getBoundingClientRect()
            .height,
          primaryContrast: contrast(button.color, button.backgroundColor),
          bodyContrast: contrast(surface.color, surface.backgroundColor),
          brandContrast: contrast(
            style('.brand-name').color,
            surface.backgroundColor,
          ),
          width: document.documentElement.scrollWidth,
          viewport: innerWidth,
        };
      });
      expect(measured.body).toEqual(['14px', '21px']);
      expect(measured.reading).toEqual(['16px', '24px']);
      // Firefox serializes its 1/64px layout units as 15.2031px for 15.2px.
      expect(parseFloat(measured.composer[0])).toBeCloseTo(
        compact ? 15.2 : 16,
        2,
      );
      expect(measured.composer[1]).toBe(compact ? '21px' : '24px');
      expect(measured.cardRadius).toBe('10px');
      expect(measured.controlRadius).toBe('3px');
      expect(measured.controlHeight).toBeGreaterThanOrEqual(compact ? 34 : 44);
      expect(measured.inputHeight).toBeGreaterThanOrEqual(compact ? 34 : 44);
      expect(measured.primaryContrast).toBeGreaterThanOrEqual(4.5);
      expect(measured.bodyContrast).toBeGreaterThanOrEqual(4.5);
      expect(measured.brandContrast).toBeGreaterThanOrEqual(4.5);
      expect(measured.width).toBeLessThanOrEqual(measured.viewport);
      await info.attach(`${appearance}-${accent}-computed`, {
        body: JSON.stringify(measured, null, 2),
        contentType: 'application/json',
      });
    }
  }
  await page.keyboard.press('Tab');
  await expect(page.locator('input')).toBeFocused();
  expect(
    await page
      .locator('input')
      .evaluate((node) => getComputedStyle(node).outlineWidth),
  ).toBe('2px');
  await page.locator('textarea').fill('An unsent draft');
  await theme(page, { density: 'comfortable' });
  await expect(page.locator('textarea')).toHaveValue('An unsent draft');
  expect(
    await page
      .locator('.primary')
      .first()
      .evaluate((node) => node.getBoundingClientRect().height),
  ).toBeGreaterThanOrEqual(44);
});

test('opaque and reduced-motion preferences preserve readable bounded controls', async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await theme(page, { reduce_transparency: true });
  const measured = await page.locator('.nav-conversations').evaluate((node) => {
    const style = getComputedStyle(node);
    return {
      image: style.backgroundImage,
      background: style.backgroundColor,
      animation: style.animationName,
    };
  });
  expect(measured.image).toBe('none');
  expect(measured.background).toBe('rgb(38, 38, 38)');
  expect(measured.animation).toBe('none');
  await page.emulateMedia({ forcedColors: 'active' });
  if (
    await page.evaluate(() => matchMedia('(forced-colors: active)').matches)
  ) {
    expect(
      await page
        .locator('.nav-conversations')
        .evaluate((node) => getComputedStyle(node).boxShadow),
    ).toBe('none');
    expect(
      await page
        .locator('.primary')
        .first()
        .evaluate((node) => getComputedStyle(node).borderTopWidth),
    ).toBe('1px');
  }
});
