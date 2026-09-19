import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ClientController } from './api';
import {
  activeConversationSessionKey,
  bindActiveConversationSession,
} from './active-conversation-session';

type State = ReturnType<ClientController['getSnapshot']>;

function record(instance: string, conversation: string) {
  return JSON.stringify({
    schema_version: 1,
    instance_id: instance,
    conversation_id: conversation,
  });
}

function controller(initial: Partial<State> = {}) {
  let state = {
    status: 'loading',
    handshake: null,
    selectedConversationId: null,
    ...initial,
  } as State;
  const listeners = new Set<() => void>();
  const selectConversation = vi.fn(async (conversationId: string) => {
    state = { ...state, selectedConversationId: conversationId };
    listeners.forEach((listener) => listener());
  });
  const value = {
    getSnapshot: () => state,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    selectConversation,
  } as unknown as Pick<
    ClientController,
    'getSnapshot' | 'selectConversation' | 'subscribe'
  >;
  return {
    value,
    selectConversation,
    publish(patch: Partial<State>) {
      state = { ...state, ...patch };
      listeners.forEach((listener) => listener());
    },
    listenerCount: () => listeners.size,
  };
}

function ready(instanceId: string): Partial<State> {
  return {
    status: 'ready',
    handshake: { instance_id: instanceId } as State['handshake'],
  };
}

beforeEach(() => sessionStorage.clear());

describe('active conversation session', () => {
  it('restores a validated conversation only after the matching handshake', () => {
    sessionStorage.setItem(
      activeConversationSessionKey,
      record('instance-a', 'conversation_1'),
    );
    const client = controller();
    const dispose = bindActiveConversationSession(
      client.value,
      '/settings/goals',
      sessionStorage,
    );

    expect(client.selectConversation).not.toHaveBeenCalled();
    client.publish(ready('instance-a'));

    expect(client.selectConversation).toHaveBeenCalledOnce();
    expect(client.selectConversation).toHaveBeenCalledWith('conversation_1');
    expect(sessionStorage.getItem(activeConversationSessionKey)).toBe(
      record('instance-a', 'conversation_1'),
    );
    dispose();
    expect(client.listenerCount()).toBe(0);
  });

  it('writes only a validated selected ID under the authenticated instance', () => {
    const client = controller(ready('instance-a'));
    bindActiveConversationSession(client.value, '/', sessionStorage);

    client.publish({ selectedConversationId: 'chat-2' });

    expect(sessionStorage.getItem(activeConversationSessionKey)).toBe(
      record('instance-a', 'chat-2'),
    );
  });

  it('removes a record from another backend without restoring it', () => {
    sessionStorage.setItem(
      activeConversationSessionKey,
      record('instance-old', 'conversation_1'),
    );
    const client = controller();
    bindActiveConversationSession(client.value, '/settings', sessionStorage);

    client.publish(ready('instance-new'));

    expect(client.selectConversation).not.toHaveBeenCalled();
    expect(sessionStorage.getItem(activeConversationSessionKey)).toBeNull();
  });

  it.each([
    ['invalid JSON', '{'],
    [
      'invalid conversation ID',
      record('instance-a', '../private-conversation'),
    ],
    [
      'unexpected persisted fields',
      JSON.stringify({
        schema_version: 1,
        instance_id: 'instance-a',
        conversation_id: 'conversation_1',
        draft: 'must not persist',
      }),
    ],
  ])('rejects and removes %s', (_label, raw) => {
    sessionStorage.setItem(activeConversationSessionKey, raw);
    const client = controller(ready('instance-a'));

    bindActiveConversationSession(
      client.value,
      '/settings/goals',
      sessionStorage,
    );

    expect(client.selectConversation).not.toHaveBeenCalled();
    expect(sessionStorage.getItem(activeConversationSessionKey)).toBeNull();
  });

  it('lets an explicit conversation route supersede stored selection', () => {
    sessionStorage.setItem(
      activeConversationSessionKey,
      record('instance-a', 'stored-chat'),
    );
    const client = controller(ready('instance-a'));

    bindActiveConversationSession(
      client.value,
      '/conversations/route-chat',
      sessionStorage,
    );

    expect(client.selectConversation).not.toHaveBeenCalled();
  });

  it.each(['unauthorized', 'incompatible'] as const)(
    'clears retained selection after %s authentication',
    (status) => {
      sessionStorage.setItem(
        activeConversationSessionKey,
        record('instance-a', 'conversation_1'),
      );
      const client = controller();
      bindActiveConversationSession(client.value, '/settings', sessionStorage);

      client.publish({ status, handshake: null, selectedConversationId: null });

      expect(sessionStorage.getItem(activeConversationSessionKey)).toBeNull();
      expect(client.selectConversation).not.toHaveBeenCalled();
    },
  );
});
