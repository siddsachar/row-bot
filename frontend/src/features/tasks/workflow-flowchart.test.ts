import { describe, expect, it } from 'vitest';
import {
  edgePath,
  flowLinks,
  layoutFlowchart,
  stepIssues,
  type FlowBox,
  type FlowStep,
  type FlowchartLayout,
} from './workflow-flowchart';

function step(
  id: string,
  type = 'prompt',
  fields: Partial<FlowStep['fields']> = {},
): FlowStep {
  return {
    id,
    type,
    fields: {
      next: null,
      if_true: null,
      if_false: null,
      if_approved: null,
      if_denied: null,
      prompt: 'Synthetic prompt',
      ...fields,
    },
  };
}

function overlaps(a: FlowBox, b: FlowBox) {
  return (
    a.x < b.x + b.width &&
    b.x < a.x + a.width &&
    a.y < b.y + b.height &&
    b.y < a.y + a.height
  );
}

/** Every card and terminal sits inside the canvas and no two overlap. */
function expectTidy(layout: FlowchartLayout) {
  const boxes = [...layout.steps, ...layout.terminals];
  for (const box of boxes) {
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.y).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(layout.width);
    expect(box.y + box.height).toBeLessThanOrEqual(layout.height);
  }
  for (let a = 0; a < boxes.length; a += 1)
    for (let b = a + 1; b < boxes.length; b += 1)
      expect(overlaps(boxes[a], boxes[b])).toBe(false);
  for (const edge of layout.edges)
    for (const point of edge.points) {
      expect(point.x).toBeGreaterThanOrEqual(0);
      expect(point.x).toBeLessThanOrEqual(layout.width);
      expect(point.y).toBeLessThanOrEqual(layout.height);
    }
}

const centre = (box: FlowBox) => box.x + box.width / 2;

describe('flowLinks', () => {
  it('reads branches the way the runner does', () => {
    const steps = [
      step('a'),
      step('check', 'condition', { if_true: '', if_false: 'end' }),
      step('ask', 'approval', { if_approved: null, if_denied: null }),
      step('tell', 'notify', { next: 'a' }),
    ];
    expect(flowLinks(steps)).toEqual([
      { from: 0, to: 1, branch: null },
      { from: 1, to: 2, branch: 'yes' },
      { from: 1, to: 'end', branch: 'no' },
      { from: 2, to: 3, branch: 'approved' },
      // An unanswered "When denied" stops the run.
      { from: 2, to: 'end', branch: 'denied' },
      { from: 3, to: 0, branch: 'next' },
    ]);
  });

  it('draws nothing for a branch to a removed step or to itself', () => {
    expect(
      flowLinks([
        step('a', 'prompt', { next: 'gone' }),
        step('b', 'prompt', { next: 'b' }),
      ]),
    ).toEqual([]);
  });
});

describe('stepIssues', () => {
  it('names the field to fix in plain words, never a step id', () => {
    const issues = stepIssues([
      step('a', 'condition', { if_true: 'gone', if_false: 'end' }),
      step('b', 'approval', { if_approved: 'b' }),
      step('c', 'condition', { if_true: '', if_false: null }),
      step('d', 'prompt', { prompt: 'Use {{ step.gone.output }}' }),
      step('e', 'prompt', { prompt: 'Use {{step.a.output}}', next: 'end' }),
    ]);
    expect([...issues]).toEqual([
      [
        0,
        {
          short: 'Goes to a removed step',
          message: 'When true goes to a step that was removed. Choose another.',
        },
      ],
      [
        1,
        {
          short: 'Goes back to itself',
          message: 'When approved goes back to this same step. Choose another.',
        },
      ],
      [
        2,
        {
          short: 'Choose a branch',
          message:
            'Choose a step or End for When true or When false. Both can’t continue to the next step.',
        },
      ],
      [
        3,
        {
          short: 'Uses a removed step',
          message: 'Uses the result of a step that was removed.',
        },
      ],
    ]);
    for (const issue of issues.values())
      expect(JSON.stringify(issue)).not.toMatch(/gone/);
  });
});

describe('layoutFlowchart', () => {
  it('draws a simple chain as one straight line from Start to End', () => {
    const layout = layoutFlowchart([step('a'), step('b'), step('c')]);
    expectTidy(layout);
    expect(layout.steps.map((box) => box.layer)).toEqual([1, 2, 3]);
    expect(new Set(layout.steps.map(centre)).size).toBe(1);
    expect(layout.terminals.map((item) => item.kind)).toEqual(['start', 'end']);
    const [start, end] = layout.terminals;
    expect(centre(start)).toBe(centre(layout.steps[0]));
    expect(centre(end)).toBe(centre(layout.steps[2]));
    expect(start.y).toBeLessThan(layout.steps[0].y);
    expect(end.y).toBeGreaterThan(layout.steps[2].y);
    expect(layout.edges.map((edge) => [edge.from, edge.to])).toEqual([
      ['start', 0],
      [0, 1],
      [1, 2],
      [2, 'end-2'],
    ]);
    for (const edge of layout.edges) {
      expect(edge.label).toBeNull();
      expect(edge.loop).toBe(false);
      expect(new Set(edge.points.map((point) => point.x)).size).toBe(1);
    }
    expect(layout.unreached).toBeNull();
  });

  it('splits a condition into labelled yes and no edges, yes on the left', () => {
    const layout = layoutFlowchart([
      step('draft'),
      step('check', 'condition', { if_true: 'publish', if_false: 'fix' }),
      step('publish', 'notify', { next: 'end' }),
      step('fix', 'prompt'),
    ]);
    expectTidy(layout);
    const edges = layout.edges.filter((edge) => edge.from === 1);
    expect(edges.map((edge) => [edge.label, edge.to])).toEqual([
      ['yes', 2],
      ['no', 3],
    ]);
    const [publish, fix] = [layout.steps[2], layout.steps[3]];
    expect(publish.layer).toBe(fix.layer);
    expect(centre(publish)).toBeLessThan(centre(layout.steps[1]));
    expect(centre(fix)).toBeGreaterThan(centre(layout.steps[1]));
    for (const edge of edges) {
      expect(edge.labelAt!.y).toBeGreaterThan(
        layout.steps[1].y + layout.steps[1].height,
      );
      expect(edge.labelAt!.y).toBeLessThan(publish.y);
    }
    // Each branch ends in its own End, under its own step.
    expect(layout.terminals.filter((item) => item.kind === 'end')).toHaveLength(
      2,
    );
  });

  it('keeps the main line straight past an optional step, which sits to one side', () => {
    const layout = layoutFlowchart([
      step('draft'),
      step('check', 'condition', { if_true: 'fix', if_false: 'review' }),
      step('fix'),
      step('review', 'approval', { if_approved: '', if_denied: 'end' }),
      step('publish', 'notify', { next: 'end' }),
    ]);
    expectTidy(layout);
    const [draft, check, fix, review, publish] = layout.steps;
    for (const box of [check, review, publish])
      expect(centre(box)).toBe(centre(draft));
    expect(centre(fix)).toBeLessThan(draft.x);
    expect(review.layer).toBe(fix.layer! + 1);
    // The "no" edge skips the optional step in a straight line.
    const skip = layout.edges.find((edge) => edge.label === 'no')!;
    expect(skip.to).toBe(3);
    expect(new Set(skip.points.map((point) => point.x)).size).toBe(1);
  });

  it('labels an approval approved and denied, and keeps the approved path straight', () => {
    const layout = layoutFlowchart([
      step('draft'),
      step('review', 'approval', { if_approved: '', if_denied: 'end' }),
      step('send', 'notify'),
    ]);
    expectTidy(layout);
    const edges = layout.edges.filter((edge) => edge.from === 1);
    expect(edges.map((edge) => edge.label)).toEqual(['approved', 'denied']);
    expect(centre(layout.steps[2])).toBeCloseTo(centre(layout.steps[1]), 0);
    const denied = layout.terminals.find((item) => item.id === 'end-1')!;
    expect(denied.y).toBeLessThan(layout.steps[2].y + layout.steps[2].height);
    expect(centre(denied)).toBeGreaterThan(
      layout.steps[2].x + layout.steps[2].width,
    );
  });

  it('routes a loop back up a lane right of every card it passes', () => {
    const layout = layoutFlowchart([
      step('draft'),
      step('review', 'approval', { if_approved: 'end', if_denied: 'draft' }),
    ]);
    expectTidy(layout);
    const loop = layout.edges.find((edge) => edge.loop)!;
    expect([loop.from, loop.to, loop.label]).toEqual([1, 0, 'denied']);
    const lane = Math.max(...loop.points.map((point) => point.x));
    for (const box of layout.steps)
      expect(lane).toBeGreaterThan(box.x + box.width);
    // It leaves its card going down and comes into the target from above.
    const [first, second] = loop.points;
    const last = loop.points[loop.points.length - 1];
    expect(first.x).toBe(second.x);
    expect(second.y).toBeGreaterThan(first.y);
    expect(last.y).toBe(layout.steps[0].y);
    expect(loop.labelAt!.x).toBe(lane);
    expect(edgePath(loop)).toMatch(/Q/);
    // A loop never moves a step down a layer.
    expect(layout.steps.map((box) => box.layer)).toEqual([1, 2]);
  });

  it('groups steps nothing leads to under "Not reached", in step order', () => {
    const layout = layoutFlowchart([
      step('a', 'prompt', { next: 'c' }),
      step('b'),
      step('c', 'prompt', { next: 'end' }),
      step('d'),
    ]);
    expectTidy(layout);
    expect(layout.steps.map((box) => box.layer)).toEqual([1, null, 2, null]);
    expect(layout.unreached).not.toBeNull();
    const [b, d] = [layout.steps[1], layout.steps[3]];
    const reachedBottom = Math.max(
      ...[...layout.steps.filter((box) => box.layer), ...layout.terminals].map(
        (box) => box.y + box.height,
      ),
    );
    expect(layout.unreached!.y).toBeGreaterThan(reachedBottom);
    expect(b.y).toBeGreaterThan(layout.unreached!.y);
    expect(b.x <= d.x || b.y < d.y).toBe(true);
    // No edge starts or ends at an unreached step.
    for (const edge of layout.edges) {
      expect([1, 3]).not.toContain(edge.from);
      expect([1, 3]).not.toContain(edge.to);
    }
  });

  it('keeps a large branching workflow tidy and within bounds', () => {
    const steps: FlowStep[] = Array.from({ length: 100 }, (_, index) => {
      const id = `s${index}`;
      if (index % 10 === 3)
        return step(id, 'condition', {
          if_true: `s${index + 1}`,
          if_false: index + 4 < 100 ? `s${index + 4}` : 'end',
        });
      if (index % 10 === 7)
        return step(id, 'approval', {
          if_approved: '',
          if_denied: index > 20 ? `s${index - 20}` : 'end',
        });
      return step(id);
    });
    steps[98] = step('s98', 'prompt', { next: 'end' });
    const layout = layoutFlowchart(steps);
    expectTidy(layout);
    expect(layout.steps).toHaveLength(100);
    // Branches widen the chart a little; it never grows sideways per step.
    expect(layout.width).toBeLessThan(1200);
    expect(layout.height).toBeLessThan(100 * 140);
    expect(layout.steps.filter((box) => box.layer === null)).toEqual([
      expect.objectContaining({ index: 99 }),
    ]);
    // The same steps always give the same picture.
    expect(layoutFlowchart(steps)).toEqual(layout);
  });

  it('makes room for a taller card that shows a problem', () => {
    const plain = layoutFlowchart([step('a'), step('b')]);
    const tall = layoutFlowchart([step('a'), step('b')], {
      nodeHeight: (index) => (index === 0 ? 80 : 60),
    });
    expect(tall.steps[0].height).toBe(80);
    expect(tall.steps[1].y - plain.steps[1].y).toBe(20);
    expectTidy(tall);
  });

  it('has nothing to draw without steps', () => {
    expect(layoutFlowchart([])).toMatchObject({ steps: [], edges: [] });
  });
});
