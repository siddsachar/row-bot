/**
 * Records whether the latest interaction was a keyboard or a pointer as
 * `data-input-modality` on the root element. Programmatic focus targets such
 * as route headings and landmark regions (`tabindex="-1"`) draw a focus ring
 * only for keyboard users; browsers otherwise show one after a fresh load.
 */
const PURE_MODIFIERS = new Set(['Shift', 'Control', 'Alt', 'Meta', 'CapsLock']);

export function installInputModality(root: Document = document): () => void {
  const element = root.documentElement;
  const keyboard = (event: KeyboardEvent) => {
    if (PURE_MODIFIERS.has(event.key)) return;
    element.dataset.inputModality = 'keyboard';
  };
  const pointer = (event: PointerEvent) => {
    // Some screen readers synthesize clicks with zero coordinates; keep the
    // keyboard ring for them.
    if (event.pointerType === '' && event.clientX === 0 && event.clientY === 0)
      return;
    element.dataset.inputModality = 'pointer';
  };
  root.addEventListener('keydown', keyboard, true);
  root.addEventListener('pointerdown', pointer, true);
  return () => {
    root.removeEventListener('keydown', keyboard, true);
    root.removeEventListener('pointerdown', pointer, true);
    delete element.dataset.inputModality;
  };
}
