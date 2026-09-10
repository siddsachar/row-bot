import { isPanelDescriptor } from '../../api/types';
import type { PanelDescriptor, ResourceView } from '../../api/types';
import {
  boundedPanelKeys,
  openPanel,
  panelInstanceKey,
  panelKey,
  panelStatus,
  type PanelLayout,
  type PanelInstance,
  type PanelPlacement,
} from './model';

export type PanelPresentationInput = {
  conversationId: string;
  activeConversationId: string | null;
  resources: readonly ResourceView[];
  hints?: readonly PanelDescriptor[];
  capabilities?: ReadonlySet<string>;
  source: 'restore' | 'update' | 'explicit';
  descriptor?: PanelDescriptor;
  placement?: PanelPlacement;
};
export type PanelPresentationResult = {
  layout: PanelLayout;
  /** Newly available compact panels; announce without moving DOM focus. */
  available: PanelDescriptor[];
};

export function resourcePanelDescriptor(
  resource: ResourceView,
): PanelDescriptor | null {
  const kind = resource.binding.kind;
  if (kind !== 'artifact' && kind !== 'workspace') return null;
  return {
    panel_kind:
      kind === 'artifact' ? 'artifact.preview' : 'workspace.inspector',
    title: resource.title.slice(0, 160),
    resource_ref: resource.resource_ref,
    resource_kind: kind,
    resource_revision: resource.resource_revision,
  };
}

/** Current binding plus registry validation; this grants no read/write authority. */
export function validResourcePanel(
  descriptor: PanelDescriptor,
  conversationId: string,
  resources: readonly ResourceView[],
  capabilities: ReadonlySet<string> = new Set(),
): boolean {
  if (!isPanelDescriptor(descriptor)) return false;
  if (
    !['artifact.preview', 'workspace.inspector'].includes(descriptor.panel_kind)
  )
    return false;
  const resource = resources.find(
    (value) => value.resource_ref === descriptor.resource_ref,
  );
  if (
    !resource?.available ||
    !resource.binding.binding_id ||
    !resource.binding.resource_id ||
    resource.resource_ref !== `${conversationId}:${resource.binding.binding_id}`
  )
    return false;
  return (
    panelStatus(descriptor, {
      capabilities,
      resources: new Map([
        [
          resource.resource_ref,
          {
            kind: resource.binding.kind,
            revision: resource.resource_revision,
            status: 'available',
          },
        ],
      ]),
    }) === 'ready'
  );
}

function stableKey(
  resource: ResourceView,
  descriptor: PanelDescriptor,
): string {
  return JSON.stringify([
    descriptor.panel_kind,
    resource.binding.binding_id,
    resource.binding.resource_id,
    descriptor.subresource_key ?? '',
  ]);
}

export function validResourcePanelInstance(
  panel: PanelInstance,
  conversationId: string,
  resources: readonly ResourceView[],
  capabilities?: ReadonlySet<string>,
): boolean {
  if (
    !validResourcePanel(
      panel.descriptor,
      conversationId,
      resources,
      capabilities,
    )
  )
    return false;
  const resource = resources.find(
    (value) => value.resource_ref === panel.descriptor.resource_ref,
  )!;
  return (
    !panel.presentationKey ||
    panel.presentationKey === stableKey(resource, panel.descriptor)
  );
}

/**
 * Pure presentation policy. The caller owns the scoped layout and supplies only
 * committed current-conversation resource views, never assistant text/HTML.
 * Auto registration has no focus, navigation, mutation or observation effects.
 */
export function reconcilePanelPresentation(
  layout: PanelLayout,
  input: PanelPresentationInput,
): PanelPresentationResult {
  if (
    !input.conversationId ||
    input.activeConversationId !== input.conversationId
  )
    return { layout, available: [] };
  const resources = [...input.resources].sort((left, right) =>
    left.binding.binding_id < right.binding.binding_id
      ? -1
      : left.binding.binding_id > right.binding.binding_id
        ? 1
        : 0,
  );
  const find = (descriptor: PanelDescriptor) =>
    resources.find(
      (resource) => resource.resource_ref === descriptor.resource_ref,
    );
  let next = layout;
  let seen = [...layout.presentation.seen];
  let dismissed = [...layout.presentation.dismissed];
  let lastExplicitKey = layout.presentation.lastExplicitKey;
  // Refresh descriptors in place. A missing/revoked/replaced binding keeps its
  // identity for a truthful unavailable renderer; it never borrows another view.
  const panels = layout.panels.map((panel) => {
    const resource = find(panel.descriptor);
    if (!resource || !resource.available) return panel;
    const descriptor = {
      ...panel.descriptor,
      resource_revision: resource.resource_revision,
    };
    if (
      !validResourcePanel(
        descriptor,
        input.conversationId,
        resources,
        input.capabilities,
      )
    )
      return panel;
    const key = stableKey(resource, descriptor);
    if (panel.presentationKey && panel.presentationKey !== key) return panel;
    const oldKey = panelInstanceKey(panel);
    const migrate = (value: string) => (value === oldKey ? key : value);
    seen = seen.map(migrate);
    dismissed = dismissed.map(migrate);
    if (lastExplicitKey === oldKey) lastExplicitKey = key;
    if (
      panel.presentationKey === key &&
      panel.descriptor.title === resource.title.slice(0, 160).slice(0, 160) &&
      panel.descriptor.resource_revision === resource.resource_revision
    )
      return panel;
    return {
      ...panel,
      presentationKey: key,
      descriptor: { ...descriptor, title: resource.title.slice(0, 160) },
    };
  });
  if (panels.some((panel, index) => panel !== layout.panels[index]))
    next = { ...next, panels };
  const candidates: PanelDescriptor[] =
    input.source === 'explicit'
      ? input.descriptor
        ? [input.descriptor]
        : []
      : [
          ...resources
            .map(resourcePanelDescriptor)
            .filter((value): value is PanelDescriptor => value !== null),
          ...(input.hints ?? []),
        ];
  const available: PanelDescriptor[] = [];
  for (const descriptor of candidates) {
    if (
      !validResourcePanel(
        descriptor,
        input.conversationId,
        resources,
        input.capabilities,
      )
    )
      continue;
    const resource = find(descriptor)!;
    const key = stableKey(resource, descriptor);
    const explicit = input.source === 'explicit';
    const known = next.panels.find((panel) => panelInstanceKey(panel) === key);
    const wasSeen = seen.includes(key);
    seen.push(key);
    if (
      !explicit &&
      (wasSeen ||
        dismissed.includes(key) ||
        known ||
        (input.source === 'restore' && layout.presentation.initialized))
    )
      continue;
    // Old layouts may contain an unavailable instance for a replaced binding.
    // Never retarget it implicitly merely because the resource_ref was reused.
    const obsolete = next.panels.find(
      (panel) =>
        panelKey(panel.descriptor) === panelKey(descriptor) &&
        panelInstanceKey(panel) !== key,
    );
    if (obsolete) {
      if (!explicit) continue;
      next = {
        ...next,
        panels: next.panels.filter((panel) => panel !== obsolete),
      };
    }
    const priorActive = next.activePanelId;
    const opened = openPanel(next, descriptor, input.placement ?? 'side');
    const openedPanel = opened.panels.find(
      (panel) => panel.instance_id === opened.activePanelId,
    );
    if (
      !openedPanel ||
      panelKey(openedPanel.descriptor) !== panelKey(descriptor)
    )
      continue;
    next = {
      ...opened,
      side: !explicit && next.side.collapsed ? next.side : opened.side,
      bottom: !explicit && next.bottom.collapsed ? next.bottom : opened.bottom,
      panels: opened.panels.map((panel) =>
        panel === openedPanel ? { ...panel, presentationKey: key } : panel,
      ),
      activePanelId: explicit
        ? opened.activePanelId
        : (priorActive ??
          (layout.widthClass === 'desktop' ? opened.activePanelId : null)),
    };
    if (explicit) {
      dismissed = dismissed.filter(
        (value) => value !== key && value !== panelKey(descriptor),
      );
      lastExplicitKey = key;
    } else if (layout.widthClass !== 'desktop') {
      available.push(descriptor);
    }
  }
  const presentation = {
    initialized: true,
    seen: boundedPanelKeys(seen),
    dismissed: boundedPanelKeys(dismissed),
    lastExplicitKey,
  };
  if (JSON.stringify(presentation) !== JSON.stringify(layout.presentation))
    next = { ...next, presentation };
  else if (next !== layout)
    next = { ...next, presentation: layout.presentation };
  return { layout: next, available };
}
