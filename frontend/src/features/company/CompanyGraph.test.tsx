import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import type { ReactNode } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type {
  CompanyEntityView,
  CompanyGraphQuery,
  CompanyGraphView,
  CompanyView,
  KnowledgeGraphNode,
} from '../../api/types';
import type { ClientPlatform } from '../../platform';
import { RuntimeContext } from '../../runtime';
import type { KnowledgeGraphHandle } from '../home/KnowledgeGraphCanvas';
import CompanyGraph from './CompanyGraph';
import CompanyScope from './CompanyScope';

// Knowledge home's Company scope (Row-Bot for Teams, B8): the switch only
// while joined, the company's graph on the personal graph's canvas, its
// filters asked of the server, an entity's details with citations and no
// edit controls, and every state in words.

type CanvasProps = {
  nodes: KnowledgeGraphNode[];
  edges: CompanyGraphView['edges'];
  visible: ReadonlySet<string>;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onStatus?: (status: 'loading' | 'ready' | 'failed') => void;
};

// jsdom has no WebGL, so the real canvas reports "failed" and the graph is
// listed instead. Tests that need the canvas opt into a stand-in that reports
// "ready" and records what it was given.
const fakeGraph = vi.hoisted(() => ({
  enabled: false,
  props: null as CanvasProps | null,
  handle: { fit: vi.fn(), zoom: vi.fn(), focus: vi.fn() },
}));

vi.mock('../home/KnowledgeGraphCanvas', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('../home/KnowledgeGraphCanvas')>();
  const { createElement, forwardRef, useEffect, useImperativeHandle } =
    await import('react');
  const ReadyGraph = forwardRef<KnowledgeGraphHandle, CanvasProps>(
    function ReadyGraph(props, ref) {
      fakeGraph.props = props;
      useImperativeHandle(ref, () => fakeGraph.handle);
      const { onStatus } = props;
      useEffect(() => onStatus?.('ready'), [onStatus]);
      return createElement('div', { 'data-testid': 'ready-graph' });
    },
  );
  const Canvas = forwardRef<KnowledgeGraphHandle, CanvasProps>(
    function Canvas(props, ref) {
      return createElement(fakeGraph.enabled ? ReadyGraph : actual.default, {
        ...props,
        ref,
      });
    },
  );
  return { ...actual, default: Canvas };
});

afterEach(() => {
  fakeGraph.enabled = false;
  fakeGraph.props = null;
  window.localStorage.clear();
});

const COMPANY = 'rowbot.example.test';
const COMPANY_SPACE = '5a000000-0000-4000-8000-000000000001';
const SALES = '5a000000-0000-4000-8000-000000000002';
const ATLAS = 'e0000000-0000-4000-8000-000000000001';
const ADA = 'e0000000-0000-4000-8000-000000000002';
const ORION = 'e0000000-0000-4000-8000-000000000003';

const joined: CompanyView = {
  schema_version: 1,
  state: 'joined',
  company: COMPANY,
  origin: `https://${COMPANY}`,
  name: 'Ada Lovelace',
  email: 'ada@example.test',
  joined_at: '2026-11-23T09:00:00+00:00',
  computer_name: 'Fixture laptop',
  rejoining: false,
  failure: null,
  notice: null,
  on_this_computer: true,
};

function node(
  id: string,
  subject: string,
  entity_type: string,
  relation_count: number,
): KnowledgeGraphNode {
  return {
    id,
    revision: 'a'.repeat(64),
    subject,
    description: `About ${subject}.`,
    entity_type,
    source: 'document',
    updated_at: '2026-11-20T09:00:00+00:00',
    relation_count,
    orphan: false,
    is_user: false,
    status: 'active',
    tier: 'resource',
    source_label: '',
  };
}

const graph: CompanyGraphView = {
  schema_version: 1,
  state: 'ready',
  company: COMPANY,
  reason: null,
  nodes: [
    node(ATLAS, 'Atlas', 'project', 2),
    node(ADA, 'Ada Lovelace', 'person', 1),
    node(ORION, 'Orion Foods', 'customer', 1),
  ],
  edges: [
    {
      id: 'f0000000-0000-4000-8000-000000000001',
      source_id: ADA,
      target_id: ATLAS,
      relation_type: 'leads',
      updated_at: '2026-11-20T09:00:00+00:00',
    },
    {
      id: 'f0000000-0000-4000-8000-000000000002',
      source_id: ORION,
      target_id: ATLAS,
      relation_type: 'uses',
      updated_at: '2026-11-21T09:00:00+00:00',
    },
  ],
  spaces: [
    { id: COMPANY_SPACE, name: 'Company', kind: 'company', is_owner: false },
    { id: SALES, name: 'Sales', kind: 'group', is_owner: true },
  ],
  space_ids: [],
  total_entities: 3,
  total_relations: 2,
  shown_entities: 3,
  shown_relations: 2,
  truncated: false,
  entity_types: ['customer', 'person', 'project'],
};

const empty: CompanyGraphView = {
  ...graph,
  nodes: [],
  edges: [],
  total_entities: 0,
  total_relations: 0,
  shown_entities: 0,
  shown_relations: 0,
  entity_types: [],
};

function entityView(spaceId: string, spaceName: string): CompanyEntityView {
  return {
    schema_version: 1,
    state: 'ready',
    company: COMPANY,
    reason: null,
    entity: {
      id: ATLAS,
      subject: 'Atlas',
      entity_type: 'project',
      description: 'The billing platform rebuild.',
      aliases: ['Project Atlas'],
      status: 'active',
      space_id: spaceId,
      space_name: spaceName,
      updated_at: '2026-11-22T09:00:00+00:00',
      edited_at: null,
    },
    relations: [
      {
        relation_type: 'leads',
        direction: 'incoming',
        peer_id: ADA,
        peer_subject: 'Ada Lovelace',
        peer_type: 'person',
      },
    ],
    mentions: [
      {
        kind: 'document',
        space: 'Company',
        document: 'Platform plan 2027',
        excerpt: 'Atlas replaces the old billing system by June.',
      },
      {
        kind: 'fact',
        space: 'Sales',
        document: null,
        excerpt: 'Orion Foods runs its invoicing on Atlas.',
      },
    ],
    mention_count: 6,
  };
}

function fakeController({
  view = joined,
  graphs = () => graph,
  entity = () => entityView(COMPANY_SPACE, 'Company'),
}: {
  view?: CompanyView;
  graphs?: (query: CompanyGraphQuery) => CompanyGraphView | Promise<never>;
  entity?: (id: string) => CompanyEntityView;
} = {}) {
  const company = vi.fn(async (_action: string) => view);
  const companyGraph = vi.fn(async (query: CompanyGraphQuery) => graphs(query));
  const companyEntity = vi.fn(async (id: string) => entity(id));
  const controller = {
    company,
    companyGraph,
    companyEntity,
  } as unknown as ClientController;
  return { controller, company, companyGraph, companyEntity };
}

function inApp(children: ReactNode, controller?: ClientController) {
  return (
    <MemoryRouter>
      {controller ? (
        <RuntimeContext.Provider
          value={{ controller, platform: {} as ClientPlatform }}
        >
          {children}
        </RuntimeContext.Provider>
      ) : (
        children
      )}
    </MemoryRouter>
  );
}

function showGraph(fake = fakeController(), view = joined) {
  render(inApp(<CompanyGraph controller={fake.controller} company={view} />));
  return fake;
}

const MINE = <p>My own memories</p>;

describe('the scope switch', () => {
  it('is not there outside the app or with no company joined, and only the person’s own graph shows', async () => {
    render(inApp(<CompanyScope>{MINE}</CompanyScope>));
    expect(screen.getByText('My own memories')).toBeInTheDocument();
    expect(screen.queryByRole('radiogroup')).toBeNull();

    const fake = fakeController({
      view: { ...joined, state: 'not_joined', company: null, origin: null },
    });
    render(inApp(<CompanyScope>{MINE}</CompanyScope>, fake.controller));
    await waitFor(() => expect(fake.company).toHaveBeenCalledWith('status'));
    expect(screen.getAllByText('My own memories')).toHaveLength(2);
    expect(screen.queryByRole('radiogroup')).toBeNull();
    expect(fake.companyGraph).not.toHaveBeenCalled();
  });

  it('shows while joined: Mine first, Company draws the company’s graph and is remembered on this device', async () => {
    const fake = fakeController();
    const { unmount } = render(
      inApp(<CompanyScope>{MINE}</CompanyScope>, fake.controller),
    );
    const scope = await screen.findByRole('radiogroup', {
      name: 'Knowledge scope',
    });
    expect(scope).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Mine' })).toHaveAttribute(
      'aria-checked',
      'true',
    );
    expect(screen.getByText('My own memories')).toBeInTheDocument();
    expect(fake.companyGraph).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('radio', { name: 'Company' }));
    expect(await screen.findByText('3 entities')).toBeInTheDocument();
    expect(screen.queryByText('My own memories')).toBeNull();
    unmount();

    // Opened again: Company, and nothing of the person's graph meanwhile.
    let answer: (view: CompanyView) => void = () => undefined;
    const again = fakeController();
    again.company.mockImplementation(
      () =>
        new Promise<CompanyView>((resolve) => {
          answer = resolve;
        }),
    );
    render(inApp(<CompanyScope>{MINE}</CompanyScope>, again.controller));
    expect(screen.queryByText('My own memories')).toBeNull();
    await act(async () => answer(joined));
    expect(screen.getByRole('radio', { name: 'Company' })).toHaveAttribute(
      'aria-checked',
      'true',
    );
    await waitFor(() => expect(again.companyGraph).toHaveBeenCalled());
  });

  it('goes back to the person’s own graph once the Row-Bot left its company', async () => {
    window.localStorage.setItem('row-bot:knowledge:scope', 'company');
    const fake = fakeController({
      view: { ...joined, state: 'not_joined', company: null, origin: null },
    });
    render(inApp(<CompanyScope>{MINE}</CompanyScope>, fake.controller));
    expect(await screen.findByText('My own memories')).toBeInTheDocument();
    expect(screen.queryByRole('radiogroup')).toBeNull();
    expect(fake.companyGraph).not.toHaveBeenCalled();
  });
});

describe('the company graph', () => {
  it('feeds the personal graph’s canvas with the company’s nodes and edges', async () => {
    fakeGraph.enabled = true;
    const fake = showGraph();

    expect(await screen.findByTestId('ready-graph')).toBeInTheDocument();
    expect(fake.companyGraph).toHaveBeenCalledWith(
      { spaces: [], q: '', limit: 300 },
      expect.any(AbortSignal),
    );
    expect(fakeGraph.props?.nodes).toBe(graph.nodes);
    expect(fakeGraph.props?.edges).toBe(graph.edges);
    expect([...(fakeGraph.props?.visible ?? [])]).toEqual([ATLAS, ADA, ORION]);
    expect(screen.getByText('3 entities')).toBeInTheDocument();
    expect(screen.getByText('2 links')).toBeInTheDocument();
    expect(screen.queryByText(/^Showing/)).toBeNull();
    expect(
      screen.getByRole('list', { name: 'Entity types' }),
    ).toHaveTextContent('Project');
    expect(await screen.findByRole('button', { name: 'Fit' })).toBeVisible();
  });

  it('lists the entities where the canvas can’t draw', async () => {
    showGraph();
    expect(
      await screen.findByText(/The interactive graph needs WebGL/),
    ).toBeInTheDocument();
    expect(screen.getAllByText('Orion Foods').length).toBeGreaterThan(0);
  });

  it('asks the server for a space and a search, and says how much of a larger graph is shown', async () => {
    const fake = showGraph(
      fakeController({
        graphs: (query) =>
          query.q === 'orion'
            ? {
                ...graph,
                nodes: [graph.nodes[2]],
                edges: [],
                space_ids: query.spaces ?? [],
                total_entities: 1,
                total_relations: 0,
                shown_entities: 1,
                shown_relations: 0,
              }
            : {
                ...graph,
                space_ids: query.spaces ?? [],
                total_entities: 120,
                total_relations: 300,
                truncated: true,
              },
      }),
    );

    expect(await screen.findByText('Showing 3 of 120')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', { name: 'Space' }), {
      target: { value: SALES },
    });
    await waitFor(() =>
      expect(fake.companyGraph).toHaveBeenLastCalledWith(
        { spaces: [SALES], q: '', limit: 300 },
        expect.any(AbortSignal),
      ),
    );
    fireEvent.change(
      screen.getByRole('searchbox', { name: 'Search company knowledge' }),
      { target: { value: '  orion ' } },
    );
    await waitFor(() =>
      expect(fake.companyGraph).toHaveBeenLastCalledWith(
        { spaces: [SALES], q: 'orion', limit: 300 },
        expect.any(AbortSignal),
      ),
    );
    expect(await screen.findByText('1 entity')).toBeInTheDocument();
  });

  it('says when nothing matches, and Show everything clears the filters', async () => {
    const fake = showGraph(
      fakeController({ graphs: (query) => (query.q ? empty : graph) }),
    );
    await screen.findByText('3 entities');
    fireEvent.change(
      screen.getByRole('searchbox', { name: 'Search company knowledge' }),
      { target: { value: 'nobody' } },
    );
    expect(await screen.findByText('Nothing matches')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Show everything' }));
    expect(await screen.findByText('3 entities')).toBeInTheDocument();
    expect(fake.companyGraph).toHaveBeenLastCalledWith(
      { spaces: [], q: '', limit: 300 },
      expect.any(AbortSignal),
    );
  });

  it('shows an entity’s description, aliases, connections and citations, read only', async () => {
    fakeGraph.enabled = true;
    const fake = showGraph();
    await screen.findByTestId('ready-graph');

    act(() => fakeGraph.props?.onSelect(ATLAS));

    expect(
      await screen.findByText('The billing platform rebuild.'),
    ).toBeInTheDocument();
    expect(fake.companyEntity).toHaveBeenCalledWith(ATLAS);
    expect(screen.getByText('Project · Company')).toBeInTheDocument();
    expect(screen.getByText('Project Atlas')).toBeInTheDocument();
    expect(
      screen.getByText('From Company: Platform plan 2027'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Atlas replaces the old billing system by June.'),
    ).toBeInTheDocument();
    expect(screen.getByText('From Sales')).toBeInTheDocument();
    expect(screen.getByText('4 more mentions')).toBeInTheDocument();
    // Not an owner of the Company space: no line about the console.
    expect(screen.queryByText('Edited in the admin console')).toBeNull();
    // Read only: nothing to edit, merge, retire or delete, and no text field.
    expect(
      screen.queryByRole('button', {
        name: /edit|merge|retire|archive|delete|rename/i,
      }),
    ).toBeNull();
    expect(
      screen
        .getByRole('region', { name: 'Selected entity detail' })
        .querySelector('input, textarea, select, [contenteditable]'),
    ).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /Ada Lovelace/ }));
    await waitFor(() => {
      expect(fake.companyEntity).toHaveBeenLastCalledWith(ADA);
      expect(fakeGraph.handle.focus).toHaveBeenCalledWith(ADA);
    });
  });

  it('tells an owner of the space that it is edited in the admin console', async () => {
    fakeGraph.enabled = true;
    showGraph(fakeController({ entity: () => entityView(SALES, 'Sales') }));
    await screen.findByTestId('ready-graph');

    act(() => fakeGraph.props?.onSelect(ATLAS));

    expect(
      await screen.findByText('Edited in the admin console'),
    ).toBeInTheDocument();
  });

  it('says so when an entity is no longer there', async () => {
    fakeGraph.enabled = true;
    showGraph(
      fakeController({
        entity: () => ({
          ...entityView(COMPANY_SPACE, 'Company'),
          state: 'not_found',
          entity: null,
          relations: [],
          mentions: [],
          mention_count: 0,
        }),
      }),
    );
    await screen.findByTestId('ready-graph');
    act(() => fakeGraph.props?.onSelect(ORION));
    expect(
      await screen.findByText(`This is no longer in ${COMPANY}’s knowledge.`),
    ).toBeInTheDocument();
  });
});

describe('the company graph’s states', () => {
  it('loading', () => {
    showGraph(
      fakeController({ graphs: () => new Promise<never>(() => undefined) }),
    );
    expect(
      screen.getByText('Loading your company’s knowledge…'),
    ).toBeInTheDocument();
  });

  it('empty: the company hasn’t added documents yet, with no filters to offer', async () => {
    showGraph(fakeController({ graphs: () => empty }));
    expect(
      await screen.findByText('Your company hasn’t added documents yet'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('searchbox')).toBeNull();
  });

  it('disconnected: C1b’s words and Sign in again in Settings › Company', async () => {
    showGraph(
      fakeController({ graphs: () => ({ ...empty, state: 'disconnected' }) }),
    );
    expect(
      await screen.findByText('Your company disconnected this Row-Bot'),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        `${COMPANY} no longer accepts this Row-Bot. That happens when an admin removes it, your account is paused, or it’s been a month since you last signed in. Your own chats and memory are still here.`,
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Sign in again' })).toHaveAttribute(
      'href',
      '/settings/company',
    );
  });

  it('locked down: C3a’s words, and Retry reads the company’s rules again first', async () => {
    let locked = true;
    const fake = showGraph(
      fakeController({
        graphs: () =>
          locked
            ? { ...empty, state: 'locked_down', reason: 'lease_expired' }
            : graph,
      }),
    );
    expect(
      await screen.findByText(
        `Row-Bot can’t reach ${COMPANY}, so it’s using only what’s safe on this computer until it can.`,
      ),
    ).toBeInTheDocument();

    locked = false;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));

    expect(await screen.findByText('3 entities')).toBeInTheDocument();
    expect(fake.company).toHaveBeenCalledWith('check');
    expect(fake.companyGraph).toHaveBeenCalledTimes(2);
  });

  it('unreachable: C1b’s words, and Try again reads the graph again', async () => {
    let answers: CompanyGraphView[] = [
      { ...empty, state: 'unreachable', reason: 'busy' },
      { ...empty, state: 'unreachable', reason: null },
    ];
    const fake = showGraph(
      fakeController({ graphs: () => answers.shift() ?? graph }),
    );
    expect(
      await screen.findByText(
        `${COMPANY} is busy. Row-Bot stays joined; try again in a minute.`,
      ),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(
      await screen.findByText(
        `Can’t reach ${COMPANY} right now. Row-Bot stays joined.`,
      ),
    ).toBeInTheDocument();
    expect(fake.company).not.toHaveBeenCalledWith('check');
    answers = [];
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('3 entities')).toBeInTheDocument();
  });

  it('the app couldn’t read it: Try again', async () => {
    let fail = true;
    showGraph(
      fakeController({
        graphs: () =>
          fail ? Promise.reject(new Error('Row-Bot isn’t answering.')) : graph,
      }),
    );
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Company knowledge could not be refreshed',
    );
    fail = false;
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('3 entities')).toBeInTheDocument();
  });
});
