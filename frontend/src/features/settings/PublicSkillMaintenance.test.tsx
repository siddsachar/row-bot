import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { PublicSkillMaintenanceIO } from './PublicSkillMaintenance';
import PublicSkillMaintenance from './PublicSkillMaintenance';

const record = {
  name: 'sample',
  source: 'fixture',
  enabled: false,
  installed_at: '2026-09-23T00:00:00Z',
  updated_at: '2026-09-23T00:00:00Z',
  file_count: 1,
  revision: 'a'.repeat(64),
};
function fixture(): PublicSkillMaintenanceIO {
  return {
    installed: vi
      .fn()
      .mockResolvedValue({ schema_version: 1, items: [record] }),
    action: vi.fn().mockImplementation(async (command) => ({
      schema_version: 1,
      command_id: command.command_id,
      action: command.action,
      success: true,
      message: 'Updated.',
      record,
    })),
    receipt: vi.fn().mockResolvedValue({
      schema_version: 1,
      command_id: crypto.randomUUID(),
      action: 'update',
      success: true,
      message: 'Updated.',
      record,
    }),
  };
}
afterEach(() => sessionStorage.clear());

it('loads only local provenance and checks an update on click', async () => {
  const io = fixture();
  render(<PublicSkillMaintenance io={io} ownerKey="session-a" />);
  expect(await screen.findByText('sample')).toBeVisible();
  expect(io.action).not.toHaveBeenCalled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Check update for sample' }),
  );
  await waitFor(() =>
    expect(io.action).toHaveBeenCalledWith(
      expect.objectContaining({
        action: 'check',
        name: 'sample',
        expected_revision: record.revision,
      }),
    ),
  );
});

it('requires confirmation before uninstall', async () => {
  const io = fixture();
  render(<PublicSkillMaintenance io={io} ownerKey="session-b" />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Uninstall sample' }),
  );
  expect(io.action).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: /^Uninstall$/ }));
  await waitFor(() =>
    expect(io.action).toHaveBeenCalledWith(
      expect.objectContaining({
        action: 'uninstall',
        confirmed: true,
        name: 'sample',
      }),
    ),
  );
});

it('recovers the original maintenance command without repeating it', async () => {
  sessionStorage.setItem(
    'row-bot-skill-hub-maintenance:session-c',
    'original-command',
  );
  const io = fixture();
  render(<PublicSkillMaintenance io={io} ownerKey="session-c" />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Check original skill action' }),
  );
  await waitFor(() =>
    expect(io.receipt).toHaveBeenCalledWith('original-command'),
  );
  expect(io.action).not.toHaveBeenCalled();
  expect(
    sessionStorage.getItem('row-bot-skill-hub-maintenance:session-c'),
  ).toBeNull();
});
