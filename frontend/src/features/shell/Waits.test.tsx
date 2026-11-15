import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { WaitView } from '../../api/types';
import type { ClientPlatform } from '../../platform';
import { RuntimeContext } from '../../runtime';
import { WaitsStrip } from './Waits';

const CI: WaitView = {
  wait_id: 'a1b2c3d4e5f6',
  conversation_id: 'conversation-a',
  conversation_title: 'Ship 5.3.1',
  kind: 'check',
  label: 'CI on PR 590 is green',
  how: 'checks every 2 min',
  until: '2026-10-28T15:00',
  status: 'waiting',
};

function setup(items: WaitView[]) {
  const snapshot = { conversations: [] };
  const controller = {
    getSnapshot: () => snapshot,
    subscribe: () => () => undefined,
    waits: vi.fn(async () => ({ schema_version: 1, items })),
    stopWait: vi.fn(async () => ({ schema_version: 1, items: [] })),
  } as unknown as ClientController & { stopWait: ReturnType<typeof vi.fn> };
  render(
    <RuntimeContext.Provider
      value={{ controller, platform: {} as ClientPlatform }}
    >
      <WaitsStrip conversationId="conversation-a" />
    </RuntimeContext.Provider>,
  );
  return controller;
}

it('shows what this conversation waits for, and Stop ends it (5.3.1)', async () => {
  const controller = setup([
    CI,
    { ...CI, wait_id: 'f6e5d4c3b2a1', conversation_id: 'conversation-b' },
  ]);

  const strip = await screen.findByRole('region', { name: 'Waiting' });
  expect(strip).toHaveTextContent(
    'Waiting for CI on PR 590 is greenchecks every 2 min',
  );
  expect(screen.getAllByRole('listitem')).toHaveLength(1);

  await userEvent.click(screen.getByRole('button', { name: 'Stop waiting' }));
  expect(controller.stopWait).toHaveBeenCalledWith('a1b2c3d4e5f6');
  await waitFor(() =>
    expect(screen.queryByRole('region', { name: 'Waiting' })).toBeNull(),
  );
});

it('shows nothing when this conversation waits for nothing', async () => {
  const controller = setup([{ ...CI, conversation_id: 'conversation-b' }]);
  await waitFor(() => expect(controller.waits).toHaveBeenCalled());
  expect(screen.queryByRole('region', { name: 'Waiting' })).toBeNull();
});
