import { expect, test, writeEvidence } from './evidence';
import {
  captureSurface,
  openSeedConversation,
  publicHandshake,
} from './phase5-helpers';

test('localhost desktop uses the unified shell with separate authority and presentation capabilities', async ({
  page,
}, testInfo) => {
  await openSeedConversation(page);
  const handshake = await publicHandshake(page);
  expect(handshake.authenticationKind).toBe('local_owner');
  expect(handshake.compatibility).toBe('current');
  expect(handshake.applicationCapabilities.length).toBeGreaterThan(0);
  expect(handshake.presentationCapabilities).toEqual(['panels', 'responsive']);
  expect(handshake.nativeProofRequired).toBe(true);
  expect(
    handshake.applicationCapabilities.some((capability) =>
      capability.startsWith('viewport.'),
    ),
  ).toBe(false);
  expect(
    handshake.presentationCapabilities.some((capability) =>
      capability.startsWith('native:'),
    ),
  ).toBe(false);
  await expect(
    page.getByRole('textbox', { name: 'Message', exact: true }),
  ).toBeVisible();
  await writeEvidence(testInfo, 'public-capability-separation', handshake);
  await captureSurface(page, testInfo, 'localhost-desktop-conversation');
});
