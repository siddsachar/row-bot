import { afterEach, expect, it, vi } from 'vitest';
import { validateWire } from '../../../contracts/client-platform/v1/typescript/client';
import { ClientController } from './controller';
import { FixtureTransport } from './fixtures';
import type { Command } from './types';

const clients: ClientController[] = [];
afterEach(() => clients.splice(0).forEach((client) => client.dispose()));

it("sends this device's zone with every message, so its local time is the person's (M10)", async () => {
  const transport = new FixtureTransport();
  const command = vi.spyOn(transport, 'command');
  const controller = new ClientController(transport, () => 1);
  clients.push(controller);
  controller.setVisible(false);
  await controller.start();
  const payload = {
    submission_id: crypto.randomUUID(),
    text: 'Every weekday at 8, tell me Sydney weather.',
    attachment_refs: [],
    model_selection: { provider_id: 'fixture', model_ref: 'fixture::model' },
  };

  await controller.intent(
    'conversation-a',
    'conversation.submit',
    payload,
    '1',
  );
  await controller.intent(
    'conversation-a',
    'conversation.pin',
    { pinned: true },
    '2',
  );

  const [sent, other] = command.mock.calls.map((call) => call[1]);
  expect(validateWire<Command>('Command', sent)).toEqual(sent);
  expect(sent.payload).toEqual({
    ...payload,
    time_zone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  });
  expect(other.payload).toEqual({ pinned: true });
});
