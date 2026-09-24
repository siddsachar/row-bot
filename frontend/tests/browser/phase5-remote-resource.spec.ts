import { captureBrowserDownload } from './download-helpers';
import { expect, test, writeEvidence } from './evidence';
import { fixtureResources, openConversation } from './unified-helpers';
import { captureSurface } from './phase5-helpers';

test('browser resource setup uses server IDs and exports through a bounded download', async ({
  page,
}, testInfo) => {
  const mutationBodies: { path: string; body: string }[] = [];
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname;
    if (request.method() === 'POST' && path.startsWith('/api/v1/'))
      mutationBodies.push({ path, body: request.postData() ?? '' });
  });
  await openConversation(page);
  await page.getByRole('button', { name: 'Add resource', exact: true }).click();
  let setup = page.getByRole('dialog', {
    name: 'Add resource',
    exact: true,
  });
  const deckName = 'Phase 5 browser-safe deck';
  await setup
    .getByRole('textbox', { name: 'Name (optional)', exact: true })
    .fill(deckName);
  await setup.getByRole('button', { name: 'Create Deck', exact: true }).click();
  await expect(
    setup.getByText('Resource ready', { exact: true }),
  ).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(
    page.getByRole('region', { name: 'Design preview', exact: true }),
  ).toBeVisible();

  await page.getByRole('button', { name: 'Add resource', exact: true }).click();
  setup = page.getByRole('dialog', { name: 'Add resource', exact: true });
  const restart = setup.getByRole('button', {
    name: 'Start another resource',
    exact: true,
  });
  const resourceType = setup.getByRole('combobox', {
    name: 'Resource type',
    exact: true,
  });
  await expect
    .poll(
      async () =>
        (await restart.isVisible()) || (await resourceType.isVisible()),
    )
    .toBe(true);
  if (await restart.isVisible()) await restart.click();
  await resourceType.selectOption('workspace');
  await setup
    .getByRole('combobox', { name: 'Choose resource', exact: true })
    .selectOption('existing');
  const workspace = await fixtureResources(page);
  await setup
    .getByRole('button', {
      name: `Phase 1 workspace Resource ID: ${workspace.workspace_id}`,
      exact: true,
    })
    .click();
  await setup
    .getByRole('button', {
      name: 'Add to this conversation',
      exact: true,
    })
    .click();
  await expect(
    setup.getByText('Resource ready', { exact: true }),
  ).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(
    page.getByRole('region', { name: 'Phase 1 workspace inspector' }),
  ).toBeVisible();

  await page
    .getByRole('button', { name: `${deckName} Design`, exact: true })
    .click();
  const preview = page.getByRole('region', {
    name: 'Design preview',
    exact: true,
  });
  await expect(preview).toBeVisible();
  await preview.getByRole('button', { name: 'Export', exact: true }).click();
  const exporting = preview.getByRole('region', {
    name: 'Design export',
    exact: true,
  });
  await exporting
    .getByRole('combobox', { name: 'Export format', exact: true })
    .selectOption('html');
  await exporting
    .getByRole('button', { name: 'Export design', exact: true })
    .click();
  const download = await captureBrowserDownload(page, () =>
    exporting
      .getByRole('button', { name: 'Download HTML', exact: true })
      .click(),
  );
  expect(download.name).toBe(`${deckName}.html`);
  expect(download.mimeType).toBe('text/html');
  expect(download.bytes.length).toBeGreaterThan(0);

  const payloadProof = mutationBodies.map((request) => ({
    path: request.path,
    bytes: new TextEncoder().encode(request.body).byteLength,
    containsClientAbsolutePath: /(?:[A-Za-z]:\\\\|\/(?:Users|home)\/)/.test(
      request.body,
    ),
  }));
  expect(payloadProof.length).toBeGreaterThan(0);
  expect(
    payloadProof.some((request) => request.containsClientAbsolutePath),
  ).toBe(false);
  await writeEvidence(testInfo, 'browser-resource-authority', {
    selectedServerResourceId: workspace.workspace_id,
    exportName: download.name,
    exportBytes: download.bytes.length,
    payloadProof,
    clientPathInputs: await page.locator('input[webkitdirectory]').count(),
    scope:
      'Real bounded API/resource/export flow through the isolated loopback browser host. Paired remote-cookie enforcement is covered by focused access/API tests; this browser runner does not claim a physical remote network.',
  });
  await captureSurface(page, testInfo, 'browser-artifact-and-workspace', {
    axe: false,
  });
});
