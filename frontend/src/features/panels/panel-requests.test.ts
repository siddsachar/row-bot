import { expect, it, vi } from 'vitest';
import { onResourcePanelRequest, requestResourcePanel } from './panel-requests';

it('delivers a panel request to every listening workspace until it stops', () => {
  const listener = vi.fn();
  expect(
    requestResourcePanel({ conversationId: 'c', resourceRef: 'c:b' }),
  ).toBe(false);
  const stop = onResourcePanelRequest(listener);
  expect(
    requestResourcePanel({ conversationId: 'c', resourceRef: 'c:b' }),
  ).toBe(true);
  expect(listener).toHaveBeenCalledWith({
    conversationId: 'c',
    resourceRef: 'c:b',
  });
  stop();
  requestResourcePanel({ conversationId: 'c', resourceRef: 'c:x' });
  expect(listener).toHaveBeenCalledOnce();
});
