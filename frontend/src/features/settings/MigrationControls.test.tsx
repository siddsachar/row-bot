import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import type {
  MigrationApplyCommand,
  MigrationApplyReceipt,
  MigrationApplyReviewRequest,
  MigrationPreview,
} from '../../api/types';
import { MigrationControls } from './MigrationControls';

const summary = {
  total: 2,
  selected: 1,
  ready: 1,
  migrated: 0,
  conflicts: 0,
  sensitive: 0,
  archive_only: 1,
  skipped: 0,
  blocked: 0,
  errors: 0,
};
const preview: MigrationPreview = {
  schema_version: 1,
  plan_id: '22222222-2222-4222-8222-222222222222',
  revision: 'a'.repeat(64),
  provider: 'hermes',
  source_found: true,
  source_label: 'Hermes Agent',
  summary,
  warnings: [],
  items: [
    {
      id: 'identity',
      category: 'identity',
      action: 'copy',
      status: 'planned',
      label: 'Agent identity',
      reason: '',
      target: 'SOUL.md',
      sensitivity: 'normal',
      selected: true,
      requires_confirmation: false,
    },
    {
      id: 'history',
      category: 'archive',
      action: 'archive',
      status: 'archive_only',
      label: 'Old sessions',
      reason: '',
      target: 'migration-reports/sessions',
      sensitivity: 'normal',
      selected: false,
      requires_confirmation: false,
    },
  ],
};

function owners() {
  const scan = vi.fn(async () => preview);
  const review = vi.fn(async (body: MigrationApplyReviewRequest) => ({
    schema_version: 1 as const,
    plan_id: body.plan_id,
    revision: body.revision,
    review_digest: 'b'.repeat(64),
    selected: body.selected_ids.length,
    conflicts: 0,
    sensitive: 0,
    overwrite: body.overwrite ?? false,
    backup_required: true,
  }));
  const apply = vi.fn(
    async (body: MigrationApplyCommand): Promise<MigrationApplyReceipt> => ({
      schema_version: 1,
      command_id: body.command_id,
      status: 'completed',
      summary: { ...summary, total: 1, migrated: 1, archive_only: 0 },
      report: 'migration-reports/fixture',
      warnings: [],
      failed_items: [],
    }),
  );
  const receipt = vi.fn(
    async (commandId: string): Promise<MigrationApplyReceipt> => ({
      schema_version: 1,
      command_id: commandId,
      status: 'interrupted',
      summary: { ...summary, total: 0, selected: 0, ready: 0, archive_only: 0 },
      report: '',
      warnings: ['Interrupted'],
      failed_items: [],
    }),
  );
  return { scan, review, apply, receipt };
}

beforeEach(() => sessionStorage.clear());

it('scans only on click and applies only the exact confirmed selection', async () => {
  const owner = owners();
  render(<MigrationControls owner={owner} />);
  expect(owner.scan).not.toHaveBeenCalled();
  expect(owner.review).not.toHaveBeenCalled();
  expect(owner.apply).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Source folder'), {
    target: { value: 'C:\\fixture\\hermes' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Scan folders' }));
  expect(await screen.findByText(/2 items found/)).toBeVisible();
  expect(owner.scan).toHaveBeenCalledWith({
    provider: 'hermes',
    source: 'C:\\fixture\\hermes',
    target: '',
    include_secrets: false,
  });
  expect(screen.getByLabelText(/Old sessions/)).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Apply selected items' }));
  expect(
    await screen.findByRole('alertdialog', { name: 'Confirm migration' }),
  ).toBeVisible();
  expect(owner.review).toHaveBeenCalledWith({
    plan_id: preview.plan_id,
    revision: preview.revision,
    selected_ids: ['identity'],
    overwrite: false,
  });
  expect(owner.apply).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Confirm import' }));
  expect(await screen.findByText('Migration complete')).toBeVisible();
  expect(owner.apply).toHaveBeenCalledWith(
    expect.objectContaining({
      selected_ids: ['identity'],
      confirmed: true,
      review_digest: 'b'.repeat(64),
    }),
  );
  expect(sessionStorage.getItem('row-bot:migration:pending:v1')).toBeNull();
});

it('drops a preview when source or secret choice changes', async () => {
  const owner = owners();
  render(<MigrationControls owner={owner} />);
  fireEvent.change(screen.getByLabelText('Source folder'), {
    target: { value: 'C:\\fixture\\hermes' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Scan folders' }));
  expect(await screen.findByText(/2 items found/)).toBeVisible();
  fireEvent.click(
    screen.getByRole('switch', { name: 'Include API keys and tokens' }),
  );
  expect(screen.queryByText(/2 items found/)).toBeNull();
  expect(owner.apply).not.toHaveBeenCalled();
});

it('recovers an original interrupted command without replaying apply', async () => {
  const owner = owners();
  sessionStorage.setItem(
    'row-bot:migration:pending:v1',
    JSON.stringify({
      command_id: '33333333-3333-4333-8333-333333333333',
      plan_id: preview.plan_id,
      revision: preview.revision,
      review_digest: 'b'.repeat(64),
      selected_ids: ['identity'],
      overwrite: false,
      confirmed: true,
    }),
  );
  render(<MigrationControls owner={owner} />);
  expect(await screen.findByText('Migration needs attention')).toBeVisible();
  expect(owner.receipt).toHaveBeenCalledWith(
    '33333333-3333-4333-8333-333333333333',
  );
  expect(owner.apply).not.toHaveBeenCalled();
});

it('finds the old app in its usual folder and scans it without a typed path', async () => {
  const owner = {
    ...owners(),
    sources: vi.fn(async () => ({
      sources: [
        {
          provider: 'hermes' as const,
          label: 'Hermes Agent',
          found: false,
          place: null,
        },
        {
          provider: 'openclaw' as const,
          label: 'OpenClaw',
          found: true,
          place: '~/.openclaw',
        },
      ],
    })),
  };
  render(<MigrationControls owner={owner} />);
  expect(
    await screen.findByText(
      'Found OpenClaw in ~/.openclaw. Leave Source folder empty to use it.',
    ),
  ).toBeVisible();
  expect(screen.getByLabelText('Source app')).toHaveValue('openclaw');
  fireEvent.click(screen.getByRole('button', { name: 'Scan folders' }));
  expect(await screen.findByText(/2 items found/)).toBeVisible();
  expect(owner.scan).toHaveBeenCalledWith({
    provider: 'openclaw',
    source: '',
    target: '',
    include_secrets: false,
  });
  fireEvent.change(screen.getByLabelText('Source app'), {
    target: { value: 'hermes' },
  });
  expect(
    screen.getByText(
      "Hermes Agent isn't in its usual folder. Enter the folder it uses.",
    ),
  ).toBeVisible();
  expect(screen.getByRole('button', { name: 'Scan folders' })).toBeDisabled();
});

it('Select all and Clear all change only the items that can be imported', async () => {
  const owner = owners();
  render(<MigrationControls owner={owner} />);
  fireEvent.change(screen.getByLabelText('Source folder'), {
    target: { value: 'C:\\fixture\\hermes' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Scan folders' }));
  expect(await screen.findByText(/1 selected/)).toBeVisible();
  expect(screen.getByRole('button', { name: 'Select all' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Clear all' }));
  expect(screen.getByText(/0 selected/)).toBeVisible();
  expect(
    screen.getByRole('button', { name: 'Apply selected items' }),
  ).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Select all' }));
  expect(screen.getByText(/1 selected/)).toBeVisible();
  expect(screen.getByLabelText(/Agent identity/)).toBeChecked();
  expect(screen.getByLabelText(/Old sessions/)).not.toBeChecked();
});
