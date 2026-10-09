import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type {
  SettingsMutationReceipt,
  SettingsMutationRequest,
  SettingsMutationReview,
  SettingsSnapshot,
  TrackerEntryPage,
} from '../../api/types';
import type { ClientPlatform } from '../../platform';
import { RuntimeContext } from '../../runtime';
import { OverlayProvider } from '../../ui/overlays';
import { WorkspaceActionsContext } from '../shell/workspace-actions';
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
const tracker = (items: (typeof water)[], enabled = true): Tracker => ({
  availability: 'available',
  tool_available: true,
  enabled,
  items,
  total_entries: items.reduce((sum, item) => sum + item.entry_count, 0),
});
const waterEntries: TrackerEntryPage = {
  schema_version: 1,
  tracker_id: 'water',
  total: 230,
  items: [
    { at: '2026-09-14T18:42:00', value: '8', note: 'After the run' },
    { at: '2026-09-13T09:00:00', value: '6', note: null },
  ],
};

function setup(
  items = [water, sleep],
  {
    enabled = true,
    trackerEntries = vi.fn(async (): Promise<TrackerEntryPage> => waterEntries),
  } = {},
) {
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
  const newChat = vi.fn();
  const controller = { trackerEntries } as unknown as ClientController;
  const view = render(
    <RuntimeContext.Provider
      value={{ controller, platform: {} as ClientPlatform }}
    >
      <WorkspaceActionsContext.Provider
        value={{ resetLayout: vi.fn(), newChat }}
      >
        <MemoryRouter>
          <OverlayProvider>
            <TrackerSnapshotPanel
              snapshot={tracker(items, enabled)}
              mutation={mutation}
            />
          </OverlayProvider>
        </MemoryRouter>
      </WorkspaceActionsContext.Provider>
    </RuntimeContext.Provider>,
  );
  return { mutation, view, newChat, trackerEntries };
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

it('opens a tracker to its latest entries, newest first, and closes it again', async () => {
  const { trackerEntries, mutation } = setup();
  const open = screen.getByRole('button', { name: /^Water/ });
  expect(open).toHaveAttribute('aria-expanded', 'false');
  expect(trackerEntries).not.toHaveBeenCalled();

  fireEvent.click(open);

  expect(open).toHaveAttribute('aria-expanded', 'true');
  expect(trackerEntries).toHaveBeenCalledWith('water', expect.any(AbortSignal));
  const entries = await screen.findByRole('region', { name: 'Water entries' });
  expect(
    await within(entries).findByText('Latest 2 of 230 entries'),
  ).toBeVisible();
  const rows = within(entries).getAllByRole('listitem');
  expect(rows.map((row) => row.querySelector('time')?.dateTime)).toEqual([
    '2026-09-14T18:42:00',
    '2026-09-13T09:00:00',
  ]);
  expect(within(rows[0]).getByText('8 glasses')).toBeVisible();
  expect(within(rows[0]).getByText('After the run')).toBeVisible();
  expect(within(rows[1]).getByText('6 glasses')).toBeVisible();
  // Read-only: no way to change or delete an entry from here.
  expect(within(entries).queryByRole('button')).toBeNull();
  expect(mutation.review).not.toHaveBeenCalled();

  fireEvent.click(open);
  expect(open).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByRole('region', { name: 'Water entries' })).toBeNull();
});

it('says when a tracker’s entries could not be read and reads them again', async () => {
  const trackerEntries = vi
    .fn()
    .mockRejectedValueOnce({ code: 'settings_unavailable' })
    .mockResolvedValueOnce({ ...waterEntries, total: 2 });
  setup([water], { trackerEntries });
  fireEvent.click(screen.getByRole('button', { name: /^Water/ }));
  const entries = await screen.findByRole('region', { name: 'Water entries' });
  expect(await within(entries).findByRole('alert')).toBeVisible();

  fireEvent.click(within(entries).getByRole('button', { name: 'Try again' }));

  expect(await within(entries).findByText('2 entries')).toBeVisible();
  expect(trackerEntries).toHaveBeenCalledTimes(2);
});

it('starts a tracker by placing a prompt in a new chat, never sending it', () => {
  const { newChat } = setup([]);
  expect(
    screen.getByText(/No trackers yet\. To start one, ask Row-Bot in a chat/),
  ).toBeVisible();
  expect(screen.queryByRole('button', { name: /^Delete / })).toBeNull();

  fireEvent.click(
    screen.getByRole('button', { name: 'Start a tracker in chat' }),
  );

  expect(newChat).toHaveBeenCalledTimes(1);
  expect(newChat).toHaveBeenCalledWith('Start tracking ');
});

it('names the switch by what it does and asks to turn it on before adding', () => {
  setup([water], { enabled: false });
  expect(screen.getByLabelText('Track in chat')).not.toBeChecked();
  expect(screen.getByText(/Turn on “Track in chat” above/)).toBeVisible();
  expect(
    screen.queryByRole('button', { name: 'Start a tracker in chat' }),
  ).toBeNull();
});
