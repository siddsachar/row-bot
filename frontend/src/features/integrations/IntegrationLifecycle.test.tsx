import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { RuntimeContext } from '../../runtime';
import type { ClientController } from '../../api/controller';
import type { ClientPlatform } from '../../platform';
import type { IntegrationItem } from '../../api/types';
import { retainCommand, readRetainedCommand } from '../../api/retained-command';
import TryInChat from './TryInChat';
import SkillMaintenance from './SkillMaintenance';

const item: IntegrationItem = {
  id: 'skill:writing',
  kind: 'skill',
  owner_ref: 'writing',
  name: 'Writing',
  revision: 'a'.repeat(64),
  status: 'ready',
  account_label: 'Work',
  children: [],
  parent_id: null,
  description: '',
  source: 'clawhub',
  publisher: '',
  source_url: '',
  version: '',
  pin: '',
  license: '',
  compatibility: 'supported',
  reasons: [],
  platforms: [],
  evidence: '',
  installed: true,
  enabled: true,
  actions: [],
  auth_status: 'none',
  target: { kind: 'standalone' },
};
function Location() {
  return <output aria-label="route">{useLocation().pathname}</output>;
}
function mount(controller: object, child = <TryInChat item={item} />) {
  return render(
    <MemoryRouter>
      <RuntimeContext.Provider
        value={{
          controller: controller as ClientController,
          platform: {} as ClientPlatform,
        }}
      >
        {child}
        <Location />
      </RuntimeContext.Provider>
    </MemoryRouter>,
  );
}
function draftFixture() {
  const workspace = {
    conversation_id: 'current',
    revision: '3',
    controls: { profile_id: 'writer', runtime_mode: 'agent' },
  };
  const state = { selectedConversationId: 'current', workspace };
  const controller = {
    subscribe: () => () => undefined,
    getSnapshot: () => state,
    integrationUse: vi.fn().mockResolvedValue({
      conversation_id: 'current',
      integration_id: item.id,
      conversation_revision: '3',
      eligible: true,
      account_label: 'Work',
      reason: 'Current profile can use this integration.',
    }),
    getDraft: vi.fn().mockReturnValue({
      text: 'Existing text',
      attachments: [{ attachment_ref: 'existing-file' }],
    }),
    setDraft: vi.fn(),
    send: vi.fn(),
    execute: vi.fn(),
    selectConversation: vi.fn(),
  };
  return { controller, state };
}
afterEach(() => {
  sessionStorage.clear();
  localStorage.clear();
});
it('Try in chat appends a visible draft, preserves attachments/context and never sends or executes', async () => {
  const { controller, state } = draftFixture();
  const original = JSON.stringify(state);
  mount(controller);
  const button = await screen.findByRole('button', { name: 'Try in chat' });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  await waitFor(() =>
    expect(controller.setDraft).toHaveBeenCalledWith('current', {
      text: expect.stringContaining(
        'Existing text\n\nHelp me use Writing (Work)',
      ),
      attachments: [{ attachment_ref: 'existing-file' }],
    }),
  );
  await waitFor(() =>
    expect(screen.getByLabelText('route')).toHaveTextContent(
      '/conversations/current',
    ),
  );
  expect(JSON.stringify(state)).toBe(original);
  expect(controller.send).not.toHaveBeenCalled();
  expect(controller.execute).not.toHaveBeenCalled();
  expect(controller.selectConversation).not.toHaveBeenCalled();
});
it('shows an exact restriction and does not change profile to make Try available', async () => {
  const { controller } = draftFixture();
  controller.integrationUse.mockResolvedValue({
    conversation_id: 'current',
    integration_id: item.id,
    conversation_revision: '3',
    eligible: false,
    reason: 'The Work profile blocks this skill. Review profile restrictions.',
  });
  mount(controller);
  await screen.findByText(
    'The Work profile blocks this skill. Review profile restrictions.',
  );
  expect(screen.getByRole('button', { name: 'Try in chat' })).toBeDisabled();
  expect(controller.setDraft).not.toHaveBeenCalled();
});
it('rechecks eligibility and refuses a late response after the current chat changes', async () => {
  const { controller, state } = draftFixture();
  mount(controller);
  const button = screen.getByRole('button', { name: 'Try in chat' });
  await waitFor(() => expect(button).toBeEnabled());
  let resolve!: (value: object) => void;
  controller.integrationUse.mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  fireEvent.click(button);
  state.selectedConversationId = 'different';
  await act(async () =>
    resolve({
      conversation_id: 'current',
      integration_id: item.id,
      conversation_revision: '3',
      eligible: true,
      account_label: 'Work',
    }),
  );
  expect(controller.setDraft).not.toHaveBeenCalled();
  await screen.findByText(/selected chat changed/);
});
it('an unresolved owner command blocks draft preparation too', async () => {
  const { controller } = draftFixture();
  retainCommand('integration-skill:' + item.id, crypto.randomUUID());
  mount(controller);
  await screen.findByText('Current profile can use this integration.');
  expect(screen.getByRole('button', { name: 'Try in chat' })).toBeDisabled();
});
function maintenanceFixture() {
  const record = { name: 'writing', revision: 'a'.repeat(64), file_count: 2 };
  const controller = {
    skillHubInstalled: vi.fn().mockResolvedValue({ items: [record] }),
    skillHubMaintenance: vi.fn(),
    skillHubMaintenanceReceipt: vi.fn(),
    reconcileIntegrationOperation: vi.fn().mockResolvedValue({ settled: true }),
  };
  const changed = vi.fn().mockResolvedValue(undefined);
  const view = mount(
    controller,
    <SkillMaintenance item={item} onChanged={changed} />,
  );
  return { controller, changed, ...view };
}
it('rejecting an update leaves the current revision and never publishes', async () => {
  const io = maintenanceFixture();
  io.controller.skillHubMaintenance.mockResolvedValue({
    success: true,
    message: 'Review available',
    update_preview: {
      preview_id: 'preview',
      skill_name: 'writing',
      content_hash: 'b'.repeat(64),
      entry: { source: 'github', url: '', author: 'Fixture' },
      files: ['SKILL.md'],
      primary_text: 'New instructions requiring service access',
      changes: ['Changed: SKILL.md'],
      scan: { blocked: false, findings: [] },
    },
  });
  fireEvent.click(
    await screen.findByRole('button', { name: 'Check for skill updates' }),
  );
  await screen.findByText('Changed: SKILL.md');
  await screen.findByText('New instructions requiring service access');
  fireEvent.click(screen.getByRole('button', { name: 'Keep current version' }));
  expect(io.controller.skillHubMaintenance).toHaveBeenCalledTimes(1);
  expect(io.changed).not.toHaveBeenCalled();
});
it('uncertain update survives remount and checks the original receipt without publication replay', async () => {
  const id = crypto.randomUUID();
  retainCommand('integration-skill:' + item.id + ':maintenance', id);
  const io = maintenanceFixture();
  io.controller.skillHubMaintenanceReceipt.mockResolvedValue({
    success: false,
    action: 'update',
    message: 'Prior revision retained.',
  });
  expect(
    await screen.findByRole('button', { name: 'Check for skill updates' }),
  ).toBeDisabled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Check original skill action' }),
  );
  await screen.findByText('Prior revision retained.');
  expect(io.controller.reconcileIntegrationOperation).toHaveBeenCalledWith(
    'skill',
    id,
  );
  expect(io.controller.skillHubMaintenanceReceipt).toHaveBeenCalledWith(id);
  expect(io.controller.skillHubMaintenance).not.toHaveBeenCalled();
  expect(
    readRetainedCommand('integration-skill:' + item.id + ':maintenance'),
  ).toBe('');
});
it('failed reconciliation keeps the original ID and blocks duplicate removal', async () => {
  const id = crypto.randomUUID();
  retainCommand('integration-skill:' + item.id + ':maintenance', id);
  const io = maintenanceFixture();
  io.controller.reconcileIntegrationOperation.mockRejectedValue(
    new Error('Original operation still running'),
  );
  fireEvent.click(
    await screen.findByRole('button', { name: 'Check original skill action' }),
  );
  await waitFor(() =>
    expect(io.controller.reconcileIntegrationOperation).toHaveBeenCalled(),
  );
  expect(
    readRetainedCommand('integration-skill:' + item.id + ':maintenance'),
  ).toBe(id);
  expect(screen.getByRole('button', { name: 'Remove skill' })).toBeDisabled();
  expect(io.controller.skillHubMaintenance).not.toHaveBeenCalled();
});

it('leaving integration details cancels a late first-use response without drafting', async () => {
  const { controller } = draftFixture();
  const view = mount(controller);
  const button = screen.getByRole('button', { name: 'Try in chat' });
  await waitFor(() => expect(button).toBeEnabled());
  let resolve!: (value: object) => void;
  controller.integrationUse.mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  fireEvent.click(button);
  view.unmount();
  await act(async () =>
    resolve({
      conversation_id: 'current',
      integration_id: item.id,
      conversation_revision: '3',
      eligible: true,
    }),
  );
  expect(controller.setDraft).not.toHaveBeenCalled();
});
