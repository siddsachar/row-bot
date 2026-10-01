import { createRef } from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import type Graph from 'graphology';
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type Mock,
} from 'vitest';
import KnowledgeGraphCanvas, {
  nodeSize,
  webglAvailable,
  type KnowledgeGraphHandle,
} from './KnowledgeGraphCanvas';
import type { KnowledgeGraphEdge, KnowledgeGraphNode } from './KnowledgeHome';
import {
  settle,
  type LayoutReply,
  type LayoutRequest,
} from './knowledge-layout';
import { glAlpha, mix } from './knowledge-palette';

type Attributes = Record<string, unknown>;
type Reducer = (key: string, data: Attributes) => Attributes;
type BBox = { x: [number, number]; y: [number, number] };
type FakeRenderer = {
  graph: Graph;
  container: HTMLElement;
  settings: { nodeReducer: Reducer; edgeReducer: Reducer } & Attributes;
  /** The frame the canvas set, as sigma keeps it; null draws the graph's own box. */
  customBBox: BBox | null;
  frames: (BBox | null)[];
  camera: {
    state: { x: number; y: number; ratio: number; angle: number };
    animate: Mock;
    setState: Mock;
    getState: Mock;
    animatedReset: Mock;
  };
  kill: Mock;
  refresh: Mock;
  resize: Mock;
  setSetting: Mock;
  emit(event: string, payload?: { node?: string }): void;
  getNodeDisplayData(
    key: string,
  ): { x: number; y: number; size: number } | undefined;
};

// A stand-in for sigma's WebGL renderer: it records what the canvas asks of
// it and never touches a GPU context. Like sigma, it places memories in the
// canvas's frame when one is set, else in the box around the graph.
const renderer = vi.hoisted(() => ({
  instances: [] as FakeRenderer[],
  fail: false,
}));

vi.mock('sigma', () => ({
  default: class FakeSigma {
    handlers = new Map<string, (payload: { node?: string }) => void>();
    camera: FakeRenderer['camera'];
    kill = vi.fn();
    refresh = vi.fn();
    resize = vi.fn();
    setSetting = vi.fn();
    customBBox: BBox | null = null;
    frames: (BBox | null)[] = [];

    constructor(
      readonly graph: Graph,
      readonly container: HTMLElement,
      readonly settings: FakeRenderer['settings'],
    ) {
      if (renderer.fail) throw new Error('The WebGL context was lost.');
      const state = { x: 0.5, y: 0.5, ratio: 1, angle: 0 };
      const update = (next: Partial<typeof state>) => {
        Object.assign(state, next);
      };
      this.camera = {
        state,
        animate: vi.fn(async (next: Partial<typeof state>) => update(next)),
        setState: vi.fn(update),
        getState: vi.fn(() => ({ ...state })),
        animatedReset: vi.fn(async () => update({ x: 0.5, y: 0.5, ratio: 1 })),
      };
      renderer.instances.push(this as unknown as FakeRenderer);
    }

    on(event: string, handler: (payload: { node?: string }) => void) {
      this.handlers.set(event, handler);
    }

    emit(event: string, payload: { node?: string } = {}) {
      this.handlers.get(event)?.(payload);
    }

    getCamera() {
      return this.camera;
    }

    setCustomBBox(box: BBox | null) {
      this.customBBox = box;
      this.frames.push(box);
    }

    getNodeDisplayData(key: string) {
      if (!this.graph.hasNode(key)) return undefined;
      const { x, y, size } = this.graph.getNodeAttributes(key) as {
        x: number;
        y: number;
        size: number;
      };
      const box = this.customBBox ?? extentOf(this.graph);
      const scale = Math.max(box.x[1] - box.x[0], box.y[1] - box.y[0]) || 1;
      return {
        x: 0.5 + (x - (box.x[0] + box.x[1]) / 2) / scale,
        y: 0.5 + (y - (box.y[0] + box.y[1]) / 2) / scale,
        size,
      };
    }
  },
}));

/** The box around the graph's positions, the way sigma frames it. */
function extentOf(graph: Graph): BBox {
  const xs = graph.mapNodes((_key, attributes) => attributes.x as number);
  const ys = graph.mapNodes((_key, attributes) => attributes.y as number);
  return {
    x: [Math.min(...xs), Math.max(...xs)],
    y: [Math.min(...ys), Math.max(...ys)],
  };
}

// A stand-in for the layout worker: it keeps what the canvas asks for and
// answers only when a test says so, running the real ForceAtlas2 iterations
// (at most `limit` of them, to stand in for a batch whose time ran out).
let workers: FakeWorker[] = [];

class FakeWorker {
  onmessage: ((event: MessageEvent<LayoutReply>) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  requests: LayoutRequest[] = [];
  terminated = false;
  private edges = new Float32Array(0);

  constructor(
    readonly url: URL | string,
    readonly options?: WorkerOptions,
  ) {
    workers.push(this);
  }

  postMessage(request: LayoutRequest) {
    this.requests.push(request);
  }

  terminate() {
    this.terminated = true;
  }

  answer(limit = Infinity) {
    const request = this.requests[this.requests.length - 1];
    if (request.edges) this.edges = new Float32Array(request.edges);
    const result = settle(
      request.settings,
      new Float32Array(request.nodes),
      this.edges,
      Math.min(limit, request.iterations),
    );
    const data: LayoutReply = { nodes: request.nodes, ...result };
    act(() => this.onmessage?.(new MessageEvent('message', { data })));
  }

  fail() {
    act(() => this.onerror?.(new Event('error')));
  }
}

const revision = 'c'.repeat(64);

function node(
  id: string,
  overrides: Partial<KnowledgeGraphNode> = {},
): KnowledgeGraphNode {
  return {
    id,
    revision,
    subject: id,
    description: '',
    entity_type: 'fact',
    source: 'manual',
    updated_at: '2026-09-01T10:00:00Z',
    relation_count: 0,
    orphan: true,
    is_user: false,
    status: 'active',
    tier: 'semantic',
    ...overrides,
  };
}

const nodes: KnowledgeGraphNode[] = [
  node('user', {
    subject: 'User',
    entity_type: 'person',
    relation_count: 1,
    orphan: false,
    is_user: true,
  }),
  node('alpha', { subject: 'Alpha project', relation_count: 1, orphan: false }),
  node('quiet', { subject: 'Quiet preference', entity_type: 'preference' }),
];

const edges: KnowledgeGraphEdge[] = [
  {
    id: 'user-alpha',
    source_id: 'user',
    target_id: 'alpha',
    relation_type: 'works_on',
    updated_at: '2026-09-19T10:00:00Z',
  },
];

const everyone = new Set(nodes.map((item) => item.id));

/** A small graph of its own: where memories came to rest is kept by id. */
function fresh(prefix: string) {
  const [hub, ...spokes] = ['hub', 'a', 'b', 'c'].map(
    (key) => `${prefix}-${key}`,
  );
  return {
    nodes: [
      node(hub, { relation_count: 3, orphan: false, is_user: true }),
      ...spokes.map((id) => node(id, { relation_count: 1, orphan: false })),
    ],
    edges: spokes.map((id) => ({
      ...edges[0],
      id: `${hub}-${id}`,
      source_id: hub,
      target_id: id,
    })),
    visible: new Set([hub, ...spokes]),
  };
}

/** A graph shaped like a real profile: a user hub, chains of linked
 * memories and some with no links at all. */
function crowd(prefix: string, count: number) {
  const ids = Array.from(
    { length: count },
    (_value, index) => `${prefix}-${index}`,
  );
  const links: KnowledgeGraphEdge[] = [];
  for (let index = 1; index < count; index += 1) {
    if (index % 7 === 0) continue;
    const other = index % 4 === 1 ? 0 : Math.floor(index / 2);
    links.push({
      ...edges[0],
      id: `${ids[other]}-${ids[index]}`,
      source_id: ids[other],
      target_id: ids[index],
    });
  }
  const linked = (id: string) =>
    links.filter((link) => link.source_id === id || link.target_id === id)
      .length;
  return {
    nodes: ids.map((id, index) =>
      node(id, {
        relation_count: linked(id),
        orphan: linked(id) === 0,
        is_user: index === 0,
      }),
    ),
    edges: links,
    visible: new Set(ids),
  };
}

const places = (graph: Graph) =>
  graph.mapNodes((key, attributes) => [key, attributes.x, attributes.y]);

/** The x, y pairs of a ForceAtlas2 node matrix (ten values a memory). */
const pairs = (matrix: ArrayBuffer | Float32Array) =>
  Array.from(new Float32Array(matrix)).filter(
    (_value, index) => index % 10 < 2,
  );

const coordinates = (graph: Graph) =>
  graph.mapNodes((_key, attributes) => [attributes.x, attributes.y]).flat();

const FRAME_MS = 1000 / 60;

// Frames run only when a test says so, each a sixtieth of a second later.
type Frames = ReturnType<typeof stubFrames>;

function stubFrames() {
  const due = new Map<number, FrameRequestCallback>();
  let handle = 0;
  let now = 1000;
  vi.stubGlobal('requestAnimationFrame', (next: FrameRequestCallback) => {
    handle += 1;
    due.set(handle, next);
    return handle;
  });
  vi.stubGlobal('cancelAnimationFrame', (cancelled: number) =>
    due.delete(cancelled),
  );
  return {
    get pending() {
      return due.size;
    },
    get elapsed() {
      return now - 1000;
    },
    run(count = 1, each?: () => void) {
      for (let frame = 0; frame < count; frame += 1) {
        now += FRAME_MS;
        const callbacks = [...due.values()];
        due.clear();
        act(() => callbacks.forEach((callback) => callback(now)));
        each?.();
      }
    },
  };
}

function stubWebgl(kind: 'webgl2' | 'webgl' | 'none' | 'throws' = 'webgl2') {
  vi.stubGlobal('WebGLRenderingContext', class {});
  return vi
    .spyOn(HTMLCanvasElement.prototype, 'getContext')
    .mockImplementation(((requested: string) => {
      if (kind === 'throws') throw new Error('Blocked by policy');
      return requested === kind ? ({} as RenderingContext) : null;
    }) as never);
}

afterEach(() => {
  vi.unstubAllGlobals();
  renderer.instances = [];
  renderer.fail = false;
  workers = [];
});

describe('webglAvailable', () => {
  it('is false in jsdom, which has no WebGL', () => {
    const getContext = vi.spyOn(HTMLCanvasElement.prototype, 'getContext');
    expect(window.WebGLRenderingContext).toBeUndefined();
    expect(webglAvailable()).toBe(false);
    expect(getContext).not.toHaveBeenCalled();
  });

  it('is true when a WebGL2 or WebGL context can be created', () => {
    stubWebgl('webgl2');
    expect(webglAvailable()).toBe(true);
    vi.restoreAllMocks();
    stubWebgl('webgl');
    expect(webglAvailable()).toBe(true);
  });

  it('is false when the host refuses or blocks a context', () => {
    stubWebgl('none');
    expect(webglAvailable()).toBe(false);
    vi.restoreAllMocks();
    stubWebgl('throws');
    expect(webglAvailable()).toBe(false);
  });
});

describe('nodeSize', () => {
  it('grows with the square root of the link count', () => {
    expect(nodeSize(node('a'))).toBeCloseTo(2.6);
    expect(nodeSize(node('a', { relation_count: 4 }))).toBeCloseTo(6.4);
    expect(nodeSize(node('a', { relation_count: 9 }))).toBeCloseTo(8.3);
  });

  it('treats a negative count as no links and caps large hubs', () => {
    expect(nodeSize(node('a', { relation_count: -5 }))).toBeCloseTo(2.6);
    expect(nodeSize(node('a', { relation_count: 100 }))).toBe(16);
    expect(nodeSize(node('a', { relation_count: 10_000 }))).toBe(16);
  });

  it('keeps the user hub prominent but within the same cap', () => {
    expect(nodeSize(node('u', { is_user: true }))).toBe(11);
    expect(nodeSize(node('u', { is_user: true, relation_count: 25 }))).toBe(
      12.1,
    );
    expect(nodeSize(node('u', { is_user: true, relation_count: 400 }))).toBe(
      16,
    );
  });
});

describe('KnowledgeGraphCanvas without WebGL', () => {
  it('reports failure so Knowledge can fall back, and never loads the renderer', () => {
    const onStatus = vi.fn();
    const ref = createRef<KnowledgeGraphHandle>();
    const { container } = render(
      <KnowledgeGraphCanvas
        ref={ref}
        nodes={nodes}
        edges={edges}
        visible={everyone}
        selectedId={null}
        onSelect={vi.fn()}
        onStatus={onStatus}
      />,
    );
    expect(onStatus).toHaveBeenLastCalledWith('failed');
    expect(container.querySelector('.knowledge-network-shell')).toHaveAttribute(
      'data-renderer-status',
      'failed',
    );
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
    expect(renderer.instances).toHaveLength(0);
    // The navigation handle stays safe to call without a renderer.
    expect(() => {
      ref.current?.fit();
      ref.current?.zoom(0.25);
      ref.current?.focus('alpha');
    }).not.toThrow();
  });
});

describe('KnowledgeGraphCanvas with a renderer', () => {
  let resizeObservers: { callback: ResizeObserverCallback }[];
  let frames: Frames;

  beforeEach(() => {
    stubWebgl();
    frames = stubFrames();
    vi.stubGlobal('Worker', FakeWorker);
    resizeObservers = [];
    vi.stubGlobal(
      'ResizeObserver',
      class {
        constructor(readonly callback: ResizeObserverCallback) {
          resizeObservers.push(this);
        }
        observe() {}
        disconnect() {}
      },
    );
  });

  async function renderCanvas(
    overrides: Partial<React.ComponentProps<typeof KnowledgeGraphCanvas>> = {},
  ) {
    const ref = createRef<KnowledgeGraphHandle>();
    const props = {
      nodes,
      edges,
      visible: everyone,
      selectedId: null,
      onSelect: vi.fn(),
      onStatus: vi.fn(),
      ...overrides,
    };
    const view = render(<KnowledgeGraphCanvas ref={ref} {...props} />);
    await waitFor(
      () =>
        expect(
          view.container.querySelector('.knowledge-network-shell'),
        ).toHaveAttribute('data-renderer-status', 'ready'),
      { timeout: 5000 },
    );
    return { ...view, ref, props, sigma: renderer.instances[0] };
  }

  it('shows a loading status, then an accessible graph of valid links only', async () => {
    const onStatus = vi.fn();
    const { container } = render(
      <KnowledgeGraphCanvas
        nodes={nodes}
        edges={[
          ...edges,
          {
            ...edges[0],
            id: 'self',
            source_id: 'alpha',
            target_id: 'alpha',
          },
          { ...edges[0], id: 'dangling', target_id: 'deleted' },
        ]}
        visible={everyone}
        selectedId={null}
        onSelect={vi.fn()}
        onStatus={onStatus}
      />,
    );
    expect(screen.getByRole('status')).toHaveTextContent('Drawing the graph…');
    await waitFor(
      () =>
        expect(
          container.querySelector('.knowledge-network-shell'),
        ).toHaveAttribute('data-renderer-status', 'ready'),
      { timeout: 5000 },
    );
    expect(onStatus.mock.calls.map(([status]) => status)).toEqual([
      'loading',
      'ready',
    ]);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(
      screen.getByRole('img', {
        name: /^Interactive knowledge graph with 3 memories/,
      }),
    ).toBeInTheDocument();
    expect(renderer.instances).toHaveLength(1);
    const { graph } = renderer.instances[0];
    expect(graph.order).toBe(3);
    expect(graph.edges()).toEqual(['user-alpha']);
    expect(graph.getNodeAttributes('user')).toMatchObject({
      label: 'User',
      size: 11,
      user: true,
      slot: 4,
    });
    expect(graph.getNodeAttribute('alpha', 'size')).toBeCloseTo(4.5);
  });

  it('hides filtered memories in place and highlights the chosen neighbourhood', async () => {
    const { sigma, rerender, props } = await renderCanvas();
    const { graph, settings } = sigma;
    const place = () =>
      graph.mapNodes((key, attributes) => [key, attributes.x, attributes.y]);
    const before = place();
    const data = (key: string) => graph.getNodeAttributes(key);
    const theme = { fact: '#3987E5', canvas: '#0B0E13', accent: '#78B8F2' };

    // Nothing chosen: every visible memory keeps its colour; the user is labelled.
    expect(settings.nodeReducer('user', data('user'))).toMatchObject({
      forceLabel: true,
    });
    expect(settings.nodeReducer('alpha', data('alpha'))).toMatchObject({
      color: theme.fact,
      forceLabel: false,
    });

    // Hidden by a filter: the memory and its links disappear but stay put.
    const withoutUser = new Set(['alpha', 'quiet']);
    rerender(
      <KnowledgeGraphCanvas
        {...props}
        visible={withoutUser}
        selectedId={null}
      />,
    );
    expect(renderer.instances).toHaveLength(1);
    expect(sigma.refresh).toHaveBeenCalled();
    expect(settings.nodeReducer('user', data('user'))).toMatchObject({
      hidden: true,
    });
    expect(
      settings.edgeReducer('user-alpha', graph.getEdgeAttributes('user-alpha')),
    ).toMatchObject({ hidden: true });
    expect(place()).toEqual(before);

    // A selection brings its neighbours forward and dims the rest.
    rerender(<KnowledgeGraphCanvas {...props} selectedId="alpha" />);
    expect(settings.nodeReducer('alpha', data('alpha'))).toMatchObject({
      highlighted: true,
      forceLabel: true,
      zIndex: 3,
    });
    expect(settings.nodeReducer('user', data('user'))).toMatchObject({
      forceLabel: true,
      zIndex: 2,
    });
    expect(settings.nodeReducer('quiet', data('quiet'))).toMatchObject({
      label: '',
      zIndex: 0,
      color: mix(theme.fact, theme.canvas, 0.15),
    });
    expect(
      settings.edgeReducer('user-alpha', graph.getEdgeAttributes('user-alpha')),
    ).toMatchObject({ color: glAlpha(theme.accent, 0.6), zIndex: 1 });
    expect(place()).toEqual(before);
  });

  it('names the memories and connections that pass the filters, in the singular for one', async () => {
    const link = (source_id: string, target_id: string) => ({
      ...edges[0],
      id: `${source_id}-${target_id}`,
      source_id,
      target_id,
    });
    const linked = [...edges, link('alpha', 'quiet'), link('user', 'quiet')];
    const { rerender, props } = await renderCanvas({ edges: linked });
    const label = () => screen.getByRole('img').getAttribute('aria-label');
    expect(label()).toBe(
      'Interactive knowledge graph with 3 memories and 3 connections',
    );

    // A connection counts only while both of its memories are shown.
    rerender(
      <KnowledgeGraphCanvas {...props} visible={new Set(['alpha', 'quiet'])} />,
    );
    expect(label()).toBe(
      'Interactive knowledge graph with 2 memories and 1 connection',
    );
    rerender(<KnowledgeGraphCanvas {...props} visible={new Set(['alpha'])} />);
    expect(label()).toBe(
      'Interactive knowledge graph with 1 memory and 0 connections',
    );

    // Fewer links rebuild the graph; the name is exposed once it is drawn.
    rerender(<KnowledgeGraphCanvas {...props} edges={edges} />);
    expect(
      await screen.findByRole('img', {
        name: 'Interactive knowledge graph with 3 memories and 1 connection',
      }),
    ).toBeInTheDocument();
  });

  const layoutOf = (container: HTMLElement) =>
    container
      .querySelector('.knowledge-network-shell')
      ?.getAttribute('data-layout');

  /** Runs frames until the memories are at rest and the frame loop stops;
   * returns how long after the first frame the memories came to rest. */
  function runToRest(container: HTMLElement, each?: () => void) {
    let restedAt = layoutOf(container) === 'settled' ? frames.elapsed : NaN;
    for (
      let frame = 0;
      frame < 1200 && (layoutOf(container) !== 'settled' || frames.pending);
      frame += 1
    )
      frames.run(1, () => {
        each?.();
        if (Number.isNaN(restedAt) && layoutOf(container) === 'settled')
          restedAt = frames.elapsed;
      });
    return restedAt;
  }

  /** Answers every layout batch, then lets the graph come to rest. */
  function settleAll(container: HTMLElement) {
    const worker = workers[workers.length - 1];
    while (!worker.terminated) worker.answer();
    return runToRest(container);
  }

  const width = (box: BBox | null) =>
    box ? Math.max(box.x[1] - box.x[0], box.y[1] - box.y[0]) : NaN;

  describe('settling', () => {
    it('draws the memories bunched up at once, then blooms them out smoothly as the layout streams in', async () => {
      const { container, sigma } = await renderCanvas(crowd('settles', 120));
      expect(layoutOf(container)).toBe('settling');
      expect(workers).toHaveLength(1);
      const [worker] = workers;
      // A module worker from this origin: the CSP refuses blob: workers.
      expect(worker.options).toEqual({ type: 'module' });
      expect(new URL(String(worker.url)).protocol).not.toBe('blob:');
      const [first] = worker.requests;
      expect(first.edges?.byteLength).toBeGreaterThan(0);
      // The whole layout is asked for, in short batches.
      expect(first.iterations).toBe(220);
      expect(first.budget).toBeGreaterThan(0);
      expect(first.budget).toBeLessThan(50);
      // The same layout run in one go, to compare the end with.
      const reference = new Float32Array(first.nodes.slice(0));
      settle(
        first.settings,
        reference,
        new Float32Array(first.edges!.slice(0)),
        220,
      );

      // Drawn at once a quarter of the way out, framed by where the layout
      // starts, so the graph blooms out from the middle.
      const start = pairs(first.nodes);
      const drawn = coordinates(sigma.graph);
      drawn.forEach((value, index) =>
        expect(value).toBeCloseTo(start[index] / 4, 4),
      );
      const frame = sigma.customBBox!;
      expect(frame.x[0]).toBeCloseTo(
        Math.min(...start.filter((_value, index) => index % 2 === 0)),
        4,
      );
      expect(frame.y[1]).toBeCloseTo(
        Math.max(...start.filter((_value, index) => index % 2 === 1)),
        4,
      );

      // A batch whose time ran out asks for the rest at once, and moves
      // nothing by itself: frames ease the memories toward it.
      worker.answer(5);
      expect(worker.requests).toHaveLength(2);
      expect(worker.requests[1].iterations).toBe(215);
      expect(worker.requests[1].edges).toBeUndefined();
      expect(coordinates(sigma.graph)).toEqual(drawn);

      let previous = drawn;
      const steps: number[] = [];
      const step = () => {
        const now = coordinates(sigma.graph);
        steps.push(
          Math.max(
            ...now.map((value, index) => Math.abs(value - previous[index])),
          ),
        );
        previous = now;
      };
      frames.run(30, step);
      expect(layoutOf(container)).toBe('settling');
      while (!worker.terminated) worker.answer();
      const restedAfter = runToRest(container, step);

      // No frame jumps: each moves a memory under 3% of the picture, starting
      // gently and slowing down at the end.
      const extent = width(frame);
      expect(Math.max(...steps)).toBeLessThan(extent * 0.03);
      expect(steps[0]).toBeLessThan(Math.max(...steps) / 5);
      const moving = steps.filter((value) => value > 0);
      expect(moving[moving.length - 1]).toBeLessThan(extent / 1000);
      // At rest a few seconds after it was drawn...
      expect(restedAfter).toBeGreaterThan(3000);
      expect(restedAfter).toBeLessThan(6000);
      // ...exactly where one uninterrupted run puts the memories; then the
      // frame loop stops and sigma frames the graph itself again.
      expect(coordinates(sigma.graph)).toEqual(pairs(reference));
      expect(frames.pending).toBe(0);
      expect(sigma.customBBox).toBeNull();
      expect(renderer.instances).toHaveLength(1);
    });

    it.each([
      ['drag', (sigma: FakeRenderer) => sigma.emit('downStage')],
      [
        'press',
        (sigma: FakeRenderer) => sigma.emit('downNode', { node: 'press-a' }),
      ],
      ['wheel', (sigma: FakeRenderer) => sigma.emit('wheelStage')],
      [
        'zoom',
        (_sigma: FakeRenderer, handle: KnowledgeGraphHandle | null) =>
          handle?.zoom(0.25),
      ],
    ])(
      'a %s holds the frame still while the memories keep settling',
      async (name, interrupt) => {
        const { container, sigma, ref } = await renderCanvas(fresh(name));
        const [worker] = workers;
        worker.answer(5);
        frames.run(10);

        act(() => interrupt(sigma, ref.current));
        const held = sigma.customBBox;
        const framed = sigma.frames.length;
        const before = coordinates(sigma.graph);
        settleAll(container);

        expect(layoutOf(container)).toBe('settled');
        expect(coordinates(sigma.graph)).not.toEqual(before);
        expect(coordinates(sigma.graph)).toEqual(
          pairs(worker.requests[worker.requests.length - 1].nodes),
        );
        // The view never shifted under the user, and stays put at rest.
        expect(sigma.frames.length).toBe(framed);
        expect(sigma.customBBox).toBe(held);
        expect(frames.pending).toBe(0);
      },
    );

    it('Fit lets a held frame take in the whole graph, then hands it back', async () => {
      const { container, sigma, ref } = await renderCanvas(fresh('fit'));
      act(() => sigma.emit('wheelStage'));
      settleAll(container);
      const held = sigma.customBBox;
      expect(held).not.toBeNull();

      act(() => ref.current?.fit());
      expect(sigma.camera.animatedReset).toHaveBeenCalledWith({
        duration: 320,
      });
      expect(frames.pending).toBe(1);
      runToRest(container);
      expect(sigma.customBBox).toBeNull();
      // The frame glided from the held box to the graph's own box.
      const glided = sigma.frames.slice(sigma.frames.indexOf(held) + 1, -1);
      expect(glided.length).toBeGreaterThan(30);
      const own = extentOf(sigma.graph);
      const last = glided[glided.length - 1]!;
      for (const [edge, value] of [
        [last.x[0], own.x[0]],
        [last.x[1], own.x[1]],
        [last.y[0], own.y[0]],
        [last.y[1], own.y[1]],
      ])
        expect(Math.abs(edge - value)).toBeLessThan(width(last) / 500);
    });

    it('a search pick while the graph settles heads where the memory comes to rest', async () => {
      const { container, sigma, ref } = await renderCanvas(fresh('pick'));
      const [worker] = workers;
      while (!worker.terminated) worker.answer();
      frames.run(20);

      act(() => ref.current?.focus('pick-a'));
      const [aim] = sigma.camera.animate.mock.lastCall!;
      runToRest(container);
      const rested = sigma.getNodeDisplayData('pick-a')!;
      expect(aim.x).toBeCloseTo(rested.x, 6);
      expect(aim.y).toBeCloseTo(rested.y, 6);
      expect(aim.ratio).toBe(0.45);
    });

    it('lays out in one pass before drawing when motion is reduced', async () => {
      vi.stubGlobal('matchMedia', (query: string) => ({
        matches: query.includes('reduce'),
        media: query,
      }));
      const graph = fresh('reduced');
      const { container } = render(
        <KnowledgeGraphCanvas
          {...graph}
          selectedId={null}
          onSelect={vi.fn()}
        />,
      );
      await waitFor(() => expect(workers).toHaveLength(1));
      const [worker] = workers;
      expect(worker.requests).toHaveLength(1);
      expect(worker.requests[0]).toMatchObject({ iterations: 220 });
      expect(worker.requests[0].budget).toBeUndefined();
      expect(renderer.instances).toHaveLength(0);
      expect(screen.getByRole('status')).toHaveTextContent(
        'Drawing the graph…',
      );

      worker.answer();
      await waitFor(() => expect(renderer.instances).toHaveLength(1));
      const [sigma] = renderer.instances;
      // Drawn where the layout ends, with nothing left to animate.
      expect(coordinates(sigma.graph)).toEqual(pairs(worker.requests[0].nodes));
      expect(worker.terminated).toBe(true);
      expect(layoutOf(container)).toBe('settled');
      expect(worker.requests).toHaveLength(1);
      expect(frames.pending).toBe(0);
      expect(sigma.frames).toEqual([]);
    });

    it('draws the start when the layout worker cannot run', async () => {
      vi.stubGlobal('matchMedia', (query: string) => ({
        matches: query.includes('reduce'),
        media: query,
      }));
      const graph = fresh('broken');
      const view = (
        <KnowledgeGraphCanvas {...graph} selectedId={null} onSelect={vi.fn()} />
      );
      const { container, unmount } = render(view);
      await waitFor(() => expect(workers).toHaveLength(1));
      workers[0].fail();
      await waitFor(() => expect(renderer.instances).toHaveLength(1));
      expect(layoutOf(container)).toBe('settled');
      expect(workers[0].terminated).toBe(true);

      // The unsettled start is not kept: the next visit lays out again.
      unmount();
      render(view);
      await waitFor(() => expect(workers).toHaveLength(2));
    });

    it('blooms out to where the memories started when the worker fails, keeping nothing', async () => {
      const graph = fresh('fails');
      const first = await renderCanvas(graph);
      const [worker] = workers;
      const start = pairs(worker.requests[0].nodes);
      worker.fail();
      expect(worker.terminated).toBe(true);
      runToRest(first.container);
      expect(layoutOf(first.container)).toBe('settled');
      expect(coordinates(first.sigma.graph)).toEqual(start);
      expect(frames.pending).toBe(0);

      first.unmount();
      await renderCanvas(graph);
      expect(workers).toHaveLength(2);
    });

    it('leaving mid-settle stops the frames and the worker', async () => {
      const { unmount } = await renderCanvas(fresh('leave'));
      const [worker] = workers;
      worker.answer(5);
      frames.run(5);
      expect(frames.pending).toBe(1);
      unmount();
      expect(frames.pending).toBe(0);
      expect(worker.terminated).toBe(true);
    });

    it('Show all: placed memories settle on from their places while new ones bloom from the middle', async () => {
      const graph = fresh('more');
      const first = await renderCanvas(graph);
      settleAll(first.container);
      const rested = places(first.sigma.graph);

      first.rerender(
        <KnowledgeGraphCanvas
          ref={first.ref}
          {...first.props}
          nodes={[...graph.nodes, node('more-new')]}
          visible={new Set([...graph.visible, 'more-new'])}
        />,
      );
      await waitFor(() => expect(renderer.instances).toHaveLength(2));
      const second = renderer.instances[1];
      expect(workers).toHaveLength(2);
      expect(layoutOf(first.container)).toBe('settling');
      // The layout starts from where the memories rested...
      const start = pairs(workers[1].requests[0].nodes);
      expect(start.slice(0, 8)).toEqual(rested.flatMap(([, x, y]) => [x, y]));
      // ...they are drawn there, and only the new memory starts bunched up.
      expect(places(second.graph).slice(0, 4)).toEqual(rested);
      const [, x, y] = places(second.graph)[4] as [string, number, number];
      expect(x).toBeCloseTo(start[8] / 4, 4);
      expect(y).toBeCloseTo(start[9] / 4, 4);

      settleAll(first.container);
      expect(coordinates(second.graph)).toEqual(
        pairs(workers[1].requests[workers[1].requests.length - 1].nodes),
      );
    });

    it('shows where the memories came to rest when the page comes back', async () => {
      const graph = fresh('return');
      const first = await renderCanvas(graph);
      settleAll(first.container);
      const rested = places(first.sigma.graph);
      first.unmount();
      renderer.instances = [];

      const second = await renderCanvas({
        ...graph,
        nodes: graph.nodes.map((item) => ({ ...item })),
      });
      expect(workers).toHaveLength(1);
      expect(places(second.sigma.graph)).toEqual(rested);
      expect(layoutOf(second.container)).toBe('settled');
      expect(frames.pending).toBe(0);
    });
  });

  it('turns renderer clicks and hovers into selection and pointer feedback', async () => {
    const onSelect = vi.fn();
    const { sigma } = await renderCanvas({ onSelect });
    sigma.emit('clickNode', { node: 'alpha' });
    expect(onSelect).toHaveBeenLastCalledWith('alpha');
    sigma.emit('clickStage');
    expect(onSelect).toHaveBeenLastCalledWith(null);
    sigma.emit('enterNode', { node: 'quiet' });
    expect(sigma.container.style.cursor).toBe('pointer');
    sigma.emit('leaveNode');
    expect(sigma.container.style.cursor).toBe('');
  });

  it('keeps navigation bounded and skips animation when motion is reduced', async () => {
    const { ref, sigma, container } = await renderCanvas();
    settleAll(container);
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query.includes('reduce'),
      media: query,
    }));
    const { camera } = sigma;

    ref.current?.zoom(0.25);
    expect(camera.setState).toHaveBeenLastCalledWith({
      ratio: expect.closeTo(1 / 1.625, 5),
    });
    for (let step = 0; step < 20; step += 1) ref.current?.zoom(0.25);
    expect(camera.state.ratio).toBe(0.06);
    for (let step = 0; step < 20; step += 1) ref.current?.zoom(-0.2);
    expect(camera.state.ratio).toBe(4);

    ref.current?.fit();
    expect(camera.setState).toHaveBeenLastCalledWith({
      x: 0.5,
      y: 0.5,
      ratio: 1,
    });

    const target = sigma.getNodeDisplayData('alpha');
    expect(target).toBeDefined();
    ref.current?.focus('alpha');
    expect(camera.setState).toHaveBeenLastCalledWith({
      x: target?.x,
      y: target?.y,
      ratio: 0.45,
    });
    camera.setState.mockClear();
    ref.current?.focus('deleted');
    expect(camera.setState).not.toHaveBeenCalled();
    expect(camera.animate).not.toHaveBeenCalled();
    expect(camera.animatedReset).not.toHaveBeenCalled();
  });

  it('eases navigation when motion is allowed', async () => {
    const { ref, sigma } = await renderCanvas();
    const { camera } = sigma;
    ref.current?.zoom(-0.2);
    expect(camera.animate).toHaveBeenLastCalledWith(
      { ratio: 2 },
      { duration: 420, easing: 'quadraticInOut' },
    );
    ref.current?.fit();
    expect(camera.animatedReset).toHaveBeenCalledWith({ duration: 320 });
    expect(camera.setState).not.toHaveBeenCalled();
  });

  it('resizes the renderer only when its box actually changes', async () => {
    const { sigma } = await renderCanvas();
    const [observer] = resizeObservers;
    const notify = () =>
      observer.callback([], observer as unknown as ResizeObserver);
    sigma.refresh.mockClear();
    notify();
    expect(sigma.resize).not.toHaveBeenCalled();

    Object.defineProperty(sigma.container, 'clientWidth', {
      configurable: true,
      value: 640,
    });
    Object.defineProperty(sigma.container, 'clientHeight', {
      configurable: true,
      value: 420,
    });
    notify();
    notify();
    expect(sigma.resize).toHaveBeenCalledOnce();
    expect(sigma.refresh).toHaveBeenCalledOnce();
  });

  it('repaints labels and links when the theme changes', async () => {
    const { sigma } = await renderCanvas();
    document.documentElement.setAttribute('data-theme', 'light');
    try {
      await waitFor(() =>
        expect(sigma.setSetting).toHaveBeenCalledWith('labelColor', {
          color: expect.any(String),
        }),
      );
      expect(sigma.setSetting).toHaveBeenCalledWith(
        'defaultEdgeColor',
        expect.stringMatching(/^rgba\(/),
      );
    } finally {
      document.documentElement.removeAttribute('data-theme');
    }
  });

  it('rebuilds only when the memories themselves change', async () => {
    const { sigma, rerender, props } = await renderCanvas();
    rerender(<KnowledgeGraphCanvas {...props} selectedId="quiet" />);
    expect(renderer.instances).toHaveLength(1);
    rerender(
      <KnowledgeGraphCanvas
        {...props}
        nodes={[...nodes, node('new', { subject: 'New memory' })]}
      />,
    );
    expect(sigma.kill).toHaveBeenCalledOnce();
    await waitFor(() => expect(renderer.instances).toHaveLength(2), {
      timeout: 5000,
    });
    expect(renderer.instances[1].graph.hasNode('new')).toBe(true);
  });

  it('reports failure when the renderer cannot start', async () => {
    renderer.fail = true;
    const onStatus = vi.fn();
    const { container } = render(
      <KnowledgeGraphCanvas
        nodes={nodes}
        edges={edges}
        visible={everyone}
        selectedId={null}
        onSelect={vi.fn()}
        onStatus={onStatus}
      />,
    );
    await waitFor(() => expect(onStatus).toHaveBeenLastCalledWith('failed'), {
      timeout: 5000,
    });
    expect(container.querySelector('.knowledge-network-shell')).toHaveAttribute(
      'data-renderer-status',
      'failed',
    );
  });
});
