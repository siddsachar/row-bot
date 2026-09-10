import { useEffect, useState, type SetStateAction } from 'react';
import {
  layoutStorageKey,
  scopedLayoutStorageKey,
  persistLayout,
  panelInstanceKey,
  reconcileLayout,
  restoreLayout,
  type PanelLayout,
} from '../panels/model';

function read(
  instance: string,
  conversation: string,
  width: number,
  height: number,
): PanelLayout {
  try {
    const scoped = localStorage.getItem(
      scopedLayoutStorageKey(instance, conversation, width),
    );
    if (scoped !== null) return restoreLayout(scoped, width, height);
    const legacy = restoreLayout(
      localStorage.getItem(layoutStorageKey('local', width)),
      width,
      height,
    );
    const panels = legacy.panels.filter(
      (panel) =>
        !panel.descriptor.resource_ref ||
        panel.descriptor.resource_ref.startsWith(`${conversation}:`),
    );
    if (
      panels.length === legacy.panels.length &&
      panels.some((panel) => panel.descriptor.resource_ref)
    )
      return legacy;
    const keys = new Set(panels.map(panelInstanceKey));
    return {
      ...legacy,
      panels,
      activePanelId: panels.some(
        (panel) => panel.instance_id === legacy.activePanelId,
      )
        ? legacy.activePanelId
        : null,
      presentation: {
        initialized: panels.some((panel) => panel.descriptor.resource_ref),
        seen: legacy.presentation.seen.filter((key) => keys.has(key)),
        dismissed: legacy.presentation.dismissed.filter((key) => keys.has(key)),
        lastExplicitKey:
          legacy.presentation.lastExplicitKey &&
          keys.has(legacy.presentation.lastExplicitKey)
            ? legacy.presentation.lastExplicitKey
            : null,
      },
    };
  } catch {
    return restoreLayout(null, width, height);
  }
}

/** A conversation change swaps presentation before any old layout can persist. */
export function useWorkspaceLayout(
  instance = 'unavailable',
  conversation = 'home',
) {
  const scope = JSON.stringify([instance, conversation]);
  const [slot, setSlot] = useState(() => ({
    scope,
    layout: read(
      instance,
      conversation,
      window.innerWidth,
      window.innerHeight - 48,
    ),
  }));
  let layout = slot.layout;
  if (slot.scope !== scope) {
    layout = read(
      instance,
      conversation,
      window.innerWidth,
      window.innerHeight - 48,
    );
    setSlot({ scope, layout });
  }
  const setLayout = (action: SetStateAction<PanelLayout>) =>
    setSlot((previous) =>
      previous.scope !== scope
        ? previous
        : {
            scope,
            layout:
              typeof action === 'function' ? action(previous.layout) : action,
          },
    );
  useEffect(() => {
    const resize = () =>
      setSlot((previous) => {
        if (previous.scope !== scope) return previous;
        const next = reconcileLayout(
          previous.layout,
          window.innerWidth,
          window.innerHeight - 48,
        );
        const restored =
          next.widthClass === previous.layout.widthClass
            ? next
            : {
                ...read(
                  instance,
                  conversation,
                  window.innerWidth,
                  window.innerHeight - 48,
                ),
                panels: next.panels,
                activePanelId: next.activePanelId,
                nextInstance: next.nextInstance,
                suggestions: next.suggestions,
                presentation: next.presentation,
              };
        return { scope, layout: restored };
      });
    window.addEventListener('resize', resize);
    return () => window.removeEventListener('resize', resize);
  }, [scope, instance, conversation]);
  useEffect(() => {
    if (slot.scope !== scope || instance === 'unavailable') return;
    try {
      localStorage.setItem(
        scopedLayoutStorageKey(instance, conversation, layout.width),
        persistLayout(layout),
      );
    } catch {
      /* A blocked/full store still allows session-only layout changes. */
    }
  }, [slot.scope, scope, instance, conversation, layout]);
  return [layout, setLayout] as const;
}
