import { expect, test, writeEvidence } from './evidence';
import { openFixture, type FixtureWindow } from './fixture';
import { captureSurface } from './phase5-helpers';

test('authenticated browser presentation has no ambient native authority', async ({
  page,
}, testInfo) => {
  await page.addInitScript(() => {
    Object.assign(window, {
      pywebview: { api: { native_client_dispatch: () => 'forged' } },
      native_available: true,
    });
  });
  await openFixture(page);
  const result = await page.evaluate(async () => {
    const fixture = (window as FixtureWindow).__ROW_BOT_FIXTURE__;
    const snapshot = fixture.controller.getSnapshot();
    return {
      status: snapshot.status,
      authenticationKind: snapshot.handshake?.authentication_kind,
      platform: await fixture.platform.discover(),
      managedWindow: await fixture.platform.managedWindow('/app-v2/'),
      commandCount: fixture.transport.counters.commands,
      ambientNativeGlobal: '__ROW_BOT_NATIVE_CLIENT__' in window,
    };
  });
  expect(result.status).toBe('ready');
  expect(result.platform).toMatchObject({
    status: 'ok',
    value: { kind: 'browser', platform: 'browser' },
  });
  expect(result.managedWindow).toEqual({
    status: 'unavailable',
    reason: 'managed_windows_require_native',
  });
  expect(result.commandCount).toBe(0);
  expect(result.ambientNativeGlobal).toBe(false);
  await writeEvidence(testInfo, 'remote-browser-presentation', {
    ...result,
    scope:
      'Deterministic browser presentation fixture. Paired-cookie issuance, exact-origin authentication and revocation are covered by the access/API subsystem tests because this loopback desktop runner is deliberately local-owner authenticated.',
  });
  await captureSurface(page, testInfo, 'remote-desktop-browser-surface');
});
