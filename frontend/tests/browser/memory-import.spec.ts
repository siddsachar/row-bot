import type { Page, TestInfo } from '@playwright/test';
import {
  accessibility,
  assertNoOverflow,
  expect,
  screenshot,
  test,
} from './evidence';

// Knowledge › Import memories, end to end on the fixture app: a synthetic
// ChatGPT export and a pasted answer are scanned, the person's own messages
// are read for more, a preview says what each memory does, and the import is
// applied and undone. The default chat model is the Python tests' fake; no
// provider is called.

async function fixture(page: Page, path: string) {
  const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
  const base = process.env.ROW_BOT_BROWSER_BASE_URL;
  if (!token || !base) throw new Error('Use the isolated browser runner');
  const response = await page.request.post(path, {
    headers: { 'X-Fixture-Token': token, Origin: new URL(base).origin },
  });
  expect(response.ok(), await response.text()).toBe(true);
}

const phone = (info: TestInfo) =>
  (info.project.use.viewport?.width ?? 1440) < 700;

async function shoot(
  page: Page,
  info: TestInfo,
  name: string,
  appearance: 'light' | 'dark',
) {
  await assertNoOverflow(page);
  await screenshot(
    page,
    info,
    `${name}-${phone(info) ? 'phone' : 'desktop'}-${appearance}`,
  );
}

const ANSWER = `\`\`\`
category | date | fact
preference | 2026-10-01 | Recordings of the app are made in light theme
project | unknown | Is building a greenhouse from recycled windows
skill | unknown | Plays the cello in a community orchestra
person | unknown | Has a sister called Ana who teaches maths
instruction | unknown | Always answer in metric units
place | unknown | Grew up in a fishing village in Cornwall
\`\`\`
This is the complete set.`;

function chat(id: string, when: number, text: string) {
  const message = (
    node: string,
    parent: string,
    role: string,
    said: string,
  ) => ({
    id: node,
    parent,
    message: {
      id: node,
      author: { role },
      create_time: when,
      content: { content_type: 'text', parts: [said] },
    },
  });
  return {
    id,
    conversation_id: id,
    title: `Synthetic chat ${id}`,
    create_time: when,
    update_time: when + 1,
    current_node: 'a',
    mapping: {
      root: { id: 'root', message: null, parent: null },
      q: message('q', 'root', 'user', text),
      a: message('a', 'q', 'assistant', `An answer to ${text}`),
    },
  };
}

const EXPORT = JSON.stringify([
  chat('c1', 1762900000, 'how deep should I plant garlic cloves'),
  chat('c2', 1762900400, 'a packing list for a week on the canal'),
  chat('c3', 1762900800, 'easy cello pieces for an orchestra audition'),
]);

for (const appearance of ['light', 'dark'] as const) {
  test(`memories from another assistant are found, previewed, imported and undone (${appearance})`, async ({
    page,
  }, info) => {
    await fixture(page, '/__mi_fixture/seed');
    await page.emulateMedia({ colorScheme: appearance });
    await page.goto('/app-v2/?tab=knowledge');
    const open = phone(info)
      ? async () => {
          await page.goto('/app-v2/settings/knowledge');
          await page
            .getByRole('button', { name: 'Import memories', exact: true })
            .click();
        }
      : async () => {
          await page
            .getByRole('button', { name: 'Import memories', exact: true })
            .click();
        };
    await open();
    const dialog = page.getByRole('dialog', { name: 'Import memories' });
    await expect(dialog).toBeVisible();

    // (a) Where from: ChatGPT takes its export and the prompt's answer.
    await dialog.getByRole('radio', { name: /ChatGPT/ }).click();
    await expect(dialog.getByText(/Settings › Data controls/)).toBeVisible();
    await expect(
      dialog.getByRole('button', { name: 'Copy prompt' }),
    ).toBeVisible();
    await dialog.getByLabel('Choose export files').setInputFiles({
      name: 'conversations.json',
      mimeType: 'application/json',
      buffer: Buffer.from(EXPORT),
    });
    await dialog
      .getByRole('textbox', { name: /Paste its answer here/ })
      .fill(ANSWER);
    await expect(
      dialog.getByRole('list', { name: 'Files to import' }),
    ).toContainText('conversations.json');
    await dialog
      .getByRole('radiogroup', { name: 'Where from' })
      .scrollIntoViewIfNeeded();
    await shoot(page, info, 'a-where-from', appearance);

    // (c) Review: what was found, and the opt-in look through past chats.
    await dialog.getByRole('button', { name: 'Find memories' }).click();
    const found = dialog.getByRole('list', { name: 'Memories found' });
    await expect(found).toBeVisible();
    await expect(dialog.getByText(/6 memories found/)).toBeVisible();
    await expect(
      dialog.getByText('Also look for memories in 3 past conversations'),
    ).toBeVisible();
    await expect(
      dialog.getByText(/Uses qwen3:8b on this computer/),
    ).toBeVisible();
    await dialog
      .getByRole('switch', { name: 'Also look in past conversations' })
      .click();
    await expect(
      dialog.getByText('Found 3 more memories in 3 conversations.'),
    ).toBeVisible();
    await expect(dialog.getByText(/9 memories found · 9 chosen/)).toBeVisible();
    await found
      .getByRole('checkbox', { name: 'Always answer in metric units' })
      .uncheck();
    await shoot(page, info, 'c-review', appearance);
    await accessibility(page, info, `memory-import-review-${appearance}`);

    // (d) Preview: what importing each memory would do.
    await dialog.getByRole('button', { name: 'Preview 8 memories' }).click();
    const preview = dialog.getByRole('list', { name: 'What each memory does' });
    await expect(preview).toBeVisible();
    await expect(
      preview.getByText('Replaces “App recordings theme”'),
    ).toBeVisible();
    await expect(preview.getByText('Adds to “Greenhouse”')).toBeVisible();
    await expect(preview.getByText('Already saved')).toBeVisible();
    await shoot(page, info, 'd-preview', appearance);

    // (e) Import, then Undo.
    await dialog.getByRole('button', { name: 'Import 7 memories' }).click();
    await expect(
      dialog.getByRole('heading', { name: /Imported from/ }),
    ).toBeVisible();
    await expect(
      dialog.getByText(/You can undo this import until/),
    ).toBeVisible();
    await shoot(page, info, 'e-imported', appearance);
    await dialog.getByRole('button', { name: 'Undo import' }).click();
    await expect(
      dialog.getByRole('heading', { name: 'Import undone' }),
    ).toBeVisible();
    await dialog.getByRole('button', { name: 'Done' }).click();
    await expect(dialog).toBeHidden();
  });
}
