import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ResourceView } from '../../api/types';
import { useWorkspaceLayout } from './layout';
import {
  closePanel,
  createPanelLayout,
  layoutStorageKey,
  persistLayout,
  resizeRegion,
  scopedLayoutStorageKey,
  toggleRegion,
} from '../panels/model';
import { reconcilePanelPresentation } from '../panels/presentation';

const resource = (conversation = 'a'): ResourceView => ({
  resource_ref: `${conversation}:deck`,
  conversation_revision: '1',
  binding: {
    binding_id: 'deck',
    resource_id: `deck-${conversation}`,
    kind: 'artifact',
    role: 'context',
    revision: '1',
  },
  title: `Deck ${conversation}`,
  resource_revision: '1',
  available: true,
});
const populated = (conversation = 'a') =>
  reconcilePanelPresentation(createPanelLayout(1440, 852), {
    conversationId: conversation,
    activeConversationId: conversation,
    resources: [resource(conversation)],
    source: 'restore',
  }).layout;
beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal('innerWidth', 1440);
  vi.stubGlobal('innerHeight', 900);
});
afterEach(() => vi.unstubAllGlobals());

it('restores A after A-B-C navigation and rejects a captured A layout setter while C is current', () => {
  const hook = renderHook(
    ({ conversation }) => useWorkspaceLayout('instance', conversation),
    { initialProps: { conversation: 'a' } },
  );
  act(() => hook.result.current[1](populated()));
  const oldSetter = hook.result.current[1];
  const savedA = localStorage.getItem(
    scopedLayoutStorageKey('instance', 'a', 1440),
  );
  hook.rerender({ conversation: 'b' });
  hook.rerender({ conversation: 'c' });
  act(() => oldSetter((layout) => resizeRegion(layout, 'navigation', 310)));
  expect(hook.result.current[0].panels).toHaveLength(0);
  expect(hook.result.current[0].navigation.size).toBe(240);
  expect(
    localStorage.getItem(scopedLayoutStorageKey('instance', 'a', 1440)),
  ).toBe(savedA);
  hook.rerender({ conversation: 'a' });
  expect(hook.result.current[0].panels[0].descriptor.resource_ref).toBe(
    'a:deck',
  );
});

it('retains explicit close across Home, route revisit and a fresh hook mount', () => {
  const hook = renderHook(
    ({ conversation }) => useWorkspaceLayout('instance', conversation),
    { initialProps: { conversation: 'a' } },
  );
  act(() => hook.result.current[1](populated()));
  act(() =>
    hook.result.current[1]((layout) =>
      closePanel(layout, layout.panels[0].instance_id),
    ),
  );
  const dismissal = hook.result.current[0].presentation.dismissed;
  hook.rerender({ conversation: 'home' });
  expect(hook.result.current[0].panels).toHaveLength(0);
  hook.rerender({ conversation: 'a' });
  expect(hook.result.current[0].presentation.dismissed).toEqual(dismissal);
  hook.unmount();
  const reloaded = renderHook(() => useWorkspaceLayout('instance', 'a'));
  const reconciled = reconcilePanelPresentation(reloaded.result.current[0], {
    conversationId: 'a',
    activeConversationId: 'a',
    resources: [resource()],
    source: 'restore',
  }).layout;
  expect(reconciled.panels).toHaveLength(0);
  expect(reconciled.presentation.dismissed).toEqual(dismissal);
});

it('migrates legacy geometry without importing another conversation panel or suppressing discovery', () => {
  const legacy = resizeRegion(populated('a'), 'navigation', 280);
  const serialized = persistLayout(legacy);
  localStorage.setItem(layoutStorageKey('local', 1440), serialized);
  const hook = renderHook(() => useWorkspaceLayout('instance', 'b'));
  expect(hook.result.current[0].navigation.size).toBe(280);
  expect(hook.result.current[0].panels).toHaveLength(0);
  const discovered = reconcilePanelPresentation(hook.result.current[0], {
    conversationId: 'b',
    activeConversationId: 'b',
    resources: [resource('b')],
    source: 'restore',
  }).layout;
  expect(
    discovered.panels.map((panel) => panel.descriptor.resource_ref),
  ).toEqual(['b:deck']);
  expect(localStorage.getItem(layoutStorageKey('local', 1440))).toBe(
    serialized,
  );
});

it('preserves legacy matching resource placement and collapse preferences', () => {
  const legacy = toggleRegion(populated('a'), 'side');
  localStorage.setItem(layoutStorageKey('local', 1440), persistLayout(legacy));
  const hook = renderHook(() => useWorkspaceLayout('instance', 'a'));
  expect(hook.result.current[0].side.collapsed).toBe(true);
  expect(hook.result.current[0].panels[0].descriptor.resource_ref).toBe(
    'a:deck',
  );
  expect(hook.result.current[0].presentation.dismissed).toEqual(
    legacy.presentation.dismissed,
  );
});

it('separates backend instances even when conversation IDs match', () => {
  const hook = renderHook(({ instance }) => useWorkspaceLayout(instance, 'a'), {
    initialProps: { instance: 'instance-a' },
  });
  act(() => hook.result.current[1](populated()));
  hook.rerender({ instance: 'instance-b' });
  expect(hook.result.current[0].panels).toHaveLength(0);
  hook.rerender({ instance: 'instance-a' });
  expect(hook.result.current[0].panels).toHaveLength(1);
});

it('migrates an empty collapsed legacy dock without suppressing available resource registration', () => {
  const legacy = toggleRegion(createPanelLayout(1440, 852), 'side');
  localStorage.setItem(layoutStorageKey('local', 1440), persistLayout(legacy));
  const hook = renderHook(() => useWorkspaceLayout('instance', 'a'));
  const discovered = reconcilePanelPresentation(hook.result.current[0], {
    conversationId: 'a',
    activeConversationId: 'a',
    resources: [resource()],
    source: 'restore',
  }).layout;
  expect(discovered.panels).toHaveLength(1);
  expect(discovered.side.collapsed).toBe(true);
});

it('keeps dismissal metadata and panel identity through compact breakpoint changes', () => {
  const hook = renderHook(() => useWorkspaceLayout('instance', 'a'));
  const desktop = toggleRegion(populated(), 'side');
  act(() => hook.result.current[1](desktop));
  act(() => {
    vi.stubGlobal('innerWidth', 390);
    window.dispatchEvent(new Event('resize'));
  });
  expect(hook.result.current[0].widthClass).toBe('phone');
  expect(hook.result.current[0].panels[0].presentationKey).toBe(
    desktop.panels[0].presentationKey,
  );
  expect(hook.result.current[0].presentation.dismissed).toEqual(
    desktop.presentation.dismissed,
  );
});
