import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { RuntimeInstallationSnapshot } from './McpRuntimeInstallation';
import {
  RuntimesRow,
  createRuntimeInstallations,
} from './RuntimeInstallations';

function snapshot(
  runtime: 'node' | 'uv',
  patch: Partial<RuntimeInstallationSnapshot> = {},
): RuntimeInstallationSnapshot {
  return {
    schema_version: 1,
    runtime_id: runtime,
    resource_revision: `${runtime}-revision`,
    availability: 'missing',
    installed: false,
    active_command_id: null,
    quiesced: true,
    version: null,
    system_available: false,
    ...patch,
  };
}

it('uses a system Node as is and offers Row-Bot’s own copy only from the ⋯ (B262)', async () => {
  const controller = {
    runtimeInstallation: vi.fn(async (runtime: 'node' | 'uv') =>
      runtime === 'node'
        ? snapshot('node', { system_available: true })
        : snapshot('uv'),
    ),
    reviewRuntimeInstallation: vi.fn(() => new Promise(() => {})),
    executeRuntimeInstallation: vi.fn(),
    runtimeInstallationReceipt: vi.fn(),
  } as unknown as ClientController;
  render(
    <RuntimesRow
      sessions={createRuntimeInstallations()}
      controller={controller}
    />,
  );
  expect(await screen.findByText('System copy')).toBeVisible();
  expect(
    screen.getByText('System Node.js found; servers use it.'),
  ).toBeVisible();
  // uv has no copy yet: its chip offers the one Install.
  expect(screen.getByRole('button', { name: 'Install uv' })).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Install Node.js' })).toBeNull();
  const more = screen.getByRole('button', { name: 'More runtime actions' });
  await act(async () => fireEvent.keyDown(more, { key: 'Enter' }));
  expect(
    screen.queryByRole('menuitem', { name: 'Install Row-Bot’s own uv' }),
  ).toBeNull();
  await act(async () =>
    fireEvent.click(
      screen.getByRole('menuitem', { name: 'Install Row-Bot’s own Node.js' }),
    ),
  );
  await waitFor(() =>
    expect(controller.reviewRuntimeInstallation).toHaveBeenCalledWith(
      expect.objectContaining({ runtime_id: 'node', operation: 'resolve' }),
      expect.any(AbortSignal),
    ),
  );
  expect(controller.executeRuntimeInstallation).not.toHaveBeenCalled();
});
