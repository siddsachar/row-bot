import { createHash } from 'node:crypto';
import {
  assertNoOverflow,
  expect,
  screenshot,
  test,
  writeEvidence,
} from './evidence';
import {
  composer,
  openConversation,
  seedLargeLibrary,
} from './unified-helpers';

test('full-history search jumps to an exact stable row outside the loaded ten-thousand-message window', async ({
  page,
}, testInfo) => {
  test.setTimeout(180_000);
  await openConversation(page);
  const fixture = await seedLargeLibrary(page);
  expect(fixture.conversation_count).toBeGreaterThan(1000);
  expect(fixture.message_count).toBeGreaterThan(10000);
  await openConversation(page, fixture.conversation_id);
  await composer(page).fill('Keep the large-history draft');
  const transcript = page.getByRole('log', { name: 'Conversation' });
  const early = transcript.locator(`[data-message-id="${fixture.message_id}"]`);
  await expect(early).toHaveCount(0);
  expect(
    await transcript.locator('[data-message-id]').count(),
  ).toBeLessThanOrEqual(100);
  const rowIds = () =>
    transcript
      .locator('[data-message-id]')
      .evaluateAll((rows) =>
        rows.map((row) => row.getAttribute('data-message-id')),
      );
  await page
    .getByRole('button', { name: 'Browse history', exact: true })
    .click();
  await expect(
    page.getByRole('button', { name: 'Earlier messages', exact: true }),
  ).toBeVisible();
  const latestIds = await rowIds();
  await page
    .getByRole('button', { name: 'Earlier messages', exact: true })
    .click();
  await expect.poll(rowIds).not.toEqual(latestIds);
  const earlierIds = await rowIds();
  expect(earlierIds.length).toBeGreaterThan(0);
  await page
    .getByRole('button', { name: 'Later messages', exact: true })
    .click();
  await expect.poll(rowIds).toEqual(latestIds);
  await page.getByRole('button', { name: 'Find', exact: true }).click();
  const dialog = page.getByRole('dialog', {
    name: 'Find in conversation',
    exact: true,
  });
  await dialog
    .getByRole('textbox', { name: 'Find in history', exact: true })
    .fill(fixture.query);
  await dialog.getByRole('button', { name: 'Search', exact: true }).click();
  const hit = dialog
    .getByRole('button')
    .filter({ hasText: 'History row 00007 archival needle' });
  await expect(hit).toBeVisible();
  await hit.click();
  await expect(dialog).toHaveCount(0);
  await expect(early).toBeVisible();
  await expect(early).toBeInViewport();
  await expect(early).toBeFocused();
  expect(
    await transcript.locator('[data-message-id]').count(),
  ).toBeLessThanOrEqual(100);
  await expect(composer(page)).toHaveValue('Keep the large-history draft');
  await assertNoOverflow(page);
  const selectedEarly = await early
    .locator('.message-text')
    .evaluate((element) => {
      const range = document.createRange();
      range.selectNodeContents(element);
      const selection = getSelection()!;
      selection.removeAllRanges();
      selection.addRange(range);
      const text = selection.toString();
      selection.removeAllRanges();
      return text;
    });
  expect(selectedEarly).toBe('History row 00007 archival needle');
  const anchorBefore = await early.evaluate(
    (element) => element.getBoundingClientRect().top,
  );
  await page.getByRole('button', { name: 'Preferences', exact: true }).click();
  const preferences = page.getByRole('dialog', {
    name: 'Preferences',
    exact: true,
  });
  await preferences
    .getByRole('combobox', { name: 'Appearance', exact: true })
    .selectOption('dark');
  await page.keyboard.press('Escape');
  await expect(early).toBeInViewport();
  expect(
    Math.abs(
      (await early.evaluate((element) => element.getBoundingClientRect().top)) -
        anchorBefore,
    ),
  ).toBeLessThanOrEqual(1);
  await expect(composer(page)).toHaveValue('Keep the large-history draft');
  await screenshot(page, testInfo, 'exact-history-search-jump');
  await page
    .getByRole('button', { name: 'Latest messages', exact: true })
    .click();
  await expect(early).toHaveCount(0);
  await writeEvidence(testInfo, 'large-history-jump', {
    ...fixture,
    loadedRows: await transcript.locator('[data-message-id]').count(),
    exactStableIdentity: 'resolved',
    unsentDraft: 'retained',
    latestIds,
    earlierIds,
    selectedEarly,
    olderAnchorThroughTheme: 'retained within one CSS pixel',
  });
  await openConversation(page, fixture.oversized_conversation_id);
  const oversized = page
    .getByRole('log', { name: 'Conversation' })
    .locator(`[data-message-id="${fixture.oversized_message_id}"]`);
  await expect(oversized).toBeVisible();
  const selectedPages: string[] = [];
  const renderedSelections: {
    page: number;
    sha256: string;
    terminalRendererNewlineOmitted: boolean;
    terminalWindowsCRLF: boolean;
  }[] = [];
  let load = oversized.getByRole('button', {
    name: 'Load message content',
    exact: true,
  });
  for (let index = 0; index < 10; index++) {
    const response = page.waitForResponse((value) =>
      new URL(value.url()).pathname.includes(
        `/text/${fixture.oversized_message_id}`,
      ),
    );
    await load.click();
    const observed = await response;
    expect(observed.ok()).toBe(true);
    const portion = await observed.json();
    expect(portion.media_type).toBe('text/plain');
    const expected = Buffer.from(portion.data, 'base64').toString('utf8');
    const text = oversized.locator('.message-text');
    await expect.poll(() => text.textContent()).toBe(expected);
    const selected = await text.evaluate((element) => {
      const range = document.createRange();
      range.selectNodeContents(element);
      const selection = getSelection()!;
      selection.removeAllRanges();
      selection.addRange(range);
      const result = {
        rangeText: selection.getRangeAt(0).toString(),
        renderedText: selection.toString(),
      };
      selection.removeAllRanges();
      return result;
    });
    expect(selected.rangeText).toBe(expected);
    const terminalRendererNewlineOmitted =
      !portion.has_more &&
      expected.endsWith('\n') &&
      selected.renderedText === expected.slice(0, -1);
    const terminalWindowsCRLF =
      process.platform === 'win32' &&
      testInfo.project.use.browserName === 'firefox' &&
      !portion.has_more &&
      expected.endsWith('\n') &&
      !expected.endsWith('\r\n') &&
      selected.renderedText === `${expected.slice(0, -1)}\r\n`;
    expect(
      selected.renderedText === expected ||
        terminalRendererNewlineOmitted ||
        terminalWindowsCRLF,
    ).toBe(true);
    selectedPages.push(selected.rangeText);
    renderedSelections.push({
      page: index + 1,
      sha256: createHash('sha256').update(selected.renderedText).digest('hex'),
      terminalRendererNewlineOmitted,
      terminalWindowsCRLF,
    });
    if (!portion.has_more) break;
    load = oversized.getByRole('button', {
      name: 'Load next content page',
      exact: true,
    });
  }
  expect(selectedPages.length).toBeGreaterThanOrEqual(3);
  await expect(
    oversized.getByRole('button', {
      name: 'Load next content page',
      exact: true,
    }),
  ).toHaveCount(0);
  const selectedComplete = selectedPages.join('');
  expect([...selectedComplete].length).toBe(
    fixture.oversized_public_characters,
  );
  expect(createHash('sha256').update(selectedComplete).digest('hex')).toBe(
    fixture.oversized_public_sha256,
  );
  expect(selectedComplete).toContain('FIRST synthetic public text');
  expect(selectedComplete).toContain('MIDDLE marker');
  expect(selectedComplete).toContain('END synthetic public text');
  await writeEvidence(testInfo, 'complete-paged-selection', {
    pages: selectedPages.length,
    renderedSelections,
    characters: [...selectedComplete].length,
    sha256: createHash('sha256').update(selectedComplete).digest('hex'),
    scope:
      'Actual public text route and exact selected DOM Range across every Unicode-safe page; separate rendered Selection digests record only terminal renderer-newline omission or the observed Windows Firefox terminal LF-to-CRLF expansion, with no other whitespace normalization. No OS clipboard access, native clipboard claim or one-click whole-message export claim.',
  });
});

test('history continuation reaches a hit after many bounded scan pages', async ({
  page,
}, testInfo) => {
  test.setTimeout(180_000);
  await openConversation(page);
  const fixture = await seedLargeLibrary(page);
  await openConversation(page, fixture.conversation_id);
  await page.getByRole('button', { name: 'Find', exact: true }).click();
  const dialog = page.getByRole('dialog', {
    name: 'Find in conversation',
    exact: true,
  });
  await dialog
    .getByRole('textbox', { name: 'Find in history', exact: true })
    .fill('History row 09999 archival needle');
  const pages: { scanned: number; hits: number; hasMore: boolean }[] = [];
  let button = dialog.getByRole('button', { name: 'Search', exact: true });
  for (let index = 0; index < 24; index++) {
    const response = page.waitForResponse(
      (value) => new URL(value.url()).pathname === '/api/v1/search',
    );
    await button.click();
    const observed = await response;
    expect(observed.ok()).toBe(true);
    const data = await observed.json();
    expect(data.scanned_messages).toBeLessThanOrEqual(500);
    pages.push({
      scanned: data.scanned_messages,
      hits: data.items.length,
      hasMore: data.has_more,
    });
    if (data.items.length) break;
    expect(data.has_more).toBe(true);
    button = dialog.getByRole('button', {
      name: 'Continue search',
      exact: true,
    });
  }
  expect(pages.length).toBeGreaterThan(10);
  expect(pages.at(-1)?.hits).toBe(1);
  await dialog
    .getByRole('button')
    .filter({ hasText: 'History row 09999 archival needle' })
    .click();
  await expect(
    page
      .getByRole('log', { name: 'Conversation' })
      .getByText('History row 09999 archival needle', { exact: true }),
  ).toBeVisible();
  await writeEvidence(testInfo, 'history-search-continuation', pages);
});
