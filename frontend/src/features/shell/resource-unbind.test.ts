import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { ResourceView } from '../../api/types';
import { unbindWithUndo } from './resource-unbind';

const resource = {
  title: 'Tides deck',
  resource_ref: 'chat-a:binding-a',
  resource_revision: 'rev-7',
  binding: {
    binding_id: 'binding-a',
    kind: 'artifact',
    resource_id: 'design-a',
  },
} as unknown as ResourceView;

it('removes a resource from the conversation and offers Undo that adds the same one back (decision 19)', async () => {
  let revision = 'conv-1';
  const intent = vi.fn(async () => {
    revision = revision === 'conv-1' ? 'conv-2' : 'conv-3';
    return { status: 'completed' };
  });
  const controller = {
    intent,
    getSnapshot: () => ({ conversation: { revision } }),
  } as unknown as ClientController;
  const notify = vi.fn();
  await unbindWithUndo(controller, notify, 'chat-a', resource);
  expect(intent).toHaveBeenCalledWith(
    'chat-a',
    'conversation.unbind',
    { binding_id: 'binding-a' },
    'conv-1',
  );
  const [message, tone, action] = notify.mock.calls[0];
  expect(message).toBe('Removed Tides deck from this conversation.');
  expect(tone).toBeUndefined();
  expect(action.label).toBe('Undo');
  action.onAction();
  await vi.waitFor(() => expect(intent).toHaveBeenCalledTimes(2));
  expect(intent).toHaveBeenLastCalledWith(
    'chat-a',
    'resource.setup',
    {
      kind: 'artifact',
      intent: 'add',
      resource_id: 'design-a',
      expected_resource_revision: 'rev-7',
    },
    'conv-2',
  );
  await vi.waitFor(() =>
    expect(notify).toHaveBeenLastCalledWith(
      'Tides deck is back in this conversation.',
    ),
  );
});
