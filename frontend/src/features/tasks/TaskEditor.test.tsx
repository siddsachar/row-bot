import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
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
/** The "Model and approvals" facts as [term, definition] pairs. */
function facts() {
  const terms = screen.getAllByRole('term');
  return terms.map((term) => [
    term.textContent,
    term.nextElementSibling?.textContent,
  ]);
}

it('exposes workflow prompts as a named accessible group', async () => {
  render(<TaskEditor {...props()} />);
  expect(
    await screen.findByRole('group', { name: 'Workflow prompts' }),
  ).toBeVisible();
});

it('shows schedule, delivery and policy in the rail of a new workflow with manual defaults', () => {
  render(
    <TaskEditor
      {...props({
        onTaskSettings: vi.fn(),
        onAdvancedSteps: vi.fn(),
      })}
    />,
  );
  expect(
    screen.getByRole('heading', { level: 2, name: 'Create task' }),
  ).toBeInTheDocument();
  const rail = screen.getByRole('complementary', {
    name: 'Schedule, delivery and policy',
  });
  const schedule = within(rail).getByRole('radiogroup', {
    name: 'When it runs',
  });
  expect(
    within(schedule).getByRole('radio', { name: 'Manually' }),
  ).toBeChecked();
  expect(
    within(schedule).getByRole('radio', { name: 'Once' }),
  ).not.toBeChecked();
  expect(
    within(schedule).getByRole('radio', { name: 'Repeats' }),
  ).not.toBeChecked();
  expect(within(rail).getByText('Runs only when you start it')).toBeVisible();
  expect(
    within(rail).queryByLabelText('Date and time'),
  ).not.toBeInTheDocument();
  expect(within(rail).queryByLabelText('Repeats')).not.toBeInTheDocument();
  expect(
    within(rail).getByRole('switch', { name: 'Enabled' }),
  ).not.toBeChecked();
  // Results always stay in the app; with no channel set up that is all.
  const delivery = within(rail).getByRole('group', { name: 'Send results to' });
  const app = within(delivery).getByRole('checkbox', { name: 'In this app' });
  expect(app).toBeChecked();
  expect(app).toBeDisabled();
  expect(within(delivery).getAllByRole('checkbox')).toHaveLength(1);
  expect(
    within(delivery).getByText(/Set up a channel in Settings › Channels/),
  ).toBeVisible();
  // New workflows are saved with the block policy, so the note says so.
  const policyNote = within(rail).getByText(/Change this after the first save/);
  expect(policyNote).toBeVisible();
  expect(policyNote).toHaveTextContent('block actions until you allow them');
  expect(policyNote).not.toHaveTextContent(/ask before/i);
  expect(
    screen.queryByRole('button', { name: 'Change model and approvals' }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: /Open step graph/ }),
  ).not.toBeInTheDocument();
});

const channels = [
  { id: 'slack', label: 'Slack', selected: false },
  { id: 'telegram', label: 'Telegram', selected: true },
];

it('prefills the channel checklist from the defaults and keeps following them', async () => {
  const callbacks = props({ deliveryChannels: channels });
  render(<TaskEditor {...callbacks} />);
  fillNew();
  const delivery = screen.getByRole('group', { name: 'Send results to' });
  expect(
    within(delivery).getByRole('checkbox', { name: 'Telegram' }),
  ).toBeChecked();
  expect(
    within(delivery).getByRole('checkbox', { name: 'Slack' }),
  ).not.toBeChecked();
  expect(within(delivery).getByText('Following your defaults.')).toBeVisible();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save task' })),
  );
  // Untouched, it keeps following the defaults (null), never a copy of them.
  expect(callbacks.create).toHaveBeenCalledWith(
    expect.objectContaining({ channels: null }),
  );
});

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
  expect(screen.getByRole('switch', { name: 'Enabled' })).not.toBeChecked();
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
      schedule: null,
      at: null,
    }),
  );
  expect(callbacks.onSaved).toHaveBeenCalledWith(snapshot());
});

it('saves [] for in-app only once the defaults are unticked', async () => {
  const callbacks = props({ deliveryChannels: channels });
  render(<TaskEditor {...callbacks} />);
  fillNew();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Telegram' }));
  expect(screen.getByText('Only in this app.')).toBeInTheDocument();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save task' })),
  );
  expect(callbacks.create).toHaveBeenLastCalledWith(
    expect.objectContaining({ channels: [] }),
  );
});

it('ticks channels for one workflow and goes back to the defaults as null', async () => {
  const callbacks = props({
    taskId: 'task-a',
    load: vi
      .fn()
      .mockResolvedValue(
        snapshot({ fields: { ...fields, channels: ['slack'] } }),
      ),
    save: vi.fn(
      async (_id: string, _revision: string, saved: TaskEditableFields) => ({
        task: snapshot({ revision: 'b'.repeat(64), fields: saved }),
        created: false,
        replayed: false,
      }),
    ),
  });
  render(<TaskEditor {...callbacks} deliveryChannels={channels} />);
  await screen.findByDisplayValue('Saved workflow');
  expect(screen.getByRole('checkbox', { name: 'Slack' })).toBeChecked();
  expect(screen.getByText('Only for this workflow.')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Telegram' }));
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save task' })),
  );
  expect(callbacks.save).toHaveBeenLastCalledWith(
    'task-a',
    'a'.repeat(64),
    expect.objectContaining({ channels: ['slack', 'telegram'] }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Use my defaults' }));
  expect(screen.getByText('Following your defaults.')).toBeInTheDocument();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save task' })),
  );
  expect(callbacks.save).toHaveBeenLastCalledWith(
    'task-a',
    'b'.repeat(64),
    expect.objectContaining({ channels: null }),
  );
});

it.each([
  [null, ['Telegram'], 'Following your defaults.'],
  [[], [], 'Only in this app.'],
  [['slack'], ['Slack'], 'Only for this workflow.'],
] as const)(
  'opens saved channels %j with %j ticked',
  async (saved, ticked, words) => {
    render(
      <TaskEditor
        {...props({
          taskId: 'task-a',
          deliveryChannels: channels,
          load: vi.fn().mockResolvedValue(
            snapshot({
              fields: {
                ...fields,
                channels: saved === null ? null : [...saved],
              },
            }),
          ),
        })}
      />,
    );
    await screen.findByDisplayValue('Saved workflow');
    const delivery = screen.getByRole('group', { name: 'Send results to' });
    expect(
      within(delivery)
        .getAllByRole('checkbox')
        .filter((box) => (box as HTMLInputElement).checked)
        .map(
          (box) =>
            box.getAttribute('aria-label') ?? box.parentElement?.textContent,
        ),
    ).toEqual(['In this app', ...ticked]);
    expect(within(delivery).getByText(words)).toBeInTheDocument();
  },
);

it('shows a saved channel that is no longer set up, so it can be unticked', async () => {
  const callbacks = props({
    taskId: 'task-a',
    deliveryChannels: channels,
    load: vi
      .fn()
      .mockResolvedValue(
        snapshot({ fields: { ...fields, channels: ['discord'] } }),
      ),
  });
  render(<TaskEditor {...callbacks} />);
  await screen.findByDisplayValue('Saved workflow');
  const gone = screen.getByRole('checkbox', { name: 'discord (not set up)' });
  expect(gone).toBeChecked();
  fireEvent.click(gone);
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save task' })),
  );
  expect(callbacks.save).toHaveBeenLastCalledWith(
    'task-a',
    'a'.repeat(64),
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
  expect(screen.getByText(/change them in the step graph/)).toBeVisible();
  const delivery = screen.getByRole('group', { name: 'Send results to' });
  expect(within(delivery).queryAllByRole('checkbox')).toHaveLength(0);
  expect(
    within(delivery).getByText(/keeps a destination from the earlier app/),
  ).toHaveTextContent('It is preserved when you save.');
  for (const name of [
    'Add step',
    'Reorder step 1',
    'Move prompt 1 up',
    'Remove prompt 1',
  ])
    expect(screen.queryByRole('button', { name })).not.toBeInTheDocument();
  expect(facts()).toEqual([
    ['Agent profile', 'Workflow default'],
    ['Approvals', 'Block actions'],
  ]);
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save task' })),
  );
  expect(callbacks.save).toHaveBeenCalledWith(
    'task-a',
    'a'.repeat(64),
    snapshot().fields,
  );
});

it('saves a local one-shot date with no recurring schedule', async () => {
  const callbacks = props();
  render(<TaskEditor {...callbacks} />);
  fillNew();
  fireEvent.click(screen.getByRole('radio', { name: 'Repeats' }));
  expect(screen.getByLabelText('Repeats')).toHaveValue('daily');
  expect(screen.getByLabelText('Time')).toHaveValue('09:00');
  fireEvent.click(screen.getByRole('radio', { name: 'Once' }));
  expect(screen.queryByLabelText('Repeats')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Date and time'), {
    target: { value: '2027-01-01T10:00' },
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save task' })),
  );
  expect(callbacks.create).toHaveBeenCalledWith(
    expect.objectContaining({ at: '2027-01-01T10:00', schedule: null }),
  );
});

it('requires a date before saving a one-shot schedule', async () => {
  const callbacks = props();
  render(<TaskEditor {...callbacks} />);
  fillNew();
  fireEvent.click(screen.getByRole('radio', { name: 'Once' }));
  expect(screen.getByLabelText('Date and time')).toBeInvalid();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save task' })),
  );
  expect(callbacks.create).not.toHaveBeenCalled();
});

it('replaces a saved one-shot date with a recurring schedule, and clears both for manual', async () => {
  const callbacks = props({
    taskId: 'task-a',
    load: vi.fn().mockResolvedValue(
      snapshot({
        fields: { ...fields, enabled: true, at: '2027-01-01T10:00' },
      }),
    ),
  });
  render(<TaskEditor {...callbacks} />);
  await screen.findByDisplayValue('Saved workflow');
  expect(screen.getByRole('radio', { name: 'Once' })).toBeChecked();
  expect(screen.getByLabelText('Date and time')).toHaveValue(
    '2027-01-01T10:00',
  );
  fireEvent.click(screen.getByRole('radio', { name: 'Repeats' }));
  fireEvent.change(screen.getByLabelText('Repeats'), {
    target: { value: 'weekdays' },
  });
  fireEvent.change(screen.getByLabelText('Time'), {
    target: { value: '07:30' },
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save task' })),
  );
  expect(callbacks.save).toHaveBeenLastCalledWith(
    'task-a',
    'a'.repeat(64),
    expect.objectContaining({
      schedule: 'cron:30 7 * * mon-fri',
      at: null,
      enabled: true,
    }),
  );
  fireEvent.click(screen.getByRole('radio', { name: 'Manually' }));
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save task' })),
  );
  expect(callbacks.save).toHaveBeenLastCalledWith(
    'task-a',
    'a'.repeat(64),
    expect.objectContaining({ schedule: null, at: null }),
  );
});

it('opens a saved recurring schedule in the builder and saves it untouched', async () => {
  const callbacks = props({
    taskId: 'task-a',
    load: vi
      .fn()
      .mockResolvedValue(
        snapshot({ fields: { ...fields, schedule: 'weekly:fri:17:00' } }),
      ),
  });
  render(<TaskEditor {...callbacks} />);
  await screen.findByDisplayValue('Saved workflow');
  expect(screen.getByRole('radio', { name: 'Repeats' })).toBeChecked();
  expect(screen.getByLabelText('Repeats')).toHaveValue('days');
  expect(screen.getByRole('button', { name: 'Friday' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  expect(screen.getByLabelText('Time')).toHaveValue('17:00');
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save task' })),
  );
  expect(callbacks.save).toHaveBeenCalledWith(
    'task-a',
    'a'.repeat(64),
    expect.objectContaining({ schedule: 'weekly:fri:17:00', at: null }),
  );
});

it('keeps a saved schedule exactly as written until the schedule is edited', async () => {
  const callbacks = props({
    taskId: 'task-a',
    load: vi
      .fn()
      .mockResolvedValue(
        snapshot({ fields: { ...fields, schedule: 'interval:0.5' } }),
      ),
    save: vi.fn(
      async (_id: string, _revision: string, saved: TaskEditableFields) => ({
        task: snapshot({ fields: saved }),
        created: false,
        replayed: false,
      }),
    ),
  });
  render(<TaskEditor {...callbacks} />);
  await screen.findByDisplayValue('Saved workflow');
  expect(screen.getByLabelText('Repeats')).toHaveValue('minutes');
  expect(screen.getByLabelText('Every (minutes)')).toHaveValue(30);
  fireEvent.change(screen.getByLabelText('Name'), {
    target: { value: 'Renamed' },
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save task' })),
  );
  expect(callbacks.save).toHaveBeenLastCalledWith(
    'task-a',
    'a'.repeat(64),
    expect.objectContaining({ name: 'Renamed', schedule: 'interval:0.5' }),
  );
  fireEvent.change(screen.getByLabelText('Every (minutes)'), {
    target: { value: '45' },
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save task' })),
  );
  expect(callbacks.save).toHaveBeenLastCalledWith(
    'task-a',
    'a'.repeat(64),
    expect.objectContaining({ schedule: 'interval_minutes:45', at: null }),
  );
});

it('shows a numbered-weekday cron as written, counting Monday as 0', async () => {
  render(
    <TaskEditor
      {...props({
        taskId: 'task-a',
        load: vi
          .fn()
          .mockResolvedValue(
            snapshot({ fields: { ...fields, schedule: 'cron:0 9 * * 1' } }),
          ),
      })}
    />,
  );
  await screen.findByDisplayValue('Saved workflow');
  expect(screen.getByLabelText('Repeats')).toHaveValue('cron');
  expect(
    screen.getByLabelText('Cron expression', { exact: false }),
  ).toHaveValue('0 9 * * 1');
  expect(screen.getByText(/^Every Tuesday at/)).toBeInTheDocument();
});

it('previews the next run only once the workflow is enabled', () => {
  render(<TaskEditor {...props()} />);
  fireEvent.click(screen.getByRole('radio', { name: 'Repeats' }));
  const preview = screen.getByText(/^Every day at/).closest('p');
  expect(preview).toHaveTextContent('once enabled');
  fireEvent.click(screen.getByRole('switch', { name: 'Enabled' }));
  expect(screen.getByRole('switch', { name: 'Enabled' })).toBeChecked();
  expect(preview).not.toHaveTextContent('once enabled');
});

it('adds, fills and removes prompt steps', async () => {
  const callbacks = props();
  render(<TaskEditor {...callbacks} />);
  fillNew();
  expect(
    screen.getByRole('button', { name: 'Remove prompt 1' }),
  ).toBeDisabled();
  expect(
    screen.getByRole('button', { name: 'Move prompt 1 up' }),
  ).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Add step' }));
  const second = screen.getByLabelText('Prompt 2');
  expect(second).toHaveValue('');
  expect(second).toBeRequired();
  fireEvent.change(second, { target: { value: 'Then draft a reply' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add step' }));
  expect(screen.getByLabelText('Prompt 3')).toHaveValue('');
  fireEvent.click(screen.getByRole('button', { name: 'Remove prompt 3' }));
  expect(screen.queryByLabelText('Prompt 3')).not.toBeInTheDocument();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save task' })),
  );
  expect(callbacks.create).toHaveBeenCalledWith(
    expect.objectContaining({
      prompts: ['Summarize fake data', 'Then draft a reply'],
    }),
  );
});

it('bounds a workflow at 100 prompt steps', async () => {
  render(
    <TaskEditor
      {...props({
        taskId: 'task-a',
        load: vi.fn().mockResolvedValue(
          snapshot({
            fields: {
              ...fields,
              prompts: Array.from({ length: 100 }, (_, i) => `Step ${i}`),
            },
          }),
        ),
      })}
    />,
  );
  await screen.findByDisplayValue('Saved workflow');
  expect(screen.getByRole('button', { name: 'Add step' })).toBeDisabled();
});

it('reorders steps from the drag handle with the arrow keys and keeps focus on the moved step', async () => {
  const callbacks = props({
    taskId: 'task-a',
    load: vi.fn().mockResolvedValue(
      snapshot({
        fields: { ...fields, prompts: ['First', 'Second', 'Third'] },
      }),
    ),
  });
  render(<TaskEditor {...callbacks} />);
  await screen.findByDisplayValue('Saved workflow');
  const values = () =>
    [1, 2, 3].map(
      (index) =>
        (screen.getByLabelText(`Prompt ${index}`) as HTMLTextAreaElement).value,
    );
  const handle = (index: number) =>
    screen.getByRole('button', { name: `Reorder step ${index}` });
  handle(1).focus();
  fireEvent.keyDown(handle(1), { key: 'ArrowDown' });
  expect(values()).toEqual(['Second', 'First', 'Third']);
  await waitFor(() => expect(handle(2)).toHaveFocus());
  expect(screen.getByText('Step 1 moved to position 2.')).toBeInTheDocument();
  fireEvent.keyDown(handle(3), { key: 'ArrowUp' });
  expect(values()).toEqual(['Second', 'Third', 'First']);
  await waitFor(() => expect(handle(2)).toHaveFocus());
  fireEvent.keyDown(handle(1), { key: 'ArrowUp' });
  fireEvent.keyDown(handle(3), { key: 'ArrowDown' });
  fireEvent.keyDown(handle(2), { key: 'Enter' });
  expect(values()).toEqual(['Second', 'Third', 'First']);
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save task' })),
  );
  expect(callbacks.save).toHaveBeenCalledWith(
    'task-a',
    'a'.repeat(64),
    expect.objectContaining({ prompts: ['Second', 'Third', 'First'] }),
  );
});

it('reorders steps by dragging a handle onto another step', () => {
  render(<TaskEditor {...props()} />);
  fillNew();
  fireEvent.click(screen.getByRole('button', { name: 'Add step' }));
  fireEvent.change(screen.getByLabelText('Prompt 2'), {
    target: { value: 'Second' },
  });
  const dataTransfer = { setData: vi.fn(), effectAllowed: 'none' };
  fireEvent.dragStart(screen.getByRole('button', { name: 'Reorder step 2' }), {
    dataTransfer,
  });
  expect(dataTransfer.effectAllowed).toBe('move');
  const first = screen.getByLabelText('Prompt 1').closest('li')!;
  fireEvent.dragOver(first, { dataTransfer });
  fireEvent.drop(first, { dataTransfer });
  expect(screen.getByLabelText('Prompt 1')).toHaveValue('Second');
  expect(screen.getByLabelText('Prompt 2')).toHaveValue('Summarize fake data');
});

it('shows the saved model and approval policy in words and opens their settings', async () => {
  const onTaskSettings = vi.fn();
  const onAdvancedSteps = vi.fn();
  render(
    <TaskEditor
      {...props({
        taskId: 'task-a',
        onTaskSettings,
        onAdvancedSteps,
        load: vi.fn().mockResolvedValue(
          snapshot({
            agent_profile_id: 'builtin:row_bot_researcher',
            approval_mode: 'approve',
          }),
        ),
      })}
    />,
  );
  await screen.findByDisplayValue('Saved workflow');
  expect(
    screen.getByRole('heading', { level: 2, name: 'Edit task' }),
  ).toBeInTheDocument();
  expect(facts()).toEqual([
    ['Agent profile', 'Researcher'],
    ['Approvals', 'Ask before actions'],
  ]);
  fireEvent.click(
    screen.getByRole('button', { name: 'Change model and approvals' }),
  );
  expect(onTaskSettings).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button', { name: /Open step graph/ }));
  expect(onAdvancedSteps).toHaveBeenCalledOnce();
});

it.each([
  ['block', 'Block actions'],
  ['approve', 'Ask before actions'],
  ['allow_all', 'Auto, within the profile'],
  // An unset policy follows the global setting; it is not "Ask".
  ['', 'Default'],
])('describes the %j approval policy as %s', async (mode, words) => {
  render(
    <TaskEditor
      {...props({
        taskId: 'task-a',
        load: vi.fn().mockResolvedValue(snapshot({ approval_mode: mode })),
      })}
    />,
  );
  await screen.findByDisplayValue('Saved workflow');
  expect(facts()).toContainEqual(['Approvals', words]);
  expect(
    screen.queryByRole('button', { name: 'Change model and approvals' }),
  ).not.toBeInTheDocument();
});

it('refuses to switch on a one-off whose time has passed, before saving (B133)', async () => {
  const callbacks = props({
    load: vi
      .fn()
      .mockResolvedValue(
        snapshot({ fields: { ...fields, at: '2020-01-01T09:00' } }),
      ),
  });
  render(<TaskEditor {...callbacks} taskId="task-a" />);
  const enabled = await screen.findByRole('switch', { name: 'Enabled' });
  await act(async () => fireEvent.click(enabled));
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save task' })),
  );
  expect(callbacks.save).not.toHaveBeenCalled();
  expect(screen.getByRole('alert')).toHaveTextContent(
    'That time has passed. Pick a later time, or switch the workflow off.',
  );
});
