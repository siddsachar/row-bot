import { expect, it } from 'vitest';
import type { EventRecord, GenerationState } from '../../api/types';
import { draftingKey, draftingOf } from './design-drafting';

const generation = {
  execution_id: 'e',
  conversation_id: 'chat',
  generation_id: 'g',
  pass_id: 'pass-1',
  status: 'running',
  revision: '1',
  cancel_requested: false,
  quiesced: false,
  cleanup_complete: false,
  external_outcome: 'not_applicable',
  approval_id: null,
  can_stop: true,
} as GenerationState;

function tool(
  name: string,
  state: 'tool_call' | 'tool_done',
  extra: Record<string, unknown> = {},
): EventRecord {
  return {
    cursor: `${name}-${state}`,
    event: {
      type: 'tool.activity',
      conversation_id: 'chat',
      payload: { tool_name: name, state, pass_id: 'pass-1', ...extra },
    },
  } as unknown as EventRecord;
}

it('says what the turn is doing to the design and changes key per finished step', () => {
  const started = [tool('designer_set_pages', 'tool_call')];
  const first = draftingKey('chat', generation, started);
  expect(draftingOf(first)?.label).toBe('Adding pages');
  const finished = [...started, tool('designer_set_pages', 'tool_done')];
  const second = draftingKey('chat', generation, finished);
  expect(second).not.toBe(first);
  const branding = [...finished, tool('designer_set_brand', 'tool_call')];
  expect(draftingOf(draftingKey('chat', generation, branding))?.label).toBe(
    'Choosing colours and fonts',
  );
  expect(
    draftingOf(
      draftingKey('chat', generation, [
        tool('designer_something_new', 'tool_call'),
      ]),
    )?.label,
  ).toBe('Working on the design');
});

it('shows nothing when the turn has not touched the design or has finished', () => {
  expect(
    draftingKey('chat', generation, [tool('web_search', 'tool_call')]),
  ).toBe('');
  const steps = [tool('designer_add_page', 'tool_done')];
  expect(draftingKey('chat', { ...generation, quiesced: true }, steps)).toBe(
    '',
  );
  expect(draftingKey('other', generation, steps)).toBe('');
  expect(draftingKey('chat', null, steps)).toBe('');
  // Steps from an earlier pass don't count.
  expect(
    draftingKey('chat', generation, [
      tool('designer_add_page', 'tool_done', { pass_id: 'pass-0' }),
    ]),
  ).toBe('');
  expect(draftingOf('')).toBeNull();
});
