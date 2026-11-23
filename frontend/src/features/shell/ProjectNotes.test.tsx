import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import type { ResourceView, WorkspaceProjectNote } from '../../api/types';
import { takeWorkspaceFile } from '../panels/workspace-file-requests';
import ConversationContextRail from './ConversationContextRail';

const runtime = vi.hoisted(() => ({
  inspector: vi.fn(),
  workspaceFor: vi.fn(async () => ({ writer_status: '' })),
  goals: vi.fn(async (conversation: string) => ({
    schema_version: 1,
    scope: 'conversation',
    conversation_id: conversation,
    revision: 'goals-1',
    current_goal_id: null,
    current_revision: 'none',
    items: [],
    total: 0,
    next_cursor: null,
  })),
  reviewGoal: vi.fn(),
  executeGoal: vi.fn(),
  outputs: vi.fn(async () => ({ items: [] })),
}));
vi.mock('../../shell-settled', () => ({ useShellSettled: () => true }));
vi.mock('../../runtime', () => ({
  useRuntime: () => ({ controller: runtime }),
}));
vi.mock('./ContextLive', () => ({ ContextLive: () => null }));

const folder: ResourceView = {
  resource_ref: 'conversation-a:workspace-a',
  conversation_revision: '1',
  resource_revision: 'resource-revision',
  title: 'shop',
  available: true,
  binding: {
    binding_id: 'workspace-a',
    resource_id: 'resource-workspace-a',
    kind: 'workspace',
    role: 'primary',
    revision: '1',
  },
};

function rail(notes: WorkspaceProjectNote[]) {
  runtime.inspector.mockResolvedValue({
    status: 'ready',
    is_git: true,
    branch: 'main',
    changed_total: 0,
    processes: [],
    todos: [],
    project_notes: notes,
  });
  const onOpenResource = vi.fn();
  render(
    <ConversationContextRail
      conversationId="conversation-a"
      conversationRevision="1"
      resources={[folder]}
      suggestions={[]}
      ready
      connectionStatus="ready"
      agents={null}
      agentsEmpty
      onAddResource={vi.fn()}
      onOpenResource={onOpenResource}
      onUnbindResource={vi.fn()}
      onManageConversation={vi.fn()}
      onManageBrowser={vi.fn()}
      onDeleteConversation={vi.fn()}
      onOpenSuggestion={vi.fn()}
      onDismissSuggestion={vi.fn()}
    />,
  );
  return { onOpenResource };
}

it('names the notes in use and those left out, with each reason a keyboard away', async () => {
  const user = userEvent.setup();
  const { onOpenResource } = rail([
    {
      path: 'AGENTS.md',
      state: 'cut',
      characters: 17_569,
      used_characters: 7_954,
    },
    {
      path: 'CLAUDE.md',
      state: 'not_used_injection',
      characters: 46,
      used_characters: 0,
    },
    {
      path: '.cursorrules',
      state: 'not_used_secret',
      characters: 80,
      used_characters: 0,
    },
  ]);

  expect(
    await screen.findByText(
      'Project notes: AGENTS.md · CLAUDE.md, .cursorrules left out',
    ),
  ).toBeVisible();

  screen.getByRole('button', { name: 'About project notes' }).focus();
  await user.keyboard('{Enter}');
  const popover = await screen.findByRole('dialog', { name: 'Project notes' });
  expect(
    within(popover).getByText(
      'Used in part: the first 7,954 of 17,569 characters',
    ),
  ).toBeVisible();
  expect(
    within(popover).getByText(
      'Not used: it tries to give the agent new instructions',
    ),
  ).toBeVisible();
  expect(
    within(popover).getByText(
      'Not used: it looks like it contains a password or key',
    ),
  ).toBeVisible();

  await user.click(
    within(popover).getByRole('button', { name: 'Open AGENTS.md' }),
  );
  expect(onOpenResource).toHaveBeenCalledWith(folder);
  expect(takeWorkspaceFile('resource-workspace-a')).toBe('AGENTS.md');
  expect(screen.queryByRole('dialog', { name: 'Project notes' })).toBeNull();
});

it('names a file the budget pushed out as not shown, without a warning', async () => {
  const user = userEvent.setup();
  rail([
    {
      path: 'AGENTS.md',
      state: 'cut',
      characters: 17_569,
      used_characters: 8_000,
    },
    { path: 'CLAUDE.md', state: 'cut', characters: 159, used_characters: 0 },
  ]);

  expect(
    await screen.findByText('Project notes: AGENTS.md · CLAUDE.md not shown'),
  ).toBeVisible();
  expect(screen.queryByText(/left out/)).toBeNull();

  await user.click(screen.getByRole('button', { name: 'About project notes' }));
  const popover = await screen.findByRole('dialog', { name: 'Project notes' });
  const row = within(popover).getByText('CLAUDE.md').closest('li');
  expect(row).toHaveAttribute('data-state', 'used');
  expect(row).toHaveTextContent(
    'Not shown: the notes are at their 8,000-character limit',
  );
});

it('shows nothing for a folder without project notes', async () => {
  rail([]);

  expect(await screen.findByText('main · 0 changed')).toBeVisible();
  expect(screen.queryByText(/Project notes/)).toBeNull();
  expect(
    screen.queryByRole('button', { name: 'About project notes' }),
  ).toBeNull();
});
