import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { CustomToolSnapshot } from '../../api/types';
import CustomToolBuilder from './CustomToolBuilder';

const empty: CustomToolSnapshot = {
  schema_version: 1,
  resource_id: 'workspace',
  conversation_id: 'conversation',
  binding_id: 'binding',
  workspace_name: 'Synthetic workspace',
  source_is_repository: false,
  drafts: [],
  tools: [],
  revision: 'a'.repeat(64),
};

const inspected: CustomToolSnapshot = {
  ...empty,
  revision: 'b'.repeat(64),
  drafts: [
    {
      id: 'draft-1',
      name: 'Example',
      version: '1.0.0',
      commands: [
        { name: 'Hello', description: 'Say hello', command: 'python hello.py' },
      ],
      warnings: [],
      test_results: {},
      python_project: false,
      setup_ok: false,
      status: 'draft',
      created_tool_id: '',
    },
  ],
};

beforeEach(() => sessionStorage.clear());

it('inspects on one click after showing the provider disclosure', async () => {
  const customTools = vi.fn().mockResolvedValue(empty);
  const executeCustomTool = vi
    .fn()
    .mockImplementation(async (_conversation, _binding, command) => ({
      command_id: command.command_id,
      status: 'completed',
      summary: 'Inspected Example.',
      snapshot: inspected,
    }));
  const controller = {
    customTools,
    executeCustomTool,
  } as unknown as ClientController;
  render(
    <CustomToolBuilder
      controller={controller}
      conversation="conversation"
      binding="binding"
      visible
    />,
  );
  await screen.findByRole('button', { name: 'Inspect tool' });
  expect(
    screen.getByText(/repository excerpts to your configured AI model/i),
  ).toBeTruthy();
  expect(executeCustomTool).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Inspect tool' }));
  await screen.findByRole('status', { name: '' });
  await waitFor(() => expect(executeCustomTool).toHaveBeenCalledTimes(1));
  expect(executeCustomTool.mock.calls[0][2]).toMatchObject({
    action: 'inspect',
    revision: empty.revision,
    payload: {},
  });
  expect(await screen.findByDisplayValue('Example')).toBeTruthy();
});

it('shows a saved command outcome after an interrupted response', async () => {
  const customTools = vi.fn().mockResolvedValue(empty);
  const executeCustomTool = vi
    .fn()
    .mockRejectedValue(new Error('network_lost'));
  const customToolReceipt = vi
    .fn()
    .mockImplementation(async (_conversation, _binding, commandId) => ({
      command_id: commandId,
      status: 'completed',
      summary: 'Inspected Example.',
      snapshot: inspected,
    }));
  const controller = {
    customTools,
    executeCustomTool,
    customToolReceipt,
  } as unknown as ClientController;
  render(
    <CustomToolBuilder
      controller={controller}
      conversation="conversation"
      binding="binding"
      visible
    />,
  );
  fireEvent.click(await screen.findByRole('button', { name: 'Inspect tool' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Check outcome' }));
  await waitFor(() => expect(customToolReceipt).toHaveBeenCalledTimes(1));
  expect(await screen.findByText('Inspected Example.')).toBeTruthy();
});

it('keeps the opaque command ID across a remount for receipt recovery', async () => {
  const customTools = vi.fn().mockResolvedValue(empty);
  const executeCustomTool = vi
    .fn()
    .mockRejectedValue(new Error('network_lost'));
  const customToolReceipt = vi
    .fn()
    .mockImplementation(async (_conversation, _binding, commandId) => ({
      command_id: commandId,
      status: 'completed',
      summary: 'Inspected Example.',
      snapshot: inspected,
    }));
  const controller = {
    customTools,
    executeCustomTool,
    customToolReceipt,
  } as unknown as ClientController;
  const first = render(
    <CustomToolBuilder
      controller={controller}
      conversation="conversation"
      binding="binding"
      visible
    />,
  );
  fireEvent.click(await screen.findByRole('button', { name: 'Inspect tool' }));
  await screen.findByRole('button', { name: 'Check outcome' });
  first.unmount();
  render(
    <CustomToolBuilder
      controller={controller}
      conversation="conversation"
      binding="binding"
      visible
    />,
  );
  fireEvent.click(
    await screen.findByRole('button', { name: 'Check previous outcome' }),
  );
  expect(await screen.findByText('Inspected Example.')).toBeTruthy();
  expect(customToolReceipt).toHaveBeenCalledTimes(1);
  expect(
    sessionStorage.getItem('row-bot:command:custom-tool:conversation:binding'),
  ).toBeNull();
});

it('allows another explicit inspection after a failed proposal saved no draft', async () => {
  const customTools = vi.fn().mockResolvedValue(empty);
  const executeCustomTool = vi
    .fn()
    .mockImplementationOnce(async (_conversation, _binding, command) => ({
      command_id: command.command_id,
      status: 'failed',
      summary: 'Inspection failed before a draft was saved. Try again.',
      snapshot: empty,
    }))
    .mockImplementationOnce(async (_conversation, _binding, command) => ({
      command_id: command.command_id,
      status: 'completed',
      summary: 'Inspected Example.',
      snapshot: inspected,
    }));
  const controller = {
    customTools,
    executeCustomTool,
  } as unknown as ClientController;
  render(
    <CustomToolBuilder
      controller={controller}
      conversation="conversation"
      binding="binding"
      visible
    />,
  );
  fireEvent.click(await screen.findByRole('button', { name: 'Inspect tool' }));
  expect(
    await screen.findByText(
      'Inspection failed before a draft was saved. Try again.',
    ),
  ).toBeTruthy();
  expect(
    sessionStorage.getItem('row-bot:command:custom-tool:conversation:binding'),
  ).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Inspect tool' }));
  expect(await screen.findByText('Inspected Example.')).toBeTruthy();
  expect(executeCustomTool).toHaveBeenCalledTimes(2);
});

it('shows a safe request error instead of an object string', async () => {
  const controller = {
    customTools: vi.fn().mockResolvedValue(empty),
    executeCustomTool: vi.fn().mockRejectedValue({
      code: 'dependency_unavailable',
      status: 503,
    }),
  } as unknown as ClientController;
  render(
    <CustomToolBuilder
      controller={controller}
      conversation="conversation"
      binding="binding"
      visible
    />,
  );
  fireEvent.click(await screen.findByRole('button', { name: 'Inspect tool' }));
  expect(
    await screen.findByText('Row-Bot could not complete this request.'),
  ).toBeTruthy();
  expect(screen.queryByText('[object Object]')).toBeNull();
  expect(screen.getByRole('button', { name: 'Check outcome' })).toBeTruthy();
});
