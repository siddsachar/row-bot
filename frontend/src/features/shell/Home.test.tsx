import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import Home from './Home';

const mock = vi.hoisted(() => ({
  state: {
    status: 'ready',
    handshake: { instance_id: 'server-a', client_session_id: 'session-a' },
  },
  controller: {
    knowledgeGraph: vi.fn().mockResolvedValue({
      schema_version: 1,
      availability: 'available',
      revision: 'a'.repeat(64),
      nodes: [],
      edges: [],
      total_entities: 0,
      total_relations: 0,
      shown_entities: 0,
      shown_relations: 0,
      truncated: false,
      center_id: null,
      entity_types: [],
      sources: [],
    }),
    monitorSnapshot: vi.fn().mockResolvedValue({
      schema_version: 1,
      dream_revision: 'b'.repeat(64),
      extraction: {
        availability: 'available',
        last_run: null,
        interval_hours: 2,
        threads_scanned: 0,
        entities_saved: 0,
        islands_repaired: 0,
      },
      extraction_journal: [],
      extraction_journal_availability: 'missing',
      dream: {
        availability: 'available',
        enabled: true,
        window: '1:00 – 5:00',
        last_run: null,
        last_summary: null,
        recent: [],
      },
      dream_journal: [],
      dream_journal_availability: 'missing',
      logs: {
        availability: 'unavailable',
        authorized: false,
        entries: [],
        full_available: false,
      },
    }),
    knowledgeEntityDetail: vi.fn(),
    reviewDreamRun: vi.fn(),
    executeDreamRun: vi.fn(),
    monitorLogs: vi.fn(),
  },
}));

vi.mock('../../runtime', () => ({
  useClientState: () => mock.state,
  useRuntime: () => ({ controller: mock.controller }),
}));

vi.mock('../../ui/overlays', async (load) => {
  const actual = await load<typeof import('../../ui/overlays')>();
  return { ...actual, useOverlay: () => ({ open: vi.fn() }) };
});

vi.mock('../tasks/TaskLibrary', () => ({
  default: () => (
    <section aria-label="Workflow library">Workflow owner</section>
  ),
}));

function show(path = '/') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Home />
    </MemoryRouter>,
  );
}

function chooseTab(name: string) {
  fireEvent.mouseDown(screen.getByRole('tab', { name }), {
    button: 0,
    ctrlKey: false,
  });
}

beforeEach(() => {
  mock.state.status = 'ready';
  mock.state.handshake = {
    instance_id: 'server-a',
    client_session_id: 'session-a',
  };
});

it('opens on Workflows and leaves conversations and pane-backed resources out of Home', () => {
  show();
  expect(screen.getByRole('tab', { name: 'Workflows' })).toHaveAttribute(
    'data-state',
    'active',
  );
  expect(
    screen.getByRole('region', { name: 'Workflow library' }),
  ).toBeVisible();
  expect(screen.queryByText('Recent conversations')).toBeNull();
  expect(
    screen.queryByRole('button', { name: /Search all conversations/ }),
  ).toBeNull();
  expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
    'Workflows',
    'Knowledge',
    'Monitor',
  ]);
  expect(screen.queryByRole('tab', { name: 'Designer' })).toBeNull();
  expect(screen.queryByRole('tab', { name: 'Developer' })).toBeNull();
});

it('uses the explicit one-shot workflow deep-link intent without persistent last-tab state', () => {
  show('/?tab=workflows');
  expect(screen.getByRole('tab', { name: 'Workflows' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
});

it('keeps Knowledge and Monitor as passive, truthful boundaries', () => {
  show();
  chooseTab('Knowledge');
  expect(screen.getByRole('region', { name: 'Knowledge' })).toBeVisible();
  chooseTab('Monitor');
  expect(screen.getByRole('region', { name: 'System Monitor' })).toBeVisible();
});

it('reports connection state without exposing client identity', () => {
  show();
  expect(screen.getByRole('status')).toHaveTextContent(
    'Connected · local workspace',
  );
  expect(document.body.textContent).not.toContain('server-a');
  expect(document.body.textContent).not.toContain('session-a');
});
