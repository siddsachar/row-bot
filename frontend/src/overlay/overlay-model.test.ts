import { describe, expect, it } from 'vitest';
import type { ClientState, TranscriptRow } from '../api/types';
import {
  avatarActivity,
  isLive,
  kindLabel,
  latestResponse,
  overlayPhase,
  phaseLabel,
  plainText,
  progressLabel,
} from './overlay-model';

const row = (
  id: string,
  role: TranscriptRow['role'],
  text: string,
): TranscriptRow =>
  ({ id, role, blocks: [{ type: 'markdown', id, text }] }) as TranscriptRow;

type Generation = NonNullable<
  NonNullable<ClientState['projection']>['generation']
>;
const generation = (changes: Partial<Generation>): Generation =>
  ({
    conversation_id: 'c1',
    generation_id: 'g1',
    execution_id: 'e1',
    pass_id: 'p1',
    revision: '1',
    status: 'running',
    quiesced: false,
    can_stop: true,
    cancel_requested: false,
    cleanup_complete: false,
    external_outcome: 'not_applicable',
    approval_id: null,
    ...changes,
  }) as Generation;
const state = (
  rows: TranscriptRow[],
  value: Generation | null,
  activity: ClientState['activity'] = [],
) =>
  ({
    projection:
      value === null && !rows.length ? null : { rows, generation: value },
    activity,
  }) as unknown as Pick<ClientState, 'projection' | 'activity'>;
const tool = (name: string, status = 'pending', safe_input = '') =>
  ({
    event: {
      type: 'tool.activity',
      event_id: `${name}-${status}`,
      payload: { tool_name: name, status, safe_input },
    },
  }) as unknown as ClientState['activity'][number];

describe('plainText', () => {
  it('reads Markdown as compact words without rendering anything', () => {
    expect(
      plainText(
        '# Title\n\n**Bold** and `code` with [a link](https://x.test) and ![alt](y.png)\n\n- one\n2. two\n```js\nlet x\n```\n<b>tag</b>\r\n\n\n\nend',
      ),
    ).toBe(
      'Title\n\nBold and code with a link and alt\n\none\ntwo\n\nlet x\n\ntag\n\nend',
    );
    expect(plainText('   ')).toBe('');
  });
});

describe('latestResponse', () => {
  it('finds the newest assistant words and whether they answer the latest turn', () => {
    expect(
      latestResponse([
        row('1', 'user', 'hi'),
        row('2', 'assistant', '**Hello**'),
      ]),
    ).toEqual({ text: 'Hello', current: true });
    expect(
      latestResponse([
        row('1', 'assistant', 'Old answer'),
        row('2', 'user', 'new question'),
      ]),
    ).toEqual({ text: 'Old answer', current: false });
    expect(latestResponse([row('1', 'assistant', '  ')])).toEqual({
      text: '',
      current: false,
    });
  });
});

describe('overlayPhase', () => {
  it('tracks the run from the projection and activity', () => {
    expect(overlayPhase(state([], null), null)).toBe('idle');
    expect(
      overlayPhase(state([row('1', 'user', 'q')], generation({})), null),
    ).toBe('thinking');
    expect(
      overlayPhase(
        state(
          [row('1', 'user', 'q'), row('2', 'assistant', 'Partial')],
          generation({}),
        ),
        null,
      ),
    ).toBe('streaming');
    expect(
      overlayPhase(
        state([row('1', 'user', 'q')], generation({}), [tool('web_search')]),
        null,
      ),
    ).toBe('tool');
    expect(
      overlayPhase(
        state(
          [],
          generation({
            status: 'waiting_approval',
            quiesced: true,
            approval_id: 'a1',
          }),
        ),
        null,
      ),
    ).toBe('approval');
    expect(
      overlayPhase(state([], generation({ status: 'stopping' })), null),
    ).toBe('stopping');
    expect(
      overlayPhase(
        state([], generation({ status: 'interrupted', quiesced: true })),
        null,
      ),
    ).toBe('interrupted');
    expect(
      overlayPhase(
        state([], generation({ status: 'completed', quiesced: true })),
        'g1',
      ),
    ).toBe('failed');
    expect(
      overlayPhase(
        state([], generation({ status: 'completed', quiesced: true })),
        'other',
      ),
    ).toBe('completed');
    expect(
      overlayPhase(
        state([], generation({ status: 'stopped', quiesced: true })),
        null,
      ),
    ).toBe('stopped');
  });

  it('marks only running phases as live and labels every phase', () => {
    expect(
      ['thinking', 'tool', 'streaming', 'stopping'].every((phase) =>
        isLive(phase as never),
      ),
    ).toBe(true);
    expect(isLive('approval')).toBe(false);
    expect(phaseLabel('approval')).toBe('Waiting for approval');
    expect(phaseLabel('completed')).toBe('Ready');
  });
});

describe('progressLabel', () => {
  it('names the running step with its key argument', () => {
    expect(
      progressLabel([
        tool(
          'web_search',
          'pending',
          JSON.stringify({ query: 'weather in Oslo' }),
        ),
      ]),
    ).toBe('Searching the web · “weather in Oslo”');
    expect(progressLabel([tool('read_file')])).toMatch(/^Reading .*…$/);
    expect(progressLabel([tool('web_search', 'succeeded')])).toBe('Thinking…');
    expect(progressLabel([])).toBe('Thinking…');
  });
});

describe('avatar and kind', () => {
  it('maps phases onto the sidebar Buddy activity and categories onto words', () => {
    expect(avatarActivity('tool', true)).toBe('tool');
    expect(avatarActivity('failed', true)).toBe('error');
    expect(avatarActivity('interrupted', true)).toBe('stopped');
    expect(avatarActivity('streaming', false)).toBe('disconnected');
    expect(kindLabel('designer')).toBe('Design');
    expect(kindLabel('code')).toBe('Code');
    expect(kindLabel(undefined)).toBe('Chat');
  });
});
