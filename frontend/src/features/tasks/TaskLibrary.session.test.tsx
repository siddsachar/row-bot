import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type {
  TaskRunPage,
  TaskRunReview,
  TaskSummary,
  TaskSummaryPage,
} from '../../api/types';
import type { ClientPlatform } from '../../platform';
import { RuntimeContext } from '../../runtime';
import { OverlayProvider } from '../../ui/overlays';
import TaskLibrary from './TaskLibrary';
import { createTaskEditSessions } from './task-edit-sessions';

function LocationProbe() {
  const location = useLocation();
  return (
    <output data-testid="location">
      {location.pathname}
      {location.search}
    </output>
  );
}

/** The part of the task editor snapshot the run drawer reads. */
type EditorSnapshot = { revision: string; fields: { name: string } };
function editorSnapshot(name: string): EditorSnapshot {
  return { revision: 'f'.repeat(64), fields: { name } };
}

function pending<T>() {
  let resolve!: (value: T) => void;
  let reject!: (cause: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function harness(items: TaskSummary[] = []) {
  const state = {
    handshake: {
      client_session_id: 'session',
      instance_id: 'instance',
      server_epoch: 'epoch',
    },
  };
  const listeners = new Set<() => void>();
  const savedTasks = vi.fn(async (): Promise<TaskSummaryPage> => ({
    schema_version: 1,
    revision: 'a'.repeat(64),
    total: items.length,
    items,
    next_cursor: null,
  }));
  const taskRunReview = vi.fn(
    async (taskId: string): Promise<TaskRunReview> => ({
      task_id: taskId,
      task_revision: 'c'.repeat(64),
      policy_revision: 'd'.repeat(64),
      agent_profile_id: '',
      approval_mode: 'approve',
      notify_only: false,
      steps_total: 2,
      conversation_id: null,
    }),
  );
  const taskRuns = vi.fn(async (taskId: string): Promise<TaskRunPage> => ({
    task_id: taskId,
    revision: 'e'.repeat(64),
    items: [],
    next_cursor: null,
    total: 0,
  }));
  // The run drawer reads the task only to name itself.
  const taskEditor = vi.fn(async (taskId: string): Promise<EditorSnapshot> =>
    editorSnapshot(`Workflow ${taskId}`),
  );
  const controller = {
    getSnapshot: () => state,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    savedTasks,
    taskDeliveryDefaults: vi.fn(async () => ({
      schema_version: 1,
      revision: 'b'.repeat(64),
      channels: [],
      web_app_always_on: true,
    })),
    taskRunReview,
    taskRuns,
    taskApprovals: vi.fn(),
    taskEditor,
    command: vi.fn(),
  } as unknown as ClientController;
  const taskEditSessions = createTaskEditSessions(controller);
  const application = (entry = '/') => (
    <RuntimeContext.Provider
      value={{ controller, taskEditSessions, platform: {} as ClientPlatform }}
    >
      <MemoryRouter initialEntries={[entry]}>
        <OverlayProvider>
          <TaskLibrary />
          <LocationProbe />
        </OverlayProvider>
      </MemoryRouter>
    </RuntimeContext.Provider>
  );
  return {
    controller,
    taskEditSessions,
    application,
    savedTasks,
    taskRunReview,
    taskRuns,
    taskEditor,
  };
}

it('the actual workflow route resumes its draft after unmount and explicit editor close', async () => {
  const { controller, taskEditSessions, application } = harness();
  const first = render(application());
  await screen.findByRole('button', { name: 'New workflow' });
  // The saved list is loaded before the builder opens over it.
  await screen.findByText('No workflows yet');
  fireEvent.click(screen.getByRole('button', { name: 'New workflow' }));
  expect(
    screen.getByRole('dialog', { name: 'New task/workflow' }),
  ).toBeVisible();
  // The library stays mounted behind the builder rather than being replaced.
  expect(document.querySelector('.workflow-library')).toBeInTheDocument();
  expect(screen.getByText('No workflows yet')).toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), {
    target: { value: 'Retained workflow draft' },
  });
  first.unmount();
  const second = render(application());
  expect(screen.getByRole('textbox', { name: 'Name' })).toHaveValue(
    'Retained workflow draft',
  );
  expect(
    screen.getByRole('dialog', { name: 'New task/workflow' }),
  ).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  await screen.findByRole('region', { name: 'Continue editing workflows' });
  expect(
    screen.getByText('New workflow', { selector: 'strong' }),
  ).toBeVisible();
  expect(screen.queryByText(/session|\["task"/i)).toBeNull();
  const resume = screen.getByRole('button', { name: 'Continue editing' });
  await userEvent.click(resume);
  expect(screen.getByRole('textbox', { name: 'Name' })).toHaveValue(
    'Retained workflow draft',
  );
  // The draft in the editor is not also offered behind it.
  expect(
    screen.queryByRole('region', { name: 'Continue editing workflows' }),
  ).toBeNull();
  await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(
    screen.queryByRole('dialog', { name: 'New task/workflow' }),
  ).toBeNull();
  expect(resume).toHaveFocus();
  expect(controller.command).not.toHaveBeenCalled();
  second.unmount();
  act(() => taskEditSessions.dispose());
});

it('opens the run drawer for a ?workflow= link, names it, and clears only the link', async () => {
  const {
    controller,
    taskEditSessions,
    application,
    savedTasks,
    taskRunReview,
    taskRuns,
    taskEditor,
  } = harness();
  const read = pending<EditorSnapshot>();
  taskEditor.mockReturnValueOnce(read.promise);
  const view = render(
    application('/?tab=workflows&workflow=task-7&from=overview'),
  );
  // The link carries only the id: a generic title until the task is read.
  const drawer = await screen.findByRole('dialog', { name: 'Workflow runs' });
  expect(drawer).toBeVisible();
  expect(taskEditor).toHaveBeenCalledOnce();
  expect(taskEditor).toHaveBeenCalledWith('task-7');
  expect(
    await screen.findByRole('button', { name: 'Run now' }),
  ).toBeInTheDocument();
  expect(taskRunReview).toHaveBeenCalledWith('task-7', expect.any(AbortSignal));
  expect(taskRuns).toHaveBeenCalledWith(
    'task-7',
    undefined,
    expect.any(AbortSignal),
  );
  // The link is consumed so a reload or Back does not reopen the drawer, and
  // the rest of the address is kept.
  await waitFor(() =>
    expect(screen.getByTestId('location')).toHaveTextContent(
      /^\/\?tab=workflows&from=overview$/,
    ),
  );
  await act(async () => read.resolve(editorSnapshot('Morning digest')));
  expect(screen.getByRole('dialog', { name: 'Morning digest' })).toBe(drawer);
  expect(taskEditor).toHaveBeenCalledOnce();
  const loads = savedTasks.mock.calls.length;
  await userEvent.click(
    screen.getByRole('button', { name: 'Close workflow runs' }),
  );
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  // Closing the drawer refreshes the list so new run facts are shown.
  await waitFor(() => expect(savedTasks).toHaveBeenCalledTimes(loads + 1));
  expect(controller.command).not.toHaveBeenCalled();
  view.unmount();
  act(() => taskEditSessions.dispose());
});

it('keeps the generic title when the task cannot be read, and never reopens a closed drawer', async () => {
  const { taskEditSessions, application, taskEditor } = harness();
  taskEditor.mockRejectedValueOnce(new Error('task_not_found'));
  let view = render(application('/?tab=workflows&workflow=task-7'));
  const drawer = await screen.findByRole('dialog', { name: 'Workflow runs' });
  await act(async () => {});
  expect(taskEditor).toHaveBeenCalledWith('task-7');
  expect(screen.getByRole('dialog', { name: 'Workflow runs' })).toBe(drawer);
  view.unmount();

  // A name that arrives after the drawer was closed does not reopen it.
  const late = pending<EditorSnapshot>();
  taskEditor.mockReturnValueOnce(late.promise);
  view = render(application('/?tab=workflows&workflow=task-8'));
  await screen.findByRole('dialog', { name: 'Workflow runs' });
  await userEvent.click(
    screen.getByRole('button', { name: 'Close workflow runs' }),
  );
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  await act(async () => late.resolve(editorSnapshot('Late digest')));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.queryByText('Late digest')).not.toBeInTheDocument();
  view.unmount();
  act(() => taskEditSessions.dispose());
});

it('Run on a row reviews in place first, without starting a run (U40)', async () => {
  const { controller, taskEditSessions, application, taskRunReview } = harness([
    {
      id: 'task-1',
      name: 'Morning digest',
      description: '',
      icon: '',
      enabled: false,
      notify_only: false,
      step_count: 2,
      schedule: 'daily:08:00',
      at: null,
      last_run: null,
      last_status: null,
      conversation_id: null,
    },
  ]);
  const view = render(application('/?tab=workflows'));
  await userEvent.click(
    await screen.findByRole('button', { name: 'Run workflow: Morning digest' }),
  );
  const line = await screen.findByRole('group', {
    name: 'Run Morning digest now?',
  });
  expect(within(line).getByRole('button', { name: 'Run' })).toBeInTheDocument();
  expect(taskRunReview).toHaveBeenCalledWith('task-1');
  expect(screen.queryByRole('dialog', { name: 'Morning digest' })).toBeNull();
  expect(screen.getByTestId('location')).toHaveTextContent(
    /^\/\?tab=workflows$/,
  );
  expect(controller.command).not.toHaveBeenCalled();
  view.unmount();
  act(() => taskEditSessions.dispose());
});
