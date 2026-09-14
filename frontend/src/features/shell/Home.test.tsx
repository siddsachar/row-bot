import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import Home from './Home';

const mock = vi.hoisted(() => ({
  state: {
    status: 'ready',
    handshake: { instance_id: 'server-a', client_session_id: 'session-a' },
  },
}));

vi.mock('../../runtime', () => ({
  useClientState: () => mock.state,
}));

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
  expect(screen.getByRole('region', { name: 'Monitor' })).toBeVisible();
});

it('reports connection state without exposing client identity', () => {
  show();
  expect(screen.getByRole('status')).toHaveTextContent(
    'Connected · local workspace',
  );
  expect(document.body.textContent).not.toContain('server-a');
  expect(document.body.textContent).not.toContain('session-a');
});
