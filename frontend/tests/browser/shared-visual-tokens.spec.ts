import { readFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import {
  bootstrapTheme,
  TOKENS,
  type ThemePreference,
} from '../../src/ui/theme-model';

// Offline computed-style contract, not a substitute for paired application captures.
// Layers are read in the cascade order declared by styles/index.css.
const entry = new URL('../../src/ui/styles/index.css', import.meta.url);
const css = [...readFileSync(entry, 'utf8').matchAll(/@import '([^']+)';/g)]
  .map((match) => readFileSync(new URL(match[1], entry), 'utf8'))
  .join('\n');
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
      <section class="home-resource-card"><h3>Conversations</h3><p>Saved local conversations</p></section>
      <label class="field"><span>Workspace name</span><input class="input" value="Reviewed local workspace"></label>
      <div class="actions"><button class="button primary">Review</button><button class="button">Cancel</button></div>
      <div class="transcript"><article class="message message-user"><div class="transcript-content"><small class="message-delivery-state">Paged content</small><p class="message-text">Preserve readable conversation text.</p></div></article></div>
      <span class="connection-status connected">Connected</span>
      <div class="panel-toolbar"><button class="button">Panel action</button></div>
      <form class="composer"><div class="composer-field"><textarea class="input message-composer" aria-label="Message">Draft stays readable</textarea><div class="composer-toolbar"><button type="button" class="button composer-model-pill">Model</button><button type="button" class="button primary">Send</button></div></div></form>
      <span class="status-chip">Waiting</span>
      <div class="dialog" style="position:static;transform:none">Dialog surface</div>
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
          label: [
            style('.field > span').fontSize,
            style('.field > span').lineHeight,
          ],
          metadata: [
            style('.message-delivery-state').fontSize,
            style('.message-delivery-state').lineHeight,
          ],
          status: [
            style('.connection-status').fontSize,
            style('.connection-status').lineHeight,
          ],
          panelLabel: [
            style('.panel-toolbar .button').fontSize,
            style('.panel-toolbar .button').lineHeight,
          ],
          composer: [style('textarea').fontSize, style('textarea').lineHeight],
          cardRadius: style('.home-resource-card').borderRadius,
          panelRadius: surface.borderRadius,
          controlRadius: button.borderRadius,
          dialogRadius: style('.dialog').borderRadius,
          composerRadius: style('.composer-field').borderRadius,
          pillRadius: style('.status-chip').borderRadius,
          borders: {
            card: [
              style('.home-resource-card').borderTopWidth,
              style('.home-resource-card').borderTopStyle,
            ],
            panel: [surface.borderTopWidth, surface.borderTopStyle],
            control: [button.borderTopWidth, button.borderTopStyle],
            dialog: [
              style('.dialog').borderTopWidth,
              style('.dialog').borderTopStyle,
            ],
          },
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
      expect(measured.reading).toEqual(['15px', '24px']);
      expect(measured.label).toEqual(['13px', '20px']);
      expect(measured.metadata).toEqual(['12px', '18px']);
      expect(measured.status).toEqual(['12px', '18px']);
      expect(measured.panelLabel).toEqual(['13px', '20px']);
      expect(parseFloat(measured.composer[0])).toBeCloseTo(
        compact ? 15 : 16,
        2,
      );
      // The composer line keeps the 15/24 reading rhythm of the transcript.
      expect(measured.composer[1]).toBe('24px');
      expect(measured.cardRadius).toBe('10px');
      expect(measured.panelRadius).toBe('12px');
      expect(measured.controlRadius).toBe('6px');
      // Phones open dialogs as full-screen task surfaces without a radius;
      // tablets and desktops keep rounded cards.
      expect(measured.dialogRadius).toBe(
        measured.viewport < 768 ? '0px' : '16px',
      );
      expect(measured.composerRadius).toBe('18px');
      expect(measured.pillRadius).toBe('999px');
      expect(measured.borders).toEqual({
        card: ['1px', 'solid'],
        panel: ['1px', 'solid'],
        control: ['1px', 'solid'],
        dialog: ['1px', 'solid'],
      });
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
  const measured = await page
    .locator('.home-resource-card')
    .evaluate((node) => {
      const style = getComputedStyle(node);
      return {
        image: style.backgroundImage,
        background: style.backgroundColor,
        animation: style.animationName,
      };
    });
  expect(measured.image).toBe('none');
  // Opaque mode keeps the raised dark surface token (#171C25).
  expect(measured.background).toBe('rgb(23, 28, 37)');
  expect(measured.animation).toBe('none');
  await page.emulateMedia({ forcedColors: 'active' });
  if (
    await page.evaluate(() => matchMedia('(forced-colors: active)').matches)
  ) {
    expect(
      await page
        .locator('.home-resource-card')
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
