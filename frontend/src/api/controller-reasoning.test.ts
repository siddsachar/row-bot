import { afterEach, expect, it, vi } from 'vitest';
import { validateWire } from '../../../contracts/client-platform/v1/typescript/client';
import { ClientController } from './controller';
import { FixtureTransport } from './fixtures';
import type { Command, ReasoningSelectionValue } from './types';

const clients: ClientController[] = [];
afterEach(() => clients.splice(0).forEach((client) => client.dispose()));

it.each<ReasoningSelectionValue>([
  { kind: 'provider_default' },
  { kind: 'effort', effort: 'exact-effort' },
  { kind: 'budget', budget: 1024 },
])(
  'preserves the exact typed reasoning %j through the single command owner and repeated receipt',
  async (selection) => {
    const transport = new FixtureTransport();
    const command = vi.spyOn(transport, 'command');
    const controller = new ClientController(transport, () => 1);
    clients.push(controller);
    controller.setVisible(false);
    await controller.start();
    const identity = crypto.randomUUID();
    const payload = {
      model_selection: {
        provider_id: 'fixture',
        model_ref: 'fixture::exact-model',
      },
      approval_mode: 'approve',
      runtime_mode: 'agent',
      profile_id: '',
      reasoning: {
        model_ref: 'fixture::exact-model',
        capability_revision: 'capability-exact',
        selection,
      },
    };
    const result = await controller.intent(
      'conversation-a',
      'conversation.controls',
      payload,
      '17',
      identity,
    );
    const admitted = command.mock.calls[0][1];
    expect(validateWire<Command>('Command', admitted)).toEqual(admitted);
    expect(admitted).toEqual({
      command_id: identity,
      client_session_id: controller.getSnapshot().handshake!.client_session_id,
      expected_revision: '17',
      type: 'conversation.controls',
      payload,
    });
    expect(command.mock.calls[0][0]).toBe('conversation-a');
    expect(command.mock.calls[0][2]).toBe(identity);
    expect(await controller.receipt(identity)).toEqual(result);
    expect(transport.counters.commands).toBe(1);
  },
);

it('does not replay or replace a rejected reasoning capability revision with a silent default', async () => {
  const transport = new FixtureTransport();
  const command = vi
    .spyOn(transport, 'command')
    .mockRejectedValue({ code: 'revision_conflict' });
  const controller = new ClientController(transport, () => 1);
  clients.push(controller);
  controller.setVisible(false);
  await controller.start();
  const identity = crypto.randomUUID();
  const payload = {
    reasoning: {
      model_ref: 'fixture::exact-model',
      capability_revision: 'stale-capability',
      selection: { kind: 'budget', budget: 2048 },
    },
  };
  await expect(
    controller.intent(
      'conversation-a',
      'conversation.controls',
      payload,
      '17',
      identity,
    ),
  ).rejects.toBeDefined();
  expect(command).toHaveBeenCalledTimes(1);
  expect(command.mock.calls[0][1].payload).toEqual(payload);
  expect(controller.getSnapshot().status).toBe('ready');
});
