import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import GoalProfileSettings, {
  createGoalProfileSettingsSession,
  type GoalPage,
  type GoalReceipt,
  type ProfilePage,
  type ProfileReceipt,
  type ProfileSummary,
} from './GoalProfileSettings';

const currentGoal = {
  id: 'goal-1',
  scope: 'conversation' as const,
  conversation_id: 'conversation-1',
  objective: 'Complete the migration',
  status: 'active' as const,
  revision: '3',
  turns_used: 4,
  max_turns: 24,
  token_budget: 0,
  tokens_used: 0,
  last_progress: 'Settings owner implemented.',
  last_reason: 'Continuing.',
  evidence: [],
  blockers: [],
  active_profile_id: 'profile-1',
};
const goalPage: GoalPage = {
  schema_version: 1,
  scope: 'conversation',
  conversation_id: 'conversation-1',
  revision: 'a'.repeat(64),
  current_goal_id: currentGoal.id,
  current_revision: currentGoal.revision,
  items: [currentGoal],
  total: 1,
  next_cursor: null,
};
const builtinProfile: ProfileSummary = {
  id: 'builtin:general',
  slug: 'general',
  display_name: 'General Assistant',
  description: 'A safe default.',
  when_to_use: 'General work.',
  scope: 'system',
  surface_scope: 'global',
  source: 'builtin',
  enabled: true,
  editable: false,
  revision: '1',
  capability: 'read_only',
  allow_tools: ['read_file'],
  skills: [],
  context_mode: 'auto',
  workspace_mode: 'read_only',
  approval_mode: 'inherit',
  instructions_preview: 'A bounded public preview.',
  instructions_truncated: true,
  instruction_edit: { mode: 'replace_only', stored: true },
};
const userProfile: ProfileSummary = {
  ...builtinProfile,
  id: 'profile-1',
  slug: 'focused_writer',
  display_name: 'Focused Writer',
  description: 'Focused workspace edits.',
  scope: 'user',
  source: 'user_created',
  editable: true,
  revision: '4',
  capability: 'write_capable',
  workspace_mode: 'single_writer',
  allow_tools: ['read_file', 'write_file'],
  skills: ['local-style'],
  instructions_preview: '[private preview]',
};
const profilePage: ProfilePage = {
  schema_version: 1,
  scope: 'global',
  revision: 'b'.repeat(64),
  items: [builtinProfile, userProfile],
  total: 2,
  next_cursor: null,
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

function options() {
  return {
    conversationId: 'conversation-1',
    session: createGoalProfileSettingsSession(),
    loadGoals: vi.fn().mockResolvedValue(goalPage),
    loadProfiles: vi.fn().mockResolvedValue(profilePage),
    loadProfile: vi.fn().mockImplementation(async (profileId: string) => ({
      schema_version: 1 as const,
      profile: profileId === userProfile.id ? userProfile : builtinProfile,
    })),
    reviewGoal: vi.fn().mockImplementation(async (payload) => ({
      schema_version: 1 as const,
      ...payload,
      action_digest: 'c'.repeat(64),
      disclosures:
        payload.operation === 'start' ? ['Replaces current goal.'] : [],
      review_id: 'goal-review',
    })),
    executeGoal: vi.fn().mockImplementation(async (command) => ({
      command_id: command.command_id,
      status: 'completed',
      operation: command.payload.operation,
      goal: currentGoal,
    })),
    reviewProfile: vi.fn().mockImplementation(async (payload) => ({
      schema_version: 1 as const,
      ...payload,
      changes: {},
      action_digest: 'd'.repeat(64),
      disclosures: payload.operation === 'delete' ? ['Cannot be undone.'] : [],
      review_id: 'profile-review',
    })),
    executeProfile: vi.fn().mockImplementation(async (command) => ({
      command_id: command.command_id,
      status: 'completed',
      operation: command.payload.operation,
      profile: userProfile,
      profile_id: userProfile.id,
    })),
  };
}

async function openProfiles() {
  fireEvent.mouseDown(screen.getByRole('tab', { name: 'Agent Profiles' }), {
    button: 0,
  });
  await screen.findByText('General Assistant');
}

it('loads only this conversation and performs no mutation during passive reads', async () => {
  const props = options();
  render(<GoalProfileSettings {...props} />);
  await screen.findByText('Complete the migration');
  expect(props.loadGoals).toHaveBeenCalledWith(
    { conversation_id: 'conversation-1', query: '' },
    expect.any(AbortSignal),
  );
  expect(props.loadProfiles).not.toHaveBeenCalled();
  expect(props.reviewGoal).not.toHaveBeenCalled();
  expect(props.executeGoal).not.toHaveBeenCalled();
});

it('does not restart a settled read when parent callback identities change', async () => {
  const props = options();
  const view = render(<GoalProfileSettings {...props} />);
  await screen.findByText('Complete the migration');

  view.rerender(
    <GoalProfileSettings
      {...props}
      loadGoals={(query, signal) => props.loadGoals(query, signal)}
      loadProfiles={(query, signal) => props.loadProfiles(query, signal)}
    />,
  );
  fireEvent.mouseDown(screen.getByRole('tab', { name: 'Agent Profiles' }), {
    button: 0,
  });

  await screen.findByText('General Assistant');
  expect(props.loadGoals).toHaveBeenCalledTimes(1);
  expect(props.loadProfiles).toHaveBeenCalledTimes(1);
});

it('validates and applies the exact goal draft once', async () => {
  const props = options();
  render(<GoalProfileSettings {...props} />);
  await screen.findByText('Complete the migration');
  fireEvent.change(screen.getByLabelText('Goal objective'), {
    target: { value: 'Ship the reviewed migration' },
  });
  fireEvent.change(screen.getByLabelText('Maximum turns'), {
    target: { value: '40' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Start goal' }));
  await screen.findByText('Goal change completed.');
  expect(props.reviewGoal.mock.calls[0][0]).toEqual({
    conversation_id: 'conversation-1',
    goal_id: 'goal-1',
    revision: '3',
    operation: 'start',
    objective: 'Ship the reviewed migration',
    max_turns: 40,
    reason: null,
  });
  expect(props.executeGoal).toHaveBeenCalledTimes(1);
  expect(props.executeGoal.mock.calls[0][0].payload.operation).toBe('start');
  expect(props.session.hasRetained()).toBe(false);
});

it('retains an uncertain goal attempt across remount for explicit recovery', async () => {
  const props = options();
  props.executeGoal.mockRejectedValueOnce(Error('transport lost'));
  const first = render(<GoalProfileSettings {...props} />);
  await screen.findByText('Complete the migration');
  fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
  await screen.findByRole('button', { name: 'Check original change' });
  const original = props.executeGoal.mock.calls[0];
  first.unmount();
  render(<GoalProfileSettings {...props} />);
  const recover = screen.getByRole('button', { name: 'Check original change' });
  await waitFor(() => expect(recover).toBeEnabled());
  fireEvent.click(recover);
  await screen.findByText('Goal change completed.');
  expect(props.executeGoal.mock.calls[1]).toEqual(original);
  expect(props.reviewGoal).toHaveBeenCalledTimes(1);
});

it('preserves stored profile instructions unless replacement is explicit', async () => {
  const props = options();
  render(<GoalProfileSettings {...props} />);
  await screen.findByText('Complete the migration');
  await openProfiles();
  fireEvent.click(screen.getByRole('button', { name: 'Edit Focused Writer' }));
  await screen.findByRole('group', { name: 'Edit profile' });
  expect(
    screen.queryByDisplayValue('[private preview]'),
  ).not.toBeInTheDocument();
  expect(screen.queryByLabelText('New instructions')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Profile display name'), {
    target: { value: 'Focused Editor' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save profile' }));
  await waitFor(() => expect(props.reviewProfile).toHaveBeenCalledTimes(1));
  expect(props.reviewProfile.mock.calls[0][0].fields.instructions).toBeNull();
  await screen.findByText('Profile change completed.');
  fireEvent.click(screen.getByRole('button', { name: 'Search profiles' }));
  await screen.findByRole('button', { name: 'Edit Focused Writer' });
  fireEvent.click(screen.getByRole('button', { name: 'Edit Focused Writer' }));
  await screen.findByRole('group', { name: 'Edit profile' });
  fireEvent.click(screen.getByRole('switch', { name: 'Replace instructions' }));
  fireEvent.change(screen.getByLabelText(/^New instructions/), {
    target: { value: 'Replacement body' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save profile' }));
  await waitFor(() => expect(props.reviewProfile).toHaveBeenCalledTimes(2));
  expect(props.reviewProfile.mock.calls[1][0].fields.instructions).toBe(
    'Replacement body',
  );
});

it('keeps built-ins read only while allowing an explicit duplicate', async () => {
  const props = options();
  render(<GoalProfileSettings {...props} />);
  await screen.findByText('Complete the migration');
  await openProfiles();
  expect(
    screen.getByRole('button', { name: 'Edit General Assistant' }),
  ).toBeDisabled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Duplicate General Assistant' }),
  );
  await screen.findByRole('group', { name: 'Duplicate profile' });
  fireEvent.change(screen.getByLabelText('Copy slug'), {
    target: { value: 'general_copy' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Duplicate profile' }));
  await waitFor(() => expect(props.reviewProfile).toHaveBeenCalledTimes(1));
  expect(props.reviewProfile.mock.calls[0][0]).toMatchObject({
    profile_id: 'builtin:general',
    revision: '1',
    operation: 'duplicate',
    fields: null,
    target_slug: 'general_copy',
  });
});

it('creates a bounded profile draft from one click', async () => {
  const props = options();
  render(<GoalProfileSettings {...props} />);
  await screen.findByText('Complete the migration');
  await openProfiles();
  fireEvent.click(screen.getByRole('button', { name: 'Create profile' }));
  fireEvent.change(screen.getByLabelText('Profile slug'), {
    target: { value: 'safe_reader' },
  });
  fireEvent.change(screen.getByLabelText('Profile display name'), {
    target: { value: 'Safe Reader' },
  });
  fireEvent.change(screen.getByLabelText(/^New instructions/), {
    target: { value: 'Read local files only.' },
  });
  fireEvent.click(
    screen.getAllByRole('button', { name: 'Create profile' }).at(-1)!,
  );
  await waitFor(() => expect(props.reviewProfile).toHaveBeenCalledTimes(1));
  expect(props.reviewProfile.mock.calls[0][0]).toMatchObject({
    profile_id: null,
    revision: 'none',
    operation: 'create',
    target_slug: null,
    target_name: null,
    fields: {
      slug: 'safe_reader',
      display_name: 'Safe Reader',
      instructions: 'Read local files only.',
      capability: 'read_only',
    },
  });
  await waitFor(() => expect(props.executeProfile).toHaveBeenCalledOnce());
});

it('tombstones private drafts and late settlements after authentication loss', async () => {
  const props = options();
  const pending = deferred<ProfileReceipt>();
  props.executeProfile.mockReturnValue(pending.promise);
  render(<GoalProfileSettings {...props} />);
  await screen.findByText('Complete the migration');
  await openProfiles();
  fireEvent.click(screen.getByRole('button', { name: 'Create profile' }));
  fireEvent.change(screen.getByLabelText('Profile slug'), {
    target: { value: 'private_profile' },
  });
  fireEvent.change(screen.getByLabelText('Profile display name'), {
    target: { value: 'Private Profile' },
  });
  fireEvent.change(screen.getByLabelText(/^New instructions/), {
    target: { value: 'private-instruction-body' },
  });
  fireEvent.click(
    screen.getAllByRole('button', { name: 'Create profile' }).at(-1)!,
  );
  await waitFor(() => expect(props.executeProfile).toHaveBeenCalledOnce());
  const command = props.executeProfile.mock.calls[0][0];
  act(() => props.session.dispose());
  await act(async () =>
    pending.resolve({
      command_id: command.command_id,
      status: 'completed',
      profile_id: 'private-profile',
    }),
  );
  expect(JSON.stringify(props.session.getSnapshot())).not.toContain(
    'private-instruction-body',
  );
  expect(props.session.getSnapshot().pending).toBeNull();
  expect(props.session.hasRetained()).toBe(false);
  expect(screen.getByText(/Sign in again/)).toBeVisible();
});

it('admits one execution during repeated synchronous clicks', async () => {
  const props = options();
  const pending = deferred<GoalReceipt>();
  props.executeGoal.mockReturnValue(pending.promise);
  render(<GoalProfileSettings {...props} />);
  await screen.findByText('Complete the migration');
  const apply = screen.getByRole('button', { name: 'Pause' });
  act(() => {
    apply.click();
    apply.click();
  });
  await waitFor(() => expect(props.executeGoal).toHaveBeenCalledTimes(1));
  act(() => props.session.dispose());
  await act(async () => pending.reject(Error('synthetic cancellation')));
});
