import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import KnowledgeHome, {
  type KnowledgeDreamState,
  type KnowledgeGraphSnapshot,
  type KnowledgeNodeDetail,
} from './KnowledgeHome';

const revision = 'a'.repeat(64);

afterEach(() => {
  delete window.vis;
  vi.unstubAllGlobals();
});

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
      source: 'system',
      updated_at: '2026-09-19T10:00:00Z',
      relation_count: 1,
      orphan: false,
      is_user: true,
    },
    {
      id: 'alpha',
      revision,
      subject: 'Alpha project',
      description: 'A saved project fact',
      entity_type: 'fact',
      source: 'chat',
      updated_at: '2026-09-18T10:00:00Z',
      relation_count: 1,
      orphan: false,
      is_user: false,
    },
    {
      id: 'quiet',
      revision,
      subject: 'Quiet preference',
      description: 'An isolated document memory',
      entity_type: 'preference',
      source: 'document:notes.txt',
      updated_at: '2026-05-01T10:00:00Z',
      relation_count: 0,
      orphan: true,
      is_user: false,
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
  sources: ['chat', 'document:notes.txt', 'system'],
};

const idleDream: KnowledgeDreamState = {
  available: true,
  enabled: true,
  state: 'idle',
  message: '',
};

function detail(id = 'alpha'): KnowledgeNodeDetail {
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

it('renders the zero-entity memory-map empty state', () => {
  render(
    <KnowledgeHome
      {...props({
        snapshot: {
          ...populated,
          nodes: [],
          edges: [],
          total_entities: 0,
          total_relations: 0,
          shown_entities: 0,
          shown_relations: 0,
          center_id: null,
        },
      })}
    />,
  );
  expect(
    screen.getByRole('heading', { name: 'Your memory map is empty' }),
  ).toBeVisible();
  expect(screen.queryByRole('searchbox')).not.toBeInTheDocument();
});

it('filters populated topology by search, type, source, User hub, and orphan state', async () => {
  const user = userEvent.setup();
  const { container } = render(<KnowledgeHome {...props()} />);
  expect(container.querySelectorAll('.knowledge-graph-node')).toHaveLength(3);
  expect(container.querySelectorAll('.knowledge-graph-edge')).toHaveLength(1);

  await user.type(
    screen.getByRole('searchbox', { name: 'Search entities' }),
    'project',
  );
  expect(container.querySelectorAll('.knowledge-graph-node')).toHaveLength(1);
  await user.clear(screen.getByRole('searchbox', { name: 'Search entities' }));
  await user.selectOptions(
    screen.getByRole('combobox', { name: 'Entity type' }),
    'preference',
  );
  expect(container.querySelectorAll('.knowledge-graph-node')).toHaveLength(1);
  await user.selectOptions(
    screen.getByRole('combobox', { name: 'Entity type' }),
    '',
  );
  await user.selectOptions(
    screen.getByRole('combobox', { name: 'Source' }),
    'chat',
  );
  expect(container.querySelectorAll('.knowledge-graph-node')).toHaveLength(1);
  await user.selectOptions(
    screen.getByRole('combobox', { name: 'Source' }),
    '',
  );
  await user.click(screen.getByRole('checkbox', { name: 'User hub' }));
  expect(container.querySelectorAll('.knowledge-graph-node')).toHaveLength(2);
  await user.click(screen.getByRole('checkbox', { name: 'Hide orphans' }));
  expect(container.querySelectorAll('.knowledge-graph-node')).toHaveLength(1);
});

it('supports keyboard node selection and the semantic list/detail fallback', async () => {
  const user = userEvent.setup();
  const loadDetail = vi.fn(async (id: string) => detail(id));
  render(<KnowledgeHome {...props({ loadDetail })} />);
  const graphNode = screen.getByRole('button', {
    name: 'Alpha project, fact, 1 connections',
  });
  graphNode.focus();
  await user.keyboard('{Enter}');
  expect(
    await screen.findByText('<img src=x onerror=sentinel()>'),
  ).toBeVisible();
  expect(loadDetail).toHaveBeenCalledWith('alpha');

  await user.click(screen.getByRole('button', { name: 'Accessible list' }));
  const list = screen.getByRole('list', { name: 'Knowledge entities' });
  const item = within(list).getByRole('button', { name: /Quiet preference/ });
  item.focus();
  await user.keyboard('{Enter}');
  expect(loadDetail).toHaveBeenLastCalledWith('quiet');
  expect(
    screen.getByRole('button', { name: 'Accessible list' }),
  ).toHaveAttribute('aria-pressed', 'true');
});

it('fits the visible graph and Show All restores every filter and toggle', async () => {
  const user = userEvent.setup();
  const { container } = render(<KnowledgeHome {...props()} />);
  await user.type(screen.getByRole('searchbox'), 'project');
  await user.click(screen.getByRole('checkbox', { name: 'User hub' }));
  await user.click(screen.getByRole('checkbox', { name: 'Hide orphans' }));
  await user.click(screen.getByRole('button', { name: 'Fit' }));
  expect(screen.getByText('Graph fitted to 1 memory.')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Show All' }));
  expect(screen.getByRole('searchbox')).toHaveValue('');
  expect(screen.getByRole('checkbox', { name: 'User hub' })).toBeChecked();
  expect(
    screen.getByRole('checkbox', { name: 'Hide orphans' }),
  ).not.toBeChecked();
  expect(container.querySelectorAll('.knowledge-graph-node')).toHaveLength(3);
});

it('uses the local vis runtime with bounded reduced-motion navigation', async () => {
  const instances: Array<{
    options: Record<string, unknown>;
    fit: ReturnType<typeof vi.fn>;
    moveTo: ReturnType<typeof vi.fn>;
    destroy: ReturnType<typeof vi.fn>;
  }> = [];
  class DataSet {
    constructor(readonly items: unknown[]) {}
  }
  class Network {
    fit = vi.fn();
    moveTo = vi.fn();
    destroy = vi.fn();
    redraw = vi.fn();
    selectNodes = vi.fn();
    getScale = vi.fn(() => 1);
    on = vi.fn();
    constructor(
      _root: HTMLElement,
      _data: Record<string, unknown>,
      readonly options: Record<string, unknown>,
    ) {
      instances.push(this);
    }
  }
  window.vis = { DataSet, Network };
  vi.stubGlobal('matchMedia', () => ({ matches: true }));
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    },
  );
  render(<KnowledgeHome {...props()} />);
  await waitFor(() =>
    expect(document.querySelector('.knowledge-network-shell')).toHaveAttribute(
      'data-renderer-status',
      'ready',
    ),
  );
  expect(instances).toHaveLength(1);
  expect(instances[0].options).toMatchObject({
    physics: false,
    interaction: {
      keyboard: { enabled: true, bindToWindow: false },
    },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }));
  expect(instances[0].moveTo).toHaveBeenCalledWith({
    scale: 1.2,
    animation: false,
  });
  fireEvent.click(screen.getByRole('button', { name: 'Fit' }));
  expect(instances[0].fit).toHaveBeenCalledWith({ animation: false });
});

it('loads escaped rich detail, exposes edit, and recovers from detail errors', async () => {
  const user = userEvent.setup();
  const onEdit = vi.fn();
  const loadDetail = vi.fn(async (id: string) => {
    if (id === 'quiet') throw new Error('Detail service is unavailable');
    return detail(id);
  });
  const { container } = render(
    <KnowledgeHome {...props({ loadDetail, onEdit })} />,
  );
  await user.click(
    screen.getByRole('button', { name: 'Alpha project, fact, 1 connections' }),
  );
  expect(
    await screen.findByText('<img src=x onerror=sentinel()>'),
  ).toBeVisible();
  expect(container.querySelector('img')).toBeNull();
  expect(screen.getByText('82%')).toBeVisible();
  expect(screen.getByText('owned_by: User')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Edit' }));
  expect(onEdit).toHaveBeenCalledWith('alpha');

  fireEvent.click(
    screen.getByRole('button', {
      name: 'Quiet preference, preference, 0 connections',
    }),
  );
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Detail service is unavailable',
  );
});

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
  expect(screen.getByRole('button', { name: 'Dream disabled' })).toBeDisabled();
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
