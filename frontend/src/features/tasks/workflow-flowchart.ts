import type { TaskGraphFields } from '../../api/types';

/**
 * The workflow editor's flowchart (F23): where each step goes next, the
 * problems a person should fix, and a small layered layout. Pure and
 * deterministic, with no graph library: the same steps always give the same
 * picture.
 */

type BranchField =
  'next' | 'if_true' | 'if_false' | 'if_approved' | 'if_denied';

export type FlowStep = {
  id: string;
  type: string;
  fields: Pick<TaskGraphFields, BranchField> &
    Partial<
      Pick<
        TaskGraphFields,
        'prompt' | 'message' | 'objective' | 'condition' | 'task_id' | 'context'
      >
    >;
};

/** A step's position in the list, or the end of the workflow. */
export type FlowTarget = number | 'end';
export type FlowBranch = 'yes' | 'no' | 'approved' | 'denied' | 'next';

export type FlowLink = {
  from: number;
  to: FlowTarget;
  /** The branch this follows. Plain step order has none. */
  branch: FlowBranch | null;
};

/** A problem with a step: a few words for its card, the sentence for its form. */
export type StepIssue = { short: string; message: string };

/** The form's own names for each branch, so a problem names the field to fix. */
export const BRANCH_FIELDS: Record<BranchField, string> = {
  next: 'Next step',
  if_true: 'When true',
  if_false: 'When false',
  if_approved: 'When approved',
  if_denied: 'When denied',
};

/**
 * Where each step goes, as the runner reads it (`tasks.py`): an empty branch
 * continues to the following step (the end after the last one), except a
 * denied approval, which stops the run. A branch to a removed step or to the
 * step itself draws nothing; `stepIssues` names it instead.
 */
export function flowLinks(steps: readonly FlowStep[]): FlowLink[] {
  const positions = new Map(steps.map((step, index) => [step.id, index]));
  const links: FlowLink[] = [];
  steps.forEach((step, index) => {
    const following: FlowTarget = index + 1 < steps.length ? index + 1 : 'end';
    const resolve = (value: string | null | undefined): FlowTarget | null => {
      if (!value) return following;
      if (value === 'end') return 'end';
      const target = positions.get(value);
      return target === undefined || target === index ? null : target;
    };
    const add = (to: FlowTarget | null, branch: FlowBranch | null) => {
      if (to !== null) links.push({ from: index, to, branch });
    };
    const { fields } = step;
    if (step.type === 'condition') {
      add(resolve(fields.if_true), 'yes');
      add(resolve(fields.if_false), 'no');
    } else if (step.type === 'approval') {
      add(resolve(fields.if_approved), 'approved');
      add(fields.if_denied ? resolve(fields.if_denied) : 'end', 'denied');
    } else {
      const chosen = fields.next && fields.next !== 'end';
      add(resolve(fields.next), chosen ? 'next' : null);
    }
  });
  return links;
}

const OUTPUT_REFERENCE = /\{\{\s*step\.([A-Za-z0-9_-]+)\.output\s*\}\}/g;

/** What would stop a save or a run, in plain words, one per step at most. */
export function stepIssues(steps: readonly FlowStep[]): Map<number, StepIssue> {
  const ids = new Set(steps.map((step) => step.id));
  const issues = new Map<number, StepIssue>();
  steps.forEach((step, index) => {
    const fields: BranchField[] =
      step.type === 'condition'
        ? ['if_true', 'if_false']
        : step.type === 'approval'
          ? ['if_approved', 'if_denied']
          : ['next'];
    for (const field of fields) {
      const value = step.fields[field];
      if (!value || value === 'end') continue;
      const name = BRANCH_FIELDS[field];
      if (value === step.id) {
        issues.set(index, {
          short: 'Goes back to itself',
          message: `${name} goes back to this same step. Choose another.`,
        });
        return;
      }
      if (!ids.has(value)) {
        issues.set(index, {
          short: 'Goes to a removed step',
          message: `${name} goes to a step that was removed. Choose another.`,
        });
        return;
      }
    }
    if (
      step.type === 'condition' &&
      !step.fields.if_true &&
      !step.fields.if_false
    ) {
      issues.set(index, {
        short: 'Choose a branch',
        message:
          'Choose a step or End for When true or When false. Both can’t continue to the next step.',
      });
      return;
    }
    for (const text of [
      step.fields.prompt,
      step.fields.message,
      step.fields.objective,
      step.fields.context,
    ]) {
      for (const match of String(text ?? '').matchAll(OUTPUT_REFERENCE)) {
        if (!ids.has(match[1])) {
          issues.set(index, {
            short: 'Uses a removed step',
            message: 'Uses the result of a step that was removed.',
          });
          return;
        }
      }
    }
  });
  return issues;
}

export type FlowchartOptions = {
  nodeWidth: number;
  /** A step's card height; a card that shows a problem is taller. */
  nodeHeight: (index: number) => number;
  terminalWidth: number;
  terminalHeight: number;
  columnGap: number;
  rowGap: number;
  margin: number;
  laneGap: number;
  /** "Not reached" steps sit in a grid at most this many cards wide. */
  groupColumns: number;
};

export const FLOWCHART_DEFAULTS: FlowchartOptions = {
  nodeWidth: 232,
  nodeHeight: () => 60,
  terminalWidth: 72,
  terminalHeight: 28,
  columnGap: 40,
  rowGap: 48,
  margin: 24,
  laneGap: 16,
  groupColumns: 3,
};

export type FlowBox = { x: number; y: number; width: number; height: number };
export type FlowPoint = { x: number; y: number };
export type FlowStepBox = FlowBox & {
  index: number;
  /** Layers count from the top; "Not reached" steps have none. */
  layer: number | null;
};
export type FlowTerminal = FlowBox & { id: string; kind: 'start' | 'end' };
export type FlowEdge = {
  id: string;
  from: number | 'start';
  /** A step's position, or the id of an End terminal. */
  to: number | string;
  label: string | null;
  /** Goes back up to an earlier step (a loop), routed down the right side. */
  loop: boolean;
  points: FlowPoint[];
  labelAt: FlowPoint | null;
};
export type FlowchartLayout = {
  width: number;
  height: number;
  /** One per step, in step order. */
  steps: FlowStepBox[];
  terminals: FlowTerminal[];
  edges: FlowEdge[];
  /** Where the "Not reached" heading sits, when any step can't be reached. */
  unreached: FlowPoint | null;
};

type Vertex = {
  key: string;
  layer: number;
  width: number;
  height: number;
  weight: number;
  gap: number;
  x: number;
  order: number;
};
type Segment = {
  edge: number;
  from: Vertex;
  to: Vertex;
  port: number;
  ports: number;
};

/**
 * Pool-adjacent-violators: centres as close to `targets` as the spacing
 * allows, keeping the given order (`gaps[k]` between items k and k+1).
 */
function placeInOrder(
  targets: number[],
  weights: number[],
  gaps: number[],
): number[] {
  const offsets = [0];
  for (let k = 1; k < targets.length; k += 1)
    offsets.push(offsets[k - 1] + gaps[k - 1]);
  const blocks: { value: number; weight: number; size: number }[] = [];
  targets.forEach((target, k) => {
    blocks.push({ value: target - offsets[k], weight: weights[k], size: 1 });
    while (
      blocks.length > 1 &&
      blocks[blocks.length - 2].value > blocks[blocks.length - 1].value
    ) {
      const last = blocks.pop()!;
      const previous = blocks[blocks.length - 1];
      const weight = previous.weight + last.weight;
      // Weightless items (an End) move out of the way; if a block is only
      // weightless items, they share the difference.
      previous.value = weight
        ? (previous.value * previous.weight + last.value * last.weight) / weight
        : (previous.value * previous.size + last.value * last.size) /
          (previous.size + last.size);
      previous.weight = weight;
      previous.size += last.size;
    }
  });
  const placed: number[] = [];
  for (const block of blocks)
    for (let k = 0; k < block.size; k += 1)
      placed.push(block.value + offsets[placed.length]);
  return placed;
}

/**
 * A layered, top-to-bottom layout: Start, then each step ranked by its
 * longest distance from the first step (loops set aside), an End after each
 * step that finishes the workflow, and the steps no path reaches in a
 * "Not reached" group below. Long edges pass through spacers so they never
 * cross a card; loops run up a lane on the right.
 */
export function layoutFlowchart(
  steps: readonly FlowStep[],
  supplied: Partial<FlowchartOptions> = {},
): FlowchartLayout {
  const options = { ...FLOWCHART_DEFAULTS, ...supplied };
  const { margin, rowGap, columnGap } = options;
  const links = flowLinks(steps);
  const count = steps.length;
  if (!count)
    return {
      width: margin * 2,
      height: margin * 2,
      steps: [],
      terminals: [],
      edges: [],
      unreached: null,
    };

  // Group parallel links (both answers to one step) into one labelled edge.
  type Logical = { from: number; to: FlowTarget; labels: string[] };
  const outgoing: Logical[][] = steps.map(() => []);
  for (const link of links) {
    const list = outgoing[link.from];
    const same = list.find((item) => item.to === link.to);
    if (same) {
      if (link.branch) same.labels.push(link.branch);
    } else
      list.push({
        from: link.from,
        to: link.to,
        labels: link.branch ? [link.branch] : [],
      });
  }

  // Depth-first from the first step, following branches in order: the
  // visiting order seeds the layer order, and a link back to a step still
  // being visited is a loop.
  const state = new Array<0 | 1 | 2>(count).fill(0);
  const preorder = new Array<number>(count).fill(-1);
  const finished: number[] = [];
  const loops = new Set<Logical>();
  let visited = 0;
  const visit = (index: number) => {
    state[index] = 1;
    preorder[index] = visited;
    visited += 1;
    for (const item of outgoing[index]) {
      if (item.to === 'end') continue;
      if (state[item.to] === 1) loops.add(item);
      else if (state[item.to] === 0) visit(item.to);
    }
    state[index] = 2;
    finished.push(index);
  };
  visit(0);
  const reached = (index: number) => state[index] !== 0;

  // Longest distance from the first step, ignoring loops.
  const rank = new Array<number>(count).fill(0);
  for (const index of [...finished].reverse())
    for (const item of outgoing[index])
      if (item.to !== 'end' && !loops.has(item))
        rank[item.to] = Math.max(rank[item.to], rank[index] + 1);

  const vertices: Vertex[] = [];
  const vertex = (
    key: string,
    layer: number,
    width: number,
    height: number,
    weight: number,
    gap: number,
  ) => {
    const made: Vertex = {
      key,
      layer,
      width,
      height,
      weight,
      gap,
      x: 0,
      order: 0,
    };
    vertices.push(made);
    return made;
  };
  const start = vertex(
    'start',
    0,
    options.terminalWidth,
    options.terminalHeight,
    1,
    columnGap,
  );
  const stepVertex = new Map<number, Vertex>();
  for (let index = 0; index < count; index += 1)
    if (reached(index))
      stepVertex.set(
        index,
        vertex(
          `step-${index}`,
          rank[index] + 1,
          options.nodeWidth,
          options.nodeHeight(index),
          1,
          columnGap,
        ),
      );
  const ends = new Map<number, Vertex>();

  type Planned = {
    id: string;
    from: number | 'start';
    to: number | string;
    label: string | null;
    loop: boolean;
    source: Vertex;
    target: Vertex;
  };
  const planned: Planned[] = [
    {
      id: 'start',
      from: 'start',
      to: 0,
      label: null,
      loop: false,
      source: start,
      target: stepVertex.get(0)!,
    },
  ];
  for (let index = 0; index < count; index += 1) {
    if (!reached(index)) continue;
    const source = stepVertex.get(index)!;
    for (const item of outgoing[index]) {
      let target: Vertex;
      if (item.to === 'end') {
        target =
          ends.get(index) ??
          vertex(
            `end-${index}`,
            source.layer + 1,
            options.terminalWidth,
            options.terminalHeight,
            // An End never pulls: the step beside it keeps its line.
            0,
            columnGap,
          );
        ends.set(index, target);
      } else target = stepVertex.get(item.to)!;
      planned.push({
        id: `${index}-${item.to}`,
        from: index,
        to: item.to === 'end' ? target.key : item.to,
        label: item.labels.length ? item.labels.join(' / ') : null,
        loop: loops.has(item),
        source,
        target,
      });
    }
  }

  // Forward edges become one segment per layer, through spacers.
  const segments: Segment[] = [];
  const chains = new Map<number, Vertex[]>();
  const portsFrom = new Map<Vertex, number>();
  planned.forEach((edge) =>
    portsFrom.set(edge.source, (portsFrom.get(edge.source) ?? 0) + 1),
  );
  const portIndex = new Map<Vertex, number>();
  planned.forEach((edge, number) => {
    const port = portIndex.get(edge.source) ?? 0;
    portIndex.set(edge.source, port + 1);
    if (edge.loop) return;
    const chain = [edge.source];
    for (let layer = edge.source.layer + 1; layer < edge.target.layer; layer++)
      // A long edge runs straight: its spacers hold their line and the
      // cards beside them move over (a skipped step sits to one side).
      chain.push(vertex(`via-${number}-${layer}`, layer, 12, 0, 1000, 16));
    chain.push(edge.target);
    chains.set(number, chain);
    for (let k = 0; k + 1 < chain.length; k += 1)
      segments.push({
        edge: number,
        from: chain[k],
        to: chain[k + 1],
        port: k === 0 ? port : 0,
        ports: k === 0 ? portsFrom.get(edge.source)! : 1,
      });
  });

  // Order each layer by where its parents sit (a branch's first answer to
  // the left), then centre each card under its parents as spacing allows.
  const layerCount = Math.max(...vertices.map((item) => item.layer)) + 1;
  const layers: Vertex[][] = Array.from({ length: layerCount }, () => []);
  const seed = (item: Vertex) => {
    const match = /^(?:step|end)-(\d+)$/.exec(item.key);
    return match
      ? preorder[Number(match[1])] + (item.key[0] === 'e' ? 0.5 : 0)
      : -1;
  };
  for (const item of vertices) layers[item.layer].push(item);
  const parents = new Map<Vertex, Segment[]>();
  for (const segment of segments)
    parents.set(segment.to, [...(parents.get(segment.to) ?? []), segment]);
  const portShift = (segment: Segment) =>
    segment.port - (segment.ports - 1) / 2;
  layers.forEach((layer, number) => {
    const centre = new Map<Vertex, number>();
    for (const item of layer) {
      const above = parents.get(item) ?? [];
      centre.set(
        item,
        number === 0 || !above.length
          ? seed(item)
          : above.reduce(
              (sum, segment) =>
                sum + segment.from.order + portShift(segment) * 0.01,
              0,
            ) / above.length,
      );
    }
    layer.sort(
      (a, b) =>
        centre.get(a)! - centre.get(b)! ||
        seed(a) - seed(b) ||
        a.key.localeCompare(b.key),
    );
    layer.forEach((item, order) => {
      item.order = order;
    });
    // Under its parents, leaning towards a long edge's straight line.
    const targets = layer.map((item) => {
      const above = parents.get(item) ?? [];
      const pull = (segment: Segment) => Math.max(segment.from.weight, 0.01);
      return above.length
        ? above.reduce(
            (sum, segment) => sum + segment.from.x * pull(segment),
            0,
          ) / above.reduce((sum, segment) => sum + pull(segment), 0)
        : 0;
    });
    const gaps = layer
      .slice(1)
      .map(
        (item, k) =>
          (layer[k].width + item.width) / 2 + Math.min(layer[k].gap, item.gap),
      );
    placeInOrder(
      targets,
      layer.map((item) => item.weight),
      gaps,
    ).forEach((x, k) => {
      layer[k].x = x;
    });
  });

  const left = Math.min(...vertices.map((item) => item.x - item.width / 2));
  // Whole pixels keep card edges crisp.
  for (const item of vertices) item.x = Math.round(item.x + margin - left);
  const tops: number[] = [];
  let y = margin;
  for (const layer of layers) {
    tops.push(y);
    y += Math.max(0, ...layer.map((item) => item.height)) + rowGap;
  }
  const top = (item: Vertex) => tops[item.layer];
  const bottom = (item: Vertex) => top(item) + item.height;
  const layerBottom = (layer: number) =>
    tops[layer] + Math.max(0, ...layers[layer].map((item) => item.height));
  let right = Math.max(...vertices.map((item) => item.x + item.width / 2));

  // Each edge leaves its card's bottom straight above where it heads (within
  // the card), so a chain is a straight line; a loop leaves near the right.
  const exits = new Map<number, number>();
  const bySource = new Map<Vertex, number[]>();
  planned.forEach((edge, number) => {
    const reach = Math.max(0, edge.source.width / 2 - 36);
    const toward = edge.loop ? null : chains.get(number)![1].x;
    exits.set(
      number,
      edge.source.x +
        (toward === null
          ? edge.source.width / 2 - 10
          : Math.max(-reach, Math.min(reach, toward - edge.source.x))),
    );
    if (!edge.loop)
      bySource.set(edge.source, [...(bySource.get(edge.source) ?? []), number]);
  });
  for (const numbers of bySource.values()) {
    if (numbers.length < 2) continue;
    const [a, b] = [...numbers].sort(
      (one, two) => chains.get(one)![1].x - chains.get(two)![1].x,
    );
    if (exits.get(b)! - exits.get(a)! < 24) {
      const middle = (exits.get(a)! + exits.get(b)!) / 2;
      exits.set(a, middle - 12);
      exits.set(b, middle + 12);
    }
  }
  const edges: FlowEdge[] = [];
  const lanes: { from: number; to: number; x: number }[] = [];
  planned.forEach((edge, number) => {
    const exit = { x: exits.get(number)!, y: bottom(edge.source) };
    if (!edge.loop) {
      const chain = chains.get(number)!;
      const points: FlowPoint[] = [exit];
      for (const spacer of chain.slice(1, -1))
        points.push(
          { x: spacer.x, y: top(spacer) },
          { x: spacer.x, y: layerBottom(spacer.layer) },
        );
      points.push({ x: edge.target.x, y: top(edge.target) });
      edges.push({
        id: edge.id,
        from: edge.from,
        to: edge.to,
        label: edge.label,
        loop: false,
        points,
        labelAt: edge.label
          ? {
              x: (points[0].x + points[1].x) / 2,
              y: (points[0].y + points[1].y) / 2,
            }
          : null,
      });
      return;
    }
    // A loop: down into the gap below its step, out to a lane right of every
    // card it passes, up to the gap above its target, and in from above.
    const span = { from: edge.target.layer, to: edge.source.layer };
    const clear = Math.max(
      ...layers
        .slice(span.from, span.to + 1)
        .flat()
        .map((item) => item.x + item.width / 2),
    );
    let x = clear + options.laneGap + 4;
    for (;;) {
      const taken = lanes.find(
        (lane) =>
          lane.from <= span.to &&
          span.from <= lane.to &&
          Math.abs(lane.x - x) < options.laneGap,
      );
      if (!taken) break;
      x = taken.x + options.laneGap;
    }
    lanes.push({ ...span, x });
    right = Math.max(right, x);
    const depth = lanes.filter(
      (lane) => lane.from <= span.to && span.from <= lane.to,
    ).length;
    const below = layerBottom(edge.source.layer) + rowGap / 2 + (depth - 1) * 6;
    const above = top(edge.target) - rowGap / 2 + 4 - (depth - 1) * 6;
    const entry =
      edge.target.x +
      Math.min(edge.target.width / 2 - 16, 40 + (depth - 1) * 8);
    const points = [
      exit,
      { x: exit.x, y: below },
      { x, y: below },
      { x, y: above },
      { x: entry, y: above },
      { x: entry, y: top(edge.target) },
    ];
    edges.push({
      id: edge.id,
      from: edge.from,
      to: edge.to,
      label: edge.label,
      loop: true,
      points,
      labelAt: edge.label ? { x, y: (below + above) / 2 } : null,
    });
  });

  const box = (item: Vertex): FlowBox => ({
    x: item.x - item.width / 2,
    y: top(item),
    width: item.width,
    height: item.height,
  });
  const stepBoxes: FlowStepBox[] = [];
  const unreachedSteps: number[] = [];
  for (let index = 0; index < count; index += 1) {
    const placed = stepVertex.get(index);
    if (placed) stepBoxes.push({ ...box(placed), index, layer: placed.layer });
    else {
      unreachedSteps.push(index);
      stepBoxes.push({
        x: 0,
        y: 0,
        width: options.nodeWidth,
        height: options.nodeHeight(index),
        index,
        layer: null,
      });
    }
  }
  let height = y - rowGap + margin;
  let unreached: FlowPoint | null = null;
  if (unreachedSteps.length) {
    const columns = Math.max(
      1,
      Math.min(
        options.groupColumns,
        Math.floor(
          (right - margin + columnGap) / (options.nodeWidth + columnGap),
        ),
      ),
    );
    unreached = { x: margin, y: y - rowGap / 2 };
    // Below the heading and its one-line explanation.
    let rowTop = unreached.y + 52;
    for (let k = 0; k < unreachedSteps.length; k += columns) {
      const row = unreachedSteps.slice(k, k + columns);
      row.forEach((index, column) => {
        const item = stepBoxes[index];
        item.x = margin + column * (options.nodeWidth + columnGap);
        item.y = rowTop;
        right = Math.max(right, item.x + item.width);
      });
      rowTop +=
        Math.max(...row.map((index) => stepBoxes[index].height)) +
        columnGap / 2;
    }
    height = rowTop - columnGap / 2 + margin;
  }

  return {
    width: Math.ceil(right + margin + 24),
    height: Math.ceil(height),
    steps: stepBoxes,
    terminals: vertices
      .filter((item) => item.key === 'start' || item.key.startsWith('end-'))
      .map((item) => ({
        ...box(item),
        id: item.key,
        kind: item.key === 'start' ? 'start' : 'end',
      })),
    edges,
    unreached,
  };
}

/**
 * An edge as an SVG path: smooth vertical curves between layers, straight
 * runs through spacers, and rounded corners on a loop's lane. The last few
 * pixels are left for the arrowhead.
 */
export function edgePath(edge: FlowEdge, arrow = 6): string {
  const points = edge.points.map((point, k) =>
    k === edge.points.length - 1 ? { ...point, y: point.y - arrow } : point,
  );
  const [first, ...rest] = points;
  let path = `M${first.x} ${first.y}`;
  if (edge.loop) {
    for (let k = 1; k < points.length; k += 1) {
      const here = points[k];
      const next = points[k + 1];
      if (!next) {
        path += ` L${here.x} ${here.y}`;
        continue;
      }
      const before = points[k - 1];
      const radius = Math.min(
        8,
        Math.hypot(here.x - before.x, here.y - before.y) / 2,
        Math.hypot(next.x - here.x, next.y - here.y) / 2,
      );
      const towards = (from: FlowPoint, to: FlowPoint) => {
        const length = Math.hypot(to.x - from.x, to.y - from.y) || 1;
        return {
          x: here.x + ((to.x - from.x) / length) * radius,
          y: here.y + ((to.y - from.y) / length) * radius,
        };
      };
      const enter = towards(here, before);
      const leave = towards(here, next);
      path += ` L${enter.x} ${enter.y} Q${here.x} ${here.y} ${leave.x} ${leave.y}`;
    }
    return path;
  }
  let previous = first;
  for (const point of rest) {
    if (point.x === previous.x) path += ` L${point.x} ${point.y}`;
    else {
      const middle = (previous.y + point.y) / 2;
      path += ` C${previous.x} ${middle} ${point.x} ${middle} ${point.x} ${point.y}`;
    }
    previous = point;
  }
  return path;
}

/** The small downward arrowhead every edge ends with. */
export function arrowPath(edge: FlowEdge, size = 6): string {
  const end = edge.points[edge.points.length - 1];
  return `M${end.x - size * 0.75} ${end.y - size} L${end.x + size * 0.75} ${end.y - size} L${end.x} ${end.y} Z`;
}
