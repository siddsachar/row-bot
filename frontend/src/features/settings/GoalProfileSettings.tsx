import { useEffect, useRef, useSyncExternalStore } from 'react';
import { Button, Field, Input, Select, Tabs } from '../../ui/primitives';

export type GoalStatus =
  | 'active'
  | 'paused'
  | 'waiting_approval'
  | 'blocked'
  | 'completed'
  | 'cleared';
export type GoalSummary = {
  id: string;
  scope: 'conversation';
  conversation_id: string;
  objective: string;
  status: GoalStatus;
  revision: string;
  turns_used: number;
  max_turns: number;
  token_budget: number;
  tokens_used: number;
  last_progress: string;
  last_reason: string;
  evidence: string[];
  blockers: string[];
  active_profile_id: string;
};
export type GoalPage = {
  schema_version: 1;
  scope: 'conversation';
  conversation_id: string;
  revision: string;
  current_goal_id: string | null;
  current_revision: string;
  items: GoalSummary[];
  total: number;
  next_cursor: string | null;
};
export type GoalOperation = 'start' | 'pause' | 'resume' | 'complete' | 'clear';
export type GoalPayload = {
  conversation_id: string;
  goal_id: string | null;
  revision: string;
  operation: GoalOperation;
  objective: string | null;
  max_turns: number | null;
  reason: string | null;
};
export type GoalReview = GoalPayload & {
  schema_version: 1;
  action_digest: string;
  disclosures: string[];
  review_id: string;
};
export type GoalCommand = {
  command_id: string;
  type: 'goal.control';
  payload: GoalPayload;
};
export type GoalReceipt = {
  command_id: string;
  status: string;
  operation?: GoalOperation | null;
  goal?: GoalSummary | null;
  code?: string | null;
};

export type ProfileScope =
  'system' | 'user' | 'workspace' | 'plugin' | 'imported';
export type ProfileSummary = {
  id: string;
  slug: string;
  display_name: string;
  description: string;
  when_to_use: string;
  scope: ProfileScope;
  surface_scope: 'global';
  source: string;
  enabled: boolean;
  editable: boolean;
  revision: string;
  capability: 'read_only' | 'write_capable' | 'orchestrator';
  allow_tools: string[];
  skills: string[];
  context_mode: 'auto' | 'focused' | 'recent' | 'full' | 'empty' | 'resume';
  workspace_mode: 'auto' | 'read_only' | 'single_writer' | 'worktree';
  approval_mode: 'inherit' | 'block' | 'approve' | 'allow_all';
  instructions_preview: string;
  instructions_truncated: boolean;
  instruction_edit?: { mode: 'replace_only'; stored: boolean } | null;
};
export type ProfilePage = {
  schema_version: 1;
  scope: 'global';
  revision: string;
  items: ProfileSummary[];
  total: number;
  next_cursor: string | null;
};
export type ProfileOperation =
  'create' | 'edit' | 'duplicate' | 'delete' | 'enable' | 'disable';
export type ProfileFields = {
  slug: string;
  display_name: string;
  description: string;
  when_to_use: string;
  instructions: string | null;
  capability: ProfileSummary['capability'];
  allow_tools: string[];
  skills: string[];
  context_mode: ProfileSummary['context_mode'];
  workspace_mode: ProfileSummary['workspace_mode'];
  approval_mode: ProfileSummary['approval_mode'];
  enabled: boolean;
};
export type ProfilePayload = {
  profile_id: string | null;
  revision: string;
  operation: ProfileOperation;
  fields: ProfileFields | null;
  target_slug: string | null;
  target_name: string | null;
};
export type ProfileReview = ProfilePayload & {
  schema_version: 1;
  changes: Record<string, unknown>;
  action_digest: string;
  disclosures: string[];
  review_id: string;
};
export type ProfileCommand = {
  command_id: string;
  type: 'profile.mutate';
  payload: ProfilePayload;
};
export type ProfileReceipt = {
  command_id: string;
  status: string;
  operation?: ProfileOperation | null;
  profile?: ProfileSummary | null;
  profile_id?: string | null;
  code?: string | null;
};

type GoalAttempt = { kind: 'goal'; command: GoalCommand; review: GoalReview };
type ProfileAttempt = {
  kind: 'profile';
  command: ProfileCommand;
  review: ProfileReview;
};
type Attempt = GoalAttempt | ProfileAttempt;
type ProfileDraft = Omit<ProfileFields, 'allow_tools' | 'skills'> & {
  allow_tools: string;
  skills: string;
  replace_instructions: boolean;
  target_slug: string;
  target_name: string;
};
type State = {
  active: boolean;
  tab: 'goals' | 'profiles';
  goalPage: GoalPage | null;
  profilePage: ProfilePage | null;
  selectedProfile: ProfileSummary | null;
  goalQuery: string;
  profileQuery: string;
  profileScope: '' | ProfileScope;
  objective: string;
  maxTurns: string;
  reason: string;
  profileMode: '' | 'create' | 'edit' | 'duplicate';
  profileDraft: ProfileDraft;
  reviewed: Attempt | null;
  pending: Attempt | null;
  busy: string;
  message: string;
  goalsRefresh: number;
  profilesRefresh: number;
};

const emptyProfileDraft = (): ProfileDraft => ({
  slug: '',
  display_name: '',
  description: '',
  when_to_use: '',
  instructions: '',
  capability: 'read_only',
  allow_tools: '',
  skills: '',
  context_mode: 'auto',
  workspace_mode: 'auto',
  approval_mode: 'inherit',
  enabled: true,
  replace_instructions: false,
  target_slug: '',
  target_name: '',
});

export function createGoalProfileSettingsSession() {
  let state: State = {
    active: true,
    tab: 'goals',
    goalPage: null,
    profilePage: null,
    selectedProfile: null,
    goalQuery: '',
    profileQuery: '',
    profileScope: '',
    objective: '',
    maxTurns: '24',
    reason: '',
    profileMode: '',
    profileDraft: emptyProfileDraft(),
    reviewed: null,
    pending: null,
    busy: '',
    message: '',
    goalsRefresh: 0,
    profilesRefresh: 0,
  };
  const listeners = new Set<() => void>();
  const reads = new Set<AbortController>();
  const update = (patch: Partial<State>) => {
    if (!state.active) return;
    state = { ...state, ...patch };
    listeners.forEach((listener) => listener());
  };
  return {
    getSnapshot: () => state,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    update,
    updateProfileDraft: (patch: Partial<ProfileDraft>) =>
      update({
        profileDraft: { ...state.profileDraft, ...patch },
        reviewed: null,
        message: '',
      }),
    beginRead: () => {
      const controller = new AbortController();
      if (state.active) reads.add(controller);
      else controller.abort();
      return controller;
    },
    endRead: (controller: AbortController) => reads.delete(controller),
    refreshGoals: () => update({ goalsRefresh: state.goalsRefresh + 1 }),
    refreshProfiles: () =>
      update({ profilesRefresh: state.profilesRefresh + 1 }),
    hasRetained: () =>
      state.active &&
      Boolean(
        state.reviewed ||
        state.pending ||
        state.objective ||
        state.reason ||
        state.profileMode ||
        state.profileDraft.instructions,
      ),
    dispose: () => {
      reads.forEach((controller) => controller.abort());
      reads.clear();
      state = {
        ...state,
        active: false,
        goalPage: null,
        profilePage: null,
        selectedProfile: null,
        objective: '',
        reason: '',
        profileMode: '',
        profileDraft: emptyProfileDraft(),
        reviewed: null,
        pending: null,
        busy: '',
        message: 'Sign in again to manage goals and profiles.',
      };
      listeners.forEach((listener) => listener());
    },
  };
}
export type GoalProfileSettingsSession = ReturnType<
  typeof createGoalProfileSettingsSession
>;

export type GoalProfileSettingsProps = {
  conversationId: string;
  session: GoalProfileSettingsSession;
  loadGoals: (
    query: { conversation_id: string; query: string; cursor?: string },
    signal: AbortSignal,
  ) => Promise<GoalPage>;
  loadProfiles: (
    query: { query: string; scope?: ProfileScope; cursor?: string },
    signal: AbortSignal,
  ) => Promise<ProfilePage>;
  loadProfile: (
    profileId: string,
    signal: AbortSignal,
  ) => Promise<{ schema_version: 1; profile: ProfileSummary }>;
  reviewGoal: (
    payload: GoalPayload,
    signal: AbortSignal,
  ) => Promise<GoalReview>;
  executeGoal: (
    command: GoalCommand,
    review: GoalReview,
  ) => Promise<GoalReceipt>;
  reviewProfile: (
    payload: ProfilePayload,
    signal: AbortSignal,
  ) => Promise<ProfileReview>;
  executeProfile: (
    command: ProfileCommand,
    review: ProfileReview,
  ) => Promise<ProfileReceipt>;
};

function validGoalPage(page: GoalPage, conversationId: string) {
  if (
    page.schema_version !== 1 ||
    page.scope !== 'conversation' ||
    page.conversation_id !== conversationId ||
    page.items.length > 50 ||
    page.items.some((item) => item.conversation_id !== conversationId)
  )
    throw Error('invalid goal page');
  return page;
}

function validProfilePage(page: ProfilePage) {
  if (
    page.schema_version !== 1 ||
    page.scope !== 'global' ||
    page.items.length > 50
  )
    throw Error('invalid profile page');
  return page;
}

function fieldsFromDraft(draft: ProfileDraft, create: boolean): ProfileFields {
  const split = (value: string) =>
    value
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean);
  return {
    slug: draft.slug.trim(),
    display_name: draft.display_name.trim(),
    description: draft.description.trim(),
    when_to_use: draft.when_to_use.trim(),
    instructions:
      create || draft.replace_instructions ? draft.instructions : null,
    capability: draft.capability,
    allow_tools: split(draft.allow_tools),
    skills: split(draft.skills),
    context_mode: draft.context_mode,
    workspace_mode: draft.workspace_mode,
    approval_mode: draft.approval_mode,
    enabled: draft.enabled,
  };
}

export default function GoalProfileSettings(props: GoalProfileSettingsProps) {
  const {
    conversationId,
    session,
    loadGoals,
    loadProfiles,
    loadProfile,
    reviewGoal,
    executeGoal,
    reviewProfile,
    executeProfile,
  } = props;
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const locked = !state.active || Boolean(state.busy || state.pending);
  const loadGoalsRef = useRef(loadGoals);
  const loadProfilesRef = useRef(loadProfiles);
  loadGoalsRef.current = loadGoals;
  loadProfilesRef.current = loadProfiles;

  useEffect(() => {
    const current = session.getSnapshot();
    if (!current.active || current.tab !== 'goals' || current.busy) return;
    const abort = session.beginRead();
    session.update({ busy: 'load-goals' });
    void loadGoalsRef
      .current(
        { conversation_id: conversationId, query: current.goalQuery },
        abort.signal,
      )
      .then((page) => {
        if (!abort.signal.aborted)
          session.update({
            goalPage: validGoalPage(page, conversationId),
            busy: '',
          });
      })
      .catch(() => {
        if (!abort.signal.aborted)
          session.update({
            busy: '',
            message:
              'Conversation goals are unavailable. Refresh to try again.',
          });
      })
      .finally(() => session.endRead(abort));
  }, [conversationId, session, state.goalsRefresh, state.tab]);

  useEffect(() => {
    const current = session.getSnapshot();
    if (!current.active || current.tab !== 'profiles' || current.busy) return;
    const abort = session.beginRead();
    session.update({ busy: 'load-profiles' });
    void loadProfilesRef
      .current(
        {
          query: current.profileQuery,
          scope: current.profileScope || undefined,
        },
        abort.signal,
      )
      .then((page) => {
        if (!abort.signal.aborted)
          session.update({ profilePage: validProfilePage(page), busy: '' });
      })
      .catch(() => {
        if (!abort.signal.aborted)
          session.update({
            busy: '',
            message: 'Agent profiles are unavailable. Refresh to try again.',
          });
      })
      .finally(() => session.endRead(abort));
  }, [session, state.profilesRefresh, state.tab]);

  const requestGoalReview = async (operation: GoalOperation) => {
    const current = session.getSnapshot();
    const page = current.goalPage;
    if (!page || locked) return;
    const maxTurns = Number(current.maxTurns);
    if (
      operation === 'start' &&
      (!current.objective.trim() ||
        !Number.isInteger(maxTurns) ||
        maxTurns < 1 ||
        maxTurns > 1000)
    ) {
      session.update({
        message: 'Enter a goal and a turn limit from 1 to 1000.',
      });
      return;
    }
    const payload: GoalPayload = {
      conversation_id: conversationId,
      goal_id: page.current_goal_id,
      revision: page.current_revision,
      operation,
      objective: operation === 'start' ? current.objective.trim() : null,
      max_turns: operation === 'start' ? maxTurns : null,
      reason: operation === 'start' ? null : current.reason.trim(),
    };
    const abort = session.beginRead();
    session.update({ busy: 'review-goal', reviewed: null, message: '' });
    try {
      const review = await reviewGoal(payload, abort.signal);
      if (abort.signal.aborted) return;
      if (
        review.conversation_id !== payload.conversation_id ||
        review.goal_id !== payload.goal_id ||
        review.revision !== payload.revision ||
        review.operation !== payload.operation
      )
        throw Error('goal review mismatch');
      session.update({
        busy: '',
        reviewed: {
          kind: 'goal',
          command: {
            command_id: crypto.randomUUID(),
            type: 'goal.control',
            payload,
          },
          review,
        },
        message: 'Review complete. Apply this exact goal change to continue.',
      });
    } catch {
      if (!abort.signal.aborted)
        session.update({
          busy: '',
          message:
            'The goal change could not be reviewed. Refresh and try again.',
        });
    } finally {
      session.endRead(abort);
    }
  };

  const beginProfile = async (
    profile: ProfileSummary,
    mode: 'edit' | 'duplicate',
  ) => {
    if (locked || (mode === 'edit' && !profile.editable)) return;
    const abort = session.beginRead();
    session.update({ busy: 'profile-detail', reviewed: null, message: '' });
    try {
      const result = await loadProfile(profile.id, abort.signal);
      if (
        result.schema_version !== 1 ||
        result.profile.id !== profile.id ||
        result.profile.surface_scope !== 'global'
      )
        throw Error('profile detail mismatch');
      if (abort.signal.aborted) return;
      const detail = result.profile;
      session.update({
        busy: '',
        selectedProfile: detail,
        profileMode: mode,
        profileDraft: {
          slug: detail.slug,
          display_name: detail.display_name,
          description: detail.description,
          when_to_use: detail.when_to_use,
          instructions: '',
          capability: detail.capability,
          allow_tools: detail.allow_tools.join(', '),
          skills: detail.skills.join(', '),
          context_mode: detail.context_mode,
          workspace_mode: detail.workspace_mode,
          approval_mode: detail.approval_mode,
          enabled: detail.enabled,
          replace_instructions: false,
          target_slug: `${detail.slug}_copy`,
          target_name: `${detail.display_name} Copy`,
        },
      });
    } catch {
      if (!abort.signal.aborted)
        session.update({
          busy: '',
          message: 'Agent profile details are unavailable.',
        });
    } finally {
      session.endRead(abort);
    }
  };

  const requestProfileReview = async (operation: ProfileOperation) => {
    const current = session.getSnapshot();
    if (locked) return;
    const selected = current.selectedProfile;
    let fields: ProfileFields | null = null;
    let targetSlug: string | null = null;
    let targetName: string | null = null;
    if (operation === 'create' || operation === 'edit') {
      fields = fieldsFromDraft(current.profileDraft, operation === 'create');
      if (!fields.slug || !fields.display_name) {
        session.update({
          message: 'A profile slug and display name are required.',
        });
        return;
      }
    } else if (operation === 'duplicate') {
      targetSlug = current.profileDraft.target_slug.trim();
      targetName = current.profileDraft.target_name.trim();
      if (!targetSlug || !targetName) {
        session.update({
          message: 'A copy slug and display name are required.',
        });
        return;
      }
    }
    if (operation !== 'create' && !selected) return;
    const payload: ProfilePayload = {
      profile_id: selected?.id ?? null,
      revision: selected?.revision ?? 'none',
      operation,
      fields,
      target_slug: targetSlug,
      target_name: targetName,
    };
    if (new TextEncoder().encode(JSON.stringify(payload)).length > 64 * 1024) {
      session.update({ message: 'The profile draft is too large to review.' });
      return;
    }
    const abort = session.beginRead();
    session.update({ busy: 'review-profile', reviewed: null, message: '' });
    try {
      const review = await reviewProfile(payload, abort.signal);
      if (abort.signal.aborted) return;
      if (
        review.profile_id !== payload.profile_id ||
        review.revision !== payload.revision ||
        review.operation !== payload.operation
      )
        throw Error('profile review mismatch');
      session.update({
        busy: '',
        reviewed: {
          kind: 'profile',
          command: {
            command_id: crypto.randomUUID(),
            type: 'profile.mutate',
            payload,
          },
          review,
        },
        message:
          'Review complete. Apply this exact profile change to continue.',
      });
    } catch {
      if (!abort.signal.aborted)
        session.update({
          busy: '',
          message:
            'The profile change could not be reviewed. Refresh and try again.',
        });
    } finally {
      session.endRead(abort);
    }
  };

  const apply = async (attempt: Attempt | null) => {
    const current = session.getSnapshot();
    if (!attempt || !current.active || current.busy) return;
    if (current.reviewed !== attempt && current.pending !== attempt) return;
    session.update({
      busy: 'apply',
      reviewed: null,
      pending: attempt,
      message: '',
    });
    try {
      const receipt =
        attempt.kind === 'goal'
          ? await executeGoal(attempt.command, attempt.review)
          : await executeProfile(attempt.command, attempt.review);
      if (!session.getSnapshot().active) return;
      if (receipt.command_id !== attempt.command.command_id) throw Error();
      if (receipt.status === 'completed') {
        session.update({
          busy: '',
          pending: null,
          objective: attempt.kind === 'goal' ? '' : current.objective,
          reason: attempt.kind === 'goal' ? '' : current.reason,
          selectedProfile:
            attempt.kind === 'profile' ? null : current.selectedProfile,
          profileMode: attempt.kind === 'profile' ? '' : current.profileMode,
          profileDraft:
            attempt.kind === 'profile'
              ? emptyProfileDraft()
              : current.profileDraft,
          message:
            attempt.kind === 'goal'
              ? 'Goal change completed.'
              : 'Profile change completed.',
        });
        if (attempt.kind === 'goal') session.refreshGoals();
        else session.refreshProfiles();
      } else if (receipt.status === 'rejected') {
        session.update({
          busy: '',
          pending: null,
          message: 'The change was rejected. Refresh and review it again.',
        });
      } else {
        session.update({
          busy: '',
          message:
            'The original change is unconfirmed. Check it before making another change.',
        });
      }
    } catch {
      if (session.getSnapshot().active)
        session.update({
          busy: '',
          message:
            'The original change is unconfirmed. Check it before making another change.',
        });
    }
  };

  const goals = (
    <section aria-label="Goals" className="settings-section stack">
      <h2>Conversation goals</h2>
      <p>
        Goals belong to this conversation. Starting a new one replaces its
        current goal after review.
      </p>
      <Field label="Search this conversation's goals">
        <Input
          type="search"
          maxLength={256}
          value={state.goalQuery}
          disabled={locked}
          onChange={(event) =>
            session.update({ goalQuery: event.target.value, reviewed: null })
          }
        />
      </Field>
      <div className="button-row">
        <Button disabled={locked} onClick={() => session.refreshGoals()}>
          Search goals
        </Button>
        <Button
          disabled={locked || !state.goalPage?.next_cursor}
          onClick={() => {
            const current = session.getSnapshot();
            if (!current.goalPage?.next_cursor) return;
            const abort = session.beginRead();
            session.update({ busy: 'load-goals' });
            void loadGoals(
              {
                conversation_id: conversationId,
                query: current.goalQuery,
                cursor: current.goalPage.next_cursor,
              },
              abort.signal,
            )
              .then((page) =>
                session.update({
                  goalPage: validGoalPage(page, conversationId),
                  busy: '',
                }),
              )
              .catch(() =>
                session.update({
                  busy: '',
                  message: 'The goal page changed. Refresh it.',
                }),
              )
              .finally(() => session.endRead(abort));
          }}
        >
          Next goals
        </Button>
      </div>
      <fieldset disabled={locked}>
        <legend>Start a goal</legend>
        <Field label="Goal objective">
          <textarea
            maxLength={4096}
            value={state.objective}
            onChange={(event) =>
              session.update({ objective: event.target.value, reviewed: null })
            }
          />
        </Field>
        <Field label="Maximum turns">
          <Input
            type="number"
            min={1}
            max={1000}
            value={state.maxTurns}
            onChange={(event) =>
              session.update({ maxTurns: event.target.value, reviewed: null })
            }
          />
        </Field>
        <Button onClick={() => void requestGoalReview('start')}>
          Review start goal
        </Button>
      </fieldset>
      <Field label="Reason for goal status change">
        <Input
          maxLength={1024}
          value={state.reason}
          disabled={locked}
          onChange={(event) =>
            session.update({ reason: event.target.value, reviewed: null })
          }
        />
      </Field>
      {state.goalPage && (
        <p role="status">{state.goalPage.total} goals in this conversation.</p>
      )}
      <ul className="settings-results">
        {state.goalPage?.items.map((goal) => (
          <li className="surface" key={goal.id}>
            <strong>{goal.objective}</strong>
            <p>
              {goal.status} · {goal.turns_used} of {goal.max_turns} turns
            </p>
            {goal.last_progress && <p>{goal.last_progress}</p>}
            {goal.last_reason && <p>{goal.last_reason}</p>}
            {goal.id === state.goalPage?.current_goal_id && (
              <div
                className="button-row"
                role="group"
                aria-label="Current goal actions"
              >
                {['active', 'waiting_approval'].includes(goal.status) && (
                  <Button
                    disabled={locked}
                    onClick={() => void requestGoalReview('pause')}
                  >
                    Review pause
                  </Button>
                )}
                {['paused', 'blocked', 'waiting_approval'].includes(
                  goal.status,
                ) && (
                  <Button
                    disabled={locked}
                    onClick={() => void requestGoalReview('resume')}
                  >
                    Review resume
                  </Button>
                )}
                {['active', 'paused', 'blocked', 'waiting_approval'].includes(
                  goal.status,
                ) && (
                  <Button
                    disabled={locked}
                    onClick={() => void requestGoalReview('complete')}
                  >
                    Review complete
                  </Button>
                )}
                {goal.status !== 'cleared' && (
                  <Button
                    variant="danger"
                    disabled={locked}
                    onClick={() => void requestGoalReview('clear')}
                  >
                    Review clear
                  </Button>
                )}
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );

  const profileEditor = state.profileMode && (
    <fieldset disabled={locked}>
      <legend>
        {state.profileMode === 'create'
          ? 'Create profile'
          : state.profileMode === 'edit'
            ? 'Edit profile'
            : 'Duplicate profile'}
      </legend>
      {state.profileMode === 'duplicate' ? (
        <>
          <Field label="Copy slug">
            <Input
              maxLength={64}
              value={state.profileDraft.target_slug}
              onChange={(event) =>
                session.updateProfileDraft({ target_slug: event.target.value })
              }
            />
          </Field>
          <Field label="Copy display name">
            <Input
              maxLength={160}
              value={state.profileDraft.target_name}
              onChange={(event) =>
                session.updateProfileDraft({ target_name: event.target.value })
              }
            />
          </Field>
          <Button onClick={() => void requestProfileReview('duplicate')}>
            Review duplicate profile
          </Button>
        </>
      ) : (
        <>
          <Field label="Profile slug">
            <Input
              maxLength={64}
              value={state.profileDraft.slug}
              onChange={(event) =>
                session.updateProfileDraft({ slug: event.target.value })
              }
            />
          </Field>
          <Field label="Profile display name">
            <Input
              maxLength={160}
              value={state.profileDraft.display_name}
              onChange={(event) =>
                session.updateProfileDraft({ display_name: event.target.value })
              }
            />
          </Field>
          <Field label="Description">
            <textarea
              maxLength={2048}
              value={state.profileDraft.description}
              onChange={(event) =>
                session.updateProfileDraft({ description: event.target.value })
              }
            />
          </Field>
          <Field label="When to use">
            <textarea
              maxLength={2048}
              value={state.profileDraft.when_to_use}
              onChange={(event) =>
                session.updateProfileDraft({ when_to_use: event.target.value })
              }
            />
          </Field>
          {state.profileMode === 'edit' && (
            <label>
              <Input
                type="checkbox"
                checked={state.profileDraft.replace_instructions}
                onChange={(event) =>
                  session.updateProfileDraft({
                    replace_instructions: event.target.checked,
                    instructions: '',
                  })
                }
              />{' '}
              Replace stored instructions
            </label>
          )}
          {(state.profileMode === 'create' ||
            state.profileDraft.replace_instructions) && (
            <Field
              label="New instructions"
              hint="The saved instruction body is never loaded into this editor."
            >
              <textarea
                maxLength={49152}
                value={state.profileDraft.instructions ?? ''}
                onChange={(event) =>
                  session.updateProfileDraft({
                    instructions: event.target.value,
                  })
                }
              />
            </Field>
          )}
          <Field label="Capability">
            <Select
              value={state.profileDraft.capability}
              onChange={(event) =>
                session.updateProfileDraft({
                  capability: event.target.value as ProfileDraft['capability'],
                })
              }
            >
              <option value="read_only">Read only</option>
              <option value="write_capable">Write capable</option>
              <option value="orchestrator">Orchestrator</option>
            </Select>
          </Field>
          <Field label="Allowed tools" hint="Comma-separated stable tool IDs.">
            <Input
              value={state.profileDraft.allow_tools}
              onChange={(event) =>
                session.updateProfileDraft({ allow_tools: event.target.value })
              }
            />
          </Field>
          <Field label="Skills" hint="Comma-separated stable skill IDs.">
            <Input
              value={state.profileDraft.skills}
              onChange={(event) =>
                session.updateProfileDraft({ skills: event.target.value })
              }
            />
          </Field>
          <Field label="Context mode">
            <Select
              value={state.profileDraft.context_mode}
              onChange={(event) =>
                session.updateProfileDraft({
                  context_mode: event.target
                    .value as ProfileDraft['context_mode'],
                })
              }
            >
              {['auto', 'focused', 'recent', 'full', 'empty', 'resume'].map(
                (item) => (
                  <option value={item} key={item}>
                    {item}
                  </option>
                ),
              )}
            </Select>
          </Field>
          <Field label="Workspace mode">
            <Select
              value={state.profileDraft.workspace_mode}
              onChange={(event) =>
                session.updateProfileDraft({
                  workspace_mode: event.target
                    .value as ProfileDraft['workspace_mode'],
                })
              }
            >
              {['auto', 'read_only', 'single_writer', 'worktree'].map(
                (item) => (
                  <option value={item} key={item}>
                    {item}
                  </option>
                ),
              )}
            </Select>
          </Field>
          <Field label="Approval mode">
            <Select
              value={state.profileDraft.approval_mode}
              onChange={(event) =>
                session.updateProfileDraft({
                  approval_mode: event.target
                    .value as ProfileDraft['approval_mode'],
                })
              }
            >
              {['inherit', 'block', 'approve', 'allow_all'].map((item) => (
                <option value={item} key={item}>
                  {item}
                </option>
              ))}
            </Select>
          </Field>
          <label>
            <Input
              type="checkbox"
              checked={state.profileDraft.enabled}
              onChange={(event) =>
                session.updateProfileDraft({ enabled: event.target.checked })
              }
            />{' '}
            Profile enabled
          </label>
          <Button
            onClick={() =>
              void requestProfileReview(
                state.profileMode === 'create' ? 'create' : 'edit',
              )
            }
          >
            Review {state.profileMode} profile
          </Button>
        </>
      )}
    </fieldset>
  );

  const profiles = (
    <section aria-label="Agent Profiles" className="settings-section stack">
      <h2>Agent Profiles</h2>
      <p>
        Reusable profiles are global. Stored instruction bodies stay private;
        edits either preserve or explicitly replace them.
      </p>
      <div className="field-row">
        <Field label="Search profiles">
          <Input
            type="search"
            maxLength={256}
            value={state.profileQuery}
            disabled={locked}
            onChange={(event) =>
              session.update({
                profileQuery: event.target.value,
                reviewed: null,
              })
            }
          />
        </Field>
        <Field label="Profile scope">
          <Select
            value={state.profileScope}
            disabled={locked}
            onChange={(event) =>
              session.update({
                profileScope: event.target.value as State['profileScope'],
                reviewed: null,
              })
            }
          >
            <option value="">All scopes</option>
            {['system', 'user', 'workspace', 'plugin', 'imported'].map(
              (item) => (
                <option value={item} key={item}>
                  {item}
                </option>
              ),
            )}
          </Select>
        </Field>
        <Button disabled={locked} onClick={() => session.refreshProfiles()}>
          Search profiles
        </Button>
        <Button
          disabled={locked}
          onClick={() =>
            session.update({
              selectedProfile: null,
              profileMode: 'create',
              profileDraft: emptyProfileDraft(),
              reviewed: null,
              message: '',
            })
          }
        >
          Create profile
        </Button>
      </div>
      {state.profilePage && (
        <p role="status">{state.profilePage.total} reusable profiles.</p>
      )}
      <ul className="settings-results">
        {state.profilePage?.items.map((profile) => (
          <li className="surface" key={profile.id}>
            <strong>{profile.display_name}</strong> · {profile.scope} ·{' '}
            {profile.enabled ? 'Enabled' : 'Disabled'}
            <p>{profile.description}</p>
            <p>
              {profile.capability} · {profile.workspace_mode} ·{' '}
              {profile.approval_mode} approvals
            </p>
            <div className="button-row">
              <Button
                disabled={locked || !profile.editable}
                onClick={() => void beginProfile(profile, 'edit')}
              >
                Edit {profile.display_name}
              </Button>
              <Button
                disabled={locked}
                onClick={() => void beginProfile(profile, 'duplicate')}
              >
                Duplicate {profile.display_name}
              </Button>
              {profile.editable && (
                <>
                  <Button
                    disabled={locked}
                    onClick={() => {
                      session.update({ selectedProfile: profile });
                      void requestProfileReview(
                        profile.enabled ? 'disable' : 'enable',
                      );
                    }}
                  >
                    Review {profile.enabled ? 'disable' : 'enable'}{' '}
                    {profile.display_name}
                  </Button>
                  <Button
                    variant="danger"
                    disabled={locked}
                    onClick={() => {
                      session.update({ selectedProfile: profile });
                      void requestProfileReview('delete');
                    }}
                  >
                    Review delete {profile.display_name}
                  </Button>
                </>
              )}
            </div>
          </li>
        ))}
      </ul>
      {profileEditor}
    </section>
  );

  return (
    <section
      aria-label="Goals and Agent Profiles"
      className="settings-section stack"
    >
      <Tabs
        label="Goal and profile settings"
        value={state.tab}
        onChange={(value) =>
          session.update({
            tab: value as State['tab'],
            reviewed: null,
            message: '',
          })
        }
        items={[
          { id: 'goals', label: 'Goals', content: goals },
          { id: 'profiles', label: 'Agent Profiles', content: profiles },
        ]}
      />
      {state.reviewed && (
        <section aria-label="Goal or profile change review" className="surface">
          <h3>Review change</h3>
          <p>
            {state.reviewed.kind === 'goal'
              ? `Goal action: ${state.reviewed.review.operation}.`
              : `Profile action: ${state.reviewed.review.operation}.`}
          </p>
          {state.reviewed.review.disclosures.map((item) => (
            <p key={item}>{item}</p>
          ))}
          <Button disabled={locked} onClick={() => void apply(state.reviewed)}>
            Apply reviewed change
          </Button>
        </section>
      )}
      {state.pending && (
        <Button
          disabled={Boolean(state.busy) || !state.active}
          onClick={() => void apply(state.pending)}
        >
          Check original change
        </Button>
      )}
      {state.message && <p role="status">{state.message}</p>}
    </section>
  );
}
