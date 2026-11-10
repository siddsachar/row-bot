import { act, fireEvent, render, screen, within } from '@testing-library/react';
import type { ComponentProps } from 'react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import type { SettingsSnapshot } from '../../api/types';
import { OverlayProvider } from '../../ui/overlays';
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

function Where() {
  const location = useLocation();
  return <output>{location.pathname + location.hash}</output>;
}

function Notice(props: ComponentProps<typeof ToolSettingsUnreadable>) {
  return (
    <MemoryRouter initialEntries={['/settings/tools']}>
      <OverlayProvider>
        <ToolSettingsUnreadable {...props} />
        <Where />
      </OverlayProvider>
    </MemoryRouter>
  );
}

async function confirmText() {
  fireEvent.click(
    within(screen.getByRole('alert')).getByRole('button', {
      name: 'Start from defaults',
    }),
  );
  return (await screen.findByRole('dialog')).textContent;
}

it('says tools are off while the file is unreadable and resets only after a confirm', async () => {
  const io = mutation();
  render(<Notice unreadable mutation={io} />);

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
  render(<Notice unreadable={false} mutation={mutation()} />);
  expect(screen.queryByRole('alert')).toBeNull();
});

it('says the workspace folder stays when Row-Bot still knows it', async () => {
  const io = mutation();
  const workspace = { kept: true, folder: 'Projects' };
  const { rerender } = render(
    <Notice unreadable workspace={workspace} mutation={io} />,
  );

  expect(await confirmText()).toContain(
    'Your workspace folder stays Projects.',
  );

  rerender(<Notice unreadable={false} workspace={null} mutation={io} />);
  expect(
    await screen.findByText(
      'Tool settings are back to their defaults; the workspace folder is still Projects. Add your blocked commands again.',
    ),
  ).toBeVisible();
});

it('says the workspace folder goes back to the default, and where to choose it, when it is unknown', async () => {
  const io = mutation();
  const workspace = { kept: false, folder: 'Documents › Row-Bot' };
  const { rerender } = render(
    <Notice unreadable workspace={workspace} mutation={io} />,
  );

  expect(await confirmText()).toContain(
    'Row-Bot can’t tell which workspace folder you chose, so it goes back to Documents › Row-Bot',
  );

  rerender(<Notice unreadable={false} workspace={null} mutation={io} />);
  expect(
    await screen.findByText(
      'Tool settings are back to their defaults, and the workspace folder is now Documents › Row-Bot. Add your blocked commands again.',
    ),
  ).toBeVisible();
  fireEvent.click(
    screen.getByRole('button', {
      name: 'Choose the workspace folder in Settings › System',
    }),
  );
  expect(screen.getByText('/settings/system#workspace-folder')).toBeVisible();
});
