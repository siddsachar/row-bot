import { useEffect, useState } from 'react';
import type {
  DesignControlItem,
  DesignControlsProps,
  DesignSection,
} from './ArtifactDesignControls';

export type DesignCatalog = {
  /** Every item of the section; null until the first read settles. */
  items: DesignControlItem[] | null;
  failed: boolean;
  retry: () => void;
};

/**
 * Every item of one design controls section (fonts, assets…) for a picker,
 * read when enabled and again when `key` changes (a new saved version).
 */
export function useDesignCatalog(
  load: DesignControlsProps['load'],
  pageId: string,
  section: DesignSection,
  enabled: boolean,
  key = '',
): DesignCatalog {
  const [items, setItems] = useState<DesignControlItem[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    let active = true;
    setFailed(false);
    (async () => {
      const found: DesignControlItem[] = [];
      let cursor: string | undefined;
      // A bounded walk over the section's pages (50 items each).
      for (let page = 0; page < 20; page += 1) {
        const value = await load({
          page_id: pageId,
          section,
          cursor,
          limit: 50,
        });
        if (value.section !== section)
          throw new Error('design_catalog_unavailable');
        found.push(...value.items);
        if (!value.next_cursor) break;
        cursor = value.next_cursor;
      }
      return found;
    })().then(
      (found) => {
        if (!active) return;
        setItems(found);
      },
      () => {
        if (active) setFailed(true);
      },
    );
    return () => {
      active = false;
    };
  }, [load, pageId, section, enabled, key, attempt]);
  return {
    items,
    failed,
    retry: () => setAttempt((value) => value + 1),
  };
}
