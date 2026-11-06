import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { SettingsSnapshot } from '../../api/types';
import {
  SettingsDraftOwner,
  ToolSettingsUnreadable,
  type SettingsMutationIO,
} from './SettingsSnapshotPanels';

function mutation() {
  return {
    revision: 'saved-a',
    page: 'system',
    review: vi.fn<SettingsMutationIO['review']>(async (request) => ({
      schema_version: 1,
      operation: 'settings.update',
      settings_revision: request.settings_revision,
      page: request.page,
      field: request.field,
      value_summary: 'reset',
      secret: false,
      action_digest: 'd'.repeat(64),
      review_id: 'review-reset',
    })),
    execute: vi.fn<SettingsMutationIO['execute']>(
      async (_request, _review, commandId) => ({
        command_id: commandId,
        status: 'completed',
        settings_revision: 'saved-b',
        snapshot: { revision: 'saved-b' } as SettingsSnapshot,
      }),
    ),
    receipt: vi.fn<SettingsMutationIO['receipt']>(),
    drafts: new SettingsDraftOwner(),
    onSnapshot: vi.fn(),
  } satisfies SettingsMutationIO;
}

it('says tools are off while the file is unreadable and resets only after a confirm', async () => {
  const io = mutation();
  render(<ToolSettingsUnreadable unreadable mutation={io} />);

  const notice = screen.getByRole('alert');
  expect(within(notice).getByText('Tool settings can’t be read')).toBeVisible();
  expect(notice).toHaveTextContent('every tool is off');
  fireEvent.click(
    within(notice).getByRole('button', { name: 'Start from defaults' }),
  );
  const dialog = await screen.findByRole('dialog');
  expect(dialog).toHaveTextContent('your blocked commands are cleared');
  expect(io.review).not.toHaveBeenCalled();

  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Start from defaults' }),
  );
  await act(async () => undefined);

  expect(io.review.mock.calls[0][0]).toEqual({
    settings_revision: 'saved-a',
    page: 'system',
    field: 'tools.reset_unreadable',
    value: true,
  });
  expect(io.execute).toHaveBeenCalledTimes(1);
});

it('shows nothing while the tool settings file reads', () => {
  const { container } = render(
    <ToolSettingsUnreadable unreadable={false} mutation={mutation()} />,
  );
  expect(container).toBeEmptyDOMElement();
});
