import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import type {
  SettingsMutationReceipt,
  SettingsMutationRequest,
  SettingsMutationReview,
  SettingsSnapshot,
} from '../../api/types';
import { OverlayProvider } from '../../ui/overlays';
import {
  SettingsDraftOwner,
  TrackerSnapshotPanel,
  type SettingsMutationIO,
} from './SettingsSnapshotPanels';

type Tracker = SettingsSnapshot['tracker'];
const water = {
  tracker_id: 'water',
  name: 'Water',
  kind: 'count',
  unit: 'glasses',
  icon: null,
  entry_count: 4,
  last_event_at: null,
};
const sleep = { ...water, tracker_id: 'sleep', name: 'Sleep', entry_count: 1 };
const tracker = (items: (typeof water)[]): Tracker => ({
  availability: 'available',
  tool_available: true,
  enabled: true,
  items,
  total_entries: items.reduce((sum, item) => sum + item.entry_count, 0),
});

function setup(items = [water, sleep]) {
  const mutation: SettingsMutationIO = {
    revision: 'settings-a',
    page: 'tracker',
    review: vi.fn(
      async (
        request: SettingsMutationRequest,
      ): Promise<SettingsMutationReview> => ({
        schema_version: 1,
        operation: 'settings.update',
        settings_revision: request.settings_revision,
        page: request.page,
        field: request.field,
        value_summary:
          'Delete the tracker “Water” and its 4 entries. This cannot be undone.',
        secret: false,
        action_digest: 'a'.repeat(64),
        review_id: 'review-a',
      }),
    ),
    execute: vi.fn(
      async (
        _request,
        _review,
        commandId,
      ): Promise<SettingsMutationReceipt> => ({
        command_id: commandId,
        status: 'completed',
        settings_revision: 'settings-b',
        snapshot: {
          revision: 'settings-b',
          tracker: tracker([sleep]),
        } as unknown as SettingsSnapshot,
      }),
    ),
    receipt: vi.fn(),
    drafts: new SettingsDraftOwner(),
    onSnapshot: vi.fn(),
  };
  const view = render(
    <MemoryRouter>
      <OverlayProvider>
        <TrackerSnapshotPanel snapshot={tracker(items)} mutation={mutation} />
      </OverlayProvider>
    </MemoryRouter>,
  );
  return { mutation, view };
}

it('deletes one tracker from its own row, only after its reviewed confirmation', async () => {
  const { mutation } = setup();
  fireEvent.click(screen.getByRole('button', { name: 'Delete Water' }));
  expect(mutation.review).toHaveBeenCalledWith(
    {
      settings_revision: 'settings-a',
      page: 'tracker',
      field: 'delete_tracker',
      value: 'water',
    },
    expect.any(AbortSignal),
  );
  const confirm = await screen.findByRole('group', { name: 'Delete Water' });
  expect(
    within(confirm).getByText(/Delete the tracker “Water” and its 4 entries/),
  ).toBeVisible();
  expect(mutation.execute).not.toHaveBeenCalled();
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Delete tracker' }),
  );
  expect(await screen.findByText('Water deleted.')).toBeInTheDocument();
  expect(mutation.execute).toHaveBeenCalledTimes(1);
  expect(mutation.onSnapshot).toHaveBeenCalledWith(
    expect.objectContaining({ revision: 'settings-b' }),
  );
});

it('keeps a tracker when its deletion is cancelled', async () => {
  const { mutation } = setup();
  fireEvent.click(screen.getByRole('button', { name: 'Delete Sleep' }));
  const confirm = await screen.findByRole('group', { name: 'Delete Sleep' });
  fireEvent.click(within(confirm).getByRole('button', { name: 'Keep it' }));
  expect(screen.getByText(/Deletion cancelled/)).toBeVisible();
  expect(mutation.execute).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Delete Sleep' })).toBeEnabled();
});

it('says how to start a tracker when there are none', () => {
  setup([]);
  expect(
    screen.getByText(/Ask Row-Bot in a chat to track something/),
  ).toBeVisible();
  expect(screen.queryByRole('button', { name: /^Delete / })).toBeNull();
});
