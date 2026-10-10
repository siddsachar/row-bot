import { expect, test, writeEvidence } from './evidence';
import {
  fixtureResources,
  openConversation,
  revealContextControl,
} from './unified-helpers';
import { captureSurface } from './surface-helpers';

test('browser resource setup uses server IDs and never sends client paths', async ({
  page,
}, testInfo) => {
  const mutationBodies: { path: string; body: string }[] = [];
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname;
    if (request.method() === 'POST' && path.startsWith('/api/v1/'))
      mutationBodies.push({ path, body: request.postData() ?? '' });
  });
  await openConversation(page);
  await revealContextControl(page, 'Add resource');
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
    setup.getByText(/^(Design ready|Code folder ready)$/),
  ).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(
    page.getByRole('region', { name: 'Design preview', exact: true }),
  ).toBeVisible();

  await revealContextControl(page, 'Add resource');
  await page.getByRole('button', { name: 'Add resource', exact: true }).click();
  setup = page.getByRole('dialog', { name: 'Add resource', exact: true });
  const restart = setup.getByRole('button', {
    name: 'Create another',
    exact: true,
  });
  const resourceType = setup.getByRole('radiogroup', {
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
  await resourceType
    .getByRole('radio', { name: 'Code folder', exact: true })
    .click();
  await setup.getByRole('radio', { name: 'Open saved', exact: true }).click();
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
    setup.getByText(/^(Design ready|Code folder ready)$/),
  ).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(
    page.getByRole('region', { name: 'Phase 1 workspace inspector' }),
  ).toBeVisible();

  await revealContextControl(page, `${deckName} Design`);
  await page
    .getByRole('button', { name: `${deckName} Design`, exact: true })
    .click();
  const preview = page.getByRole('region', {
    name: 'Design preview',
    exact: true,
  });
  await expect(preview).toBeVisible();
  // This runner's browser is the computer's own owner, so an export saves into
  // the Exports folder (resources.spec); another device gets a download instead
  // (ArtifactExports.test.tsx, and the API's scoped download test).

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
    payloadProof,
    clientPathInputs: await page.locator('input[webkitdirectory]').count(),
    scope:
      'Real bounded API/resource flow through the isolated loopback browser host. Paired remote-cookie enforcement is covered by focused access/API tests; this browser runner does not claim a physical remote network.',
  });
  await captureSurface(page, testInfo, 'browser-artifact-and-workspace', {
    axe: false,
  });
});
