/**
 * How the step editor shows a workflow's steps: as a flowchart or as a list.
 * Remembered on this device, like the app's other view choices (the diff
 * view, the sidebar); the workflow itself never changes.
 */
export type StepView = 'flowchart' | 'list';

const KEY = 'row-bot.workflow-steps-view.v1';

export function readStepView(): StepView {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved === 'flowchart' || saved === 'list') return saved;
  } catch {
    /* Private browsing: fall through to the default. */
  }
  // A phone starts with the list; the flowchart is one tap away.
  try {
    if (window.matchMedia('(max-width: 640px)').matches) return 'list';
  } catch {
    /* No media queries: the flowchart. */
  }
  return 'flowchart';
}

export function saveStepView(view: StepView): void {
  try {
    localStorage.setItem(KEY, view);
  } catch {
    /* The choice still holds while the editor is open. */
  }
}
