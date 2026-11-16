import type { Page, TestInfo } from '@playwright/test';
import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';

/*
 * C1 · Save as a skill. A reply whose work is worth a skill offers it by
 * name; any reply that used tools has "Save as skill" in its actions. The
 * draft (the chat model's scripted answer) opens in a review sheet, is edited
 * and approved, and the reply then shows it saved. A draft saved by hand waits
 * in Settings › Skills, "From" its chat. No model is called.
 */

type Seed = { offered: boolean; expenses: string; photos: string };

async function seed(page: Page): Promise<Seed> {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated browser runner');
  const response = await page.request.post('/__c1_fixture/seed', {
    headers: { 'X-Fixture-Token': token, Origin: new URL(base).origin },
  });
  expect(response.ok(), await response.text()).toBe(true);
  return (await response.json()) as Seed;
}

const SIZES = [
  [1280, 900, 'desktop'],
  [375, 812, 'phone'],
] as const;

/** The state on screen at desktop and phone width; never wider than the screen. */
async function shoot(
  page: Page,
  info: TestInfo,
  name: string,
  appearance: string,
) {
  for (const [width, height, size] of SIZES) {
    await page.setViewportSize({ width, height });
    await assertNoOverflow(page);
    await screenshot(page, info, `${name}-${size}-${appearance}`);
  }
  await page.setViewportSize({ width: 1280, height: 900 });
}

for (const appearance of ['light', 'dark'] as const) {
  test(`a reply worth a skill offers it by name; saved after review (${appearance})`, async ({
    page,
  }, info) => {
    const chats = await seed(page);
    expect(chats.offered).toBe(true);
    await page.emulateMedia({ colorScheme: appearance });
    await page.setViewportSize({ width: 1280, height: 900 });

    // Any reply that used tools: Save as skill in its actions, no offer line.
    await page.goto(`/app-v2/conversations/${chats.photos}`);
    const tripReply = page
      .getByRole('article', { name: 'Row-Bot message' })
      .last();
    await expect(tripReply).toContainText('46 photos renamed');
    const manual = tripReply.getByRole('button', { name: 'Save as skill' });
    await expect(manual).toBeVisible();
    await expect(
      page.getByRole('group', { name: 'Save as a skill' }),
    ).toHaveCount(0);
    await manual.hover();
    await expect(page.getByRole('tooltip')).toContainText('Save as skill');
    await screenshot(page, info, `c1-manual-tooltip-desktop-${appearance}`);
    await page.mouse.move(640, 600);
    await page.keyboard.press('Escape');
    await expect(page.getByRole('tooltip')).toHaveCount(0);
    await shoot(page, info, 'c1-manual-toolbar', appearance);
    await manual.click();
    const sheet = page.getByRole('dialog');
    await expect(
      sheet.getByRole('heading', { name: '📷 Trip photo tidy-up' }),
    ).toBeVisible();
    await expect(sheet.getByText('You asked to save this.')).toBeVisible();
    await sheet.getByRole('button', { name: 'Close skill draft' }).click();
    await expect(
      tripReply.getByRole('button', { name: 'Open skill draft' }),
    ).toBeVisible();

    // The offer, by name, under the newest reply.
    await page.goto(`/app-v2/conversations/${chats.expenses}`);
    const offer = page.getByRole('group', { name: 'Save as a skill' });
    await expect(offer).toContainText(
      'Save “Monthly expense report” as a skill?',
    );
    await shoot(page, info, 'c1-offer', appearance);

    // The draft, from the chat's own turn, in the review sheet.
    await offer.getByRole('button', { name: 'Save as skill' }).click();
    await expect(
      sheet.getByRole('heading', { name: '🧾 Monthly expense report' }),
    ).toBeVisible();
    await expect(sheet.getByText(/From November expenses/)).toBeVisible();
    await expect(
      sheet.getByRole('region', { name: 'Why Row-Bot offered this' }),
    ).toContainText('You said this comes round again.');
    await expect(
      sheet.getByRole('region', { name: 'What it will do' }),
    ).toContainText('Add up the totals with calculate');
    await accessibility(page, info, `c1-draft-${appearance}`);
    await shoot(page, info, 'c1-draft', appearance);

    // Edit: the person's words are final.
    await sheet.getByRole('button', { name: 'Edit' }).click();
    const form = sheet.getByRole('form', { name: 'Edit the skill' });
    await form.getByLabel('Name').fill('Expense report');
    const steps = form.getByLabel(/^Steps/);
    await steps.fill(
      `${await steps.inputValue()}\nEmail the PDF to me as a draft with \`create_gmail_draft\`.`,
    );
    await shoot(page, info, 'c1-draft-editing', appearance);
    await sheet.getByRole('button', { name: 'Save changes' }).click();
    await expect(
      sheet.getByRole('region', { name: 'What it will do' }),
    ).toContainText('Email the PDF to me as a draft');
    await expect(
      sheet.getByRole('heading', { name: '🧾 Expense report' }),
    ).toBeVisible();
    await shoot(page, info, 'c1-draft-edited', appearance);

    // Approve: saved, on but not pinned, with Undo.
    await sheet.getByRole('button', { name: 'Approve' }).click();
    await expect(sheet).toHaveCount(0);
    await expect(
      page.getByText('Saved “Expense report” as a skill.').first(),
    ).toBeVisible();
    await expect(page.getByRole('button', { name: 'Undo' })).toBeVisible();
    const reply = page.getByRole('article', { name: 'Row-Bot message' }).last();
    await expect(
      reply.getByRole('button', { name: 'Saved as a skill: Expense report' }),
    ).toBeVisible();
    await expect(offer).toHaveCount(0);
    await shoot(page, info, 'c1-saved', appearance);

    // The hand-saved draft waits in Settings › Skills, from its chat.
    await page.goto('/app-v2/settings/skills');
    const waiting = page.getByRole('region', {
      name: 'Waiting for your review',
    });
    await expect(waiting).toContainText('Trip photo tidy-up');
    await expect(waiting).toContainText('From Trip photos');
    await shoot(page, info, 'c1-settings-from-chat', appearance);
    await waiting
      .getByRole('button', { name: 'Review Trip photo tidy-up' })
      .click();
    await expect(
      sheet.getByRole('heading', { name: '📷 Trip photo tidy-up' }),
    ).toBeVisible();
    await sheet.getByRole('button', { name: 'Reject' }).click();
    await sheet.getByRole('button', { name: 'Not useful' }).click();
    await expect(waiting).toHaveCount(0);
    await expect(sheet).toHaveCount(0);
  });
}
