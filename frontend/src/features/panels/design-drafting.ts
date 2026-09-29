import type { EventRecord, GenerationState } from '../../api/types';

/**
 * While a turn works on a design, the Design panel says what it is doing and
 * refreshes the page as each step is saved (U35). Only the designer's own
 * tools count (they show as "🎨 Designer" and name their step in
 * `runtime_tool`); a running turn that has not touched the design shows
 * nothing.
 */
const WORDS: Array<[RegExp, string]> = [
  [/^designer_(set_pages|add_page|add_screen)$/, 'Adding pages'],
  [/^designer_(update_page|refine_text)$/, 'Writing a page'],
  [/^designer_(delete_page|move_page|reorder_routes)$/, 'Arranging pages'],
  [/^designer_set_brand$/, 'Choosing colours and fonts'],
  [/^designer_rename_project$/, 'Naming the design'],
  [/^designer_(generate_image|insert_image|replace_image)$/, 'Adding pictures'],
  [/^designer_(add_chart)$/, 'Adding a chart'],
  [
    /^designer_(critique_page|brand_lint|apply_repairs)$/,
    'Checking the design',
  ],
  [/^designer_resize_project$/, 'Changing the size'],
];

export type DesignDrafting = {
  /** Changes whenever a designer step finishes: refresh the preview. */
  key: string;
  label: string;
};

function label(tool: string) {
  return (
    WORDS.find(([pattern]) => pattern.test(tool))?.[1] ??
    'Working on the design'
  );
}

/** A stable string (for selector equality); parse with `draftingOf`. */
export function draftingKey(
  conversationId: string,
  generation: GenerationState | null | undefined,
  activity: readonly EventRecord[],
): string {
  if (
    !generation ||
    generation.conversation_id !== conversationId ||
    generation.quiesced ||
    !['running', 'stopping'].includes(generation.status)
  )
    return '';
  let done = 0;
  let latest = '';
  for (const record of activity) {
    const event = record.event;
    if (
      event.type !== 'tool.activity' ||
      event.conversation_id !== conversationId
    )
      continue;
    const tool = event.payload.runtime_tool ?? '';
    if (!tool.startsWith('designer_')) continue;
    if (event.payload.pass_id && event.payload.pass_id !== generation.pass_id)
      continue;
    latest = tool;
    if (event.payload.state === 'tool_done') done += 1;
  }
  return latest ? `${generation.pass_id}\u0000${done}\u0000${latest}` : '';
}

export function draftingOf(key: string): DesignDrafting | null {
  if (!key) return null;
  const [, , tool = ''] = key.split('\u0000');
  return { key, label: label(tool) };
}
