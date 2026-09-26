import { createRef } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
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
import { glAlpha, mix } from './knowledge-palette';

type Attributes = Record<string, unknown>;
type Reducer = (key: string, data: Attributes) => Attributes;
type FakeRenderer = {
  graph: Graph;
  container: HTMLElement;
  settings: { nodeReducer: Reducer; edgeReducer: Reducer } & Attributes;
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
// it and never touches a GPU context.
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

    getNodeDisplayData(key: string) {
      if (!this.graph.hasNode(key)) return undefined;
      const { x, y, size } = this.graph.getNodeAttributes(key) as {
        x: number;
        y: number;
        size: number;
      };
      return { x, y, size };
    }
  },
}));

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

  beforeEach(() => {
    stubWebgl();
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

  it('lays out the same memories in the same places every time', async () => {
    const first = await renderCanvas();
    const positions = first.sigma.graph.mapNodes((key, attributes) => [
      key,
      attributes.x,
      attributes.y,
    ]);
    first.unmount();
    expect(first.sigma.kill).toHaveBeenCalledOnce();
    renderer.instances = [];
    const second = await renderCanvas({ nodes: [...nodes] });
    expect(
      second.sigma.graph.mapNodes((key, attributes) => [
        key,
        attributes.x,
        attributes.y,
      ]),
    ).toEqual(positions);
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
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query.includes('reduce'),
      media: query,
    }));
    const { ref, sigma } = await renderCanvas();
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
