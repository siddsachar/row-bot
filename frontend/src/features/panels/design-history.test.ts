import { describe, expect, it } from 'vitest';
import {
  afterRedo,
  afterUndo,
  redoTarget,
  undoTarget,
  type HistoryEntry,
} from './design-history';

const entry = (id: string, available = true): HistoryEntry => ({
  id,
  available,
});

describe('design undo chain', () => {
  it('walks back through history and forward again', () => {
    // Saved before the two latest edits: 200 (before edit B), 100 (before A).
    let history = [entry('200.000001'), entry('100.000001')];
    expect(undoTarget(null, history, 'r3')).toBe('200.000001');
    // Undo restores 200 and snapshots the replaced state as 300.
    let chain = afterUndo(null, 'r3', '200.000001', 'r4', '300.000001');
    history = [entry('300.000001'), ...history];
    expect(redoTarget(chain, 'r4')).toBe('300.000001');
    // A second undo skips the snapshot the first undo created.
    expect(undoTarget(chain, history, 'r4')).toBe('100.000001');
    chain = afterUndo(chain, 'r4', '100.000001', 'r5', '400.000001');
    history = [entry('400.000001'), ...history];
    expect(undoTarget(chain, history, 'r5')).toBeNull();
    // Redo restores the most recent replaced state, then the one before.
    expect(redoTarget(chain, 'r5')).toBe('400.000001');
    chain = afterRedo(chain, 'r6');
    expect(redoTarget(chain, 'r6')).toBe('300.000001');
    expect(undoTarget(chain, history, 'r6')).toBe('100.000001');
  });

  it('ends the chain when anything else changes the design', () => {
    const chain = afterUndo(null, 'r1', '200', 'r2', '300');
    expect(redoTarget(chain, 'r-agent')).toBeNull();
    expect(undoTarget(chain, [entry('500'), entry('300')], 'r-agent')).toBe(
      '500',
    );
  });

  it('skips unavailable snapshots and ignores a stale created id', () => {
    expect(undoTarget(null, [entry('9', false), entry('8')], 'r')).toBe('8');
    expect(afterUndo(null, 'r', '8', 'r2', '7').redo).toEqual([]);
  });
});
