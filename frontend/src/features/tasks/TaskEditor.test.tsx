import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type {
  TaskEditableFields,
  TaskEditorSnapshot,
  TaskSaveResult,
} from '../../api/types';
import TaskEditor, { type TaskEditorProps } from './TaskEditor';
import { TaskEditSession } from './task-edit-sessions';

afterEach(cleanup);

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
const fields: TaskEditableFields = {
  name: 'Saved workflow',
  description: '',
  icon: '',
  prompts: ['First'],
  enabled: false,
  schedule: null,
  at: null,
  notify_only: false,
  notify_label: '',
  channels: null,
};
function snapshot(
  overrides: Partial<TaskEditorSnapshot> = {},
): TaskEditorSnapshot {
  return {
    id: 'task-a',
    revision: 'a'.repeat(64),
    fields: { ...fields, prompts: [...fields.prompts] },
    advanced: false,
    agent_profile_id: 'workflow-default',
    approval_mode: 'block',
    conversation_id: 'conversation-a',
    legacy_delivery: false,
    ...overrides,
  };
}
function props(overrides: Partial<TaskEditorProps> = {}): TaskEditorProps {
  return {
    load: vi.fn().mockResolvedValue(snapshot()),
    create: vi
      .fn()
      .mockResolvedValue({ task: snapshot(), created: true, replayed: false }),
    save: vi
      .fn()
      .mockResolvedValue({ task: snapshot(), created: false, replayed: false }),
    onSaved: vi.fn(),
    onCancel: vi.fn(),
    ...overrides,
  };
}
function fillNew() {
  fireEvent.change(screen.getByLabelText('Name'), {
    target: { value: 'New workflow' },
  });
  fireEvent.change(screen.getByLabelText('Prompt 1'), {
    target: { value: 'Summarize fake data' },
  });
}

it('retains a new draft and settles create after an actual unmount without creating again', async () => {
  const session = new TaskEditSession('task');
  const response = deferred<TaskSaveResult>();
  const callbacks = props({
    session,
    create: vi.fn().mockReturnValue(response.promise),
  });
  const first = render(<TaskEditor {...callbacks} />);
  fillNew();
  first.unmount();
  const second = render(<TaskEditor {...callbacks} />);
  expect(screen.getByLabelText('Name')).toHaveValue('New workflow');
  expect(screen.getByLabelText('Prompt 1')).toHaveValue('Summarize fake data');
  fireEvent.click(screen.getByRole('button', { name: 'Save task' }));
  second.unmount();
  const third = render(<TaskEditor {...callbacks} />);
  expect(screen.getByLabelText('Name')).toBeDisabled();
  third.unmount();
  await act(async () =>
    response.resolve({ task: snapshot(), created: true, replayed: false }),
  );
  render(<TaskEditor {...callbacks} />);
  expect(screen.getByLabelText('Name')).toHaveValue('Saved workflow');
  expect(
    screen.getByText('Saved task. No workflow was run.'),
  ).toBeInTheDocument();
  expect(callbacks.create).toHaveBeenCalledOnce();
  expect(session.retained()).toBe(false);
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save task' })),
  );
  expect(callbacks.create).toHaveBeenCalledOnce();
  expect(callbacks.save).toHaveBeenCalledWith(
    'task-a',
    snapshot().revision,
    snapshot().fields,
  );
});

it('creates a disabled workflow using inherited delivery, without run controls', async () => {
  const callbacks = props();
  render(<TaskEditor {...callbacks} />);
  fillNew();
  expect(screen.getByRole('checkbox', { name: /^Enabled/ })).not.toBeChecked();
  expect(
    screen.queryByRole('button', { name: /^run/i }),
  ).not.toBeInTheDocument();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save task' })),
  );
  expect(callbacks.create).toHaveBeenCalledWith(
    expect.objectContaining({
      name: 'New workflow',
      prompts: ['Summarize fake data'],
      channels: null,
      enabled: false,
    }),
  );
  expect(callbacks.onSaved).toHaveBeenCalledWith(snapshot());
});

it('preserves [] versus inherited delivery and never invents a selected channel', async () => {
  const callbacks = props();
  render(<TaskEditor {...callbacks} />);
  fillNew();
  fireEvent.change(screen.getByRole('combobox', { name: /^Delivery/ }), {
    target: { value: 'selected' },
  });
  expect(screen.getByLabelText('Channel names', { exact: false })).toHaveValue(
    '',
  );
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save task' })),
  );
  expect(callbacks.create).not.toHaveBeenCalled();
  expect(screen.getByRole('alert')).toHaveTextContent(
    'at least one registered channel',
  );
  fireEvent.change(screen.getByRole('combobox', { name: /^Delivery/ }), {
    target: { value: 'app' },
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save task' })),
  );
  expect(callbacks.create).toHaveBeenCalledWith(
    expect.objectContaining({ channels: [] }),
  );
});

it('preserves prompt order and exact full-row revision on update', async () => {
  const callbacks = props({
    taskId: 'task-a',
    load: vi.fn().mockResolvedValue(
      snapshot({
        fields: {
          ...fields,
          prompts: ['First', 'Second'],
          channels: ['slack'],
        },
      }),
    ),
  });
  render(<TaskEditor {...callbacks} />);
  await screen.findByDisplayValue('Saved workflow');
  fireEvent.click(screen.getByRole('button', { name: 'Move prompt 2 up' }));
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save task' })),
  );
  expect(callbacks.save).toHaveBeenCalledWith(
    'task-a',
    'a'.repeat(64),
    expect.objectContaining({
      prompts: ['Second', 'First'],
      channels: ['slack'],
    }),
  );
});

it('creates a reminder from a blank initial prompt without fabricating a workflow step', async () => {
  const callbacks = props();
  render(<TaskEditor {...callbacks} />);
  fireEvent.change(screen.getByLabelText('Name'), {
    target: { value: 'Reminder' },
  });
  fireEvent.change(screen.getByLabelText('Task type'), {
    target: { value: 'reminder' },
  });
  fireEvent.change(screen.getByLabelText('Reminder text'), {
    target: { value: 'Review synthetic result' },
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save task' })),
  );
  expect(callbacks.create).toHaveBeenCalledWith(
    expect.objectContaining({
      prompts: [],
      notify_only: true,
      notify_label: 'Review synthetic result',
    }),
  );
});

it('submits once while saving and keeps cancellation from abandoning an in-flight result', async () => {
  const operation = deferred<TaskSaveResult>();
  const callbacks = props({
    create: vi.fn().mockReturnValue(operation.promise),
  });
  render(<TaskEditor {...callbacks} />);
  fillNew();
  const form = screen.getByRole('form', { name: 'Create task' });
  fireEvent.submit(form);
  fireEvent.submit(form);
  expect(callbacks.create).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
  expect(screen.getByRole('status')).toHaveTextContent('confirmed outcome');
  await act(async () =>
    operation.resolve({ task: snapshot(), created: true, replayed: false }),
  );
  expect(callbacks.onSaved).toHaveBeenCalledTimes(1);
});

it('cancels a draft without a save or run', () => {
  const callbacks = props();
  render(<TaskEditor {...callbacks} />);
  fillNew();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(callbacks.onCancel).toHaveBeenCalledOnce();
  expect(callbacks.create).not.toHaveBeenCalled();
});

it('aborts an old detail load and ignores its late resolution after switching tasks', async () => {
  const first = deferred<TaskEditorSnapshot>();
  const second = deferred<TaskEditorSnapshot>();
  const load = vi
    .fn()
    .mockReturnValueOnce(first.promise)
    .mockReturnValueOnce(second.promise);
  const callbacks = props({ taskId: 'task-a', load });
  const view = render(<TaskEditor {...callbacks} />);
  const signal = load.mock.calls[0][1] as AbortSignal;
  view.rerender(<TaskEditor {...callbacks} taskId="task-b" />);
  expect(signal.aborted).toBe(true);
  await act(async () =>
    second.resolve(
      snapshot({ id: 'task-b', fields: { ...fields, name: 'Second task' } }),
    ),
  );
  await act(async () => first.resolve(snapshot()));
  expect(screen.getByLabelText('Name')).toHaveValue('Second task');
});

it('ignores a completed save callback after the editor unmounts', async () => {
  const operation = deferred<TaskSaveResult>();
  const callbacks = props({
    create: vi.fn().mockReturnValue(operation.promise),
  });
  const view = render(<TaskEditor {...callbacks} />);
  fillNew();
  fireEvent.submit(screen.getByRole('form', { name: 'Create task' }));
  view.unmount();
  await act(async () =>
    operation.resolve({ task: snapshot(), created: true, replayed: false }),
  );
  expect(callbacks.onSaved).not.toHaveBeenCalled();
});

it('retains user edits on conflict and requires reload before another save', async () => {
  const callbacks = props({
    taskId: 'task-a',
    save: vi.fn().mockRejectedValue({ code: 'task_revision_conflict' }),
  });
  render(<TaskEditor {...callbacks} />);
  await screen.findByDisplayValue('Saved workflow');
  fireEvent.change(screen.getByLabelText('Name'), {
    target: { value: 'My unsaved name' },
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save task' })),
  );
  expect(screen.getByLabelText('Name')).toHaveValue('My unsaved name');
  expect(screen.getByRole('button', { name: 'Save task' })).toBeDisabled();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Reload saved task' })),
  );
  expect(screen.getByLabelText('Name')).toHaveValue('Saved workflow');
  expect(screen.getByRole('button', { name: 'Save task' })).toBeEnabled();
});

it('does not reveal arbitrary errors and preserves the draft for recovery', async () => {
  const callbacks = props({
    create: vi.fn().mockRejectedValue(new Error('PRIVATE_PATH_SECRET')),
  });
  render(<TaskEditor {...callbacks} />);
  fillNew();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save task' })),
  );
  expect(screen.getByRole('alert')).not.toHaveTextContent(
    'PRIVATE_PATH_SECRET',
  );
  expect(screen.getByLabelText('Name')).toHaveValue('New workflow');
});

it('preserves advanced graph and legacy destination settings as readonly', async () => {
  const callbacks = props({
    taskId: 'task-a',
    load: vi
      .fn()
      .mockResolvedValue(snapshot({ advanced: true, legacy_delivery: true })),
  });
  render(<TaskEditor {...callbacks} />);
  await screen.findByDisplayValue('Saved workflow');
  expect(screen.getByLabelText('Prompt 1')).toHaveAttribute('readonly');
  expect(screen.getByLabelText('Task type')).toBeDisabled();
  expect(screen.getByRole('combobox', { name: /^Delivery/ })).toBeDisabled();
  expect(
    screen.queryByRole('button', { name: 'Add prompt' }),
  ).not.toBeInTheDocument();
  expect(screen.getByText(/Agent profile:/)).toHaveTextContent(
    'Approval policy: block',
  );
});

it('uses mutually exclusive recurring and local one-shot schedule fields', async () => {
  const callbacks = props();
  render(<TaskEditor {...callbacks} />);
  fillNew();
  fireEvent.change(screen.getByLabelText('Schedule'), {
    target: { value: 'recurring' },
  });
  expect(
    screen.getByLabelText('Recurring schedule', { exact: false }),
  ).toHaveValue('daily:09:00');
  fireEvent.change(screen.getByLabelText('Schedule'), {
    target: { value: 'once' },
  });
  fireEvent.change(screen.getByLabelText('Date and time', { exact: false }), {
    target: { value: '2027-01-01T10:00' },
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save task' })),
  );
  expect(callbacks.create).toHaveBeenCalledWith(
    expect.objectContaining({ at: '2027-01-01T10:00', schedule: null }),
  );
});
