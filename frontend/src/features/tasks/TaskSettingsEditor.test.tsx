import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { TaskSettingsFields, TaskSettingsSnapshot } from '../../api/types';
import TaskSettingsEditor, {
  type TaskSettingsEditorProps,
} from './TaskSettingsEditor';
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
const fields: TaskSettingsFields = {
  concurrency_group: null,
  trigger_type: 'none',
  trigger_task_id: null,
  model_override: null,
  agent_profile_id: 'builtin:worker',
  approval_mode: 'block',
  persistent_enabled: false,
};
function snapshot(
  overrides: Partial<TaskSettingsSnapshot> = {},
): TaskSettingsSnapshot {
  return {
    task_id: 'task-a',
    revision: 'a'.repeat(64),
    profile_revision: 'b'.repeat(64),
    fields: { ...fields },
    profile_available: true,
    effective_approval_mode: 'block',
    webhook_configured: false,
    conversation_id: null,
    ...overrides,
  };
}
function props(
  overrides: Partial<TaskSettingsEditorProps> = {},
): TaskSettingsEditorProps {
  return {
    taskId: 'task-a',
    load: vi.fn().mockResolvedValue(snapshot()),
    review: vi
      .fn()
      .mockImplementation(async (_id, proposed) =>
        snapshot({ fields: proposed }),
      ),
    save: vi.fn().mockResolvedValue(snapshot()),
    rotate: vi.fn().mockResolvedValue(snapshot()),
    download: vi.fn().mockResolvedValue(undefined),
    onSaved: vi.fn(),
    onCancel: vi.fn(),
    ...overrides,
  };
}

it('retains an unsent configuration through remount and observes late save without another effect', async () => {
  const session = new TaskEditSession('settings', 'task-a');
  const response = deferred<TaskSettingsSnapshot>();
  const callbacks = props({
    session,
    save: vi.fn().mockReturnValue(response.promise),
  });
  const first = render(<TaskSettingsEditor {...callbacks} />);
  fireEvent.change(await screen.findByLabelText(/Concurrency group/), {
    target: { value: 'Retained group' },
  });
  first.unmount();
  const second = render(<TaskSettingsEditor {...callbacks} />);
  expect(await screen.findByLabelText(/Concurrency group/)).toHaveValue(
    'Retained group',
  );
  expect(screen.getByRole('button', { name: 'Save settings' })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  second.unmount();
  const third = render(<TaskSettingsEditor {...callbacks} />);
  expect(await screen.findByLabelText(/Concurrency group/)).toBeDisabled();
  third.unmount();
  await act(async () =>
    response.resolve(
      snapshot({ fields: { ...fields, concurrency_group: 'Retained group' } }),
    ),
  );
  render(<TaskSettingsEditor {...callbacks} />);
  expect(await screen.findByLabelText(/Concurrency group/)).toHaveValue(
    'Retained group',
  );
  expect(
    screen.getByText('Saved workflow settings. No workflow was run.'),
  ).toBeInTheDocument();
  expect(callbacks.load).toHaveBeenCalledOnce();
  expect(callbacks.save).toHaveBeenCalledOnce();
  expect(callbacks.onSaved).not.toHaveBeenCalled();
  expect(session.retained()).toBe(false);
});

it('allows only original receipt recovery while a stale settings rejection is still unconfirmed', async () => {
  let originalPending = true;
  const session = new TaskEditSession(
    'settings',
    'task-a',
    () => {},
    () => originalPending,
  );
  const save = vi
    .fn()
    .mockRejectedValueOnce({ code: 'task_settings_profile_conflict' })
    .mockImplementation(async () => {
      originalPending = false;
      throw { code: 'task_settings_profile_conflict' };
    });
  const callbacks = props({ session, save });
  render(<TaskSettingsEditor {...callbacks} />);
  await screen.findByLabelText(/Agent profile ID/);
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' })),
  );
  expect(
    screen.getByRole('button', { name: 'Reload saved settings' }),
  ).toBeDisabled();
  const retry = screen.getByRole('button', {
    name: 'Retry original settings change',
  });
  expect(retry).toBeEnabled();
  await act(async () => fireEvent.click(retry));
  expect(
    screen.getByRole('button', { name: 'Reload saved settings' }),
  ).toBeEnabled();
  expect(session.getMeta().uncertain).toBe(false);
  expect(save).toHaveBeenCalledTimes(2);
});

it('loads settings without effects and validates changed fields while saving', async () => {
  const callbacks = props();
  render(<TaskSettingsEditor {...callbacks} />);
  await screen.findByLabelText(/Agent profile ID/);
  expect(callbacks.review).not.toHaveBeenCalled();
  expect(callbacks.save).not.toHaveBeenCalled();
  expect(callbacks.rotate).not.toHaveBeenCalled();
  expect(callbacks.download).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText(/Concurrency group/), {
    target: { value: 'synthetic-group' },
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' })),
  );
  expect(
    screen.getByText('Saved workflow settings. No workflow was run.'),
  ).toBeVisible();
  expect(callbacks.save).toHaveBeenCalledWith(
    'task-a',
    'a'.repeat(64),
    'b'.repeat(64),
    { ...fields, concurrency_group: 'synthetic-group' },
  );
  expect(callbacks.onSaved).toHaveBeenCalledOnce();
});

it('uses canonical reviewed profile and shows stricter effective policy', async () => {
  const canonical = snapshot({
    fields: {
      ...fields,
      agent_profile_id: 'builtin:review',
      approval_mode: 'allow_all',
    },
    effective_approval_mode: 'block',
  });
  const callbacks = props({
    review: vi.fn().mockResolvedValue(canonical),
    save: vi.fn().mockResolvedValue(canonical),
  });
  render(<TaskSettingsEditor {...callbacks} />);
  await screen.findByLabelText(/Agent profile ID/);
  fireEvent.change(screen.getByLabelText(/Agent profile ID/), {
    target: { value: 'review' },
  });
  fireEvent.change(screen.getByLabelText('Approval policy'), {
    target: { value: 'allow_all' },
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' })),
  );
  expect(screen.getByLabelText(/Agent profile ID/)).toHaveValue(
    'builtin:review',
  );
  expect(
    screen.getByText(/Reviewed effective approval policy: Block/),
  ).toBeVisible();
  expect(screen.getByText(/Auto permits actions/)).toBeInTheDocument();
});

it('clears inactive trigger target when changing trigger kind without creating a secret', async () => {
  const callbacks = props();
  render(<TaskSettingsEditor {...callbacks} />);
  await screen.findByLabelText('Trigger');
  fireEvent.change(screen.getByLabelText('Trigger'), {
    target: { value: 'task_complete' },
  });
  fireEvent.change(screen.getByLabelText('Source workflow ID'), {
    target: { value: 'source-a' },
  });
  fireEvent.change(screen.getByLabelText('Trigger'), {
    target: { value: 'webhook' },
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' })),
  );
  expect(callbacks.review).toHaveBeenCalledWith(
    'task-a',
    { ...fields, trigger_type: 'webhook' },
    expect.any(AbortSignal),
  );
  expect(callbacks.rotate).not.toHaveBeenCalled();
  expect(callbacks.download).not.toHaveBeenCalled();
});

it('keeps webhook download explicit and rotates only after acknowledgement', async () => {
  const webhook = snapshot({
    fields: { ...fields, trigger_type: 'webhook' },
    webhook_configured: true,
  });
  const rotated = { ...webhook, revision: 'c'.repeat(64) };
  const callbacks = props({
    load: vi.fn().mockResolvedValue(webhook),
    rotate: vi.fn().mockResolvedValue(rotated),
  });
  render(<TaskSettingsEditor {...callbacks} />);
  await screen.findByText('A private webhook secret is configured.');
  expect(screen.queryByLabelText(/^Secret$/)).not.toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: 'Rotate webhook secret' }),
  ).toBeDisabled();
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Download private webhook configuration',
      }),
    ),
  );
  expect(callbacks.download).toHaveBeenCalledWith('task-a', 'a'.repeat(64));
  fireEvent.click(
    screen.getByRole('checkbox', { name: /I will update existing callers/ }),
  );
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'Rotate webhook secret' }),
    ),
  );
  expect(callbacks.rotate).toHaveBeenCalledWith('task-a', 'a'.repeat(64));
  expect(
    screen.getByRole('checkbox', { name: /I will update existing callers/ }),
  ).not.toBeChecked();
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Download private webhook configuration',
      }),
    ),
  );
  expect(callbacks.download).toHaveBeenLastCalledWith('task-a', 'c'.repeat(64));
});

it('does not rotate a webhook while a settings save is pending', async () => {
  const webhook = snapshot({
    fields: { ...fields, trigger_type: 'webhook' },
    webhook_configured: true,
  });
  const pending = deferred<TaskSettingsSnapshot>();
  const callbacks = props({
    load: vi.fn().mockResolvedValue(webhook),
    save: vi.fn().mockReturnValue(pending.promise),
  });
  render(<TaskSettingsEditor {...callbacks} />);
  await screen.findByLabelText(/Concurrency group/);
  fireEvent.change(screen.getByLabelText(/Concurrency group/), {
    target: { value: 'unsaved' },
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' })),
  );
  expect(
    screen.getByRole('button', {
      name: 'Download private webhook configuration',
    }),
  ).toBeDisabled();
  expect(
    screen.getByRole('checkbox', { name: /I will update existing callers/ }),
  ).toBeDisabled();
  expect(callbacks.rotate).not.toHaveBeenCalled();
  await act(async () => pending.resolve(webhook));
});

it('aborts a stale review and cannot overwrite newer edits with its result', async () => {
  const pending = deferred<TaskSettingsSnapshot>();
  const callbacks = props({ review: vi.fn().mockReturnValue(pending.promise) });
  render(<TaskSettingsEditor {...callbacks} />);
  await screen.findByLabelText(/Concurrency group/);
  fireEvent.change(screen.getByLabelText(/Concurrency group/), {
    target: { value: 'first' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
  fireEvent.change(screen.getByLabelText(/Concurrency group/), {
    target: { value: 'second' },
  });
  expect(vi.mocked(callbacks.review).mock.calls[0][2]?.aborted).toBe(true);
  await act(async () =>
    pending.resolve(
      snapshot({ fields: { ...fields, concurrency_group: 'first' } }),
    ),
  );
  expect(screen.getByLabelText(/Concurrency group/)).toHaveValue('second');
  expect(callbacks.save).not.toHaveBeenCalled();
});

it('retains draft on stale profile revision and requires explicit reload', async () => {
  const callbacks = props({
    save: vi.fn().mockRejectedValue({ code: 'task_settings_profile_conflict' }),
  });
  render(<TaskSettingsEditor {...callbacks} />);
  await screen.findByLabelText(/Concurrency group/);
  fireEvent.change(screen.getByLabelText(/Concurrency group/), {
    target: { value: 'retained' },
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' })),
  );
  expect(screen.getByLabelText(/Concurrency group/)).toHaveValue('retained');
  expect(screen.getByRole('button', { name: 'Save settings' })).toBeDisabled();
  expect(callbacks.load).toHaveBeenCalledTimes(1);
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'Reload saved settings' }),
    ),
  );
  expect(screen.getByLabelText(/Concurrency group/)).toHaveValue('');
});

it('blocks duplicate effect submission and late save callbacks after unmount', async () => {
  const pending = deferred<TaskSettingsSnapshot>();
  const callbacks = props({ save: vi.fn().mockReturnValue(pending.promise) });
  const view = render(<TaskSettingsEditor {...callbacks} />);
  await screen.findByLabelText(/Agent profile ID/);
  const save = screen.getByRole('button', { name: 'Save settings' });
  fireEvent.click(save);
  fireEvent.click(save);
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  expect(callbacks.save).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
  view.unmount();
  await act(async () => pending.resolve(snapshot()));
  expect(callbacks.onSaved).not.toHaveBeenCalled();
});

it('allows unavailable profile recovery without silently selecting another profile', async () => {
  const missing = snapshot({
    fields: { ...fields, agent_profile_id: 'missing' },
    profile_available: false,
    profile_revision: null,
    effective_approval_mode: null,
  });
  const callbacks = props({ load: vi.fn().mockResolvedValue(missing) });
  render(<TaskSettingsEditor {...callbacks} />);
  await screen.findByLabelText(/Agent profile ID/);
  expect(screen.getByLabelText(/Agent profile ID/)).toHaveValue('missing');
  expect(screen.getByRole('button', { name: 'Save settings' })).toBeEnabled();
  fireEvent.change(screen.getByLabelText(/Agent profile ID/), {
    target: { value: 'builtin:worker' },
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' })),
  );
  expect(screen.getByRole('button', { name: 'Save settings' })).toBeEnabled();
});

it('fences an old task review while the next task loads', async () => {
  const pending = deferred<TaskSettingsSnapshot>();
  const callbacks = props({ review: vi.fn().mockReturnValue(pending.promise) });
  const view = render(<TaskSettingsEditor {...callbacks} />);
  await screen.findByLabelText(/Agent profile ID/);
  fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
  view.rerender(<TaskSettingsEditor {...callbacks} taskId="task-b" />);
  await screen.findByRole('button', { name: 'Save settings' });
  await act(async () =>
    pending.resolve(
      snapshot({ fields: { ...fields, concurrency_group: 'old' } }),
    ),
  );
  expect(screen.getByLabelText(/Concurrency group/)).toHaveValue('');
  expect(screen.getByRole('button', { name: 'Save settings' })).toBeEnabled();
});

it('bounds optional suggestions and displays authored labels as plain text', async () => {
  const options = Array.from({ length: 201 }, (_, index) => ({
    id: `profile-${index}`,
    label: '<script>fake</script>',
  }));
  const callbacks = props({ profileOptions: options });
  render(<TaskSettingsEditor {...callbacks} />);
  await screen.findByLabelText(/Agent profile ID/);
  expect(document.querySelectorAll('datalist option')).toHaveLength(200);
  expect(document.querySelector('script')).toBeNull();
  expect(
    screen.getByText(/Showing the first 200 suggestions/),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(callbacks.onCancel).toHaveBeenCalledOnce();
  expect(callbacks.save).not.toHaveBeenCalled();
});
