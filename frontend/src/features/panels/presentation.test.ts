import { describe, expect, it } from 'vitest';
import type { PanelDescriptor, ResourceView } from '../../api/types';
import {
  closeAllPanels,
  closePanel,
  createPanelLayout,
  focusPanel,
  movePanel,
  openPanel,
  persistLayout,
  resetLayout,
  resizeRegion,
  restoreLayout,
  samplePanels,
  scopedLayoutStorageKey,
  toggleRegion,
} from './model';
import {
  reconcilePanelPresentation,
  resourcePanelDescriptor,
  validResourcePanel,
  validResourcePanelInstance,
  type PanelPresentationInput,
} from './presentation';

const resource = (
  binding = 'deck',
  kind: 'artifact' | 'workspace' = 'artifact',
  conversation = 'a',
): ResourceView => ({
  resource_ref: `${conversation}:${binding}`,
  conversation_revision: '1',
  binding: {
    binding_id: binding,
    resource_id: `saved-${binding}`,
    kind,
    role: 'context',
    revision: '1',
  },
  title: `Saved ${binding}`,
  resource_revision: '1',
  available: true,
});
const deck = resource();
const workspace = resource('workspace', 'workspace');
const descriptor = resourcePanelDescriptor(deck)!;
const input = (
  overrides: Partial<PanelPresentationInput> = {},
): PanelPresentationInput => ({
  conversationId: 'a',
  activeConversationId: 'a',
  resources: [deck],
  source: 'restore',
  ...overrides,
});

describe('one deterministic conversation panel presentation policy', () => {
  it('keeps Home and plain chat empty and uses no resource or conversation mutation', () => {
    const layout = createPanelLayout();
    expect(
      reconcilePanelPresentation(
        layout,
        input({ conversationId: '', activeConversationId: null }),
      ).layout,
    ).toBe(layout);
    expect(
      reconcilePanelPresentation(layout, input({ resources: [] })).layout
        .panels,
    ).toEqual([]);
  });
  it('opens first available resources in stable binding order and reuses instances', () => {
    const first = reconcilePanelPresentation(
      createPanelLayout(),
      input({ resources: [workspace, deck] }),
    ).layout;
    expect(first.panels.map((panel) => panel.descriptor.resource_kind)).toEqual(
      ['artifact', 'workspace'],
    );
    expect(first.activePanelId).toBe(first.panels[0].instance_id);
    const replay = reconcilePanelPresentation(
      first,
      input({ resources: [workspace, deck], source: 'update' }),
    );
    expect(replay.layout).toBe(first);
    expect(replay.available).toEqual([]);
  });
  it.each([deck, workspace])(
    'explicit successful setup opens $binding.kind after confirmed binding',
    (value) => {
      const result = reconcilePanelPresentation(
        createPanelLayout(),
        input({
          source: 'explicit',
          resources: [value],
          descriptor: resourcePanelDescriptor(value)!,
        }),
      ).layout;
      expect(result.panels).toHaveLength(1);
      expect(result.activePanelId).toBe(result.panels[0].instance_id);
      const duplicate = reconcilePanelPresentation(
        result,
        input({
          source: 'explicit',
          resources: [value],
          descriptor: resourcePanelDescriptor(value)!,
        }),
      ).layout;
      expect(duplicate.panels).toHaveLength(1);
      expect(duplicate.panels[0].instance_id).toBe(
        result.panels[0].instance_id,
      );
    },
  );
  it('cannot open a success descriptor before binding commits or after revocation', () => {
    for (const resources of [[], [{ ...deck, available: false }]])
      expect(
        reconcilePanelPresentation(
          createPanelLayout(),
          input({ source: 'explicit', descriptor, resources }),
        ).layout.panels,
      ).toEqual([]);
  });
  it('preserves restored placement, explicit priority and collapsed state before discovery', () => {
    let layout = reconcilePanelPresentation(
      createPanelLayout(),
      input(),
    ).layout;
    layout = movePanel(layout, layout.activePanelId!, 'bottom');
    layout = toggleRegion(layout, 'bottom');
    layout = restoreLayout(persistLayout(layout), 1440, 900);
    const next = reconcilePanelPresentation(
      layout,
      input({ resources: [deck, workspace] }),
    ).layout;
    expect(next.panels).toHaveLength(1);
    expect(next.panels[0].placement).toBe('bottom');
    expect(next.bottom.collapsed).toBe(true);
    expect(
      reconcilePanelPresentation(
        next,
        input({ resources: [deck, workspace], source: 'update' }),
      ).layout,
    ).toBe(next);
  });
  it('refreshes title/revision in place without focus changes or repeated auto reveals', () => {
    const first = reconcilePanelPresentation(
      createPanelLayout(),
      input(),
    ).layout;
    const newer = { ...deck, title: 'Renamed Deck', resource_revision: '2' };
    const next = reconcilePanelPresentation(
      first,
      input({
        resources: [newer],
        source: 'update',
        hints: [resourcePanelDescriptor(newer)!],
      }),
    ).layout;
    expect(next.panels[0].instance_id).toBe(first.panels[0].instance_id);
    expect(next.panels[0].descriptor.title).toBe('Renamed Deck');
    expect(next.panels[0].descriptor.resource_revision).toBe('2');
    expect(next.activePanelId).toBe(first.activePanelId);
    expect(
      reconcilePanelPresentation(
        next,
        input({ resources: [newer], source: 'update' }),
      ).layout,
    ).toBe(next);
  });
  it('reveals a validated typed subresource hint once and ignores arbitrary markup', () => {
    const first = reconcilePanelPresentation(
      createPanelLayout(),
      input(),
    ).layout;
    const hint = { ...descriptor, subresource_key: 'page-2' };
    const next = reconcilePanelPresentation(
      first,
      input({
        source: 'update',
        hints: [
          hint,
          hint,
          '<script>openPanel()</script>' as unknown as PanelDescriptor,
        ],
      }),
    ).layout;
    expect(next.panels).toHaveLength(2);
    expect(next.activePanelId).toBe(first.activePanelId);
    expect(
      reconcilePanelPresentation(
        next,
        input({ source: 'update', hints: [hint] }),
      ).layout,
    ).toBe(next);
  });
  it('late A/B updates cannot change visible conversation C layout or focus', () => {
    const current = reconcilePanelPresentation(
      createPanelLayout(),
      input({
        conversationId: 'c',
        activeConversationId: 'c',
        resources: [resource('c-deck', 'artifact', 'c')],
      }),
    ).layout;
    for (const conversationId of ['a', 'b'])
      expect(
        reconcilePanelPresentation(
          current,
          input({
            conversationId,
            activeConversationId: 'c',
            source: 'explicit',
            descriptor,
          }),
        ).layout,
      ).toBe(current);
  });
  it('close survives updates, revisit and reload until explicit Open', () => {
    const opened = reconcilePanelPresentation(
      createPanelLayout(),
      input(),
    ).layout;
    let closed = closePanel(opened, opened.activePanelId!);
    closed = restoreLayout(persistLayout(closed), 1440, 900);
    expect(reconcilePanelPresentation(closed, input()).layout.panels).toEqual(
      [],
    );
    expect(
      reconcilePanelPresentation(
        closed,
        input({ source: 'update', hints: [descriptor] }),
      ).layout.panels,
    ).toEqual([]);
    const explicit = reconcilePanelPresentation(
      closed,
      input({ source: 'explicit', descriptor }),
    ).layout;
    expect(explicit.panels).toHaveLength(1);
    expect(explicit.presentation.dismissed).toEqual([]);
  });
  it('collapse persists and ordinary activity cannot reopen or replace the last explicit panel', () => {
    let first = reconcilePanelPresentation(
      createPanelLayout(),
      input({ source: 'explicit', descriptor }),
    ).layout;
    first = toggleRegion(first, 'side');
    const next = reconcilePanelPresentation(
      first,
      input({ source: 'update', resources: [deck, workspace] }),
    ).layout;
    expect(next.side.collapsed).toBe(true);
    expect(next.activePanelId).toBe(first.activePanelId);
    expect(next.presentation.lastExplicitKey).toBe(
      first.presentation.lastExplicitKey,
    );
    const reopened = reconcilePanelPresentation(
      next,
      input({ source: 'explicit', descriptor, resources: [deck, workspace] }),
    ).layout;
    expect(reopened.side.collapsed).toBe(false);
  });
  it('the last explicit resource retains priority against new resources and replay', () => {
    const initial = reconcilePanelPresentation(
      createPanelLayout(),
      input({ resources: [deck, workspace] }),
    ).layout;
    const selected = focusPanel(initial, initial.panels[1].instance_id);
    const newer = reconcilePanelPresentation(
      selected,
      input({
        source: 'update',
        resources: [resource('aaa'), deck, workspace],
      }),
    ).layout;
    expect(newer.activePanelId).toBe(selected.activePanelId);
    expect(newer.presentation.lastExplicitKey).toBe(
      selected.presentation.lastExplicitKey,
    );
  });
  it('keeps missing bindings unavailable without borrowing a different resource', () => {
    const first = reconcilePanelPresentation(
      createPanelLayout(),
      input(),
    ).layout;
    const missing = reconcilePanelPresentation(
      first,
      input({ source: 'update', resources: [] }),
    ).layout;
    expect(missing.panels[0]).toBe(first.panels[0]);
    expect(validResourcePanel(missing.panels[0].descriptor, 'a', [])).toBe(
      false,
    );
    const replaced = {
      ...deck,
      binding: { ...deck.binding, resource_id: 'another-deck' },
    };
    const next = reconcilePanelPresentation(
      first,
      input({ source: 'update', resources: [replaced] }),
    ).layout;
    expect(next.panels[0].presentationKey).toBe(
      first.panels[0].presentationKey,
    );
    expect(next.panels).toHaveLength(1);
    expect(validResourcePanelInstance(next.panels[0], 'a', [replaced])).toBe(
      false,
    );
  });
  it.each([
    [390, 844],
    [820, 1180],
  ])(
    'compact %dx%d automatic discovery only announces; explicit Open selects',
    (width, height) => {
      const initial = reconcilePanelPresentation(
        createPanelLayout(width, height),
        input(),
      );
      expect(initial.layout.activePanelId).toBeNull();
      expect(initial.available).toEqual([descriptor]);
      const selected = reconcilePanelPresentation(
        initial.layout,
        input({ source: 'explicit', descriptor }),
      ).layout;
      expect(selected.activePanelId).toBe(selected.panels[0].instance_id);
      const next = reconcilePanelPresentation(
        selected,
        input({ source: 'update', resources: [deck, workspace] }),
      );
      expect(next.layout.activePanelId).toBe(selected.activePanelId);
      expect(next.available).toHaveLength(1);
      const chat = focusPanel(next.layout, null);
      expect(
        reconcilePanelPresentation(
          chat,
          input({ source: 'update', resources: [deck, workspace] }),
        ).layout.activePanelId,
      ).toBeNull();
    },
  );
  it('close all records every view dismissal and preserves a deliberately empty restored layout', () => {
    const initial = reconcilePanelPresentation(
      createPanelLayout(),
      input({ resources: [deck, workspace] }),
    ).layout;
    const closed = restoreLayout(
      persistLayout(closeAllPanels(initial)),
      1440,
      900,
    );
    expect(closed.presentation.dismissed).toHaveLength(2);
    expect(
      reconcilePanelPresentation(
        closed,
        input({ resources: [deck, workspace] }),
      ).layout.panels,
    ).toEqual([]);
  });
  it.each([
    [1440, 900],
    [1280, 720],
    [820, 1180],
    [390, 844],
    [360, 800],
  ])(
    'reset at %dx%d keeps existing resources closed through updates and reload while allowing explicit or new resources',
    (width, height) => {
      const defaults = createPanelLayout(width, height);
      const resources = [deck, workspace];
      let layout = reconcilePanelPresentation(
        defaults,
        input({ resources }),
      ).layout;
      layout = movePanel(layout, layout.panels[1].instance_id, 'bottom');
      layout = resizeRegion(layout, 'navigation', 280);
      layout = resizeRegion(layout, 'side', 500);
      layout = resizeRegion(layout, 'bottom', 300);
      layout = toggleRegion(layout, 'side');
      layout = toggleRegion(layout, 'bottom');
      layout = resetLayout(layout);
      expect(layout.panels).toEqual([]);
      expect(layout.activePanelId).toBeNull();
      expect(layout.navigation).toEqual(defaults.navigation);
      expect(layout.side).toEqual(defaults.side);
      expect(layout.bottom).toEqual(defaults.bottom);
      expect(layout.presentation.initialized).toBe(true);
      expect(layout.presentation.dismissed).toHaveLength(2);
      expect(layout.presentation.lastExplicitKey).toBeNull();

      const revised = resources.map((value) => ({
        ...value,
        title: `${value.title} updated`,
        resource_revision: '2',
      }));
      // Ordinary refresh, a relevant revision, and replay cannot undo Reset.
      for (const current of [resources, revised, revised]) {
        const result = reconcilePanelPresentation(
          layout,
          input({
            resources: current,
            source: 'update',
            hints: current.map((value) => resourcePanelDescriptor(value)!),
          }),
        );
        expect(result.layout.panels).toEqual([]);
        expect(result.layout.activePanelId).toBeNull();
        expect(result.available).toEqual([]);
        layout = result.layout;
      }
      const restored = restoreLayout(persistLayout(layout), width, height);
      const revisit = reconcilePanelPresentation(
        restored,
        input({ resources: revised, source: 'restore' }),
      );
      expect(revisit.layout.panels).toEqual([]);
      expect(revisit.layout.activePanelId).toBeNull();
      expect(revisit.available).toEqual([]);

      const explicit = reconcilePanelPresentation(
        revisit.layout,
        input({
          resources: revised,
          source: 'explicit',
          descriptor: resourcePanelDescriptor(revised[0])!,
        }),
      );
      expect(explicit.layout.panels).toHaveLength(1);
      expect(explicit.layout.panels[0].descriptor.resource_ref).toBe(
        deck.resource_ref,
      );
      expect(explicit.layout.activePanelId).toBe(
        explicit.layout.panels[0].instance_id,
      );
      expect(explicit.layout.presentation.dismissed).toHaveLength(1);
      expect(explicit.available).toEqual([]);

      const added = resource('new-deck');
      const discovered = reconcilePanelPresentation(
        revisit.layout,
        input({ resources: [...revised, added], source: 'update' }),
      );
      expect(discovered.layout.panels).toHaveLength(1);
      expect(discovered.layout.panels[0].descriptor.resource_ref).toBe(
        added.resource_ref,
      );
      expect(discovered.available).toEqual(
        defaults.widthClass === 'desktop'
          ? []
          : [resourcePanelDescriptor(added)],
      );
      expect(discovered.layout.activePanelId).toBe(
        defaults.widthClass === 'desktop'
          ? discovered.layout.panels[0].instance_id
          : null,
      );
    },
  );
  it('still resets sample panel geometry and closes its view', () => {
    const defaults = createPanelLayout();
    let layout = openPanel(defaults, samplePanels[0]);
    layout = resizeRegion(layout, 'navigation', 280);
    layout = resizeRegion(layout, 'side', 500);
    layout = toggleRegion(layout, 'side');
    const reset = resetLayout(layout);
    expect({ ...reset, presentation: defaults.presentation }).toEqual(defaults);
    expect(reset.presentation.initialized).toBe(true);
    expect(reset.presentation.dismissed).toHaveLength(1);
    expect(reset.presentation.lastExplicitKey).toBeNull();
  });
  it('migrates old saved resource geometry and collapse identity without losing preferences', () => {
    const old = {
      version: 1,
      widthClass: 'desktop',
      side: { size: 390, restoreSize: 390, collapsed: true },
      panels: [
        {
          instance_id: 'panel-3',
          descriptor,
          placement: 'side',
          visibility: 'visible',
        },
      ],
      activePanelId: 'panel-3',
    };
    const migrated = reconcilePanelPresentation(
      restoreLayout(JSON.stringify(old), 1440, 900),
      input(),
    ).layout;
    expect(migrated.side).toEqual(old.side);
    expect(migrated.presentation.dismissed).toContain(
      migrated.panels[0].presentationKey,
    );
    expect(migrated.panels[0].instance_id).toBe('panel-3');
    const empty = restoreLayout('{"version":0,"side":390}', 1440, 900);
    expect(
      reconcilePanelPresentation(empty, input()).layout.panels,
    ).toHaveLength(1);
  });
  it('separates storage by backend instance, conversation and width class', () => {
    const keys = [
      scopedLayoutStorageKey('one', 'a', 1440),
      scopedLayoutStorageKey('two', 'a', 1440),
      scopedLayoutStorageKey('one', 'b', 1440),
      scopedLayoutStorageKey('one', 'a', 390),
      scopedLayoutStorageKey('one', null, 1440),
    ];
    expect(new Set(keys).size).toBe(keys.length);
  });
  it('fails closed for forged, stale, unsupported, capability or mismatched descriptors', () => {
    for (const invalid of [
      { ...descriptor, panel_kind: 'workspace.inspector' },
      { ...descriptor, resource_kind: 'workspace' },
      { ...descriptor, resource_revision: '999' },
      { ...descriptor, resource_ref: 'b:deck' },
      { ...descriptor, panel_kind: 'untrusted.renderer' },
      { ...descriptor, required_capabilities: ['native.write'] },
    ] as PanelDescriptor[])
      expect(validResourcePanel(invalid, 'a', [deck])).toBe(false);
    expect(
      validResourcePanel(descriptor, 'a', [
        { ...deck, resource_ref: 'a:other' },
      ]),
    ).toBe(false);
  });
});

it('keeps a long named resource reopenable within the typed panel title limit', () => {
  const named = { ...deck, title: 'A'.repeat(240) };
  const bounded = resourcePanelDescriptor(named)!;
  expect(bounded.title).toHaveLength(160);
  expect(validResourcePanel(bounded, 'a', [named])).toBe(true);
  const opened = reconcilePanelPresentation(
    createPanelLayout(),
    input({ resources: [named], source: 'explicit', descriptor: bounded }),
  );
  expect(opened.layout.panels).toHaveLength(1);
  expect(opened.layout.panels[0].descriptor.resource_ref).toBe(
    named.resource_ref,
  );
});
