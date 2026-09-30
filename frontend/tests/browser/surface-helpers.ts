import type { Page, TestInfo } from '@playwright/test';
import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  writeEvidence,
} from './evidence';
import { openConversation } from './unified-helpers';

export function fixtureHeaders(): Record<string, string> {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated browser runner');
  return { 'X-Fixture-Token': token, Origin: new URL(base).origin };
}

export async function openHome(page: Page): Promise<void> {
  await page.goto('/app-v2/');
  await expect(
    page.getByRole('heading', { name: 'Home', exact: true }),
  ).toBeVisible();
}

export async function openSeedConversation(page: Page): Promise<void> {
  await openConversation(page);
  await expect(
    page.getByRole('heading', {
      name: 'Phase 1 conversation A',
      exact: true,
    }),
  ).toBeVisible();
}

export async function publicHandshake(page: Page): Promise<{
  authenticationKind: string;
  compatibility: string;
  applicationCapabilities: string[];
  presentationCapabilities: string[];
  nativeProofRequired: boolean;
}> {
  return page.evaluate(async () => {
    const response = await fetch('/api/v1/handshake', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        protocol_major: 1,
        minimum_minor: 0,
        maximum_minor: 0,
        client_build: 'row-bot-client-v2',
        presentation_features: ['panels', 'responsive'],
      }),
    });
    if (!response.ok) throw new Error(`Handshake failed: ${response.status}`);
    const value = (await response.json()) as {
      authentication_kind: string;
      client_compatibility: string;
      application_capabilities: string[];
      presentation_capabilities: string[];
      native_adapter: { proof_required: boolean };
    };
    // Deliberately return no CSRF, session, device or instance identifiers.
    return {
      authenticationKind: value.authentication_kind,
      compatibility: value.client_compatibility,
      applicationCapabilities: value.application_capabilities,
      presentationCapabilities: value.presentation_capabilities,
      nativeProofRequired: value.native_adapter.proof_required,
    };
  });
}

export async function assertCompactSurface(
  page: Page,
  testInfo: TestInfo,
  name: string,
): Promise<void> {
  await assertNoOverflow(page);
  const controls = await page
    .locator('button:visible, [role="button"]:visible')
    .evaluateAll((elements) =>
      elements.slice(0, 80).map((element) => {
        const box = element.getBoundingClientRect();
        return {
          name:
            element.getAttribute('aria-label') ||
            element.textContent?.trim().slice(0, 80) ||
            '<unnamed>',
          width: box.width,
          height: box.height,
        };
      }),
    );
  expect(controls.length).toBeGreaterThan(0);
  expect(
    controls.filter((control) => control.width < 43 || control.height < 43),
    'Visible compact controls retain a 44 CSS pixel target, allowing sub-pixel rounding.',
  ).toEqual([]);
  await writeEvidence(testInfo, `${name}-touch-targets`, controls);
}

export async function installVisualViewportFixture(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const listeners = new Map<string, Set<EventListener>>();
    const viewport = {
      width: window.innerWidth,
      height: window.innerHeight,
      offsetLeft: 0,
      offsetTop: 0,
      pageLeft: 0,
      pageTop: 0,
      scale: 1,
      addEventListener(type: string, listener: EventListener) {
        const registered = listeners.get(type) ?? new Set<EventListener>();
        registered.add(listener);
        listeners.set(type, registered);
      },
      removeEventListener(type: string, listener: EventListener) {
        listeners.get(type)?.delete(listener);
      },
      setHeight(height: number, offsetTop = 0) {
        this.height = height;
        this.offsetTop = offsetTop;
        for (const listener of listeners.get('resize') ?? [])
          listener.call(this, new Event('resize'));
      },
    };
    Object.defineProperty(window, 'visualViewport', {
      configurable: true,
      value: viewport,
    });
    Object.assign(window, { __P5_VISUAL_VIEWPORT__: viewport });
  });
}

export async function setVisualViewportHeight(
  page: Page,
  height: number,
): Promise<void> {
  await page.evaluate((next) => {
    (
      window as unknown as {
        __P5_VISUAL_VIEWPORT__: { setHeight(value: number): void };
      }
    ).__P5_VISUAL_VIEWPORT__.setHeight(next);
  }, height);
}

export async function captureSurface(
  page: Page,
  testInfo: TestInfo,
  name: string,
  options: { axe?: boolean } = {},
): Promise<void> {
  await assertNoOverflow(page);
  if (options.axe !== false) await accessibility(page, testInfo, `${name}-axe`);
  await screenshot(page, testInfo, name);
}
