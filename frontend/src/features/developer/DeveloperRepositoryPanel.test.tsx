import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
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
  return { scope, visible: true, session, load, review, execute };
}

it('reads repository state without starting a Git, sandbox, or network action', async () => {
  const props = options();
  render(<DeveloperRepositoryPanel {...props} />);
  await screen.findByRole('heading', { name: 'Repository & sandbox' });
  expect(screen.getByText('main · Local changes')).toBeVisible();
  expect(
    screen.getByText(/delete: no recoverable repository delete owner/),
  ).toBeVisible();
  expect(props.load).toHaveBeenCalledOnce();
  expect(props.review).not.toHaveBeenCalled();
  expect(props.execute).not.toHaveBeenCalled();
});

it('reviews an exact confined commit before applying it', async () => {
  const props = options();
  render(<DeveloperRepositoryPanel {...props} />);
  await screen.findByRole('heading', { name: 'Repository & sandbox' });
  fireEvent.change(screen.getByLabelText('Commit message'), {
    target: { value: 'Save focused changes' },
  });
  fireEvent.change(screen.getByLabelText(/Commit paths/), {
    target: { value: 'src/one.py\nsrc/two.py' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Review commit' }));
  await screen.findByText('Synthetic reviewed repository effect.');
  expect(props.execute).not.toHaveBeenCalled();
  expect(props.review).toHaveBeenCalledWith(
    'developer.repository.commit',
    {
      revision: page.revision,
      message: 'Save focused changes',
      paths: ['src/one.py', 'src/two.py'],
    },
    expect.any(AbortSignal),
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Apply reviewed repository change' }),
  );
  await screen.findByText('Developer repository change completed.');
  expect(props.execute).toHaveBeenCalledOnce();
  expect(props.load).toHaveBeenCalledTimes(2);
});

it('keeps unavailable clone install network and delete outside this authority', async () => {
  const props = options();
  render(<DeveloperRepositoryPanel {...props} />);
  await screen.findByRole('heading', { name: 'Safety boundaries' });
  expect(
    screen.queryByRole('button', { name: /clone/i }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: /install/i }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: /^delete/i }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByText(/network: use workspace process review/),
  ).toBeVisible();
});

it('reviews exact sandbox policy fields without probing or rebuilding it', async () => {
  const props = options();
  render(<DeveloperRepositoryPanel {...props} />);
  await screen.findByRole('heading', { name: 'Execution sandbox' });
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
    screen.getByRole('button', { name: 'Review sandbox settings' }),
  );
  await screen.findByText('Synthetic reviewed repository effect.');
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
  expect(props.execute).not.toHaveBeenCalled();
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
  await screen.findByRole('heading', { name: 'Git repository' });
  fireEvent.click(screen.getByRole('button', { name: 'Review push' }));
  await screen.findByText('Policy: block');
  expect(
    screen.getByRole('button', { name: 'Apply reviewed repository change' }),
  ).toBeDisabled();
  expect(props.execute).not.toHaveBeenCalled();
});

it('retains one unconfirmed command across remount and checks only that command', async () => {
  const props = options();
  props.execute.mockRejectedValueOnce(Error('response lost'));
  const rendered = render(<DeveloperRepositoryPanel {...props} />);
  await screen.findByRole('heading', { name: 'Repository & sandbox' });
  fireEvent.click(screen.getByRole('button', { name: 'Review push' }));
  await screen.findByText('Synthetic reviewed repository effect.');
  fireEvent.click(
    screen.getByRole('button', { name: 'Apply reviewed repository change' }),
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
  await screen.findByText('Developer repository change completed.');
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
  await screen.findByRole('heading', { name: 'Repository & sandbox' });
  fireEvent.change(screen.getByLabelText('Pull request body'), {
    target: { value: 'private draft' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Review push' }));
  await screen.findByText('Synthetic reviewed repository effect.');
  fireEvent.click(
    screen.getByRole('button', { name: 'Apply reviewed repository change' }),
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
  await screen.findByRole('heading', { name: 'Repository & sandbox' });
  fireEvent.click(screen.getByRole('button', { name: 'Review push' }));
  await screen.findByText('Synthetic reviewed repository effect.');
  fireEvent.click(
    screen.getByRole('button', { name: 'Apply reviewed repository change' }),
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
