import { render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { ResourceView } from '../../api/types';
import ConversationContextRail from './ConversationContextRail';

const runtime = vi.hoisted(() => ({
  inspector: vi.fn(),
  artifactEditing: vi.fn(),
}));

vi.mock('../../runtime', () => ({
  useRuntime: () => ({ controller: runtime }),
}));

function resource(kind: 'workspace' | 'artifact', id: string): ResourceView {
  return {
    resource_ref: `conversation-a:${id}`,
    conversation_revision: '1',
    resource_revision: 'resource-revision',
    title: kind === 'workspace' ? 'Synthetic workspace' : 'Synthetic design',
    available: true,
    binding: {
      binding_id: id,
      resource_id: `resource-${id}`,
      kind,
      role: 'primary',
      revision: '1',
    },
  };
}

it('projects bounded Developer and Designer summaries from canonical owners', async () => {
  runtime.inspector.mockResolvedValue({
    status: 'ready',
    is_git: true,
    branch: 'synthetic-branch',
    changed_total: 3,
    processes: [{ pid: 1, status: 'running' }],
    todos: [{ id: 'todo', label: 'Review', status: 'open' }],
  });
  runtime.artifactEditing.mockResolvedValue({
    mode: 'app_mockup',
    page_count: 4,
    page_title: 'Dashboard',
    history_count: 2,
  });
  const onOpenResource = vi.fn();
  render(
    <ConversationContextRail
      conversationId="conversation-a"
      resources={[
        resource('workspace', 'workspace-a'),
        resource('artifact', 'artifact-a'),
      ]}
      suggestions={[]}
      ready
      connectionStatus="ready"
      terminalAvailable={false}
      agents={<p>No delegated agents in this conversation.</p>}
      onAddResource={vi.fn()}
      onOpenResource={onOpenResource}
      onUnbindResource={vi.fn()}
      onFind={vi.fn()}
      onManageConversation={vi.fn()}
      onManageBrowser={vi.fn()}
      onDeleteConversation={vi.fn()}
      onOpenTerminal={vi.fn()}
      onOpenSuggestion={vi.fn()}
      onDismissSuggestion={vi.fn()}
    />,
  );

  expect(await screen.findByText('synthetic-branch · 3 changed')).toBeVisible();
  expect(screen.getByText('1 running · 1 todo')).toBeVisible();
  expect(screen.getByText('app mockup · 4 pages')).toBeVisible();
  expect(screen.getByText('Dashboard · 2 history entries')).toBeVisible();
  expect(runtime.inspector).toHaveBeenCalledWith(
    'conversation-a',
    'workspace-a',
    false,
    expect.any(AbortSignal),
  );
  expect(runtime.artifactEditing).toHaveBeenCalledWith(
    'conversation-a',
    'artifact-a',
    undefined,
    undefined,
    undefined,
    undefined,
    undefined,
    10,
    expect.any(AbortSignal),
  );
  const rail = screen.getByRole('complementary', {
    name: 'Conversation context',
  });
  expect(
    within(rail).getByRole('button', { name: 'Interactive terminal' }),
  ).toBeDisabled();
});

it('keeps unavailable resources visible without querying their owners', async () => {
  runtime.inspector.mockClear();
  runtime.artifactEditing.mockClear();
  render(
    <ConversationContextRail
      conversationId="conversation-a"
      resources={[{ ...resource('workspace', 'missing'), available: false }]}
      suggestions={[]}
      ready
      connectionStatus="ready"
      terminalAvailable={false}
      agents={<p>No delegated agents in this conversation.</p>}
      onAddResource={vi.fn()}
      onOpenResource={vi.fn()}
      onUnbindResource={vi.fn()}
      onFind={vi.fn()}
      onManageConversation={vi.fn()}
      onManageBrowser={vi.fn()}
      onDeleteConversation={vi.fn()}
      onOpenTerminal={vi.fn()}
      onOpenSuggestion={vi.fn()}
      onDismissSuggestion={vi.fn()}
    />,
  );

  expect(await screen.findByText('Resource unavailable')).toBeVisible();
  expect(runtime.inspector).not.toHaveBeenCalled();
});
