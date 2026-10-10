import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
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
// SettingRow names its control and its row group with the same visible label,
// so query the control itself by role and accessible name.
const concurrencyGroup = () =>
  screen.getByRole('textbox', { name: 'Queue name' });
const findConcurrencyGroup = () =>
  screen.findByRole('textbox', { name: 'Queue name' });

/** The webhook rotation sits in a collapsed Danger zone until opened. */
function openDangerZone() {
  const summary = screen.getByText('Danger zone').closest('summary');
  const zone = summary?.closest('details');
  expect(zone).not.toHaveAttribute('open');
  fireEvent.click(summary!);
  expect(zone).toHaveAttribute('open');
}

const profileField = () =>
  screen.getByRole('combobox', { name: 'Agent profile' });
const findProfileField = () =>
  screen.findByRole('combobox', { name: 'Agent profile' });

function props(
  overrides: Partial<TaskSettingsEditorProps> = {},
): TaskSettingsEditorProps {
  return {
    profileOptions: [
      { id: 'builtin:worker', label: 'Worker' },
      { id: 'review', label: 'Review' },
      { id: 'builtin:review', label: 'Review (built in)' },
    ],
    modelOptions: [{ id: 'model:codex:gpt-5.6-sol', label: 'GPT-5.6-Sol' }],
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
  fireEvent.change(await findConcurrencyGroup(), {
    target: { value: 'Retained group' },
  });
  first.unmount();
  const second = render(<TaskSettingsEditor {...callbacks} />);
  expect(await findConcurrencyGroup()).toHaveValue('Retained group');
  expect(screen.getByRole('button', { name: 'Save settings' })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  second.unmount();
  const third = render(<TaskSettingsEditor {...callbacks} />);
  expect(await findConcurrencyGroup()).toBeDisabled();
  third.unmount();
  await act(async () =>
    response.resolve(
      snapshot({ fields: { ...fields, concurrency_group: 'Retained group' } }),
    ),
  );
  render(<TaskSettingsEditor {...callbacks} />);
  expect(await findConcurrencyGroup()).toHaveValue('Retained group');
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
  await findProfileField();
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
  await findProfileField();
  expect(callbacks.review).not.toHaveBeenCalled();
  expect(callbacks.save).not.toHaveBeenCalled();
  expect(callbacks.rotate).not.toHaveBeenCalled();
  expect(callbacks.download).not.toHaveBeenCalled();
  fireEvent.change(concurrencyGroup(), {
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
  await findProfileField();
  fireEvent.change(profileField(), {
    target: { value: 'review' },
  });
  fireEvent.change(screen.getByLabelText('Approval policy'), {
    target: { value: 'allow_all' },
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' })),
  );
  expect(profileField()).toHaveValue('builtin:review');
  expect(
    screen.getByText(/^Approvals: Block\. This workflow can only read/),
  ).toBeVisible();
  expect(screen.getByText(/Auto permits actions/)).toBeInTheDocument();
});

it('clears inactive trigger target when changing trigger kind without creating a secret', async () => {
  const callbacks = props();
  render(<TaskSettingsEditor {...callbacks} />);
  const trigger = await screen.findByRole('combobox', { name: 'Trigger' });
  fireEvent.change(trigger, {
    target: { value: 'task_complete' },
  });
  fireEvent.change(
    screen.getByRole('textbox', { name: 'Source workflow ID' }),
    { target: { value: 'source-a' } },
  );
  fireEvent.change(trigger, {
    target: { value: 'webhook' },
  });
  expect(
    screen.queryByRole('textbox', { name: 'Source workflow ID' }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByText(/A private secret is created when you save/),
  ).toBeInTheDocument();
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
  await screen.findByText(
    'A private webhook secret is configured. Keep the file private.',
  );
  expect(screen.queryByLabelText(/^Secret$/)).not.toBeInTheDocument();
  openDangerZone();
  expect(
    screen.getByRole('checkbox', { name: /I will update existing callers/ }),
  ).not.toBeChecked();
  expect(
    screen.getByRole('button', { name: 'Rotate webhook secret' }),
  ).toBeDisabled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Rotate webhook secret' }),
  );
  expect(callbacks.rotate).not.toHaveBeenCalled();
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
  const group = await findConcurrencyGroup();
  openDangerZone();
  // Acknowledge while the saved settings are clean, then start a save.
  const acknowledgement = screen.getByRole('checkbox', {
    name: /I will update existing callers/,
  });
  fireEvent.click(acknowledgement);
  expect(acknowledgement).toBeChecked();
  fireEvent.change(group, { target: { value: 'unsaved' } });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' })),
  );
  expect(callbacks.save).toHaveBeenCalledOnce();
  expect(
    screen.getByRole('button', {
      name: 'Download private webhook configuration',
    }),
  ).toBeDisabled();
  expect(acknowledgement).toBeDisabled();
  const rotate = screen.getByRole('button', { name: 'Rotate webhook secret' });
  expect(rotate).toBeDisabled();
  fireEvent.click(rotate);
  expect(callbacks.rotate).not.toHaveBeenCalled();
  await act(async () => pending.resolve(webhook));
  expect(callbacks.rotate).not.toHaveBeenCalled();
  // A completed save clears the acknowledgement; rotation needs a new one.
  expect(acknowledgement).not.toBeChecked();
  expect(rotate).toBeDisabled();
});

it('aborts a stale review and cannot overwrite newer edits with its result', async () => {
  const pending = deferred<TaskSettingsSnapshot>();
  const callbacks = props({ review: vi.fn().mockReturnValue(pending.promise) });
  render(<TaskSettingsEditor {...callbacks} />);
  fireEvent.change(await findConcurrencyGroup(), {
    target: { value: 'first' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
  fireEvent.change(concurrencyGroup(), {
    target: { value: 'second' },
  });
  expect(vi.mocked(callbacks.review).mock.calls[0][2]?.aborted).toBe(true);
  await act(async () =>
    pending.resolve(
      snapshot({ fields: { ...fields, concurrency_group: 'first' } }),
    ),
  );
  expect(concurrencyGroup()).toHaveValue('second');
  expect(callbacks.save).not.toHaveBeenCalled();
});

it('retains draft on stale profile revision and requires explicit reload', async () => {
  const callbacks = props({
    save: vi.fn().mockRejectedValue({ code: 'task_settings_profile_conflict' }),
  });
  render(<TaskSettingsEditor {...callbacks} />);
  fireEvent.change(await findConcurrencyGroup(), {
    target: { value: 'retained' },
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' })),
  );
  expect(concurrencyGroup()).toHaveValue('retained');
  expect(screen.getByRole('alert')).toHaveTextContent(
    'Your draft is retained. Reload the saved settings before continuing.',
  );
  expect(screen.getByRole('button', { name: 'Save settings' })).toBeDisabled();
  expect(callbacks.load).toHaveBeenCalledTimes(1);
  expect(callbacks.save).toHaveBeenCalledOnce();
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'Reload saved settings' }),
    ),
  );
  expect(callbacks.load).toHaveBeenCalledTimes(2);
  expect(await findConcurrencyGroup()).toHaveValue('');
  expect(callbacks.save).toHaveBeenCalledOnce();
});

it('blocks duplicate effect submission and late save callbacks after unmount', async () => {
  const pending = deferred<TaskSettingsSnapshot>();
  const callbacks = props({ save: vi.fn().mockReturnValue(pending.promise) });
  const view = render(<TaskSettingsEditor {...callbacks} />);
  await findProfileField();
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
  await findProfileField();
  expect(profileField()).toHaveValue('missing');
  expect(profileField()).toHaveDisplayValue('missing (not available)');
  expect(screen.getByRole('button', { name: 'Save settings' })).toBeEnabled();
  fireEvent.change(profileField(), {
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
  await findProfileField();
  fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
  view.rerender(<TaskSettingsEditor {...callbacks} taskId="task-b" />);
  await screen.findByRole('button', { name: 'Save settings' });
  await act(async () =>
    pending.resolve(
      snapshot({ fields: { ...fields, concurrency_group: 'old' } }),
    ),
  );
  expect(concurrencyGroup()).toHaveValue('');
  expect(screen.getByRole('button', { name: 'Save settings' })).toBeEnabled();
  expect(callbacks.save).not.toHaveBeenCalled();
});

it('lists profiles and models to pick from, with authored labels as plain text (U41)', async () => {
  const options = [
    { id: 'builtin:worker', label: 'Worker' },
    { id: 'custom', label: '<script>fake</script>' },
  ];
  const callbacks = props({ profileOptions: options });
  render(<TaskSettingsEditor {...callbacks} />);
  const profile = await findProfileField();
  expect(
    within(profile)
      .getAllByRole('option')
      .map((option) => option.textContent),
  ).toEqual(['Worker', '<script>fake</script>']);
  expect(document.querySelector('script')).toBeNull();
  expect(screen.queryByRole('textbox', { name: /profile|model/i })).toBeNull();
  const model = screen.getByRole('button', { name: 'Model' });
  expect(model).toHaveTextContent('Default model');
  fireEvent.click(model);
  fireEvent.click(await screen.findByRole('option', { name: 'GPT-5.6-Sol' }));
  expect(screen.getByRole('button', { name: 'Model' })).toHaveTextContent(
    'GPT-5.6-Sol',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(callbacks.onCancel).toHaveBeenCalledOnce();
  expect(callbacks.save).not.toHaveBeenCalled();
});

it('groups controls by purpose and names the workflow in its description', async () => {
  const callbacks = props({ taskName: 'Morning digest' });
  const view = render(<TaskSettingsEditor {...callbacks} />);
  const model = await screen.findByRole('group', {
    name: 'Model and approvals',
  });
  expect(
    within(model).getByRole('combobox', { name: 'Agent profile' }),
  ).toHaveValue('builtin:worker');
  expect(
    within(model).getByRole('combobox', { name: 'Approval policy' }),
  ).toHaveValue('block');
  expect(
    within(model).getByRole('button', { name: 'Model' }),
  ).toHaveTextContent('Default model');
  const runs = screen.getByRole('group', { name: 'Runs' });
  expect(
    within(runs).getByRole('switch', {
      name: 'Reuse a conversation across runs',
    }),
  ).not.toBeChecked();
  expect(within(runs).getByRole('textbox', { name: 'Queue name' })).toHaveValue(
    '',
  );
  expect(within(runs).getByRole('combobox', { name: 'Trigger' })).toHaveValue(
    'none',
  );
  expect(
    screen.getByText(/^Morning digest · How future runs choose their model/),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('group', { name: 'Saved webhook' }),
  ).not.toBeInTheDocument();
  expect(screen.queryByText('Danger zone')).not.toBeInTheDocument();
  view.rerender(<TaskSettingsEditor {...callbacks} taskName="" />);
  expect(
    screen.getByText(/^How future runs choose their model/),
  ).toBeInTheDocument();
  expect(callbacks.load).toHaveBeenCalledOnce();
  expect(callbacks.review).not.toHaveBeenCalled();
  expect(callbacks.save).not.toHaveBeenCalled();
});

it('offers rotation but not download for a saved webhook without a secret', async () => {
  const webhook = snapshot({
    fields: { ...fields, trigger_type: 'webhook' },
    webhook_configured: false,
  });
  const rotated = {
    ...webhook,
    revision: 'c'.repeat(64),
    webhook_configured: true,
  };
  const callbacks = props({
    load: vi.fn().mockResolvedValue(webhook),
    rotate: vi.fn().mockResolvedValue(rotated),
  });
  render(<TaskSettingsEditor {...callbacks} />);
  await screen.findByText(
    'This webhook has no private secret yet. Rotate it to create one.',
  );
  const download = screen.getByRole('button', {
    name: 'Download private webhook configuration',
  });
  expect(download).toBeDisabled();
  openDangerZone();
  fireEvent.click(
    screen.getByRole('checkbox', { name: /I will update existing callers/ }),
  );
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'Rotate webhook secret' }),
    ),
  );
  expect(callbacks.rotate).toHaveBeenCalledOnce();
  expect(callbacks.rotate).toHaveBeenCalledWith('task-a', 'a'.repeat(64));
  expect(
    screen.getByText(
      'Webhook secret rotated. Download the new configuration and update existing callers.',
    ),
  ).toHaveAttribute('role', 'status');
  expect(download).toBeEnabled();
  expect(callbacks.download).not.toHaveBeenCalled();
  expect(callbacks.save).not.toHaveBeenCalled();
});
