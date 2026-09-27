import { afterEach, expect, it, vi } from 'vitest';
import { bindDraftSync } from './draft-sync';

afterEach(() => vi.unstubAllGlobals());

function controller(selected: string | null) {
  return {
    bindDraftChannel: vi.fn(() => vi.fn()),
    refreshDraft: vi.fn(async () => true),
    getSnapshot: () => ({ selectedConversationId: selected }) as never,
  };
}

it('binds a same-origin channel and re-reads the open draft on focus', () => {
  class Channel {
    constructor(readonly name: string) {}
  }
  vi.stubGlobal('BroadcastChannel', Channel);
  const value = controller('c1');
  const unbind = bindDraftSync(value);
  expect(value.bindDraftChannel).toHaveBeenCalledWith(expect.any(Channel));
  expect(
    (value.bindDraftChannel.mock.calls[0] as unknown as [Channel])[0].name,
  ).toBe('row-bot-client');
  window.dispatchEvent(new Event('focus'));
  document.dispatchEvent(new Event('visibilitychange'));
  expect(value.refreshDraft.mock.calls).toEqual([['c1'], ['c1']]);
  unbind();
  window.dispatchEvent(new Event('focus'));
  expect(value.refreshDraft).toHaveBeenCalledTimes(2);
});

it('works without BroadcastChannel and with nothing open', () => {
  vi.stubGlobal('BroadcastChannel', undefined);
  const value = controller(null);
  const unbind = bindDraftSync(value);
  expect(value.bindDraftChannel).not.toHaveBeenCalled();
  window.dispatchEvent(new Event('focus'));
  expect(value.refreshDraft).not.toHaveBeenCalled();
  unbind();
});
