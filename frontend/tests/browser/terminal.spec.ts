import type { Page } from '@playwright/test';
import { openFixture, type FixtureWindow } from './fixture';
import {
  distribution,
  expect,
  screenshot,
  test,
  writeEvidence,
} from './evidence';
import { openPanel } from './panel-helpers';

/**
 * A desktop terminal on the fixture (B248, B249): the platform opens it and
 * the transport answers like a shell, echoing each key and printing a
 * coloured line for each command. Nothing leaves the page.
 */
async function fakeTerminal(page: Page): Promise<void> {
  await page.evaluate(() => {
    const fixture = (window as FixtureWindow).__ROW_BOT_FIXTURE__;
    const frames: { sequence: number; data: string }[] = [];
    const print = (data: string) =>
      frames.push({ sequence: frames.length + 1, data });
    print('\x1b[1mRow-Bot fixture shell\x1b[0m\r\nPS C:\\fixture> ');
    let line = '';
    fixture.platform.openTerminal = async () => ({
      status: 'ok',
      value: { terminalId: 'fixture-terminal' },
    });
    fixture.transport.terminalRead = async (_terminal, cursor) => ({
      cursor: frames.length,
      latest: frames.length,
      truncated: false,
      frames: frames.filter((frame) => frame.sequence > cursor),
      status: 'running',
    });
    fixture.transport.terminalInput = async (_terminal, data) => {
      if (data === '\x03') print('^C\r\nPS C:\\fixture> ');
      else
        for (const key of data)
          if (key === '\r') {
            print(`\r\n\x1b[32mran ${line}\x1b[0m\r\nPS C:\\fixture> `);
            line = '';
          } else {
            line += key;
            print(key);
          }
      return { ok: true };
    };
  });
}

function screenText(page: Page) {
  return page.locator('.native-terminal .xterm-rows');
}

async function box(page: Page, name: string) {
  const bounds = await page
    .getByRole('region', { name, exact: true })
    .boundingBox();
  expect(bounds, `${name} is on screen`).not.toBeNull();
  return bounds!;
}

test('the terminal docks under the conversation, takes keys and keeps its scrollback (B248, B249)', async ({
  page,
}, info) => {
  test.skip(
    info.project.use.viewport!.width < 1024,
    'The dock is a full-screen sheet on phones (see below).',
  );
  await openFixture(page);
  await fakeTerminal(page);
  const toggle = page.getByRole('button', { name: 'Terminal', exact: true });
  await toggle.click();
  const terminal = page.getByRole('region', { name: 'Terminal', exact: true });
  await expect(terminal).toBeVisible();
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  await expect(screenText(page)).toContainText('Row-Bot fixture shell');

  // Under the conversation, across its whole width.
  const chat = await box(page, 'Conversation');
  const dock = await box(page, 'Terminal');
  expect(dock.y).toBeGreaterThanOrEqual(chat.y + chat.height - 2);
  expect(Math.abs(dock.x - chat.x)).toBeLessThanOrEqual(2);
  expect(Math.abs(dock.width - chat.width)).toBeLessThanOrEqual(2);

  // Keys go straight to the shell; the echo is timed from the key press to
  // the text changing on screen (a blinking cursor changes no text).
  await page.evaluate(() => {
    const times = { keys: [] as number[], changes: [] as number[] };
    Object.assign(window, { __TERMINAL_TIMES__: times });
    document.addEventListener(
      'keydown',
      () => times.keys.push(performance.now()),
      { capture: true },
    );
    const rows = document.querySelector('.native-terminal .xterm-rows')!;
    let text = rows.textContent;
    new MutationObserver(() => {
      if (rows.textContent === text) return;
      text = rows.textContent;
      times.changes.push(performance.now());
    }).observe(rows, { childList: true, subtree: true, characterData: true });
  });
  const typed = 'dir';
  for (const [index, key] of [...typed].entries()) {
    await page.keyboard.press(key);
    await expect(screenText(page)).toContainText(
      `PS C:\\fixture> ${typed.slice(0, index + 1)}`,
    );
  }
  await page.keyboard.press('Enter');
  await expect(screenText(page)).toContainText('ran dir');
  for (let index = 0; index < 12; index++) {
    await page.keyboard.press('x');
    await expect(screenText(page)).toContainText(
      `PS C:\\fixture> ${'x'.repeat(index + 1)}`,
    );
  }
  const echo = await page.evaluate(() => {
    const times = (
      window as Window &
        typeof globalThis & {
          __TERMINAL_TIMES__: { keys: number[]; changes: number[] };
        }
    ).__TERMINAL_TIMES__;
    return times.keys.map(
      (key) => (times.changes.find((change) => change > key) ?? key) - key,
    );
  });
  const echoMs = distribution(echo);
  await writeEvidence(info, 'terminal-keystroke-echo', echoMs);
  expect(echoMs.p50).toBeLessThan(100);

  // Ctrl+C with nothing selected stops the running command.
  await page.keyboard.press('Control+c');
  await expect(screenText(page)).toContainText('^C');

  // Ctrl+` closes it; opened again, the scrollback is still there.
  await page.keyboard.press('Control+Backquote');
  await expect(terminal).toHaveCount(0);
  await expect(toggle).toBeFocused();
  await page.keyboard.press('Control+Backquote');
  await expect(terminal).toBeVisible();
  await expect(screenText(page)).toContainText('ran dir');

  // The height follows the divider and is remembered on this device.
  const before = (await box(page, 'Terminal')).height;
  await page.getByRole('separator', { name: 'Resize terminal' }).focus();
  await page.keyboard.press('ArrowUp');
  await page.keyboard.press('ArrowUp');
  const grown = async () =>
    Math.round((await box(page, 'Terminal')).height - before);
  await expect.poll(grown).toBe(32);
  await openFixture(page);
  await fakeTerminal(page);
  // It opens only when asked, at the height it had.
  await expect(terminal).toHaveCount(0);
  await toggle.click();
  await expect.poll(grown).toBe(32);
  await screenshot(page, info, 'terminal-dock');
});

test('the terminal stays docked while the side region shows a panel (B249)', async ({
  page,
}, info) => {
  test.skip(
    info.project.use.viewport!.width < 1024,
    'Side panels are sheets on phones.',
  );
  await openFixture(page);
  await fakeTerminal(page);
  await page.getByRole('button', { name: 'Terminal', exact: true }).click();
  await expect(
    page.getByRole('region', { name: 'Terminal', exact: true }),
  ).toBeVisible();
  await openPanel(page, 'Workspace notes');
  const side = await box(page, 'Side panels');
  const dock = await box(page, 'Terminal');
  // The side region keeps the full height; the terminal sits beside it.
  expect(dock.x + dock.width).toBeLessThanOrEqual(side.x + 2);
  expect(side.y).toBeLessThan(dock.y);
  expect(side.y + side.height).toBeGreaterThanOrEqual(dock.y + dock.height - 2);
  await screenshot(page, info, 'terminal-dock-with-side-panel');
});

test('on a phone the terminal is a full-screen sheet (B249)', async ({
  page,
}, info) => {
  test.skip(
    info.project.use.viewport!.width >= 768,
    'Phones only; wider windows dock it.',
  );
  await openFixture(page);
  await fakeTerminal(page);
  await page
    .getByRole('button', { name: 'Conversation menu', exact: true })
    .click();
  await page
    .getByRole('menuitem', { name: 'Open Interactive terminal', exact: true })
    .click();
  const terminal = page.getByRole('region', { name: 'Terminal', exact: true });
  await expect(terminal).toBeVisible();
  await expect(screenText(page)).toContainText('Row-Bot fixture shell');
  const viewport = page.viewportSize()!;
  const sheet = await box(page, 'Terminal');
  expect(sheet.width).toBeGreaterThanOrEqual(viewport.width - 2);
  expect(sheet.height).toBeGreaterThan(viewport.height * 0.8);
  await expect(page.getByTestId('conversation-workspace')).toBeHidden();
  await screenshot(page, info, 'terminal-phone-sheet');
  await page
    .getByRole('button', { name: 'Close terminal', exact: true })
    .click();
  await expect(terminal).toHaveCount(0);
  await expect(page.getByTestId('conversation-workspace')).toBeVisible();
});
