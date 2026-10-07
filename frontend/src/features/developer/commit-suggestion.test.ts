import { describe, expect, it } from 'vitest';
import { suggestCommit } from './commit-suggestion';

describe('suggestCommit', () => {
  it('uses the one agent change set that covers the change', () => {
    expect(
      suggestCommit(
        [
          { path: 'src/app.py', status: 'M' },
          { path: 'tests/test_app.py', status: '??' },
        ],
        [
          {
            summary: 'Add a greeting endpoint and its test',
            reverted: false,
            paths: ['src/app.py'],
          },
          { summary: 'Old change', reverted: true },
        ],
      ),
    ).toEqual({
      subject: 'Add a greeting endpoint and its test',
      body: '- src/app.py\n- tests/test_app.py',
    });
  });

  it('describes files when no single change set explains them', () => {
    expect(
      suggestCommit([{ path: 'docs/guide.md', status: 'M' }], [])?.subject,
    ).toBe('Update guide.md');
    expect(
      suggestCommit(
        [
          { path: 'src/a/one.ts', status: '??' },
          { path: 'src/a/two.ts', status: 'A' },
        ],
        [],
      )?.subject,
    ).toBe('Add 2 files in src/a');
    const mixed = suggestCommit(
      [
        { path: 'a.txt', status: 'D' },
        { path: 'b/c.txt', status: 'M' },
      ],
      [
        { summary: 'First step', reverted: false },
        { summary: 'Second step', reverted: false },
      ],
    );
    expect(mixed?.subject).toBe('Update 2 files');
    expect(mixed?.body).toContain('- First step\n- Second step');
    expect(suggestCommit([], [])).toBeNull();
  });

  it('keeps the subject to one short line', () => {
    const subject = suggestCommit(
      [{ path: 'x.py', status: 'M' }],
      [{ summary: `Refactor ${'very '.repeat(30)}long`, reverted: false }],
    )?.subject;
    expect(subject!.length).toBeLessThanOrEqual(72);
    expect(subject).toMatch(/…$/);
  });
});
