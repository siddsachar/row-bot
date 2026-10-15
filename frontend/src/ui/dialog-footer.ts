import { createContext, useContext, useLayoutEffect } from 'react';

/**
 * A dialog's footer, for content that has its own actions: while content
 * claims it, its actions are rendered there (a portal) in place of the
 * footer's Close, so the header's × stays the one way out and the primary
 * action is always in view, also on a phone.
 */
export type FooterSlot = {
  /** Where the actions go; null until the footer is on screen. */
  host: HTMLElement | null;
  /** Take the footer's place; the returned function gives it back. */
  claim: () => () => void;
};
export const DialogFooterContext = createContext<FooterSlot | null>(null);

/**
 * The dialog footer to render actions into while *active* (with
 * `createPortal`), or null: outside a dialog, or until its footer is on
 * screen, a caller renders its actions in place.
 */
export function useDialogFooter(active: boolean): HTMLElement | null {
  const slot = useContext(DialogFooterContext);
  const claim = slot?.claim;
  useLayoutEffect(
    () => (active && claim ? claim() : undefined),
    [active, claim],
  );
  return active ? (slot?.host ?? null) : null;
}
