import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';
import type {
  ArtifactPreview,
  ClientState,
  ResourceView,
} from '../../api/types';
import ResourcePanel from './ResourcePanel';
import { createPanelLayout } from './model';
import { onResourcePanelRequest } from './panel-requests';
import { OverlayProvider } from '../../ui/overlays';
import { reconcilePanelPresentation } from './presentation';

const store = vi.hoisted(() => ({
  state: {} as ClientState,
  listeners: new Set<() => void>(),
  controller: {
    artifactPreview: vi.fn(),
    getSnapshot: vi.fn(),
    workspaceFor: vi.fn(),
    intent: vi.fn(),
  },
}));
vi.mock('../../runtime', async () => {
  const { useSyncExternalStore } = await import('react');
  return {
    useRuntime: () => ({ controller: store.controller }),
    useClientSelector: (selector: (state: ClientState) => unknown) =>
      useSyncExternalStore(
        (listener) => {
          store.listeners.add(listener);
          return () => store.listeners.delete(listener);
        },
        () => selector(store.state),
      ),
  };
});

function resource(): ResourceView {
  return {
    resource_ref: 'chat:binding',
    conversation_revision: '1',
    binding: {
      binding_id: 'binding',
      resource_id: 'design',
      kind: 'artifact',
      role: 'context',
      revision: '1',
    },
    title: 'Saved design',
    resource_revision: '1',
    available: true,
  };
}
function preview(): ArtifactPreview {
  return {
    resource_id: 'design',
    resource_revision: '1',
    preview_revision: 'preview-1',
    mode: 'storyboard',
    scripts_allowed: true,
    page_id: 'first',
    page_index: 0,
    page_count: 1,
    page_title: 'First',
    canvas_width: 1920,
    canvas_height: 1080,
    pages: [{ id: 'first', index: 0, title: 'First' }],
    html: '<html><body>Saved design</body></html>',
    unchanged: false,
  };
}
beforeEach(() => {
  store.controller.getSnapshot.mockImplementation(() => store.state);
  store.controller.artifactPreview.mockReset().mockResolvedValue(preview());
  store.state = {
    selectedConversationId: 'chat',
    loadingConversation: false,
    workspace: { conversation_id: 'chat', resources: [resource()] },
  } as ClientState;
});
async function publish(change: Partial<ClientState>) {
  await act(async () => {
    store.state = { ...store.state, ...change };
    store.listeners.forEach((listener) => listener());
  });
}
async function open() {
  const panel = reconcilePanelPresentation(createPanelLayout(), {
    conversationId: 'chat',
    activeConversationId: 'chat',
    resources: [resource()],
    source: 'restore',
  }).layout.panels[0];
  await act(async () => render(<ResourcePanel panel={panel} visible />));
  return panel;
}

it('retains iframe and zoom when current authority advances before the layout descriptor', async () => {
  const panel = await open();
  const frame = screen.getByTitle('Page preview: First');
  fireEvent.change(screen.getByRole('combobox', { name: 'Preview zoom' }), {
    target: { value: 'actual' },
  });
  store.controller.artifactPreview.mockResolvedValue({
    ...preview(),
    resource_revision: '2',
    html: null,
    unchanged: true,
  });
  await publish({
    workspace: {
      ...store.state.workspace!,
      resources: [{ ...resource(), resource_revision: '2' }],
    },
  });
  expect(panel.descriptor.resource_revision).toBe('1');
  expect(screen.getByTitle('Page preview: First')).toBe(frame);
  expect(screen.getByRole('combobox', { name: 'Preview zoom' })).toHaveValue(
    'actual',
  );
  expect(store.controller.artifactPreview).toHaveBeenCalledTimes(2);
  expect(store.controller.artifactPreview.mock.calls[1].slice(0, 4)).toEqual([
    'chat',
    'binding',
    undefined,
    'preview-1',
  ]);
  expect(frame).toHaveAttribute('sandbox', 'allow-scripts');
});

it.each([
  'missing',
  'unavailable',
  'replacement',
  'kind',
  'conversation',
  'loading',
])(
  'removes private content on %s instead of borrowing the refreshed authority',
  async (change) => {
    await open();
    const next = { ...resource(), resource_revision: '2' };
    if (change === 'unavailable') next.available = false;
    if (change === 'replacement')
      next.binding = { ...next.binding, resource_id: 'foreign' };
    if (change === 'kind')
      next.binding = { ...next.binding, kind: 'workspace' };
    await publish({
      workspace: {
        ...store.state.workspace!,
        resources: change === 'missing' ? [] : [next],
      },
      ...(change === 'conversation' ? { selectedConversationId: 'other' } : {}),
      ...(change === 'loading' ? { loadingConversation: true } : {}),
    });
    expect(screen.queryByTitle('Page preview: First')).not.toBeInTheDocument();
    expect(store.controller.artifactPreview).toHaveBeenCalledTimes(1);
  },
);

it('removes cached content if the refreshed preview rejects access', async () => {
  await open();
  store.controller.artifactPreview.mockRejectedValue({
    code: 'resource_binding_revoked',
    status: 403,
  });
  await publish({
    workspace: {
      ...store.state.workspace!,
      resources: [{ ...resource(), resource_revision: '2' }],
    },
  });
  expect(screen.queryByTitle('Page preview: First')).not.toBeInTheDocument();
  expect(screen.getByRole('alert')).toHaveTextContent(
    'Access to this design changed',
  );
});

it('deletes the design as shown, then asks the workspace to close its panels', async () => {
  const user = userEvent.setup();
  await open();
  store.controller.workspaceFor.mockResolvedValue({
    ...store.state.workspace,
    revision: '7',
  });
  store.controller.intent.mockResolvedValue({
    command_id: 'delete',
    status: 'completed',
  });
  const requests = vi.fn();
  const stop = onResourcePanelRequest(requests);
  await screen.findByTitle('Page preview: First');
  await user.click(screen.getByRole('button', { name: 'More design actions' }));
  await user.click(screen.getByRole('menuitem', { name: 'Delete design…' }));
  await user.click(
    within(
      await screen.findByRole('dialog', { name: 'Delete design?' }),
    ).getByRole('button', { name: 'Delete design' }),
  );
  expect(store.controller.intent).toHaveBeenCalledWith(
    'chat',
    'resource.delete',
    { binding_id: 'binding', expected_resource_revision: '1' },
    '7',
  );
  expect(requests).toHaveBeenCalledWith({
    conversationId: 'chat',
    resourceRef: 'chat:binding',
    close: true,
  });
  stop();
});

it('says how to finish when the design left its conversations but files stayed', async () => {
  const user = userEvent.setup();
  const panel = reconcilePanelPresentation(createPanelLayout(), {
    conversationId: 'chat',
    activeConversationId: 'chat',
    resources: [resource()],
    source: 'restore',
  }).layout.panels[0];
  await act(async () =>
    render(
      <OverlayProvider>
        <ResourcePanel panel={panel} visible />
      </OverlayProvider>,
    ),
  );
  store.controller.workspaceFor.mockResolvedValue({
    ...store.state.workspace,
    revision: '7',
  });
  store.controller.intent.mockResolvedValue({
    command_id: 'delete',
    status: 'partial',
    code: 'design_files_remain',
  });
  const requests = vi.fn();
  const stop = onResourcePanelRequest(requests);
  await screen.findByTitle('Page preview: First');
  await user.click(screen.getByRole('button', { name: 'More design actions' }));
  await user.click(screen.getByRole('menuitem', { name: 'Delete design…' }));
  await user.click(
    within(
      await screen.findByRole('dialog', { name: 'Delete design?' }),
    ).getByRole('button', { name: 'Delete design' }),
  );
  expect(
    await screen.findByText(
      "Saved design left its conversations, but some of its files couldn't be deleted. To finish, open it from Add resource › Open saved and choose Delete design again.",
    ),
  ).toBeInTheDocument();
  expect(requests).toHaveBeenCalledWith({
    conversationId: 'chat',
    resourceRef: 'chat:binding',
    close: true,
  });
  stop();
});
