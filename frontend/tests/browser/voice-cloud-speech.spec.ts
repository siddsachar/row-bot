import type { Page } from '@playwright/test';
import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';

/*
 * V5, Settings › Voice: cloud listening and speaking with the person's own
 * keys. The fixture holds synthetic keys in its process (never a keyring, and
 * nothing is sent anywhere); choosing a service makes no call.
 */

async function fixture(page: Page, state: 'keys' | 'fresh') {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated Phase 4 runner');
  const response = await page.request.post(
    `/__v5_fixture/cloud-speech/${state}`,
    { headers: { 'X-Fixture-Token': token, Origin: new URL(base).origin } },
  );
  expect(response.ok(), await response.text()).toBe(true);
}

test.afterEach(async ({ page }) => {
  await fixture(page, 'fresh');
});

test('cloud speech says who hears what, at what price, and needs a key until one is saved', async ({
  page,
}, info) => {
  await fixture(page, 'fresh');
  await page.goto('/app-v2/settings/voice');
  const listen = page.getByLabel('Listen with', { exact: true });
  await expect(listen).toHaveValue('local');
  await listen.selectOption('deepgram');
  await expect(
    page.getByText('Your voice goes to Deepgram while you talk.', {
      exact: false,
    }),
  ).toBeVisible();
  await expect(page.getByText('Needs your Deepgram key')).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Add Deepgram API key' }),
  ).toBeVisible();
  await expect(
    page.getByText('Talk now listens with Deepgram Nova-3').first(),
  ).toBeVisible();
  await screenshot(page, info, 'cloud-listen-needs-key');

  await fixture(page, 'keys');
  await page.reload();
  await expect(page.getByLabel('Listen with', { exact: true })).toHaveValue(
    'deepgram',
  );
  await expect(page.getByText('Needs your Deepgram key')).toHaveCount(0);
  await expect(page.getByText('Deepgram API key')).toBeVisible();
  await page
    .getByLabel('Speak with', { exact: true })
    .selectOption('elevenlabs');
  await expect(page.getByText('Speak with saved').first()).toBeVisible();
  await expect(
    page.getByText(/spoken by ElevenLabs: their text goes to ElevenLabs/),
  ).toBeVisible();
  await expect(
    page.getByRole('combobox', { name: 'Voice', exact: true }),
  ).toHaveValue('EXAVITQu4vr4xnSDxMaL');
  await expect(page.getByLabel('Describe a voice')).toBeVisible();
  await assertNoOverflow(page);
  await accessibility(page, info, 'cloud-speak-voices');
  await screenshot(page, info, 'cloud-speak-voices');
});
