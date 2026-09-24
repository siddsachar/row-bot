import { expect, test, writeEvidence } from './evidence';
import { openFixture, type FixtureWindow } from './fixture';
import { captureSurface } from './phase5-helpers';

test('expired, revoked and unauthorized projects converge on a fenced recovery surface', async ({
  page,
}, testInfo) => {
  const scenario = String(testInfo.project.metadata.phase5Scenario ?? '');
  expect(['expired', 'revoked', 'unauthorized']).toContain(scenario);

  if (scenario === 'unauthorized') {
    await openFixture(page, 'unauthorized');
  } else {
    await openFixture(page);
    await page.evaluate(async () => {
      const fixture = (window as FixtureWindow).__ROW_BOT_FIXTURE__;
      fixture.transport.scenario = 'unauthorized';
      await fixture.controller.reconnect();
    });
  }

  const state = await page.evaluate(() => {
    const fixture = (window as FixtureWindow).__ROW_BOT_FIXTURE__;
    const snapshot = fixture.controller.getSnapshot();
    return {
      status: snapshot.status,
      handshakeRetained: snapshot.handshake !== null,
      projectionRetained: snapshot.projection !== null,
      commands: fixture.transport.counters.commands,
      activeSubscriptions: fixture.transport.counters.active,
      error: snapshot.error?.message,
    };
  });
  expect(state).toMatchObject({
    status: 'unauthorized',
    handshakeRetained: false,
    projectionRetained: false,
    commands: 0,
    activeSubscriptions: 0,
  });
  expect(state.error).toBeTruthy();
  await expect(page.getByText(state.error!, { exact: true })).toBeVisible();
  await writeEvidence(testInfo, `${scenario}-authority-loss`, {
    scenario,
    state,
    scope:
      scenario === 'unauthorized'
        ? 'Unauthenticated startup fixture.'
        : 'Client-side authority-loss transition. Server-side expiry/revocation identity and event-stream enforcement are asserted in focused access/API tests.',
  });
  await captureSurface(page, testInfo, `${scenario}-recovery`);
});
