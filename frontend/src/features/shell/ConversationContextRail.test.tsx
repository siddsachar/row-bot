import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import type { ResourceView } from '../../api/types';
import ConversationContextRail from './ConversationContextRail';

const runtime = vi.hoisted(() => ({
  inspector: vi.fn(),
  artifactEditing: vi.fn(),
  download: vi.fn(),
  intent: vi.fn(),
  workspaceFor: vi.fn(async () => ({ writer_status: '' })),
  designSession: { upload: vi.fn() },
}));

vi.mock('../../runtime', () => ({
  useRuntime: () => ({
    controller: runtime,
    artifactDesignSessions: { get: () => runtime.designSession },
  }),
}));
vi.mock('./MediaPreview', () => ({
  MediaPreview: () => <span>Media preview</span>,
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
      conversationRevision="1"
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
    within(rail).queryByRole('button', { name: 'Interactive terminal' }),
  ).toBeNull();
  await userEvent
    .setup()
    .click(within(rail).getByRole('button', { name: 'Conversation actions' }));
  expect(
    await screen.findByRole('menuitem', { name: 'Manage browser' }),
  ).toBeVisible();
});

it('keeps unavailable resources visible without querying their owners', async () => {
  runtime.inspector.mockClear();
  runtime.artifactEditing.mockClear();
  render(
    <ConversationContextRail
      conversationId="conversation-a"
      conversationRevision="1"
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

it('keeps setup quiet and exposes a cancellable checkout wait', () => {
  const onAddResource = vi.fn();
  const onCancelWait = vi.fn();
  render(
    <ConversationContextRail
      conversationId="conversation-a"
      conversationRevision="1"
      resources={[]}
      suggestions={[]}
      ready
      connectionStatus="ready"
      terminalAvailable={false}
      agents={<p>No agents</p>}
      writerQueued
      onCancelWait={onCancelWait}
      onAddResource={onAddResource}
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
  expect(screen.getByRole('heading', { name: 'Working on' })).toBeVisible();
  expect(
    screen.queryByText('No coding workspace or design is bound yet.'),
  ).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Add resource' }));
  fireEvent.click(screen.getByRole('button', { name: 'Cancel wait' }));
  expect(onAddResource).toHaveBeenCalledOnce();
  expect(onCancelWait).toHaveBeenCalledOnce();
});

it('copies one saved media output into the chosen design without generating again', async () => {
  runtime.artifactEditing.mockResolvedValue({
    mode: 'deck',
    page_count: 1,
    page_title: 'Cover',
    history_count: 0,
  });
  runtime.download.mockResolvedValue(
    new Blob(['fixture'], { type: 'image/png' }),
  );
  runtime.designSession.upload.mockResolvedValue({ status: 'saved' });
  render(
    <ConversationContextRail
      conversationId="conversation-a"
      conversationRevision="1"
      resources={[resource('artifact', 'artifact-a')]}
      outputs={[
        {
          id: 'media-a',
          reference: 'conversation-a:media-a',
          mime: 'image/png',
        },
      ]}
      suggestions={[]}
      ready
      connectionStatus="ready"
      terminalAvailable={false}
      agents={<p>No agents</p>}
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
  fireEvent.click(screen.getByRole('button', { name: 'Add to design' }));
  await waitFor(() =>
    expect(runtime.designSession.upload).toHaveBeenCalledOnce(),
  );
  expect(runtime.designSession.upload.mock.calls[0][0]).toBeInstanceOf(File);
  expect(runtime.designSession.upload.mock.calls[0][1]).toBe(
    'resource-revision',
  );
});

it('saves an output with one scoped command and prepares code import without regenerating', async () => {
  runtime.inspector.mockResolvedValue({
    status: 'ready',
    is_git: false,
    changed_total: 0,
    processes: [],
    todos: [],
  });
  runtime.intent.mockResolvedValue({
    status: 'completed',
    saved_name: 'output-fixture.png',
  });
  const onUseOutputInCode = vi.fn();
  render(
    <ConversationContextRail
      conversationId="conversation-a"
      conversationRevision="7"
      resources={[resource('workspace', 'workspace-a')]}
      outputs={[
        {
          id: 'media-a',
          reference: 'conversation-a:media-a',
          mime: 'image/png',
        },
      ]}
      suggestions={[]}
      ready
      connectionStatus="ready"
      terminalAvailable={false}
      agents={<p>No agents</p>}
      onUseOutputInCode={onUseOutputInCode}
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
  fireEvent.click(screen.getByText('Image output'));
  fireEvent.click(screen.getByRole('button', { name: 'Use in code folder' }));
  expect(onUseOutputInCode).toHaveBeenCalledWith(
    expect.objectContaining({
      reference: 'conversation-a:media-a',
      mime: 'image/png',
    }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Save to workspace' }));
  await waitFor(() =>
    expect(runtime.intent).toHaveBeenCalledWith(
      'conversation-a',
      'media.save',
      { media_ref: 'conversation-a:media-a' },
      '7',
    ),
  );
  expect(
    await screen.findByText('Saved outputs/output-fixture.png'),
  ).toBeVisible();
});
