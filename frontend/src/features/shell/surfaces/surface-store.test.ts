import { describe, expect, it } from 'vitest';
import type { TranscriptRow } from '../../../api/types';
import { carriedEdits, previousSurface } from './surface-store';

function drawing(id: string, surfaceId = 'water') {
  return {
    id,
    type: 'surface',
    surface_id: surfaceId,
    title: 'Water',
    surface_json: '{}',
    text: 'Water',
    state_key: `state-${id}`,
  } as TranscriptRow['blocks'][number];
}

const rows = [
  { message_id: 'r1', blocks: [drawing('a')] },
  { message_id: 'r2', blocks: [drawing('other', 'trip'), drawing('b')] },
] as unknown as TranscriptRow[];

describe('a redrawn interface', () => {
  it('replaces the drawing of the same surface just before it', () => {
    expect(previousSurface(rows, 'water', 'b')?.id).toBe('a');
    expect(previousSurface(rows, 'water', 'a')).toBeNull();
    expect(previousSurface(rows, 'trip', 'other')).toBeNull();
  });

  it('keeps what the person entered where Row-Bot left the value as it was', () => {
    const before = { glasses: { 1: false, 2: false }, goal: 8 };
    const entered = { glasses: { 1: true, 2: false }, goal: 8 };
    expect(
      carriedEdits({ ...before, note: 'Well done' }, before, entered),
    ).toEqual({
      glasses: { 1: true, 2: false },
      goal: 8,
      note: 'Well done',
    });
  });

  it('shows a value Row-Bot changed on purpose, and nothing new when nothing was entered', () => {
    const before = { budget: 2700 };
    const next = { budget: 3000 };
    expect(carriedEdits(next, before, { budget: 2500 })).toBe(next);
    expect(carriedEdits(next, before, null)).toBe(next);
  });
});
