import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import DeveloperRepositoryPanel, {
  createDeveloperRepositorySession,
  type DeveloperRepositoryReceipt,
  type DeveloperRepositorySnapshot,
} from './DeveloperRepositoryPanel';

const scope = 'conversation-1:workspace-1:binding-1:1';
const availability = Object.fromEntries(
  [
    'developer.repository.branch.create',
    'developer.repository.branch.switch',
    'developer.repository.commit',
    'developer.repository.push',
    'developer.repository.pull_request',
    'developer.repository.worktree.create',
    'developer.repository.worktree.preserve',
    'developer.repository.sandbox.configure',
    'developer.repository.sandbox.rebuild',
    'developer.repository.sandbox.cleanup',
  ].map((action) => [action, { available: true, code: null }]),
);
const page: DeveloperRepositorySnapshot = {
  schema_version: 1,
  resource_id: 'workspace-1',
  conversation_id: 'conversation-1',
  binding_id: 'binding-1',
  binding_revision: '1',
  resource_revision: 'resource-1',
  revision: 'a'.repeat(64),
  workspace_name: 'Disposable repository',
  trusted: true,
  repository: {
    state: 'ready',
    is_git: true,
    is_root: true,
    branch: 'main',
    detached: false,
    dirty: true,
    remote_configured: true,
    tracking_summary: '## main',
    branches: ['main', 'feature/x'],
  },
  worktrees: [],
  sandbox: {
    execution_mode: 'local',
    network: 'off',
    image: 'row-bot-sandbox:latest',
    pending_imports: 0,
    owned_processes: 0,
    runtime_status: 'not_probed',
  },
  availability: {
    ...availability,
    'developer.repository.clone': {
      available: false,
      code: 'use_workspace_setup',
    },
    'developer.repository.install': {
      available: false,
      code: 'use_workspace_process_review',
    },
    'developer.repository.network': {
      available: false,
      code: 'use_workspace_process_review',
    },
    'developer.repository.delete': {
      available: false,
      code: 'no_recoverable_repository_delete_owner',
    },
  },
};

function options() {
  const session = createDeveloperRepositorySession(scope);
  const load = vi.fn().mockResolvedValue(page);
  const review = vi.fn().mockImplementation(async (action, payload) => ({
    schema_version: 1,
    action,
    resource_id: page.resource_id,
    conversation_id: page.conversation_id,
    binding_id: page.binding_id,
    binding_revision: page.binding_revision,
    resource_revision: page.resource_revision,
    revision: payload.revision,
    policy_action: 'git_commit',
    policy_decision: 'ask',
    approval_required: true,
    disclosures: ['Synthetic reviewed repository effect.'],
    action_digest: 'b'.repeat(64),
    review_id: 'review-1',
  }));
  const execute = vi.fn().mockImplementation(async (command) => ({
    schema_version: 1,
    command_id: command.command_id,
    action: command.type,
    resource_id: page.resource_id,
    conversation_id: page.conversation_id,
    status: 'completed',
    code: null,
    revision: 'c'.repeat(64),
    worktree_id: null,
    external_url: null,
  }));
  return {
    scope,
    visible: true,
    session,
    load,
    review,
    execute,
    onChanged: vi.fn(),
  };
}
const ready = () => screen.findByRole('button', { name: 'Push branch' });

it('reads repository state without starting a Git, sandbox, or network action', async () => {
  const props = options();
  render(<DeveloperRepositoryPanel {...props} />);
  await ready();
  const branch = screen.getByRole('region', { name: 'Branch' });
  expect(
    within(branch).getByRole('button', { name: 'Switch branch' }),
  ).toHaveTextContent('main');
  expect(within(branch).getByText(/Local changes/)).toBeVisible();
  // Clone, install, network and delete stay outside this panel's authority.
  for (const name of [/clone/i, /install/i, /^delete/i, /network/i])
    expect(screen.queryByRole('button', { name })).not.toBeInTheDocument();
  expect(props.load).toHaveBeenCalledOnce();
  expect(props.review).not.toHaveBeenCalled();
  expect(props.execute).not.toHaveBeenCalled();
});

it('commits exactly the checked changed files from one click', async () => {
  const props = options();
  render(
    <DeveloperRepositoryPanel
      {...props}
      changedFiles={[
        { path: 'src/one.py', status: 'modified' },
        { path: 'src/two.py', status: 'added' },
        { path: 'src/three.py', status: 'modified' },
      ]}
    />,
  );
  await ready();
  fireEvent.change(screen.getByLabelText('Commit message'), {
    target: { value: 'Save focused changes' },
  });
  expect(screen.getByRole('button', { name: 'Commit changes' })).toBeEnabled();
  fireEvent.click(screen.getByRole('checkbox', { name: 'src/three.py' }));
  fireEvent.click(screen.getByRole('button', { name: 'Commit 2 files' }));
  expect(props.review).toHaveBeenCalledWith(
    'developer.repository.commit',
    {
      revision: page.revision,
      message: 'Save focused changes',
      paths: ['src/one.py', 'src/two.py'],
    },
    expect.any(AbortSignal),
  );
  await screen.findByText('Committed.');
  expect(props.execute).toHaveBeenCalledOnce();
  expect(props.load).toHaveBeenCalledTimes(2);
  expect(props.onChanged).toHaveBeenCalledOnce();
});

it('fills a suggested commit message that stays editable before committing', async () => {
  const props = options();
  render(
    <DeveloperRepositoryPanel
      {...props}
      changedFiles={[{ path: 'src/one.py', status: 'modified' }]}
      commitSuggestion={{ subject: 'Update one.py', body: '- src/one.py' }}
    />,
  );
  await ready();
  const commit = screen.getByRole('button', { name: 'Commit changes' });
  expect(commit).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Suggest message' }));
  expect(screen.getByLabelText('Commit message')).toHaveValue(
    'Update one.py\n\n- src/one.py',
  );
  expect(props.review).not.toHaveBeenCalled();
  fireEvent.click(commit);
  await screen.findByText('Committed.');
  expect(props.review.mock.calls[0][1]).toEqual({
    revision: page.revision,
    message: 'Update one.py\n\n- src/one.py',
    paths: [],
  });
});

it('switches to a local branch from the branch menu in one reviewed step', async () => {
  const user = userEvent.setup();
  const props = options();
  render(<DeveloperRepositoryPanel {...props} />);
  await ready();
  await user.click(screen.getByRole('button', { name: 'Switch branch' }));
  expect(screen.getByRole('menuitem', { name: /main/ })).toHaveAttribute(
    'aria-disabled',
    'true',
  );
  await user.click(screen.getByRole('menuitem', { name: 'feature/x' }));
  await screen.findByText('Switched branch.');
  expect(props.review).toHaveBeenCalledWith(
    'developer.repository.branch.switch',
    { revision: page.revision, branch: 'feature/x' },
    expect.any(AbortSignal),
  );
  expect(props.execute).toHaveBeenCalledOnce();
});

it('asks before pushing and keeps the current state when cancelled', async () => {
  const props = options();
  render(<DeveloperRepositoryPanel {...props} />);
  fireEvent.click(await ready());
  const confirm = await screen.findByRole('group', {
    name: 'Confirm repository change',
  });
  expect(
    within(confirm).getByText('Synthetic reviewed repository effect.'),
  ).toBeVisible();
  expect(props.execute).not.toHaveBeenCalled();
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Keep current state' }),
  );
  await screen.findByText('Cancelled. No repository change was made.');
  expect(props.execute).not.toHaveBeenCalled();
});

it('saves exact sandbox policy fields in one click without rebuilding', async () => {
  const props = options();
  render(<DeveloperRepositoryPanel {...props} />);
  await ready();
  fireEvent.change(screen.getByLabelText('Execution mode'), {
    target: { value: 'docker' },
  });
  fireEvent.change(screen.getByLabelText('Sandbox network'), {
    target: { value: 'ask' },
  });
  fireEvent.change(screen.getByLabelText('Sandbox image'), {
    target: { value: 'local/synthetic:1' },
  });
  fireEvent.click(
    screen.getByRole('button', { name: 'Save sandbox settings' }),
  );
  await screen.findByText('Sandbox settings saved.');
  expect(props.review).toHaveBeenCalledWith(
    'developer.repository.sandbox.configure',
    {
      revision: page.revision,
      execution_mode: 'docker',
      sandbox_network: 'ask',
      sandbox_image: 'local/synthetic:1',
    },
    expect.any(AbortSignal),
  );
  expect(props.execute).toHaveBeenCalledOnce();
});

it('shows a blocked policy review without making it executable', async () => {
  const props = options();
  props.review.mockImplementationOnce(async (action, payload) => ({
    schema_version: 1,
    action,
    resource_id: page.resource_id,
    conversation_id: page.conversation_id,
    binding_id: page.binding_id,
    binding_revision: page.binding_revision,
    resource_revision: page.resource_revision,
    revision: payload.revision,
    policy_action: 'git_push',
    policy_decision: 'block',
    approval_required: true,
    disclosures: ['Remote changes are blocked.'],
    action_digest: 'b'.repeat(64),
  }));
  render(<DeveloperRepositoryPanel {...props} />);
  fireEvent.click(await ready());
  await screen.findByText(/policy blocks this action/);
  expect(props.execute).not.toHaveBeenCalled();
});

it('retains one unconfirmed command across remount and checks only that command', async () => {
  const props = options();
  props.execute.mockRejectedValueOnce(Error('response lost'));
  const rendered = render(<DeveloperRepositoryPanel {...props} />);
  await ready();
  fireEvent.click(screen.getByRole('button', { name: 'Push branch' }));
  await screen.findByText('Synthetic reviewed repository effect.');
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm repository action' }),
  );
  await screen.findByText(/original change is unconfirmed/i);
  const original = props.execute.mock.calls[0];
  rendered.unmount();
  render(<DeveloperRepositoryPanel {...props} />);
  const recovery = screen.getByRole('button', {
    name: 'Check original repository change',
  });
  await waitFor(() => expect(recovery).toBeEnabled());
  fireEvent.click(recovery);
  await screen.findByText('Pushed.');
  expect(props.execute.mock.calls[1]).toEqual(original);
  expect(props.review).toHaveBeenCalledOnce();
});

it('tombstones drafts and an in-flight command when authentication ends', async () => {
  const props = options();
  let resolve!: (receipt: DeveloperRepositoryReceipt) => void;
  props.execute.mockReturnValue(
    new Promise((done) => {
      resolve = done;
    }),
  );
  render(<DeveloperRepositoryPanel {...props} />);
  await ready();
  fireEvent.change(screen.getByLabelText('Pull request body'), {
    target: { value: 'private draft' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Push branch' }));
  await screen.findByText('Synthetic reviewed repository effect.');
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm repository action' }),
  );
  const command = props.execute.mock.calls[0][0];
  act(() => props.session.dispose());
  await act(async () =>
    resolve({
      schema_version: 1,
      command_id: command.command_id,
      action: command.type,
      resource_id: page.resource_id,
      conversation_id: page.conversation_id,
      status: 'completed',
      code: null,
      revision: 'c'.repeat(64),
      worktree_id: null,
      external_url: null,
    }),
  );
  expect(props.session.hasRetained()).toBe(false);
  expect(props.session.getSnapshot().drafts.pullBody).toBe('');
  expect(screen.queryByText('private draft')).not.toBeInTheDocument();
  expect(
    screen.getByText('Sign in again to manage the repository.'),
  ).toBeVisible();
});

it('rejects a response for another resource and retains the original recovery identity', async () => {
  const props = options();
  props.execute.mockImplementationOnce(async (command) => ({
    schema_version: 1,
    command_id: command.command_id,
    action: command.type,
    resource_id: 'other-workspace',
    conversation_id: page.conversation_id,
    status: 'completed',
    code: null,
    revision: 'c'.repeat(64),
    worktree_id: null,
    external_url: null,
  }));
  render(<DeveloperRepositoryPanel {...props} />);
  await ready();
  fireEvent.click(screen.getByRole('button', { name: 'Push branch' }));
  await screen.findByText('Synthetic reviewed repository effect.');
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm repository action' }),
  );
  await screen.findByText(/receipt target did not match/i);
  expect(
    screen.getByRole('button', { name: 'Check original repository change' }),
  ).toBeVisible();
});

it('does not read or reuse a session outside its exact binding scope', async () => {
  const props = options();
  render(<DeveloperRepositoryPanel {...props} scope="another-scope" />);
  expect(await screen.findByText('Developer workspace changed')).toBeVisible();
  await waitFor(() => expect(props.load).not.toHaveBeenCalled());
});
