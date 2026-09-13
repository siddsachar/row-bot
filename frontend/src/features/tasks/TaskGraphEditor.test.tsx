import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import type { TaskGraphFields, TaskGraphSnapshot } from '../../api/types';
import TaskGraphEditor, { type TaskGraphEditorProps } from './TaskGraphEditor';
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
const fields: TaskGraphFields = {
  prompt: 'Saved prompt',
  condition: null,
  message: null,
  next: null,
  if_true: null,
  if_false: null,
  if_approved: null,
  if_denied: null,
  on_error: null,
  task_id: null,
  channel: null,
  objective: null,
  profile: null,
  developer_workspace_id: null,
  editing_safety: null,
  return_mode: null,
  context: null,
  max_retries: null,
  retry_delay_seconds: null,
  timeout_minutes: null,
  timeout_seconds: null,
  pass_output: null,
  run_ids: null,
};
function snapshot(
  overrides: Partial<TaskGraphSnapshot> = {},
): TaskGraphSnapshot {
  return {
    task_id: 'task-a',
    revision: 'a'.repeat(64),
    notify_only: false,
    steps: [
      {
        id: 'draft',
        type: 'prompt',
        fields: { ...fields },
        editable: true,
        retained_fields: true,
      },
      {
        id: 'review',
        type: 'approval',
        fields: {
          ...fields,
          prompt: null,
          message: 'Review {{step.draft.output}}',
          if_approved: 'end',
          if_denied: 'draft',
          timeout_minutes: 0,
        },
        editable: true,
        retained_fields: false,
      },
    ],
    ...overrides,
  };
}
function props(
  overrides: Partial<TaskGraphEditorProps> = {},
): TaskGraphEditorProps {
  return {
    taskId: 'task-a',
    load: vi.fn().mockResolvedValue(snapshot()),
    save: vi.fn().mockResolvedValue(snapshot()),
    onSaved: vi.fn(),
    onCancel: vi.fn(),
    ...overrides,
  };
}

it('retains the actual graph draft across remount and locks uncertain fields until original retry settles', async () => {
  const session = new TaskEditSession('graph', 'task-a');
  const callbacks = props({
    session,
    save: vi
      .fn()
      .mockRejectedValueOnce(new TypeError('lost'))
      .mockResolvedValue(snapshot()),
  });
  const first = render(<TaskGraphEditor {...callbacks} />);
  fireEvent.change(await screen.findByLabelText('Prompt'), {
    target: { value: 'Retained graph draft' },
  });
  first.unmount();
  const second = render(<TaskGraphEditor {...callbacks} />);
  expect(await screen.findByLabelText('Prompt')).toHaveValue(
    'Retained graph draft',
  );
  expect(callbacks.load).toHaveBeenCalledOnce();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save graph' })),
  );
  expect(screen.getByLabelText('Prompt')).toBeDisabled();
  expect(
    screen.getByRole('button', { name: 'Reload saved graph' }),
  ).toBeDisabled();
  second.unmount();
  render(<TaskGraphEditor {...callbacks} />);
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'Retry original graph save' }),
    ),
  );
  expect(vi.mocked(callbacks.save).mock.calls[1]).toEqual(
    vi.mocked(callbacks.save).mock.calls[0],
  );
  expect(session.retained()).toBe(false);
});

it('loads existing fields and saves exact reviewed revision without running', async () => {
  const callbacks = props();
  render(<TaskGraphEditor {...callbacks} />);
  await screen.findByLabelText('Prompt');
  expect(callbacks.save).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Prompt'), {
    target: { value: 'Edited' },
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save graph' })),
  );
  expect(callbacks.save).toHaveBeenCalledWith('task-a', 'a'.repeat(64), [
    { id: 'draft', type: 'prompt', fields: { ...fields, prompt: 'Edited' } },
    { id: 'review', type: 'approval', fields: snapshot().steps[1].fields },
  ]);
  expect(callbacks.onSaved).toHaveBeenCalledWith(snapshot());
  expect(
    screen.queryByRole('button', { name: /^Run$/ }),
  ).not.toBeInTheDocument();
});

it('keyboard reorder preserves stable IDs and named output references', async () => {
  const callbacks = props();
  render(<TaskGraphEditor {...callbacks} />);
  await screen.findByLabelText('Prompt');
  screen.getByRole('button', { name: 'Move step down' }).focus();
  await userEvent.keyboard('{Enter}');
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save graph' })),
  );
  const saved = vi.mocked(callbacks.save).mock.calls[0][2];
  expect(saved.map((step) => step.id)).toEqual(['review', 'draft']);
  expect(saved[0].fields.message).toBe('Review {{step.draft.output}}');
  expect(saved[0].fields.if_denied).toBe('draft');
});

it('adds every retained type and edits branching without evaluation', async () => {
  const callbacks = props();
  render(<TaskGraphEditor {...callbacks} />);
  await screen.findByLabelText('Prompt');
  for (const kind of [
    'condition',
    'approval',
    'subtask',
    'delegate_agent',
    'wait_for_agents',
    'notify',
  ]) {
    fireEvent.change(screen.getByLabelText('New step type'), {
      target: { value: kind },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add step' }));
    expect(screen.getByLabelText(/Step type/)).toHaveValue(kind);
  }
  fireEvent.change(screen.getByLabelText(/^Step type/), {
    target: { value: 'condition' },
  });
  fireEvent.change(screen.getByLabelText(/Condition expression/), {
    target: { value: 'llm:Is this ready?' },
  });
  fireEvent.change(screen.getByLabelText('When true'), {
    target: { value: 'review' },
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save graph' })),
  );
  const saved = vi.mocked(callbacks.save).mock.calls[0][2];
  expect(saved).toHaveLength(8);
  expect(new Set(saved.map((step) => step.id)).size).toBe(8);
  expect(saved[7].fields.condition).toBe('llm:Is this ready?');
  expect(saved[7].fields.if_true).toBe('review');
});

it('preserves zero approval timeout and explicit decline branch', async () => {
  const callbacks = props();
  render(<TaskGraphEditor {...callbacks} />);
  await screen.findByLabelText('Prompt');
  fireEvent.click(screen.getByRole('button', { name: '2. Approval · review' }));
  expect(
    screen.getByLabelText('Approval timeout (minutes, 0 means no timeout)'),
  ).toHaveValue(0);
  expect(screen.getByLabelText('When denied')).toHaveValue('draft');
  fireEvent.change(screen.getByLabelText('When denied'), {
    target: { value: 'end' },
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save graph' })),
  );
  expect(vi.mocked(callbacks.save).mock.calls[0][2][1].fields).toMatchObject({
    timeout_minutes: 0,
    if_denied: 'end',
  });
});

it('retains draft on conflict and requires explicit reload before another save', async () => {
  const callbacks = props({
    save: vi.fn().mockRejectedValue({ code: 'task_revision_conflict' }),
  });
  render(<TaskGraphEditor {...callbacks} />);
  await screen.findByLabelText('Prompt');
  fireEvent.change(screen.getByLabelText('Prompt'), {
    target: { value: 'Unsaved draft' },
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save graph' })),
  );
  expect(screen.getByLabelText('Prompt')).toHaveValue('Unsaved draft');
  expect(screen.getByRole('button', { name: 'Save graph' })).toBeDisabled();
  expect(callbacks.load).toHaveBeenCalledTimes(1);
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Reload saved graph' })),
  );
  expect(screen.getByLabelText('Prompt')).toHaveValue('Saved prompt');
  expect(screen.getByRole('button', { name: 'Save graph' })).toBeEnabled();
});

it('blocks duplicate submissions and cancel while the effect is pending', async () => {
  const pending = deferred<TaskGraphSnapshot>();
  const callbacks = props({ save: vi.fn().mockReturnValue(pending.promise) });
  render(<TaskGraphEditor {...callbacks} />);
  await screen.findByLabelText('Prompt');
  const button = screen.getByRole('button', { name: 'Save graph' });
  fireEvent.click(button);
  fireEvent.click(button);
  expect(callbacks.save).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
  await act(async () => pending.resolve(snapshot()));
  expect(callbacks.onSaved).toHaveBeenCalledTimes(1);
});

it('aborts stale reads and ignores late results from a previous task', async () => {
  const first = deferred<TaskGraphSnapshot>();
  const load = vi
    .fn()
    .mockReturnValueOnce(first.promise)
    .mockResolvedValueOnce(snapshot({ task_id: 'task-b' }));
  const callbacks = props({ load });
  const view = render(<TaskGraphEditor {...callbacks} />);
  view.rerender(<TaskGraphEditor {...callbacks} taskId="task-b" />);
  await screen.findByLabelText('Prompt');
  expect(load.mock.calls[0][1].aborted).toBe(true);
  await act(async () => first.resolve(snapshot({ steps: [] })));
  expect(screen.getByLabelText('Prompt')).toHaveValue('Saved prompt');
  view.unmount();
  expect(load.mock.calls[1][1].aborted).toBe(true);
});

it('does not deliver a late save callback after unmount', async () => {
  const pending = deferred<TaskGraphSnapshot>();
  const callbacks = props({ save: vi.fn().mockReturnValue(pending.promise) });
  const view = render(<TaskGraphEditor {...callbacks} />);
  await screen.findByLabelText('Prompt');
  fireEvent.click(screen.getByRole('button', { name: 'Save graph' }));
  view.unmount();
  await act(async () => pending.resolve(snapshot()));
  expect(callbacks.onSaved).not.toHaveBeenCalled();
});

it('leaves unknown retained steps read-only and does not expose hidden data', async () => {
  const unknown = {
    id: 'future',
    type: 'future',
    fields: { ...fields, prompt: null },
    editable: false,
    retained_fields: true,
  };
  const callbacks = props({
    load: vi.fn().mockResolvedValue(snapshot({ steps: [unknown] })),
  });
  render(<TaskGraphEditor {...callbacks} />);
  await screen.findByText(/This saved step type is not editable/);
  expect(screen.queryByLabelText('Prompt')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(callbacks.onCancel).toHaveBeenCalledOnce();
  expect(callbacks.save).not.toHaveBeenCalled();
});

it('renders authored text as plain text and explains notify-only behavior', async () => {
  const authored = '<script>window.attack=true</script>';
  const callbacks = props({
    load: vi.fn().mockResolvedValue(
      snapshot({
        notify_only: true,
        steps: [
          { ...snapshot().steps[0], fields: { ...fields, prompt: authored } },
        ],
      }),
    ),
    onTaskSettings: vi.fn(),
  });
  render(<TaskGraphEditor {...callbacks} />);
  expect(await screen.findByLabelText('Prompt')).toHaveValue(authored);
  expect(document.querySelector('script')).toBeNull();
  expect(
    screen.getByText(/currently sends a notification only/),
  ).toBeInTheDocument();
  fireEvent.click(
    screen.getByRole('button', { name: 'Schedule and task settings' }),
  );
  expect(callbacks.onTaskSettings).toHaveBeenCalledOnce();
});

it('bounds the editing window to 100 steps without hiding saved steps', async () => {
  const many = Array.from({ length: 100 }, (_, index) => ({
    ...snapshot().steps[0],
    id: `p_${index}`,
  }));
  const callbacks = props({
    load: vi.fn().mockResolvedValue(snapshot({ steps: many })),
  });
  render(<TaskGraphEditor {...callbacks} />);
  await screen.findByLabelText('Prompt');
  expect(screen.getAllByRole('listitem')).toHaveLength(100);
  expect(screen.getByRole('button', { name: 'Add step' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '100. Prompt · p_99' }));
  fireEvent.change(screen.getByLabelText('Prompt'), {
    target: { value: 'Last step edited' },
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save graph' })),
  );
  const saved = vi.mocked(callbacks.save).mock.calls[0][2];
  expect(saved).toHaveLength(100);
  expect(saved[99].fields.prompt).toBe('Last step edited');
});
