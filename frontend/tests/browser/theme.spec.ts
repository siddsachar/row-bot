import {
  test,
  expect,
  accessibility,
  assertNoOverflow,
  screenshot,
  writeEvidence,
} from './evidence';
import {
  assertConversationMarker,
  openFixture,
  stableConversationMarker,
  type FixtureWindow,
} from './fixture';
import {
  TOKENS,
  bootstrapTheme,
  type ThemePreference,
} from '../../src/ui/theme-model';
import { readFileSync } from 'node:fs';
import { test as baseTest, type Page } from '@playwright/test';

test('system preference is applied before the first frame and updates without remounting', async ({
  page,
}, testInfo) => {
  test.skip(
    ![1440, 390].includes(testInfo.project.use.viewport!.width),
    'System startup matrix uses desktop and phone; blue explicit appearances cover all five sizes.',
  );
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.addInitScript(() => {
    localStorage.setItem(
      'row-bot.appearance.v1',
      JSON.stringify({ version: 1, appearance: 'system', accent: 'blue' }),
    );
    const frames: {
      time: number;
      theme: string | undefined;
      canvas: string;
    }[] = [];
    Object.assign(window, { __QA_STARTUP_FRAMES__: frames });
    function sample(time: number) {
      frames.push({
        time,
        theme: document.documentElement?.dataset.theme,
        canvas:
          document.documentElement?.style.getPropertyValue('--canvas') ?? '',
      });
      if (
        frames.length < 120 &&
        !document.querySelector('[data-testid="conversation-workspace"]')
      )
        requestAnimationFrame(sample);
    }
    requestAnimationFrame(sample);
  });
  await openFixture(page);
  const frames = await page.evaluate(
    () =>
      (
        window as unknown as {
          __QA_STARTUP_FRAMES__: {
            time: number;
            theme: string;
            canvas: string;
          }[];
        }
      ).__QA_STARTUP_FRAMES__,
  );
  expect(frames.length).toBeGreaterThan(0);
  expect(
    frames.every(
      (frame) => frame.theme === 'dark' && frame.canvas === TOKENS.dark.canvas,
    ),
  ).toBe(true);
  await writeEvidence(testInfo, 'before-paint-dark-frame-audit', frames);
  await screenshot(page, testInfo, 'system-dark-startup');
  await stableConversationMarker(page);
  for (const mode of ['light', 'dark', 'light'] as const) {
    await page.emulateMedia({ colorScheme: mode });
    await expect(page.locator('html')).toHaveAttribute('data-theme', mode);
    await assertConversationMarker(page);
    await screenshot(page, testInfo, `system-runtime-${mode}`);
  }
});

test('all alternative accents retain readable integrated controls in both appearances', async ({
  page,
}, testInfo) => {
  test.skip(
    ![1440, 390].includes(testInfo.project.use.viewport!.width),
    'Alternative accent matrix is desktop and phone in both appearances.',
  );
  await openFixture(page);
  await page.goto('/app-v2/settings/appearance?fixture=normal');
  for (const appearance of ['light', 'dark']) {
    for (const accent of ['teal', 'violet', 'amber']) {
      await page
        .getByRole('combobox', { name: 'Appearance', exact: true })
        .selectOption(appearance);
      await page
        .getByRole('combobox', { name: 'Colour theme', exact: true })
        .selectOption(accent);
      await expect(page.locator('html')).toHaveAttribute(
        'data-theme',
        appearance,
      );
      await expect(page.locator('html')).toHaveAttribute('data-accent', accent);
      await assertNoOverflow(page);
      await accessibility(page, testInfo, `${appearance}-${accent}-axe`);
      await screenshot(page, testInfo, `preferences-${appearance}-${accent}`);
    }
  }
});

test('unavailable or corrupt local storage leaves a usable shell', async ({
  page,
}, testInfo) => {
  const width = testInfo.project.use.viewport!.width;
  const size = width >= 1024 ? 'desktop' : width >= 768 ? 'tablet' : 'phone';
  await page.addInitScript((size) => {
    localStorage.setItem('row-bot.appearance.v1', '{broken');
    localStorage.setItem(
      `row-bot:layout:v1:local:${size}`,
      JSON.stringify({
        version: 999,
        navigation: -999,
        side: 999999,
        panels: [{ instance_id: 'panel-' + '9'.repeat(400) }],
      }),
    );
    Object.defineProperty(Storage.prototype, 'setItem', {
      value: () => {
        throw new DOMException('Storage denied', 'SecurityError');
      },
    });
  }, size);
  await openFixture(page);
  // Unreadable preferences fall back to the default, which follows the
  // system appearance (the fixture context emulates light).
  await expect(page.locator('html')).toHaveAttribute(
    'data-appearance',
    'system',
  );
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await page.goto('/app-v2/settings/appearance?fixture=normal');
  await page
    .getByRole('combobox', { name: 'Appearance', exact: true })
    .selectOption('dark');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.goto('/app-v2/conversations/conversation-a?fixture=normal');
  await expect(page.getByTestId('conversation-workspace')).toBeVisible();
  await assertNoOverflow(page);
  expect(
    await page.evaluate(
      () =>
        (window as FixtureWindow).__ROW_BOT_FIXTURE__.transport.counters
          .commands,
    ),
  ).toBe(0);
  await screenshot(page, testInfo, 'storage-denied-still-usable');
});

test('reduced motion, forced colours and narrow 200-percent layout remain operable', async ({
  page,
}, testInfo) => {
  await page.emulateMedia({
    reducedMotion: 'reduce',
    forcedColors: 'active',
    colorScheme: 'dark',
  });
  await page.addInitScript(() => {
    localStorage.setItem(
      'row-bot.appearance.v1',
      JSON.stringify({
        version: 1,
        appearance: 'dark',
        accent: 'blue',
        density: 'compact',
        reduce_transparency: true,
      }),
    );
  });
  await openFixture(page);
  // CSS zoom exercises the actual integrated layout at 200%; browser chrome
  // zoom and physical keyboard/safe-area behaviour remain manual device checks.
  await page.evaluate(() => {
    document.documentElement.style.zoom = '2';
  });
  const headerControls = [];
  // Phones keep search and panels in the conversation header's menu.
  for (const name of page.viewportSize()!.width < 768
    ? ['Toggle navigation', 'Conversation menu']
    : ['Workspace commands', 'Toggle navigation', 'Open panel']) {
    headerControls.push(
      await page
        .getByRole('button', { name, exact: true })
        .evaluate((element, label) => {
          const bounds = element.getBoundingClientRect();
          return {
            name: label,
            x: bounds.x,
            y: bounds.y,
            width: bounds.width,
            height: bounds.height,
            right: bounds.right,
            bottom: bounds.bottom,
            viewportWidth: innerWidth,
            viewportHeight: innerHeight,
            hit: element.contains(
              document.elementFromPoint(
                bounds.x + bounds.width / 2,
                bounds.y + bounds.height / 2,
              ),
            ),
          };
        }, name),
    );
  }
  await writeEvidence(
    testInfo,
    '200percent-header-control-geometry',
    headerControls,
  );
  await screenshot(page, testInfo, '200percent-header-before-overlay');
  for (const control of headerControls) {
    expect(control.x, `${control.name} left bound`).toBeGreaterThanOrEqual(0);
    expect(control.right, `${control.name} right bound`).toBeLessThanOrEqual(
      control.viewportWidth + 1,
    );
    expect(control.bottom, `${control.name} bottom bound`).toBeLessThanOrEqual(
      control.viewportHeight + 1,
    );
    expect(control.hit, `${control.name} visible hit target`).toBe(true);
  }
  await page.goto('/app-v2/settings/appearance?fixture=normal');
  await page.evaluate(() => {
    document.documentElement.style.zoom = '2';
  });
  const appearance = page.getByRole('combobox', {
    name: 'Appearance',
    exact: true,
  });
  await expect(appearance).toBeVisible();
  await appearance.scrollIntoViewIfNeeded();
  await appearance.selectOption('light');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await appearance.selectOption('dark');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  const settingsControls = [];
  for (const [name, control] of [['Appearance', appearance]] as const) {
    await control.scrollIntoViewIfNeeded();
    settingsControls.push(
      await control.evaluate((element, label) => {
        const bounds = element.getBoundingClientRect();
        return {
          name: label,
          x: bounds.x,
          y: bounds.y,
          width: bounds.width,
          height: bounds.height,
          right: bounds.right,
          bottom: bounds.bottom,
          viewportWidth: innerWidth,
          viewportHeight: innerHeight,
          hit: element.contains(
            document.elementFromPoint(
              bounds.x + bounds.width / 2,
              bounds.y + bounds.height / 2,
            ),
          ),
        };
      }, name),
    );
  }
  await writeEvidence(
    testInfo,
    '200percent-settled-settings-geometry',
    settingsControls,
  );
  await writeEvidence(
    testInfo,
    'emulated-preference-support',
    await page.evaluate(() => ({
      requested: {
        forcedColors: 'active',
        reducedMotion: 'reduce',
        colorScheme: 'dark',
        cssZoom: 2,
      },
      observed: {
        forcedColors: matchMedia('(forced-colors: active)').matches,
        reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
        dark: matchMedia('(prefers-color-scheme: dark)').matches,
        opaque: document.documentElement.dataset.opaque,
        zoom: getComputedStyle(document.documentElement).zoom,
        background: getComputedStyle(
          document.querySelector('.settings-page-content')!,
        ).backgroundColor,
        text: getComputedStyle(
          document.querySelector('.settings-page-content')!,
        ).color,
      },
      limitation:
        'Engine media emulation and CSS zoom only; unsupported forced-colors emulation is reported, not counted as operating-system proof.',
    })),
  );
  await expect(page.locator('html')).toHaveAttribute('data-opaque', 'true');
  await screenshot(
    page,
    testInfo,
    'forced-colours-dark-opaque-reduced-motion-200percent-settings',
  );
  for (const control of settingsControls) {
    expect(control.x, `${control.name} left bound`).toBeGreaterThanOrEqual(0);
    expect(control.y, `${control.name} top bound`).toBeGreaterThanOrEqual(0);
    expect(control.right, `${control.name} right bound`).toBeLessThanOrEqual(
      control.viewportWidth + 1,
    );
    expect(control.bottom, `${control.name} bottom bound`).toBeLessThanOrEqual(
      control.viewportHeight + 1,
    );
    expect(control.hit, `${control.name} visible hit target`).toBe(true);
  }
  await assertNoOverflow(page);
});

baseTest.describe('shared visual tokens', () => {
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
  baseTest.beforeEach(async ({ page }) => {
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

  baseTest(
    'reference chrome density preserves reading size, focus and accessible theme pairs',
    async ({ page }, info) => {
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
                  return v <= 0.04045
                    ? v / 12.92
                    : ((v + 0.055) / 1.055) ** 2.4;
                });
              return (
                channels[0] * 0.2126 +
                channels[1] * 0.7152 +
                channels[2] * 0.0722
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
              composer: [
                style('textarea').fontSize,
                style('textarea').lineHeight,
              ],
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
              inputHeight: document
                .querySelector('input')!
                .getBoundingClientRect().height,
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
          expect(measured.controlHeight).toBeGreaterThanOrEqual(
            compact ? 34 : 44,
          );
          expect(measured.inputHeight).toBeGreaterThanOrEqual(
            compact ? 34 : 44,
          );
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
    },
  );

  baseTest(
    'opaque and reduced-motion preferences preserve readable bounded controls',
    async ({ page }) => {
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
    },
  );
});
