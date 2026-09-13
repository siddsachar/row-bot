import { act, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { ClientPlatform } from '../../platform';
import { RuntimeContext } from '../../runtime';
import TaskLibrary from './TaskLibrary';
import { createTaskEditSessions } from './task-edit-sessions';

it('the actual workflow route resumes its draft after unmount and explicit editor close', async () => {
  const state = {
    handshake: {
      client_session_id: 'session',
      instance_id: 'instance',
      server_epoch: 'epoch',
    },
  };
  const listeners = new Set<() => void>();
  const controller = {
    getSnapshot: () => state,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    savedTasks: vi.fn(async () => ({
      schema_version: 1,
      revision: 'a'.repeat(64),
      total: 0,
      items: [],
      next_cursor: null,
    })),
    command: vi.fn(),
  } as unknown as ClientController;
  const taskEditSessions = createTaskEditSessions(controller);
  const application = () => (
    <RuntimeContext.Provider
      value={{ controller, taskEditSessions, platform: {} as ClientPlatform }}
    >
      <MemoryRouter>
        <TaskLibrary />
      </MemoryRouter>
    </RuntimeContext.Provider>
  );
  const first = render(application());
  await screen.findByRole('button', { name: 'New workflow' });
  fireEvent.click(screen.getByRole('button', { name: 'New workflow' }));
  fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), {
    target: { value: 'Retained workflow draft' },
  });
  first.unmount();
  const second = render(application());
  expect(screen.getByRole('textbox', { name: 'Name' })).toHaveValue(
    'Retained workflow draft',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  await screen.findByRole('region', { name: 'Retained workflow drafts' });
  fireEvent.click(
    screen.getByRole('button', { name: 'Resume workflow: New workflow' }),
  );
  expect(screen.getByRole('textbox', { name: 'Name' })).toHaveValue(
    'Retained workflow draft',
  );
  expect(controller.command).not.toHaveBeenCalled();
  second.unmount();
  act(() => taskEditSessions.dispose());
});
