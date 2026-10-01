import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, onTestFinished, vi } from 'vitest';
import type { EntitySummary, EntitySummaryPage } from '../../api/types';
import KnowledgeHome, {
  type KnowledgeDreamState,
  type KnowledgeGraphEdge,
  type KnowledgeGraphNode,
  type KnowledgeGraphSnapshot,
  type KnowledgeMemoryQuery,
  type KnowledgeNodeDetail,
} from './KnowledgeHome';
import type { KnowledgeGraphHandle } from './KnowledgeGraphCanvas';

type CanvasProps = {
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
  visible: ReadonlySet<string>;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onStatus?: (status: 'loading' | 'ready' | 'failed') => void;
};

// jsdom has no WebGL, so the real canvas always reports "failed" and Knowledge
// falls back to its list. Tests that need a working graph opt into a stand-in
// renderer that reports "ready" and records what Knowledge asks of it.
const fakeGraph = vi.hoisted(() => ({
  enabled: false,
  props: null as CanvasProps | null,
  handle: { fit: vi.fn(), zoom: vi.fn(), focus: vi.fn() },
}));

vi.mock('./KnowledgeGraphCanvas', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('./KnowledgeGraphCanvas')>();
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

const revision = 'a'.repeat(64);

afterEach(() => {
  fakeGraph.enabled = false;
  fakeGraph.props = null;
  vi.unstubAllGlobals();
});

/**
 * Browsers compute the absolutely positioned `.visually-hidden` as a block, so
 * its words stay apart from the text beside them in an accessible name. jsdom
 * has no layout, so this models that for the rest of the current test.
 */
function blockVisuallyHidden() {
  const style = document.createElement('style');
  style.textContent = '.visually-hidden { display: block; }';
  document.head.append(style);
  onTestFinished(() => style.remove());
}

const populated: KnowledgeGraphSnapshot = {
  schema_version: 1,
  availability: 'available',
  revision,
  nodes: [
    {
      id: 'user',
      revision,
      subject: 'User',
      description: 'The user hub',
      entity_type: 'person',
      source: 'manual',
      updated_at: '2026-09-19T10:00:00Z',
      relation_count: 1,
      orphan: false,
      is_user: true,
      status: 'active',
      tier: 'core',
    },
    {
      id: 'alpha',
      revision,
      subject: 'Alpha project',
      description: 'A saved project fact',
      entity_type: 'fact',
      source: 'extraction',
      updated_at: '2026-09-18T10:00:00Z',
      relation_count: 1,
      orphan: false,
      is_user: false,
      status: 'needs_review',
      tier: 'episodic',
    },
    {
      id: 'quiet',
      revision,
      subject: 'Quiet preference',
      description: 'An isolated document memory',
      entity_type: 'preference',
      source: 'document',
      updated_at: '2026-05-01T10:00:00Z',
      relation_count: 0,
      orphan: true,
      is_user: false,
      status: 'archived',
      tier: 'resource',
    },
  ],
  edges: [
    {
      id: 'user-alpha',
      source_id: 'user',
      target_id: 'alpha',
      relation_type: 'works_on',
      updated_at: '2026-09-19T10:00:00Z',
    },
  ],
  total_entities: 3,
  total_relations: 1,
  shown_entities: 3,
  shown_relations: 1,
  truncated: false,
  center_id: 'user',
  entity_types: ['fact', 'person', 'preference'],
  sources: ['document', 'extraction', 'manual'],
  status_counts: { active: 1, needs_review: 1, superseded: 0, archived: 1 },
};

const empty: KnowledgeGraphSnapshot = {
  ...populated,
  nodes: [],
  edges: [],
  total_entities: 0,
  total_relations: 0,
  shown_entities: 0,
  shown_relations: 0,
  center_id: null,
  entity_types: [],
  sources: [],
  status_counts: {},
};

const idleDream: KnowledgeDreamState = {
  available: true,
  enabled: true,
  state: 'idle',
  message: '',
};

function detail(id = 'alpha'): KnowledgeNodeDetail {
  if (id === 'user')
    return {
      id,
      revision,
      subject: 'User',
      description: 'The person Row-Bot works for',
      entity_type: 'person',
      source: 'manual',
      updated_at: '2026-09-19T10:00:00Z',
      relation_count: 1,
      status: 'active',
      tier: 'core',
      relations: [
        {
          relation_type: 'works_on',
          direction: 'outgoing',
          peer_id: 'alpha',
          peer_subject: 'Alpha project',
        },
      ],
    };
  return {
    id,
    revision,
    subject: id === 'alpha' ? 'Alpha project' : 'Quiet preference',
    description: '<img src=x onerror=sentinel()>',
    entity_type: id === 'alpha' ? 'fact' : 'preference',
    source: id === 'alpha' ? 'chat' : 'document:notes.txt',
    updated_at: '2026-09-19T10:00:00Z',
    relation_count: 1,
    status: 'active',
    tier: 'episodic',
    confidence: 0.82,
    aliases: ['Project A'],
    tags: ['important'],
    relations: [
      { relation_type: 'owned_by', peer_id: 'user', peer_subject: 'User' },
    ],
  };
}

function props(
  overrides: Partial<React.ComponentProps<typeof KnowledgeHome>> = {},
): React.ComponentProps<typeof KnowledgeHome> {
  return {
    snapshot: populated,
    loading: false,
    error: '',
    reload: vi.fn(),
    loadDetail: vi.fn(async (id) => detail(id)),
    onEdit: vi.fn(),
    dream: idleDream,
    onDream: vi.fn(),
    ...overrides,
  };
}

/** Memory names listed in the table, in order (header and spacers excluded). */
function listedMemories() {
  const table = screen.getByRole('table', { name: 'Knowledge entities' });
  const [, body] = within(table).getAllByRole('rowgroup');
  return within(body)
    .queryAllByRole('button')
    .map((button) => button.textContent);
}

function stats() {
  return screen.getByLabelText('Knowledge statistics');
}

async function openFilters(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'Filters' }));
  return screen.findByRole('dialog', { name: 'Memory filters' });
}

async function openMemory(
  user: ReturnType<typeof userEvent.setup>,
  subject: string,
) {
  await user.click(screen.getByRole('button', { name: subject }));
  return screen.findByRole('dialog', { name: subject });
}

describe('knowledge states', () => {
  it('renders the zero-entity memory-map empty state', async () => {
    const user = userEvent.setup();
    const reload = vi.fn();
    render(<KnowledgeHome {...props({ snapshot: empty, reload })} />);
    expect(screen.getByRole('region', { name: 'Knowledge' })).toBeVisible();
    expect(screen.getByText('Your memory map is empty')).toBeVisible();
    expect(
      screen.queryByRole('combobox', { name: 'Search memories' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('radiogroup', { name: 'Knowledge view' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('group', { name: 'Memory types' }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText('Knowledge statistics'),
    ).not.toBeInTheDocument();
    // Dream and refresh stay reachable while the map is empty.
    const controls = screen.getByRole('toolbar', {
      name: 'Knowledge controls',
    });
    expect(
      within(controls).getByRole('button', { name: 'Run Dream Cycle' }),
    ).toBeEnabled();
    await user.click(
      within(controls).getByRole('button', { name: 'Refresh knowledge' }),
    );
    expect(reload).toHaveBeenCalledOnce();
  });

  it('shows loading, refresh errors, and unavailable graphs honestly', async () => {
    const user = userEvent.setup();
    const reload = vi.fn();
    const { rerender } = render(
      <KnowledgeHome {...props({ snapshot: null, loading: true, reload })} />,
    );
    expect(screen.getByRole('status')).toHaveTextContent(
      'Loading knowledge graph…',
    );
    expect(screen.queryByRole('toolbar')).not.toBeInTheDocument();

    rerender(
      <KnowledgeHome
        {...props({ snapshot: null, error: 'The server is busy.', reload })}
      />,
    );
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('Knowledge could not be refreshed');
    expect(alert).toHaveTextContent('The server is busy.');
    await user.click(within(alert).getByRole('button', { name: 'Try again' }));
    expect(reload).toHaveBeenCalledOnce();

    rerender(
      <KnowledgeHome
        {...props({
          snapshot: { ...empty, availability: 'missing' },
          reload,
        })}
      />,
    );
    expect(screen.getByText('Knowledge unavailable')).toBeVisible();
    expect(
      screen.getByText('The knowledge graph has not been created yet.'),
    ).toBeVisible();
    expect(
      screen.queryByText('Your memory map is empty'),
    ).not.toBeInTheDocument();

    rerender(
      <KnowledgeHome
        {...props({
          snapshot: { ...empty, availability: 'corrupt' },
          loading: true,
          reload,
        })}
      />,
    );
    expect(
      screen.getByText('The knowledge graph could not be read safely.'),
    ).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'Refresh knowledge' }),
    ).toBeDisabled();
  });
});

describe('knowledge exploration', () => {
  it('falls back to the memory list with a note when WebGL is unavailable', async () => {
    const user = userEvent.setup();
    render(<KnowledgeHome {...props()} />);
    expect(
      screen.getByText(/The interactive graph needs WebGL/),
    ).toHaveAttribute('role', 'status');
    // Most connected first; ties keep a stable order.
    expect(listedMemories()).toEqual([
      'Alpha project',
      'User',
      'Quiet preference',
    ]);
    expect(screen.getByRole('radio', { name: 'Graph' })).toBeChecked();
    expect(
      screen.queryByRole('toolbar', { name: 'Graph navigation' }),
    ).not.toBeInTheDocument();
    expect(stats()).toHaveTextContent('3 memories');
    expect(stats()).toHaveTextContent('1 link');
    expect(stats()).not.toHaveTextContent('showing');

    await user.click(screen.getByRole('radio', { name: 'List' }));
    expect(screen.getByRole('radio', { name: 'List' })).toBeChecked();
    expect(
      screen.queryByText(/The interactive graph needs WebGL/),
    ).not.toBeInTheDocument();
    expect(listedMemories()).toHaveLength(3);
  });

  it('filters populated topology by type, source, User hub, and orphan state', async () => {
    const user = userEvent.setup();
    render(<KnowledgeHome {...props()} />);
    const legend = screen.getByRole('group', { name: 'Memory types' });
    expect(
      within(legend)
        .getAllByRole('button')
        .map((button) => button.getAttribute('aria-label')),
    ).toEqual(['Preference, 1 memory', 'Person, 1 memory', 'Fact, 1 memory']);
    expect(screen.getByRole('button', { name: 'Filters' })).toHaveAttribute(
      'aria-pressed',
      'false',
    );

    // Type: the legend hides and restores one type at a time.
    const fact = within(legend).getByRole('button', { name: 'Fact, 1 memory' });
    await user.click(fact);
    expect(fact).toHaveAttribute('aria-pressed', 'false');
    expect(listedMemories()).toEqual(['User', 'Quiet preference']);
    expect(stats()).toHaveTextContent('showing 2 of 3');
    expect(screen.getByRole('button', { name: 'Filters' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await user.click(
      within(legend).getByRole('button', { name: 'Show all types' }),
    );
    expect(fact).toHaveAttribute('aria-pressed', 'true');
    expect(listedMemories()).toHaveLength(3);
    expect(
      within(legend).queryByRole('button', { name: 'Show all types' }),
    ).not.toBeInTheDocument();

    // Source: only memories from the chosen source bucket stay listed.
    let filters = await openFilters(user);
    const source = within(filters).getByRole('combobox', { name: 'Source' });
    expect(
      within(source)
        .getAllByRole('option')
        .map((option) => option.textContent),
    ).toEqual([
      'All sources',
      'From documents',
      'From conversations',
      'Saved by you',
    ]);
    await user.selectOptions(source, 'extraction');
    expect(listedMemories()).toEqual(['Alpha project']);
    await user.selectOptions(source, '');
    expect(listedMemories()).toHaveLength(3);

    // User hub, then orphans.
    await user.click(within(filters).getByRole('switch', { name: 'User hub' }));
    expect(listedMemories()).toEqual(['Alpha project', 'Quiet preference']);
    await user.click(
      within(filters).getByRole('switch', { name: 'Hide orphans' }),
    );
    expect(listedMemories()).toEqual(['Alpha project']);
    expect(stats()).toHaveTextContent('showing 1 of 3');

    // Nothing left: say so and offer the way back.
    await user.click(
      within(legend).getByRole('button', { name: 'Fact, 1 memory' }),
    );
    expect(listedMemories()).toEqual([]);
    expect(screen.getByText('No memories match these filters')).toBeVisible();
    filters = await openFilters(user);
    expect(
      within(filters).getByRole('switch', { name: 'User hub' }),
    ).not.toBeChecked();
    expect(
      within(filters).getByRole('switch', { name: 'Hide orphans' }),
    ).toBeChecked();
  });

  it('search suggests memories by name, then description, and Enter opens the best match', async () => {
    const user = userEvent.setup();
    const loadDetail = vi.fn(async (id: string) => detail(id));
    render(
      <KnowledgeHome
        {...props({
          loadDetail,
          snapshot: {
            ...populated,
            nodes: [
              ...populated.nodes,
              {
                ...populated.nodes[2],
                id: 'atlas',
                subject: 'Project Atlas',
                description: 'Roadmap for the atlas project',
                entity_type: 'project',
                orphan: true,
              },
            ],
            total_entities: 4,
          },
        })}
      />,
    );
    const search = screen.getByRole('combobox', { name: 'Search memories' });
    const results = document.getElementById(
      search.getAttribute('aria-controls') ?? '',
    ) as HTMLElement;
    expect(results).toHaveAttribute('role', 'listbox');
    expect(results).not.toBeVisible();
    expect(search).toHaveAttribute('aria-expanded', 'false');

    await user.type(search, 'project');
    expect(search).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('listbox', { name: 'Matching memories' })).toBe(
      results,
    );
    // A name that starts with the query ranks above one that contains it.
    const options = within(results).getAllByRole('option');
    expect(options.map((option) => option.textContent)).toEqual([
      'Project AtlasProject · 0',
      'Alpha projectFact · 1',
    ]);
    expect(options[0]).toHaveAttribute('aria-selected', 'true');

    await user.keyboard('{ArrowDown}');
    expect(options[1]).toHaveAttribute('aria-selected', 'true');
    expect(search).toHaveAttribute('aria-activedescendant', options[1].id);
    await user.keyboard('{Enter}');
    expect(
      await screen.findByRole('dialog', { name: 'Alpha project' }),
    ).toBeInTheDocument();
    expect(loadDetail).toHaveBeenCalledWith('alpha');
    expect(search).toHaveValue('Alpha project');
    expect(search).toHaveAttribute('aria-expanded', 'false');
    expect(
      screen.getByText('Alpha project and its connections are highlighted.'),
    ).toBeInTheDocument();

    // Descriptions match too; Enter takes the first suggestion.
    await user.clear(search);
    await user.type(search, 'isolated');
    expect(
      within(results)
        .getAllByRole('option')
        .map((option) => option.textContent),
    ).toEqual(['Quiet preferencePreference · 0']);
    await user.keyboard('{Enter}');
    expect(
      await screen.findByRole('dialog', { name: 'Quiet preference' }),
    ).toBeInTheDocument();
    expect(loadDetail).toHaveBeenLastCalledWith('quiet');
  });

  it('search says when nothing matches and reveals memories hidden by filters', async () => {
    const user = userEvent.setup();
    render(<KnowledgeHome {...props()} />);
    const search = screen.getByRole('combobox', { name: 'Search memories' });
    await user.type(search, 'zebra');
    expect(screen.getByText('No memory matches “zebra”.')).toHaveAttribute(
      'role',
      'status',
    );
    await user.keyboard('{Enter}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    // Escape closes the suggestions first, then clears the query.
    await user.keyboard('{Escape}');
    expect(search).toHaveValue('zebra');
    await user.keyboard('{Escape}');
    expect(search).toHaveValue('');

    const legend = screen.getByRole('group', { name: 'Memory types' });
    await user.click(
      within(legend).getByRole('button', { name: 'Fact, 1 memory' }),
    );
    expect(listedMemories()).not.toContain('Alpha project');
    await user.type(search, 'alpha{Enter}');
    expect(
      await screen.findByRole('dialog', { name: 'Alpha project' }),
    ).toBeInTheDocument();
    expect(
      within(legend).getByRole('button', { name: 'Fact, 1 memory' }),
    ).toHaveAttribute('aria-pressed', 'true');
    expect(listedMemories()).toContain('Alpha project');
  });

  it('search offers every memory when nothing matches the part that is loaded', async () => {
    const user = userEvent.setup();
    const onShowAll = vi.fn();
    const truncated = { ...populated, truncated: true, total_entities: 400 };
    const { rerender } = render(
      <KnowledgeHome {...props({ snapshot: truncated, onShowAll })} />,
    );
    const search = screen.getByRole('combobox', { name: 'Search memories' });
    await user.type(search, 'zebra');
    const status = screen.getByText('No match in the 3 shown.');
    expect(status).toHaveAttribute('role', 'status');
    expect(screen.queryByText(/No memory matches/)).not.toBeInTheDocument();
    await user.click(
      within(status).getByRole('button', { name: 'Search all 400 memories' }),
    );
    expect(onShowAll).toHaveBeenCalledOnce();
    // The query and suggestions stay put while every memory loads.
    expect(search).toHaveValue('zebra');
    expect(search).toHaveFocus();

    // Once every memory is loaded, or when none can be, it is a plain miss.
    rerender(
      <KnowledgeHome
        {...props({ snapshot: truncated, onShowAll, showingAll: true })}
      />,
    );
    expect(screen.getByText('No memory matches “zebra”.')).toHaveAttribute(
      'role',
      'status',
    );
    expect(
      screen.queryByRole('button', { name: /^Search all/ }),
    ).not.toBeInTheDocument();
    rerender(<KnowledgeHome {...props({ snapshot: truncated })} />);
    expect(screen.getByText('No memory matches “zebra”.')).toBeInTheDocument();
    rerender(<KnowledgeHome {...props({ onShowAll })} />);
    expect(screen.getByText('No memory matches “zebra”.')).toBeInTheDocument();
    expect(onShowAll).toHaveBeenCalledOnce();
  });

  it('supports keyboard node selection and the semantic list/detail fallback', async () => {
    const user = userEvent.setup();
    const loadDetail = vi.fn(async (id: string) => detail(id));
    render(<KnowledgeHome {...props({ loadDetail })} />);
    const alpha = screen.getByRole('button', { name: 'Alpha project' });
    alpha.focus();
    await user.keyboard('{Enter}');
    const inspector = await screen.findByRole('dialog', {
      name: 'Alpha project',
    });
    expect(
      await within(inspector).findByText('<img src=x onerror=sentinel()>'),
    ).toBeVisible();
    expect(loadDetail).toHaveBeenCalledWith('alpha');
    expect(alpha).toHaveAttribute('aria-current', 'true');

    // Arrow keys move the view switch like a radio group.
    const graph = screen.getByRole('radio', { name: 'Graph' });
    graph.focus();
    await user.keyboard('{ArrowRight}');
    expect(screen.getByRole('radio', { name: 'List' })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'List' })).toHaveFocus();
    expect(
      screen.queryByText(/The interactive graph needs WebGL/),
    ).not.toBeInTheDocument();

    const list = screen.getByRole('table', { name: 'Knowledge entities' });
    within(list).getByRole('button', { name: 'Quiet preference' }).focus();
    await user.keyboard('{Enter}');
    expect(loadDetail).toHaveBeenLastCalledWith('quiet');
    expect(
      await screen.findByRole('dialog', { name: 'Quiet preference' }),
    ).toBeInTheDocument();

    // Reopening a memory whose detail is loaded does not read it again.
    within(list).getByRole('button', { name: 'Alpha project' }).focus();
    await user.keyboard('{Enter}');
    expect(
      await screen.findByRole('dialog', { name: 'Alpha project' }),
    ).toBeInTheDocument();
    expect(loadDetail.mock.calls.filter(([id]) => id === 'alpha')).toHaveLength(
      1,
    );

    await user.click(
      screen.getByRole('button', { name: 'Close memory detail' }),
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(
      within(list).getByRole('button', { name: 'Alpha project' }),
    ).not.toHaveAttribute('aria-current');
  });

  it('Clear filters and Show everything restore every filter and toggle', async () => {
    const user = userEvent.setup();
    render(<KnowledgeHome {...props()} />);
    const legend = screen.getByRole('group', { name: 'Memory types' });
    const restoredEverything = async () => {
      expect(listedMemories()).toHaveLength(3);
      expect(
        screen.getByText('All memories and connections are shown.'),
      ).toBeInTheDocument();
      for (const button of within(legend).getAllByRole('button'))
        expect(button).toHaveAttribute('aria-pressed', 'true');
      expect(screen.getByRole('button', { name: 'Filters' })).toHaveAttribute(
        'aria-pressed',
        'false',
      );
      expect(stats()).not.toHaveTextContent('showing');
      expect(
        within(stats()).queryByRole('button', { name: 'Clear filters' }),
      ).not.toBeInTheDocument();
      const filters = await openFilters(user);
      expect(
        within(filters).getByRole('switch', { name: 'User hub' }),
      ).toBeChecked();
      expect(
        within(filters).getByRole('switch', { name: 'Hide orphans' }),
      ).not.toBeChecked();
      expect(
        within(filters).getByRole('combobox', { name: 'Source' }),
      ).toHaveValue('');
      expect(
        within(filters).queryByRole('button', { name: 'Show everything' }),
      ).not.toBeInTheDocument();
      await user.keyboard('{Escape}');
    };
    const filterEverything = async () => {
      await user.click(
        within(legend).getByRole('button', { name: 'Preference, 1 memory' }),
      );
      const filters = await openFilters(user);
      await user.selectOptions(
        within(filters).getByRole('combobox', { name: 'Source' }),
        'extraction',
      );
      await user.click(
        within(filters).getByRole('switch', { name: 'User hub' }),
      );
      await user.click(
        within(filters).getByRole('switch', { name: 'Hide orphans' }),
      );
      expect(listedMemories()).toEqual(['Alpha project']);
      return filters;
    };

    await filterEverything();
    await user.keyboard('{Escape}');
    await user.click(
      within(stats()).getByRole('button', { name: 'Clear filters' }),
    );
    await restoredEverything();

    const filters = await filterEverything();
    await user.click(
      within(filters).getByRole('button', { name: 'Show everything' }),
    );
    await user.keyboard('{Escape}');
    await restoredEverything();

    // The empty-result message offers the same way back.
    await user.click(
      within(legend).getByRole('button', { name: 'Fact, 1 memory' }),
    );
    await user.click(
      within(legend).getByRole('button', { name: 'Person, 1 memory' }),
    );
    await user.click(
      within(legend).getByRole('button', { name: 'Preference, 1 memory' }),
    );
    expect(listedMemories()).toEqual([]);
    await user.click(screen.getByRole('button', { name: 'Show everything' }));
    await restoredEverything();
  });

  it('offers Show all only for a truncated snapshot and asks for every memory', async () => {
    const user = userEvent.setup();
    const onShowAll = vi.fn();
    const truncated = { ...populated, truncated: true, total_entities: 400 };
    const { rerender } = render(
      <KnowledgeHome {...props({ snapshot: truncated, onShowAll })} />,
    );
    expect(stats()).toHaveTextContent('400 memories');
    expect(stats()).toHaveTextContent('showing 3');
    expect(stats()).not.toHaveTextContent('showing 3 of');
    await user.click(
      within(stats()).getByRole('button', { name: 'Show all memories' }),
    );
    expect(onShowAll).toHaveBeenCalledOnce();

    // Once the shell has raised the limit, the offer goes away.
    rerender(
      <KnowledgeHome
        {...props({ snapshot: truncated, onShowAll, showingAll: true })}
      />,
    );
    expect(
      screen.queryByRole('button', { name: 'Show all memories' }),
    ).not.toBeInTheDocument();
    expect(stats()).toHaveTextContent('showing 3');

    rerender(<KnowledgeHome {...props({ onShowAll })} />);
    expect(
      screen.queryByRole('button', { name: 'Show all memories' }),
    ).not.toBeInTheDocument();
    rerender(<KnowledgeHome {...props({ snapshot: truncated })} />);
    expect(
      screen.queryByRole('button', { name: 'Show all memories' }),
    ).not.toBeInTheDocument();
  });

  it('drives graph navigation, focus, and selection through the renderer', async () => {
    fakeGraph.enabled = true;
    const user = userEvent.setup();
    const loadDetail = vi.fn(async (id: string) => detail(id));
    render(<KnowledgeHome {...props({ loadDetail })} />);
    expect(screen.getByTestId('ready-graph')).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    const navigation = screen.getByRole('toolbar', {
      name: 'Graph navigation',
    });
    await user.click(
      within(navigation).getByRole('button', { name: 'Zoom in' }),
    );
    expect(fakeGraph.handle.zoom).toHaveBeenLastCalledWith(0.25);
    await user.click(
      within(navigation).getByRole('button', { name: 'Zoom out' }),
    );
    expect(fakeGraph.handle.zoom).toHaveBeenLastCalledWith(-0.2);

    // Clicking a memory or the empty stage selects and clears.
    act(() => fakeGraph.props?.onSelect('quiet'));
    expect(
      await screen.findByRole('dialog', { name: 'Quiet preference' }),
    ).toBeInTheDocument();
    expect(loadDetail).toHaveBeenLastCalledWith('quiet');
    expect(fakeGraph.props?.selectedId).toBe('quiet');
    act(() => fakeGraph.props?.onSelect(null));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(fakeGraph.props?.selectedId).toBeNull();

    // Filters hide memories in place: the graph keeps every memory.
    await user.click(
      within(screen.getByRole('group', { name: 'Memory types' })).getByRole(
        'button',
        { name: 'Preference, 1 memory' },
      ),
    );
    const filters = await openFilters(user);
    await user.click(within(filters).getByRole('switch', { name: 'User hub' }));
    await user.keyboard('{Escape}');
    expect(fakeGraph.props?.nodes).toHaveLength(3);
    expect([...(fakeGraph.props?.visible ?? [])]).toEqual(['alpha']);
    expect(screen.getByText('0 links visible.')).toBeInTheDocument();
    await user.click(within(navigation).getByRole('button', { name: 'Fit' }));
    expect(fakeGraph.handle.fit).toHaveBeenCalledOnce();
    expect(screen.getByText('Graph fitted to 1 memory.')).toBeInTheDocument();

    // Search moves the camera to the chosen memory.
    await user.type(
      screen.getByRole('combobox', { name: 'Search memories' }),
      'alpha{Enter}',
    );
    await waitFor(() =>
      expect(fakeGraph.handle.focus).toHaveBeenCalledWith('alpha'),
    );
    expect(
      await screen.findByRole('dialog', { name: 'Alpha project' }),
    ).toBeInTheDocument();
    expect(fakeGraph.props?.selectedId).toBe('alpha');
    expect(loadDetail).toHaveBeenLastCalledWith('alpha');

    // The list view replaces the graph and its navigation.
    await user.click(screen.getByRole('radio', { name: 'List' }));
    expect(screen.queryByTestId('ready-graph')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('toolbar', { name: 'Graph navigation' }),
    ).not.toBeInTheDocument();
    expect(listedMemories()).toEqual(['Alpha project']);
  });
});

describe('memory inspector', () => {
  it('loads escaped rich detail, exposes edit, and recovers from detail errors', async () => {
    const user = userEvent.setup();
    const onEdit = vi.fn();
    let quietFailures = 1;
    const loadDetail = vi.fn(async (id: string) => {
      if (id === 'quiet' && quietFailures-- > 0)
        throw new Error('Detail service is unavailable');
      return detail(id);
    });
    render(<KnowledgeHome {...props({ loadDetail, onEdit })} />);
    const inspector = await openMemory(user, 'Alpha project');
    expect(inspector).toHaveAccessibleDescription('Fact · From conversations');
    const body = within(inspector).getByRole('region', {
      name: 'Selected memory detail',
    });
    expect(
      await within(body).findByText('<img src=x onerror=sentinel()>'),
    ).toBeVisible();
    expect(document.querySelector('img')).toBeNull();
    expect(within(body).getByText('82%')).toBeVisible();
    expect(within(body).getByText('Project A')).toBeVisible();
    expect(within(body).getByText('#important')).toBeVisible();

    const connections = within(body).getByRole('region', {
      name: 'Connections',
    });
    expect(
      within(connections).getByRole('heading', { level: 4, name: 'Owned by' }),
    ).toBeVisible();
    expect(
      within(connections).getByRole('button', { name: /User$/ }),
    ).toBeEnabled();

    // B8: human words and relative time, never raw ISO values or tier ids.
    const fact = (term: string) =>
      within(body).getByText(term, { selector: 'dt' }).nextElementSibling;
    const updated = fact('Updated')?.querySelector('time');
    expect(updated).toHaveAttribute('datetime', '2026-09-19T10:00:00.000Z');
    expect(updated?.textContent).not.toMatch(/\d{4}-\d{2}-\d{2}T/);
    expect(updated).toHaveAttribute('title', expect.stringMatching(/2026/));
    expect(within(body).queryByText('Tier')).toBeNull();
    expect(fact('Memory type')).toHaveTextContent('From a conversation');
    expect(fact('Source')).toHaveTextContent('Chat');
    expect(fact('Confidence')).toHaveTextContent('82%');
    // Active is the normal state, so it is not called out.
    expect(within(body).queryByText('Status', { selector: 'dt' })).toBeNull();

    expect(
      within(inspector).queryByRole('button', { name: 'Merge or replace' }),
    ).not.toBeInTheDocument();
    expect(
      within(inspector).queryByRole('button', { name: 'Delete memory' }),
    ).not.toBeInTheDocument();
    await user.click(
      within(inspector).getByRole('button', { name: 'Edit memory' }),
    );
    expect(onEdit).toHaveBeenCalledWith('alpha');

    const quiet = await openMemory(user, 'Quiet preference');
    const alert = await within(quiet).findByRole('alert');
    expect(alert).toHaveTextContent('Memory detail unavailable');
    expect(alert).toHaveTextContent('Detail service is unavailable');
    // The snapshot summary still shows while the detail is unavailable.
    expect(
      within(quiet).getByText('An isolated document memory'),
    ).toBeVisible();

    await user.click(within(alert).getByRole('button', { name: 'Try again' }));
    expect(
      await within(quiet).findByText('<img src=x onerror=sentinel()>'),
    ).toBeVisible();
    expect(within(quiet).queryByRole('alert')).not.toBeInTheDocument();
    expect(
      within(quiet).getByText('Source', { selector: 'dt' }).nextElementSibling,
    ).toHaveTextContent('Document · notes.txt');
    expect(loadDetail.mock.calls.filter(([id]) => id === 'quiet')).toHaveLength(
      2,
    );
  });

  it('offers merge, delete, the source conversation, and connected memories', async () => {
    const user = userEvent.setup();
    const onMerge = vi.fn();
    const onOpenConversation = vi.fn();
    const onDelete = vi
      .fn<(id: string, subject: string) => Promise<boolean>>()
      .mockResolvedValueOnce(false)
      .mockResolvedValueOnce(true);
    const loadDetail = vi.fn(async (id: string) =>
      id === 'alpha'
        ? {
            ...detail(id),
            status: 'needs_review',
            relation_count: 4,
            relations: [
              {
                relation_type: 'owned_by',
                direction: 'incoming',
                peer_id: 'user',
                peer_subject: 'User',
              },
              { relation_type: 'mentions', peer_subject: 'Archived memory' },
            ],
            source_context: ['thread id: thread-42', 'thread name: Planning'],
          }
        : id === 'quiet'
          ? {
              ...detail(id),
              relation_count: 0,
              relations: [],
              source_context: ['document title: Notes'],
            }
          : detail(id),
    );
    render(
      <KnowledgeHome
        {...props({ loadDetail, onMerge, onDelete, onOpenConversation })}
      />,
    );
    let inspector = await openMemory(user, 'Alpha project');
    const body = within(inspector).getByRole('region', {
      name: 'Selected memory detail',
    });
    expect(
      within(body).getByText('Status', { selector: 'dt' }).nextElementSibling,
    ).toHaveTextContent('Needs review');
    const connections = within(body).getByRole('region', {
      name: 'Connections',
    });
    expect(
      within(connections).getByRole('button', { name: /Archived memory$/ }),
    ).toBeDisabled();
    // The rest are listed in the editor's relations (B264).
    expect(
      within(connections).getByText(/^2 more connections\./),
    ).toBeVisible();
    await user.click(
      within(connections).getByRole('button', { name: 'Show all connections' }),
    );
    expect(onMerge).toHaveBeenLastCalledWith('alpha');
    onMerge.mockClear();

    await user.click(
      within(inspector).getByRole('button', { name: 'Merge or replace' }),
    );
    expect(onMerge).toHaveBeenCalledWith('alpha');
    await user.click(
      within(body).getByRole('button', {
        name: 'Open source conversation: Planning',
      }),
    );
    expect(onOpenConversation).toHaveBeenCalledWith('thread-42');

    // A declined delete keeps the inspector; a completed one closes it.
    await user.click(
      within(inspector).getByRole('button', { name: 'Delete memory' }),
    );
    expect(onDelete).toHaveBeenLastCalledWith('alpha', 'Alpha project');
    expect(
      screen.getByRole('dialog', { name: 'Alpha project' }),
    ).toBeInTheDocument();
    await user.click(
      within(inspector).getByRole('button', { name: 'Delete memory' }),
    );
    await waitFor(() =>
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument(),
    );

    // Following a connection opens the connected memory.
    inspector = await openMemory(user, 'Alpha project');
    await user.click(within(inspector).getByRole('button', { name: /User$/ }));
    inspector = await screen.findByRole('dialog', { name: 'User' });
    expect(loadDetail).toHaveBeenLastCalledWith('user');
    expect(
      screen.getByText('User and its connections are highlighted.'),
    ).toBeInTheDocument();
    expect(
      await within(inspector).findByText('Core · always recalled'),
    ).toBeVisible();

    inspector = await openMemory(user, 'Quiet preference');
    expect(
      await within(inspector).findByText('From document “Notes”'),
    ).toBeVisible();
    expect(within(inspector).getByText('No connections yet.')).toBeVisible();
    expect(
      within(inspector).queryByRole('button', {
        name: /Open source conversation/,
      }),
    ).not.toBeInTheDocument();
  });

  it('names each connection by its direction and the connected memory', async () => {
    blockVisuallyHidden();
    const user = userEvent.setup();
    const loadDetail = vi.fn(async (id: string) =>
      id === 'alpha'
        ? {
            ...detail(id),
            relation_count: 3,
            relations: [
              {
                relation_type: 'works_with',
                direction: 'outgoing',
                peer_id: 'user',
                peer_subject: 'Partner',
              },
              {
                relation_type: 'works_with',
                direction: 'incoming',
                peer_id: 'quiet',
                peer_subject: 'Mentor',
              },
              { relation_type: 'mentions', peer_subject: 'Archived memory' },
            ],
          }
        : detail(id),
    );
    render(<KnowledgeHome {...props({ loadDetail })} />);
    const inspector = await openMemory(user, 'Alpha project');
    const connections = within(inspector).getByRole('region', {
      name: 'Connections',
    });
    // The arrow is decoration: the name reads the direction, then the memory.
    expect(
      await within(connections).findByRole('button', { name: 'to Partner' }),
    ).toBeEnabled();
    expect(
      within(connections).getByRole('button', { name: 'from Mentor' }),
    ).toBeEnabled();
    // Without a direction a connection reads as outgoing.
    expect(
      within(connections).getByRole('button', { name: 'to Archived memory' }),
    ).toBeDisabled();

    await user.click(
      within(connections).getByRole('button', { name: 'from Mentor' }),
    );
    expect(
      await screen.findByRole('dialog', { name: 'Quiet preference' }),
    ).toBeInTheDocument();
    expect(loadDetail).toHaveBeenLastCalledWith('quiet');
  });

  it('clamps long summaries behind Show more', async () => {
    const user = userEvent.setup();
    const long = 'Long memory. '.repeat(40);
    render(
      <KnowledgeHome
        {...props({
          loadDetail: vi.fn(async (id: string) => ({
            ...detail(id),
            description: long,
          })),
        })}
      />,
    );
    const inspector = await openMemory(user, 'Alpha project');
    const more = await within(inspector).findByRole('button', {
      name: 'Show more',
    });
    const summary = within(inspector).getByText(long.trim());
    expect(summary).toHaveAttribute('data-clamped', 'true');
    expect(more).toHaveAttribute('aria-expanded', 'false');
    await user.click(more);
    expect(summary).not.toHaveAttribute('data-clamped');
    expect(
      within(inspector).getByRole('button', { name: 'Show less' }),
    ).toHaveAttribute('aria-expanded', 'true');
  });

  it('ignores detail reads that finish after the selection moved on', async () => {
    const user = userEvent.setup();
    const pending: {
      resolve: (value: KnowledgeNodeDetail) => void;
      reject: (cause: Error) => void;
    }[] = [];
    const loadDetail = vi.fn((id: string) =>
      id === 'alpha'
        ? new Promise<KnowledgeNodeDetail>((resolve, reject) =>
            pending.push({ resolve, reject }),
          )
        : Promise.resolve(detail(id)),
    );
    render(<KnowledgeHome {...props({ loadDetail })} />);
    const alpha = await openMemory(user, 'Alpha project');
    expect(within(alpha).getByRole('status')).toHaveTextContent(
      'Loading memory detail…',
    );
    const quiet = await openMemory(user, 'Quiet preference');
    expect(
      await within(quiet).findByText('<img src=x onerror=sentinel()>'),
    ).toBeVisible();

    await act(async () =>
      pending[0].resolve({ ...detail('alpha'), subject: 'Stale alpha' }),
    );
    expect(
      screen.getByRole('dialog', { name: 'Quiet preference' }),
    ).toBeInTheDocument();
    expect(screen.queryByText('Stale alpha')).not.toBeInTheDocument();

    // The stale answer was not kept, so reopening reads the memory again.
    await openMemory(user, 'Alpha project');
    expect(loadDetail.mock.calls.filter(([id]) => id === 'alpha')).toHaveLength(
      2,
    );
    await openMemory(user, 'User');
    await act(async () =>
      pending[1].reject(new Error('Late failure for Alpha')),
    );
    expect(screen.getByRole('dialog', { name: 'User' })).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(
      screen.queryByText('Late failure for Alpha'),
    ).not.toBeInTheDocument();
  });

  // Known product bug (reported, not yet fixed): the refresh effect in
  // KnowledgeHome.tsx keeps the selection but discards its detail without
  // reading it again, so the inspector silently drops to snapshot-only facts
  // and "1 more connection in Settings › Memory." with no connection list.
  // Flip to `it` once the detail is read again after a refresh.
  it('keeps an open memory across a refresh with its full detail', async () => {
    const user = userEvent.setup();
    const loadDetail = vi.fn(async (id: string) => detail(id));
    const { rerender } = render(<KnowledgeHome {...props({ loadDetail })} />);
    let inspector = await openMemory(user, 'Alpha project');
    expect(await within(inspector).findByText('82%')).toBeVisible();
    expect(loadDetail).toHaveBeenCalledTimes(1);

    // A refresh (Refresh knowledge, Show all, or after Dream Cycle) delivers
    // a new snapshot that still contains the open memory, at a new revision.
    const refreshed = 'd'.repeat(64);
    rerender(
      <KnowledgeHome
        {...props({
          loadDetail,
          snapshot: {
            ...populated,
            revision: refreshed,
            nodes: populated.nodes.map((node) => ({
              ...node,
              revision: refreshed,
            })),
          },
        })}
      />,
    );
    inspector = screen.getByRole('dialog', { name: 'Alpha project' });
    expect(await within(inspector).findByText('82%')).toBeVisible();
    expect(
      within(inspector).getByRole('button', { name: /User$/ }),
    ).toBeVisible();
    // The memory changed revision, so its detail is read again.
    expect(loadDetail).toHaveBeenCalledTimes(2);
  });

  it('closes the inspector when a refresh no longer has the memory', async () => {
    const user = userEvent.setup();
    const { rerender } = render(<KnowledgeHome {...props()} />);
    await openMemory(user, 'Alpha project');
    rerender(
      <KnowledgeHome
        {...props({
          snapshot: {
            ...populated,
            revision: 'e'.repeat(64),
            nodes: populated.nodes.filter((node) => node.id !== 'alpha'),
            edges: [],
          },
        })}
      />,
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(listedMemories()).toEqual(['User', 'Quiet preference']);
  });
});

describe('Dream Cycle', () => {
  it('renders honest disabled, running, and error Dream states', () => {
    const onDream = vi.fn();
    const { rerender } = render(
      <KnowledgeHome
        {...props({
          dream: {
            available: true,
            enabled: false,
            state: 'idle',
            message: 'Enable Dream Cycle in Settings.',
          },
          onDream,
        })}
      />,
    );
    expect(
      screen.getByRole('button', { name: 'Dream disabled' }),
    ).toBeDisabled();
    expect(screen.getByText('Enable Dream Cycle in Settings.')).toBeVisible();

    rerender(
      <KnowledgeHome
        {...props({
          dream: {
            available: true,
            enabled: true,
            state: 'running',
            message: 'Dream Cycle is running.',
          },
          onDream,
        })}
      />,
    );
    expect(screen.getByRole('button', { name: 'Dreaming…' })).toBeDisabled();
    expect(screen.getByText('Dream Cycle is running.')).toHaveAttribute(
      'role',
      'status',
    );

    rerender(
      <KnowledgeHome
        {...props({
          dream: {
            available: true,
            enabled: true,
            state: 'error',
            message: 'Dream Cycle failed safely.',
          },
          onDream,
        })}
      />,
    );
    expect(screen.getByRole('button', { name: 'Retry Dream' })).toBeEnabled();
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Dream Cycle failed safely.',
    );
  });

  it('names every Dream state and explains when it last ran', () => {
    const { rerender } = render(
      <KnowledgeHome
        {...props({
          dream: { ...idleDream, available: false, enabled: false },
        })}
      />,
    );
    const button = (name: string) => screen.getByRole('button', { name });
    expect(button('Dream unavailable')).toBeDisabled();
    expect(button('Dream unavailable')).toHaveAccessibleDescription(
      'Dream Cycle is unavailable.',
    );

    rerender(
      <KnowledgeHome {...props({ dream: { ...idleDream, enabled: false } })} />,
    );
    expect(button('Dream disabled')).toHaveAccessibleDescription(
      'Dream Cycle is off in Settings.',
    );

    rerender(<KnowledgeHome {...props()} />);
    expect(button('Run Dream Cycle')).toBeEnabled();
    expect(button('Run Dream Cycle')).toHaveAccessibleDescription(
      'Dream Cycle has not run yet.',
    );

    rerender(
      <KnowledgeHome
        {...props({
          dreamLastRun: new Date(Date.now() - 7_200_000).toISOString(),
        })}
      />,
    );
    expect(button('Run Dream Cycle')).toHaveAccessibleDescription(
      /^Dream Cycle last ran .+\.$/,
    );

    rerender(
      <KnowledgeHome
        {...props({
          dream: { ...idleDream, state: 'reviewing', message: '' },
        })}
      />,
    );
    expect(button('Checking Dream Cycle…')).toBeDisabled();

    rerender(
      <KnowledgeHome
        {...props({
          dream: {
            ...idleDream,
            state: 'success',
            message: 'Dream Cycle merged 2 memories.',
          },
        })}
      />,
    );
    expect(button('Dream again')).toBeEnabled();
    expect(screen.getByText('Dream Cycle merged 2 memories.')).toHaveAttribute(
      'role',
      'status',
    );
  });

  it('runs Dream Cycle on request and reports a failure to start', async () => {
    const user = userEvent.setup();
    const onDream = vi
      .fn<() => Promise<void>>()
      .mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new Error('Dream Cycle is already running.'));
    render(<KnowledgeHome {...props({ onDream })} />);
    await user.click(screen.getByRole('button', { name: 'Run Dream Cycle' }));
    expect(onDream).toHaveBeenCalledOnce();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Run Dream Cycle' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Dream Cycle is already running.',
    );
    expect(onDream).toHaveBeenCalledTimes(2);
  });
});

function saved(
  id: string,
  subject: string,
  entity_type = 'fact',
): EntitySummary {
  return {
    id,
    entity_type,
    subject,
    description: '',
    updated_at: '2026-09-19T10:00:00Z',
    truncated: false,
    saved_state: 'saved',
    semantic_state: 'unknown',
  };
}

function savedPage(items: EntitySummary[]): EntitySummaryPage {
  return {
    schema_version: 1,
    revision: 'r'.repeat(64),
    items,
    total: items.length,
    next_cursor: null,
    availability: 'available',
  };
}

function optionTexts(select: HTMLElement) {
  return within(select)
    .getAllByRole('option')
    .map((option) => option.textContent);
}

// Everything Settings › Memory used to hold, now in Knowledge (B264).
describe('the saved library in Knowledge', () => {
  it('filters by status and memory type, with counts, and Show everything clears them', async () => {
    const user = userEvent.setup();
    render(<KnowledgeHome {...props()} />);
    const filters = await openFilters(user);
    const status = within(filters).getByRole('combobox', { name: 'Status' });
    expect(optionTexts(status)).toEqual([
      'All statuses',
      'Active · 1',
      'Needs review · 1',
      'Superseded · 0',
      'Archived · 1',
    ]);
    await user.selectOptions(status, 'needs_review');
    expect(listedMemories()).toEqual(['Alpha project']);
    await user.selectOptions(status, 'archived');
    expect(listedMemories()).toEqual(['Quiet preference']);
    await user.selectOptions(status, '');

    const tier = within(filters).getByRole('combobox', { name: 'Memory type' });
    expect(optionTexts(tier)).toEqual([
      'All memory types',
      'Core · always recalled · 1',
      'Long-term knowledge · 0',
      'From a conversation · 1',
      'From a document or media · 1',
    ]);
    await user.selectOptions(tier, 'core');
    expect(listedMemories()).toEqual(['User']);
    expect(stats()).toHaveTextContent('showing 1 of 3');
    await user.click(
      within(filters).getByRole('button', { name: 'Show everything' }),
    );
    expect(listedMemories()).toHaveLength(3);
    expect(tier).toHaveValue('');
    expect(status).toHaveValue('');
  });

  it('reviews every memory Row-Bot was unsure about: mark reviewed, edit, archive or open', async () => {
    const user = userEvent.setup();
    const onEdit = vi.fn();
    const onLifecycle = vi.fn(async () => true);
    const listMemories = vi.fn(async (query: KnowledgeMemoryQuery) =>
      savedPage(
        query.status === 'needs_review'
          ? [saved('alpha', 'Alpha project'), saved('far', 'Far memory')]
          : [],
      ),
    );
    const loadDetail = vi.fn(async (id: string) => ({
      ...detail(id),
      id,
      subject: id === 'far' ? 'Far memory' : detail(id).subject,
      revision: `${id}-revision`,
    }));
    render(
      <KnowledgeHome
        {...props({ onEdit, onLifecycle, listMemories, loadDetail })}
      />,
    );
    // The whole library's count, from the snapshot, leads to the queue.
    await user.click(
      within(stats()).getByRole('button', { name: '1 needs review' }),
    );
    expect(screen.getByRole('radio', { name: 'Review' })).toBeChecked();
    const review = screen.getByRole('region', { name: 'Needs review' });
    expect(listMemories).toHaveBeenCalledWith(
      { status: 'needs_review' },
      undefined,
      expect.any(AbortSignal),
    );
    const far = await within(review).findByRole('group', {
      name: 'Far memory',
    });
    expect(review).toHaveTextContent('2 memories Row-Bot was unsure about.');

    // Each change is reviewed at the memory's current revision.
    await user.click(
      within(far).getByRole('button', { name: 'Mark as reviewed' }),
    );
    expect(onLifecycle).toHaveBeenLastCalledWith(
      'far',
      'far-revision',
      'knowledge.resolve',
      'Far memory',
    );
    await waitFor(() => expect(listMemories).toHaveBeenCalledTimes(2));
    const alpha = within(review).getByRole('group', { name: 'Alpha project' });
    await user.click(
      within(alpha).getByRole('button', { name: 'Archive memory' }),
    );
    expect(onLifecycle).toHaveBeenLastCalledWith(
      'alpha',
      'alpha-revision',
      'knowledge.archive',
      'Alpha project',
    );
    await user.click(
      within(alpha).getByRole('button', { name: 'Edit memory' }),
    );
    expect(onEdit).toHaveBeenCalledWith('alpha');

    // A memory the map does not include still opens.
    await user.click(within(far).getByRole('button', { name: /^Far memory/ }));
    expect(
      await screen.findByRole('dialog', { name: 'Far memory' }),
    ).toBeInTheDocument();
    expect(loadDetail).toHaveBeenLastCalledWith('far');
  });

  it('says when nothing needs review', async () => {
    const user = userEvent.setup();
    render(
      <KnowledgeHome
        {...props({
          onLifecycle: vi.fn(),
          listMemories: vi.fn(async () => savedPage([])),
        })}
      />,
    );
    await user.click(screen.getByRole('radio', { name: 'Review' }));
    expect(await screen.findByText('Nothing needs review')).toBeVisible();
    // The map's legend and caption belong to Graph and List.
    expect(screen.queryByRole('group', { name: 'Memory types' })).toBeNull();
    expect(screen.queryByLabelText('Knowledge statistics')).toBeNull();
  });

  it('ticks memories in the list and deletes them together', async () => {
    const user = userEvent.setup();
    const onDeleteMany = vi
      .fn<(memories: { id: string; subject: string }[]) => Promise<boolean>>()
      .mockResolvedValueOnce(false)
      .mockResolvedValueOnce(true);
    render(<KnowledgeHome {...props({ onDeleteMany })} />);
    // Only the List view offers tick boxes.
    expect(screen.queryByRole('checkbox')).toBeNull();
    await user.click(screen.getByRole('radio', { name: 'List' }));
    await user.click(
      screen.getByRole('checkbox', { name: 'Select Alpha project' }),
    );
    await user.click(
      screen.getByRole('checkbox', { name: 'Select Quiet preference' }),
    );
    // Ticking a row does not open it.
    expect(screen.queryByRole('dialog')).toBeNull();
    const bar = screen.getByRole('toolbar', { name: 'Selected memories' });
    expect(bar).toHaveTextContent('2 selected');
    expect(screen.queryByLabelText('Knowledge statistics')).toBeNull();

    await user.click(
      within(bar).getByRole('button', { name: 'Delete selected memories' }),
    );
    expect(onDeleteMany).toHaveBeenLastCalledWith([
      { id: 'alpha', subject: 'Alpha project' },
      { id: 'quiet', subject: 'Quiet preference' },
    ]);
    // Declined: the ticks stay. Done: they clear.
    expect(
      screen.getByRole('checkbox', { name: 'Select Alpha project' }),
    ).toBeChecked();
    await user.click(
      within(bar).getByRole('button', { name: 'Delete selected memories' }),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole('toolbar', { name: 'Selected memories' }),
      ).toBeNull(),
    );
    expect(
      screen.getByRole('checkbox', { name: 'Select Alpha project' }),
    ).not.toBeChecked();

    await user.click(screen.getByRole('checkbox', { name: 'Select User' }));
    await user.click(
      within(
        screen.getByRole('toolbar', { name: 'Selected memories' }),
      ).getByRole('button', { name: 'Clear selection' }),
    );
    expect(
      screen.getByRole('checkbox', { name: 'Select User' }),
    ).not.toBeChecked();
    expect(onDeleteMany).toHaveBeenCalledTimes(2);
  });

  it('searches the whole saved library, including memories the map does not show', async () => {
    const user = userEvent.setup();
    const listMemories = vi.fn(async () =>
      savedPage([
        saved('quiet', 'Quiet preference', 'preference'),
        saved('far', 'Far memory', 'project'),
      ]),
    );
    const loadDetail = vi.fn(async (id: string) =>
      id === 'far'
        ? { ...detail('quiet'), id, subject: 'Far memory' }
        : detail(id),
    );
    render(
      <KnowledgeHome
        {...props({ listMemories, loadDetail, onLifecycle: vi.fn() })}
      />,
    );
    const search = screen.getByRole('combobox', { name: 'Search memories' });
    // An alias or tag: nothing loaded matches it by name or description.
    await user.type(search, 'nickname');
    expect(screen.getByText('Searching every memory…')).toBeInTheDocument();
    const results = await screen.findByRole('listbox', {
      name: 'Matching memories',
    });
    expect(optionTexts(results)).toEqual([
      'Quiet preferencePreference · 0',
      'Far memoryProject · not in the map',
    ]);
    // One read after typing stops, not one per key.
    expect(listMemories).toHaveBeenCalledExactlyOnceWith(
      { query: 'nickname' },
      undefined,
      expect.any(AbortSignal),
    );
    await user.keyboard('{ArrowDown}{Enter}');
    expect(
      await screen.findByRole('dialog', { name: 'Far memory' }),
    ).toBeInTheDocument();
    expect(loadDetail).toHaveBeenLastCalledWith('far');
  });

  it('shows the full record, the review reason and archive, restore and review actions', async () => {
    const user = userEvent.setup();
    const onLifecycle = vi.fn(async () => true);
    const loadDetail = vi.fn(
      async (id: string): Promise<KnowledgeNodeDetail> =>
        id === 'alpha'
          ? {
              ...detail(id),
              status: 'needs_review',
              review_reason: 'Two sources disagree',
              can_archive: true,
              can_resolve: true,
              created_at: '2026-09-10T10:00:00Z',
              last_user_modified_at: '2026-09-12T10:00:00Z',
              last_evolved_at: '2026-09-13T10:00:00Z',
              last_recalled_at: '2026-09-14T10:00:00Z',
              source_context: ['actor: extraction', 'thread name: Planning'],
              evidence: ['Said on Monday'],
              evidence_count: 3,
            }
          : id === 'quiet'
            ? {
                ...detail(id),
                status: 'superseded',
                superseded_by: 'user',
                can_archive: true,
              }
            : { ...detail(id), status: 'archived', can_restore: true },
    );
    render(<KnowledgeHome {...props({ loadDetail, onLifecycle })} />);
    let inspector = await openMemory(user, 'Alpha project');
    const note = await within(inspector).findByRole('note', {
      name: 'Needs review',
    });
    expect(note).toHaveTextContent('Two sources disagree');
    await user.click(
      within(note).getByRole('button', { name: 'Mark as reviewed' }),
    );
    expect(onLifecycle).toHaveBeenLastCalledWith(
      'alpha',
      revision,
      'knowledge.resolve',
      'Alpha project',
    );
    // The memory is read again once it changed.
    await waitFor(() =>
      expect(
        loadDetail.mock.calls.filter(([id]) => id === 'alpha'),
      ).toHaveLength(2),
    );
    await user.click(
      await within(inspector).findByRole('button', { name: 'Archive memory' }),
    );
    expect(onLifecycle).toHaveBeenLastCalledWith(
      'alpha',
      revision,
      'knowledge.archive',
      'Alpha project',
    );

    // The record waits behind Details.
    const fact = (term: string) =>
      within(inspector).getByText(term, { selector: 'dt' }).nextElementSibling;
    expect(fact('ID')).not.toBeVisible();
    await user.click(within(inspector).getByText('Details'));
    expect(fact('ID')).toHaveTextContent('alpha');
    for (const term of [
      'Created',
      'Edited by you',
      'Refined by Row-Bot',
      'Last recalled',
    ])
      expect(fact(term)?.querySelector('time')).toBeVisible();
    expect(within(inspector).getByText('thread name: Planning')).toBeVisible();
    expect(within(inspector).getByText('Said on Monday')).toBeVisible();
    expect(
      within(inspector).getByText('2 more pieces of evidence'),
    ).toBeVisible();

    // A replaced memory leads to the one that replaced it.
    inspector = await openMemory(user, 'Quiet preference');
    await user.click(
      await within(inspector).findByRole('button', {
        name: 'Open the newer memory',
      }),
    );
    inspector = await screen.findByRole('dialog', { name: 'User' });
    await user.click(
      await within(inspector).findByRole('button', { name: 'Restore memory' }),
    );
    expect(onLifecycle).toHaveBeenLastCalledWith(
      'user',
      revision,
      'knowledge.restore',
      'User',
    );
    expect(
      within(inspector).queryByRole('button', { name: 'Archive memory' }),
    ).toBeNull();
  });

  it('reads the change and recall logs only when Activity opens', async () => {
    const user = userEvent.setup();
    const loadChangeLog = vi.fn(async () => ({
      schema_version: 1 as const,
      availability: 'available' as const,
      items: [
        {
          timestamp: '2026-09-19T11:00:00Z',
          action: 'user_modified',
          actor: 'manual',
          old_status: 'needs_review',
          new_status: 'active',
          subjects: ['Alpha project'],
          additional_subjects: 2,
          reason: 'resolve_review',
        },
      ],
    }));
    const loadRecalls = vi.fn(async () => ({
      schema_version: 1 as const,
      availability: 'available' as const,
      items: [
        {
          timestamp: '2026-09-19T10:00:00Z',
          outcome: 'used' as const,
          reason: 'Relevant to the question',
          candidate_count: 2,
          selected_count: 1,
          context_characters: 120,
          candidates: [{ subject: 'Alpha project', score: 0.91 }],
          rejection_reasons: [],
        },
      ],
    }));
    render(<KnowledgeHome {...props({ loadChangeLog, loadRecalls })} />);
    expect(loadChangeLog).not.toHaveBeenCalled();
    expect(loadRecalls).not.toHaveBeenCalled();
    await user.click(screen.getByRole('radio', { name: 'Activity' }));

    const changes = screen.getByRole('region', { name: 'Memory changes' });
    expect(await within(changes).findByText('User modified')).toBeVisible();
    expect(within(changes).getByText('Alpha project +2 more')).toBeVisible();
    // Status codes read as words (U59).
    expect(
      within(changes).getByText('Status: needs review → active'),
    ).toBeVisible();
    expect(within(changes).getByText('resolve review')).toBeVisible();

    const recalls = screen.getByRole('region', { name: 'Recall decisions' });
    expect(await within(recalls).findByText('Memory used')).toBeVisible();
    expect(
      within(recalls).getByText('Candidates: Alpha project (0.91)'),
    ).toBeVisible();
    expect(within(recalls).getByText(/1 of 2 used/)).toBeVisible();
    // Times read in words, with the full date on hover (U59).
    expect(document.body.textContent).not.toMatch(/\d{4}-\d{2}-\d{2}T\d{2}:/);
    expect(loadChangeLog).toHaveBeenCalledOnce();
    expect(loadRecalls).toHaveBeenCalledOnce();
  });
});
